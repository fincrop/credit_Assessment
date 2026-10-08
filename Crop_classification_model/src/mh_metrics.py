"""
Pure helpers for the Marathwada retrain (accuracy plan Track A, A4-A6).

Nothing here touches Earth Engine, the backend pipeline or the filesystem
except `load_district_shares`, so every function is unit-testable in
milliseconds (src/test_run_mh.py).

  wilson_interval        95% score interval for a binomial proportion
  per_class_report       precision / recall / F1 with Wilson intervals
  ece                    expected calibration error on top-1 probability
  abstain_mask           the production abstain rule (CropDetector semantics)
  mh_sample_weights      capped inverse-frequency x block de-clustering x QA
  apply_label_shift      Saerens et al. (2002) prior correction
  load_district_shares   reader for data/reference/district_kharif_shares.csv
  fit_ood / ood_score    PCA-whitened Mahalanobis distance to the training set
  training_split_mask    rows allowed to train, given the frozen split
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

# =============================================================================
# intervals and per-class metrics
# =============================================================================
def wilson_interval(k: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Wilson score interval for k successes in n trials. (nan, nan) when n == 0."""
    if n <= 0:
        return (float("nan"), float("nan"))
    if k < 0 or k > n:
        raise ValueError(f"k={k} outside [0, {n}]")
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def per_class_report(y_true: Sequence[str], y_pred: Sequence[Optional[str]],
                     classes: Iterable[str]) -> Dict[str, Dict[str, float]]:
    """
    Per-class precision / recall / F1 with Wilson 95% intervals.

    `y_pred` may contain None (abstained). An abstention counts against recall
    (the field was not named) and never as a false positive.
    """
    yt = np.asarray(list(y_true), dtype=object)
    yp = np.asarray(list(y_pred), dtype=object)
    out: Dict[str, Dict[str, float]] = {}
    for c in classes:
        tp = int(((yt == c) & (yp == c)).sum())
        fn = int(((yt == c) & (yp != c)).sum())
        fp = int(((yt != c) & (yp == c)).sum())
        n_true, n_pred = tp + fn, tp + fp
        rec = tp / n_true if n_true else float("nan")
        prec = tp / n_pred if n_pred else float("nan")
        f1 = (2 * prec * rec / (prec + rec)
              if n_true and n_pred and (prec + rec) > 0 else float("nan"))
        r_lo, r_hi = wilson_interval(tp, n_true)
        p_lo, p_hi = wilson_interval(tp, n_pred)
        out[c] = {
            "support": n_true, "n_predicted": n_pred, "tp": tp, "fp": fp, "fn": fn,
            "recall": _r(rec), "recall_ci95": [_r(r_lo), _r(r_hi)],
            "precision": _r(prec), "precision_ci95": [_r(p_lo), _r(p_hi)],
            "f1": _r(f1),
        }
    return out


def _r(x: float, nd: int = 4) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def ece(proba: np.ndarray, y: np.ndarray, n_bins: int = 10) -> float:
    """Top-1 ECE with equal-width bins — identical to train._ece."""
    conf = proba.max(axis=1)
    correct = (proba.argmax(axis=1) == y).astype(float)
    edges = np.linspace(0, 1, n_bins + 1)
    out = 0.0
    for i in range(n_bins):
        m = (conf > edges[i]) & (conf <= edges[i + 1])
        if m.any():
            out += m.mean() * abs(correct[m].mean() - conf[m].mean())
    return float(out)


def abstain_mask(proba: np.ndarray, n_scenes: np.ndarray, rule: Mapping) -> np.ndarray:
    """
    True where CropDetector would ABSTAIN. Same order and semantics as
    crop_detector.CropDetector._classify: raw p_max floor, then top-2 gap, then
    scene count.
    """
    srt = np.sort(proba, axis=1)[:, ::-1]
    p = srt[:, 0]
    gap = srt[:, 0] - srt[:, 1] if proba.shape[1] > 1 else p
    return ((p < float(rule.get("p_min", 0.0)))
            | (gap < float(rule.get("gap_min", 0.0)))
            | (np.asarray(n_scenes) < int(rule.get("n_scenes_min", 0))))


