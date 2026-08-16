"""
Tests for assessment/drift_analysis.py

The drift report is what decides whether the new scores are safe to show a
lender, so its own honesty matters: a missing baseline must not read as zero,
and a newly-rejected parcel must not enter the movement statistics as a large
fall.
"""

from __future__ import annotations

import pytest

from assessment.drift_analysis import (
    compare_many, compare_one, format_report, summarise,
)


def _doc(score=62.0, band="MEDIUM", subs=None, cycles=5, status="SUCCESS"):
    subs = subs or {"landuse": 68.0, "vigor": 54.0, "stability": 61.0, "weather": 59.0}
    return {
        "status": status,
        "index_score": score,
        "risk_category": band,
        "risk_assessment": {
            "index_score": score,
            "risk_category": band,
            "sub_indices": {k: {"score": v} for k, v in subs.items()},
        },
        "performance_summary": {"n_complete_cycles": cycles},
    }


def _rejected(cls="WATER", confidence=0.91):
    return {
        "status": "REJECTED_NOT_AGRICULTURAL",
        "rejection_class": cls,
        "land_cover": {"class": cls, "confidence": confidence,
                       "reason": f"Parcel appears to be {cls.lower()}."},
    }


# ── basic comparison ──────────────────────────────────────────────────────

def test_delta_is_current_minus_baseline():
    c = compare_one("F1", _doc(score=60.0), _doc(score=48.0))
    assert c["outcome"] == "compared"
    assert c["delta"] == pytest.approx(-12.0)


def test_attribution_names_the_pillar_that_moved_most():
    before = _doc(subs={"landuse": 68.0, "vigor": 54.0, "stability": 84.0, "weather": 59.0})
    after = _doc(subs={"landuse": 66.0, "vigor": 52.0, "stability": 25.0, "weather": 59.0})
    c = compare_one("F1", before, after)
    assert c["primary_driver"] == "stability"
    assert c["primary_driver_delta"] == pytest.approx(-59.0)


def test_band_movement_is_directional():
    worse = compare_one("F1", _doc(band="LOW"), _doc(band="HIGH"))
    better = compare_one("F2", _doc(band="HIGH"), _doc(band="LOW"))
    same = compare_one("F3", _doc(band="MEDIUM"), _doc(band="MEDIUM"))
    assert worse["band_direction"] == "worsened"
    assert better["band_direction"] == "improved"
    assert same["band_direction"] == "unchanged"


def test_large_move_is_flagged():
    assert compare_one("F1", _doc(score=60.0), _doc(score=40.0))["large_move"] is True
    assert compare_one("F2", _doc(score=60.0), _doc(score=58.0))["large_move"] is False


# ── honesty about non-comparable cases ────────────────────────────────────

def test_missing_baseline_is_not_treated_as_zero():
    c = compare_one("F1", None, _doc(score=55.0))
    assert c["outcome"] == "no_baseline"
    assert c["delta"] is None, "a farmer with no history did not gain 55 points"


def test_newly_rejected_parcel_has_no_delta():
    c = compare_one("F1", _doc(score=41.0), _rejected("WATER"))
    assert c["outcome"] == "newly_rejected"
    assert c["delta"] is None
    assert c["rejection_class"] == "WATER"


def test_rejections_do_not_enter_the_delta_distribution():
    """
    Counting a rejection as a fall to zero would corrupt every aggregate —
    which is exactly the kind of number that gets quoted in a deck.
    """
    comps = [
        compare_one("F1", _doc(score=60.0), _doc(score=58.0)),
        compare_one("F2", _doc(score=41.0), _rejected("BUILTUP")),
        compare_one("F3", _doc(score=64.0), _doc(score=62.0)),
    ]
    s = summarise(comps)
    assert s["delta_sample_size"] == 2
    assert s["n_newly_rejected"] == 1
    assert s["mean_delta"] == pytest.approx(-2.0)


def test_failed_current_run_is_distinguished_from_a_low_score():
    c = compare_one("F1", _doc(score=60.0), {"status": "FAILED", "error": "GEE timeout"})
    assert c["outcome"] == "current_failed"
    assert c["delta"] is None
    assert c["current_error"] == "GEE timeout"


# ── aggregation ───────────────────────────────────────────────────────────

