"""
Tier-2 feature blocks — Sentinel-1 radar, Sentinel-2 surface reflectance,
thermal time / weather anomalies, and AlphaEarth satellite embeddings.

SHARED BY TRAINING AND SERVING
──────────────────────────────
Like `crop_detector.build_feature_dict`, every function here is imported by
both the offline trainer (Crop_classification_model/src/features_extra.py) and
the live pipeline. They are pure: inputs are plain Python / pandas structures
already fetched by `data_acquisition.extra_sources`, so a feature can never be
computed two different ways.

WHY EACH BLOCK EXISTS
─────────────────────
S1   Kharif is monsoon. Sentinel-2 loses most of June–September to cloud, so
     rice / soybean / cotton / tur trajectories are interpolated across exactly
     the weeks that separate them. C-band SAR sees through cloud: VH tracks
     canopy volume, VV drops sharply on flooded paddies, and VH/VV separates
     broadleaf from grass-type canopies.
REFL Raw reflectance (not only ratios). Indices cancel brightness by design, but
     brightness carries crop information — chilli/cotton open canopies over
     bright black-cotton soils, grapes' trellis rows, sugarcane's dark dense
     canopy. Includes red-edge and SWIR bands the index set only partly uses.
WX   Thermal time: a crop's heat budget to maturity is a property of the plant.
     Measured +5.9 blocked / +50% LOEO (src/weather.py docstring).
EMB  Google/DeepMind AlphaEarth annual 64-d embeddings — a learned summary of a
     full year of S1+S2+Landsat+climate at 10 m. Annual, so it cannot isolate a
     single season on double-cropped land; it is an auxiliary block and is
     judged on LOEO, not only on blocked CV.

A block the bundle does not list is never computed; a block the bundle lists
but whose input is unavailable yields NaN columns (never zeros — zero VH is a
real, meaningful radar value), which XGBoost routes down its learned missing
branch.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

EXTRA_FEATURES_VERSION = "tier2_v1"

# Scenes this far outside [sowing, harvest] still count — the same padding the
# weather block uses, so the rise and fall shoulders are visible.
PAD_DAYS = 15

S1_GRID = 12          # normalised grid points for radar trajectories
REFL_GRID = 6         # normalised grid points for each reflectance band
S1_BANDS = ("VV", "VH")
REFL_BANDS = ("B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12")
EMB_DIM = 64

GDD_BASE_C = 10.0
GDD_CAP_C = 35.0

WEATHER_NAMES: Tuple[str, ...] = (
    "gdd_total", "log_gdd_total", "gdd_per_day", "gdd_peak_fraction",
    "rain_anomaly_ratio", "gdd_anomaly_ratio", "t2m_anomaly_c",
    "dry_spell_max_days", "rain_days_fraction",
)


# =============================================================================
# names — ground truth for the bundle fail-fast check
# =============================================================================
def s1_feature_names() -> List[str]:
    names = []
    for b in ("S1VV", "S1VH", "S1RATIO"):
        names += [f"{b}_t{t + 1:02d}" for t in range(S1_GRID)]
    names += ["S1VH_min", "S1VH_max", "S1VH_range", "S1VV_min", "S1VV_drop",
              "S1RATIO_mean", "S1VH_std", "S1_n_obs"]
    return names


def refl_feature_names() -> List[str]:
    names = []
    for b in REFL_BANDS:
        names += [f"R{b}_t{t + 1:02d}" for t in range(REFL_GRID)]
    names += [f"R{b}_peak" for b in REFL_BANDS]
    names += ["R_n_obs"]
    return names


def weather_feature_names() -> List[str]:
    return list(WEATHER_NAMES)


def embedding_feature_names() -> List[str]:
    return [f"EMB{i:02d}" for i in range(EMB_DIM)]


BLOCKS: Dict[str, Any] = {
    "s1": s1_feature_names,
    "refl": refl_feature_names,
    "weather": weather_feature_names,
    "emb": embedding_feature_names,
}


def all_extra_feature_names() -> List[str]:
    out: List[str] = []
    for fn in BLOCKS.values():
        out += fn()
    return out


def blocks_required(feature_names: Iterable[str]) -> List[str]:
    """Which tier-2 blocks a bundle's feature list needs."""
    wanted = set(feature_names)
    return [b for b, fn in BLOCKS.items() if wanted & set(fn())]


# =============================================================================
# helpers
# =============================================================================
def _as_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "date") and callable(v.date):          # pandas Timestamp
        try:
            return v.date()
        except Exception:                                # noqa: BLE001
            pass
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _window(sowing: Any, harvest: Any) -> Tuple[date, date]:
    s, h = _as_date(sowing), _as_date(harvest)
    if s is None or h is None:
        raise ValueError("sowing and harvest dates are required")
    return s - timedelta(days=PAD_DAYS), h + timedelta(days=PAD_DAYS)


