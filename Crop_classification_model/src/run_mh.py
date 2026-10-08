"""
Marathwada retrain driver — accuracy plan Track A, steps A3 (post-extraction)
through A6.

    data/01_scenes_augmented.parquet + data/01_scenes_mh.parquet
      -> data/01_scenes_mhfull.parquet                       (merge)
      -> data/02_cycles_mh.parquet, 02_cycles_mh_poly.parquet (cycles + Kharif check)
      -> data/03_features_tier1_mh.parquet, ..._mh_poly       (features, both footprints)
      -> reports/mh_phenology_flags.csv, mh_label_review.csv  (A4 label QA)
      -> models/crop_classifier_tier1_mh_v1.joblib            (A5 retrain)
      -> reports/mh_eval.json, reports/mh_eval.md             (A6 evaluation)

Every stage reuses the existing pipeline: src.cycles and src.features run
unchanged (in-process, with the shared bin-grid anchor pinned and their output
paths redirected so nothing shipped is overwritten), the estimator, nested
temperature calibration and bundle layout are train.py's, and the extra
feature variants (augmentation, in-season truncation) are built from the same
functions as src.features (see mh_features.parity_check).

THE FROZEN SPLIT IS LAW. Rows whose geom_hash is in `excluded_from_training`
or `test_geom_hashes` (data/splits/mh2023_split.json) never enter training,
cross-validation, calibration, abstain-rule selection, OOD fitting or
augmentation. `_assert_no_leak` checks this at every point a training matrix is
formed.

    python -m src.run_mh                      # full run (needs 01_scenes_mh.parquet)
    python -m src.run_mh --from-stage train   # reuse cycles/features already built
    python -m src.run_mh --dev                # small end-to-end proof run in a scratch dir
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ._bootstrap import DATA, MODELS, PROJECT, REPORTS, setup_logging
from . import mh_metrics as M

log = setup_logging("run_mh")

PY = sys.executable
ANCHOR = date(2020, 12, 6)          # the shared extraction grid (see extract.global_bin_anchor)
SEED = 42
N_SPLITS = 5
SHIPPED_BLOCKED_BAL_ACC = 0.7492
STAGES = ("merge", "cycles", "features", "train", "eval")
KHARIF = {"kharif", "late_kharif"}
FOCUS = ("Cotton", "Soyabean", "Tur")
DESIGN_RULE = {"p_min": 0.35, "gap_min": 0.10, "n_scenes_min": 8}
GATE = {"value": 0.85, "ci_lower": 0.80, "ece": 0.05}


# =============================================================================
# configuration
# =============================================================================
class Paths:
    def __init__(self, a: argparse.Namespace):
        self.work = Path(a.workdir) if a.workdir else DATA
        self.reports = Path(a.reports) if a.reports else REPORTS
        self.work.mkdir(parents=True, exist_ok=True)
        self.reports.mkdir(parents=True, exist_ok=True)
        self.parcels = Path(a.parcels) if a.parcels else DATA / "00_parcels_mh_full.parquet"
        self.scenes_base = Path(a.scenes_base) if a.scenes_base else DATA / "01_scenes_augmented.parquet"
        self.scenes_new = Path(a.scenes_new) if a.scenes_new else DATA / "01_scenes_mh.parquet"
        self.split = Path(a.split) if a.split else DATA / "splits" / "mh2023_split.json"
        self.scenes = self.work / "01_scenes_mhfull.parquet"
        self.model_out = Path(a.model_out) if a.model_out else MODELS / "crop_classifier_tier1_mh_v1.joblib"
        self.shipped = MODELS / "crop_classifier_tier1_v1.joblib"
        self.shares = Path(a.shares) if a.shares else DATA / "reference" / "district_kharif_shares.csv"

    def cycles(self, kind: str, stage: bool = False) -> Path:
        k = "" if kind == "bbox" else "_poly"
        return self.work / f"02_cycles_mh{k}{'_stage' if stage else ''}.parquet"

    def features(self, kind: str) -> Path:
        k = "" if kind == "bbox" else "_poly"
        return self.work / f"03_features_tier1_mh{k}.parquet"

    def suffix(self, kind: str) -> str:
        return "_mh" if kind == "bbox" else "_mh_poly"


# =============================================================================
# stage 1 — merge scenes
# =============================================================================
def merge_scenes(P: Paths) -> None:
    for f in (P.scenes_base, P.scenes_new):
        if not f.exists():
            raise SystemExit(f"missing {f}")
    base = pd.read_parquet(P.scenes_base)
    new = pd.read_parquet(P.scenes_new)
    out = pd.concat([base, new], ignore_index=True)
    before = len(out)
    # Same rule as run_augmented.merge_scenes.
    out = out.drop_duplicates(subset=["geom_hash", "geom_kind", "bin_start"])
    out.to_parquet(P.scenes, index=False)
    log.info("merged scenes: %d base + %d new = %d rows (%d dupes dropped); "
             "parcels bbox=%d poly=%d", len(base), len(new), len(out), before - len(out),
             out[out.geom_kind == "bbox"].geom_hash.nunique(),
             out[out.geom_kind == "poly"].geom_hash.nunique())


def check_new_scenes(P: Paths) -> Dict[str, Any]:
    """Is 01_scenes_mh.parquet complete (every new parcel present, both kinds)?"""
    import geopandas as gpd
    new = gpd.read_parquet(DATA / "00_parcels_mh_new.parquet")
    if not P.scenes_new.exists():
        return {"exists": False}
    s = pd.read_parquet(P.scenes_new, columns=["geom_hash", "geom_kind"])
    have = {k: set(g.geom_hash) for k, g in s.groupby("geom_kind")}
    want = set(new.geom_hash)
    return {"exists": True, "n_new_parcels": len(want),
            "bbox": len(want & have.get("bbox", set())),
            "poly": len(want & have.get("poly", set()))}


# =============================================================================
# stage 2 — cycles (src.cycles, in-process, chunked across worker processes)
# =============================================================================
def _pin_and_redirect(module, out_path: Path) -> None:
    import src.extract as ex
    ex.BIN_ANCHOR_OVERRIDE = ANCHOR
    # The stages log `OUT.relative_to(PROJECT)` after writing; outputs outside
    # the project (dev runs) would raise there. Any path is relative to its drive.
    module.PROJECT = Path(Path(out_path).resolve().anchor)


def cycles_inprocess(parcels: Path, scenes: Path, out: Path, rejects: Path,
                     kind: str) -> int:
    import src.cycles as c
    _pin_and_redirect(c, out)
    c.REJECTS = Path(rejects)                     # never data/rejections.csv
    argv = sys.argv
    sys.argv = ["cycles", "--parcels", str(parcels), "--scenes", str(scenes),
                "--out", str(out), "--geom-kind", kind, "--attribution", "season"]
    try:
        return int(c.main() or 0)
    finally:
        sys.argv = argv


def run_cycles(P: Paths, kind: str, workers: int) -> None:
    import geopandas as gpd
    out, rej = P.cycles(kind, stage=True), P.reports / f"rejections_mh_{kind}.csv"
    if workers <= 1:
        rc = cycles_inprocess(P.parcels, P.scenes, out, rej, kind)
        if rc:
            raise SystemExit(f"cycles failed ({kind})")
        return
    tmp = P.work / f"_chunks_{kind}"
    tmp.mkdir(exist_ok=True)
    parcels = gpd.read_parquet(P.parcels)
    sc = pd.read_parquet(P.scenes)
    sc = sc[sc.geom_kind == kind]
    parcels = parcels[parcels.geom_hash.isin(set(sc.geom_hash))].reset_index(drop=True)
    order = np.argsort(parcels.geom_hash.to_numpy())          # deterministic chunks
    chunks = np.array_split(order, workers)
    procs = []
    for i, idx in enumerate(chunks):
        pc = parcels.iloc[idx]
        pp, sp = tmp / f"parcels_{i}.parquet", tmp / f"scenes_{i}.parquet"
        pc.to_parquet(pp, index=False)
        sc[sc.geom_hash.isin(set(pc.geom_hash))].to_parquet(sp, index=False)
        logf = open(tmp / f"cycles_{i}.log", "w", encoding="utf-8")
        cmd = [PY, "-u", "-m", "src.run_mh", "_cycles_chunk", str(pp), str(sp),
               str(tmp / f"cycles_{i}.parquet"), str(tmp / f"rej_{i}.csv"), kind]
        procs.append((subprocess.Popen(cmd, cwd=PROJECT, stdout=logf, stderr=subprocess.STDOUT), logf))
        log.info("  cycles[%s] worker %d: %d parcels", kind, i, len(pc))
    del sc
    t0 = time.time()
    for p, f in procs:
        p.wait()
        f.close()
    log.info("  cycles[%s] workers done in %.1f min", kind, (time.time() - t0) / 60)
    outs = [pd.read_parquet(f) for f in sorted(tmp.glob("cycles_*.parquet"))]
    rejs = [pd.read_csv(f) for f in sorted(tmp.glob("rej_*.csv")) if f.stat().st_size > 2]
    if not outs:
        raise SystemExit(f"cycles produced nothing ({kind}); see {tmp}")
    for i, (p, _) in enumerate(procs):
        if p.returncode not in (0, 1):
            raise SystemExit(f"cycles worker {i} crashed ({p.returncode}); see {tmp}/cycles_{i}.log")
    pd.concat(outs, ignore_index=True).to_parquet(out, index=False)
    (pd.concat(rejs, ignore_index=True) if rejs else pd.DataFrame()).to_csv(rej, index=False)
    log.info("  cycles[%s]: %d attributed, %d rejected", kind,
             sum(len(o) for o in outs), sum(len(r) for r in rejs))


def kharif_check(P: Paths, kind: str, parcels: pd.DataFrame) -> Dict[str, Any]:
    """
    A3: every NEW Marathwada label must sit on a Kharif cycle per the crop
    calendar (cotton Date 2023-07-10, soybean Date 2023-08-10). Others are
    rejected and logged; survey containment is recorded as a flag only, because
    the detector's sowing lags the true sowing and cotton was surveyed right
    after sowing.
    """
    cyc = pd.read_parquet(P.cycles(kind, stage=True))
    meta = parcels.set_index("geom_hash")
    cyc["mh_origin"] = cyc.geom_hash.map(meta["mh_origin"])
    new = cyc.mh_origin == "new"
    season_ok = cyc["label_season"].isin(KHARIF) & cyc["season_consistent"].fillna(False).astype(bool)
    bad = new & ~season_ok
    s = pd.to_datetime(cyc.sowing_date)
    h = pd.to_datetime(cyc.harvest_date)
    sv = pd.to_datetime(cyc.survey_date)
    cyc["survey_in_cycle"] = (sv >= s - pd.Timedelta(days=30)) & (sv <= h + pd.Timedelta(days=15))
    gd = pd.to_datetime(cyc.geom_hash.map(meta["survey_date"]), errors="coerce")
    cyc["gdate_in_cycle"] = np.where(gd.notna(), (gd >= s) & (gd <= h + pd.Timedelta(days=15)), np.nan)

    rej = cyc[bad].assign(reason=np.where(cyc[bad]["label_season"].isin(KHARIF),
                                          "kharif_label_but_cycle_fallback",
                                          "cycle_not_kharif"))
    rej_cols = ["geom_hash", "Crop_Name", "mh_origin", "label_season", "season_consistent",
                "attribution", "sowing_date", "peak_date", "harvest_date", "duration_days", "reason"]
    rej[rej_cols].to_csv(P.reports / f"rejections_mh_kharif_{kind}.csv", index=False)
    kept = cyc[~bad].drop(columns=["mh_origin"])
    kept.to_parquet(P.cycles(kind), index=False)

    # Parcels never attributed at all (cycles-stage rejections), by origin/crop.
    stage_rej = pd.read_csv(P.reports / f"rejections_mh_{kind}.csv") \
        if (P.reports / f"rejections_mh_{kind}.csv").stat().st_size > 2 else pd.DataFrame()
    if len(stage_rej):
        stage_rej["mh_origin"] = stage_rej.geom_hash.map(meta["mh_origin"])
    nn = cyc[new]
    summary = {
        "geom_kind": kind,
        "attributed_total": int(len(cyc)),
        "new_attributed": int(new.sum()),
        "new_kharif_ok": int((new & season_ok).sum()),
        "new_rejected_not_kharif": {k: int(v) for k, v in rej.Crop_Name.value_counts().items()},
        "new_survey_in_cycle_rate": round(float(nn.survey_in_cycle.mean()), 4) if len(nn) else None,
        "new_soy_gdate_in_cycle_rate": (
            round(float(pd.to_numeric(nn[nn.Crop_Name == "Soyabean"].gdate_in_cycle).mean()), 4)
            if (nn.Crop_Name == "Soyabean").any() else None),
        "new_label_season_counts": {k: int(v) for k, v in nn.label_season.value_counts().items()},
        "stage_rejections_new": ({f"{c}:{r}": int(n) for (c, r), n in
                                  stage_rej[stage_rej.mh_origin == "new"]
                                  .groupby(["Crop_Name", "reason"]).size().items()}
                                 if len(stage_rej) else {}),
        "stage_rejections_all": ({k: int(v) for k, v in stage_rej.reason.value_counts().items()}
                                 if len(stage_rej) else {}),
    }
    log.info("kharif check [%s]: new attributed %d, kharif-ok %d, rejected %s",
             kind, summary["new_attributed"], summary["new_kharif_ok"],
             summary["new_rejected_not_kharif"])
    return summary


# =============================================================================
# stage 3 — features (src.features, in-process)
# =============================================================================
def features_inprocess(parcels: Path, scenes: Path, cycles: Path, outdir: Path,
                       suffix: str, kind: str) -> int:
    import src.features as f
    _pin_and_redirect(f, outdir / "x")
    f.DATA = Path(outdir)                      # outputs: outdir/03_features_tier{0,1}{suffix}
    argv = sys.argv
    sys.argv = ["features", "--parcels", str(parcels), "--scenes", str(scenes),
                "--cycles", str(cycles), "--suffix", suffix, "--geom-kind", kind]
    try:
        return int(f.main() or 0)
    finally:
        sys.argv = argv


# =============================================================================
# label QA (A4)
# =============================================================================
def phenology_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Cotton >= 140 d with peak Aug-Nov; soybean 85-125 d with peak Aug-Sep."""
    out = pd.Series("", index=df.index, dtype=object)
    dur = df["duration_days"].astype(float)
    pm = pd.to_datetime(df["peak_date"]).dt.month
    cot, soy = df.Crop_Name == "Cotton", df.Crop_Name == "Soyabean"
    out[cot & (dur < 140)] += "cotton_short_cycle;"
    out[cot & ~pm.isin([8, 9, 10, 11])] += "cotton_peak_outside_aug_nov;"
    out[soy & ((dur < 85) | (dur > 125))] += "soy_duration_outside_85_125;"
    out[soy & ~pm.isin([8, 9])] += "soy_peak_outside_aug_sep;"
    return out


