"""
Train the cloud-robust kharif classifier (`crop_classifier_<MODEL_VERSION>`).

  data/06_fused_series.parquet -> models/crop_classifier_<MODEL_VERSION>.joblib
                                  reports/<MODEL_VERSION>_eval.json / .md

  1. SAR -> NDVI imputer, fitted on training parcels' bins where a clear
     optical look and a Sentinel-1 acquisition fall within 5 days; scored on
     the frozen Marathwada test parcels.
  2. Features from backend `crop_analysis.fused_features.build` (the function
     the live classifier calls) at several as-of cut-offs per parcel-season,
     so the model learns partial seasons: 1 Jul, 1 Aug, 1 Sep, 1 Oct, 1 Nov
     and the full season.
  3. XGBoost (same family as tier1), capped inverse-frequency x block weights
     (mh_metrics.mh_sample_weights), unchecked soybean at 0.5.
  4. Temperature + per-class bias fitted on Marathwada rows of the out-of-fold probabilities from GroupKFold(5) over
     0.25 deg blocks; abstain rule chosen on those OOF rows.
  5. Evaluation on the FROZEN mh2023 test blocks (never trained or tuned on),
     per cut-off, with Wilson intervals; plus blocked CV on all crops.

    python -m src.train_fused
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

from ._bootstrap import DATA, MODELS, REPORTS, setup_logging
from . import mh_metrics as M

log = setup_logging("train_fused")

SERIES = DATA / "06_fused_series.parquet"
SPLIT = DATA / "splits" / "mh2023_split.json"
CUTOFFS = ((7, 1), (8, 1), (9, 1), (10, 1), (11, 1), None)     # None = full season
# Time-shift augmentation. Almost every cotton and all soybean labels are
# from 2023, a late-monsoon year, and an unaugmented model reads the calendar:
# moving the frozen test parcels 20 days later took Soyabean and Tur recall to
# 0 (src.eval_timeshift), and Dhaswadi 2026 (early monsoon) came out "Onion".
# Each training parcel is also seen with every date moved by these offsets,
# label unchanged, so timing alone stops being a shortcut.
AUG_SHIFTS = (-20, -10, 10, 20)
AUG_CUTOFFS = ((8, 1), (10, 1), None)
MODEL_VERSION = "fused_v4"
SEED = 42
FOCUS = ("Cotton", "Soyabean", "Tur")
RULE = {"p_min": 0.35, "gap_min": 0.10}
LATE_SEASON_STEPS = 19       # season_seen_steps from 1 Nov on: a separate calibration
MH_LAT, MH_LON = (15.6, 22.1), (72.6, 80.9)        # Maharashtra bounding box


# ── 1. imputer ────────────────────────────────────────────────────────────────
def pair_rows(row) -> Tuple[np.ndarray, np.ndarray]:
    from crop_analysis.fused_features import parse_series, sar_rows

    opt, rad = parse_series(row.s1_series, row.refl_series)
    if not opt or not rad:
        return np.zeros((0, 8)), np.zeros(0)
    X = sar_rows(rad, int(row.season_year))
    xs, ys = [], []
    for i, r in enumerate(rad):
        best = min(opt, key=lambda o: abs((o["date"] - r["date"]).days))
        if abs((best["date"] - r["date"]).days) <= 5 and best["ndvi"] is not None:
            xs.append(X[i])
            ys.append(best["ndvi"])
    return (np.asarray(xs).reshape(-1, 8), np.asarray(ys))


def fit_imputer(train: pd.DataFrame, test: pd.DataFrame):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from crop_analysis.fused_features import SarNdviImputer

    def stack(df):
        xs, ys = [], []
        for r in df.itertuples():
            x, y = pair_rows(r)
            if len(y):
                xs.append(x)
                ys.append(y)
        return np.vstack(xs), np.concatenate(ys)

    Xtr, ytr = stack(train)
    Xte, yte = stack(test)
    reg = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=63,
                                        l2_regularization=1.0, random_state=SEED)
    reg.fit(Xtr, ytr)
    pred = np.clip(reg.predict(Xte), -0.2, 1.0)
    resid = yte - pred
    r2 = 1 - np.sum(resid ** 2) / np.sum((yte - yte.mean()) ** 2)
    info = {"train_pairs": int(len(ytr)), "test_pairs": int(len(yte)),
            "test_r2": round(float(r2), 4), "test_rmse": round(float(np.sqrt(np.mean(resid ** 2))), 4),
            "test_mae": round(float(np.mean(np.abs(resid))), 4)}
    log.info("SAR->NDVI imputer: %s", info)
    return SarNdviImputer(reg, float(np.std(resid)), float(r2)), info


# ── 2. features ───────────────────────────────────────────────────────────────
_IMPUTER = None


def _init(imp):
    global _IMPUTER
    _IMPUTER = imp


def _shift(records, days: int):
    if not days:
        return records
    recs = json.loads(records) if isinstance(records, str) else (records or [])
    out = []
    for r in recs:
        d = date.fromisoformat(r["date"]) + timedelta(days=days)
        out.append({**r, "date": d.isoformat()})
    return out


def _feat_job(args) -> List[dict]:
    from crop_analysis.fused_features import build

    gh, crop, year, s1, refl, shifts = args
    rows = []
    for sh in shifts:
        for co in (CUTOFFS if sh == 0 else AUG_CUTOFFS):
            as_of = date(year, *co) if co else None
            f, _ = build(_shift(s1, sh), _shift(refl, sh), year, _IMPUTER, as_of)
            f.update({"geom_hash": gh, "Crop_Name": crop, "shift": sh,
                      "cutoff": "full" if co is None else f"{co[0]:02d}-01"})
            rows.append(f)
    return rows


def build_features(df: pd.DataFrame, imputer, workers: int, shifts=(0,)) -> pd.DataFrame:
    jobs = [(r.geom_hash, r.Crop_Name, int(r.season_year), r.s1_series, r.refl_series, shifts)
            for r in df.itertuples()]
    out: List[dict] = []
    t = time.time()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(imputer,)) as pool:
        for i, rows in enumerate(pool.map(_feat_job, jobs, chunksize=50), 1):
            out.extend(rows)
            if i % 1000 == 0:
                log.info("  features %d/%d (%.1f min)", i, len(jobs), (time.time() - t) / 60)
    return pd.DataFrame(out)


# ── 3-4. model ────────────────────────────────────────────────────────────────
def _xgb():
    from xgboost import XGBClassifier
    return XGBClassifier(n_estimators=500, max_depth=6, learning_rate=0.05, subsample=0.8,
                         colsample_bytree=0.6, min_child_weight=2, reg_lambda=1.0,
                         tree_method="hist", objective="multi:softprob", random_state=SEED,
                         n_jobs=8, eval_metric="mlogloss")


def _temp_fit(logits: np.ndarray, y: np.ndarray) -> float:
    from scipy.optimize import minimize_scalar

    def nll(T):
        z = logits / T
        z = z - z.max(1, keepdims=True)
        lp = z - np.log(np.exp(z).sum(1, keepdims=True))
        return -lp[np.arange(len(y)), y].mean()

    return float(minimize_scalar(nll, bounds=(0.3, 5.0), method="bounded").x)


def _calib_fit(logits: np.ndarray, y: np.ndarray, n_classes: int) -> Tuple[float, np.ndarray]:
    """Temperature + per-class bias (prior shift) by NLL, bias L2-penalised."""
    from scipy.optimize import minimize

    def nll(v):
        T, b = np.exp(v[0]), v[1:]
        z = logits / T + b
        z = z - z.max(1, keepdims=True)
        lp = z - np.log(np.exp(z).sum(1, keepdims=True))
        return -lp[np.arange(len(y)), y].mean() + 0.01 * (b ** 2).sum()

    r = minimize(nll, np.zeros(n_classes + 1), method="L-BFGS-B")
    b = r.x[1:] - r.x[1:].mean()
    return float(np.exp(r.x[0])), b


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def _logits(model, X) -> np.ndarray:
    return np.log(np.clip(model.predict_proba(X), 1e-9, 1.0))


def blocked_oof(X, y, groups, w, n_classes) -> np.ndarray:
    from sklearn.model_selection import GroupKFold

    oof = np.zeros((len(y), n_classes))
    for k, (tr, te) in enumerate(GroupKFold(n_splits=5).split(X, y, groups), 1):
        m = _xgb()
        m.fit(X[tr], y[tr], sample_weight=w[tr])
        lg = np.full((len(te), n_classes), np.log(1e-9))
        lg[:, m.classes_] = _logits(m, X[te])
        oof[te] = lg
        log.info("    fold %d done", k)
    return oof


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--features-cache", default=None,
                    help="parquet of built features to reuse (same FEATURE_VERSION)")
    a = ap.parse_args()
    from crop_analysis.fused_features import FEATURE_VERSION, feature_names
    out_path = MODELS / f"crop_classifier_{MODEL_VERSION}.joblib"

    split = json.loads(SPLIT.read_text())
    test_hashes = set(split["test_geom_hashes"])
    df = pd.read_parquet(SERIES)
    train_mask = M.training_split_mask(df.geom_hash, split)
    log.info("parcel-seasons %d: train %d, frozen test %d", len(df), int(train_mask.sum()),
             int(df.geom_hash.isin(test_hashes).sum()))

    imputer, imp_info = fit_imputer(df[train_mask], df[df.geom_hash.isin(test_hashes)])
    cache = DATA / f"07_features_{FEATURE_VERSION}.parquet"
    if cache.exists():
        F = pd.read_parquet(cache)
        log.info("features from cache %s", cache.name)
    else:
        F = build_features(df, imputer, a.workers)
        F.to_parquet(cache)
    F["shift"] = 0
    # Shifted copies: training parcels only. The frozen test is scored as observed.
    aug_cache = DATA / f"07_features_{FEATURE_VERSION}_shift.parquet"
    if aug_cache.exists():
        A = pd.read_parquet(aug_cache)
        log.info("shifted features from cache %s", aug_cache.name)
    else:
        log.info("building time-shifted copies %s", AUG_SHIFTS)
        A = build_features(df[train_mask], imputer, a.workers, shifts=AUG_SHIFTS)
        A.to_parquet(aug_cache)
    F = pd.concat([F, A], ignore_index=True)
    F = F.merge(df[["geom_hash", "block_id", "qa_checked", "mh_origin", "lat", "lon"]], on="geom_hash", how="left")
    names = feature_names()
    classes = sorted(df.Crop_Name.unique())
    cidx = {c: i for i, c in enumerate(classes)}

    tr = F[M.training_split_mask(F.geom_hash, split)].reset_index(drop=True)
    X = tr[names].to_numpy(float)
    y = tr.Crop_Name.map(cidx).to_numpy()
    groups = tr.block_id.to_numpy()
    w = M.mh_sample_weights(y, groups, M.qa_weights(tr.qa_checked.tolist(), tr.Crop_Name.tolist()))
    log.info("training rows (parcel x cut-off): %d, classes %d", len(y), len(classes))

    oof_cache = DATA / f"07_oof_{MODEL_VERSION}.npy"
    if oof_cache.exists() and np.load(oof_cache).shape == (len(y), len(classes)):
        oof = np.load(oof_cache)
        log.info("out-of-fold logits from cache %s", oof_cache.name)
    else:
        log.info("blocked GroupKFold(5) out-of-fold")
        oof = blocked_oof(X, y, groups, w, len(classes))
        np.save(oof_cache, oof)
    # Calibration on Marathwada out-of-fold rows only: the deployment region.
    mh = (tr.lat.between(*MH_LAT) & tr.lon.between(*MH_LON)).to_numpy()
    log.info("calibration rows inside Maharashtra: %d of %d", int(mh.sum()), len(mh))
    # One calibration for in-season reads, one from 1 Nov on: a single
    # temperature left the full-season model under-confident (ECE 0.17).
    late = (tr.season_seen_steps >= LATE_SEASON_STEPS).to_numpy()
    calib = []
    for lo_steps, sel in ((0, ~late), (LATE_SEASON_STEPS, late)):
        Tg, bg = _calib_fit(oof[mh & sel], y[mh & sel], len(classes))
        calib.append({"min_seen_steps": lo_steps, "temperature": Tg, "class_bias": bg.tolist()})
        log.info("MH calibration from step %d: T=%.3f bias=%s", lo_steps, Tg,
                 dict(zip(classes, np.round(bg, 2))))
    T, bias = calib[0]["temperature"], np.asarray(calib[0]["class_bias"])

    def calibrated(lg, seen):
        out = np.empty_like(lg)
        for g in calib:
            m = np.asarray(seen) >= g["min_seen_steps"]
            out[m] = _softmax(lg[m] / g["temperature"] + np.asarray(g["class_bias"]))
        return out

    p_oof = calibrated(oof, tr.season_seen_steps.to_numpy())
    full = ((tr.cutoff == "full") & (tr["shift"] == 0)).to_numpy()
    cv = {
        "temperature": round(T, 4), "class_bias": dict(zip(classes, np.round(bias, 3).tolist())),
        "ece_full_mh": round(M.ece(p_oof[full & mh], y[full & mh]), 4),
        "balanced_accuracy_full": round(float(_bal_acc(y[full], p_oof[full].argmax(1))), 4),
        "ece_full": round(M.ece(p_oof[full], y[full]), 4),
        "by_cutoff": {c: round(float(_bal_acc(y[((tr.cutoff == c) & (tr["shift"] == 0)).to_numpy()],
                                              p_oof[((tr.cutoff == c) & (tr["shift"] == 0)).to_numpy()].argmax(1))), 4)
                      for c in sorted(tr.cutoff.unique())},
        "shifted_rows_balanced_accuracy": round(float(_bal_acc(y[(tr["shift"] != 0).to_numpy()],
                                                              p_oof[(tr["shift"] != 0).to_numpy()].argmax(1))), 4),
    }
    log.info("blocked OOF: %s", cv)

    model = _xgb()
    model.fit(X, y, sample_weight=w)
    bundle = {
        "tier": "fused", "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
        "augmentation": {"time_shift_days": list(AUG_SHIFTS),
                         "cutoffs": [("full" if c is None else f"{c[0]:02d}-01") for c in AUG_CUTOFFS]}, "feature_names": names,
        "classes": classes, "model": model, "temperature": T, "class_bias": bias.tolist(), "calibration": calib, "imputer": imputer,
        "imputer_info": imp_info, "abstain_rule": RULE, "season": "kharif",
        "cv_protocol": "GroupKFold(5) on 0.25deg spatial blocks, per-cut-off rows grouped by block",
        "cv": cv, "split_version": split.get("version"), "split_hash": M.split_hash(SPLIT),
        "cutoffs": [("full" if c is None else f"{c[0]:02d}-01") for c in CUTOFFS],
        "trained_at": datetime.now(timezone.utc).isoformat(), "n_train_rows": int(len(y)),
        "untrained_kharif_crops": ["Maize (no kharif maize labels)"],
    }

    # ── 5. frozen Marathwada test ──
    te = F[F.geom_hash.isin(test_hashes) & (F["shift"] == 0)].reset_index(drop=True)
    p_te = calibrated(_full_logits(model, te[names].to_numpy(float), len(classes)),
                      te.season_seen_steps.to_numpy())
    report = {"imputer": imp_info, "blocked_cv": cv, "test": {}}
    for c in sorted(te.cutoff.unique()):
        m = (te.cutoff == c).to_numpy()
        yt = te.Crop_Name[m].tolist()
        pm = p_te[m]
        pred = [classes[i] for i in pm.argmax(1)]
        srt = np.sort(pm, 1)[:, ::-1]
        keep = (srt[:, 0] >= RULE["p_min"]) & (srt[:, 0] - srt[:, 1] >= RULE["gap_min"])
        pred_rule = [p if k else None for p, k in zip(pred, keep)]
        yi = np.array([cidx.get(v, -1) for v in yt])
        ok = yi >= 0
        report["test"][c] = {
            "n": int(m.sum()), "coverage_at_rule": round(float(keep.mean()), 4),
            "ece": round(M.ece(pm[ok], yi[ok]), 4) if ok.any() else None,
            "argmax": M.per_class_report(yt, pred, FOCUS),
            "at_rule": M.per_class_report(yt, pred_rule, FOCUS),
            "confusions": _focus_confusions(yt, pred),
        }
        r = report["test"][c]["argmax"]
        log.info("test %-5s n=%d cov=%.2f | Cotton R=%s P=%s | Soy R=%s P=%s | Tur R=%s | ECE=%s",
                 c, m.sum(), keep.mean(), r["Cotton"]["recall"], r["Cotton"]["precision"],
                 r["Soyabean"]["recall"], r["Soyabean"]["precision"], r["Tur"]["recall"],
                 report["test"][c]["ece"])
    bundle["test_metrics"] = report["test"]
    joblib.dump(bundle, out_path, compress=3)
    # Region support: training parcels per (crop, ecoregion), read by the guard.
    from crop_analysis.region_guard import VIABLE
    sup = df[train_mask].groupby(["Crop_Name", "ecoregion"]).size()
    counts: Dict[str, Dict[str, int]] = {}
    for (c, e), n in sup.items():
        counts.setdefault(c, {})[e] = int(n)
    out_path.with_suffix(".region_support.json").write_text(
        json.dumps({"viable_threshold": VIABLE, "counts": counts}, indent=1))
    M.dump_json(report, REPORTS / f"{MODEL_VERSION}_eval.json")
    _write_md(report, REPORTS / f"{MODEL_VERSION}_eval.md", MODEL_VERSION)
    log.info("wrote %s", out_path)
    return 0


def _focus_confusions(y_true, y_pred) -> Dict[str, Dict[str, int]]:
    """Where Cotton, Soyabean and Tur actually went, including Onion and Rice.

    Recall alone hides a cotton field called Onion. That is the 2024/2026 failure.
    """
    out: Dict[str, Dict[str, int]] = {}
    for t, p in zip(y_true, y_pred):
        if t not in FOCUS:
            continue
        out.setdefault(t, {})
        out[t][p] = out[t].get(p, 0) + 1
    return {t: dict(sorted(c.items(), key=lambda kv: -kv[1])) for t, c in out.items()}


def _full_logits(model, X, n_classes):
    lg = np.full((len(X), n_classes), np.log(1e-9))
    lg[:, model.classes_] = _logits(model, X)
    return lg


def _bal_acc(y, p) -> float:
    from sklearn.metrics import balanced_accuracy_score
    return balanced_accuracy_score(y, p) if len(y) else float("nan")


def _write_md(rep: Dict, path, version: str) -> None:
    lines = [f"# Fused kharif classifier (`crop_classifier_{version}`) - evaluation", "",
             f"SAR->NDVI imputer on frozen test parcels: {rep['imputer']}", "",
             f"Blocked CV (training rows): {rep['blocked_cv']}", "",
             "## Frozen Marathwada test blocks (95% Wilson intervals)", "",
             "| As of | n | coverage @rule | Cotton R | Cotton P | Soyabean R | Soyabean P | Tur R | ECE |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c, r in rep["test"].items():
        a = r["argmax"]
        fmt = lambda d, k: f"{d[k]} {d[k + '_ci95']}"  # noqa: E731
        lines.append(f"| {c} | {r['n']} | {r['coverage_at_rule']} | {fmt(a['Cotton'], 'recall')} | "
                     f"{fmt(a['Cotton'], 'precision')} | {fmt(a['Soyabean'], 'recall')} | "
                     f"{fmt(a['Soyabean'], 'precision')} | {a['Tur']['recall']} | {r['ece']} |")
    lines += ["", "## Where the three crops were sent", ""]
    for c, r in rep["test"].items():
        lines.append(f"### {c}")
        for crop, dest in (r.get("confusions") or {}).items():
            lines.append(f"- {crop}: {dest}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
