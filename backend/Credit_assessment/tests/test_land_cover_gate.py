"""
Land-cover gate fixture parcels.

Seven synthetic parcels covering the cases the pipeline previously scored as
farmland. Traced before this gate existed:

    a parking lot   -> index 41 / HIGH / SUCCESS
    dense forest    -> scored BETTER than a genuine fallow farm

The two that must NOT be rejected are the genuine fallow farm (fallow land is
still farmland) and the orchard (perennials are fundable, decision D-6).
"""

from __future__ import annotations

import numpy as np
import pytest

from config import PipelineConfig as P
from crop_analysis.land_cover_gate import (
    BARREN, BUILTUP, CROPLAND, FLAG, FOREST, PASS, PLANTATION, REJECT, WATER,
    classify_land_cover, confidence_gate_penalty,
)

N = 36  # ~1 year of 10-day bins


def _cd(ndvi, mndwi=None, ndbi=None, bsi=None, nirv=None):
    """Assemble the continuous_data keys the gate reads."""
    n = len(ndvi)

    def _fill(v):
        return list(v) if v is not None else None

    out = {
        "dates": [f"2023-{1 + i // 3:02d}-{1 + (i % 3) * 10:02d}" for i in range(n)],
        "ndvi_values": list(ndvi),
    }
    for key, val in (
        ("mndwi_values", mndwi), ("ndbi_values", ndbi),
        ("bsi_values", bsi), ("nirv_values", nirv),
    ):
        f = _fill(val)
        if f is not None:
            out[key] = f
    return out


def _const(v, n=N, jitter=0.0):
    rng = np.random.default_rng(0)
    return list(v + jitter * rng.standard_normal(n))