# =============================================================================
# training helpers
# =============================================================================
def _assert_no_leak(hashes, split: Dict) -> None:
    banned = set(split["excluded_from_training"]) | set(split["test_geom_hashes"])
    leak = banned & set(hashes)
    if leak:
        raise AssertionError(f"{len(leak)} held-out geom_hash(es) reached training, "
                             f"e.g. {sorted(leak)[:3]}")


def _matrix(df: pd.DataFrame, names: List[str]) -> np.ndarray:
    X = df[names].to_numpy(dtype=float)
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)   # tier-1 historical fill


class TrainSet:
    """Training originals + their augmented copies, aligned for weighting/CV."""

    def __init__(self, F: pd.DataFrame, A: Optional[pd.DataFrame], names: List[str],
                 classes: List[str], unchecked_weight: float):
        self.F = F.reset_index(drop=True)
        self.names = names
        self.classes = classes
        cidx = {c: i for i, c in enumerate(classes)}
        self.X = _matrix(self.F, names)
        self.y = np.array([cidx[c] for c in self.F.Crop_Name])
        self.blocks = self.F.block_id.to_numpy()
        self.qa = M.qa_weights(self.F.qa_checked.tolist(), self.F.Crop_Name.tolist(),
                               unchecked_weight)
        if A is not None and len(A):
            pos = {g: i for i, g in enumerate(self.F.geom_hash)}
            A = A[A.geom_hash.isin(pos)].reset_index(drop=True)
            self.XA = _matrix(A, names)
            self.parent = A.geom_hash.map(pos).to_numpy()
        else:
            self.XA = np.zeros((0, len(names)))
            self.parent = np.zeros(0, dtype=int)

    def fit_arrays(self, rows: np.ndarray, use_aug: bool = True):
        """X, y, w for fitting on original rows `rows` (+ copies of those rows)."""
        w = M.mh_sample_weights(self.y[rows], self.blocks[rows], self.qa[rows])
        X, y = self.X[rows], self.y[rows]
        if use_aug and len(self.parent):
            loc = {r: i for i, r in enumerate(rows)}
            sel = np.array([p in loc for p in self.parent])
            if sel.any():
                pi = np.array([loc[p] for p in self.parent[sel]])
                X = np.vstack([X, self.XA[sel]])
                y = np.concatenate([y, self.y[rows][pi]])
                w = np.concatenate([w, w[pi]])
        return X, y, w * (len(w) / w.sum())


