"""
Per-season land utilization, and occupancy consistency.

Two defects covered here:

  * There was no Kharif/Rabi/Zaid breakdown ANYWHERE in the pipeline.
    LandUtilizationAnalyzer never read season_type, and neither did the risk
    engine — so despite every cycle carrying a season label, no per-season
    analysis existed.

  * land_utilization_index was built from a UNION of occupied days while
    fallow_fraction was built from SUM(duration_days), which double-counts
    overlapping cycles. The two therefore did not add to 1, and fallow_fraction
    feeds the risk index at 15% weight.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer


class _Cycle:
    """Minimal stand-in for CropCycle (the analyzer reads attributes)."""

    def __init__(self, sow, harvest, season_type="kharif", peak_ndvi=0.72,
                 crop_type="MEDIUM_MODERATE"):
        self.sowing_date = datetime.strptime(sow, "%Y-%m-%d")
        self.harvest_date = datetime.strptime(harvest, "%Y-%m-%d")
        self.duration_days = (self.harvest_date - self.sowing_date).days
        self.season_type = season_type
        self.season_label = f"{season_type.title()} {self.sowing_date.year}"
        self.peak_ndvi = peak_ndvi
        self.crop_type = crop_type


START = datetime(2021, 6, 1)
END = datetime(2024, 6, 1)


def _analyze(cycles, start=START, end=END):
    return LandUtilizationAnalyzer().analyze(cycles, start, end)


# ── occupancy consistency ─────────────────────────────────────────────────

def test_utilization_and_fallow_sum_to_one():
    cycles = [
        _Cycle("2021-06-15", "2021-10-20", "kharif"),
        _Cycle("2021-11-05", "2022-03-10", "rabi"),
        _Cycle("2022-06-20", "2022-10-25", "kharif"),
    ]
    r = _analyze(cycles)
    total = r["land_utilization_index"] + r["fallow_analysis"]["fallow_fraction"]
    assert total == pytest.approx(1.0, abs=0.01), (
        f"utilization + fallow = {total}, should be 1.0"
    )


def test_overlapping_cycles_are_not_double_counted():
    """
    Phenology refinement can push adjacent cycles into overlap. Summing
    durations then over-reports cultivation and can drive fallow negative.
    """
    overlapping = [
        _Cycle("2021-06-15", "2021-11-30", "kharif"),
        _Cycle("2021-10-01", "2022-03-10", "rabi"),   # overlaps the first
    ]
    r = _analyze(overlapping)
    assert 0.0 <= r["land_utilization_index"] <= 1.0
    assert 0.0 <= r["fallow_analysis"]["fallow_fraction"] <= 1.0
    total = r["land_utilization_index"] + r["fallow_analysis"]["fallow_fraction"]
    assert total == pytest.approx(1.0, abs=0.01)

    naive_sum = sum(c.duration_days for c in overlapping)
    assert r["fallow_analysis"]["cultivated_days"] < naive_sum, (
        "overlap was not de-duplicated"
    )


def test_cultivated_days_never_exceed_the_window():
    cycles = [_Cycle("2021-06-15", "2024-05-01", "kharif")]
    r = _analyze(cycles)
    fa = r["fallow_analysis"]
    assert fa["cultivated_days"] <= fa["total_days"]


# ── per-season breakdown ──────────────────────────────────────────────────

def test_by_season_block_exists():
    r = _analyze([_Cycle("2021-06-15", "2021-10-20", "kharif")])
    assert "by_season" in r
    for season in ("kharif", "rabi", "zaid"):
        assert season in r["by_season"]


def test_per_season_cycle_counts_are_correct():
    cycles = [
        _Cycle("2021-06-15", "2021-10-20", "kharif"),
        _Cycle("2022-06-20", "2022-10-25", "kharif"),
        _Cycle("2021-11-05", "2022-03-10", "rabi"),
    ]
    bs = _analyze(cycles)["by_season"]
    assert bs["kharif"]["n_cycles"] == 2
    assert bs["rabi"]["n_cycles"] == 1
    assert bs["zaid"]["n_cycles"] == 0


def test_season_utilisation_reflects_years_cropped():
    """
    The reportable fact: 'cropped every kharif, but only one rabi in three'.
    """
    cycles = [
        _Cycle("2021-06-15", "2021-10-20", "kharif"),
        _Cycle("2022-06-20", "2022-10-25", "kharif"),
        _Cycle("2023-06-18", "2023-10-22", "kharif"),
        _Cycle("2021-11-05", "2022-03-10", "rabi"),
    ]
    bs = _analyze(cycles)["by_season"]
    assert bs["kharif"]["years_cropped"] == 3
    assert bs["kharif"]["utilisation"] == pytest.approx(1.0, abs=0.01)
    assert bs["rabi"]["years_cropped"] == 1
    assert bs["rabi"]["utilisation"] < 0.5


def test_seasons_cropped_counts_distinct_seasons():
    cycles = [
        _Cycle("2021-06-15", "2021-10-20", "kharif"),
        _Cycle("2021-11-05", "2022-03-10", "rabi"),
    ]
    assert _analyze(cycles)["by_season"]["seasons_cropped"] == 2


def test_per_season_metrics_are_reported():
    cycles = [_Cycle("2021-06-15", "2021-10-20", "kharif", peak_ndvi=0.78)]
    k = _analyze(cycles)["by_season"]["kharif"]
    assert k["mean_duration_days"] is not None
    assert k["mean_peak_ndvi"] == pytest.approx(0.78, abs=1e-6)


def test_absent_season_reports_none_not_zero():
    """Rule P-1: no rabi cycle means no rabi measurement, not a zero one."""
    r = _analyze([_Cycle("2021-06-15", "2021-10-20", "kharif")])
    rabi = r["by_season"]["rabi"]
    assert rabi["n_cycles"] == 0
    assert rabi["mean_peak_ndvi"] is None
    assert rabi["mean_duration_days"] is None


def test_cross_season_and_perennial_are_tracked_separately():
    cycles = [
        _Cycle("2021-07-15", "2021-12-31", "cross_season"),
        _Cycle("2022-01-01", "2022-12-31", "perennial"),
    ]
    bs = _analyze(cycles)["by_season"]
    assert bs["cross_season"]["n_cycles"] == 1
    assert bs["perennial"]["n_production_years"] == 1
    # A cross-season cycle must not be silently counted into kharif or rabi.
    assert bs["kharif"]["n_cycles"] == 0
    assert bs["rabi"]["n_cycles"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
