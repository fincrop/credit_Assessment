"""
Phase 4b — Train, calibrate, evaluate honestly.

data/03_features_tier{0,1}.parquet -> models/crop_classifier_model.joblib
                                      reports/cv_report_tier{0,1}.json

THE ONLY NUMBER THAT COUNTS IS THE BLOCKED ONE
──────────────────────────────────────────────
233 of 280 spatial blocks in this dataset contain a single crop, and satellite
trajectories are spatially autocorrelated through climate, soil and acquisition
calendar. A random split therefore puts near-neighbours on both sides of the
fold and scores the model on memorised neighbourhoods. GroupKFold over 0.25 deg
blocks is the primary protocol; the random split is computed too, but ONLY as a
leakage diagnostic, and the gap between them is reported as a headline.

CALIBRATION IS NOT OPTIONAL
───────────────────────────
performance_analyzer.py:145 forks on `crop_confidence >= 0.25`, which unlocks
ICAR-curve crop-specific scoring where a wrong crop name swings up to 45% of the
risk index. So probabilities must mean what they say. Temperature scaling is
fitted on HELD-OUT BLOCKS inside each outer fold — never on training rows, and
never on a random split, which would inherit the spatial leak and certify a
model that is not actually calibrated.

Run:
  python -m src.train --tier 0
  python -m src.train --tier 1
  python -m src.train --tier 0 --export      # write the joblib bundle
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ._bootstrap import DATA, MODELS, PROJECT, REPORTS, setup_logging
from .features import META_COLS

log = setup_logging("train")

N_SPLITS = 5
SEED = 42
PIPELINE_GATE = 0.25          # performance_analyzer's crop_specific threshold


# =============================================================================
# calibration / estimator wrappers
# =============================================================================
# Imported from the DEPLOYED package, never defined here. A joblib bundle stores
# each custom class's import path, so anything it contains must be resolvable on
# the production server. Defining these in this module (run as
# `python -m src.train`) pickled them as `__main__.CalibratedBundle` and made the
# exported model unloadable:
#     AttributeError: Can't get attribute 'CalibratedBundle' on <module '__main__'>
from crop_analysis.model_bundle import (      # noqa: E402
    CalibratedBundle,
    ContiguousLabelXGB,
    TemperatureScaler,
)

# =============================================================================
# metrics
# =============================================================================
def _pipeline_confidence(proba: np.ndarray) -> np.ndarray:
    """
    The number production actually compares against 0.25.

    CropDetector shrinks p_max by the top-2 gap:
        conf = min(p, p * min(1, gap/0.10 + 0.5))
    Evaluating raw p_max instead would report an operating point that does not
    exist in the pipeline.
    """
    srt = np.sort(proba, axis=1)[:, ::-1]
    p = srt[:, 0]
    gap = srt[:, 0] - srt[:, 1] if proba.shape[1] > 1 else p
    return np.minimum(p, p * np.minimum(1.0, gap / 0.10 + 0.5))


def _ece(proba: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    conf = proba.max(axis=1)
    pred = proba.argmax(axis=1)
    correct = (pred == y).astype(float)
    edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        m = (conf > edges[i]) & (conf <= edges[i + 1])
        if m.sum() == 0:
            continue
        ece += (m.sum() / len(y)) * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def _summarise(y: np.ndarray, proba: np.ndarray, classes: List[str]) -> Dict[str, Any]:
    from sklearn.metrics import (
        balanced_accuracy_score, classification_report, f1_score,
    )

    pred = proba.argmax(axis=1)
    conf = _pipeline_confidence(proba)
    gated = conf >= PIPELINE_GATE

    rep = classification_report(
        y, pred, labels=list(range(len(classes))), target_names=classes,
        output_dict=True, zero_division=0,
    )
    per_class = {
        c: {"precision": round(rep[c]["precision"], 4),
            "recall": round(rep[c]["recall"], 4),
            "f1": round(rep[c]["f1-score"], 4),
            "support": int(rep[c]["support"])}
        for c in classes if c in rep
    }
    recalls = [v["recall"] for v in per_class.values()]

    return {
        "n": int(len(y)),
        "balanced_accuracy": round(float(balanced_accuracy_score(y, pred)), 4),
        "accuracy": round(float((pred == y).mean()), 4),
        "macro_f1": round(float(f1_score(y, pred, average="macro", zero_division=0)), 4),
        "ece": round(_ece(proba, y), 4),
        "min_class_recall": round(float(min(recalls)) if recalls else 0.0, 4),
        "worst_class": min(per_class, key=lambda c: per_class[c]["recall"]) if per_class else None,
        "coverage_at_gate": round(float(gated.mean()), 4),
        "precision_at_gate": round(
            float((pred[gated] == y[gated]).mean()) if gated.any() else 0.0, 4),
        "per_class": per_class,
    }


# =============================================================================
# models
# =============================================================================
def _make_xgb(n_classes: int):
    """
    XGBoost, not LightGBM.

    classification_model.md E.2 proposed LightGBM, but production already pins
    `xgboost==2.0.3` and does NOT ship lightgbm. Adding a runtime dependency to
    serve one model is a worse trade than using the booster already in
    requirements.txt.

    Capacity was cut from the doc's 600 trees / depth 6 after the first real-data
    run: on ~2.4k rows that configuration was BEATEN by a plain random forest on
    blocked CV (0.3738 vs 0.4479), which is the signature of overfitting the
    training folds' geography.
    """
    # num_class is deliberately NOT passed: XGBClassifier infers it, and an
    # explicit value conflicts with the contiguous-label remapping.
    _ = n_classes

    return ContiguousLabelXGB(
        objective="multi:softprob",
        n_estimators=300,
        learning_rate=0.06,
        max_depth=4,
        min_child_weight=10,
        subsample=0.8,
        colsample_bytree=0.6,
        reg_lambda=3.0,
        reg_alpha=0.5,
        tree_method="hist",
        random_state=SEED,
        n_jobs=-1,
        verbosity=0,
    )


def _make_rf(n_classes: int):
    from sklearn.ensemble import RandomForestClassifier

    _ = n_classes
    return RandomForestClassifier(
        n_estimators=800, min_samples_leaf=2, max_features="sqrt",
        class_weight=None,          # weighting comes through sample_weight
        random_state=SEED, n_jobs=-1,
    )


def _make_logistic(n_classes: int):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    _ = n_classes
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=3000, C=0.5),
    )


CANDIDATES = {
    "xgboost": _make_xgb,
    "random_forest": _make_rf,
    "logistic": _make_logistic,
}


def _make_model(n_classes: int, kind: str = "xgboost"):
    """Estimator factory. `kind` is chosen by measurement, not assertion."""
    return CANDIDATES[kind](n_classes)


def _sample_weights(y: np.ndarray, blocks: np.ndarray) -> np.ndarray:
    """
    Class balance x spatial de-clustering.

    Attribution loss is not uniform across crops, so classes need rebalancing.
    And Tur puts 65% of its samples in ONE 25 km block: without a 1/sqrt(n)
    block down-weight that single neighbourhood would define the class.
    """
    w = np.ones(len(y), dtype=float)

    cls, cnt = np.unique(y, return_counts=True)
    cls_w = {c: len(y) / (len(cls) * n) for c, n in zip(cls, cnt)}
    w *= np.array([cls_w[v] for v in y])

    b, bcnt = np.unique(blocks, return_counts=True)
    bw = {k: 1.0 / np.sqrt(n) for k, n in zip(b, bcnt)}
    w *= np.array([bw[v] for v in blocks])

    return w * (len(w) / w.sum())


def _baselines(Xtr, ytr, Xte, wtr, n_classes: int) -> Dict[str, float]:
    """
    A fancy model that cannot beat a simple one is a finding, not an
    embarrassment — so the simple ones are always reported.
    """
    from sklearn.dummy import DummyClassifier

    out = {}

    d = DummyClassifier(strategy="stratified", random_state=SEED).fit(Xtr, ytr)
    # Must be re-embedded like the others: a blocked fold missing a class gives
    # the dummy fewer columns too, and an unexpanded array silently misaligns
    # every class index (it crashed outright at 15 vs 16 columns).
    out["dummy"] = _expand(d.predict_proba(Xte), d.classes_, n_classes)

    for name in ("logistic", "random_forest"):
        m = _make_model(n_classes, name)
        # Pipelines need the step-prefixed weight kwarg.
        if hasattr(m, "steps"):
            m.fit(Xtr, ytr, **{f"{m.steps[-1][0]}__sample_weight": wtr})
        else:
            m.fit(Xtr, ytr, sample_weight=wtr)
        out[name] = _expand(m.predict_proba(Xte), m.classes_, n_classes)

    return out


def _expand(proba: np.ndarray, present: np.ndarray, n_classes: int) -> np.ndarray:
    """Re-embed a fold's probabilities into the full class space — a fold can
    legitimately be missing a class when blocks are held out."""
    full = np.zeros((proba.shape[0], n_classes))
    full[:, np.asarray(present, dtype=int)] = proba
    return full


# =============================================================================
# cross-validation
# =============================================================================
def _cv(
    X: np.ndarray, y: np.ndarray, groups: np.ndarray, blocks: np.ndarray,
    classes: List[str], protocol: str, run_baselines: bool = False,
    kind: str = "xgboost",
) -> Dict[str, Any]:
    from sklearn.model_selection import GroupKFold, StratifiedKFold

    n_classes = len(classes)

    if protocol == "blocked":
        splitter = GroupKFold(n_splits=N_SPLITS)
        folds = list(splitter.split(X, y, groups=groups))
    elif protocol == "random":
        splitter = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=SEED)
        folds = list(splitter.split(X, y))
    elif protocol == "loeo":
        folds = []
        for eco in np.unique(groups):
            te = np.where(groups == eco)[0]
            tr = np.where(groups != eco)[0]
            # A fold is only meaningful if training still spans several classes.
            if len(te) >= 20 and len(np.unique(y[tr])) >= 2:
                folds.append((tr, te))
    else:
        raise ValueError(protocol)

    oof = np.zeros((len(y), n_classes))
    oof_cal = np.zeros((len(y), n_classes))
    covered = np.zeros(len(y), dtype=bool)
    base_oof: Dict[str, np.ndarray] = {}
    temps: List[float] = []
    fold_stats: List[Dict[str, Any]] = []

    for k, (tr, te) in enumerate(folds, 1):
        # Nested calibration split, MATCHED TO THE OUTER PROTOCOL.
        #
        # For blocked/LOEO the calibration rows must come from held-out BLOCKS,
        # or the temperature is fitted on neighbourhoods the model memorised.
        # For the random diagnostic the calibration rows must be random too:
        # calibrating on hard held-out blocks and testing on easy random rows
        # made the temperature over-correct and drove random-protocol ECE from
        # 0.0140 raw to 0.2188 calibrated. Each protocol has to be internally
        # consistent for its number to mean anything.
        rng = np.random.default_rng(SEED + k)
        if protocol == "random":
            perm = rng.permutation(len(tr))
            n_cal = max(1, int(0.20 * len(tr)))
            cal, fit = tr[perm[:n_cal]], tr[perm[n_cal:]]
        else:
            tr_blocks = np.unique(blocks[tr])
            rng.shuffle(tr_blocks)
            n_cal_b = max(1, int(0.20 * len(tr_blocks)))
            cal_blocks = set(tr_blocks[:n_cal_b])
            cal = tr[np.isin(blocks[tr], list(cal_blocks))]
            fit = tr[~np.isin(blocks[tr], list(cal_blocks))]
        if len(cal) < 50 or len(np.unique(y[fit])) < n_classes * 0.5:
            # Too few calibration rows to fit a temperature honestly.
            fit, cal = tr, tr

        w = _sample_weights(y[fit], blocks[fit])
        model = _make_model(n_classes, kind)
        if hasattr(model, "steps"):
            model.fit(X[fit], y[fit],
                      **{f"{model.steps[-1][0]}__sample_weight": w})
        else:
            model.fit(X[fit], y[fit], sample_weight=w)

        p_cal = _expand(model.predict_proba(X[cal]), model.classes_, n_classes)
        scaler = TemperatureScaler().fit(p_cal, y[cal])
        temps.append(scaler.temperature)

        p_raw = _expand(model.predict_proba(X[te]), model.classes_, n_classes)
        oof[te] = p_raw
        oof_cal[te] = scaler.transform(p_raw)
        covered[te] = True

        if run_baselines:
            # Fit baselines on the SAME `fit` subset the candidate used, not the
            # whole training fold. Using all of `tr` gave them ~25% more rows and
            # made logistic look like it beat the selected model (0.7646 vs
            # 0.7492) when the like-for-like comparison had it losing (0.7448).
            wb = _sample_weights(y[fit], blocks[fit])
            for name, p in _baselines(X[fit], y[fit], X[te], wb, n_classes).items():
                base_oof.setdefault(name, np.zeros((len(y), n_classes)))[te] = p

        fold_stats.append({
            "fold": k, "n_train": int(len(fit)), "n_cal": int(len(cal)),
            "n_test": int(len(te)), "temperature": round(scaler.temperature, 4),
            "test_classes": int(len(np.unique(y[te]))),
            "train_classes": int(len(np.unique(y[fit]))),
        })

    m = covered
    result = {
        "protocol": protocol,
        "estimator": kind,
        "n_folds": len(folds),
        "folds": fold_stats,
        "mean_temperature": round(float(np.mean(temps)), 4) if temps else None,
        "raw": _summarise(y[m], oof[m], classes),
        "calibrated": _summarise(y[m], oof_cal[m], classes),
    }
    if run_baselines:
        from sklearn.metrics import balanced_accuracy_score
        result["baselines"] = {
            name: round(float(balanced_accuracy_score(y[m], p[m].argmax(axis=1))), 4)
            for name, p in base_oof.items()
        }
    result["_oof_cal"] = oof_cal
    result["_oof_raw"] = oof
    result["_covered"] = covered
    return result


# =============================================================================
# driver
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=0, choices=[0, 1])
    ap.add_argument("--export", action="store_true",
                    help="fit on all data and write the joblib bundle")
    ap.add_argument("--skip-loeo", action="store_true")
    ap.add_argument("--estimator", default="auto",
                    choices=["auto", "xgboost", "random_forest", "logistic"],
                    help="'auto' picks the winner on BLOCKED cv, not on a "
                         "random split and not by assertion")
    args = ap.parse_args()

    src = DATA / f"03_features_tier{args.tier}.parquet"
    if not src.exists():
        log.error("missing %s — run `python -m src.features` first", src)
        return 1

    from sklearn.preprocessing import LabelEncoder

    df = pd.read_parquet(src)
    feat_cols = [c for c in df.columns if c not in META_COLS]

    banned = {"lat", "lon", "area_ha", "Date", "year", "month", "sample_id",
              "source_file", "survey_date"}
    leaked = banned & set(feat_cols)
    if leaked:
        log.error("BANNED features present in the matrix: %s", sorted(leaked))
        return 1

    X = df[feat_cols].to_numpy(dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    le = LabelEncoder().fit(sorted(df["Crop_Name"].unique()))
    y = le.transform(df["Crop_Name"])
    classes = list(le.classes_)
    blocks = df["block_id"].to_numpy()
    ecos = df["ecoregion"].to_numpy()

    log.info("tier %d: %d rows, %d features, %d classes, %d blocks",
             args.tier, len(df), len(feat_cols), len(classes), len(np.unique(blocks)))

    # Fold sanity: with Chilli in 6 blocks a 5-fold block split can starve a
    # class out of a training fold entirely.
    from sklearn.model_selection import GroupKFold
    for k, (tr, te) in enumerate(GroupKFold(n_splits=N_SPLITS).split(X, y, blocks), 1):
        counts = np.bincount(y[tr], minlength=len(classes))
        thin = [classes[i] for i, n in enumerate(counts) if n < 20]
        if thin:
            log.warning("fold %d has <20 training samples for: %s", k, thin)

    report: Dict[str, Any] = {
        "tier": args.tier,
        "source": src.name,
        "n_rows": int(len(df)),
        "n_features": len(feat_cols),
        "classes": classes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    log.info("")
    log.info("── PRIMARY: GroupKFold on 0.25 deg spatial blocks ──────────────")

    if args.estimator == "auto":
        # Every candidate is scored on the SAME blocked folds. The winner is the
        # model we ship; a simple model winning is the answer, not a problem.
        trials: Dict[str, Dict[str, Any]] = {}
        for kind in CANDIDATES:
            log.info("  candidate: %s", kind)
            trials[kind] = _cv(X, y, blocks, blocks, classes, "blocked",
                               run_baselines=False, kind=kind)
            log.info("    blocked bal_acc=%.4f  macro_f1=%.4f  ECE=%.4f",
                     trials[kind]["calibrated"]["balanced_accuracy"],
                     trials[kind]["calibrated"]["macro_f1"],
                     trials[kind]["calibrated"]["ece"])
        chosen = max(trials, key=lambda k: trials[k]["calibrated"]["balanced_accuracy"])
        report["estimator_selection"] = {
            k: v["calibrated"]["balanced_accuracy"] for k, v in trials.items()
        }
        log.info("  selected by blocked balanced accuracy: %s", chosen)
        blocked = trials[chosen]
        # Re-run only to attach the dummy/logistic/RF comparison columns.
        blocked_with_base = _cv(X, y, blocks, blocks, classes, "blocked",
                                run_baselines=True, kind=chosen)
        blocked["baselines"] = blocked_with_base.get("baselines", {})
    else:
        chosen = args.estimator
        blocked = _cv(X, y, blocks, blocks, classes, "blocked",
                      run_baselines=True, kind=chosen)

    report["estimator"] = chosen
    _log_result(blocked)
    report["blocked"] = {k: v for k, v in blocked.items() if not k.startswith("_")}

    log.info("")
    log.info("── DIAGNOSTIC ONLY: random StratifiedKFold ─────────────────────")
    log.info("   (never quote this as performance — it is the leakage measure)")
    rnd = _cv(X, y, blocks, blocks, classes, "random", kind=chosen)
    _log_result(rnd)
    report["random_diagnostic"] = {k: v for k, v in rnd.items() if not k.startswith("_")}

    gap = (rnd["calibrated"]["balanced_accuracy"]
           - blocked["calibrated"]["balanced_accuracy"])
    report["leakage_gap"] = round(float(gap), 4)
    log.info("")
    log.info("LEAKAGE GAP (random - blocked): %+.4f balanced accuracy", gap)

    if not args.skip_loeo:
        log.info("")
        log.info("── STRESS TEST: leave-one-ecoregion-out ────────────────────────")
        loeo = _cv(X, y, ecos, blocks, classes, "loeo", kind=chosen)
        _log_result(loeo)
        report["loeo"] = {k: v for k, v in loeo.items() if not k.startswith("_")}

    # ── gates from classification_model.md C.3 ────────────────────────────
    cal = blocked["calibrated"]
    gates = {
        "balanced_accuracy>=0.55": cal["balanced_accuracy"] >= 0.55,
        "macro_f1>=0.50": cal["macro_f1"] >= 0.50,
        "min_class_recall>=0.30": cal["min_class_recall"] >= 0.30,
        "ece<=0.05": cal["ece"] <= 0.05,
        "precision_at_gate>=0.70": cal["precision_at_gate"] >= 0.70,
        "coverage_at_gate>=0.40": cal["coverage_at_gate"] >= 0.40,
    }
    report["gates"] = gates
    report["gates_passed"] = all(gates.values())

    log.info("")
    log.info("=" * 68)
    log.info("SHIP GATES (evaluated on the BLOCKED protocol)")
    log.info("=" * 68)
    for name, ok in gates.items():
        log.info("  [%s] %s", "PASS" if ok else "FAIL", name)
    log.info("  -> %s", "ALL GATES PASSED" if all(gates.values())
             else "NOT READY TO SHIP")

    out_json = REPORTS / f"cv_report_tier{args.tier}.json"
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    log.info("")
    log.info("wrote %s", out_json.relative_to(PROJECT))

    np.save(REPORTS / f"oof_proba_tier{args.tier}.npy", blocked["_oof_cal"])
    df[["geom_hash", "Crop_Name", "block_id", "ecoregion"]].to_parquet(
        REPORTS / f"oof_index_tier{args.tier}.parquet", index=False)

    if args.export:
        return _export(X, y, blocks, classes, le, feat_cols, report,
                       args.tier, chosen)
    return 0


def _log_result(r: Dict[str, Any]) -> None:
    for kind in ("raw", "calibrated"):
        s = r[kind]
        log.info(
            "  %-11s bal_acc=%.4f  macro_f1=%.4f  ECE=%.4f  "
            "min_recall=%.4f (%s)",
            kind, s["balanced_accuracy"], s["macro_f1"], s["ece"],
            s["min_class_recall"], s["worst_class"],
        )
        log.info("              gate>=0.25: coverage=%.3f  precision=%.3f",
                 s["coverage_at_gate"], s["precision_at_gate"])
    if r.get("mean_temperature") is not None:
        log.info("  mean temperature: %.3f", r["mean_temperature"])
    if "baselines" in r:
        log.info("  baselines (bal_acc): %s", r["baselines"])


def _abstain_rule_for(tier: int) -> Dict[str, float]:
    """
    The abstain rule, taken from this tier's measured sweep when available.

    classification_model.md E.8 picked (p_min 0.35, n_scenes_min 8) by judgement.
    Measured, that discarded 43.7% of cycles before the confidence gate even
    applied. The rule now used is the LOOSEST setting whose precision still
    clears the 0.70 ship gate — looser would let wrong crop names into
    ICAR-curve scoring, stricter only throws away cycles the model got right.
    Falls back to the doc's conservative values if no sweep has been run.
    """
    default = {"p_min": 0.35, "gap_min": 0.10, "n_scenes_min": 8}
    path = REPORTS / f"evaluation_extra_tier{tier}.json"
    if not path.exists():
        log.warning("  no sweep for tier %d — shipping the conservative "
                    "design-doc abstain rule %s", tier, default)
        return default
    try:
        sweep = json.loads(path.read_text(encoding="utf-8"))["abstain_sweep"]
    except (KeyError, ValueError):
        return default
    viable = [s for s in sweep if s.get("precision", 0.0) >= 0.70]
    if not viable:
        log.warning("  no abstain setting reaches 0.70 precision — shipping the "
                    "strictest swept rule; this model should not be enabled")
        strict = max(sweep, key=lambda s: s["precision"])
        return {"p_min": float(strict["p_min"]), "gap_min": 0.10,
                "n_scenes_min": int(strict["n_scenes_min"])}
    best = max(viable, key=lambda s: s["coverage"])
    log.info("  abstain rule from measured sweep: n_scenes_min=%d p_min=%.2f "
             "(coverage %.3f, precision %.3f)",
             best["n_scenes_min"], best["p_min"], best["coverage"],
             best["precision"])
    return {"p_min": float(best["p_min"]), "gap_min": 0.10,
            "n_scenes_min": int(best["n_scenes_min"])}


def _export(X, y, blocks, classes, le, feat_cols, report, tier,
            kind: str = "xgboost") -> int:
    """
    Fit on everything and write the bundle CropDetector loads.

    Calibration still comes from held-out blocks, so the shipped temperature was
    never fitted on rows the final model also trained on.
    """
    import joblib
    import sklearn
    import xgboost

    from crop_analysis.crop_detector import EXTRACTOR_VERSION, extractor_feature_names
    from config import PipelineConfig

    if tier != 0:
        producible = set(extractor_feature_names())
        missing = [c for c in feat_cols if c not in producible]
        if missing:
            log.error(
                "REFUSING TO EXPORT tier %d: the live extractor cannot produce "
                "%d of its features (e.g. %s). Deploying this bundle would make "
                "CropDetector zero-fill them silently. Ship the matching "
                "crop_detector.py change first.",
                tier, len(missing), sorted(missing)[:6],
            )
            return 1

    log.info("")
    log.info("── EXPORT: fitting final model on all %d rows ──────────────────", len(y))

    n_classes = len(classes)
    all_blocks = np.unique(blocks)
    rng = np.random.default_rng(SEED)
    rng.shuffle(all_blocks)
    n_cal = max(1, int(0.20 * len(all_blocks)))
    cal_blocks = set(all_blocks[:n_cal])
    cal = np.isin(blocks, list(cal_blocks))
    fit = ~cal
    log.info("  fit rows=%d  calibration rows=%d (%d held-out blocks)",
             int(fit.sum()), int(cal.sum()), n_cal)

    def _fit(m, Xa, ya, wa):
        if hasattr(m, "steps"):
            m.fit(Xa, ya, **{f"{m.steps[-1][0]}__sample_weight": wa})
        else:
            m.fit(Xa, ya, sample_weight=wa)
        return m

    log.info("  estimator: %s", kind)
    w = _sample_weights(y[fit], blocks[fit])
    model = _fit(_make_model(n_classes, kind), X[fit], y[fit], w)

    p_cal = _expand(model.predict_proba(X[cal]), model.classes_, n_classes)
    scaler = TemperatureScaler().fit(p_cal, y[cal])
    log.info("  temperature = %.4f", scaler.temperature)

    # Refit on everything with the temperature fixed: the calibration map is
    # already validated on held-out blocks, and the extra 20% of blocks is worth
    # more to the estimator than holding them back.
    w_all = _sample_weights(y, blocks)
    final = _fit(_make_model(n_classes, kind), X, y, w_all)

    bundle_model = CalibratedBundle(final, scaler, np.arange(n_classes))

    # crop_names MUST be in label_encoder.classes_ order — CropDetector zips it
    # with predict_proba columns, and a mismatch mislabels every probability.
    assert list(le.classes_) == sorted(classes)

    bundle = {
        "model": bundle_model,
        "label_encoder": le,
        "feature_names": list(feat_cols),
        "crop_names": list(le.classes_),
        "extractor_version": EXTRACTOR_VERSION,
        "ml_feature_scenes": int(PipelineConfig.ML_FEATURE_SCENES),
        "ml_feature_indices": list(PipelineConfig.ML_FEATURE_INDICES),
        "tier": tier,
        "estimator": kind,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_data": "crop_classification_train_500.gpkg",
        "n_train": int(len(y)),
        "sklearn_version": sklearn.__version__,
        "xgboost_version": xgboost.__version__,
        "cv_protocol": f"GroupKFold({N_SPLITS}) on 0.25deg spatial blocks",
        "metrics": report.get("blocked", {}).get("calibrated", {}),
        "leakage_gap": report.get("leakage_gap"),
        "gates": report.get("gates"),
        "temperature": scaler.temperature,
        "abstain_rule": _abstain_rule_for(tier),
        "geographic_footprint": "69-88E, 12-31N (peninsular + Indo-Gangetic India)",
        "temporal_footprint": "2022-2024 survey dates; no year is a feature",
        "untrained_reference_crops": ["Cabbage", "Others", "Papaya",
                                      "Pomegranate", "Sunflower"],
    }

    # Deliberately NOT "crop_classifier_model.joblib": that name is the
    # CROP_MODEL_PATH default and is already occupied by a tracked legacy
    # 23-class bundle. Overwriting it would silently replace a deployed artifact.
    out = MODELS / f"crop_classifier_tier{tier}_v1.joblib"
    joblib.dump(bundle, out)
    log.info("  wrote %s (%.1f KB)", out.relative_to(PROJECT),
             out.stat().st_size / 1024)

    # Load it back through the real consumer path — an export that CropDetector
    # cannot load is not an export.
    _smoke_test(out, X[:3])
    return 0


def _smoke_test(path, X_sample) -> None:
    import joblib

    b = joblib.load(path)
    p = b["model"].predict_proba(X_sample)
    pred = b["model"].predict(X_sample)
    names = b["label_encoder"].inverse_transform(pred)
    assert p.shape[1] == len(b["crop_names"])
    assert np.allclose(p.sum(axis=1), 1.0, atol=1e-6)
    log.info("  smoke test OK — proba shape %s, sample predictions %s",
             p.shape, list(names))


if __name__ == "__main__":
    sys.exit(main())