def _fit_xgb(X, y, w, n_classes):
    from .train import _make_xgb
    return _make_xgb(n_classes).fit(X, y, sample_weight=w)


def _cal_split(rows: np.ndarray, blocks: np.ndarray, y: np.ndarray, seed: int,
               n_classes: int) -> Tuple[np.ndarray, np.ndarray]:
    """train._cv's nested calibration split: 20% of the fold's BLOCKS."""
    rng = np.random.default_rng(seed)
    tb = np.unique(blocks[rows])
    rng.shuffle(tb)
    cal_b = set(tb[:max(1, int(0.20 * len(tb)))])
    m = np.isin(blocks[rows], list(cal_b))
    cal, fit = rows[m], rows[~m]
    if len(cal) < 50 or len(np.unique(y[fit])) < n_classes * 0.5:
        fit, cal = rows, rows
    return fit, cal


def blocked_cv(T: TrainSet, use_aug: bool) -> Dict[str, Any]:
    """GroupKFold(5) on 0.25 deg blocks, nested block calibration — train._cv's protocol."""
    from sklearn.model_selection import GroupKFold
    from crop_analysis.model_bundle import TemperatureScaler
    from .train import _expand

    n = len(T.classes)
    oof = np.zeros((len(T.y), n))
    temps = []
    for k, (tr, te) in enumerate(GroupKFold(N_SPLITS).split(T.X, T.y, T.blocks), 1):
        fit, cal = _cal_split(tr, T.blocks, T.y, SEED + k, n)
        X, y, w = T.fit_arrays(fit, use_aug)
        m = _fit_xgb(X, y, w, n)
        sc = TemperatureScaler().fit(_expand(m.predict_proba(T.X[cal]), m.classes_, n), T.y[cal])
        temps.append(sc.temperature)
        oof[te] = sc.transform(_expand(m.predict_proba(T.X[te]), m.classes_, n))
        log.info("    fold %d: fit=%d (+aug rows %d) cal=%d test=%d T=%.3f", k, len(fit),
                 len(y) - len(fit), len(cal), len(te), sc.temperature)
    return {"oof": oof, "temperatures": temps}


def cv_summary(y: np.ndarray, proba: np.ndarray, classes: List[str],
               mask: Optional[np.ndarray] = None) -> Dict[str, Any]:
    from .train import _summarise
    if mask is not None:
        y, proba = y[mask], proba[mask]
    present = np.unique(y)
    s = _summarise(y, proba, classes)
    # balanced accuracy over the classes actually present in this subset
    from sklearn.metrics import recall_score
    s["balanced_accuracy_present"] = round(float(recall_score(
        y, proba.argmax(1), labels=present, average="macro", zero_division=0)), 4)
    return s


def select_abstain_rule(proba: np.ndarray, y: np.ndarray, n_scenes: np.ndarray) -> Dict[str, Any]:
    """Loosest (n_scenes_min, p_min) whose OOF precision >= 0.70 — the shipped
    selection rule, but with CropDetector's actual semantics (raw p_max)."""
    pred = proba.argmax(1)
    sweep = []
    for n_min in (5, 6, 7, 8, 10):
        for p_min in (0.25, 0.30, 0.35, 0.40, 0.50):
            rule = {"p_min": p_min, "gap_min": 0.10, "n_scenes_min": n_min}
            keep = ~M.abstain_mask(proba, n_scenes, rule)
            sweep.append({**rule, "coverage": round(float(keep.mean()), 4),
                          "precision": round(float((pred[keep] == y[keep]).mean()), 4)
                          if keep.any() else 0.0})
    ok = [s for s in sweep if s["precision"] >= 0.70]
    best = max(ok, key=lambda s: s["coverage"]) if ok else max(sweep, key=lambda s: s["precision"])
    return {"rule": {k: best[k] for k in ("p_min", "gap_min", "n_scenes_min")},
            "selected": best, "sweep": sweep}


def fit_final(T: TrainSet, use_aug: bool):
    """train._export: temperature on 20% held-out training blocks, then refit on all."""
    from crop_analysis.model_bundle import CalibratedBundle, TemperatureScaler
    from .train import _expand

    n = len(T.classes)
    blocks = np.unique(T.blocks)
    rng = np.random.default_rng(SEED)
    rng.shuffle(blocks)
    cal_b = set(blocks[:max(1, int(0.20 * len(blocks)))])
    cal = np.where(np.isin(T.blocks, list(cal_b)))[0]
    fit = np.where(~np.isin(T.blocks, list(cal_b)))[0]
    X, y, w = T.fit_arrays(fit, use_aug)
    m = _fit_xgb(X, y, w, n)
    scaler = TemperatureScaler().fit(_expand(m.predict_proba(T.X[cal]), m.classes_, n), T.y[cal])
    log.info("  final: temperature %.4f from %d calibration rows in %d blocks",
             scaler.temperature, len(cal), len(cal_b))
    X, y, w = T.fit_arrays(np.arange(len(T.y)), use_aug)
    final = _fit_xgb(X, y, w, n)
    prior = np.bincount(y, weights=w, minlength=n)
    prior = prior / prior.sum()
    return CalibratedBundle(final, scaler, np.arange(n)), scaler, X, y, w, prior


