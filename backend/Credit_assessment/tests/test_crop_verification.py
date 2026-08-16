"""
Tests for crop_analysis/crop_verification.py

The registry crop is self-reported. Trusting it outright lets a wrong label
swing 45% of the index; ignoring it (crop_confidence hardcoded 0.0 against a
>= 0.25 gate) left ~1150 lines of ICAR reference data and the per-stage weather
analysis permanently dead.

Checking it against observed phenology needs no ground truth — cotton is
documented at 165-180 days, so a 90-day cycle contradicts the declaration from
data we already hold.
"""

from __future__ import annotations

import pytest

from config import PipelineConfig as P
from crop_analysis.crop_verification import (
    CONSISTENT, INCONSISTENT, INDETERMINATE, verify_declared_crop,
)

GATE = 0.25  # performance_analyzer's is_crop_reliable threshold


def _cycle(duration, peak=0.82, season="kharif"):
    return {"duration_days": duration, "peak_ndvi": peak, "season_type": season}


# ── the decision it exists to make ────────────────────────────────────────

def test_a_matching_declaration_is_corroborated():
    v = verify_declared_crop([_cycle(120)], "Rice")
    assert v["outcome"] == CONSISTENT
    assert v["canonical_crop"] == "Rice"


def test_a_contradicted_declaration_is_rejected():
    """Rice is documented at 100-150 days. Sixty days is not rice."""
    v = verify_declared_crop([_cycle(60, peak=0.45)], "Rice")
    assert v["outcome"] == INCONSISTENT
    assert v["confidence"] == 0.0


def test_a_cotton_declaration_on_a_short_cycle_is_rejected():
    v = verify_declared_crop([_cycle(90, peak=0.70)], "Cotton")
    assert v["outcome"] == INCONSISTENT


# ── the confidence contract ───────────────────────────────────────────────

def test_a_corroborated_declaration_clears_the_crop_specific_gate():
    """Below 0.25 the crop-specific scoring path stays shut — that was the bug."""
    v = verify_declared_crop([_cycle(120)], "Rice")
    assert v["confidence"] > GATE


def test_confidence_never_reads_as_a_detection():
    """
    A match is corroboration, not measurement: wheat and mustard both run ~130
    days in rabi and both peak near 0.8. Agreement makes a declaration
    plausible; it does not establish it.
    """
    v = verify_declared_crop([_cycle(120)] * 5, "Rice")
    assert v["confidence"] <= P.CROP_VERIFY_MAX_CONFIDENCE
    assert v["confidence"] < 0.7


def test_confidence_scales_with_agreement():
    strong = verify_declared_crop([_cycle(120), _cycle(125), _cycle(118)], "Rice")
    mixed = verify_declared_crop([_cycle(120), _cycle(125), _cycle(40)], "Rice")
    assert strong["confidence"] > mixed["confidence"]


def test_a_rejected_declaration_yields_zero_confidence():
    v = verify_declared_crop([_cycle(45)], "Rice")
    assert v["confidence"] == 0.0


# ── refusing to claim what it cannot ──────────────────────────────────────

def test_an_unknown_crop_is_indeterminate_not_a_rejection():
    """Absence of a reference curve is not evidence against the farmer."""
    v = verify_declared_crop([_cycle(120)], "Zorblax")
    assert v["outcome"] == INDETERMINATE
    assert "not in the reference set" in v["reason"]


def test_no_declaration_is_indeterminate():
    v = verify_declared_crop([_cycle(120)], None)
    assert v["outcome"] == INDETERMINATE


def test_no_cycles_is_indeterminate():
    v = verify_declared_crop([], "Rice")
    assert v["outcome"] == INDETERMINATE


def test_a_perennial_parcel_is_not_judged_against_annual_curves():
    """The reference curves describe sown crops; applying them here is a
    category error, not a mismatch."""
    v = verify_declared_crop([_cycle(350)], "Rice", cycle_kind="perennial")
    assert v["outcome"] == INDETERMINATE
    assert "perennial" in v["reason"].lower()


# ── tolerances behave sensibly ────────────────────────────────────────────

def test_outperforming_the_reference_canopy_is_not_a_mismatch():
    """A crop greener than its reference curve is not evidence against it."""
    v = verify_declared_crop([_cycle(120, peak=0.95)], "Rice")
    assert v["outcome"] == CONSISTENT


def test_duration_tolerance_absorbs_binning_and_sowing_variation():
    """
    Our dates are quantised to 10-day bins and real sowing shifts with monsoon
    onset, so a tight band would reject genuine matches.
    """
    ref = __import__("config").CropGrowthCurves.CROP_DURATIONS["Rice"]
    just_over = ref["max_days"] + 10
    assert verify_declared_crop([_cycle(just_over)], "Rice")["outcome"] == CONSISTENT


def test_case_and_spacing_in_the_declaration_are_handled():
    for name in ("rice", "  Rice ", "RICE"):
        v = verify_declared_crop([_cycle(120)], name)
        assert v["canonical_crop"] == "Rice", f"failed on {name!r}"


# ── evidence is auditable ─────────────────────────────────────────────────

def test_evidence_shows_the_reference_and_every_cycle_checked():
    v = verify_declared_crop([_cycle(120), _cycle(40)], "Rice")
    ev = v["evidence"]
    assert ev["reference"]["min_days"] == 100
    assert ev["n_cycles_checked"] == 2
    assert len(ev["cycles"]) == 2
    assert ev["cycles"][0]["duration_ok"] is True
    assert ev["cycles"][1]["duration_ok"] is False


def test_the_reason_names_the_expected_range():
    v = verify_declared_crop([_cycle(40)], "Rice")
    assert "100" in v["reason"] and "150" in v["reason"]


def test_a_mismatch_is_recorded_as_a_reusable_signal():
    """
    Every mismatch is a labelled example — a declared crop that demonstrably
    did not grow. That is the first crop-label data this system collects, and
    what a future classifier retraining needs.
    """
    v = verify_declared_crop([_cycle(45)], "Rice")
    assert v["outcome"] == INCONSISTENT
    assert v["declared_crop"] == "Rice"
    assert v["evidence"]["cycles"][0]["duration_days"] == 45


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
