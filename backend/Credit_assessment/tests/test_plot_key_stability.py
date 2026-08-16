"""
Tests for plot_key stability (assessment/multi_farm_assessor.assign_plot_keys)

A plot_key is the join key for a parcel's evidence and score history. If it
changes between runs, that parcel's history silently splits in two. These tests
lock in that keys do not depend on array position.
"""

from __future__ import annotations

import pytest

from assessment.multi_farm_assessor import assign_plot_keys


def _farm(**kw):
    base = {
        "farm_id": None,
        "survey_number": None,
        "sub_survey_number": None,
        "area_ha": 1.0,
        "centroid": {"lat": 17.385, "lng": 78.487},
    }
    base.update(kw)
    return base


def _keys(farms):
    return [f["plot_key"] for f in assign_plot_keys(farms)]


# ── order independence ────────────────────────────────────────────────────

def test_keys_are_stable_when_farms_are_reordered():
    """A re-ingest that reorders farms[] must not reassign identities."""
    farms = [
        _farm(survey_number="107", area_ha=0.83, centroid={"lat": 17.1, "lng": 78.1}),
        _farm(survey_number="108", area_ha=1.20, centroid={"lat": 17.2, "lng": 78.2}),
        _farm(survey_number="109", area_ha=0.45, centroid={"lat": 17.3, "lng": 78.3}),
    ]
    forward = dict(zip((f["survey_number"] for f in farms), _keys(farms)))
    reversed_farms = list(reversed(farms))
    backward = dict(zip((f["survey_number"] for f in reversed_farms), _keys(reversed_farms)))
    assert forward == backward


def test_unidentifiable_farms_keep_keys_across_reordering():
    """The old plot_{i} fallback broke exactly here."""
    a = _farm(area_ha=0.83, centroid={"lat": 17.1, "lng": 78.1})
    b = _farm(area_ha=1.20, centroid={"lat": 17.2, "lng": 78.2})

    key_a_first = _keys([a, b])[0]
    key_a_second = _keys([b, a])[1]
    assert key_a_first == key_a_second, "key must follow the parcel, not its index"


def test_keys_survive_an_arbitrary_permutation():
    """Stronger than a simple reverse: every rotation must agree."""
    farms = [
        _farm(area_ha=0.5 + i, centroid={"lat": 17.0 + i, "lng": 78.0})
        for i in range(4)
    ]
    baseline = {f["area_ha"]: k for f, k in zip(farms, _keys(farms))}
    for shift in range(1, len(farms)):
        rotated = farms[shift:] + farms[:shift]
        got = {f["area_ha"]: k for f, k in zip(rotated, _keys(rotated))}
        assert got == baseline, f"keys changed under rotation by {shift}"


# ── persisted keys win ────────────────────────────────────────────────────

def test_existing_plot_key_is_always_reused():
    farms = [_farm(survey_number="107", plot_key="LEGACY_KEY_A")]
    assert _keys(farms) == ["LEGACY_KEY_A"]


def test_existing_key_survives_even_when_farm_id_would_differ():
    farms = [_farm(farm_id="AGRISTACK_99", plot_key="LEGACY_KEY_A")]
    assert _keys(farms) == ["LEGACY_KEY_A"], (
        "a parcel already assessed under a key must keep it"
    )


# ── precedence and uniqueness ─────────────────────────────────────────────

def test_farm_id_takes_precedence_over_survey_number():
    farms = [_farm(farm_id="AGRISTACK_42", survey_number="107")]
    assert _keys(farms) == ["AGRISTACK_42"]


def test_survey_and_sub_survey_are_combined():
    farms = [_farm(survey_number="107", sub_survey_number="2")]
    assert _keys(farms) == ["107_2"]


def test_keys_are_unique_within_a_farmer():
    farms = [
        _farm(survey_number="107", area_ha=0.5, centroid={"lat": 17.1, "lng": 78.1}),
        _farm(survey_number="107", area_ha=1.5, centroid={"lat": 17.9, "lng": 78.9}),
        _farm(survey_number="107", area_ha=2.5, centroid={"lat": 17.5, "lng": 78.5}),
    ]
    keys = _keys(farms)
    assert len(set(keys)) == len(keys), f"duplicate plot keys: {keys}"


def test_identical_rows_are_disambiguated_and_flagged():
    """Two byte-identical parcels are a data problem; make it visible."""
    farms = [_farm(area_ha=1.0), _farm(area_ha=1.0)]
    out = assign_plot_keys(farms)
    assert out[0]["plot_key"] != out[1]["plot_key"]
    assert out[1].get("plot_key_collision") is True


def test_farm_id_is_backfilled_from_the_key():
    out = assign_plot_keys([_farm(survey_number="107")])
    assert out[0]["farm_id"] == out[0]["plot_key"]


def test_input_is_not_mutated():
    src = _farm(survey_number="107")
    assign_plot_keys([src])
    assert "plot_key" not in src, "assign_plot_keys must operate on copies"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