# =============================================================================
# training weights and split hygiene
# =============================================================================
def mh_sample_weights(y: np.ndarray, blocks: np.ndarray,
                      qa_weight: Optional[np.ndarray] = None,
                      cap: float = 3.0) -> np.ndarray:
    """
    Capped inverse-frequency class weight x 1/sqrt(block size) x QA weight,
    normalised to mean 1.

    The class factor is n / (k * n_c), clipped to [1/cap, cap], so adding ~1,000
    Marathwada cotton cannot make cotton a dominant class and a rare class
    cannot be up-weighted more than `cap` times. The block factor is the shipped
    recipe's de-clustering (train._sample_weights). `qa_weight` carries the
    0.5 for unreviewed soybean.
    """
    y = np.asarray(y)
    w = np.ones(len(y), dtype=float)
    cls, cnt = np.unique(y, return_counts=True)
    raw = {c: len(y) / (len(cls) * n) for c, n in zip(cls, cnt)}
    cls_w = {c: float(np.clip(v, 1.0 / cap, cap)) for c, v in raw.items()}
    w *= np.array([cls_w[v] for v in y])

    b, bcnt = np.unique(np.asarray(blocks), return_counts=True)
    bw = {k: 1.0 / np.sqrt(n) for k, n in zip(b, bcnt)}
    w *= np.array([bw[v] for v in blocks])

    if qa_weight is not None:
        w *= np.asarray(qa_weight, dtype=float)
    return w * (len(w) / w.sum())


def qa_weights(qa_checked: Sequence, crop: Sequence[str],
               unchecked_weight: float = 0.5) -> np.ndarray:
    """0.5 for soybean explicitly marked Unchecked (qa_checked False); 1 otherwise.

    Cotton has no review flag (NA) and existing rows have none either: unknown is
    not the same as unchecked, so they keep full weight.
    """
    out = np.ones(len(crop), dtype=float)
    for i, (q, c) in enumerate(zip(qa_checked, crop)):
        if c == "Soyabean" and q is not None and not pd.isna(q) and not bool(q):
            out[i] = unchecked_weight
    return out


def training_split_mask(geom_hash: Sequence[str], split: Mapping) -> np.ndarray:
    """True for rows that may train or tune: not in any held-out block."""
    banned = set(split["excluded_from_training"]) | set(split["test_geom_hashes"])
    return ~np.isin(np.asarray(geom_hash, dtype=object), list(banned))


def split_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def data_hash(X: np.ndarray, y: Sequence, ids: Sequence[str], w: np.ndarray) -> str:
    """Content hash of exactly what the final model was fitted on."""
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(np.round(X, 6)).tobytes())
    h.update("\x1f".join(map(str, y)).encode())
    h.update("\x1f".join(map(str, ids)).encode())
    h.update(np.ascontiguousarray(np.round(w, 6)).tobytes())
    return h.hexdigest()[:16]


# =============================================================================
# label shift (Saerens, Latinne & Decaestecker 2002)
# =============================================================================
def apply_label_shift(proba: np.ndarray, classes: Sequence[str],
                      train_prior: Mapping[str, float],
                      target_prior: Mapping[str, float],
                      floor: float = 1e-6) -> np.ndarray:
    """
    p'(c|x) proportional to p(c|x) * pi_target(c) / pi_train(c), renormalised.

    `train_prior` is the prior the classifier was effectively fitted under
    (with class-balanced weights that is close to uniform, NOT the raw counts).
    `target_prior` may name only some classes — e.g. official statistics list
    the major Kharif crops. The unlisted classes then share the remaining mass
    in proportion to their training prior, so the correction says nothing about
    crops the statistics are silent on beyond "everything else is this rare".
    A class absent from both priors keeps ratio 1.
    """
    proba = np.asarray(proba, dtype=float)
    classes = list(classes)
    pt = np.array([float(train_prior.get(c, 0.0)) for c in classes])
    if pt.sum() <= 0:
        raise ValueError("train_prior has no mass on these classes")
    pt = np.maximum(pt / pt.sum(), floor)

    listed = np.array([c in target_prior for c in classes])
    tgt = np.array([float(target_prior.get(c, 0.0)) for c in classes])
    listed_mass = float(tgt[listed].sum())
    if listed_mass > 1.0 + 1e-9:
        tgt[listed] = tgt[listed] / listed_mass
        listed_mass = 1.0
    rest = max(0.0, 1.0 - listed_mass)
    unl = ~listed
    if unl.any():
        tgt[unl] = rest * pt[unl] / pt[unl].sum()
    tgt = np.maximum(tgt, floor)

    ratio = tgt / pt
    out = proba * ratio[None, :]
    s = out.sum(axis=1, keepdims=True)
    s[s <= 0] = 1.0
    return out / s


