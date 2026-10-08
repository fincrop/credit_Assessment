"""Weighted Whittaker smoothing with an upper envelope (Atzberger & Eilers 2011).

Each pixel's irregular NDVI observations go onto a daily grid and are solved as

    (W + lambda D'D) z = W y      D = second differences

Residual cloud and haze only ever lower NDVI, so after each pass observations
below the curve lose weight and the curve rises to the upper envelope of the
clear looks (Chen et al. 2004 use the same idea with Savitzky-Golay).

The smoothed curve drives phenology and the yield integral. Published index
maps always show the raw observation of a real clear date, never this curve.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
from scipy.linalg import solveh_banded

# Daily grid. lambda 400 lets a crop rise over ~3 weeks while flattening single
# bad looks; tuned on synthetic cotton/soybean curves with 30% cloud gaps
# (tests/test_raster_smooth.py).
LAMBDA = 400.0
ENVELOPE_PASSES = 2
BELOW_WEIGHT = 0.35


def _banded_penalty(n: int, lam: float) -> np.ndarray:
    """Upper-banded form of lambda * D2'D2 for solveh_banded (u=2)."""
    d0 = np.full(n, 6.0)
    d0[[0, -1]] = 1.0
    if n > 2:
        d0[[1, -2]] = 5.0
    d1 = np.full(n - 1, -4.0)
    if n > 1:
        d1[[0, -1]] = -2.0
    d2 = np.ones(n - 2)
    ab = np.zeros((3, n))
    ab[2] = d0 * lam
    ab[1, 1:] = d1 * lam
    ab[0, 2:] = d2 * lam
    return ab


def daily_axis(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def to_daily(obs_dates: list[date], values: np.ndarray, weights: np.ndarray,
             start: date, end: date) -> tuple[np.ndarray, np.ndarray]:
    """Observations (T, N) -> daily (D, N) weighted means and summed weights."""
    days = (end - start).days + 1
    n = values.shape[1] if values.ndim == 2 else 0
    y = np.zeros((days, n), np.float64)
    w = np.zeros((days, n), np.float64)
    for t, d in enumerate(obs_dates):
        k = (d - start).days
        if k < 0 or k >= days:
            continue
        v = values[t]
        ok = np.isfinite(v) & (weights[t] > 0)
        y[k, ok] += v[ok] * weights[t][ok]
        w[k, ok] += weights[t][ok]
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.where(w > 0, y / np.where(w > 0, w, 1.0), 0.0)
    return y, w


def whittaker(y: np.ndarray, w: np.ndarray, lam: float = LAMBDA,
              passes: int = ENVELOPE_PASSES) -> np.ndarray:
    """Smooth each column of y (D, N) with weights w (D, N). Columns without
    any weight come back NaN."""
    days, n = y.shape
    out = np.full((days, n), np.nan, np.float32)
    if days < 4:
        return out
    pen = _banded_penalty(days, lam)
    for j in range(n):
        wj = w[:, j].copy()
        if (wj > 0).sum() < 3:
            continue
        yj = y[:, j]
        for p in range(passes + 1):
            ab = pen.copy()
            ab[2] += wj
            z = solveh_banded(ab, wj * yj, lower=False, check_finite=False)
            if p == passes:
                break
            below = (wj > 0) & (yj < z)
            wj = np.where(below, wj * BELOW_WEIGHT, wj)
        out[:, j] = z.astype(np.float32)
    return out


def smooth_series(obs_dates: list[date], values: np.ndarray, weights: np.ndarray,
                  start: date, end: date, lam: float = LAMBDA) -> np.ndarray:
    """Convenience: observations (T, N) straight to a smoothed daily (D, N) curve."""
    y, w = to_daily(obs_dates, values, weights, start, end)
    return whittaker(y, w, lam)
