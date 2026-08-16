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

from config import PipelineConfig as P
from assessment.parcel_viability import (
    MARGINAL, NOT_VIABLE, PIXEL_HA, VIABLE,
    assess_parcel_viability, viability_gate_penalty,
)

# Derived from config, never hardcoded: these are POLICY values (the fundable
# floor is a lending decision) and they have already changed once. A test that
# pins them would fail on every policy change rather than on a real regression.
HARD_HA = P.PARCEL_MIN_PIXELS_HARD * PIXEL_HA
RELIABLE_HA = P.PARCEL_MIN_PIXELS_RELIABLE * PIXEL_HA


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


def test_a_parcel_between_the_two_floors_is_marginal_not_refused():
    """Fundable, but small enough that the signal carries neighbouring land."""
    midpoint = (HARD_HA + RELIABLE_HA) / 2
    v = assess_parcel_viability(registered_ha=midpoint, geometry_ha=midpoint)
    assert v["outcome"] == MARGINAL
    assert "neighbouring land" in v["reason"]


def test_the_fundable_floor_is_the_hard_boundary():
    """Just below the lending floor is refused; just above is not."""
    assert assess_parcel_viability(
        geometry_ha=HARD_HA - PIXEL_HA)["outcome"] == NOT_VIABLE
    assert assess_parcel_viability(
        geometry_ha=HARD_HA + PIXEL_HA)["outcome"] != NOT_VIABLE


def test_the_reliable_floor_separates_marginal_from_viable():
    assert assess_parcel_viability(
        geometry_ha=RELIABLE_HA - PIXEL_HA)["outcome"] == MARGINAL
    assert assess_parcel_viability(
        geometry_ha=RELIABLE_HA + PIXEL_HA)["outcome"] == VIABLE


def test_the_fundable_floor_sits_below_the_reliable_floor():
    """
    They measure different things — one is lending policy, one is physics — but
    an inverted pair would make the marginal band empty and silently disable the
    flag-and-discount behaviour.
    """
    assert P.PARCEL_MIN_PIXELS_HARD < P.PARCEL_MIN_PIXELS_RELIABLE


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
    marginal = assess_parcel_viability(geometry_ha=(HARD_HA + RELIABLE_HA) / 2)
    clean = assess_parcel_viability(registered_ha=1.6, geometry_ha=1.6)
    assert marginal["outcome"] == MARGINAL
    assert viability_gate_penalty(marginal) < 1.0
    assert viability_gate_penalty(clean) == 1.0


def test_evidence_states_the_thresholds():
    ev = assess_parcel_viability(geometry_ha=1.0)["evidence"]
    assert ev["thresholds"]["min_pixels_hard"] > 0
    assert ev["thresholds"]["min_pixels_reliable"] > ev["thresholds"]["min_pixels_hard"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── the polygon must win over the registered claim ────────────────────────

def test_polygon_area_rescues_a_parcel_the_registered_area_would_refuse():
    """
    REGRESSION. The viability check read the polygon area off the geometry
    object with getattr(geometry, 'area_ha') — which Shapely does not have — so
    it always fell back to the REGISTERED area. That is precisely the figure the
    geometry audit showed cannot be trusted, and it produced a real false
    rejection: 14322905350 refused at 0.1409 ha / 14 px when its actual polygon
    is 0.4517 ha / 45 px.
    """
    refused = assess_parcel_viability(registered_ha=0.1409, geometry_ha=None)
    correct = assess_parcel_viability(registered_ha=0.1409, geometry_ha=0.4517)
    assert refused["outcome"] == NOT_VIABLE
    assert correct["outcome"] != NOT_VIABLE
    assert correct["evidence"]["approx_pixels"] == 45


def test_polygon_area_can_also_refuse_what_the_registered_area_would_pass():
    """The precedence holds in both directions — it is not a one-way rescue."""
    v = assess_parcel_viability(registered_ha=1.5, geometry_ha=0.02)
    assert v["outcome"] == NOT_VIABLE
    assert v["evidence"]["effective_ha"] == 0.02


def test_geometry_area_helper_matches_a_known_square():
    """A helper that silently returns None reintroduces the bug above."""
    import math
    from shapely.geometry import Polygon
    from utils.geometry_utils import GeometryUtils

    lat, lon, target_ha = 29.0, 77.0, 0.45
    side = math.sqrt(target_ha * 10_000)
    dlat = side / 110_540.0
    dlon = side / (111_320.0 * math.cos(math.radians(lat)))
    poly = Polygon([(lon, lat), (lon + dlon, lat),
                    (lon + dlon, lat + dlat), (lon, lat + dlat), (lon, lat)])
    assert GeometryUtils.polygon_area_ha(poly) == pytest.approx(target_ha, rel=0.02)


def test_geometry_area_helper_accepts_geojson_and_degrades_safely():
    from utils.geometry_utils import GeometryUtils
    gj = {"type": "Polygon", "coordinates": [[[77.0, 29.0], [77.001, 29.0],
                                              [77.001, 29.001], [77.0, 29.001],
                                              [77.0, 29.0]]]}
    assert GeometryUtils.polygon_area_ha(gj) > 0
    assert GeometryUtils.polygon_area_ha(None) is None
    assert GeometryUtils.polygon_area_ha("not a geometry") is None