def _season(base=0.12, peak=0.78, n=N):
    """A cropping year: bare -> canopy -> bare, twice."""
    half = n // 2
    one = list(np.linspace(base, peak, half // 2)) + list(np.linspace(peak, base, half - half // 2))
    return (one * 2)[:n]


# ── fixtures ──────────────────────────────────────────────────────────────

WATER_BODY = _cd(
    ndvi=_const(-0.28, jitter=0.03),
    mndwi=_const(0.55, jitter=0.05),
    ndbi=_const(-0.30, jitter=0.03),
    nirv=_const(0.01, jitter=0.005),
)

ROOFTOP = _cd(
    ndvi=_const(0.08, jitter=0.02),
    mndwi=_const(-0.20, jitter=0.03),
    ndbi=_const(0.22, jitter=0.04),
    bsi=_const(0.30, jitter=0.03),
    nirv=_const(0.02, jitter=0.005),
)

QUARRY = _cd(
    ndvi=_const(0.13, jitter=0.02),
    mndwi=_const(-0.25, jitter=0.03),
    ndbi=_const(0.05, jitter=0.04),
    bsi=_const(0.42, jitter=0.03),
    nirv=_const(0.03, jitter=0.005),
)

FOREST_PARCEL = _cd(
    ndvi=_const(0.80, jitter=0.02),
    mndwi=_const(-0.35, jitter=0.02),
    ndbi=_const(-0.28, jitter=0.02),
    nirv=_const(0.30, jitter=0.01),
)

ORCHARD = _cd(
    ndvi=_const(0.62, jitter=0.03),
    mndwi=_const(-0.30, jitter=0.02),
    ndbi=_const(-0.20, jitter=0.02),
    nirv=_const(0.22, jitter=0.01),
)

DOUBLE_CROPPED = _cd(
    ndvi=_season(),
    mndwi=_const(-0.15, jitter=0.05),
    ndbi=_const(-0.10, jitter=0.05),
    nirv=_const(0.15, jitter=0.05),
)

# A real farm that lay fallow for most of the record but greened up once.
FALLOW_FARM = _cd(
    ndvi=(_const(0.16, n=24, jitter=0.03)
          + list(np.linspace(0.16, 0.66, 6))
          + list(np.linspace(0.66, 0.16, 6))),
    mndwi=_const(-0.18, jitter=0.04),
    ndbi=_const(-0.05, jitter=0.05),
    nirv=_const(0.08, jitter=0.03),
)


# ── must be rejected ──────────────────────────────────────────────────────

def test_water_body_is_rejected():
    v = classify_land_cover(WATER_BODY)
    assert v["class"] == WATER
    assert v["outcome"] == REJECT
    assert v["is_cultivable"] is False


def test_rooftop_is_rejected():
    v = classify_land_cover(ROOFTOP)
    assert v["class"] == BUILTUP
    assert v["outcome"] == REJECT


def test_quarry_is_rejected():
    """
    Whether a quarry reads as BARREN or BUILTUP is not something the spectral
    evidence can settle reliably, and we do not claim it can — both are
    non-agricultural and both are rejected. What matters is that it is never
    scored as cropland.
    """
    v = classify_land_cover(QUARRY)
    assert v["class"] in (BARREN, BUILTUP)
    assert v["outcome"] == REJECT
    assert v["is_cultivable"] is False


def test_forest_is_not_scored_as_cropland():
    """Forest previously scored better than a genuine fallow farm."""
    v = classify_land_cover(FOREST_PARCEL)
    assert v["class"] in (FOREST, PLANTATION)
    assert v["outcome"] in (REJECT, FLAG)
    assert v["class"] != CROPLAND


# ── must NOT be rejected ──────────────────────────────────────────────────

def test_double_cropped_farm_passes():
    v = classify_land_cover(DOUBLE_CROPPED, n_cycles=2)
    assert v["class"] == CROPLAND
    assert v["outcome"] == PASS
    assert v["is_cultivable"] is True


def test_fallow_farm_is_not_rejected():
    """
    Fallow land is still farmland. This is the false-positive that would
    generate angry support tickets, so it is asserted explicitly.
    """
    v = classify_land_cover(FALLOW_FARM)
    assert v["outcome"] != REJECT, f"a real fallow farm was rejected as {v['class']}"
    assert v["is_cultivable"] is True


def test_orchard_is_plantation_and_not_rejected():
    """Decision D-6: perennials are fundable, so PLANTATION never rejects."""
    v = classify_land_cover(ORCHARD)
    assert v["class"] == PLANTATION
    assert v["outcome"] == FLAG
    assert v["is_cultivable"] is True


def test_plantation_is_never_collapsed_into_forest_or_barren():
    v = classify_land_cover(ORCHARD)
    assert v["class"] not in (FOREST, BARREN)


def test_registry_crop_hint_can_rescue_a_perennial_but_never_condemn():
    """An unverified self-report may only move a verdict AWAY from rejection."""
    strict = classify_land_cover(FOREST_PARCEL)
    hinted = classify_land_cover(FOREST_PARCEL, registry_crop="Sugarcane")
    assert hinted["class"] == PLANTATION
    assert hinted["outcome"] != REJECT
    # And it cannot push a good parcel toward rejection.
    farm = classify_land_cover(DOUBLE_CROPPED, n_cycles=2, registry_crop="Sugarcane")
    assert farm["outcome"] != REJECT


# ── behaviour of the gate itself ──────────────────────────────────────────

def test_insufficient_observations_flags_rather_than_rejects():
    """We cannot reject a parcel we were unable to look at."""
    v = classify_land_cover(_cd(ndvi=[0.1, 0.1, 0.1]))
    assert v["outcome"] == FLAG
    assert v["insufficient_data"] is True
    assert v["is_cultivable"] is True


def test_empty_input_does_not_crash_or_reject():
    v = classify_land_cover(None)
    assert v["outcome"] == FLAG and v["is_cultivable"] is True


def test_verdict_is_produced_on_pass_too():
    """Evidence must be visible for a lender either way, not only on rejection."""
    v = classify_land_cover(DOUBLE_CROPPED, n_cycles=2)
    assert v["evidence"]["n_obs"] > 0
    assert v["streams"]["spectral"]["class"] is not None
    assert v["reason"]


def test_evidence_is_auditable_and_raw():
    v = classify_land_cover(WATER_BODY)
    ev = v["evidence"]
    for key in ("ndvi_p10", "ndvi_p50", "ndvi_p90", "water_frac_positive", "n_obs"):
        assert key in ev, f"{key} missing from evidence"
    assert ev["water_frac_positive"] > 0.5


def test_flagged_parcel_discounts_the_confidence_gate():
    """A parcel we are unsure about must not score like clean cropland."""
    flagged = classify_land_cover(ORCHARD)
    passed = classify_land_cover(DOUBLE_CROPPED, n_cycles=2)
    assert confidence_gate_penalty(flagged) < 1.0
    assert confidence_gate_penalty(passed) == 1.0


def test_confidence_is_bounded():
    for cd in (WATER_BODY, ROOFTOP, QUARRY, FOREST_PARCEL, ORCHARD,
               DOUBLE_CROPPED, FALLOW_FARM):
        v = classify_land_cover(cd)
        assert 0.0 <= v["confidence"] <= 1.0


def test_external_lulc_is_off_until_its_accuracy_is_recorded():
    """Rule P-6: no 'validated source' without having read its validation."""
    assert P.LANDCOVER_USE_EXTERNAL_LULC is False
    assert P.LANDCOVER_EXTERNAL_LULC_ACCURACY is None


def test_external_lulc_verdict_is_used_when_supplied():
    v = classify_land_cover(
        DOUBLE_CROPPED, n_cycles=2,
        external_lulc={"class": CROPLAND, "confidence": 0.9, "source": "test"},
    )
    assert v["external_lulc_used"] is True
    assert v["streams"]["external_lulc"]["class"] == CROPLAND


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── geometry provenance: a bad boundary is not bad land ───────────────────

def test_rejection_is_downgraded_when_geometry_was_substituted():
    """
    When a polygon fails QA the collector silently analyses a circular buffer
    instead. A circle overlapping the adjacent river yields a confident WATER
    verdict about land that is perfectly fine. We cannot tell the two apart, so
    we must not refuse the farmer.
    """
    clean = classify_land_cover(WATER_BODY)
    substituted = classify_land_cover(
        WATER_BODY,
        geospatial_prep={"geometry_source": "polygon_rejected_fallback_point"},
    )
    assert clean["outcome"] == REJECT
    assert substituted["outcome"] == FLAG
    assert substituted["is_cultivable"] is True
    assert "boundary may be wrong" in substituted["reason"]


def test_geometry_provenance_is_recorded_on_every_verdict():
    v = classify_land_cover(
        DOUBLE_CROPPED, n_cycles=2,
        geospatial_prep={"geometry_source": "polygon"},
    )
    assert v["geometry_substituted"] is False
    assert v["geometry_source"] == "polygon"


def test_substituted_geometry_is_called_out_in_the_notes():
    v = classify_land_cover(
        QUARRY, geospatial_prep={"geometry_source": "polygon_rejected_fallback_point"},
    )
    assert any("substituted geometry" in n for n in v["notes"])


def test_a_good_boundary_still_rejects_normally():
    """The downgrade must only apply when geometry was actually substituted."""
    v = classify_land_cover(WATER_BODY, geospatial_prep={"geometry_source": "polygon"})
    assert v["outcome"] == REJECT
