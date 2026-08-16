"""
Tests for api/report_payload.py

The report is where a number stops being an internal figure and becomes
something a lender acts on, so the tests here are mostly about what the payload
REFUSES to assert: no invented policy action, no synthesised peer line, no
trend against a baseline that does not exist, and no loan amount.
"""

from __future__ import annotations

import pytest

from api.report_payload import (
    KBS_BANDS, build_report_payload, index_to_kbs, kbs_band,
)


def _assessment(index=62.0, **extra):
    doc = {
        "farmer_id": "FARMER_001",
        "assessment_date": "2026-08-15T09:00:00",
        "status": "SUCCESS",
        "pipeline_version": "5.0",
        "index_version": "index_v5",
        "risk_assessment": {
            "index_score": index, "raw_index": 71.0, "risk_category": "MEDIUM",
            "confidence_gate": 0.87, "index_version": "index_v5",
            "weights": {"landuse": 30, "vigor": 25, "stability": 20, "weather": 25},
            "sub_indices": {
                "landuse": {"score": 68.0}, "vigor": {"score": 54.0},
                "stability": {"score": 61.0}, "weather": {"score": 59.0},
                "data_confidence": {"score": 74.0},
            },
            "weak_sub_indices": ["vigor"],
            "reason_codes": [{"code": "VIGOR_WEAK", "message": "x", "polarity": "negative"}],
            "driver_captions": {"landuse": "Driven by 3 cycle(s).",
                                "data_confidence": "85% usable."},
        },
    }
    doc.update(extra)
    return doc


def _evidence():
    return {
        "window": {"start_date": "2023-06-01", "end_date": "2026-06-01", "n_bins": 4},
        "series": {
            "dates": ["2023-06-01", "2023-06-11", "2023-06-21", "2023-07-01"],
            "ndvi": [0.31, 0.48, None, 0.72],
            "signal_source": ["optical", "optical", "imputed", "fused"],
        },
        "series_completeness": {"ndvi": {"n": 4, "n_present": 3}},
    }


# ── the scale decided in D-3 ──────────────────────────────────────────────

def test_kbs_uses_the_300_900_scale_with_four_bands():
    assert index_to_kbs(0) == 300
    assert index_to_kbs(100) == 900
    assert index_to_kbs(50) == 600
    assert len(KBS_BANDS) == 4


def test_band_lookup_covers_the_whole_scale():
    for score in range(0, 101, 5):
        assert kbs_band(index_to_kbs(score)) is not None


def test_a_top_score_lands_in_the_top_band():
    assert kbs_band(index_to_kbs(100))["name"] == "Excellent"


# ── what it must never assert ─────────────────────────────────────────────

def test_no_loan_amount_anywhere():
    p = build_report_payload(_assessment())
    blob = str(p).lower()
    for forbidden in ("recommended_limit", "credit_limit", "interest_rate", "emi"):
        assert forbidden not in blob
    assert p["score"]["no_repayment_calibration"] is True


def test_the_policy_action_panel_is_declared_omitted_not_faked():
    """
    The design's most persuasive panel — 'defer 30 days, threshold 55' — has no
    engine behind it. Omitting it silently would look like missing data.
    """
    p = build_report_payload(_assessment())
    assert "policy_action" in p["omitted"]
    assert "invented" in p["omitted"]["policy_action"]


def test_no_district_median_is_synthesised():
    p = build_report_payload(_assessment(), evidence=_evidence())
    traj = p["ndvi_trajectory"]
    assert traj["comparison_available"] is False
    assert "warm" in traj["comparison_note"]


def test_a_first_assessment_shows_no_trend():
    """A trend tile against a baseline that does not exist invents a history."""
    p = build_report_payload(_assessment(), score_history=[])
    assert p["trend"] is None


def test_a_single_prior_point_is_not_a_trend():
    history = [{"index_score": 62.0, "assessment_date": "2026-08-15"}]
    assert build_report_payload(_assessment(), score_history=history)["trend"] is None


