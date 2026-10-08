"""
Crop calendars — which detected cycle a survey label actually refers to.

`Date` in the source GeoPackages is the *season of cultivation* marker from the
crop survey (it is always the 10th of a month), not a sowing date. The original
attribution rule picked "the cycle that contains the survey date", which is only
right when the survey happens to fall inside the crop's own growth window.
Measured on 02_cycles_aug.parquet it frequently does not:

    Gram     surveyed 10-Nov   31 attributed cycles peak Aug-Sep  (kharif crop)
    Tobacco  surveyed 10-Jun   most attributed cycles peak Nov-Jan
    Maize    surveyed 10-Mar   111 attributed cycles peak Aug-Sep

A November survey of a gram field lands on the tail of the preceding kharif
crop, because gram is only just being sown. Those rows train the model on the
wrong trajectory under the right name — the worst kind of label noise.

The fix is to resolve (crop, survey date) to a *season instance* with the
crop's agronomic calendar, and take the detected cycle whose PEAK falls in that
season's expected peak window. The peak is used rather than sowing because
sowing is the least reliable date the detector emits (it is fitted against a
noisy soil baseline); the peak is where the curve is best constrained.

This is label-aware by design and is used ONLY to decide which cycle carries a
training label, and at inference to check that a named crop's season agrees
with the observed peak (`season_consistent`). It never reaches the feature
vector.

This file is the single copy. `Crop_classification_model/src/crop_calendar.py`
and the monitoring package re-export it.

Calendars are compiled from ICAR / DES crop-calendar tables and state
agriculture department schedules. Windows are deliberately generous (a month
of slack either side is added at match time) because the goal is to reject
cycles from the *wrong season*, not to police sowing dates within one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class SeasonWindow:
    name: str                 # kharif | rabi | zaid | late_kharif | summer
    sow: Tuple[int, int]      # (first month, last month) of sowing, wraps year
    peak: Tuple[int, int]     # (first month, last month) of expected NDVI peak


# Perennial / ratoon crops have no seasonal peak to anchor on — they keep the
# original containment rule.
PERENNIAL = {"Banana", "Sugarcane", "Grapes"}

CALENDAR: Dict[str, List[SeasonWindow]] = {
    "Rice": [
        SeasonWindow("kharif", (6, 8), (8, 10)),
        SeasonWindow("rabi", (11, 1), (2, 4)),        # boro / rabi rice
    ],
    "Wheat": [SeasonWindow("rabi", (10, 12), (1, 3))],
    "Mustard": [SeasonWindow("rabi", (9, 11), (12, 2))],
    "Gram": [SeasonWindow("rabi", (10, 12), (12, 2))],
    "Potato": [SeasonWindow("rabi", (10, 12), (12, 2))],
    "Onion": [
        SeasonWindow("kharif", (6, 8), (8, 10)),
        SeasonWindow("late_kharif", (9, 10), (11, 1)),
        SeasonWindow("rabi", (11, 1), (1, 3)),
    ],
    "Maize": [
        SeasonWindow("kharif", (6, 7), (8, 10)),
        SeasonWindow("rabi", (10, 11), (1, 3)),
        SeasonWindow("zaid", (1, 3), (3, 5)),         # spring maize (Bihar/UP)
    ],
    "Bajra": [
        SeasonWindow("kharif", (6, 7), (8, 9)),
        SeasonWindow("summer", (2, 3), (4, 5)),       # summer bajra (Gujarat/UP)
    ],
    "Jowar": [
        SeasonWindow("kharif", (6, 7), (8, 10)),
        SeasonWindow("rabi", (9, 10), (11, 1)),
    ],
    "Groundnut": [
        SeasonWindow("kharif", (6, 7), (8, 10)),
        SeasonWindow("rabi", (11, 1), (2, 4)),
    ],
    "Soyabean": [SeasonWindow("kharif", (6, 7), (8, 9))],
    "Cotton": [SeasonWindow("kharif", (5, 7), (8, 11))],
    "Tur": [SeasonWindow("kharif", (6, 7), (9, 12))],
    "Chilli": [
        SeasonWindow("kharif", (7, 9), (10, 1)),
        SeasonWindow("rabi", (10, 11), (1, 3)),
    ],
    "Tobacco": [
        SeasonWindow("kharif", (5, 6), (7, 9)),       # Karnataka FCV
        SeasonWindow("late_kharif", (8, 9), (11, 1)), # Gujarat bidi
        SeasonWindow("rabi", (10, 11), (12, 2)),      # Andhra FCV / natu
    ],
}

# Slack applied to the peak window at match time, in days.
PEAK_SLACK_DAYS = 30
# How far a season instance's growth period may sit from the survey date and
# still be the one the survey refers to.
SURVEY_TOLERANCE_DAYS = 150


def _month_start(y: int, m: int) -> date:
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def _month_end(y: int, m: int) -> date:
    return _month_start(y, m + 1) - timedelta(days=1)


def _span(y: int, window: Tuple[int, int]) -> Tuple[date, date]:
    """Concrete dates for a (first, last) month pair starting in year y."""
    a, b = window
    end_year = y if b >= a else y + 1
    return _month_start(y, a), _month_end(end_year, b)


@dataclass(frozen=True)
class SeasonInstance:
    crop: str
    season: str
    sow_start: date
    sow_end: date
    peak_start: date
    peak_end: date

    @property
    def active(self) -> Tuple[date, date]:
        """Sowing start to ~2 months past the peak window — when a survey of
        this season could plausibly be taken."""
        return self.sow_start, self.peak_end + timedelta(days=60)

    def survey_gap_days(self, survey: date) -> int:
        a, b = self.active
        if a <= survey <= b:
            return 0
        return min(abs((survey - a).days), abs((survey - b).days))

    def peak_matches(self, peak: date, slack: int = PEAK_SLACK_DAYS) -> bool:
        return (self.peak_start - timedelta(days=slack)
                <= peak <= self.peak_end + timedelta(days=slack))

    def peak_distance(self, peak: date) -> int:
        if self.peak_start <= peak <= self.peak_end:
            return 0
        return min(abs((peak - self.peak_start).days),
                   abs((peak - self.peak_end).days))


def season_instances(crop: str, survey: date) -> List[SeasonInstance]:
    """Season instances of `crop` whose growth period is near `survey`,
    nearest first. Empty for perennials and crops with no calendar."""
    windows = CALENDAR.get(crop)
    if not windows:
        return []
    out: List[Tuple[int, SeasonInstance]] = []
    for w in windows:
        for y in (survey.year - 1, survey.year, survey.year + 1):
            s0, s1 = _span(y, w.sow)
            # The peak follows sowing; anchor its year on the sowing start.
            py = y if w.peak[0] >= w.sow[0] else y + 1
            p0, p1 = _span(py, w.peak)
            inst = SeasonInstance(crop, w.name, s0, s1, p0, p1)
            gap = inst.survey_gap_days(survey)
            if gap <= SURVEY_TOLERANCE_DAYS:
                out.append((gap, inst))
    out.sort(key=lambda t: (t[0], t[1].peak_start))
    return [i for _, i in out]


def pick_cycle_by_season(cycles: Sequence, crop: str, survey: date,
                         typical_days: float) -> Tuple[Optional[int], str, Optional[SeasonInstance]]:
    """
    Choose the cycle whose peak falls in the crop's season nearest the survey.

    Returns (index, tag, season_instance). index is None when no cycle's peak
    is consistent with any nearby season instance — the caller decides whether
    to fall back or reject.
    """
    insts = season_instances(crop, survey)
    if not insts:
        return None, "no_calendar", None

    # Season instances are ordered by distance from the survey. Take the first
    # instance that has at least one peak-consistent cycle; within it, prefer
    # the cycle nearest the peak window, then agronomic duration.
    for inst in insts:
        cands = []
        for i, c in enumerate(cycles):
            pk = c.peak_date.date() if hasattr(c.peak_date, "date") else c.peak_date
            if inst.peak_matches(pk):
                cands.append((inst.peak_distance(pk),
                              abs(float(c.duration_days) - typical_days), i))
        if cands:
            cands.sort()
            return cands[0][2], f"season_{inst.season}", inst
    return None, "no_cycle_in_season", insts[0]


def expected_peak_window(crop: str, survey: date) -> Optional[Tuple[date, date]]:
    insts = season_instances(crop, survey)
    return (insts[0].peak_start, insts[0].peak_end) if insts else None


# Season names a request can ask for, mapped to the calendar windows that count
# as that season. Late kharif is still a kharif-sown crop for a kharif request.
_SEASON_ALIASES = {
    "kharif": ("kharif", "late_kharif"),
    "rabi": ("rabi",),
    "zaid": ("zaid", "summer"),
}


def season_consistent(crop: str, peak: date, season: Optional[str] = None,
                      slack: int = PEAK_SLACK_DAYS) -> Optional[bool]:
    """Does `crop` have a calendar window whose peak can fall on `peak`?

    None when the crop is perennial or has no calendar: no evidence either way.
    With `season`, only that season's windows count, so a cycle peaking in
    September cannot be wheat on a kharif request.
    """
    if crop in PERENNIAL:
        return None
    windows = CALENDAR.get(crop)
    if not windows:
        return None
    allowed = _SEASON_ALIASES.get((season or "").lower())
    for w in windows:
        if allowed and w.name not in allowed:
            continue
        for y in (peak.year - 1, peak.year):
            s0, s1 = _span(y, w.sow)
            py = y if w.peak[0] >= w.sow[0] else y + 1
            p0, p1 = _span(py, w.peak)
            inst = SeasonInstance(crop, w.name, s0, s1, p0, p1)
            if inst.peak_matches(peak, slack):
                return True
    return False
