"""Stress against comparable fields, not against a farm's own pixels.

The point engine compared each farm with the top quarter of its own 1-4
points, so a uniformly stressed farm read Healthy and an edge point on a bund
read "Tissue Damage". Here (plan §5.5):

  * Reference = same crop, aligned on days after sowing (DAS). Fields sown
    within COHORT_DAYS of the field form its cohort; with fewer than
    MIN_COHORT_FIELDS such fields every same-crop field in the village is used,
    and with fewer than that the crop reference curve is used (flagged).
  * Field anomaly z = (field value - cohort median at the field's DAS) / robust
    spread (1.4826 MAD, floored), per index.
  * Pixel anomaly maps use the same reference, for within-field maps.
  * Only real clear observation dates are scored. A call is `confirmed` when
    it holds on two consecutive clear looks (or optical and radar agree).
  * Type priority: waterlogging > canopy damage > water > nutrient > sub-optimal.
    Pests and diseases are never named: 10 m multispectral data cannot tell
    them apart, and the output says "cause unconfirmed".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np

COHORT_DAYS = 14
MIN_COHORT_FIELDS = 8
MIN_REFERENCE_FIELDS = 5
SPREAD_FLOOR = {"ndvi": 0.04, "ndre": 0.03, "ndmi": 0.04}

Z_MILD, Z_MODERATE, Z_SEVERE = -1.0, -1.5, -2.0
ESTABLISHING_DAS = 25           # low canopy before this is expected, not stress
WATERLOG_SHARE = 0.20           # interior pixels with MNDWI > 0
WATERLOG_RAIN_MM = 50.0         # over the 3 days before the look
WATER_VH_DB, WATER_VV_DB = -22.0, -16.0
DAMAGE_DROP = 0.15              # NDVI fall between consecutive clear looks
DAMAGE_PATCHY_STD = 0.08
SOIL_WATER_DEFICIT = 0.40       # village bucket fraction of available water


def classify_z(z: float) -> str:
    if not np.isfinite(z) or z >= Z_MILD:
        return "healthy"
    if z >= Z_MODERATE:
        return "mild"
    if z >= Z_SEVERE:
        return "moderate"
    return "severe"


@dataclass
class FieldCurve:
    field_id: str
    crop: str
    sowing: Optional[date]
    days: list[date]
    values: dict[str, np.ndarray]     # index -> smoothed daily (D,)
    sowing_sigma: float = 0.0         # days; (P90 - P10) / 2.56 of the sowing posterior

    def at_das(self, index: str, das: int) -> float:
        if self.sowing is None:
            return np.nan
        d = self.sowing + timedelta(days=das)
        k = (d - self.days[0]).days
        arr = self.values.get(index)
        if arr is None or k < 0 or k >= len(arr):
            return np.nan
        return float(arr[k])


@dataclass
class Reference:
    source: str                # cohort | crop_village | crop_curve
    n_fields: int
    median: dict[str, np.ndarray] = field(default_factory=dict)   # index -> by DAS
    spread: dict[str, np.ndarray] = field(default_factory=dict)

    def z(self, index: str, das: int, value: float, sowing_sigma: float = 0.0) -> float:
        m = self.median.get(index)
        s = self.spread.get(index)
        if m is None or das < 0 or das >= len(m) or not np.isfinite(m[das]):
            return np.nan
        return float((value - m[das]) / self.effective_spread(index, das, sowing_sigma))

    def effective_spread(self, index: str, das: int, sowing_sigma: float = 0.0) -> float:
        """Cohort spread widened by the field's own sowing uncertainty.

        A sowing date off by a few days shifts the field along the reference
        curve; during fast growth that alone looks like an anomaly. The slope of
        the reference times the sowing sigma is added in quadrature.
        """
        m, s = self.median[index], self.spread[index]
        lo, hi = max(das - 3, 0), min(das + 3, len(m) - 1)
        slope = (m[hi] - m[lo]) / max(hi - lo, 1) if np.isfinite(m[hi]) and np.isfinite(m[lo]) else 0.0
        return float(np.hypot(s[das], slope * sowing_sigma))


MAX_DAS = 300


def aligned(c: FieldCurve, index: str) -> np.ndarray:
    """The field's smoothed curve indexed by days after sowing (MAX_DAS,)."""
    out = np.full(MAX_DAS, np.nan, np.float32)
    arr = c.values.get(index)
    if arr is None or c.sowing is None:
        return out
    k0 = (c.sowing - c.days[0]).days
    lo, hi = max(k0, 0), min(k0 + MAX_DAS, len(arr))
    if hi > lo:
        out[lo - k0:hi - k0] = arr[lo:hi]
    return out


def _reference_from(rows: dict[str, np.ndarray], n_fields: int, source: str) -> Reference:
    import warnings

    ref = Reference(source, n_fields)
    for idx, mat in rows.items():
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)     # all-NaN DAS columns
            n = np.isfinite(mat).sum(axis=0)
            med = np.nanmedian(mat, axis=0)
            mad = np.nanmedian(np.abs(mat - med[None, :]), axis=0) * 1.4826
        med[n < MIN_REFERENCE_FIELDS] = np.nan
        ref.median[idx] = med.astype(np.float32)
        ref.spread[idx] = np.maximum(np.nan_to_num(mad, nan=1.0),
                                     SPREAD_FLOOR.get(idx, 0.04)).astype(np.float32)
    return ref


