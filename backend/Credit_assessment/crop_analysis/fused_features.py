"""
Cloud-robust kharif features: optical + Sentinel-1 fused on one season calendar.

Shared by the trainer (Crop_classification_model/src/train_fused.py) and the
live classifier (area_classifier), so a feature means the same thing on both
sides.

Why: the tier-1 model needs >= 5 clear optical looks inside a detected crop
cycle. Under the Marathwada monsoon most fields have 2-4 (Dhaswadi, 2 Oct
2026: 1,786 of 2,355 fields "Insufficient data"). Sentinel-1 sees through
cloud every 6-12 days. Here every field gets a continuous season curve:

  1. Per 10-day bin: optical indices where the bin had a clear look, and
     Sentinel-1 VV / VH (dB) where it had an acquisition.
  2. Radar-to-NDVI imputation (`SarNdviImputer`): a gradient-boosted model
     learned on bins where BOTH were seen, predicting NDVI from VH, VV, the
     cross-pol ratio, the field's own radar baseline and the day of season.
  3. Weighted Whittaker smoothing on a daily axis: optical looks weight 1.0,
     radar-imputed values IMPUTED_WEIGHT, with an upper envelope on optical
     only (residual cloud lowers NDVI; radar errors are symmetric).
  4. Features on a FIXED season calendar (1 May + 10-day steps), so a run
     on any date simply leaves later steps missing (NaN). The model is
     trained on seasons cut at many dates and learns to read partial ones.

Every bin also carries whether it was observed or imputed, so confidence can
reflect how much of the season was actually seen.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

SEASON_START = (5, 1)          # kharif season calendar starts 1 May
SEASON_END = (12, 31)
GRID_STEP = 10
N_STEPS = 25                   # 1 May .. 26 Dec
LAMBDA = 400.0
IMPUTED_WEIGHT = 0.3
ENVELOPE_PASSES = 2
BELOW_WEIGHT = 0.35
FEATURE_VERSION = "fused_v2"
# Steps of the green-up-aligned grid (10-day steps from the field's own green-up).
N_ALIGNED = 15
# Pre-season steps (from 1 May) whose low end is a field's own off-season floor.
FLOOR_STEPS = 6
# The live bundle is crop_classifier_fused_v3, trained on these absolute levels.
# A relative-to-floor rewrite (fused_v4) was fully trained and scored worse on
# the frozen Marathwada test: full-season soybean recall 0.916 against 0.958,
# and 1 October soybean recall 0.30 against 0.42. Those weights were removed.


def grid_dates(year: int) -> List[date]:
    s = date(year, *SEASON_START)
    return [s + timedelta(days=5 + GRID_STEP * k) for k in range(N_STEPS)]


# ── Whittaker (canonical copy; Crop_Monitoring's raster engine uses the same maths) ──
def _penalty(n: int, lam: float) -> np.ndarray:
    d0 = np.full(n, 6.0)
    d0[[0, -1]] = 1.0
    if n > 2:
        d0[[1, -2]] = 5.0
    d1 = np.full(n - 1, -4.0)
    if n > 1:
        d1[[0, -1]] = -2.0
    ab = np.zeros((3, n))
    ab[2] = d0 * lam
    ab[1, 1:] = d1 * lam
    ab[0, 2:] = 1.0 * lam
    return ab


def whittaker(y: np.ndarray, w: np.ndarray, envelope: np.ndarray, lam: float = LAMBDA) -> np.ndarray:
    """Smooth one daily series. `envelope` marks points the upper envelope may
    down-weight (optical); other weighted points keep their weight."""
    from scipy.linalg import solveh_banded

    n = len(y)
    if n < 4 or (w > 0).sum() < 3:
        return np.full(n, np.nan)
    pen = _penalty(n, lam)
    wj = w.astype(float).copy()
    for p in range(ENVELOPE_PASSES + 1):
        ab = pen.copy()
        ab[2] += wj
        z = solveh_banded(ab, wj * y, lower=False, check_finite=False)
        if p == ENVELOPE_PASSES:
            return z
        below = envelope & (wj > 0) & (y < z)
        wj = np.where(below, wj * BELOW_WEIGHT, wj)
    return z


# ── per-bin parsing ──────────────────────────────────────────────────────────
def _nd(a, b):
    if a is None or b is None:
        return None
    s = a + b
    return None if s == 0 else (a - b) / s


def parse_series(s1: Sequence[Mapping[str, Any]], refl: Sequence[Mapping[str, Any]],
                 as_of: Optional[date] = None) -> Tuple[List[dict], List[dict]]:
    """Series as stored by extra_sources (lists or JSON strings) -> optical and
    radar records, dropping anything dated after `as_of`."""
    if isinstance(s1, str):
        s1 = json.loads(s1)
    if isinstance(refl, str):
        refl = json.loads(refl)
    opt, rad = [], []
    for r in refl or []:
        d = date.fromisoformat(str(r["date"])[:10])
        if as_of and d > as_of:
            continue
        b4, b8 = r.get("B4"), r.get("B8")
        if b4 is None or b8 is None:
            continue
        opt.append({"date": d, "ndvi": _nd(b8, b4), "ndmi": _nd(b8, r.get("B11")),
                    "ndre": _nd(b8, r.get("B5"))})
    for r in s1 or []:
        d = date.fromisoformat(str(r["date"])[:10])
        if as_of and d > as_of:
            continue
        vv, vh = r.get("VV"), r.get("VH")
        if vv is None or vh is None:
            continue
        rad.append({"date": d, "vv": float(vv), "vh": float(vh), "cr": float(vh) - float(vv)})
    return opt, rad


def _doy_feats(d: date, year: int) -> Tuple[float, float, float]:
    t = (d - date(year, *SEASON_START)).days
    return t / 245.0, math.sin(2 * math.pi * d.timetuple().tm_yday / 365.25), \
        math.cos(2 * math.pi * d.timetuple().tm_yday / 365.25)


def sar_rows(rad: List[dict], year: int) -> np.ndarray:
    """Imputer inputs per radar record: VH, VV, VH-VV, VH minus the field's
    lowest VH this season (removes soil / incidence offsets), day of season."""
    if not rad:
        return np.zeros((0, 8))
    vh_min = min(r["vh"] for r in rad)
    cr_min = min(r["cr"] for r in rad)
    out = []
    for r in rad:
        t, s, c = _doy_feats(r["date"], year)
        out.append([r["vh"], r["vv"], r["cr"], r["vh"] - vh_min, r["cr"] - cr_min, t, s, c])
    return np.asarray(out, float)


class SarNdviImputer:
    """NDVI from Sentinel-1 on bins with no clear optical look.

    Wraps a fitted regressor; `resid_sd` is the held-out residual spread,
    reported with every imputed value.
    """

    def __init__(self, model: Any, resid_sd: float, r2: float):
        self.model = model
        self.resid_sd = float(resid_sd)
        self.r2 = float(r2)

    def predict(self, X: np.ndarray) -> np.ndarray:
        if len(X) == 0:
            return np.zeros(0)
        return np.clip(self.model.predict(X), -0.2, 1.0)


@dataclass
class FusedCurve:
    days: List[date]
    ndvi: np.ndarray                 # smoothed daily
    observed_days: List[date]        # optical looks used
    imputed_days: List[date]         # radar-imputed points used
    rad: List[dict]
    opt: List[dict]


def fuse(s1, refl, year: int, imputer: Optional[SarNdviImputer], as_of: Optional[date] = None,
         ) -> FusedCurve:
    end = min(date(year, *SEASON_END), as_of or date(year, *SEASON_END))
    start = date(year, *SEASON_START)
    opt, rad = parse_series(s1, refl, end)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    n = len(days)
    y = np.zeros(n)
    w = np.zeros(n)
    env = np.zeros(n, bool)
    obs_days = set()
    for o in opt:
        k = (o["date"] - start).days
        if 0 <= k < n and o["ndvi"] is not None:
            y[k] = (y[k] * w[k] + o["ndvi"]) / (w[k] + 1.0)
            w[k] += 1.0
            env[k] = True
            obs_days.add(o["date"])
    imp_days = []
    if imputer is not None and rad:
        pred = imputer.predict(sar_rows(rad, year))
        for r, v in zip(rad, pred):
            # Impute only where no optical look sits within 5 days.
            if any(abs((r["date"] - d).days) <= 5 for d in obs_days):
                continue
            k = (r["date"] - start).days
            if 0 <= k < n and w[k] == 0:
                y[k], w[k] = float(v), IMPUTED_WEIGHT
                imp_days.append(r["date"])
    z = whittaker(y, w, env) if n >= 4 else np.full(n, np.nan)
    return FusedCurve(days, z, sorted(obs_days), imp_days, rad, opt)


def _at(days: List[date], arr: np.ndarray, d: date) -> float:
    k = (d - days[0]).days
    if 0 <= k < len(arr) and np.isfinite(arr[k]):
        return float(arr[k])
    return float("nan")


def _interp(records: List[dict], key: str, d: date, max_gap: int = 20) -> float:
    """Linear interpolation of a sparse radar/optical series at d (NaN if the
    nearest looks are more than max_gap days away)."""
    if not records:
        return float("nan")
    before = [r for r in records if r["date"] <= d and r.get(key) is not None]
    after = [r for r in records if r["date"] >= d and r.get(key) is not None]
    if before and after:
        a, b = before[-1], after[0]
        if (b["date"] - a["date"]).days > 2 * max_gap:
            return float("nan")
        if a["date"] == b["date"]:
            return float(a[key])
        f = (d - a["date"]).days / (b["date"] - a["date"]).days
        return float(a[key] + f * (b[key] - a[key]))
    near = before[-1] if before else after[0]
    return float(near[key]) if abs((near["date"] - d).days) <= max_gap else float("nan")


def feature_names() -> List[str]:
    names = []
    for k in range(N_STEPS):
        names += [f"ndvi_f_{k:02d}", f"vh_{k:02d}", f"cr_{k:02d}", f"ndmi_{k:02d}", f"obs_{k:02d}"]
    names += ["ndvi_peak_step", "ndvi_amp", "greenup_step",
              "decline_step", "ndvi_integral", "vh_max", "vh_peak_step", "cr_max", "cr_amp",
              "season_seen_steps", "frac_observed", "frac_imputed", "n_opt", "n_sar"]
    # Green-up-aligned view: the same season read from each field's own
    # green-up. A late-sown soybean on 1 October (2023, late monsoon) looks like
    # cotton on the calendar but not 60 days after its own green-up.
    for k in range(N_ALIGNED):
        names += [f"ndvi_g_{k:02d}", f"vh_g_{k:02d}", f"cr_g_{k:02d}"]
    names += ["days_since_greenup", "rise_30d", "rise_60d", "greenup_to_peak_days",
              "peak_level_g", "cr_rise_30d", "vh_rise_30d"]
    return names


def _greenup_day(days: List[date], z: np.ndarray) -> Optional[int]:
    """Index of the daily fused curve's green-up: first day after the
    pre-peak minimum reaching 20 % of the seasonal amplitude."""
    if len(z) < 20 or not np.isfinite(z).any():
        return None
    zz = np.where(np.isfinite(z), z, np.nan)
    pk = int(np.nanargmax(zz))
    base_i = int(np.nanargmin(zz[: pk + 1])) if pk > 0 else 0
    base, peak = zz[base_i], zz[pk]
    if not np.isfinite(base) or peak - base < 0.15:
        return None
    thr = base + 0.2 * (peak - base)
    for k in range(base_i, pk + 1):
        if np.isfinite(zz[k]) and zz[k] >= thr:
            return k
    return None


def _aligned_feats(cur: "FusedCurve") -> Dict[str, float]:
    f: Dict[str, float] = {}
    gi = _greenup_day(cur.days, cur.ndvi)
    nan = float("nan")
    if gi is None:
        for k in range(N_ALIGNED):
            f[f"ndvi_g_{k:02d}"] = f[f"vh_g_{k:02d}"] = f[f"cr_g_{k:02d}"] = nan
        f.update(days_since_greenup=nan, rise_30d=nan, rise_60d=nan, greenup_to_peak_days=nan,
                 peak_level_g=nan, cr_rise_30d=nan, vh_rise_30d=nan)
        return f
    g0 = cur.days[gi]
    last = cur.days[-1]

    def nd(off):
        k = gi + off
        return float(cur.ndvi[k]) if 0 <= k < len(cur.ndvi) and np.isfinite(cur.ndvi[k]) else nan

    for k in range(N_ALIGNED):
        d = g0 + timedelta(days=GRID_STEP * k)
        seen = d <= last
        f[f"ndvi_g_{k:02d}"] = nd(GRID_STEP * k) if seen else nan
        f[f"vh_g_{k:02d}"] = _interp(cur.rad, "vh", d) if seen else nan
        f[f"cr_g_{k:02d}"] = _interp(cur.rad, "cr", d) if seen else nan
    z = cur.ndvi
    pk = int(np.nanargmax(z))
    f["days_since_greenup"] = float((last - g0).days)
    f["rise_30d"] = nd(30) - nd(0)
    f["rise_60d"] = nd(60) - nd(0)
    f["greenup_to_peak_days"] = float(pk - gi)
    f["peak_level_g"] = float(np.nanmax(z[gi:])) if gi < len(z) else nan
    f["cr_rise_30d"] = _interp(cur.rad, "cr", g0 + timedelta(days=30)) - _interp(cur.rad, "cr", g0)
    f["vh_rise_30d"] = _interp(cur.rad, "vh", g0 + timedelta(days=30)) - _interp(cur.rad, "vh", g0)
    return f


def build(s1, refl, year: int, imputer: Optional[SarNdviImputer], as_of: Optional[date] = None
          ) -> Tuple[Dict[str, float], FusedCurve]:
    """Feature dict on the fixed season calendar (NaN after `as_of`)."""
    cur = fuse(s1, refl, year, imputer, as_of)
    g = grid_dates(year)
    last = cur.days[-1] if cur.days else date(year, *SEASON_START)
    f: Dict[str, float] = {}
    ndvi_g, vh_g, cr_g = [], [], []
    for k, d in enumerate(g):
        seen = d <= last
        v = _at(cur.days, cur.ndvi, d) if seen else float("nan")
        vh = _interp(cur.rad, "vh", d) if seen else float("nan")
        cr = _interp(cur.rad, "cr", d) if seen else float("nan")
        nm = _interp([o for o in cur.opt if o["ndmi"] is not None], "ndmi", d, 8) if seen else float("nan")
        near = any(abs((x - d).days) <= 5 for x in cur.observed_days)
        f[f"ndvi_f_{k:02d}"], f[f"vh_{k:02d}"], f[f"cr_{k:02d}"] = v, vh, cr
        f[f"ndmi_{k:02d}"] = nm
        f[f"obs_{k:02d}"] = (1.0 if near else 0.0) if seen else float("nan")
        ndvi_g.append(v)
        vh_g.append(vh)
        cr_g.append(cr)
    a = np.array(ndvi_g, float)
    ok = np.isfinite(a)
    if ok.any():
        pk = int(np.nanargmax(a))
        pre = a[: max(pk, 1)]
        base = float(np.nanmin(pre)) if np.isfinite(pre).any() else float(np.nanmin(a))
        amp = float(np.nanmax(a) - base)
        thr_up, thr_dn = base + 0.2 * amp, base + 0.5 * amp
        gu = next((k for k in range(pk + 1) if np.isfinite(a[k]) and a[k] >= thr_up), np.nan)
        dn = next((k for k in range(pk + 1, len(a)) if np.isfinite(a[k]) and a[k] <= thr_dn), np.nan)
        f.update(ndvi_max=float(np.nanmax(a)), ndvi_peak_step=float(pk), ndvi_min_pre=base,
                 ndvi_amp=amp, greenup_step=float(gu), decline_step=float(dn),
                 ndvi_integral=float(np.nansum(np.clip(a - base, 0, None))))
    else:
        f.update({k: float("nan") for k in ("ndvi_max", "ndvi_peak_step", "ndvi_min_pre", "ndvi_amp",
                                            "greenup_step", "decline_step", "ndvi_integral")})
    v = np.array(vh_g, float)
    c = np.array(cr_g, float)
    f["vh_max"] = float(np.nanmax(v)) if np.isfinite(v).any() else float("nan")
    f["vh_peak_step"] = float(np.nanargmax(v)) if np.isfinite(v).any() else float("nan")
    f["cr_max"] = float(np.nanmax(c)) if np.isfinite(c).any() else float("nan")
    f["cr_amp"] = float(np.nanmax(c) - np.nanmin(c)) if np.isfinite(c).sum() > 1 else float("nan")
    seen = sum(1 for d in g if d <= last)
    f["season_seen_steps"] = float(seen)
    n_obs = sum(1 for k in range(seen) if f[f"obs_{k:02d}"] == 1.0)
    f["frac_observed"] = n_obs / seen if seen else 0.0
    f["frac_imputed"] = len(cur.imputed_days) / max(len(cur.imputed_days) + len(cur.observed_days), 1)
    f["n_opt"] = float(len(cur.observed_days))
    f["n_sar"] = float(len(cur.rad))
    f.update(_aligned_feats(cur))
    return f, cur


def _floor(x: np.ndarray, q: float) -> float:
    head = x[:FLOOR_STEPS]
    head = head[np.isfinite(head)]
    if head.size:
        return float(np.percentile(head, q))
    x = x[np.isfinite(x)]
    return float(np.percentile(x, q)) if x.size else float("nan")


def _to_relative(f: Dict[str, float], ndvi: np.ndarray, vh: np.ndarray, cr: np.ndarray) -> None:
    """Rewrite levels as offsets from the field's pre-season floor, in place.
    `ndvi_max` and `ndvi_min_pre` stay absolute in the dict (fallow test,
    peak date) but are not model inputs."""
    nfl = f.get("ndvi_min_pre", float("nan"))
    vfl, cfl = _floor(vh, 20), _floor(cr, 20)
    nm = np.array([f.get(f"ndmi_{k:02d}", np.nan) for k in range(N_STEPS)], float)
    mfl = _floor(nm, 20)
    for k in range(N_STEPS):
        f[f"ndvi_f_{k:02d}"] -= nfl
        f[f"vh_{k:02d}"] -= vfl
        f[f"cr_{k:02d}"] -= cfl
        f[f"ndmi_{k:02d}"] -= mfl
    for k in range(N_ALIGNED):
        f[f"ndvi_g_{k:02d}"] -= nfl
        f[f"vh_g_{k:02d}"] -= vfl
        f[f"cr_g_{k:02d}"] -= cfl
    f["vh_max"] -= vfl
    f["cr_max"] -= cfl
    f["peak_level_g"] -= nfl


def has_any_data(feats: Mapping[str, float]) -> bool:
    return (feats.get("n_opt") or 0) + (feats.get("n_sar") or 0) > 0


def peak_date(feats: Mapping[str, float], year: int) -> Optional[date]:
    pk = feats.get("ndvi_peak_step")
    if pk is None or not np.isfinite(pk):
        return None
    return grid_dates(year)[int(pk)]