CROP_NAME_MAP = {
    # official DES / APY spellings -> model class names
    "soybean": "Soyabean", "soyabean": "Soyabean",
    "cotton": "Cotton", "cotton(lint)": "Cotton", "cotton (lint)": "Cotton",
    "arhar/tur": "Tur", "arhar": "Tur", "tur": "Tur", "pigeonpea": "Tur",
    "jowar": "Jowar", "bajra": "Bajra", "maize": "Maize", "rice": "Rice",
    "sugarcane": "Sugarcane", "groundnut": "Groundnut", "gram": "Gram",
    "wheat": "Wheat", "onion": "Onion", "banana": "Banana", "grapes": "Grapes",
    "chillies": "Chilli", "dry chillies": "Chilli", "chilli": "Chilli",
    "potato": "Potato", "tobacco": "Tobacco",
    "rapeseed &mustard": "Mustard", "rapeseed & mustard": "Mustard", "mustard": "Mustard",
}

SHARES_COLUMNS = ["district", "state", "crop", "area_ha", "year", "source"]


def load_district_shares(path: Path) -> pd.DataFrame:
    """
    Read the official-statistics file. Returns an empty frame (right columns)
    when the file is absent or has no data rows; never invents numbers.
    """
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=SHARES_COLUMNS + ["model_crop"])
    df = pd.read_csv(path, comment="#")
    missing = [c for c in SHARES_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} lacks columns {missing}")
    df = df.dropna(subset=["district", "crop", "area_ha"])
    df = df[pd.to_numeric(df["area_ha"], errors="coerce") > 0].copy()
    df["area_ha"] = df["area_ha"].astype(float)
    df["model_crop"] = [CROP_NAME_MAP.get(str(c).strip().lower(), str(c).strip())
                        for c in df["crop"]]
    return df


def region_prior(shares: pd.DataFrame, districts: Optional[Sequence[str]] = None,
                 year: Optional[int] = None) -> Dict[str, float]:
    """Area-weighted crop shares over `districts` (all rows when None)."""
    df = shares
    if districts is not None:
        want = {d.strip().lower() for d in districts}
        df = df[df["district"].str.strip().str.lower().isin(want)]
    if year is not None and "year" in df:
        df = df[df["year"].astype(str) == str(year)]
    if df.empty:
        return {}
    a = df.groupby("model_crop")["area_ha"].sum()
    return {k: float(v / a.sum()) for k, v in a.items()}


# =============================================================================
# out-of-distribution score
# =============================================================================
def fit_ood(X: np.ndarray, feature_names: Sequence[str], var_keep: float = 0.95,
            quantile: float = 0.99) -> Dict:
    """
    PCA-whitened Mahalanobis distance to the training feature distribution.

    Stored as plain arrays so inference needs only numpy:
        z      = (x - mean) / scale
        u      = z @ components.T / sqrt(explained_variance)
        score  = mean(u ** 2)            # ~1 for a typical training row
        ood    = score > threshold       # threshold = training q99
    Raw duration and integral features span hundreds; standardising first
    keeps them from dominating.
    """
    X = np.asarray(X, dtype=float)
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale < 1e-9] = 1.0
    Z = (X - mean) / scale
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    var = S ** 2 / max(len(X) - 1, 1)
    cum = np.cumsum(var) / var.sum()
    k = int(np.searchsorted(cum, var_keep) + 1)
    comps, ev = Vt[:k], np.maximum(var[:k], 1e-12)
    model = {
        "method": "pca_whitened_mahalanobis",
        "feature_names": list(feature_names),
        "mean": mean, "scale": scale, "components": comps,
        "explained_variance": ev, "n_components": k, "var_keep": var_keep,
    }
    tr = ood_score(model, X)
    model["threshold"] = float(np.quantile(tr, quantile))
    model["threshold_quantile"] = quantile
    model["formula"] = ("z=(x-mean)/scale; u=(z@components.T)/sqrt(explained_variance); "
                        "score=mean(u**2); ood = score > threshold")
    return model


def ood_score(model: Mapping, X: np.ndarray) -> np.ndarray:
    Z = (np.asarray(X, dtype=float) - model["mean"]) / model["scale"]
    U = (Z @ np.asarray(model["components"]).T) / np.sqrt(model["explained_variance"])
    return (U ** 2).mean(axis=1)


# =============================================================================
# small utilities
# =============================================================================
def json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def dump_json(obj, path: Path) -> None:
    Path(path).write_text(json.dumps(obj, indent=2, default=json_default), encoding="utf-8")