def build_reference(curves: list[FieldCurve], indices=("ndvi", "ndre", "ndmi"),
                    fallback_curve=None) -> Reference:
    """Median and robust spread by DAS across fields with a sowing date."""
    usable = [c for c in curves if c.sowing is not None]
    if len(usable) < MIN_REFERENCE_FIELDS:
        if fallback_curve is None:
            return Reference("none", len(usable))
        das = np.arange(MAX_DAS)
        med = {"ndvi": fallback_curve.value(das).astype(np.float32)}
        spr = {"ndvi": np.full(MAX_DAS, 0.10, np.float32)}
        return Reference("crop_curve", len(usable), med, spr)
    rows = {idx: np.stack([aligned(c, idx) for c in usable]) for idx in indices}
    return _reference_from(rows, len(usable), "cohort")


def reference_for(target: FieldCurve, curves: list[FieldCurve], fallback_curve=None) -> Reference:
    """Cohort (same crop, sown within COHORT_DAYS), else same crop village-wide."""
    same = [c for c in curves if c.crop == target.crop and c.field_id != target.field_id and c.sowing]
    if target.sowing is not None:
        near = [c for c in same if abs((c.sowing - target.sowing).days) <= COHORT_DAYS]
        if len(near) >= MIN_COHORT_FIELDS:
            return build_reference(near)
    ref = build_reference(same, fallback_curve=fallback_curve)
    if ref.source == "cohort":
        ref.source = "crop_village"
    return ref


class ReferenceBank:
    """References for a whole village, built once.

    Every field's curve is aligned on DAS one time; a reference is computed per
    (crop, sowing date) and shared by every field sown that day. The target
    field is included in its own cohort (no leave-one-out): with the cohort
    floor of MIN_COHORT_FIELDS the self-influence on a median is small, and
    it keeps a village of thousands of fields to seconds.
    """

    def __init__(self, curves: list[FieldCurve], indices=("ndvi", "ndre", "ndmi"),
                 fallbacks: Optional[dict] = None):
        self.indices = indices
        self.fallbacks = fallbacks or {}
        self.by_crop: dict[str, tuple[list[FieldCurve], np.ndarray, dict[str, np.ndarray]]] = {}
        for crop in sorted({c.crop for c in curves}):
            members = [c for c in curves if c.crop == crop and c.sowing is not None]
            sow = np.array([c.sowing.toordinal() for c in members], int)
            rows = {idx: (np.stack([aligned(c, idx) for c in members]) if members
                          else np.zeros((0, MAX_DAS), np.float32)) for idx in indices}
            self.by_crop[crop] = (members, sow, rows)
        self._cache: dict[tuple, Reference] = {}

    def get(self, target: FieldCurve) -> Reference:
        key = (target.crop, target.sowing)
        if key in self._cache:
            return self._cache[key]
        members, sow, rows = self.by_crop.get(target.crop, ([], np.zeros(0, int), {}))
        ref = None
        if target.sowing is not None and len(members):
            near = np.abs(sow - target.sowing.toordinal()) <= COHORT_DAYS
            if near.sum() >= MIN_COHORT_FIELDS:
                ref = _reference_from({k: v[near] for k, v in rows.items()}, int(near.sum()), "cohort")
        if ref is None:
            if len(members) >= MIN_REFERENCE_FIELDS:
                ref = _reference_from(rows, len(members), "crop_village")
            else:
                ref = build_reference([], fallback_curve=self.fallbacks.get(target.crop))
        self._cache[key] = ref
        return ref


@dataclass
class Look:
    """One clear optical observation of one field."""
    date: date
    ndvi: float
    ndre: float
    ndmi: float
    pixel_ndvi: np.ndarray            # interior pixels on this date (may hold NaN)
    mndwi_share: float = 0.0          # interior pixels with MNDWI > 0
    rain_3d_mm: float = 0.0
    soil_water: Optional[float] = None
    radar_water: bool = False


