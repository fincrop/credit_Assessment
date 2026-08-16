"""
Tests for assessment/evidence_snapshot.py

These assert the properties that make the evidence record trustworthy:
series stay aligned to their date axis, imputed bins remain distinguishable
from observed ones, nothing numpy or NaN reaches the driver, and a run with no
score produces no trend point.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from assessment.evidence_snapshot import (
    build_evidence_document,
    build_score_history_entry,
)


def _assessment(n_bins: int = 6, with_cycles: bool = True) -> dict:
    """A trimmed but structurally faithful pipeline output."""
    dates = [f"2023-06-{2 + i * 10:02d}" for i in range(n_bins)]
    ndvi = [0.31, 0.48, float("nan"), 0.72, 0.61, 0.29]
    return {
        "farmer_id": "FARMER_001",
        "status": "SUCCESS",
        "pipeline_version": "5.0",
        "index_version": "index_v5",
        "assessment_date": datetime(2026, 8, 15, 9, 0),
        "field_area_ha": 1.24,
        "location": {"latitude": 17.385, "longitude": 78.487, "region": "UNCLASSIFIED"},
        "cohort_key": "DECCAN|KHARIF|NA",
        "satellite_provider": "gee",
        "satellite_data": {
            "continuous_data": {
                "start_date": "2023-06-02",
                "end_date": "2023-07-22",
                "interval_days": 10,
                "dates": dates,
                "ndvi_values": np.array(ndvi[:n_bins]),
                "evi_values": [0.2, 0.3, None, 0.5, 0.4, 0.19][:n_bins],
                "ndwi_values": [-0.3] * n_bins,
                "nirv_values": np.array([0.1] * n_bins),
                "vs_smooth": [0.3, 0.45, 0.55, 0.7, 0.6, 0.3][:n_bins],
                "bin_quality": [np.float64(0.8)] * n_bins,
                "signal_source": ["optical", "optical", "imputed", "fused", "sar", "optical"][:n_bins],
                "signal_quality_summary": {"valid_fraction": 0.83, "n_bins": n_bins},
            }
        },
        "crop_cycles": {
            "cycles": (
                [{
                    "sowing_date": "2023-06-12", "harvest_date": "2023-10-30",
                    "duration_days": 140, "peak_ndvi": 0.72, "season_type": "kharif",
                    "phenology": {"fit_ok": True, "r2": 0.91,
                                  "sos": "2023-06-15", "pos": "2023-08-20",
                                  "eos": "2023-10-25"},
                }] if with_cycles else []
            ),
            "utilization_metrics": {"land_utilization_index": 0.62, "crops_per_year": 1.7},
        },
        "cycle_detection_diag": {"cycles_found": 1, "signal_source_used": "composite_vs"},
        "cropping_analysis": {
            "dominant_crop": "Cotton",
            "crop_label_source": "registry_self_report",
            "classification_enabled": False,
        },
        "performance_analysis": {
            "seasonal_performance": [{
                "season": "cycle_1", "season_type": "kharif",
                "yield_potential_score": 68.0, "health_score": 71.0,
                "yield_detail": {"nirv_auc_mean": np.float64(0.42), "peak_cvi": 0.66,
                                 "yield_index_basis": "internal_cvi_auc"},
                "health_detail": {"cv_cvi": 0.18, "arc_score": 74.0},
                "anomaly_events": [{"type": "water_stress", "date": "2023-08-14",
                                    "stage": "FLOWERING", "impact": "HIGH",
                                    "z_score": np.float64(-2.1)}],
            }],
        },
        "weather_analysis": {
            "seasonal_weather": [{
                "cycle_id": "cycle_1", "season_type": "kharif",
                "total_rainfall_mm": 612.0, "max_temp_c": 41.2,
                "weather_indicators": {"gdd_total": 1842, "max_dry_spell_days": 12,
                                       "spi_like": np.float64(-1.1),
                                       "water_balance_mm": -88.0},
            }],
            "forward_exposure": {"exposure_score": 33.0},
            "backward_resilience": {"mean_resilience_score": 71.4},
        },
        "risk_assessment": {
            "index_score": 62.4, "raw_index": 71.7, "risk_category": "MEDIUM",
            "confidence_gate": 0.898, "index_version": "index_v5",
            "weights": {"landuse": 30, "vigor": 25, "stability": 20, "weather": 25},
            "sub_indices": {"landuse": {"score": 68.2}, "vigor": {"score": 54.1},
                            "stability": {"score": 61.0}, "weather": {"score": 58.9},
                            "data_confidence": {"score": 74.6}},
            "weak_sub_indices": [],
            "calibration": {"subindex_inputs": {"landuse": {"cycles_per_year": 1.7}}},
        },
    }


# ── the data that used to be thrown away is now kept ──────────────────────

def test_series_are_persisted_columnar_and_aligned():
    doc = build_evidence_document(_assessment(), assessment_id="A1")
    series = doc["series"]
    n = len(series["dates"])
    for name in ("ndvi", "evi", "ndwi", "nirv", "vs_smooth", "bin_quality"):
        assert name in series, f"{name} series missing"
        assert len(series[name]) == n, f"{name} not aligned to the date axis"


def test_misaligned_series_is_dropped_not_stored_ragged():
    a = _assessment()
    a["satellite_data"]["continuous_data"]["evi_values"] = [0.1, 0.2]  # wrong length
    doc = build_evidence_document(a)
    assert "evi" not in doc["series"], "a ragged series must be dropped, not stored"
    assert "ndvi" in doc["series"], "other series must be unaffected"


def test_explainable_detail_survives():
    doc = build_evidence_document(_assessment())
    season = doc["performance_seasons"][0]
    assert season["yield_detail"]["peak_cvi"] == 0.66
    assert season["health_detail"]["arc_score"] == 74.0
    # The per-event stress narrative that previously collapsed to three counts.
    assert season["anomaly_events"][0]["stage"] == "FLOWERING"
    assert season["anomaly_events"][0]["date"] == "2023-08-14"

    wx = doc["weather_cycles"][0]
    assert wx["weather_indicators"]["gdd_total"] == 1842
    assert wx["weather_indicators"]["max_dry_spell_days"] == 12


def test_phenology_survives():
    doc = build_evidence_document(_assessment())
    ph = doc["cycles"][0]["phenology"]
    assert ph["sos"] == "2023-06-15" and ph["eos"] == "2023-10-25"
    assert ph["r2"] == 0.91


# ── provenance: measured vs reconstructed ─────────────────────────────────

def test_provenance_distinguishes_observed_from_imputed():
    doc = build_evidence_document(_assessment())
    prov = doc["provenance"]
    assert prov["n_bins"] == 6
    assert prov["n_imputed_bins"] == 1, "an imputed bin must remain identifiable"
    assert prov["n_sar_only_bins"] == 1
    assert prov["n_observed_bins"] == 4  # 3 optical + 1 fused
    assert prov["observed_fraction"] == pytest.approx(4 / 6, abs=1e-3)


def test_signal_source_is_stored_alongside_the_values():
    doc = build_evidence_document(_assessment())
    assert doc["series"]["signal_source"][2] == "imputed"
    assert doc["series"]["ndvi"][2] is None, "the imputed bin had no NDVI observation"


def test_completeness_reports_sample_size_per_series():
    """Rule P-3: no statistic without its sample size."""
    doc = build_evidence_document(_assessment())
    ndvi = doc["series_completeness"]["ndvi"]
    assert ndvi["n"] == 6
    assert ndvi["n_present"] == 5, "the NaN bin must not count as present"
    assert ndvi["fraction_present"] == pytest.approx(5 / 6, abs=1e-3)


# ── encoding safety ───────────────────────────────────────────────────────

def test_document_is_mongo_safe():
    doc = build_evidence_document(_assessment())

    def walk(v, path="doc"):
        if isinstance(v, dict):
            for k, x in v.items():
                assert isinstance(k, str), f"non-string key at {path}"
                walk(x, f"{path}.{k}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        else:
            assert not isinstance(v, (np.generic, np.ndarray)), f"numpy at {path}"
            if isinstance(v, float):
                assert math.isfinite(v), f"non-finite float at {path}"

    walk(doc)


def test_nan_became_none_not_zero():
    doc = build_evidence_document(_assessment())
    assert doc["series"]["ndvi"][2] is None
    assert 0.0 not in doc["series"]["ndvi"][2:3]


# ── refusing to write meaningless records ─────────────────────────────────

def test_returns_none_when_there_is_nothing_observed():
    """An empty evidence doc would claim we looked and saw nothing."""
    empty = {"farmer_id": "F", "satellite_data": {}, "crop_cycles": {}}
    assert build_evidence_document(empty) is None


def test_cycles_only_assessment_still_produces_evidence():
    a = _assessment()
    a["satellite_data"] = {}
    assert build_evidence_document(a) is not None


# ── score history ─────────────────────────────────────────────────────────

def test_score_history_entry_is_flat_and_complete():
    e = build_score_history_entry(_assessment(), assessment_id="A1", plot_key="plot_1")
    assert e["index_score"] == 62.4
    assert e["risk_category"] == "MEDIUM"
    assert e["plot_key"] == "plot_1" and e["scope"] == "plot"
    assert e["sub_index_scores"]["landuse"] == 68.2
    # Weights are stored per row so an old point stays interpretable after a
    # weighting change.
    assert e["weights"]["landuse"] == 30


def test_scope_is_farmer_when_no_plot_key():
    e = build_score_history_entry(_assessment())
    assert e["scope"] == "farmer" and e["plot_key"] is None


def test_failed_run_produces_no_trend_point():
    a = _assessment()
    a["risk_assessment"] = {}
    assert build_score_history_entry(a) is None, (
        "a failed run must not appear on the trend line as a zero"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
