"""
Phase 4c — Honest reporting.

reports/oof_proba_tier{N}.npy -> reports/evaluation_tier{N}.md + PNGs

Produces every item classification_model.md F.2 makes mandatory, including the
ones that are uncomfortable:

  * blocked AND random numbers side by side — the gap is the leakage measure
  * 18x18 row-normalised confusion matrix from the blocked folds
  * per-class precision / recall / F1 / support (never aggregate-only)
  * reliability diagram + ECE on the COMPOSED pipeline confidence
  * precision-coverage curve with the 0.25 operating point marked
  * feature importance — if NDVI_t01 dominates, the model is reading sowing
    date, i.e. season, i.e. the label
  * adversarial validation: how well can ecoregion be predicted FROM the
    features? That AUC is how much geography the model has to work with
  * the CropGrowthCurves template-match baseline — a zero-parameter competitor
    already in the codebase. If it ties the ML model, we do not need the model
  * the rejection ledger, by class and reason

Run:  python -m src.evaluate --tier 0
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from ._bootstrap import DATA, MODELS, PROJECT, REPORTS, setup_logging
from .features import META_COLS
from .train import PIPELINE_GATE, _pipeline_confidence

log = setup_logging("evaluate")

matplotlib = None


def _plt():
    global matplotlib
    import matplotlib as _m
    _m.use("Agg")
    import matplotlib.pyplot as plt
    matplotlib = _m
    return plt


# =============================================================================
# the zero-parameter competitor
# =============================================================================
def _template_baseline(X: np.ndarray, feat_cols: List[str],
                       classes: List[str]) -> Optional[np.ndarray]:
    """
    Nearest expected NDVI curve from CropGrowthCurves — no learning at all.

    The pipeline already ships these ICAR-derived curves. If this matches the
    trained model, the honest conclusion is that the model adds nothing and the
    curves should be used directly.
    """
    from config import CropGrowthCurves

    ndvi_cols = [c for c in feat_cols if c.startswith("NDVI_t")]
    if len(ndvi_cols) < 5:
        return None
    idx = [feat_cols.index(c) for c in sorted(ndvi_cols)]
    obs = X[:, idx]
    n_grid = obs.shape[1]

    templates = []
    for crop in classes:
        try:
            curve = CropGrowthCurves.get_expected_curve(crop, num_points=200)
        except Exception:                                  # noqa: BLE001
            templates.append(np.full(n_grid, np.nan))
            continue
        days, ndvi = curve[:, 0], curve[:, 1]
        # Resample onto the same normalised 0-1 grid the features live on, so
        # the comparison is like-for-like despite tier 0 discarding duration.
        t_norm = (days - days.min()) / max(days.max() - days.min(), 1e-9)
        templates.append(np.interp(np.linspace(0, 1, n_grid), t_norm, ndvi))
    T = np.vstack(templates)

    if np.isnan(T).all():
        return None
    T = np.nan_to_num(T, nan=0.0)

    # Negative RMSE -> softmax, so the baseline emits a comparable distribution.
    d = np.sqrt(((obs[:, None, :] - T[None, :, :]) ** 2).mean(axis=2))
    z = -d / max(d.std(), 1e-6)
    z -= z.max(axis=1, keepdims=True)
    p = np.exp(z)
    return p / p.sum(axis=1, keepdims=True)


def _abstain_sweep(
    proba: np.ndarray, y: np.ndarray, n_scenes: np.ndarray,
) -> List[Dict[str, Any]]:
    """
    Joint sweep of the abstain rule's three knobs.

    Reports, for each candidate `n_scenes_min` x `p_min`, the coverage and the
    precision on what survives. The rule to apply: take the LOOSEST setting that
    still clears the 0.70 precision gate — a stricter one only discards cycles
    the model was getting right, and a looser one lets wrong crop names into
    ICAR-curve scoring.
    """
    pred = proba.argmax(axis=1)
    correct = (pred == y)
    conf = _pipeline_confidence(proba)
    srt = np.sort(proba, axis=1)[:, ::-1]
    gap = srt[:, 0] - srt[:, 1]

    rows: List[Dict[str, Any]] = []
    for n_min in (5, 6, 7, 8, 10):
        for p_min in (0.25, 0.30, 0.35, 0.40, 0.50):
            keep = (n_scenes >= n_min) & (conf >= p_min) & (gap >= 0.10)
            rows.append({
                "n_scenes_min": n_min,
                "p_min": p_min,
                "coverage": round(float(keep.mean()), 4),
                "precision": round(float(correct[keep].mean()), 4) if keep.any() else 0.0,
                "n_kept": int(keep.sum()),
            })
    return rows


def _adversarial_ecoregion(X: np.ndarray, ecos: np.ndarray,
                           blocks: np.ndarray) -> Dict[str, float]:
    """
    Can the features predict WHERE a parcel is? Whatever accuracy this reaches
    is geography the classifier could be using as a shortcut instead of
    phenology.
    """
    from sklearn.metrics import balanced_accuracy_score
    from sklearn.model_selection import GroupKFold

    from .train import _make_model

    le = {e: i for i, e in enumerate(sorted(set(ecos)))}
    y = np.array([le[e] for e in ecos])
    if len(le) < 2:
        return {"balanced_accuracy": float("nan"), "n_classes": len(le)}

    pred = np.zeros(len(y), dtype=int)
    for tr, te in GroupKFold(n_splits=min(5, len(np.unique(blocks)))).split(
            X, y, groups=blocks):
        m = _make_model(len(le), "xgboost").fit(X[tr], y[tr])
        pred[te] = m.predict(X[te])
    return {
        "balanced_accuracy": round(float(balanced_accuracy_score(y, pred)), 4),
        "n_classes": len(le),
        "chance": round(1.0 / len(le), 4),
    }


# =============================================================================
# plots
# =============================================================================
def _plot_confusion(y, pred, classes, path, title):
    from sklearn.metrics import confusion_matrix

    plt = _plt()
    cm = confusion_matrix(y, pred, labels=range(len(classes)))
    row = cm.sum(axis=1, keepdims=True)
    norm = np.divide(cm, np.maximum(row, 1), dtype=float)

    fig, ax = plt.subplots(figsize=(11, 9.5))
    im = ax.imshow(norm, cmap="magma", vmin=0, vmax=1)
    ax.set_xticks(range(len(classes)), classes, rotation=90, fontsize=8)
    ax.set_yticks(range(len(classes)), classes, fontsize=8)
    ax.set_xlabel("predicted"); ax.set_ylabel("true")
    ax.set_title(title, fontsize=11)
    for i in range(len(classes)):
        for j in range(len(classes)):
            if norm[i, j] >= 0.04:
                ax.text(j, i, f"{norm[i,j]*100:.0f}", ha="center", va="center",
                        fontsize=6.5,
                        color="white" if norm[i, j] < 0.55 else "black")
    fig.colorbar(im, ax=ax, fraction=0.046, label="row-normalised share")
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)
    return norm


def _plot_reliability(conf, correct, path, ece, n_bins=10):
    plt = _plt()
    edges = np.linspace(0, 1, n_bins + 1)
    xs, ys, ns = [], [], []
    for i in range(n_bins):
        m = (conf > edges[i]) & (conf <= edges[i + 1])
        if m.sum() == 0:
            continue
        xs.append(conf[m].mean()); ys.append(correct[m].mean()); ns.append(int(m.sum()))

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(6.2, 7),
                                  gridspec_kw={"height_ratios": [3, 1]})
    ax.plot([0, 1], [0, 1], "--", color="grey", lw=1, label="perfect")
    ax.plot(xs, ys, "o-", color="#c1440e", label="observed")
    ax.axvline(PIPELINE_GATE, color="#1f4e79", lw=1.2, ls=":",
               label=f"pipeline gate {PIPELINE_GATE}")
    ax.set_xlabel("mean predicted confidence"); ax.set_ylabel("observed accuracy")
    ax.set_title(f"Reliability (ECE = {ece:.4f})")
    ax.legend(fontsize=8); ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax2.bar(xs, ns, width=1.0 / n_bins * 0.85, color="#1f4e79")
    ax2.set_xlabel("confidence"); ax2.set_ylabel("n"); ax2.set_xlim(0, 1)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def _plot_precision_coverage(conf, correct, path):
    plt = _plt()
    ts = np.linspace(0.0, 0.95, 60)
    cov, prec = [], []
    for t in ts:
        m = conf >= t
        cov.append(m.mean())
        prec.append(correct[m].mean() if m.any() else np.nan)

    fig, ax = plt.subplots(figsize=(6.2, 4.4))
    ax.plot(cov, prec, "-", color="#c1440e")
    m = conf >= PIPELINE_GATE
    if m.any():
        ax.plot([m.mean()], [correct[m].mean()], "o", ms=9, color="#1f4e79",
                label=f"gate {PIPELINE_GATE}: cov={m.mean():.2f} "
                      f"prec={correct[m].mean():.2f}")
    ax.axhline(0.70, ls="--", lw=1, color="grey", label="ship gate prec 0.70")
    ax.set_xlabel("coverage (share of cycles classified)")
    ax.set_ylabel("precision on classified cycles")
    ax.set_title("Precision vs coverage — composed pipeline confidence")
    ax.legend(fontsize=8); ax.grid(alpha=0.25)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def _plot_importance(names, gains, path, top=30):
    plt = _plt()
    order = np.argsort(gains)[::-1][:top]
    fig, ax = plt.subplots(figsize=(6.6, max(4, 0.24 * len(order))))
    ax.barh([names[i] for i in order][::-1], [gains[i] for i in order][::-1],
            color="#1f4e79")
    ax.set_xlabel("gain importance")
    ax.set_title(f"Top {len(order)} features")
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


# =============================================================================
# driver
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", type=int, default=0, choices=[0, 1])
    args = ap.parse_args()

    tier = args.tier
    rep_json = REPORTS / f"cv_report_tier{tier}.json"
    proba_npy = REPORTS / f"oof_proba_tier{tier}.npy"
    feats = DATA / f"03_features_tier{tier}.parquet"

    for f in (rep_json, proba_npy, feats):
        if not f.exists():
            log.error("missing %s — run `python -m src.train --tier %d` first", f, tier)
            return 1

    from sklearn.metrics import balanced_accuracy_score
    from sklearn.preprocessing import LabelEncoder

    report = json.loads(rep_json.read_text(encoding="utf-8"))
    proba = np.load(proba_npy)
    df = pd.read_parquet(feats)

    feat_cols = [c for c in df.columns if c not in META_COLS]
    X = np.nan_to_num(df[feat_cols].to_numpy(dtype=float))
    classes = report["classes"]
    le = LabelEncoder().fit(classes)
    y = le.transform(df["Crop_Name"])
    pred = proba.argmax(axis=1)
    conf = _pipeline_confidence(proba)
    correct = (pred == y).astype(float)

    log.info("tier %d: %d rows, %d features, %d classes",
             tier, len(df), len(feat_cols), len(classes))

    # ── plots ─────────────────────────────────────────────────────────────
    cm_png = REPORTS / f"confusion_tier{tier}.png"
    rel_png = REPORTS / f"reliability_tier{tier}.png"
    pc_png = REPORTS / f"precision_coverage_tier{tier}.png"
    imp_png = REPORTS / f"importance_tier{tier}.png"

    norm = _plot_confusion(y, pred, classes, cm_png,
                           f"Tier {tier} — blocked-CV confusion (row %)")
    _plot_reliability(conf, correct, rel_png, report["blocked"]["calibrated"]["ece"])
    _plot_precision_coverage(conf, correct, pc_png)

    # ── feature importance on a blocked refit ─────────────────────────────
    from sklearn.model_selection import GroupKFold

    from .train import _make_model, _sample_weights

    blocks = df["block_id"].to_numpy()
    tr, te = next(iter(GroupKFold(n_splits=5).split(X, y, groups=blocks)))
    # Importance always comes from a tree model, whichever estimator was
    # selected — a linear pipeline has coefficients, not gains, and mixing the
    # two in one table would not be comparable.
    m = _make_model(len(classes), "xgboost").fit(
        X[tr], y[tr], sample_weight=_sample_weights(y[tr], blocks[tr]))
    gains = np.asarray(m.feature_importances_, dtype=float)
    _plot_importance(feat_cols, gains, imp_png)
    top_feats = sorted(zip(feat_cols, gains), key=lambda kv: -kv[1])[:12]

    # ── competitors and diagnostics ───────────────────────────────────────
    # n_scenes_real lives in the cycles table, not the feature matrix (it is a
    # tier-1 feature and metadata at tier 0), so join it back for the sweep.
    cyc = pd.read_parquet(DATA / "02_cycles.parquet")[["geom_hash", "n_scenes_real"]]
    n_scenes = (
        df[["geom_hash"]].merge(cyc, on="geom_hash", how="left")["n_scenes_real"]
        .fillna(0).to_numpy()
    )
    sweep = _abstain_sweep(proba, y, n_scenes)

    tmpl = _template_baseline(X, feat_cols, classes)
    tmpl_ba = (round(float(balanced_accuracy_score(y, tmpl.argmax(axis=1))), 4)
               if tmpl is not None else None)
    adv = _adversarial_ecoregion(X, df["ecoregion"].to_numpy(), blocks)

    # ── confusion pairs worth naming ──────────────────────────────────────
    pairs = []
    for i in range(len(classes)):
        for j in range(len(classes)):
            if i != j and norm[i, j] >= 0.08:
                pairs.append((classes[i], classes[j], round(float(norm[i, j]), 3)))
    pairs.sort(key=lambda t: -t[2])

    # ── rejection ledger ──────────────────────────────────────────────────
    rej_summary = ""
    rej_path = DATA / "rejections.csv"
    if rej_path.exists():
        rj = pd.read_csv(rej_path)
        if len(rj):
            by_reason = rj["reason"].value_counts()
            rej_summary = "\n".join(
                f"| {r} | {n} |" for r, n in by_reason.items())

    # ── markdown ──────────────────────────────────────────────────────────
    b = report["blocked"]["calibrated"]
    r = report["random_diagnostic"]["calibrated"]
    loeo = report.get("loeo", {}).get("calibrated")

    lines: List[str] = []
    A = lines.append
    A(f"# Crop Classifier — Tier {tier} Evaluation")
    A("")
    A(f"Generated {report['generated_at']}  ·  {len(df)} cycles  ·  "
      f"{len(feat_cols)} features  ·  {len(classes)} classes")
    A("")
    A("## Headline")
    A("")
    A("| Protocol | Balanced acc. | Macro F1 | ECE | Min class recall | "
      "Coverage@0.25 | Precision@0.25 |")
    A("|---|---|---|---|---|---|---|")
    A(f"| **Blocked (the real number)** | **{b['balanced_accuracy']:.4f}** | "
      f"{b['macro_f1']:.4f} | {b['ece']:.4f} | {b['min_class_recall']:.4f} "
      f"({b['worst_class']}) | {b['coverage_at_gate']:.3f} | "
      f"{b['precision_at_gate']:.3f} |")
    A(f"| Random *(diagnostic only)* | {r['balanced_accuracy']:.4f} | "
      f"{r['macro_f1']:.4f} | {r['ece']:.4f} | {r['min_class_recall']:.4f} | "
      f"{r['coverage_at_gate']:.3f} | {r['precision_at_gate']:.3f} |")
    if loeo:
        A(f"| Leave-one-ecoregion-out | {loeo['balanced_accuracy']:.4f} | "
          f"{loeo['macro_f1']:.4f} | {loeo['ece']:.4f} | "
          f"{loeo['min_class_recall']:.4f} | {loeo['coverage_at_gate']:.3f} | "
          f"{loeo['precision_at_gate']:.3f} |")
    A("")
    A(f"**Leakage gap (random − blocked): {report['leakage_gap']:+.4f}** balanced "
      "accuracy. This is how much of a random-split score would have been "
      "geography rather than crop.")
    A(f"Chance is {1/len(classes):.4f}.")
    A("")
    A("## Ship gates")
    A("")
    A("| Gate | Result |")
    A("|---|---|")
    for k, v in report["gates"].items():
        A(f"| `{k}` | {'PASS' if v else 'FAIL'} |")
    A("")
    A(f"**{'ALL GATES PASSED' if report['gates_passed'] else 'NOT READY TO SHIP'}**")
    A("")
    A("## Competitors")
    A("")
    A("| Model | Blocked balanced acc. |")
    A("|---|---|")
    A(f"| **XGBoost (this model)** | **{b['balanced_accuracy']:.4f}** |")
    for name, v in report["blocked"].get("baselines", {}).items():
        A(f"| {name} | {v:.4f} |")
    if tmpl_ba is not None:
        A(f"| CropGrowthCurves template match (zero parameters) | {tmpl_ba:.4f} |")
    A("")
    if tmpl_ba is not None and tmpl_ba >= b["balanced_accuracy"] - 0.02:
        A("> The zero-parameter template match is within 2 points of the trained "
          "model. On this evidence the ML model is not earning its complexity — "
          "use the reference curves directly, or fix the features (tier 1) "
          "before shipping a classifier.")
        A("")
    A("## Abstain-rule sweep")
    A("")
    A("`n_scenes_min` and `p_min` were set by judgement in the design doc. This "
      "is the measured version: **take the loosest setting that still clears "
      "0.70 precision.** A stricter one only discards cycles the model was "
      "getting right; a looser one lets wrong crop names reach ICAR-curve "
      "scoring, where they swing up to 45% of the risk index.")
    A("")
    A("| n_scenes_min | p_min | Coverage | Precision | N kept |")
    A("|---|---|---|---|---|")
    for s_ in sweep:
        flag = " **<-**" if (s_["precision"] >= 0.70 and s_["coverage"] >= 0.40) else ""
        A(f"| {s_['n_scenes_min']} | {s_['p_min']:.2f} | {s_['coverage']:.3f} | "
          f"{s_['precision']:.3f} | {s_['n_kept']}{flag} |")
    A("")
    viable = [s_ for s_ in sweep if s_["precision"] >= 0.70]
    if viable:
        best = max(viable, key=lambda s_: s_["coverage"])
        A(f"Loosest setting clearing 0.70 precision: "
          f"**n_scenes_min={best['n_scenes_min']}, p_min={best['p_min']:.2f}** "
          f"(coverage {best['coverage']:.3f}, precision {best['precision']:.3f}).")
    else:
        A("> **No setting clears 0.70 precision.** The model cannot be made "
          "safe by thresholding alone — do not ship it. Fix the features "
          "(tier 1) or keep classification disabled.")
    A("")
    A("## Geography check (adversarial validation)")
    A("")
    A(f"Predicting **ecoregion** from the same features reaches "
      f"{adv['balanced_accuracy']:.4f} balanced accuracy across "
      f"{adv['n_classes']} regions (chance {adv['chance']:.4f}). The higher "
      "this is, the more location signal the features carry — and the more a "
      "high random-split score should be distrusted.")
    A("")
    A("## Per-class performance (blocked)")
    A("")
    A("| Crop | Precision | Recall | F1 | Support |")
    A("|---|---|---|---|---|")
    for c in classes:
        pc = b["per_class"].get(c)
        if pc:
            A(f"| {c} | {pc['precision']:.3f} | {pc['recall']:.3f} | "
              f"{pc['f1']:.3f} | {pc['support']} |")
    A("")
    A("## Confusions above 8% of a class")
    A("")
    if pairs:
        A("| True | Predicted as | Share |")
        A("|---|---|---|")
        for t_, p_, s_ in pairs[:25]:
            A(f"| {t_} | {p_} | {s_:.1%} |")
    else:
        A("None above threshold.")
    A("")
    A("## Top features")
    A("")
    A("| Feature | Gain |")
    A("|---|---|")
    for n_, g_ in top_feats:
        A(f"| `{n_}` | {g_:.4f} |")
    A("")
    if any(n_.endswith(("_t01", "_t02", "_t15")) for n_, _ in top_feats[:4]):
        A("> Endpoint features dominate. On a normalised time axis the endpoints "
          "encode where the cycle starts and ends, i.e. season — check the "
          "leakage gap and the LOEO row before trusting this.")
        A("")
    if rej_summary:
        A("## Rejection ledger")
        A("")
        A("Parcels dropped before training, by reason. A class dominated by "
          "`no_cycle_detected` is a finding about Stage 3, not a data problem.")
        A("")
        A("| Reason | N |")
        A("|---|---|")
        A(rej_summary)
        A("")
    A("## Figures")
    A("")
    for p in (cm_png, rel_png, pc_png, imp_png):
        A(f"- `{p.name}`")
    A("")

    out_md = REPORTS / f"evaluation_tier{tier}.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")

    extra = {
        "abstain_sweep": sweep,
        "template_baseline_balanced_accuracy": tmpl_ba,
        "adversarial_ecoregion": adv,
        "top_features": [[n, float(g)] for n, g in top_feats],
        "confusion_pairs_over_8pct": pairs[:40],
    }
    (REPORTS / f"evaluation_extra_tier{tier}.json").write_text(
        json.dumps(extra, indent=2), encoding="utf-8")

    log.info("")
    log.info("=" * 68)
    log.info("blocked balanced accuracy : %.4f   (chance %.4f)",
             b["balanced_accuracy"], 1 / len(classes))
    log.info("random  balanced accuracy : %.4f   <- diagnostic only",
             r["balanced_accuracy"])
    log.info("leakage gap               : %+.4f", report["leakage_gap"])
    if tmpl_ba is not None:
        log.info("template-match baseline   : %.4f", tmpl_ba)
    log.info("ecoregion adversarial     : %.4f (chance %.4f)",
             adv["balanced_accuracy"], adv["chance"])
    log.info("precision @ gate 0.25     : %.3f  (coverage %.3f)",
             b["precision_at_gate"], b["coverage_at_gate"])
    log.info("gates passed              : %s", report["gates_passed"])
    viable = [s_ for s_ in sweep if s_["precision"] >= 0.70]
    if viable:
        best = max(viable, key=lambda s_: s_["coverage"])
        log.info("abstain sweep best        : n_scenes_min=%d p_min=%.2f "
                 "-> coverage %.3f precision %.3f",
                 best["n_scenes_min"], best["p_min"],
                 best["coverage"], best["precision"])
    else:
        log.warning("abstain sweep             : NO setting reaches 0.70 "
                    "precision — thresholding cannot make this model safe")
    log.info("")
    log.info("wrote %s", out_md.relative_to(PROJECT))
    log.info("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