def score_looks(target: FieldCurve, looks: list[Look], ref: Reference,
                expected_senescence_das: Optional[int] = None) -> list[dict]:
    """Stress record per clear look, in date order, with persistence applied."""
    out: list[dict] = []
    prev: Optional[Look] = None
    for lk in sorted(looks, key=lambda x: x.date):
        rec: dict = {"date": lk.date.isoformat()}
        das = (lk.date - target.sowing).days if target.sowing else None
        rec["das"] = das
        if das is None or das < 0:
            rec.update({"type": None, "class": "pre_sowing", "z": None, "confirmed": False})
            out.append(rec)
            prev = lk
            continue
        z = {k: ref.z(k, das, getattr(lk, k), target.sowing_sigma) for k in ("ndvi", "ndre", "ndmi")}
        rec["z"] = {k: (round(v, 2) if np.isfinite(v) else None) for k, v in z.items()}
        zn = z["ndvi"]
        px = lk.pixel_ndvi[np.isfinite(lk.pixel_ndvi)]
        if np.isfinite(zn) and ref.median.get("ndvi") is not None and len(px):
            m = ref.median["ndvi"][das] if das < MAX_DAS else np.nan
            s = ref.effective_spread("ndvi", das, target.sowing_sigma) if das < MAX_DAS else np.nan
            rec["stressed_fraction"] = round(float(np.mean((px - m) / s < Z_MILD)), 3) if np.isfinite(m) else None
        else:
            rec["stressed_fraction"] = None

        drop = (prev.ndvi - lk.ndvi) if prev is not None and np.isfinite(prev.ndvi) else 0.0
        patchy = False
        if prev is not None and len(px) >= 8 and len(prev.pixel_ndvi) == len(lk.pixel_ndvi):
            d = prev.pixel_ndvi - lk.pixel_ndvi
            d = d[np.isfinite(d)]
            patchy = len(d) >= 8 and float(np.std(d)) >= DAMAGE_PATCHY_STD
        senescing = expected_senescence_das is not None and das >= expected_senescence_das

        stype = None
        if das < ESTABLISHING_DAS:
            cls = "establishing"
        elif lk.mndwi_share >= WATERLOG_SHARE or (lk.radar_water and lk.rain_3d_mm >= WATERLOG_RAIN_MM):
            cls, stype = "severe", "Waterlogging"
        elif drop >= DAMAGE_DROP and patchy and not senescing:
            cls, stype = classify_z(min(zn, Z_MODERATE) if np.isfinite(zn) else Z_MODERATE), \
                "Canopy damage (cause unconfirmed)"
        else:
            cls = classify_z(zn)
            if cls != "healthy":
                zm, zr = z["ndmi"], z["ndre"]
                dry = lk.soil_water is not None and lk.soil_water < SOIL_WATER_DEFICIT
                if np.isfinite(zm) and zm < Z_MILD and (not np.isfinite(zr) or zm < zr - 0.5) and dry:
                    stype = "Water stress"
                elif np.isfinite(zr) and zr < Z_MILD and (not np.isfinite(zm) or zr <= zm) and zn > Z_MODERATE:
                    stype = "Nutrient deficiency"
                else:
                    stype = "Sub-optimal growth"
        if senescing and stype in ("Sub-optimal growth", "Nutrient deficiency"):
            # A falling canopy at the end of the season is expected, not a deficit.
            cls, stype = "healthy", None
        rec["class"] = cls
        rec["type"] = stype
        rec["reference"] = ref.source
        out.append(rec)
        prev = lk

    # Persistence: a stress call stands when the previous scored look agreed.
    last_bad = False
    for rec in out:
        bad = rec.get("class") in ("mild", "moderate", "severe")
        rec["confirmed"] = bool(bad and (last_bad or rec.get("type") == "Waterlogging"))
        if rec.get("class") not in ("pre_sowing", "establishing"):
            last_bad = bad
    return out


def summarise(records: list[dict]) -> dict:
    """Field-level stress summary over the season so far."""
    scored = [r for r in records if r.get("class") in ("healthy", "mild", "moderate", "severe")]
    if not scored:
        return {"status": "condition_unavailable", "latest": None}
    latest = scored[-1]
    stressed = [r for r in scored if r["class"] != "healthy"]
    sev = 0.0
    for r in stressed:
        zn = (r.get("z") or {}).get("ndvi")
        if zn is not None:
            sev += -zn * (r.get("stressed_fraction") or 1.0)
    return {
        "status": "scored",
        "latest_date": latest["date"],
        "latest_class": latest["class"],
        "latest_type": latest.get("type"),
        "latest_confirmed": latest.get("confirmed", False),
        "looks_scored": len(scored),
        "looks_stressed": len(stressed),
        "confirmed_looks": sum(1 for r in stressed if r.get("confirmed")),
        "severity_index": round(sev, 2),
        "reference": latest.get("reference"),
    }


def water_balance(rain: dict[date, float], et0: dict[date, float], start: date, end: date,
                  awc_mm: float = 150.0, kc: float = 0.8) -> dict[date, float]:
    """Village root-zone bucket as a fraction of available water capacity.

    Village-level by construction (CHIRPS 5 km, ERA5-Land 9 km). The default
    AWC of 150 mm is a Deccan vertisol root-zone figure; pass the soil-map value
    when one is available.
    """
    out = {}
    store = awc_mm * 0.5
    d = start
    while d <= end:
        store += float(rain.get(d, 0.0) or 0.0)
        store -= kc * float(et0.get(d, 4.0) or 0.0) * min(1.0, store / (0.5 * awc_mm) if awc_mm else 1.0)
        store = min(max(store, 0.0), awc_mm)
        out[d] = store / awc_mm if awc_mm else 0.0
        d += timedelta(days=1)
    return out
