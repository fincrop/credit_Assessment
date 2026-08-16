"""
Tests for assessment/data_sufficiency.py

Motivated by the first real drift run, which split cleanly:

    cycles detected (n=5)   mean change  -8.6   healthy correction
    zero cycles     (n=3)   mean change -50.5   all landing at ~26

Those three had previously scored 71-83 and now score ~26 VERY_HIGH. Both
numbers are wrong. One parcel had TWO 240-day stretches with no usable optical
observation — 41% of the window blind. You cannot conclude "nothing grew here"
from a period you did not observe.
"""

from __future__ import annotations

import pytest

from assessment.data_sufficiency import (
    INSUFFICIENT, SUFFICIENT, assess_data_sufficiency,
)

STEP = 10


def _cd(sources, start_year=2023):
    """Build continuous_data with a given per-bin signal_source list."""
    from datetime import datetime, timedelta
    t0 = datetime(start_year, 6, 1)
    dates = [(t0 + timedelta(days=i * STEP)).strftime("%Y-%m-%d")
             for i in range(len(sources))]
    return {"dates": dates, "signal_source": list(sources)}


def _well_observed(n=110):
    return _cd(["optical"] * n)


def _with_blind_gap(gap_bins, n=110):
    """A record with one contiguous imputed stretch in the middle."""
    src = ["optical"] * n
    start = n // 3
    for i in range(start, start + gap_bins):
        src[i] = "imputed"
    return _cd(src)


# ── the asymmetry: observed cycles are always evidence ────────────────────

def test_detected_cycles_are_sufficient_regardless_of_gaps():
    """
    Gaps elsewhere reduce confidence, they do not invalidate what we saw.
    """
    v = assess_data_sufficiency(_with_blind_gap(30), n_cycles=2)
    assert v["outcome"] == SUFFICIENT
    assert "2 cycle(s) observed" in v["reason"]


def test_a_single_cycle_is_enough_to_be_sufficient():
    v = assess_data_sufficiency(_with_blind_gap(40), n_cycles=1)
    assert v["outcome"] == SUFFICIENT


# ── zero cycles: it depends on whether we were looking ────────────────────

def test_zero_cycles_with_good_coverage_is_a_real_finding():
    """Genuinely fallow land must still be scoreable."""
    v = assess_data_sufficiency(_well_observed(), n_cycles=0)
    assert v["outcome"] == SUFFICIENT
    assert "good enough" in v["reason"]


def test_zero_cycles_with_a_season_long_blind_gap_is_not_a_finding():
    """The observed failure: a 240-day gap could hide a whole season."""
    v = assess_data_sufficiency(_with_blind_gap(24), n_cycles=0)  # 24 bins = 240d
    assert v["outcome"] == INSUFFICIENT
    assert "hidden" in v["reason"] or "unseen" in v["reason"]


def test_a_short_gap_does_not_trigger_insufficiency():
    """Two or three cloudy bins are normal and must not block scoring."""
    v = assess_data_sufficiency(_with_blind_gap(2), n_cycles=0)
    assert v["outcome"] == SUFFICIENT


def test_mostly_unobserved_record_is_insufficient():
    src = ["imputed"] * 80 + ["optical"] * 30
    v = assess_data_sufficiency(_cd(src), n_cycles=0)
    assert v["outcome"] == INSUFFICIENT
    assert v["evidence"]["observed_fraction"] < 0.35


# ── what counts as an observation ─────────────────────────────────────────

def test_imputed_bins_do_not_count_as_observations():
    v = assess_data_sufficiency(_cd(["imputed"] * 110), n_cycles=0)
    assert v["evidence"]["n_observed_bins"] == 0
    assert v["outcome"] == INSUFFICIENT


def test_sar_only_bins_do_not_count_as_direct_observation():
    """SAR-only is a weaker inference than optical or fused."""
    v = assess_data_sufficiency(_cd(["sar"] * 110), n_cycles=0)
    assert v["evidence"]["observed_fraction"] == 0.0


def test_fused_bins_count_as_observed():
    v = assess_data_sufficiency(_cd(["fused"] * 110), n_cycles=0)
    assert v["evidence"]["observed_fraction"] == 1.0
    assert v["outcome"] == SUFFICIENT


# ── evidence is auditable ─────────────────────────────────────────────────

def test_evidence_reports_the_gap_and_its_dates():
    v = assess_data_sufficiency(_with_blind_gap(24), n_cycles=0)
    ev = v["evidence"]
    assert ev["largest_blind_gap_days"] >= 240
    assert ev["largest_blind_gap_span"] is not None
    assert len(ev["largest_blind_gap_span"]) == 2


def test_evidence_states_the_thresholds_in_force():
    ev = assess_data_sufficiency(_well_observed(), n_cycles=0)["evidence"]
    assert ev["thresholds"]["blind_gap_days"] > 0
    assert 0 < ev["thresholds"]["min_observed_fraction"] < 1


def test_reason_is_specific_not_generic():
    v = assess_data_sufficiency(_with_blind_gap(24), n_cycles=0)
    assert str(v["evidence"]["largest_blind_gap_days"]) in v["reason"]


# ── degenerate inputs ─────────────────────────────────────────────────────

def test_empty_input_is_insufficient_not_a_crash():
    v = assess_data_sufficiency(None, n_cycles=0)
    assert v["outcome"] == INSUFFICIENT


def test_trailing_blind_run_is_detected():
    src = ["optical"] * 80 + ["imputed"] * 30
    v = assess_data_sufficiency(_cd(src), n_cycles=0)
    assert v["evidence"]["largest_blind_gap_days"] >= 290


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
