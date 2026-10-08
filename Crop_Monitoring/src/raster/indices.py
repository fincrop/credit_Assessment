"""Spectral indices from reflectance stacks, and Landsat-to-Sentinel-2 calibration.

Landsat 8/9 and Sentinel-2 see slightly different bands, so their NDVI differs
by a few hundredths on the same field. Instead of hard-coding published
coefficients, the offset is fitted on this village's own data: pixels both
sensors saw clear within two days of each other. With too few such pairs the
Landsat looks keep their raw values and a lower weight.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from src.raster.stack import LANDSAT_WEIGHT, OpticalSeries, SceneStack

OPTICAL_INDICES = ("ndvi", "ndre", "cire", "ndmi", "mndwi", "bsi", "psri")
# Indices both sensors can produce. Landsat has no red-edge bands.
SHARED_INDICES = ("ndvi", "ndmi", "mndwi", "bsi")

PAIR_MAX_DAYS = 2
MIN_PAIR_PIXELS = 500
MIN_PAIR_CORR = 0.80
MIN_PAIRS = 3
SLOPE_RANGE = (0.7, 1.7)


def _nd(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (a - b) / (a + b)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def s2_indices(stack: SceneStack) -> dict[str, np.ndarray]:
    b = stack.bands
    with np.errstate(invalid="ignore", divide="ignore"):
        cire = (b["B7"] / b["B5"] - 1.0).astype(np.float32)
        swir_red = b["B11"] + b["B4"]
        nir_blue = b["B8"] + b["B2"]
        bsi = ((swir_red - nir_blue) / (swir_red + nir_blue)).astype(np.float32)
        psri = ((b["B4"] - b["B2"]) / b["B6"]).astype(np.float32)
    for arr in (cire, bsi, psri):
        arr[~np.isfinite(arr)] = np.nan
    return {
        "ndvi": _nd(b["B8"], b["B4"]),
        "ndre": _nd(b["B8"], b["B5"]),
        "cire": cire,
        "ndmi": _nd(b["B8"], b["B11"]),
        "mndwi": _nd(b["B3"], b["B11"]),
        "bsi": bsi,
        "psri": psri,
    }


def landsat_indices(stack: SceneStack) -> dict[str, np.ndarray]:
    """Landsat bands are named by their Sentinel-2 equivalent at fetch time."""
    b = stack.bands
    with np.errstate(invalid="ignore", divide="ignore"):
        swir_red = b["B11"] + b["B4"]
        nir_blue = b["B8"] + b["B2"]
        bsi = ((swir_red - nir_blue) / (swir_red + nir_blue)).astype(np.float32)
    bsi[~np.isfinite(bsi)] = np.nan
    return {
        "ndvi": _nd(b["B8"], b["B4"]),
        "ndmi": _nd(b["B8"], b["B11"]),
        "mndwi": _nd(b["B3"], b["B11"]),
        "bsi": bsi,
    }


def fit_cross_calibration(
    s2_dates: list[date], s2: dict[str, np.ndarray],
    ls_dates: list[date], ls: dict[str, np.ndarray],
) -> dict[str, dict]:
    """Per index: Landsat -> Sentinel-2 linear fit on near-simultaneous clear pixels.

    Theil-Sen-like robustness from a median-of-slopes on binned values would be
    stronger; an ordinary fit after trimming the 2% tails is adequate at
    thousands of pairs and is easy to audit. Returns {index: {slope, offset, n}}.
    """
    out = {}
    for name in SHARED_INDICES:
        fits = []
        n_total = 0
        for i, dl in enumerate(ls_dates):
            for j, ds in enumerate(s2_dates):
                if abs((dl - ds).days) > PAIR_MAX_DAYS:
                    continue
                x = ls[name][i].ravel()
                y = s2[name][j].ravel()
                ok = np.isfinite(x) & np.isfinite(y)
                if ok.sum() < MIN_PAIR_PIXELS:
                    continue
                x, y = x[ok], y[ok]
                lo, hi = np.quantile(y - x, [0.02, 0.98])
                keep = ((y - x) >= lo) & ((y - x) <= hi)
                if keep.sum() < MIN_PAIR_PIXELS or np.std(x[keep]) < 1e-3:
                    continue
                corr = float(np.corrcoef(x[keep], y[keep])[0, 1])
                if corr < MIN_PAIR_CORR:
                    continue
                slope, offset = np.polyfit(x[keep], y[keep], 1)
                fits.append((float(slope), float(offset), corr))
                n_total += int(keep.sum())
        # One fit per near-simultaneous pair, then the median across pairs. A
        # single fit pooled over all pairs is flattened by regression dilution
        # (noise in the Landsat values): on Dhaswadi 2026 the pooled NDVI slope
        # was 0.43 while every individual pair gave 1.1-1.5 (r ~ 0.9).
        if len(fits) < MIN_PAIRS:
            out[name] = {"slope": 1.0, "offset": 0.0, "pairs": len(fits), "n": n_total, "applied": False}
            continue
        slope = float(np.median([f[0] for f in fits]))
        offset = float(np.median([f[1] for f in fits]))
        sane = SLOPE_RANGE[0] <= slope <= SLOPE_RANGE[1] and abs(offset) <= 0.3
        out[name] = {"slope": slope if sane else 1.0, "offset": offset if sane else 0.0,
                     "fit_slope": slope, "fit_offset": offset, "pairs": len(fits),
                     "slope_iqr": [round(float(q), 3) for q in np.percentile([f[0] for f in fits], [25, 75])],
                     "n": n_total, "applied": bool(sane)}
    return out


def apply_calibration(ls: dict[str, np.ndarray], cal: dict[str, dict]) -> dict[str, np.ndarray]:
    out = {}
    for name, arr in ls.items():
        c = cal.get(name)
        out[name] = (arr * c["slope"] + c["offset"]).astype(np.float32) if c else arr
    return out


def optical_series(
    s2: SceneStack | None,
    landsat: SceneStack | None,
    pixels: np.ndarray,
    s2_idx: dict[str, np.ndarray] | None = None,
    ls_idx: dict[str, np.ndarray] | None = None,
    cloud_weight: np.ndarray | None = None,
) -> OpticalSeries:
    """Observations for `pixels` (bool (H, W) mask) from both sensors, date-sorted."""
    rows: list[tuple[date, str, dict[str, np.ndarray], np.ndarray]] = []
    if s2 is not None and s2.n:
        idx = s2_idx or s2_indices(s2)
        for t, d in enumerate(s2.dates):
            vals = {k: v[t][pixels] for k, v in idx.items()}
            w = np.isfinite(vals["ndvi"]).astype(np.float32)
            if cloud_weight is not None:
                w = w * np.nan_to_num(cloud_weight[t][pixels], nan=0.0)
            rows.append((d, "s2", vals, w))
    if landsat is not None and landsat.n:
        idx = ls_idx or landsat_indices(landsat)
        for t, d in enumerate(landsat.dates):
            vals = {k: v[t][pixels] for k, v in idx.items()}
            w = np.isfinite(vals["ndvi"]).astype(np.float32) * LANDSAT_WEIGHT
            rows.append((d, "landsat", vals, w))
    rows.sort(key=lambda r: (r[0], r[1]))
    n = int(pixels.sum())
    values = {}
    for name in OPTICAL_INDICES:
        values[name] = np.stack([r[2].get(name, np.full(n, np.nan, np.float32)) for r in rows]) \
            if rows else np.zeros((0, n), np.float32)
    weights = np.stack([r[3] for r in rows]) if rows else np.zeros((0, n), np.float32)
    return OpticalSeries([r[0] for r in rows], [r[1] for r in rows], values, weights)
