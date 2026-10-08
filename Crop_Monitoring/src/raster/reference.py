"""Crop reference NDVI curves by days after sowing, and curve fitting.

A field's smoothed curve is fitted to each crop's reference by shifting it in
time (the shift is the sowing date) and stretching it (season length), with a
linear base/amplitude scale solved in closed form (Sakamoto et al. 2005,
shape-model fitting). That gives:

  * a sowing estimate from the whole curve, not one threshold crossing;
  * a crop check: when another crop's reference fits clearly better, the field
    is flagged `phenology_disagrees_with_class` instead of being kept or
    silently dropped (plan §5.4). Soybean labelled cotton is caught once the
    season passes the point where soybean would have senesced.

PARAMETRIC DEFAULTS. Until reference curves are built from the labelled 2023
Marathwada parcels (`build_reference`), each crop uses a double logistic
(Beck et al. 2006) with timings from agronomic durations for rainfed Deccan
kharif crops. They are marked `source = "parametric_default"` in every output.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

REFERENCE_FILE = Path(__file__).resolve().parents[2] / "reference" / "crop_reference_curves.json"
# Smallest RMSE improvement (NDVI units) that counts as a better fit.
MIN_RMSE_GAIN = 0.015
# Share of the green-up phase that must be near a real look for the fitted
# sowing to be trusted at full weight.
RISE_SUPPORT_MIN = 0.30


@dataclass(frozen=True)
class CropCurve:
    crop: str
    t_rise: float      # DAS at half of the green-up
    k_rise: float      # steepness of the rise (1/day)
    t_fall: float      # DAS at half of the senescence
    k_fall: float
    base: float = 0.18
    peak: float = 0.78
    source: str = "parametric_default"
    perennial: bool = False
    n_fields: int = 0

    def value(self, das: np.ndarray) -> np.ndarray:
        das = np.asarray(das, float)
        if self.perennial:
            rise = 1.0 / (1.0 + np.exp(-self.k_rise * (das - self.t_rise)))
            return self.base + (self.peak - self.base) * rise
        rise = 1.0 / (1.0 + np.exp(-self.k_rise * (das - self.t_rise)))
        fall = 1.0 / (1.0 + np.exp(-self.k_fall * (das - self.t_fall)))
        return self.base + (self.peak - self.base) * (rise - fall)

    @property
    def peak_das(self) -> float:
        das = np.arange(0, 400)
        return float(das[int(np.argmax(self.value(das)))])

    @property
    def season_days(self) -> float:
        return self.t_fall + 3.0 / max(self.k_fall, 1e-3)


# Rainfed Deccan kharif timings (days after sowing). Cotton: squaring ~35 DAS,
# boll development into Oct-Nov, picking from ~140 DAS. Soybean: 90-110 day
# varieties, pod fill ~60-85 DAS. Tur: long duration, flowering ~110-130 DAS.
DEFAULT_CURVES: dict[str, CropCurve] = {
    "Cotton": CropCurve("Cotton", t_rise=48, k_rise=0.09, t_fall=175, k_fall=0.06, peak=0.76),
    "Soyabean": CropCurve("Soyabean", t_rise=27, k_rise=0.16, t_fall=92, k_fall=0.14, peak=0.82),
    "Tur": CropCurve("Tur", t_rise=55, k_rise=0.07, t_fall=170, k_fall=0.07, peak=0.72),
    "Maize": CropCurve("Maize", t_rise=28, k_rise=0.15, t_fall=95, k_fall=0.11, peak=0.80),
    "Jowar": CropCurve("Jowar", t_rise=30, k_rise=0.13, t_fall=100, k_fall=0.10, peak=0.74),
    "Bajra": CropCurve("Bajra", t_rise=24, k_rise=0.16, t_fall=78, k_fall=0.13, peak=0.68),
    "Groundnut": CropCurve("Groundnut", t_rise=30, k_rise=0.12, t_fall=105, k_fall=0.10, peak=0.74),
    "Sugarcane": CropCurve("Sugarcane", t_rise=70, k_rise=0.05, t_fall=9999, k_fall=0.01,
                           peak=0.80, perennial=True),
}


def load_curves(path: Path = REFERENCE_FILE) -> dict[str, CropCurve]:
    """Curves built from labelled data override the parametric defaults."""
    curves = dict(DEFAULT_CURVES)
    if path.exists():
        for row in json.loads(path.read_text(encoding="utf-8")).get("curves", []):
            curves[row["crop"]] = CropCurve(**row)
    return curves


def save_curves(curves: dict[str, CropCurve], path: Path = REFERENCE_FILE, meta: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"meta": meta or {}, "curves": [asdict(c) for c in curves.values()]},
                               indent=1), encoding="utf-8")


@dataclass
class Fit:
    crop: str
    sowing: date
    stretch: float
    rmse: float
    scale: float        # fitted amplitude multiplier
    offset: float
    n_days: int
    source: str
    # Share of the green-up phase (t_rise -20..+10 DAS) close to a real look.
    # Inside a monsoon cloud gap the rise is unseen, the fit trades sowing date
    # against stretch, and the sowing it implies deserves a wide sigma.
    rise_support: float = 0.0

    @property
    def rise_observed(self) -> bool:
        return self.rise_support >= RISE_SUPPORT_MIN


def fit_curve(
    days: list[date], z: np.ndarray, support: np.ndarray, curve: CropCurve,
    sow_from: date, sow_to: date, stretches=(0.85, 0.92, 1.0, 1.08, 1.15), step: int = 2,
) -> Fit | None:
    """Best time shift / stretch of `curve` to one field curve z (D,).

    `support` (D,) weights days near a real clear observation; interpolated
    stretches between distant looks carry little weight.
    """
    ok = np.isfinite(z) & (support > 0)
    if ok.sum() < 20:
        return None
    k0 = days[0]
    dk = np.array([(d - k0).days for d in days], float)[ok]
    zo = z[ok].astype(float)
    wo = support[ok].astype(float)
    shifts = np.arange((sow_from - k0).days, (sow_to - k0).days + 1, step, dtype=float)
    if not len(shifts):
        return None
    st_arr = np.asarray(stretches, float)
    # All (shift, stretch) combinations at once: das (C, D).
    S, T = np.meshgrid(shifts, st_arr, indexing="ij")
    das = (dk[None, :] - S.reshape(-1, 1))
    r = curve.value(das / T.reshape(-1, 1))
    r = np.where(das < 0, curve.base, r)
    sw_ = wo.sum()
    rm = (r * wo).sum(1) / sw_
    zm = (zo * wo).sum() / sw_
    rc = r - rm[:, None]
    var = (wo * rc ** 2).sum(1)
    b = np.where(var > 1e-9, (wo * rc * (zo - zm)).sum(1) / np.maximum(var, 1e-12), 0.0)
    b = np.maximum(b, 0.0)
    a = zm - b * rm
    resid = zo[None, :] - (a[:, None] + b[:, None] * r)
    rmse = np.sqrt((wo * resid ** 2).sum(1) / sw_)
    # A reference scaled near zero fits anything flat; require a real rise.
    rmse = np.where((b < 0.4) | (b > 1.6), np.inf, rmse)
    i = int(np.argmin(rmse))
    if not np.isfinite(rmse[i]):
        return None
    best = Fit(curve.crop, k0 + timedelta(days=int(S.reshape(-1)[i])), float(T.reshape(-1)[i]),
               float(rmse[i]), float(b[i]), float(a[i]), int(ok.sum()), curve.source)
    dk = np.array([(d - k0).days for d in days], float)
    if best is not None and not curve.perennial:
        das = (dk - (best.sowing - k0).days) / best.stretch
        rise = (das >= curve.t_rise - 20) & (das <= curve.t_rise + 10)
        best.rise_support = float((support[rise] > 0).mean()) if rise.any() else 0.0
    return best


def crop_check(
    days: list[date], z: np.ndarray, support: np.ndarray, claimed: str,
    curves: dict[str, CropCurve], sow_from: date, sow_to: date, as_of: date,
    margin: float = 0.7,
) -> dict:
    """Compare the claimed crop's fit with every other reference.

    A disagreement is only called once the season has run past the claimed
    crop's distinguishing point: e.g. cotton vs soybean is undecidable at 50
    days after sowing (both are rising) but clear at 110 (soybean has senesced,
    cotton has not).
    """
    fits = {}
    for name, curve in curves.items():
        f = fit_curve(days, z, support, curve, sow_from, sow_to)
        if f is not None:
            fits[name] = f
    if claimed not in fits:
        return {"checked": False, "reason": "claimed crop has no reference fit", "fits": _fit_rows(fits)}
    mine = fits[claimed]
    others = {k: v for k, v in fits.items() if k != claimed}
    if not others:
        return {"checked": False, "reason": "no alternative reference", "fits": _fit_rows(fits)}
    alt_name, alt = min(others.items(), key=lambda kv: kv[1].rmse)
    elapsed = (as_of - mine.sowing).days
    decisive = min(curves[claimed].peak_das, curves[alt_name].peak_das) + 25
    out = {
        "checked": elapsed >= decisive,
        "claimed": claimed,
        "claimed_rmse": round(mine.rmse, 4),
        "best_alternative": alt_name,
        "alternative_rmse": round(alt.rmse, 4),
        "ratio": round(alt.rmse / mine.rmse, 3) if mine.rmse > 0 else None,
        "days_since_sowing": elapsed,
        "decisive_after_das": int(decisive),
        "fits": _fit_rows(fits),
    }
    # Better by a ratio AND by more than the noise floor of a smoothed 10 m curve.
    out["disagrees"] = bool(out["checked"] and alt.rmse < margin * mine.rmse
                            and (mine.rmse - alt.rmse) > MIN_RMSE_GAIN)
    # Short-season crops (soybean, groundnut, maize, jowar...) have similar
    # curves; report every crop that fits about as well as the best, not one name.
    out["candidates"] = [k for k, f in sorted(fits.items(), key=lambda kv: kv[1].rmse)
                         if f.rmse <= alt.rmse * 1.35 and k != claimed]
    if not out["checked"]:
        out["reason"] = f"too early to separate {claimed} from {alt_name} ({elapsed} < {int(decisive)} days)"
    return out


# Crop groups that 10 m NDVI curves CAN separate in season. Inside a group the
# curves are near-identical (soybean vs bajra vs groundnut; cotton vs tur until
# tur flowers in Nov-Dec), so in season the group is the honest answer and the
# crop name waits for the post-harvest classification.
#
# Newly planted sugarcane sits in the long-season group: a field seen bare
# before the season cannot be ratoon cane, and a fresh planting rises to a
# plateau exactly like cotton and tur until cotton declines in December.
# (Ratoon / standing cane never passes the bare-soil check and is reported as
# green_at_start, not through this function.)
GROUPS = {
    "short_season_kharif": ("Soyabean", "Bajra", "Maize", "Jowar", "Groundnut"),
    "long_season_kharif": ("Cotton", "Tur", "Sugarcane"),
}
GROUP_OF = {crop: g for g, crops in GROUPS.items() for crop in crops}
GROUP_MARGIN = 0.75          # best group RMSE must be < 0.75 x the next group's


def crop_group(days: list[date], z: np.ndarray, support: np.ndarray, curves: dict[str, CropCurve],
               sow_from: date, sow_to: date, as_of: date) -> dict:
    """Which crop group's references fit this field's curve best, and is it decisive yet?

    Decisive once the season has run past the point where short-season crops
    senesce (their peak + 25 days after the fitted sowing) -- before that a
    rising curve fits every group.
    """
    fits = {}
    for name, curve in curves.items():
        if name not in GROUP_OF:
            continue
        f = fit_curve(days, z, support, curve, sow_from, sow_to)
        if f is not None:
            fits[name] = f
    if not fits:
        return {"group": None, "status": "no_fit"}
    by_group: dict[str, Fit] = {}
    for name, f in fits.items():
        g = GROUP_OF[name]
        if g not in by_group or f.rmse < by_group[g].rmse:
            by_group[g] = f
    ranked = sorted(by_group.items(), key=lambda kv: kv[1].rmse)
    best_g, best = ranked[0]
    second = ranked[1][1].rmse if len(ranked) > 1 else np.inf
    short_peak = max(curves[c].peak_das for c in GROUPS["short_season_kharif"] if c in curves)
    elapsed = (as_of - best.sowing).days
    decisive_after = int(short_peak + 25)
    # Separated by a ratio AND by more than the fit noise: the gain must exceed
    # half the best RMSE (real 10 m curves fit at 0.02-0.04) with a small floor.
    separated = best.rmse < GROUP_MARGIN * second and (second - best.rmse) > max(0.008, 0.5 * best.rmse)
    status = ("decided" if elapsed >= decisive_after and separated
              else "too_early" if elapsed < decisive_after else "ambiguous")
    return {
        "group": best_g if status == "decided" else None,
        "best_group": best_g,
        "status": status,
        # Crops of the winning group that fit about as well as its best member.
        "candidates": [c for c in sorted(GROUPS[best_g], key=lambda c: fits[c].rmse if c in fits else 9)
                       if c in fits and fits[c].rmse <= best.rmse * 1.35 + 0.005],
        "best_rmse": round(best.rmse, 4),
        "next_group_rmse": None if not np.isfinite(second) else round(second, 4),
        "days_since_sowing": elapsed,
        "decisive_after_das": decisive_after,
        "sowing": best.sowing.isoformat(),
        "best_crop_curve": best.crop,
    }


# Printed only when that crop's own curve is among the group's good fits.
# Tur, sugarcane, groundnut and the rest stay unnamed: their curves are too
# close to cotton or soybean to justify a different name in October.
_GROUP_CROP = {"long_season_kharif": "Cotton", "short_season_kharif": "Soyabean"}


def local_kharif_name(group: dict | None) -> dict | None:
    """Cotton or Soyabean from a decided reference-curve group.

    Returns None when the season is still too early, the two groups fit about
    equally, or the named crop's curve is a poor fit inside the winning group.
    Confidence stays below the classifier's confirmed range: this is a shape
    check, not a calibrated class probability.
    """
    if not group or group.get("status") != "decided":
        return None
    crop = _GROUP_CROP.get(group.get("group") or "")
    if not crop or crop not in (group.get("candidates") or []):
        return None
    best = float(group.get("best_rmse") or 0.05)
    other = group.get("next_group_rmse")
    separated = 0.0 if not other else max(0.0, 1.0 - best / float(other))
    confidence = round(min(0.72, 0.45 + 0.40 * separated), 3)
    kind = "long-season kharif" if crop == "Cotton" else "short-season kharif"
    return {
        "crop": crop,
        "confidence": confidence,
        "basis": "reference_curve",
        "note": (
            f"Named {crop} because the canopy matches the {kind} reference "
            f"(RMSE {best:.3f}). The classifier did not print this crop."
        ),
    }


def looks_to_daily(
    dates: list[date], values: list[float], max_gap_days: int = 20,
) -> tuple[list[date], np.ndarray, np.ndarray] | None:
    """Daily NDVI from real looks. Gaps longer than `max_gap_days` stay empty."""
    pairs = [(d, float(v)) for d, v in zip(dates, values) if d is not None and np.isfinite(v)]
    pairs.sort(key=lambda item: item[0])
    if len(pairs) < 6:
        return None
    d0, d1 = pairs[0][0], pairs[-1][0]
    n = (d1 - d0).days + 1
    if n < 20 or n > 400:
        return None
    z = np.full(n, np.nan, np.float64)
    sup = np.zeros(n, np.float32)
    for d, v in pairs:
        z[(d - d0).days] = v
        sup[(d - d0).days] = 1.0
    i = 0
    while i < n:
        if np.isfinite(z[i]):
            i += 1
            continue
        j = i
        while j < n and not np.isfinite(z[j]):
            j += 1
        if i > 0 and j < n and (j - i) <= max_gap_days:
            left, right = float(z[i - 1]), float(z[j])
            span = j - (i - 1)
            for t in range(i, j):
                w = (t - (i - 1)) / span
                z[t] = (1.0 - w) * left + w * right
                sup[t] = 0.35
        i = j if j > i else i + 1
    days = [d0 + timedelta(days=k) for k in range(n)]
    return days, z, sup


def name_from_looks(
    dates: list[date], values: list[float], as_of: date,
    sow_from: date | None = None, sow_to: date | None = None,
    curves: dict[str, CropCurve] | None = None,
) -> dict | None:
    """Name Cotton or Soyabean from a field's own NDVI looks, or return None."""
    packed = looks_to_daily(dates, values)
    if packed is None:
        return None
    days, z, sup = packed
    y = as_of.year
    group = crop_group(
        days, z, sup, curves or load_curves(),
        sow_from or date(y, 6, 1), sow_to or date(y, 8, 15), as_of,
    )
    return local_kharif_name(group)


