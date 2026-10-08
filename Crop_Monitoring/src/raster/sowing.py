"""Sowing date: monsoon-onset prior x optical curve fit x radar emergence.

What replaced what (Dhaswadi audit, plan §5.3):

  * The calendar prior no longer widens itself around whatever the cue says
    (old sowing.py:263-267). The prior is fixed before any satellite cue is read.
  * Rainfed kharif sowing follows the monsoon. The prior is anchored on onset:
    the first day cumulative rain since 1 June reaches ONSET_MM without a
    false start (a dry spell of DRY_SPELL_DAYS right after). Maharashtra's
    sowing advisory uses the same 75-100 mm rule.
  * Pre-monsoon (irrigated) sowing is allowed only with evidence: the field
    was seen bare and then greened up before onset.
  * Cues combine through a robust likelihood (Gaussian + uniform outlier
    term), not a product with a 1e-6 floor, which turned fusion into a vote.
  * The answer carries P10-P90. Wider than INSUFFICIENT_SPAN_DAYS is reported
    as `insufficient_evidence`, never as a date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from datetime import date, timedelta
from typing import Optional

import numpy as np

ONSET_MM = 75.0
DRY_SPELL_DAYS = 10
DRY_DAY_MM = 1.0
ONSET_SEARCH_FROM = (6, 1)       # month, day
ONSET_SEARCH_TO = (8, 15)
RAINFED_LAG_MAX = 21             # sowing within this many days after onset
IRRIGATED_EARLIEST = (4, 15)
CALENDAR_PAD_DAYS = 15
OUTLIER_P = 0.10
INSUFFICIENT_SPAN_DAYS = 25

# Days from sowing to the cue each sensor reports, and its spread.
OPTICAL_FIT_SIGMA = 7.0          # whole-curve fit: sowing is its own parameter
OPTICAL_SOS_LAG = {"Cotton": 24, "Soyabean": 18, "Tur": 26, "default": 21}
OPTICAL_SOS_SIGMA = 8.0
RADAR_LAG = {"Cotton": 32, "Soyabean": 24, "Tur": 34, "default": 28}
RADAR_SIGMA = 9.0


@dataclass
class Onset:
    date: Optional[date]
    cumulative_mm: float
    false_starts: list[date] = field(default_factory=list)
    note: str = ""


def monsoon_onset(rain_by_day: dict[date, float], year: int) -> Onset:
    """First day cumulative rain since 1 June reaches ONSET_MM with no
    DRY_SPELL_DAYS run of dry days in the 20 days after it."""
    d = date(year, *ONSET_SEARCH_FROM)
    end = date(year, *ONSET_SEARCH_TO)
    cum = 0.0
    false_starts: list[date] = []
    while d <= end:
        cum += float(rain_by_day.get(d, 0.0) or 0.0)
        if cum >= ONSET_MM:
            run = longest = 0
            for k in range(1, 21):
                r = rain_by_day.get(d + timedelta(days=k))
                if r is None:
                    continue
                run = run + 1 if r < DRY_DAY_MM else 0
                longest = max(longest, run)
            if longest < DRY_SPELL_DAYS:
                return Onset(d, cum, false_starts,
                             f"{ONSET_MM:.0f} mm since 1 June reached {d.isoformat()}")
            false_starts.append(d)
            cum = 0.0           # a false start resets the count
        d += timedelta(days=1)
    return Onset(None, cum, false_starts, f"onset not reached by {end.isoformat()}")


@dataclass
class SowingCue:
    family: str            # optical_fit | optical_sos | radar
    date: date
    sigma: float
    detail: str = ""


@dataclass
class SowingResult:
    status: str            # estimated | insufficient_evidence | no_cue
    date: Optional[date]
    p10: Optional[date]
    p90: Optional[date]
    sources: list[str]
    regime: str            # rainfed | irrigated_evidence
    onset: Optional[date]
    note: str

    def to_dict(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {"status": self.status, "date": iso(self.date), "p10": iso(self.p10),
                "p90": iso(self.p90), "sources": list(self.sources), "regime": self.regime,
                "onset": iso(self.onset), "note": self.note}


def prior_weights(days: list[date], crop_sow_months: tuple[int, int], onset: Optional[date],
                  irrigated: bool) -> np.ndarray:
    """Fixed prior over candidate sowing days. Never reshaped by a cue."""
    y = days[0].year
    m0, m1 = crop_sow_months
    cal_start = date(y, m0, 1) - timedelta(days=CALENDAR_PAD_DAYS)
    cal_end = (date(y, m1 + 1, 1) if m1 < 12 else date(y + 1, 1, 1)) - timedelta(days=1) \
        + timedelta(days=CALENDAR_PAD_DAYS)
    w = np.zeros(len(days))
    for i, d in enumerate(days):
        if irrigated:
            if date(y, *IRRIGATED_EARLIEST) <= d <= cal_end:
                w[i] = 1.0
            continue
        if not cal_start <= d <= cal_end:
            continue
        if onset is None:
            w[i] = 1.0          # onset unknown: flat over the calendar window
            continue
        k = (d - onset).days
        if 0 <= k <= RAINFED_LAG_MAX:
            # Triangular: most sowing in the first week after onset.
            w[i] = 1.0 - k / (RAINFED_LAG_MAX + 1.0) * 0.7
        elif -7 <= k < 0:
            w[i] = 0.25          # a heavy shower in the week before onset
        elif k > RAINFED_LAG_MAX:
            w[i] = 0.08          # sowing after a break in the monsoon: possible
        # Dates more than a week before onset stay at 0. A May green-up
        # (weeds, an orchard, a pre-monsoon cue) must not become the sowing date.
    if w.sum() == 0:
        w[:] = 1.0
    return w / w.sum()


def posterior(days: list[date], prior: np.ndarray, cues: list[SowingCue]) -> np.ndarray:
    span = max(len(days), 1)
    k = np.arange(len(days))
    post = prior.copy()
    for c in cues:
        mu = (c.date - days[0]).days
        g = np.exp(-0.5 * ((k - mu) / c.sigma) ** 2) / (c.sigma * np.sqrt(2 * np.pi))
        post = post * ((1 - OUTLIER_P) * g + OUTLIER_P / span)
    s = post.sum()
    return post / s if s > 0 else prior


def _quantile(days: list[date], p: np.ndarray, q: float) -> date:
    c = np.cumsum(p)
    return days[min(int(np.searchsorted(c, q)), len(days) - 1)]


def estimate(
    crop: str,
    crop_sow_months: tuple[int, int],
    season_year: int,
    onset: Onset,
    cues: list[SowingCue],
    irrigated: bool = False,
    window_start: Optional[date] = None,
) -> SowingResult:
    """`window_start` is the first day imagery was read for. A median before it
    is an extrapolation of a canopy already up on that day (sugarcane, an
    orchard, an early irrigated crop), not a measured sowing, so no date is
    printed for it."""
    y = season_year
    days = [date(y, 3, 1) + timedelta(days=i) for i in range((date(y, 9, 30) - date(y, 3, 1)).days + 1)]
    prior = prior_weights(days, crop_sow_months, onset.date, irrigated)
    regime = "irrigated_evidence" if irrigated else "rainfed"
    if not cues:
        return SowingResult("no_cue", None, None, None, [], regime, onset.date,
                            "No optical or radar cue for this field. Sowing was not invented.")
    post = posterior(days, prior, cues)
    p10, p50, p90 = (_quantile(days, post, q) for q in (0.10, 0.50, 0.90))
    used = [c.family for c in cues if abs((c.date - p50).days) <= 2.5 * c.sigma]
    if window_start is not None and p50 < window_start:
        return SowingResult("before_window", None, None, None, used, regime, onset.date,
                            f"Canopy already established on {window_start.isoformat()}, the first "
                            "day observed; sown before the season window, so no date is given.")
    span = (p90 - p10).days
    if span > INSUFFICIENT_SPAN_DAYS:
        return SowingResult("insufficient_evidence", None, p10, p90, used, regime, onset.date,
                            f"Sowing window {p10.isoformat()} to {p90.isoformat()} is wider than "
                            f"{INSUFFICIENT_SPAN_DAYS} days; reported as a window, not a date.")
    note = "Median of the onset prior times the optical and radar cues."
    rejected = [c.family for c in cues if c.family not in used]
    if rejected:
        note += f" Cue(s) {', '.join(rejected)} disagreed and were down-weighted as outliers."
    return SowingResult("estimated", p50, p10, p90, used, regime, onset.date, note)


SAR_REFERENCE_FILE = Path(__file__).resolve().parents[2] / "reference" / "sar_reference.json"
_SAR_DEFAULT = {"bare_vh_max_db": -20.0, "bare_cr_max_db": -8.5,
                "canopy_vh_min_db": -19.0, "canopy_cr_min_db": -9.0}


def load_sar_reference(path: Path = SAR_REFERENCE_FILE) -> dict:
    """Absolute Sentinel-1 bare-soil / canopy signatures (reference/sar_reference.json).

    Absolute, not relative to the field's own pre-season radar: a field may
    already carry a crop before the monsoon, and a baseline taken from it (or
    from its surroundings) would then call a crop "bare".
    """
    ref = dict(_SAR_DEFAULT)
    try:
        ref.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
                    if k in _SAR_DEFAULT})
    except (OSError, ValueError):
        pass
    return ref


def radar_bare(vh_db: np.ndarray, cr_db: np.ndarray, ref: dict) -> np.ndarray:
    """True where a radar look shows bare soil (low VH and low cross-ratio).
    The cross-ratio barely moves when bare soil is wet (Marathwada June
    median -10.9 dB vs -10.4 dB dry), so monsoon rain does not read as a crop."""
    return (vh_db <= ref["bare_vh_max_db"]) & (cr_db <= ref["bare_cr_max_db"])


def radar_canopy(vh_db: np.ndarray, cr_db: np.ndarray, ref: dict) -> np.ndarray:
    return (vh_db >= ref["canopy_vh_min_db"]) & (cr_db >= ref["canopy_cr_min_db"])


def radar_emergence(dates: list[date], vh_db: np.ndarray, cr_db: np.ndarray,
                    ref: Optional[dict] = None) -> Optional[date]:
    """First of two consecutive canopy looks that FOLLOW a bare look.

    Uses the absolute signatures in `ref` (load_sar_reference): the field must
    first be seen bare by radar, then show a sustained canopy signature. A
    field already vegetated on 1 May returns None (no datable emergence).
    """
    ref = ref or load_sar_reference()
    vh = np.asarray(vh_db, float)
    cr = np.asarray(cr_db, float)
    ok = np.isfinite(vh) & np.isfinite(cr)
    looks = [(d, b, c) for d, b, c, k in zip(dates, radar_bare(vh, cr, ref), radar_canopy(vh, cr, ref), ok) if k]
    looks.sort(key=lambda t: t[0])
    seen_bare = False
    for (d1, b1, c1), (_, _, c2) in zip(looks, looks[1:]):
        if b1:
            seen_bare = True
            continue
        if seen_bare and c1 and c2:
            return d1
    return None


# NDVI at which the radar canopy signature switches on: 50 % of looks at
# NDVI 0.35-0.40 on labelled Marathwada parcels (radar look paired with an
# optical look in the same 10-day bin, n ~ 18,000).
RADAR_CANOPY_NDVI = 0.37


def radar_lag(curve) -> int:
    """Days from sowing to the radar canopy signature for one crop: the first
    DAS at which its reference NDVI curve reaches RADAR_CANOPY_NDVI. Ties the
    radar cue to the same curves the optical fit uses, instead of constants."""
    if curve is None:
        return RADAR_LAG["default"]
    das = np.arange(0, 200)
    v = curve.value(das)
    hit = np.nonzero(v >= RADAR_CANOPY_NDVI)[0]
    return int(das[hit[0]]) if len(hit) else RADAR_LAG["default"]


def lag_for(table: dict[str, int], crop: str) -> int:
    return table.get(crop, table["default"])