def _grid_by_date(dates: Sequence[date], values: Sequence[float],
                  lo: date, hi: date, n: int) -> np.ndarray:
    """
    Interpolate irregular observations onto `n` evenly spaced *calendar*
    positions spanning [lo, hi].

    Unlike build_feature_dict (which spaces observations by rank), this uses
    real dates: S1 has a fixed revisit, and a gap is a gap, not a compression
    of the curve. Outside the observed span the nearest value is held rather
    than extrapolated. Fewer than 2 finite observations -> all NaN.
    """
    x = np.array([(d - lo).days for d in dates], dtype=float)
    y = np.array(values, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 2:
        return np.full(n, np.nan)
    order = np.argsort(x[ok])
    xs, ys = x[ok][order], y[ok][order]
    grid = np.linspace(0.0, float((hi - lo).days), n)
    return np.interp(grid, xs, ys)


def _slice_series(series: Sequence[Mapping[str, Any]], lo: date, hi: date
                  ) -> List[Tuple[date, Mapping[str, Any]]]:
    out = []
    for rec in series or []:
        d = _as_date(rec.get("date"))
        if d is not None and lo <= d <= hi:
            out.append((d, rec))
    out.sort(key=lambda t: t[0])
    return out


def _f(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return f if math.isfinite(f) else float("nan")


# =============================================================================
# Sentinel-1
# =============================================================================
def build_s1_features(series: Sequence[Mapping[str, Any]],
                      sowing: Any, harvest: Any) -> Dict[str, float]:
    """
    series: [{"date": "YYYY-MM-DD", "VV": dB, "VH": dB}, ...] — parcel-mean
    backscatter per composite bin (GRD is already in dB in Earth Engine).
    """
    names = s1_feature_names()
    out = {k: float("nan") for k in names}
    lo, hi = _window(sowing, harvest)
    obs = [(d, r) for d, r in _slice_series(series, lo, hi)
           if math.isfinite(_f(r.get("VV"))) and math.isfinite(_f(r.get("VH")))]
    out["S1_n_obs"] = float(len(obs))
    if len(obs) < 2:
        return out

    dts = [d for d, _ in obs]
    vv = np.array([_f(r["VV"]) for _, r in obs])
    vh = np.array([_f(r["VH"]) for _, r in obs])
    ratio = vh - vv                                  # dB difference = log ratio

    for tag, arr in (("S1VV", vv), ("S1VH", vh), ("S1RATIO", ratio)):
        g = _grid_by_date(dts, arr, lo, hi, S1_GRID)
        for t, v in enumerate(g):
            out[f"{tag}_t{t + 1:02d}"] = float(v)

    out["S1VH_min"] = float(np.min(vh))
    out["S1VH_max"] = float(np.max(vh))
    out["S1VH_range"] = float(np.max(vh) - np.min(vh))
    out["S1VV_min"] = float(np.min(vv))
    # Flooding signature: how far VV dips below its own median. Transplanted
    # rice drops 5-10 dB for a few weeks; dryland crops barely move.
    out["S1VV_drop"] = float(np.median(vv) - np.min(vv))
    out["S1RATIO_mean"] = float(np.mean(ratio))
    out["S1VH_std"] = float(np.std(vh))
    return out


# =============================================================================
# Sentinel-2 surface reflectance
# =============================================================================
def build_reflectance_features(series: Sequence[Mapping[str, Any]],
                               sowing: Any, harvest: Any,
                               peak: Any = None) -> Dict[str, float]:
    """
    series: [{"date": ..., "B2": refl, ..., "B12": refl}, ...] with reflectance
    in 0-1 units (L2A DN / 10000), parcel mean per composite bin.
    `peak` (the cycle's NDVI peak date) selects the R*_peak values.
    """
    names = refl_feature_names()
    out = {k: float("nan") for k in names}
    lo, hi = _window(sowing, harvest)
    obs = [(d, r) for d, r in _slice_series(series, lo, hi)
           if math.isfinite(_f(r.get("B4"))) and math.isfinite(_f(r.get("B8")))]
    out["R_n_obs"] = float(len(obs))
    if len(obs) < 2:
        return out

    dts = [d for d, _ in obs]
    pk = _as_date(peak)
    if pk is not None:
        pi = int(np.argmin([abs((d - pk).days) for d in dts]))
    else:
        ndvi = [(_f(r["B8"]) - _f(r["B4"])) / max(_f(r["B8"]) + _f(r["B4"]), 1e-6)
                for _, r in obs]
        pi = int(np.nanargmax(ndvi))

    for b in REFL_BANDS:
        arr = np.array([_f(r.get(b)) for _, r in obs])
        g = _grid_by_date(dts, arr, lo, hi, REFL_GRID)
        for t, v in enumerate(g):
            out[f"R{b}_t{t + 1:02d}"] = float(v)
        out[f"R{b}_peak"] = float(arr[pi])
    return out


# =============================================================================
# weather — thermal time and anomalies
# =============================================================================
def gdd(tmax: np.ndarray, tmin: np.ndarray) -> np.ndarray:
    """Capped-mean growing degree days. A single generic base temperature: a
    crop-specific base would leak the label into the feature."""
    hi = np.clip(tmax, GDD_BASE_C, GDD_CAP_C)
    lo = np.clip(tmin, GDD_BASE_C, GDD_CAP_C)
    return np.maximum((hi + lo) / 2.0 - GDD_BASE_C, 0.0)


def add_climatology(daily):
    """
    Attach per-day-of-year climatology (rain_norm, gdd_norm, t2m_norm) to a
    single location's daily frame, computed from every year the frame holds.

    Expects columns date, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR. Returns a copy
    sorted by date with `gdd` added. Multi-location frames must be grouped by
    the caller.
    """
    import pandas as pd

    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"])
    d["gdd"] = gdd(d["T2M_MAX"].to_numpy(dtype=float), d["T2M_MIN"].to_numpy(dtype=float))
    d["doy"] = d["date"].dt.dayofyear
    clim = (d.groupby("doy")
             .agg(rain_norm=("PRECTOTCORR", "mean"),
                  gdd_norm=("gdd", "mean"),
                  t2m_norm=("T2M", "mean"))
             .reset_index())
    d = d.drop(columns=[c for c in ("rain_norm", "gdd_norm", "t2m_norm") if c in d])
    return d.merge(clim, on="doy", how="left").sort_values("date")


def build_weather_features(daily, sowing: Any, harvest: Any,
                           peak: Any = None) -> Dict[str, float]:
    """
    daily: one location's frame from `add_climatology` (NASA POWER daily).
    Only thermal-time quantities and anomalies — see src/weather.py.
    """
    import pandas as pd

    out = {k: float("nan") for k in WEATHER_NAMES}
    if daily is None or len(daily) == 0:
        return out
    s, h = pd.Timestamp(_as_date(sowing)), pd.Timestamp(_as_date(harvest))
    w = daily[(daily["date"] >= s) & (daily["date"] <= h)]
    if len(w) < 10:
        return out

    g = w["gdd"].to_numpy(dtype=float)
    total = float(np.nansum(g))
    rain = w["PRECTOTCORR"].to_numpy(dtype=float)
    rain_n = float(np.nansum(w["rain_norm"].to_numpy(dtype=float)))

    peak_frac = float("nan")
    pk = _as_date(peak)
    if pk is not None and total > 0:
        pkt = pd.Timestamp(pk)
        if s <= pkt <= h:
            peak_frac = float(np.nansum(g[(w["date"] <= pkt).to_numpy()]) / total)

    best = cur = 0
    for r in rain:
        cur = cur + 1 if (r < 1.0) else 0
        best = max(best, cur)

    out.update({
        "gdd_total": round(total, 1),
        "log_gdd_total": round(float(np.log1p(total)), 4),
        "gdd_per_day": round(total / max(len(w), 1), 3),
        "gdd_peak_fraction": round(peak_frac, 4) if peak_frac == peak_frac else float("nan"),
        "rain_anomaly_ratio": round(float(np.nansum(rain) / rain_n), 4) if rain_n > 5 else float("nan"),
        "gdd_anomaly_ratio": round(float(total / max(float(np.nansum(w["gdd_norm"])), 1e-6)), 4),
        "t2m_anomaly_c": round(float(np.nanmean(w["T2M"] - w["t2m_norm"])), 3),
        "dry_spell_max_days": float(best),
        "rain_days_fraction": round(float((rain >= 1.0).mean()), 4),
    })
    return out


# =============================================================================
# AlphaEarth embeddings
# =============================================================================
def embedding_year(sowing: Any, harvest: Any, peak: Any = None) -> int:
    """The embedding year a cycle belongs to: the calendar year of its peak
    (the season's densest canopy), falling back to the window midpoint."""
    pk = _as_date(peak)
    if pk is None:
        s, h = _as_date(sowing), _as_date(harvest)
        pk = s + (h - s) / 2
    return pk.year


def build_embedding_features(vec: Optional[Sequence[float]]) -> Dict[str, float]:
    names = embedding_feature_names()
    if vec is None or len(vec) != EMB_DIM:
        return {k: float("nan") for k in names}
    v = np.array([_f(x) for x in vec])
    # Parcel means of unit vectors are shorter than 1; re-normalise so the
    # block encodes direction (land-surface type), not within-parcel spread.
    n = float(np.sqrt(np.nansum(v * v)))
    if n > 1e-9:
        v = v / n
    return {k: float(x) for k, x in zip(names, v)}


# =============================================================================
# one call for everything a bundle needs
# =============================================================================
def build_extra_features(blocks: Iterable[str], sowing: Any, harvest: Any,
                         peak: Any = None, *, s1_series=None, refl_series=None,
                         weather_daily=None, embedding=None) -> Dict[str, float]:
    out: Dict[str, float] = {}
    blocks = set(blocks)
    if "s1" in blocks:
        out.update(build_s1_features(s1_series or [], sowing, harvest))
    if "refl" in blocks:
        out.update(build_reflectance_features(refl_series or [], sowing, harvest, peak))
    if "weather" in blocks:
        out.update(build_weather_features(weather_daily, sowing, harvest, peak))
    if "emb" in blocks:
        out.update(build_embedding_features(embedding))
    return out
