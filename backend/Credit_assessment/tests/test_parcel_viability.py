"""
Tests for assessment/parcel_viability.py

Driven by a geometry audit over the live database:

  * 60 of 113 parcels were under 20 Sentinel-2 pixels; 24 were under FIVE.
    At four pixels the AOI mean is mostly neighbouring land, whatever the
    boundary says.
  * Among AgriStack-ingested parcels only 7 of 106 had a plausible ratio
    between polygon area and registered area. Ratios spanned 0.022 to 1841 in
    BOTH directions — scatter, not a unit error, so the two figures describe
    different parcels. App-drawn boundaries were 7/7 within tolerance.
"""

from __future__ import annotations

import pytest

from assessment.parcel_viability import (
    MARGINAL, NOT_VIABLE, VIABLE, assess_parcel_viability, viability_gate_penalty,
)


# ── size ──────────────────────────────────────────────────────────────────

def test_a_normal_field_is_viable():
    v = assess_parcel_viability(registered_ha=1.648, geometry_ha=1.6406)
    assert v["outcome"] == VIABLE
    assert v["evidence"]["approx_pixels"] == 164


def test_a_four_pixel_parcel_is_not_viable():
    """The real case: 0.0412 ha, about 4 pixels."""
    v = assess_parcel_viability(registered_ha=0.0412, geometry_ha=0.0412)
    assert v["outcome"] == NOT_VIABLE
    assert "neighbouring land" in v["reason"]


def test_a_one_pixel_parcel_is_not_viable():
    v = assess_parcel_viability(registered_ha=0.0139, geometry_ha=0.0139)
    assert v["outcome"] == NOT_VIABLE


def test_a_small_but_measurable_parcel_is_marginal_not_refused():
    """0.0994 ha, about 9 pixels — measurable, but the signal is impure."""
    v = assess_parcel_viability(registered_ha=0.0994, geometry_ha=0.0994)
    assert v["outcome"] == MARGINAL
    assert "neighbouring land" in v["reason"]


def test_the_boundary_between_marginal_and_viable_is_the_reliable_floor():
    assert assess_parcel_viability(geometry_ha=0.19)["outcome"] == MARGINAL
    assert assess_parcel_viability(geometry_ha=0.25)["outcome"] == VIABLE


# ── boundary trust ────────────────────────────────────────────────────────

def test_disagreeing_areas_are_flagged_not_silently_accepted():
    """registered 1.42 ha vs polygon 0.0826 ha — one of them is wrong."""
    v = assess_parcel_viability(registered_ha=1.42, geometry_ha=0.0826)
    assert v["outcome"] in (MARGINAL, NOT_VIABLE)
    assert v["evidence"]["areas_disagree"] is True


def test_an_extreme_mismatch_is_reported_with_its_ratio():
    """The worst observed: 0.0036 ha registered against a 6.63 ha polygon."""
    v = assess_parcel_viability(registered_ha=0.0036, geometry_ha=6.6295)
    assert v["evidence"]["areas_disagree"] is True
    assert v["evidence"]["area_ratio"] > 1000
    # Big enough to measure, but the boundary cannot be trusted.
    assert v["outcome"] == MARGINAL


def test_agreeing_areas_are_not_flagged():
    v = assess_parcel_viability(registered_ha=0.545, geometry_ha=0.5419)
    assert v["evidence"]["areas_disagree"] is False
    assert v["outcome"] == VIABLE


def test_the_module_does_not_guess_which_area_is_correct():
    """
    Deciding that would just move the error. It reports disagreement; fixing
    the pairing is an ingest concern.
    """
    v = assess_parcel_viability(registered_ha=0.07, geometry_ha=9.3504)
    assert v["evidence"]["registered_ha"] == 0.07
    assert v["evidence"]["geometry_ha"] == 9.3504
    assert "cannot tell which" in v["reason"]


def test_measurement_uses_the_polygon_not_the_claim():
    """The polygon is what we actually measure over."""
    v = assess_parcel_viability(registered_ha=5.0, geometry_ha=0.30)
    assert v["evidence"]["effective_ha"] == 0.30


# ── degenerate input ──────────────────────────────────────────────────────

def test_no_area_at_all_is_not_viable():
    assert assess_parcel_viability()["outcome"] == NOT_VIABLE
    assert assess_parcel_viability(registered_ha=0)["outcome"] == NOT_VIABLE


def test_registered_area_alone_is_usable():
    v = assess_parcel_viability(registered_ha=1.5)
    assert v["outcome"] == VIABLE
    assert v["evidence"]["areas_disagree"] is False


# ── confidence discount ───────────────────────────────────────────────────

def test_marginal_parcels_carry_a_confidence_discount():
    marginal = assess_parcel_viability(geometry_ha=0.10)
    clean = assess_parcel_viability(registered_ha=1.6, geometry_ha=1.6)
    assert viability_gate_penalty(marginal) < 1.0
    assert viability_gate_penalty(clean) == 1.0


def test_evidence_states_the_thresholds():
    ev = assess_parcel_viability(geometry_ha=1.0)["evidence"]
    assert ev["thresholds"]["min_pixels_hard"] > 0
    assert ev["thresholds"]["min_pixels_reliable"] > ev["thresholds"]["min_pixels_hard"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
