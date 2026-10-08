"""Classification and agreement metrics (pure numpy / pandas).

All functions take plain sequences of labels (strings or ints) and return
plain Python / pandas objects so the results can be written to JSON.

Conventions
-----------
* ``y_true`` is the reference label, ``y_pred`` the map / model label.
* Precision = TP / (TP + FP)  (user's accuracy for a simple random sample).
* Recall    = TP / (TP + FN)  (producer's accuracy for a simple random sample).
* 95% intervals for proportions use the Wilson score interval.
* If the sample is *stratified* by map class (as in D1), naive precision is
  fine within a stratum but recall is biased; use :mod:`evaluation.area`
  for D1 instead.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

Z95 = 1.959963984540054


# ---------------------------------------------------------------------------
# Proportions
# ---------------------------------------------------------------------------
def wilson_interval(k: float, n: float, z: float = Z95) -> tuple:
    """Wilson score interval for k successes in n trials.

    Returns ``(low, high)``; ``(nan, nan)`` when ``n == 0``.
    """
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _prop(k: int, n: int, z: float = Z95) -> dict:
    lo, hi = wilson_interval(k, n, z)
    return {
        "value": (k / n) if n else float("nan"),
        "k": int(k),
        "n": int(n),
        "ci_low": lo,
        "ci_high": hi,
    }


def _clean(seq: Iterable) -> np.ndarray:
    arr = np.asarray(list(seq), dtype=object)
    return arr


def _labels(y_true, y_pred, labels: Optional[Sequence] = None) -> List:
    if labels is not None:
        return list(labels)
    seen = []
    for v in list(y_true) + list(y_pred):
        if v not in seen:
            seen.append(v)
    try:
        return sorted(seen)
    except TypeError:
        return seen


# ---------------------------------------------------------------------------
# Confusion matrix and per-class metrics
# ---------------------------------------------------------------------------
def confusion_matrix(y_true, y_pred, labels: Optional[Sequence] = None) -> pd.DataFrame:
    """Counts with rows = reference (true) and columns = predicted."""
    yt, yp = _clean(y_true), _clean(y_pred)
    if len(yt) != len(yp):
        raise ValueError("y_true and y_pred differ in length")
    labs = _labels(yt, yp, labels)
    idx = {l: i for i, l in enumerate(labs)}
    m = np.zeros((len(labs), len(labs)), dtype=int)
    for t, p in zip(yt, yp):
        if t in idx and p in idx:
            m[idx[t], idx[p]] += 1
    df = pd.DataFrame(m, index=labs, columns=labs)
    df.index.name = "reference"
    df.columns.name = "predicted"
    return df


def per_class_metrics(y_true, y_pred, labels: Optional[Sequence] = None,
                      z: float = Z95) -> Dict[str, dict]:
    """Precision, recall (Wilson 95% CI) and F1 per class.

    F1 is reported as a point estimate only (it is not a binomial
    proportion, so a Wilson interval does not apply).
    """
    cm = confusion_matrix(y_true, y_pred, labels)
    out = {}
    m = cm.values
    for i, lab in enumerate(cm.index):
        tp = int(m[i, i])
        fp = int(m[:, i].sum() - tp)
        fn = int(m[i, :].sum() - tp)
        prec = _prop(tp, tp + fp, z)
        rec = _prop(tp, tp + fn, z)
        p, r = prec["value"], rec["value"]
        if (tp + fp) and (tp + fn) and (p + r) > 0:
            f1 = 2 * p * r / (p + r)
        elif (tp + fp + fn) > 0:
            f1 = 0.0
        else:
            f1 = float("nan")
        out[str(lab)] = {
            "precision": prec,
            "recall": rec,
            "f1": f1,
            "support": int(tp + fn),
            "predicted": int(tp + fp),
        }
    return out


def balanced_accuracy(y_true, y_pred, labels: Optional[Sequence] = None) -> float:
    """Mean recall over classes present in ``y_true``."""
    pcm = per_class_metrics(y_true, y_pred, labels)
    recalls = [v["recall"]["value"] for v in pcm.values() if v["support"] > 0]
    return float(np.mean(recalls)) if recalls else float("nan")


def macro_f1(y_true, y_pred, labels: Optional[Sequence] = None) -> float:
    """Unweighted mean F1 over classes (classes with no support and no
    predictions are ignored)."""
    pcm = per_class_metrics(y_true, y_pred, labels)
    vals = [v["f1"] for v in pcm.values() if not math.isnan(v["f1"])]
    return float(np.mean(vals)) if vals else float("nan")


def overall_accuracy(y_true, y_pred) -> dict:
    yt, yp = _clean(y_true), _clean(y_pred)
    k = int(sum(1 for a, b in zip(yt, yp) if a == b))
    return _prop(k, len(yt))


# ---------------------------------------------------------------------------
# Calibration and selective prediction
# ---------------------------------------------------------------------------
def expected_calibration_error(confidence, correct, n_bins: int = 10) -> dict:
    """ECE with ``n_bins`` equal-width bins on [0, 1].

    Bins are [0, 0.1), [0.1, 0.2), ..., [0.9, 1.0]; confidence 1.0 falls in
    the last bin. ``ECE = sum_b (n_b / N) * |acc_b - conf_b|``.
    """
    conf = np.asarray(confidence, dtype=float)
    corr = np.asarray(correct, dtype=float)
    if conf.shape != corr.shape:
        raise ValueError("confidence and correct differ in shape")
    if conf.size == 0:
        return {"ece": float("nan"), "n": 0, "bins": []}
    if np.any((conf < 0) | (conf > 1)) or np.any(np.isnan(conf)):
        raise ValueError("confidence must be within [0, 1]")
    # searchsorted on explicit edges avoids float error such as 0.3*10 = 2.999
    edges = np.arange(n_bins + 1) / n_bins
    idx = np.clip(np.searchsorted(edges, conf, side="right") - 1, 0, n_bins - 1)
    n = conf.size
    ece = 0.0
    bins = []
    for b in range(n_bins):
        mask = idx == b
        nb = int(mask.sum())
        if nb == 0:
            bins.append({"bin": b, "low": edges[b], "high": edges[b + 1], "n": 0,
                         "accuracy": None, "confidence": None})
            continue
        acc = float(corr[mask].mean())
        cb = float(conf[mask].mean())
        ece += nb / n * abs(acc - cb)
        bins.append({"bin": b, "low": float(edges[b]), "high": float(edges[b + 1]),
                     "n": nb, "accuracy": acc, "confidence": cb})
    return {"ece": float(ece), "n": int(n), "bins": bins}


def coverage_at_threshold(confidence, correct, threshold: float) -> dict:
    """Coverage (share with confidence >= threshold) and precision on the
    covered set (Wilson 95% CI). ``abstain_rate = 1 - coverage``."""
    conf = np.asarray(confidence, dtype=float)
    corr = np.asarray(correct, dtype=bool)
    keep = conf >= threshold
    n = conf.size
    nk = int(keep.sum())
    cov = _prop(nk, n)
    prec = _prop(int(corr[keep].sum()), nk)
    return {
        "threshold": float(threshold),
        "coverage": cov,
        "abstain_rate": (1 - cov["value"]) if n else float("nan"),
        "precision": prec,
    }


# ---------------------------------------------------------------------------
# Agreement between two raters
# ---------------------------------------------------------------------------
def interpret_kappa(kappa: float) -> str:
    """Landis & Koch (1977) wording."""
    if kappa is None or (isinstance(kappa, float) and math.isnan(kappa)):
        return "undefined"
    if kappa < 0:
        return "poor"
    if kappa <= 0.20:
        return "slight"
    if kappa <= 0.40:
        return "fair"
    if kappa <= 0.60:
        return "moderate"
    if kappa <= 0.80:
        return "substantial"
    return "almost perfect"


def cohen_kappa(rater_a, rater_b, labels: Optional[Sequence] = None) -> dict:
    """Cohen's kappa for two raters on the same items.

    Returns observed agreement ``po``, chance agreement ``pe``, ``kappa``,
    an approximate standard error ``sqrt(po(1-po) / (n (1-pe)^2))`` and the
    Landis & Koch interpretation.
    """
    a, b = _clean(rater_a), _clean(rater_b)
    if len(a) != len(b):
        raise ValueError("raters differ in length")
    n = len(a)
    if n == 0:
        return {"kappa": float("nan"), "po": float("nan"), "pe": float("nan"),
                "n": 0, "se": float("nan"), "interpretation": "undefined"}
    cm = confusion_matrix(a, b, labels).values.astype(float)
    tot = cm.sum()
    po = float(np.trace(cm) / tot)
    pe = float((cm.sum(axis=1) * cm.sum(axis=0)).sum() / (tot * tot))
    if pe >= 1.0:
        kappa = 1.0 if po >= 1.0 else float("nan")
        se = float("nan")
    else:
        kappa = (po - pe) / (1.0 - pe)
        se = math.sqrt(po * (1 - po) / (tot * (1 - pe) ** 2))
    return {"kappa": float(kappa), "po": po, "pe": pe, "n": int(tot), "se": se,
            "interpretation": interpret_kappa(kappa)}


def per_class_kappa(rater_a, rater_b, labels: Optional[Sequence] = None) -> Dict[str, dict]:
    """One-vs-rest kappa for each class."""
    a, b = _clean(rater_a), _clean(rater_b)
    labs = _labels(a, b, labels)
    out = {}
    for lab in labs:
        aa = [x == lab for x in a]
        bb = [x == lab for x in b]
        out[str(lab)] = cohen_kappa(aa, bb, labels=[True, False])
    return out


# ---------------------------------------------------------------------------
# Convenience bundle
# ---------------------------------------------------------------------------
def classification_report(y_true, y_pred, confidence=None, labels=None,
                          threshold: Optional[float] = None,
                          source: str = "unspecified") -> dict:
    """Everything the gate report needs from a simple-random or held-out
    evaluation set, as a JSON-ready dict."""
    yt = list(y_true)
    yp = list(y_pred)
    rep = {
        "kind": "classification_metrics",
        "source": source,
        "n": len(yt),
        "labels": [str(l) for l in _labels(yt, yp, labels)],
        "per_class": per_class_metrics(yt, yp, labels),
        "overall_accuracy": overall_accuracy(yt, yp),
        "balanced_accuracy": balanced_accuracy(yt, yp, labels),
        "macro_f1": macro_f1(yt, yp, labels),
        "confusion_matrix": confusion_matrix(yt, yp, labels).to_dict(orient="index"),
    }
    if confidence is not None:
        correct = [a == b for a, b in zip(yt, yp)]
        rep["calibration"] = expected_calibration_error(confidence, correct)
        rep["ece"] = rep["calibration"]["ece"]
        if threshold is not None:
            rep["coverage"] = coverage_at_threshold(confidence, correct, threshold)
    return rep