def _fit_rows(fits: dict[str, Fit]) -> list[dict]:
    return [{"crop": k, "rmse": round(f.rmse, 4), "sowing": f.sowing.isoformat(),
             "stretch": f.stretch, "source": f.source} for k, f in sorted(fits.items(), key=lambda kv: kv[1].rmse)]


def build_reference(series: list[tuple[str, list[date], np.ndarray, date]],
                    min_fields: int = 30) -> dict[str, CropCurve]:
    """Fit a double logistic per crop to labelled fields aligned on their sowing.

    `series` items: (crop, daily axis, smoothed NDVI (D,), sowing date[, stretch]). The
    sowing date for training labels comes from the curve fit to the default
    reference; the rebuilt curve then replaces the default. Crops with fewer
    than `min_fields` fields keep the default.
    """
    from scipy.optimize import least_squares

    out = dict(DEFAULT_CURVES)
    by_crop: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    for item in series:
        crop, axis, z, sow = item[:4]
        stretch = item[4] if len(item) > 4 else 1.0
        # Align on DAS / stretch: the fitter applies a stretch to the reference,
        # so the stored reference must be the unstretched shape. Aligning on
        # DAS alone blended stretched curves and moved soybean's peak ~10 days.
        das = np.array([(d - sow).days for d in axis], float) / stretch
        ok = np.isfinite(z) & (das >= -30) & (das <= 260)
        by_crop.setdefault(crop, []).append((das[ok], z[ok]))
    for crop, rows in by_crop.items():
        if len(rows) < min_fields or crop not in DEFAULT_CURVES:
            continue
        das = np.concatenate([r[0] for r in rows])
        val = np.concatenate([r[1] for r in rows])
        bins = np.arange(-30, 261, 5)
        idx = np.digitize(das, bins)
        med_x = np.array([das[idx == i].mean() for i in np.unique(idx) if (idx == i).sum() >= 10])
        med_y = np.array([np.median(val[idx == i]) for i in np.unique(idx) if (idx == i).sum() >= 10])
        d0 = DEFAULT_CURVES[crop]
        p0 = [d0.t_rise, d0.k_rise, d0.t_fall, d0.k_fall, d0.base, d0.peak]

        def resid(p):
            c = CropCurve(crop, *p)
            return c.value(med_x) - med_y

        res = least_squares(resid, p0, bounds=([0, 0.01, 40, 0.005, 0.0, 0.3],
                                               [150, 0.5, 400, 0.5, 0.5, 1.0]))
        t_rise, k_rise, t_fall, k_fall, base, peak = res.x
        out[crop] = CropCurve(crop, float(t_rise), float(k_rise), float(t_fall), float(k_fall),
                              float(base), float(peak), source="labelled_mh2023", n_fields=len(rows),
                              perennial=d0.perennial)
    return out