# =============================================================================
# evaluation helpers
# =============================================================================
def score_set(model, crop_names: List[str], df: pd.DataFrame, names: List[str],
              rule: Dict, extra_rules: Optional[Dict[str, Dict]] = None,
              n_total: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """Frozen-test metrics for one model x one feature table."""
    if df.empty:
        return {"n": 0}
    X = _matrix(df, names)
    proba = model.predict_proba(X)
    return metrics_from_proba(proba, crop_names, df, rule, extra_rules, n_total)


def metrics_from_proba(proba: np.ndarray, crop_names: List[str], df: pd.DataFrame,
                       rule: Dict, extra_rules: Optional[Dict[str, Dict]] = None,
                       n_total: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    crop_names = [str(c) for c in crop_names]
    cidx = {c: i for i, c in enumerate(crop_names)}
    truth = df.Crop_Name.astype(str).to_numpy()
    y = np.array([cidx.get(c, -1) for c in truth])
    pred = np.array(crop_names, dtype=object)[proba.argmax(1)]
    n_sc = df["n_scenes_real"].to_numpy(dtype=float)
    out: Dict[str, Any] = {"n": int(len(df)),
                           "ece_10bin": round(M.ece(proba, y), 4),
                           "accuracy_argmax": round(float((pred == truth).mean()), 4)}
    out["argmax"] = M.per_class_report(truth, pred, FOCUS)
    out["confusion_argmax"] = _confusion(truth, pred)
    rules = {"bundle_rule": rule, **(extra_rules or {})}
    for name, r in rules.items():
        ab = M.abstain_mask(proba, n_sc, r)
        p_r = np.where(ab, None, pred)
        kept = ~ab
        res = {"rule": r, "coverage": round(float(kept.mean()), 4),
               "precision_on_kept": round(float((pred[kept] == truth[kept]).mean()), 4)
               if kept.any() else None,
               "per_class": M.per_class_report(truth, p_r, FOCUS),
               "confusion": _confusion(truth, p_r)}
        out[name] = res
    if n_total:
        out["end_to_end_recall_bundle_rule"] = {
            c: _e2e(out["bundle_rule"]["per_class"][c]["tp"], n_total.get(c, 0))
            for c in FOCUS if n_total.get(c)}
    return out


def _e2e(tp: int, n: int) -> Dict[str, Any]:
    lo, hi = M.wilson_interval(tp, n)
    return {"tp": tp, "n_scored_parcels": n, "recall": round(tp / n, 4),
            "ci95": [round(lo, 4), round(hi, 4)]}


def _confusion(truth: np.ndarray, pred: np.ndarray) -> Dict[str, Dict[str, int]]:
    cols = list(FOCUS) + ["Others", "abstained"]
    out = {}
    for t in FOCUS:
        m = truth == t
        row = {c: 0 for c in cols}
        for p in pred[m]:
            k = "abstained" if p is None else (p if p in FOCUS else "Others")
            row[k] += 1
        out[t] = row
    return out


def gate_check(res: Dict[str, Any], key: str) -> Dict[str, Any]:
    pc = res[key] if key == "argmax" else res[key]["per_class"]
    out, ok = {}, True
    for c in ("Cotton", "Soyabean"):
        r, p = pc[c]["recall"], pc[c]["precision"]
        rl, pl = pc[c]["recall_ci95"][0], pc[c]["precision_ci95"][0]
        good = all(v is not None for v in (r, p, rl, pl)) and (
            r >= GATE["value"] and p >= GATE["value"] and rl >= GATE["ci_lower"]
            and pl >= GATE["ci_lower"])
        out[c] = {"recall": r, "recall_lb": rl, "precision": p, "precision_lb": pl,
                  "pass": bool(good)}
        ok &= bool(good)
    out["ece"] = {"value": res["ece_10bin"], "pass": res["ece_10bin"] <= GATE["ece"]}
    ok &= out["ece"]["pass"]
    out["all_pass"] = bool(ok)
    return out


# =============================================================================
# driver
# =============================================================================
def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_cycles_chunk":                    # worker entry
        pp, sp, out, rej, kind = argv[1:6]
        return cycles_inprocess(Path(pp), Path(sp), Path(out), Path(rej), kind)

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-stage", default="merge", choices=STAGES)
    ap.add_argument("--to-stage", default="eval", choices=STAGES)
    ap.add_argument("--workers", type=int, default=3, help="cycle-detection processes")
    ap.add_argument("--geom-kinds", default="bbox,poly")
    ap.add_argument("--n-aug", type=int, default=1, help="augmented copies per training cycle (0 = off)")
    ap.add_argument("--unchecked-weight", type=float, default=0.5)
    ap.add_argument("--truncations", default="60,90,120")
    ap.add_argument("--skip-shipped-recipe-cv", action="store_true")
    ap.add_argument("--dev", action="store_true", help="small proof run into a scratch dir")
    ap.add_argument("--dev-per-class", type=int, default=30)
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="run even if 01_scenes_mh.parquet lacks some new parcels")
    for k in ("workdir", "reports", "parcels", "scenes_base", "scenes_new", "split",
              "model_out", "shares"):
        ap.add_argument(f"--{k.replace('_', '-')}", default=None)
    a = ap.parse_args(argv)

    if a.dev:
        from .mh_dev import prepare_dev
        prepare_dev(a)
    P = Paths(a)
    kinds = [k.strip() for k in a.geom_kinds.split(",") if k.strip()]
    first, last = STAGES.index(a.from_stage), STAGES.index(a.to_stage)
    run = lambda s: first <= STAGES.index(s) <= last          # noqa: E731
    split = json.loads(P.split.read_text(encoding="utf-8"))
    log.info("split %s: %d test parcels, %d excluded from training",
             split.get("version"), len(split["test_geom_hashes"]), len(split["excluded_from_training"]))

    import geopandas as gpd
    parcels = gpd.read_parquet(P.parcels)
    if parcels.win_start.min() != ANCHOR.isoformat() and not a.dev:
        log.warning("parcel set's min win_start %s != pinned anchor %s",
                    parcels.win_start.min(), ANCHOR)
    pmeta = pd.DataFrame(parcels.drop(columns="geometry")).set_index("geom_hash")

    if run("merge"):
        if not a.dev:
            chk = check_new_scenes(P)
            log.info("01_scenes_mh check: %s", chk)
            if not chk.get("exists"):
                raise SystemExit("01_scenes_mh.parquet does not exist yet — extraction still running?")
            if (chk["bbox"] < chk["n_new_parcels"] or chk["poly"] < chk["n_new_parcels"]) \
                    and not a.allow_incomplete:
                raise SystemExit(f"01_scenes_mh.parquet incomplete: {chk}. Re-consolidate "
                                 "or pass --allow-incomplete.")
        merge_scenes(P)

    attribution: Dict[str, Any] = {}
    if run("cycles"):
        for kind in kinds:
            log.info("── cycles [%s] ──", kind)
            run_cycles(P, kind, a.workers)
            attribution[kind] = kharif_check(P, kind, pmeta.reset_index())
        M.dump_json(attribution, P.reports / "mh_attribution.json")
    elif (P.reports / "mh_attribution.json").exists():
        attribution = json.loads((P.reports / "mh_attribution.json").read_text(encoding="utf-8"))

    if run("features"):
        for kind in kinds:
            log.info("── features [%s] ──", kind)
            rc = features_inprocess(P.parcels, P.scenes, P.cycles(kind), P.work,
                                    P.suffix(kind), kind)
            if rc:
                raise SystemExit(f"features failed ({kind})")

    if not (run("train") or run("eval")):
        return 0
    return train_and_evaluate(a, P, kinds, split, parcels, pmeta, attribution)


def _load_features(P: Paths, kind: str, pmeta: pd.DataFrame) -> pd.DataFrame:
    f = P.features(kind)
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_parquet(f)
    for col in ("mh_origin", "qa_checked"):
        df[col] = df.geom_hash.map(pmeta[col])
    return df


def train_and_evaluate(a, P: Paths, kinds, split, parcels, pmeta, attribution) -> int:
    import joblib
    from .features import META_COLS
    from .mh_features import augment_cycles, parity_check, truncated_features
    from .train import _cv

    shipped = joblib.load(P.shipped)
    names = list(shipped["feature_names"])
    FB = _load_features(P, "bbox", pmeta)
    feat_cols = [c for c in FB.columns if c not in META_COLS and c not in ("mh_origin", "qa_checked")]
    if set(feat_cols) != set(names):
        raise SystemExit(f"feature set differs from the shipped bundle: {set(feat_cols) ^ set(names)}")
    FP = _load_features(P, "poly", pmeta) if "poly" in kinds else pd.DataFrame()

    # ── A4 phenology flags (flag, never drop) ─────────────────────────────
    for F in (FB, FP):
        if len(F):
            F["pheno_flags"] = phenology_flags(F)
    flags = FB[FB.Crop_Name.isin(["Cotton", "Soyabean"])][
        ["geom_hash", "Crop_Name", "mh_origin", "qa_checked", "sowing_date", "peak_date",
         "harvest_date", "duration_days", "pheno_flags", "lat", "lon"]]
    flags.to_csv(P.reports / "mh_phenology_flags.csv", index=False)
    pheno = {c: {"n": int((flags.Crop_Name == c).sum()),
                 "flagged": int(((flags.Crop_Name == c) & (flags.pheno_flags != "")).sum()),
                 "by_reason": {r: int(flags[flags.Crop_Name == c].pheno_flags.str.contains(r).sum())
                               for r in ("short_cycle", "peak_outside", "duration_outside")}}
             for c in ("Cotton", "Soyabean")}
    pheno["new_only"] = {c: {"n": int(((flags.Crop_Name == c) & (flags.mh_origin == "new")).sum()),
                             "flagged": int(((flags.Crop_Name == c) & (flags.mh_origin == "new")
                                             & (flags.pheno_flags != "")).sum())}
                         for c in ("Cotton", "Soyabean")}
    log.info("phenology flags: %s", pheno)

    # ── split ──────────────────────────────────────────────────────────────
    train_mask = M.training_split_mask(FB.geom_hash, split)
    test_set = set(split["test_geom_hashes"])
    FT = FB[train_mask].reset_index(drop=True)
    _assert_no_leak(FT.geom_hash, split)
    classes = sorted(FT.Crop_Name.unique())
    log.info("training rows: %d (%d classes); test rows with a bbox cycle: %d of %d",
             len(FT), len(classes), int(FB.geom_hash.isin(test_set).sum()), len(test_set))

    # ── augmentation (training rows only) ─────────────────────────────────
    scenes_b = pd.read_parquet(P.scenes)
    scenes_b = scenes_b[scenes_b.geom_kind == "bbox"]
    cyc_b = pd.read_parquet(P.cycles("bbox"))
    pdf = pd.DataFrame(parcels.drop(columns="geometry"))
    parity = parity_check(pdf, scenes_b, cyc_b[cyc_b.geom_hash.isin(set(FB.geom_hash))],
                          FB, ANCHOR, names, n=25)
    log.info("augmentation builder parity vs src.features (max |diff|): %.3g", parity)
    if not np.isfinite(parity) or parity > 1e-9:
        raise SystemExit(f"augmentation builder does not reproduce src.features (diff {parity})")
    A = None
    if a.n_aug > 0:
        tr_cyc = cyc_b[cyc_b.geom_hash.isin(set(FT.geom_hash))]
        _assert_no_leak(tr_cyc.geom_hash, split)
        t0 = time.time()
        A = augment_cycles(pdf, scenes_b[scenes_b.geom_hash.isin(set(tr_cyc.geom_hash))],
                           tr_cyc, ANCHOR, n_aug=a.n_aug, seed=SEED)
        _assert_no_leak(A.geom_hash, split)
        A.to_parquet(P.work / "03_features_tier1_mh_aug.parquet", index=False)
        log.info("augmented rows: %d in %.1f min", len(A), (time.time() - t0) / 60)
    T = TrainSet(FT, A, names, classes, a.unchecked_weight)

    # ── A6(a) blocked CV on training rows ─────────────────────────────────
    log.info("── blocked GroupKFold(5), MH recipe (capped weights, QA, aug=%d) ──", a.n_aug)
    cv = blocked_cv(T, use_aug=a.n_aug > 0)
    existing = (FT.mh_origin != "new").to_numpy()
    cv_rep = {
        "protocol": "GroupKFold(5) over 0.25deg blocks, nested 20%-block temperature calibration",
        "n_rows": int(len(T.y)), "n_aug_rows": int(len(T.parent)),
        "mh_recipe": cv_summary(T.y, cv["oof"], classes),
        "mh_recipe_existing_rows_only": cv_summary(T.y, cv["oof"], classes, existing),
        "mh_recipe_mean_temperature": round(float(np.mean(cv["temperatures"])), 4),
        "shipped_reference_balanced_accuracy": SHIPPED_BLOCKED_BAL_ACC,
    }
    if a.n_aug > 0:
        log.info("── blocked CV, MH recipe without augmentation (ablation) ──")
        cv0 = blocked_cv(T, use_aug=False)
        cv_rep["mh_recipe_no_aug"] = cv_summary(T.y, cv0["oof"], classes)
    if not a.skip_shipped_recipe_cv:
        log.info("── blocked CV, SHIPPED recipe (train._cv) on the same rows ──")
        sr = _cv(T.X, T.y, T.blocks, T.blocks, classes, "blocked", kind="xgboost")
        cv_rep["shipped_recipe_same_rows"] = sr["calibrated"]
        cv_rep["shipped_recipe_same_rows_existing_only"] = cv_summary(T.y, sr["_oof_cal"], classes, existing)
    for k in ("mh_recipe", "mh_recipe_no_aug", "shipped_recipe_same_rows"):
        if k in cv_rep:
            s = cv_rep[k]
            log.info("  %-26s bal_acc=%.4f macro_f1=%.4f ECE=%.4f", k, s["balanced_accuracy"],
                     s["macro_f1"], s["ece"])

    # ── A4 label-noise review list from OOF ───────────────────────────────
    p_label = cv["oof"][np.arange(len(T.y)), T.y]
    top = np.array(classes)[cv["oof"].argmax(1)]
    rev = FT.assign(p_given_label=np.round(p_label, 4), oof_top=top,
                    oof_top_p=np.round(cv["oof"].max(1), 4))
    rev = rev[rev.p_given_label < 0.2][
        ["geom_hash", "Crop_Name", "mh_origin", "qa_checked", "p_given_label", "oof_top",
         "oof_top_p", "pheno_flags", "duration_days", "peak_date", "block_id", "lat", "lon"]
    ].sort_values("p_given_label")
    rev.to_csv(P.reports / "mh_label_review.csv", index=False)
    review = {"threshold": 0.2, "n": int(len(rev)),
              "by_class": {k: int(v) for k, v in rev.Crop_Name.value_counts().items()},
              "new_by_class": {k: int(v) for k, v in rev[rev.mh_origin == "new"].Crop_Name.value_counts().items()},
              "new_soy_unchecked": int(((rev.Crop_Name == "Soyabean") & (rev.mh_origin == "new")
                                        & (rev.qa_checked == False)).sum()),   # noqa: E712
              "new_soy_checked": int(((rev.Crop_Name == "Soyabean") & (rev.mh_origin == "new")
                                      & (rev.qa_checked == True)).sum())}      # noqa: E712
    log.info("label review list: %s", review)

    # ── abstain rule from training OOF ────────────────────────────────────
    ab = select_abstain_rule(cv["oof"], T.y, FT.n_scenes_real.to_numpy(float))
    log.info("abstain rule (OOF): %s -> %s", ab["rule"], ab["selected"])

    # ── A5 final fit + bundle ─────────────────────────────────────────────
    log.info("── final fit ──")
    model, scaler, Xfit, yfit, wfit, prior = fit_final(T, use_aug=a.n_aug > 0)
    from sklearn.preprocessing import LabelEncoder
    le = LabelEncoder().fit(classes)
    assert list(le.classes_) == classes
    ood = M.fit_ood(T.X, names)
    bundle = _bundle(a, P, model, le, names, classes, scaler, ab, cv_rep, ood, prior,
                     T, split, Xfit, yfit, wfit)
    joblib.dump(bundle, P.model_out)
    log.info("wrote %s (%.1f MB)", P.model_out, P.model_out.stat().st_size / 1e6)
    loader_ok = _loader_smoke(P.model_out, T.X[:3])

    # ── A6(b,c) frozen test ───────────────────────────────────────────────
    del scenes_b
    sc_test = pd.read_parquet(P.scenes)
    sc_test = sc_test[sc_test.geom_hash.isin(test_set)]
    scored = pmeta.loc[pmeta.index.intersection(list(test_set))]
    # Denominator for end-to-end recall: scored parcels that HAVE scenes of the
    # footprint (existing tur has a polygon extraction for only ~25% of parcels).
    n_total_by_kind = {
        k: {c: int(v) for c, v in scored[scored.index.isin(set(
            sc_test[sc_test.geom_kind == k].geom_hash))].Crop_Name.value_counts().items()}
        for k in ("bbox", "poly")}
    models = {"new_mh_v1": (model, classes, ab["rule"]),
              "shipped_tier1_v1": (shipped["model"], [str(c) for c in shipped["crop_names"]],
                                   shipped["abstain_rule"])}
    test_res: Dict[str, Any] = {}
    test_frames = {"bbox": FB[FB.geom_hash.isin(test_set)]}
    if len(FP):
        test_frames["poly"] = FP[FP.geom_hash.isin(test_set)]
    for kind, TF in test_frames.items():
        cover = TF.Crop_Name.value_counts().to_dict()
        n_total = n_total_by_kind[kind]
        test_res[kind] = {"n_scored_parcels_with_scenes": n_total,
                          "n_with_attributed_cycle": {k: int(v) for k, v in cover.items()}}
        for mname, (mdl, cn, rule) in models.items():
            r = score_set(mdl, cn, TF, names, rule, {"design_rule_0.35": DESIGN_RULE}, n_total)
            if r.get("n"):
                r["gate_argmax"] = gate_check(r, "argmax")
                r["gate_at_bundle_rule"] = gate_check(r, "bundle_rule")
            if mname == "new_mh_v1" and len(TF):
                sc = M.ood_score(ood, _matrix(TF, names))
                is_ood = sc > ood["threshold"]
                pred = np.array(classes)[model.predict_proba(_matrix(TF, names)).argmax(1)]
                corr = pred == TF.Crop_Name.to_numpy()
                r["ood"] = {"rate": round(float(is_ood.mean()), 4),
                            "by_class": {c: round(float(is_ood[TF.Crop_Name.to_numpy() == c].mean()), 4)
                                         for c in FOCUS if (TF.Crop_Name == c).any()},
                            "accuracy_in_dist": round(float(corr[~is_ood].mean()), 4) if (~is_ood).any() else None,
                            "accuracy_ood": round(float(corr[is_ood].mean()), 4) if is_ood.any() else None}
            test_res[kind][mname] = r
            if r.get("n"):
                log.info("  test[%s] %-17s n=%d ECE=%.4f cov=%.3f | Cotton R=%s P=%s | Soy R=%s P=%s | Tur R=%s",
                         kind, mname, r["n"], r["ece_10bin"], r["bundle_rule"]["coverage"],
                         r["argmax"]["Cotton"]["recall"], r["argmax"]["Cotton"]["precision"],
                         r["argmax"]["Soyabean"]["recall"], r["argmax"]["Soyabean"]["precision"],
                         r["argmax"]["Tur"]["recall"])

    # ── label-shift prior ─────────────────────────────────────────────────
    shares = M.load_district_shares(P.shares)
    if shares.empty:
        label_shift = {"applied": False, "reason": "not applied (no official shares file)",
                       "file": str(P.shares)}
    else:
        tgt = M.region_prior(shares)
        tp = {c: float(prior[i]) for i, c in enumerate(classes)}
        label_shift = {"applied": True, "target_prior": tgt, "train_prior": tp,
                       "note": "single area-weighted prior over every district in the file "
                               "(parcels carry no district code)"}
        for kind, TF in test_frames.items():
            pr = M.apply_label_shift(model.predict_proba(_matrix(TF, names)), classes, tp, tgt)
            r = metrics_from_proba(pr, classes, TF, ab["rule"], None, n_total_by_kind[kind])
            r["gate_argmax"] = gate_check(r, "argmax")
            label_shift[kind] = r

    # ── A6(d) in-season truncation ────────────────────────────────────────
    trunc: Dict[str, Any] = {}
    days_list = [int(x) for x in a.truncations.split(",") if x.strip()]
    for kind in test_frames:
        sc_k = sc_test[sc_test.geom_kind == kind]
        cyc_k = pd.read_parquet(P.cycles(kind))
        cyc_k = cyc_k[cyc_k.geom_hash.isin(test_set)]
        trunc[kind] = {}
        for d in days_list:
            t0 = time.time()
            TR = truncated_features(pdf, sc_k, cyc_k, ANCHOR, d)
            ok = TR[TR.trunc_status == "ok"] if len(TR) else TR
            entry = {"n_cycles": int(len(cyc_k)),
                     "status": {k: int(v) for k, v in TR.trunc_status.value_counts().items()} if len(TR) else {}}
            for mname, (mdl, cn, rule) in models.items():
                if len(ok):
                    r = score_set(mdl, cn, ok, names, rule, None, n_total_by_kind[kind])
                    entry[mname] = {
                        "n_featurised": r["n"], "ece_10bin": r["ece_10bin"],
                        "accuracy_argmax_on_featurised": r["accuracy_argmax"],
                        "coverage_bundle_rule_of_featurised": r["bundle_rule"]["coverage"],
                        "per_class_argmax": {c: {k: r["argmax"][c][k] for k in
                                                 ("recall", "recall_ci95", "precision", "precision_ci95", "support")}
                                             for c in FOCUS},
                        "end_to_end_recall_bundle_rule": r.get("end_to_end_recall_bundle_rule"),
                    }
            trunc[kind][str(d)] = entry
            log.info("  truncation[%s] %3d d: %s (%.1fs)", kind, d, entry["status"], time.time() - t0)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": str(P.model_out), "shipped_model": str(P.shipped),
        "split": {"version": split.get("version"), "hash": M.split_hash(P.split),
                  "test_counts": split.get("counts")},
        "training_data_hash": bundle["training_data_hash"],
        "dev_run": bool(a.dev),
        "attribution": attribution,
        "phenology_flags": pheno,
        "label_review": review,
        "augmentation": bundle["augmentation"],
        "augmentation_parity_max_abs_diff": parity,
        "abstain_rule": ab,
        "cv": cv_rep,
        "loader_smoke_ok": loader_ok,
        "test": test_res,
        "label_shift": label_shift,
        "truncation": trunc,
        "ood": {"method": ood["method"], "n_components": ood["n_components"],
                "threshold": ood["threshold"], "threshold_quantile": ood["threshold_quantile"]},
        "caveats": CAVEATS,
    }
    M.dump_json(report, P.reports / "mh_eval.json")
    (P.reports / "mh_eval.md").write_text(render_md(report), encoding="utf-8")
    log.info("wrote %s and %s", P.reports / "mh_eval.json", P.reports / "mh_eval.md")
    return 0


