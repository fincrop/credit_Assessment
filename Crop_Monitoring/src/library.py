"""Crop priors, season windows, and duration checks.

Durations come from ``CropGrowthCurves`` when the credit-assessment package
imports. Season windows come from the classification crop calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import src._bootstrap  # noqa: F401  (sys.path)


TYPED_CONFIDENCE = 0.50
MIN_ZONE_PIXELS = 12
MIN_ZONE_SHARE = 0.15
MIN_SOWING_GAP_DAYS = 21

# National order-of-magnitude yields (t/ha) used only when no district figure
# and no peer set is supplied. Cotton is lint. The document marks this pool.
DEFAULT_YIELD_T_HA = {
    "Bajra": 1.3,
    "Jowar": 1.0,
    "Maize": 3.1,
    "Rice": 2.8,
    "Wheat": 3.5,
    "Groundnut": 1.4,
    "Mustard": 1.5,
    "Soyabean": 1.2,
    "Sunflower": 1.0,
    "Tobacco": 1.6,
    "Gram": 1.1,
    "Tur": 0.9,
    "Cotton": 0.45,
    "Chilli": 2.2,
    "Onion": 16.0,
    "Potato": 23.0,
    "Banana": 35.0,
    "Grapes": 20.0,
    "Sugarcane": 80.0,
    "Cabbage": 22.0,
    "Others": 2.0,
}


@dataclass(frozen=True)
class CropPrior:
    name: str
    min_days: int
    typical_days: int
    max_days: int
    base_temp: float
    opt_temp: float
    ceiling_temp: float
    heat_tmax: float
    emergence_ndvi: float
    emergence_lag: int
    tau_peak: float
    rue: float
    hi: float
    cover_max: float
    residue_cover: float
    rainfed_kharif: bool
    perennial: bool
    stages: tuple[tuple[float, float, str], ...]


def _stages(*rows: tuple[float, float, str]) -> tuple[tuple[float, float, str], ...]:
    return rows


_DEFAULT_STAGES = _stages(
    (0.00, 0.12, "emergence"),
    (0.12, 0.40, "vegetative"),
    (0.40, 0.70, "reproductive"),
    (0.70, 0.88, "fill"),
    (0.88, 1.01, "maturity"),
)

_CEREAL = _stages(
    (0.00, 0.12, "establishment"),
    (0.12, 0.38, "tillering"),
    (0.38, 0.58, "heading"),
    (0.58, 0.82, "grain fill"),
    (0.82, 1.01, "maturity"),
)

_COTTON = _stages(
    (0.00, 0.10, "emergence"),
    (0.10, 0.32, "vegetative"),
    (0.32, 0.55, "flowering"),
    (0.55, 0.82, "boll"),
    (0.82, 1.01, "maturity"),
)

_LEGUME = _stages(
    (0.00, 0.12, "emergence"),
    (0.12, 0.38, "vegetative"),
    (0.38, 0.58, "flowering"),
    (0.58, 0.85, "pod fill"),
    (0.85, 1.01, "maturity"),
)

_TUBER = _stages(
    (0.00, 0.12, "emergence"),
    (0.12, 0.35, "vegetative"),
    (0.35, 0.78, "bulking"),
    (0.78, 1.01, "maturity"),
)

_CANE = _stages(
    (0.00, 0.15, "establishment"),
    (0.15, 0.70, "grand growth"),
    (0.70, 1.01, "ripening"),
)


def _prior(
    name: str,
    *,
    min_days: int,
    typical_days: int,
    max_days: int,
    base: float,
    opt: float,
    ceiling: float,
    heat: float,
    ndvi: float,
    lag: int,
    tau_peak: float,
    rue: float,
    hi: float,
    cover: float,
    residue: float,
    rainfed: bool,
    perennial: bool,
    stages: tuple,
) -> CropPrior:
    return CropPrior(
        name, min_days, typical_days, max_days, base, opt, ceiling, heat,
        ndvi, lag, tau_peak, rue, hi, cover, residue, rainfed, perennial, stages,
    )


# Keys match CropGrowthCurves names. Durations below are fallbacks; load_prior
# overwrites min/typical/max from the live table when that import works.
_FALLBACK_DAYS = {
    "Bajra": (65, 85, 110),
    "Jowar": (90, 110, 130),
    "Maize": (80, 100, 120),
    "Rice": (100, 120, 150),
    "Wheat": (110, 130, 150),
    "Groundnut": (90, 110, 130),
    "Mustard": (110, 130, 150),
    "Soyabean": (90, 110, 130),
    "Sunflower": (85, 100, 120),
    "Tobacco": (130, 160, 180),
    "Gram": (100, 120, 140),
    "Tur": (150, 180, 220),
    "Cotton": (150, 180, 210),
    "Chilli": (120, 150, 180),
    "Onion": (100, 120, 150),
    "Potato": (90, 110, 130),
    "Banana": (270, 330, 365),
    "Grapes": (120, 150, 180),
    "Sugarcane": (270, 330, 365),
    "Cabbage": (60, 80, 100),
    "Others": (90, 120, 150),
}


def _build(name: str, days: tuple[int, int, int], **kw) -> CropPrior:
    return _prior(name, min_days=days[0], typical_days=days[1], max_days=days[2], **kw)


def _table() -> dict[str, CropPrior]:
    d = _FALLBACK_DAYS
    cereal = dict(base=5, opt=22, ceiling=35, heat=34, ndvi=0.28, lag=10,
                  tau_peak=0.62, rue=2.2, hi=0.45, cover=0.90, residue=0.22,
                  rainfed=False, perennial=False, stages=_CEREAL)
    return {
        "Wheat": _build("Wheat", d["Wheat"], **{**cereal, "base": 0, "opt": 18, "rainfed": False}),
        "Rice": _build("Rice", d["Rice"], **{**cereal, "base": 10, "opt": 28, "heat": 35,
                       "ndvi": 0.30, "rainfed": True}),
        "Maize": _build("Maize", d["Maize"], **{**cereal, "base": 10, "opt": 28, "heat": 35,
                        "rue": 3.2, "hi": 0.50, "rainfed": True, "stages": _DEFAULT_STAGES,
                        "tau_peak": 0.60}),
        "Bajra": _build("Bajra", d["Bajra"], **{**cereal, "base": 10, "opt": 30, "heat": 40,
                        "rue": 2.0, "hi": 0.35, "cover": 0.75, "ndvi": 0.25, "rainfed": True}),
        "Jowar": _build("Jowar", d["Jowar"], **{**cereal, "base": 8, "opt": 28, "heat": 38,
                        "rue": 2.2, "hi": 0.40, "rainfed": True}),
        "Soyabean": _build("Soyabean", d["Soyabean"], base=10, opt=28, ceiling=38, heat=36,
                           ndvi=0.30, lag=10, tau_peak=0.58, rue=1.6, hi=0.40, cover=0.88,
                           residue=0.22, rainfed=True, perennial=False, stages=_LEGUME),
        "Groundnut": _build("Groundnut", d["Groundnut"], base=10, opt=28, ceiling=38, heat=36,
                            ndvi=0.28, lag=10, tau_peak=0.60, rue=1.5, hi=0.40, cover=0.75,
                            residue=0.20, rainfed=True, perennial=False, stages=_LEGUME),
        "Gram": _build("Gram", d["Gram"], base=5, opt=20, ceiling=32, heat=32,
                       ndvi=0.25, lag=12, tau_peak=0.58, rue=1.4, hi=0.40, cover=0.70,
                       residue=0.18, rainfed=False, perennial=False, stages=_LEGUME),
        "Tur": _build("Tur", d["Tur"], base=10, opt=28, ceiling=38, heat=36,
                      ndvi=0.30, lag=12, tau_peak=0.55, rue=1.4, hi=0.28, cover=0.85,
                      residue=0.22, rainfed=True, perennial=False, stages=_LEGUME),
        "Mustard": _build("Mustard", d["Mustard"], base=5, opt=20, ceiling=32, heat=32,
                          ndvi=0.28, lag=8, tau_peak=0.60, rue=1.8, hi=0.28, cover=0.80,
                          residue=0.20, rainfed=False, perennial=False, stages=_DEFAULT_STAGES),
        "Cotton": _build("Cotton", d["Cotton"], base=15, opt=28, ceiling=40, heat=38,
                         ndvi=0.25, lag=12, tau_peak=0.48, rue=1.6, hi=0.12, cover=0.85,
                         residue=0.25, rainfed=True, perennial=False, stages=_COTTON),
        "Potato": _build("Potato", d["Potato"], base=4, opt=18, ceiling=30, heat=30,
                         ndvi=0.30, lag=15, tau_peak=0.55, rue=2.0, hi=0.75, cover=0.85,
                         residue=0.20, rainfed=False, perennial=False, stages=_TUBER),
        "Onion": _build("Onion", d["Onion"], base=5, opt=20, ceiling=32, heat=34,
                        ndvi=0.25, lag=12, tau_peak=0.50, rue=1.6, hi=0.80, cover=0.70,
                        residue=0.18, rainfed=False, perennial=False, stages=_TUBER),
        "Chilli": _build("Chilli", d["Chilli"], base=12, opt=26, ceiling=38, heat=36,
                         ndvi=0.28, lag=14, tau_peak=0.55, rue=1.5, hi=0.45, cover=0.75,
                         residue=0.20, rainfed=False, perennial=False, stages=_DEFAULT_STAGES),
        "Tobacco": _build("Tobacco", d["Tobacco"], base=10, opt=26, ceiling=36, heat=35,
                          ndvi=0.28, lag=14, tau_peak=0.55, rue=1.5, hi=0.55, cover=0.80,
                          residue=0.20, rainfed=False, perennial=False, stages=_DEFAULT_STAGES),
        "Sunflower": _build("Sunflower", d["Sunflower"], base=8, opt=25, ceiling=36, heat=35,
                            ndvi=0.28, lag=10, tau_peak=0.55, rue=1.8, hi=0.30, cover=0.80,
                            residue=0.18, rainfed=True, perennial=False, stages=_DEFAULT_STAGES),
        "Cabbage": _build("Cabbage", d["Cabbage"], base=5, opt=18, ceiling=30, heat=30,
                          ndvi=0.30, lag=10, tau_peak=0.70, rue=1.8, hi=0.70, cover=0.75,
                          residue=0.20, rainfed=False, perennial=False, stages=_DEFAULT_STAGES),
        "Banana": _build("Banana", d["Banana"], base=14, opt=28, ceiling=40, heat=38,
                         ndvi=0.45, lag=20, tau_peak=0.50, rue=1.8, hi=0.25, cover=0.92,
                         residue=0.40, rainfed=False, perennial=True, stages=_CANE),
        "Sugarcane": _build("Sugarcane", d["Sugarcane"], base=12, opt=30, ceiling=42, heat=40,
                            ndvi=0.35, lag=20, tau_peak=0.55, rue=1.8, hi=0.70, cover=0.95,
                            residue=0.30, rainfed=False, perennial=True, stages=_CANE),
        "Grapes": _build("Grapes", d["Grapes"], base=10, opt=25, ceiling=38, heat=36,
                         ndvi=0.30, lag=15, tau_peak=0.55, rue=1.4, hi=0.30, cover=0.75,
                         residue=0.22, rainfed=False, perennial=True, stages=_DEFAULT_STAGES),
        "Others": _build("Others", d["Others"], base=8, opt=26, ceiling=38, heat=36,
                         ndvi=0.28, lag=10, tau_peak=0.60, rue=1.8, hi=0.40, cover=0.85,
                         residue=0.22, rainfed=True, perennial=False, stages=_DEFAULT_STAGES),
    }


PRIORS = _table()


def _duration_overrides() -> dict[str, tuple[int, int, int]]:
    try:
        from config import CropGrowthCurves
    except Exception:
        return {}
    out = {}
    for name, row in CropGrowthCurves.CROP_DURATIONS.items():
        try:
            out[name] = (int(row["min_days"]), int(row["typical_days"]), int(row["max_days"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


_DURATION_OVERRIDES = _duration_overrides()


def get_prior(crop: str) -> CropPrior:
    base = PRIORS.get(crop) or PRIORS["Others"]
    days = _DURATION_OVERRIDES.get(crop)
    if not days or base.name != crop and crop not in PRIORS:
        if crop not in PRIORS:
            # Unknown label: Others physiology, caller-facing name preserved
            # only when we have no dedicated row. Duration still applies.
            if days:
                return CropPrior(
                    crop, days[0], days[1], days[2], base.base_temp, base.opt_temp,
                    base.ceiling_temp, base.heat_tmax, base.emergence_ndvi,
                    base.emergence_lag, base.tau_peak, base.rue, base.hi,
                    base.cover_max, base.residue_cover, base.rainfed_kharif,
                    base.perennial, base.stages,
                )
            return base
    if days and (days[0], days[1], days[2]) != (base.min_days, base.typical_days, base.max_days):
        base = CropPrior(
            base.name, days[0], days[1], days[2], base.base_temp, base.opt_temp,
            base.ceiling_temp, base.heat_tmax, base.emergence_ndvi, base.emergence_lag,
            base.tau_peak, base.rue, base.hi, base.cover_max, base.residue_cover,
            base.rainfed_kharif, base.perennial, base.stages,
        )
    return base


def harvest_month_window(sow_start: date, sow_end: date, prior: CropPrior) -> tuple[int, int] | None:
    """Harvest months for whatever crop classification named.

    The window is that crop's own sowing months plus its own min and max
    duration. A short crop and a long crop in the same season do not share it.
    """
    if prior.perennial:
        return None
    early = sow_start + timedelta(days=prior.min_days)
    late = sow_end + timedelta(days=prior.max_days)
    return early.month, late.month


def harvest_in_season(when: date | None, months: tuple[int, int] | None) -> bool | None:
    """True when an observed harvest falls in the months expected for this crop."""
    if when is None or months is None:
        return None
    start, end = months
    month = when.month
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end


def stage_name(prior: CropPrior, tau: float) -> str:
    if tau <= 0:
        return "pre-emergence"
    for lo, hi, name in prior.stages:
        if lo <= tau < hi:
            return name
    return prior.stages[-1][2]


@dataclass(frozen=True)
class ResolvedWindow:
    season: str
    sow_start: date
    sow_end: date
    monitor_start: date
    monitor_end: date


def _month_start(y: int, m: int) -> date:
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def _month_end(y: int, m: int) -> date:
    return _month_start(y, m + 1) - timedelta(days=1)


def _span(y: int, window: tuple[int, int]) -> tuple[date, date]:
    a, b = window
    end_year = y if b >= a else y + 1
    return _month_start(y, a), _month_end(end_year, b)


def _calendar():
    try:
        from crop_calendar import CALENDAR
        return CALENDAR
    except Exception:
        return {}


def _season_months(crop: str, season: Optional[str], as_of: date) -> tuple[str, tuple[int, int]]:
    cal = _calendar()
    rows = list(cal.get(crop) or [])
    if season:
        rows = [r for r in rows if r.name == season] or rows
    if rows:
        # Prefer the window whose sowing period is closest to as_of.
        best = None
        for r in rows:
            for y in (as_of.year - 1, as_of.year):
                s0, s1 = _span(y, r.sow)
                # Distance from as_of to the sowing interval.
                if s0 <= as_of <= s1:
                    dist = 0
                elif as_of < s0:
                    dist = (s0 - as_of).days
                else:
                    dist = (as_of - s1).days
                # A season still in progress (as_of within typical cycle after sow start)
                # beats a nearer future window.
                item = (dist, s0, r.name, r.sow, y)
                if best is None or item[0] < best[0]:
                    best = item
        if best is not None:
            return best[2], best[3]
    # Duration-table season, then month of the request.
    season_name = season
    if not season_name:
        try:
            from config import CropGrowthCurves
            season_name = CropGrowthCurves.CROP_DURATIONS.get(crop, {}).get("season")
        except Exception:
            season_name = None
    if season_name == "rabi" or (not season_name and as_of.month in (10, 11, 12, 1, 2, 3)):
        return "rabi", (10, 12)
    if season_name == "perennial":
        return "perennial", (as_of.month, as_of.month)
    return "kharif", (6, 8)


def resolve_window(
    crop: str,
    as_of: date,
    season: Optional[str],
    prior: CropPrior,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> ResolvedWindow:
    """Calendar sowing window and the satellite search around it."""
    if prior.perennial and not season:
        sow_end = as_of
        sow_start = as_of - timedelta(days=prior.typical_days)
        mon_start = start or (sow_start - timedelta(days=15))
        mon_end = end or as_of
        return ResolvedWindow("perennial", sow_start, sow_end, mon_start, mon_end)

    season_name, months = _season_months(crop, season, as_of)
    # Choose the year whose growing period contains as_of.
    chosen = None
    for y in (as_of.year - 1, as_of.year, as_of.year + 1):
        s0, s1 = _span(y, months)
        cycle_end = s0 + timedelta(days=prior.max_days + 30)
        if s0 - timedelta(days=20) <= as_of <= cycle_end:
            # Prefer the interval that has already started.
            if as_of >= s0 - timedelta(days=20):
                chosen = (s0, s1, y)
                if as_of <= cycle_end and as_of >= s0:
                    break
    if chosen is None:
        s0, s1 = _span(as_of.year, months)
        if as_of < s0:
            s0, s1 = _span(as_of.year - 1, months)
    else:
        s0, s1 = chosen[0], chosen[1]
    mon_start = start or (s0 - timedelta(days=15))
    mon_end = end or as_of
    if mon_end < mon_start:
        mon_end = mon_start
    return ResolvedWindow(season_name, s0, s1, mon_start, mon_end)


def temperature_scalar(tmean: float, prior: CropPrior) -> float:
    if tmean <= prior.base_temp or tmean >= prior.ceiling_temp:
        return 0.05
    if tmean <= prior.opt_temp:
        span = max(prior.opt_temp - prior.base_temp, 0.1)
        return max(0.05, (tmean - prior.base_temp) / span)
    span = max(prior.ceiling_temp - prior.opt_temp, 0.1)
    return max(0.05, (prior.ceiling_temp - tmean) / span)


def default_yield(crop: str) -> float:
    return float(DEFAULT_YIELD_T_HA.get(crop, DEFAULT_YIELD_T_HA["Others"]))
