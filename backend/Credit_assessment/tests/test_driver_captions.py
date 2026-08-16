"""
Tests for assessment/driver_captions.py

The report needs a sentence under each sub-index bar, and every one must cite a
real computed number. An LLM asked to write these would produce fluent
sentences containing invented figures — the exact failure the provenance
contract forbids. So these are templates over the engine's own inputs, and the
tests exist mainly to guarantee they never assert something the payload does
not support.
"""

from __future__ import annotations

import pytest

from assessment.driver_captions import build_driver_captions


def _ra(**subs):
    base = {
        "landuse": {"score": 62.0, "inputs": {
            "n_complete_cycles": 3, "years": 3.0, "cycles_per_year": 1.0,
            "season_coverage": 50.0, "fallow_fraction": 0.22,
            "fallow_basis": "measured"}, "drivers": {}},
        "vigor": {"score": 70.0, "inputs": {
            "mean_yield_potential": 70.0, "mean_peak_cvi": 0.70,
            "n_cycles_scored": 3, "peer_relative": False,
            "n_cycles_peer_scored": 0}},
        "stability": {"score": 80.0, "inputs": {
            "anomalies_high": 0, "anomalies_medium": 3, "anomalies_low": 0,
            "n_seasons_observed": 3, "vigor_cv": 0.05,
            "no_cultivation_evidence": False}},
        "weather": {"score": 65.0, "inputs": {
            "weather_risk_score": 38.0, "forward_exposure": 30.0},
            "drivers": {"resilience": 72.0}},
        "data_confidence": {"score": 74.0, "inputs": {
            "valid_fraction": 0.85, "mean_bin_quality": 0.79,
            "sar_fallback_fraction": 0.12, "n_cycles": 3}},
    }
    base.update(subs)
    return {"sub_indices": base, "confidence_gate": 0.91}


# ── a caption exists for every bar the report renders ─────────────────────

def test_every_sub_index_gets_a_caption():
    caps = build_driver_captions(_ra())
    for key in ("landuse", "vigor", "stability", "weather", "data_confidence"):
        assert key in caps and caps[key].strip()


def test_captions_cite_actual_numbers():
    caps = build_driver_captions(_ra())
    assert "3 cycle(s)" in caps["landuse"]
    assert "70/100" in caps["vigor"]
    assert "72/100" in caps["weather"]
    assert "85%" in caps["data_confidence"]


def test_the_confidence_gate_is_explained_not_just_applied():
    caps = build_driver_captions(_ra())
    assert "0.91" in caps["data_confidence"]


# ── they must not overclaim ───────────────────────────────────────────────

def test_no_peer_claim_when_no_cohort_ran():
    caps = build_driver_captions(_ra())
    assert "no peer cohort" in caps["vigor"]
    assert "peer-scored" not in caps["vigor"]


def test_peer_claim_appears_only_once_a_cohort_exists():
    ra = _ra(vigor={"score": 70.0, "inputs": {
        "mean_yield_potential": 70.0, "mean_peak_cvi": 0.70,
        "n_cycles_scored": 3, "peer_relative": True,
        "n_cycles_peer_scored": 3}})
    assert "peer-scored" in build_driver_captions(ra)["vigor"]


def test_absence_of_stress_on_uncultivated_land_is_not_called_stability():
    ra = _ra(stability={"score": 25.0, "inputs": {
        "anomalies_high": 0, "anomalies_medium": 0, "anomalies_low": 0,
        "n_seasons_observed": 0, "no_cultivation_evidence": True}})
    caption = build_driver_captions(ra)["stability"]
    assert "not evidence of stability" in caption


def test_no_vigour_evidence_is_stated_plainly():
    ra = _ra(vigor={"score": 25.0, "inputs": {
        "mean_yield_potential": 25.0, "n_cycles_scored": 0}})
    assert "no vigour evidence" in build_driver_captions(ra)["vigor"]


def test_inferred_fallow_is_labelled_as_inferred():
    ra = _ra(landuse={"score": 9.0, "inputs": {
        "n_complete_cycles": 0, "years": 3.0, "cycles_per_year": 0.0,
        "season_coverage": 0.0, "fallow_fraction": 1.0,
        "fallow_basis": "inferred_no_cycles"}, "drivers": {}})
    assert "no cycles detected" in build_driver_captions(ra)["landuse"]


# ── perennial branch ──────────────────────────────────────────────────────

def test_a_perennial_is_described_on_perennial_terms():
    ra = _ra(landuse={"score": 88.0, "inputs": {
        "cycle_kind": "perennial", "n_production_years": 3,
        "cycles_per_year": None, "fallow_fraction": None},
        "drivers": {"canopy_persistence": 92.0, "production_continuity": 100.0,
                    "inter_annual_stability": 78.0}})
    caption = build_driver_captions(ra)["landuse"]
    assert "Perennial" in caption
    assert "production year" in caption
    # Cycles-per-year is meaningless here and must not be quoted.
    assert "cycles per year" not in caption


# ── footprint caveat travels with the explanation ─────────────────────────

def test_a_substituted_footprint_is_called_out():
    ra = _ra()
    ra["footprint"] = {"geometry_substituted": True,
                       "geometry_source": "polygon_rejected_fallback_point"}
    caps = build_driver_captions(ra)
    assert "footprint" in caps
    assert "re-draw" in caps["footprint"].lower()


def test_a_clean_footprint_adds_no_caveat():
    ra = _ra()
    ra["footprint"] = {"geometry_substituted": False, "geometry_source": "polygon"}
    assert "footprint" not in build_driver_captions(ra)


# ── degenerate input ──────────────────────────────────────────────────────

def test_no_risk_assessment_yields_no_captions():
    """An empty set is honest; generic sentences would not be."""
    assert build_driver_captions(None) == {}
    assert build_driver_captions({}) == {}


def test_missing_inputs_do_not_raise():
    ra = {"sub_indices": {"landuse": {"score": 50.0}}}
    caps = build_driver_captions(ra)
    assert isinstance(caps, dict)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
