"""
Canonical season assignment.

Replaces two functions that disagreed with each other:
  _season_label      called Feb-Mar "Rabi"
  _assign_season_type called Feb-Mar "Zaid"
so the same cycle carried contradictory season fields. Both keyed off the
sowing MONTH alone — harvest_date was accepted and ignored, making a crop sown
in September and harvested in February "100% kharif" — and 'cross_season' was
unreachable because the month branches covered all twelve.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from crop_analysis.crop_cycle_detector import CropCycleDetector as D

NORTH = {"latitude": 30.9}   # Punjab
SOUTH = {"latitude": 11.0}   # Tamil Nadu


def _dt(s):
    return datetime.strptime(s, "%Y-%m-%d")


def _assign(start, end, ctx=None):
    return D.assign_season(_dt(start), _dt(end), ctx)


# ── the two functions no longer contradict each other ─────────────────────

def test_season_type_and_label_always_agree():
    """The Feb-Mar contradiction, asserted across the whole calendar."""
    for month in range(1, 13):
        start = f"2023-{month:02d}-10"
        stype, label = _assign(start, f"2023-{month:02d}-10")
        head = label.split()[0].lower()
        if stype == "cross_season":
            assert head == "cross-season"
        else:
            assert head == stype, (
                f"month {month}: type={stype} but label={label}"
            )


# ── assignment uses the whole cycle, not just the sowing month ────────────

def test_harvest_date_is_actually_used():
    """
    Sown late September, harvested February. Under sowing-month-only logic this
    was 100% kharif; by overlap it is overwhelmingly rabi.
    """
    stype, _ = _assign("2023-09-25", "2024-02-20")
    assert stype in ("rabi", "cross_season")
    assert stype != "kharif"


def test_classic_kharif_cycle():
    stype, label = _assign("2023-06-20", "2023-10-05")
    assert stype == "kharif"
    assert label == "Kharif 2023"


def test_classic_rabi_cycle_spans_the_year_boundary():
    stype, label = _assign("2023-11-05", "2024-03-10")
    assert stype == "rabi"
    assert label == "Rabi 2023/2024"


def test_classic_zaid_cycle():
    stype, label = _assign("2023-03-25", "2023-05-30")
    assert stype == "zaid"
    assert label == "Zaid 2023"


def test_cross_season_is_reachable():
    """
    It was dead code before: the month branches covered all twelve months.

    Mid-July to end-December splits roughly 92 days kharif / 77 days rabi —
    no season holds the 55% majority, which is exactly what 'cross_season'
    is for.
    """
    stype, _ = _assign("2023-07-15", "2023-12-31")
    assert stype == "cross_season"


def test_a_long_cycle_is_not_forced_into_one_season():
    stype, _ = _assign("2023-06-01", "2024-06-01")
    assert stype == "cross_season"


# ── region awareness ──────────────────────────────────────────────────────

def test_southern_calendar_can_flip_a_boundary_cycle():
    """
    A short cycle straddling the kharif/rabi boundary should be assigned
    differently in Punjab and Tamil Nadu, because the southern calendar runs
    ~a month later.

    Early October to end November: in the north that is mostly rabi; in the
    south, where kharif still runs to mid-November, it is mostly kharif.
    """
    boundary = ("2023-10-05", "2023-11-30")
    assert _assign(*boundary, NORTH)[0] == "rabi"
    assert _assign(*boundary, SOUTH)[0] == "kharif"


def test_the_shift_only_matters_near_a_boundary():
    """
    A cycle sitting squarely inside one season must be assigned the same way
    everywhere — the regional shift should not move unambiguous cases.
    """
    solidly_kharif = ("2023-06-20", "2023-09-20")
    assert _assign(*solidly_kharif, NORTH)[0] == _assign(*solidly_kharif, SOUTH)[0]


def test_missing_latitude_falls_back_without_guessing():
    """No location information must not invent a regional shift."""
    assert D._region_shift_days(None) == 0
    assert D._region_shift_days({}) == 0
    assert D._region_shift_days({"latitude": "not-a-number"}) == 0


def test_northern_latitude_gets_no_shift():
    assert D._region_shift_days(NORTH) == 0


def test_southern_latitude_gets_a_shift():
    assert D._region_shift_days(SOUTH) > 0


# ── robustness ────────────────────────────────────────────────────────────

def test_reversed_dates_do_not_crash():
    stype, label = _assign("2023-10-01", "2023-06-01")
    assert stype and label


def test_same_day_cycle_is_assigned():
    stype, label = _assign("2023-07-15", "2023-07-15")
    assert stype == "kharif"


def test_every_assignment_returns_a_known_type():
    valid = {"kharif", "rabi", "zaid", "cross_season"}
    for m in range(1, 13):
        for dur in (45, 120, 200, 330):
            start = datetime(2023, m, 5)
            end = start.replace() + (datetime(2023, m, 5) - datetime(2023, m, 5))
            from datetime import timedelta
            end = start + timedelta(days=dur)
            stype, _ = D.assign_season(start, end)
            assert stype in valid, f"unexpected season type {stype}"


# ── backwards-compatible wrappers still work ──────────────────────────────

def test_legacy_wrappers_delegate_to_the_canonical_implementation():
    s, e = _dt("2023-06-20"), _dt("2023-10-05")
    assert D._assign_season_type(s, e) == "kharif"
    assert D._season_label(s, e) == "Kharif 2023"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