CAVEATS = [
    "Held-out blocks contain only Cotton, Soyabean and Tur. Test-set precision therefore "
    "counts only confusions among these three (and predictions of any other class as "
    "misses); it cannot see false positives from maize, jowar, fallow etc. Real-world "
    "precision will be lower.",
    "Tur test rows are existing data that the SHIPPED model trained on: the shipped model's "
    "Tur numbers are in-sample. The new model never saw them.",
    "All Cotton/Soyabean labels are one season (Kharif 2023) and one surveyor programme; "
    "spatial blocking guards against neighbourhood memorisation, not against year effects.",
    "Recall 'argmax' ignores abstention; 'bundle_rule' counts an abstention as a miss; "
    "'end_to_end' also counts parcels with no attributed cycle as misses.",
]


def _bundle(a, P, model, le, names, classes, scaler, ab, cv_rep, ood, prior, T, split,
            Xfit, yfit, wfit) -> Dict[str, Any]:
    import sklearn
    import xgboost
    from config import PipelineConfig
    from crop_analysis.crop_detector import EXTRACTOR_VERSION

    return {
        # ── keys CropDetector reads (same layout as train._export) ──
        "model": model,
        "label_encoder": le,
        "feature_names": list(names),
        "crop_names": list(le.classes_),
        "extractor_version": EXTRACTOR_VERSION,
        "ml_feature_scenes": int(PipelineConfig.ML_FEATURE_SCENES),
        "ml_feature_indices": list(PipelineConfig.ML_FEATURE_INDICES),
        "tier": 1,
        "estimator": "xgboost",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "training_data": "00_parcels_mh_full.parquet (crop_classification_train_500 + augment + "
                         "Marathwada 2023 cotton/soybean), held-out blocks removed",
        "n_train": int(len(T.y)),
        "sklearn_version": sklearn.__version__,
        "xgboost_version": xgboost.__version__,
        "cv_protocol": f"GroupKFold({N_SPLITS}) on 0.25deg spatial blocks",
        "metrics": cv_rep["mh_recipe"],
        "leakage_gap": None,
        "gates": None,
        "temperature": scaler.temperature,
        "calibration": {"method": "temperature", "temperature": scaler.temperature,
                        "fitted_on": "20% of training 0.25deg blocks, held out from the fit"},
        "abstain_rule": ab["rule"],
        "geographic_footprint": "69-88E, 12-31N; Marathwada cotton/soybean 2023 added",
        "temporal_footprint": "2022-2024 survey dates; MH cotton/soy 2023 only; no year is a feature",
        "untrained_reference_crops": ["Cabbage", "Others", "Papaya", "Pomegranate", "Sunflower"],
        # ── provenance added for Track A ──
        "training_data_hash": M.data_hash(Xfit, yfit, list(T.F.geom_hash), wfit),
        "split_version": split.get("version"),
        "split_hash": M.split_hash(P.split),
        "ood": ood,
        "train_prior": {c: float(prior[i]) for i, c in enumerate(classes)},
        "label_shift": {"method": "Saerens et al. 2002: p'(c|x) ~ p(c|x) pi_target(c)/pi_train(c)",
                        "train_prior_key": "train_prior",
                        "target_prior_source": "data/reference/district_kharif_shares.csv (official only)"},
        "augmentation": {"n_aug": a.n_aug, "shift_days": 20, "stretch": 0.10,
                         "level": "cycle-scene window shift + duration stretch (src/mh_features.py)",
                         "applied_to": "training rows only"},
        "sample_weighting": {"class": "inverse frequency clipped to [1/3, 3]",
                             "block": "1/sqrt(rows per 0.25deg block)",
                             "unchecked_soybean": a.unchecked_weight},
    }