def test_summary_reports_its_own_sample_size():
    comps = [compare_one(f"F{i}", _doc(score=60.0), _doc(score=60.0 - i)) for i in range(5)]
    s = summarise(comps)
    assert s["delta_sample_size"] == 5
    assert s["small_sample"] is True


def test_small_sample_flag_clears_with_enough_data():
    comps = [compare_one(f"F{i}", _doc(score=60.0), _doc(score=59.0)) for i in range(25)]
    assert summarise(comps)["small_sample"] is False


def test_quantiles_and_extremes():
    scores = [70.0, 65.0, 60.0, 55.0, 50.0]
    comps = [compare_one(f"F{i}", _doc(score=60.0), _doc(score=s))
             for i, s in enumerate(scores)]
    s = summarise(comps)
    assert s["max_increase"] == pytest.approx(10.0)
    assert s["max_decrease"] == pytest.approx(-10.0)
    assert s["median_delta"] == pytest.approx(0.0)
    assert s["n_improved"] == 2 and s["n_worsened"] == 2 and s["n_unchanged"] == 1


def test_band_migration_matrix_is_built():
    comps = [
        compare_one("F1", _doc(band="LOW"), _doc(band="MEDIUM")),
        compare_one("F2", _doc(band="LOW"), _doc(band="MEDIUM")),
        compare_one("F3", _doc(band="MEDIUM"), _doc(band="MEDIUM")),
    ]
    s = summarise(comps)
    assert s["band_migration"]["LOW->MEDIUM"] == 2
    assert s["band_migration"]["MEDIUM->MEDIUM"] == 1
    assert s["n_band_worsened"] == 2


def test_newly_perennial_parcels_are_counted():
    before = _doc(cycles=0)
    after = _doc(cycles=3)
    after["risk_assessment"]["sub_indices"]["landuse"]["inputs"] = {"cycle_kind": "perennial"}
    s = summarise([compare_one("F1", before, after)])
    assert s["n_newly_perennial"] == 1


def test_compare_many_covers_ids_in_either_set():
    comps = compare_many({"A": _doc(), "B": _doc()}, {"B": _doc(), "C": _doc()})
    assert {c["farmer_id"] for c in comps} == {"A", "B", "C"}


# ── document shape tolerance ──────────────────────────────────────────────

def test_multi_farm_document_shape_is_understood():
    """Multi-farm docs carry the score under farmer_level."""
    multi = {
        "status": "SUCCESS",
        "farmer_level": {
            "index_score": 58.0, "risk_category": "MEDIUM",
            "sub_indices": {"landuse": 60.0, "vigor": 55.0},
        },
    }
    c = compare_one("F1", _doc(score=62.0), multi)
    assert c["current_score"] == pytest.approx(58.0)
    assert c["current_band"] == "MEDIUM"
    assert c["delta"] == pytest.approx(-4.0)


def test_legacy_component_scores_shape_is_understood():
    legacy = {"status": "SUCCESS", "credit_score": 55.0, "risk_category": "MEDIUM",
              "component_scores": {"landuse": 60.0, "vigor": 50.0}}
    c = compare_one("F1", legacy, _doc(score=50.0))
    assert c["baseline_score"] == pytest.approx(55.0)
    assert "landuse" in c["sub_index_deltas"]


def test_new_sub_indices_are_listed_not_treated_as_deltas():
    before = _doc(subs={"landuse": 60.0})
    after = _doc(subs={"landuse": 60.0, "data_confidence": 74.0})
    c = compare_one("F1", before, after)
    assert c["sub_indices_only_in_current"] == ["data_confidence"]
    assert "data_confidence" not in c["sub_index_deltas"]


# ── report rendering ──────────────────────────────────────────────────────

def test_report_renders_and_names_the_key_facts():
    comps = [
        compare_one("F1", _doc(score=60.0, band="MEDIUM"), _doc(score=44.0, band="HIGH")),
        compare_one("F2", _doc(score=41.0), _rejected("WATER")),
        compare_one("F3", None, _doc(score=55.0)),
    ]
    text = format_report(comps, summarise(comps))
    assert "SCORE DRIFT REPORT" in text
    assert "Newly rejected" in text
    assert "no_baseline" in text
    assert "SMALL SAMPLE" in text


def test_report_is_explicit_when_nothing_is_comparable():
    comps = [compare_one("F1", None, _doc())]
    text = format_report(comps, summarise(comps))
    assert "Nothing can be concluded" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