def test_a_real_trend_is_reported_with_direction():
    history = [
        {"index_score": 70.0, "assessment_date": "2026-04-01"},
        {"index_score": 62.0, "assessment_date": "2026-08-15"},
    ]
    trend = build_report_payload(_assessment(), score_history=history)["trend"]
    assert trend["direction"] == "down"
    assert trend["delta"] < 0
    assert trend["previous_kbs"] == index_to_kbs(70.0)


# ── PII stays at the edge ─────────────────────────────────────────────────

def test_the_payload_never_reaches_for_farmer_pii_itself():
    """
    Masking policy is unsettled (D-5), so identity is supplied by the caller
    rather than read from the database here.
    """
    p = build_report_payload(_assessment())
    assert p["farmer"] == {}

    supplied = build_report_payload(
        _assessment(), farmer={"name": "A. Kumar", "village": "X"}
    )
    assert supplied["farmer"]["name"] == "A. Kumar"


# ── content ───────────────────────────────────────────────────────────────

def test_sub_indices_carry_weight_weakness_and_caption():
    p = build_report_payload(_assessment())
    landuse = next(s for s in p["sub_indices"] if s["key"] == "landuse")
    vigor = next(s for s in p["sub_indices"] if s["key"] == "vigor")
    assert landuse["weight"] == 30
    assert landuse["caption"]
    assert vigor["is_weakest"] is True


def test_captions_are_regenerated_for_older_records():
    """A record stored before captions existed must still render."""
    doc = _assessment()
    doc["risk_assessment"].pop("driver_captions")
    p = build_report_payload(doc)
    assert any(s["caption"] for s in p["sub_indices"])


def test_the_ndvi_trajectory_carries_provenance_per_point():
    traj = build_report_payload(_assessment(), evidence=_evidence())["ndvi_trajectory"]
    assert traj["signal_source"][2] == "imputed"
    assert traj["ndvi"][2] is None
    assert traj["n_present"] == 3 and traj["n_total"] == 4


def test_a_ragged_series_is_omitted_rather_than_drawn_wrong():
    ev = _evidence()
    ev["series"]["ndvi"] = [0.3, 0.4]  # shorter than dates
    assert build_report_payload(_assessment(), evidence=ev)["ndvi_trajectory"] is None


def test_gates_and_verification_are_surfaced():
    doc = _assessment(
        land_cover={"class": "CROPLAND", "outcome": "pass"},
        parcel_viability={"outcome": "viable"},
        cropping_analysis={"crop_verification": {"outcome": "consistent"}},
    )
    p = build_report_payload(doc)
    assert p["land_cover"]["class"] == "CROPLAND"
    assert p["parcel_viability"]["outcome"] == "viable"
    assert p["crop_verification"]["outcome"] == "consistent"


def test_narrative_provenance_travels_with_the_text():
    doc = _assessment(ai_enrichment={
        "english_narrative": "text", "narrative_source": "groq",
        "model_snapshot": {"model": "llama-3.3-70b-versatile", "prompt_hash": "abc"},
    })
    n = build_report_payload(doc)["narrative"]
    assert n["text"] == "text"
    assert n["model"]["model"] == "llama-3.3-70b-versatile"


# ── integrity ─────────────────────────────────────────────────────────────

def test_the_content_hash_is_stable_across_renders():
    """A hash that changes every render proves nothing."""
    a = build_report_payload(_assessment())
    b = build_report_payload(_assessment())
    assert a["integrity"]["content_sha256"] == b["integrity"]["content_sha256"]


def test_the_content_hash_changes_when_the_score_changes():
    a = build_report_payload(_assessment(index=62.0))
    b = build_report_payload(_assessment(index=71.0))
    assert a["integrity"]["content_sha256"] != b["integrity"]["content_sha256"]


def test_the_report_id_is_deterministic():
    a = build_report_payload(_assessment())
    b = build_report_payload(_assessment())
    assert a["report_id"] == b["report_id"]
    assert a["report_id"].startswith("KBS-")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