def _loader_smoke(path: Path, X: np.ndarray) -> bool:
    """Load through the real consumer (CropDetector.__init__)."""
    import joblib
    try:
        from crop_analysis.crop_detector import CropDetector
        det = CropDetector(str(path), latitude=18.8, longitude=76.85, verbose=False)
        p = det.model.predict_proba(X)
        assert p.shape[1] == len(det.crop_names) and np.allclose(p.sum(1), 1, atol=1e-6)
        b = joblib.load(path)
        log.info("loader smoke OK: CropDetector loaded %d classes, rule %s",
                 len(det.crop_names), det.abstain_rule)
        return bool(b["feature_names"] == det.feature_names)
    except Exception as exc:                                  # noqa: BLE001
        log.error("loader smoke FAILED: %s", exc)
        return False


# =============================================================================
# markdown
# =============================================================================
def _ci(d: Dict, k: str) -> str:
    v, ci = d.get(k), d.get(f"{k}_ci95") or [None, None]
    if v is None:
        return "n/a"
    return f"{v:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]"


def render_md(R: Dict[str, Any]) -> str:
    L: List[str] = []
    w = L.append
    w("# Marathwada retrain — evaluation (`crop_classifier_tier1_mh_v1`)")
    w("")
    if R.get("dev_run"):
        w("> **DEV RUN on a small subset — numbers are a pipeline proof, not results.**")
        w("")
    w(f"Generated {R['generated_at']} · split `{R['split']['version']}` (hash `{R['split']['hash']}`) · "
      f"training data hash `{R['training_data_hash']}`")
    w("")
    w("## Gate check (plan §8: Cotton & Soyabean recall and precision ≥ 0.85, Wilson LB ≥ 0.80, ECE ≤ 0.05)")
    w("")
    w("| Footprint | Model | Scoring | Cotton R / P | Soyabean R / P | ECE | Pass |")
    w("|---|---|---|---|---|---|---|")
    for kind, T in R["test"].items():
        for m in ("new_mh_v1", "shipped_tier1_v1"):
            r = T.get(m) or {}
            if not r.get("n"):
                continue
            for key, lab in (("gate_argmax", "argmax"), ("gate_at_bundle_rule", "at abstain rule")):
                g = r[key]
                w(f"| {kind} | {m} | {lab} | "
                  f"{g['Cotton']['recall']} (LB {g['Cotton']['recall_lb']}) / {g['Cotton']['precision']} (LB {g['Cotton']['precision_lb']}) | "
                  f"{g['Soyabean']['recall']} (LB {g['Soyabean']['recall_lb']}) / {g['Soyabean']['precision']} (LB {g['Soyabean']['precision_lb']}) | "
                  f"{g['ece']['value']} | **{'PASS' if g['all_pass'] else 'FAIL'}** |")
    w("")
    w("## Frozen Marathwada test set — per class (95% Wilson intervals)")
    for kind, T in R["test"].items():
        w("")
        w(f"### Footprint: {kind}")
        w("")
        w(f"Scored parcels with scenes of this footprint: {T['n_scored_parcels_with_scenes']} · "
          f"with an attributed cycle: {T['n_with_attributed_cycle']}")
        for m in ("new_mh_v1", "shipped_tier1_v1"):
            r = T.get(m) or {}
            if not r.get("n"):
                continue
            w("")
            note = " (Tur in-sample for this model)" if m == "shipped_tier1_v1" else ""
            w(f"**{m}**{note} — n={r['n']}, ECE={r['ece_10bin']}, argmax accuracy={r['accuracy_argmax']}, "
              f"bundle rule {r['bundle_rule']['rule']}: coverage {r['bundle_rule']['coverage']}, "
              f"precision on kept {r['bundle_rule']['precision_on_kept']}; design rule 0.35: coverage "
              f"{r['design_rule_0.35']['coverage']}, precision {r['design_rule_0.35']['precision_on_kept']}")
            w("")
            w("| Crop | n | Recall (argmax) | Precision (argmax) | F1 | Recall @rule | Precision @rule | End-to-end recall @rule |")
            w("|---|---|---|---|---|---|---|---|")
            e2e = r.get("end_to_end_recall_bundle_rule") or {}
            for c in FOCUS:
                ar, br = r["argmax"][c], r["bundle_rule"]["per_class"][c]
                ee = e2e.get(c)
                ees = f"{ee['recall']:.3f} [{ee['ci95'][0]:.3f}, {ee['ci95'][1]:.3f}]" if ee else "n/a"
                w(f"| {c} | {ar['support']} | {_ci(ar, 'recall')} | {_ci(ar, 'precision')} | {ar['f1']} | "
                  f"{_ci(br, 'recall')} | {_ci(br, 'precision')} | {ees} |")
            w("")
            w("Confusion (argmax; rows = truth):")
            w("")
            cols = list(FOCUS) + ["Others", "abstained"]
            w("| truth \\ pred | " + " | ".join(cols) + " |")
            w("|---" * (len(cols) + 1) + "|")
            for t, row in r["confusion_argmax"].items():
                w(f"| {t} | " + " | ".join(str(row[c]) for c in cols) + " |")
            if "ood" in r:
                w("")
                w(f"OOD (score > training q99): rate {r['ood']['rate']} by class {r['ood']['by_class']}; "
                  f"accuracy in-dist {r['ood']['accuracy_in_dist']} vs OOD {r['ood']['accuracy_ood']}")
    w("")
    w("## Blocked GroupKFold(5) on training rows (regression check vs shipped 0.7492)")
    w("")
    w("| Recipe | rows | balanced acc | macro F1 | ECE | min recall (class) |")
    w("|---|---|---|---|---|---|")
    cv = R["cv"]
    for k in ("mh_recipe", "mh_recipe_no_aug", "shipped_recipe_same_rows",
              "mh_recipe_existing_rows_only", "shipped_recipe_same_rows_existing_only"):
        if k in cv:
            s = cv[k]
            w(f"| {k} | {s['n']} | {s['balanced_accuracy']} | {s['macro_f1']} | {s['ece']} | "
              f"{s['min_class_recall']} ({s['worst_class']}) |")
    w("")
    w(f"Shipped model card: 0.7492 on 7,907 rows of the original set. The rows here differ "
      f"(augmented set + Marathwada, held-out blocks removed), so `shipped_recipe_same_rows` is "
      f"the like-for-like comparison.")
    s = cv["mh_recipe"]["per_class"]
    w("")
    w("Per-class (MH recipe, blocked OOF): " + ", ".join(
        f"{c} R {s[c]['recall']} P {s[c]['precision']}" for c in FOCUS if c in s))
    w("")
    w("## In-season truncation (features from scenes up to D days after detected sowing)")
    for kind, T in R["truncation"].items():
        w("")
        w(f"### {kind}")
        w("")
        w("| D | model | featurised / cycles | status | argmax acc | Cotton R | Soyabean R | Tur R | Cotton P | Soyabean P |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for d, e in T.items():
            for m in ("new_mh_v1", "shipped_tier1_v1"):
                r = e.get(m)
                if not r:
                    continue
                pc = r["per_class_argmax"]
                w(f"| {d} | {m} | {r['n_featurised']} / {e['n_cycles']} | {e['status']} | "
                  f"{r['accuracy_argmax_on_featurised']} | {pc['Cotton']['recall']} | {pc['Soyabean']['recall']} | "
                  f"{pc['Tur']['recall']} | {pc['Cotton']['precision']} | {pc['Soyabean']['precision']} |")
    w("")
    w("## Label-shift prior (Saerens et al. 2002)")
    w("")
    ls = R["label_shift"]
    w(ls["reason"] if not ls.get("applied") else f"Applied with target prior {ls['target_prior']}.")
    if ls.get("applied"):
        for kind in ("bbox", "poly"):
            r = ls.get(kind)
            if not r:
                continue
            w("")
            w(f"- {kind}, new model + prior (argmax): " + "; ".join(
                f"{c} R {_ci(r['argmax'][c], 'recall')} P {_ci(r['argmax'][c], 'precision')}"
                for c in FOCUS) + f"; ECE {r['ece_10bin']}; gate {'PASS' if r['gate_argmax']['all_pass'] else 'FAIL'}")
    w("")
    w("## Label QA (A4)")
    w("")
    w(f"Phenology flags (flagged, not dropped): {R['phenology_flags']}")
    w("")
    w(f"Cross-validated label-noise review list (OOF p(label) < 0.2): {R['label_review']} — "
      "see `mh_label_review.csv`.")
    w("")
    w("## Attribution (A3)")
    w("")
    for kind, s in (R.get("attribution") or {}).items():
        w(f"- **{kind}**: {s}")
    w("")
    w("## Model and training")
    w("")
    w(f"- Abstain rule (selected on training OOF, loosest with precision ≥ 0.70): {R['abstain_rule']['rule']} "
      f"-> OOF {R['abstain_rule']['selected']}")
    w(f"- Augmentation: {R['augmentation']}; builder parity with src.features: max |diff| = "
      f"{R['augmentation_parity_max_abs_diff']}")
    w(f"- OOD: {R['ood']}")
    w(f"- Loads through CropDetector: {R['loader_smoke_ok']}")
    w("")
    w("## Caveats")
    w("")
    for c in R["caveats"]:
        w(f"- {c}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
