"""
Report payload
==============
Assembles everything the Farmer Assessment Report renders, from the assessment,
its evidence record and the score history.

WHAT THIS DELIBERATELY DOES NOT INCLUDE
───────────────────────────────────────
The supplied design has panels this pipeline cannot honestly fill, and filling
them is worse than omitting them because a number in a report gets used:

  * "Suggested action — defer 30 days, committee threshold 55, re-assess
    11 Oct, projected band Standard". There is no policy engine and no
    validated forecast behind any of that. Dropped (F-12 / D-9).
  * District-median NDVI on the trajectory chart. Real once a peer cohort is
    warm; until then the parcel's own line is drawn alone rather than against
    a synthesised comparison.
  * Reviewed-by / review status. No review workflow exists.
  * Farmer PII beyond what the caller supplies. Masking policy is unsettled
    (D-5), so this module never reaches into farm_info for Aadhaar or mobile.

The result is materially thinner than the mockup. That is the correct outcome.

SCALE
─────
KBS is 300-900 over four agronomic bands (decision D-3), matching the existing
gauge. The design's 300-950 / five credit-policy bands were not adopted.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from assessment.driver_captions import build_driver_captions
from assessment.legacy_credit_shim import sub_index_score
from utils.mongo_encoding import as_utc, utc_now

__all__ = ["build_report_payload", "REPORT_PAYLOAD_VERSION", "KBS_BANDS"]

REPORT_PAYLOAD_VERSION = "report_payload_v1"

# 300-900, four equal bands — the scale the existing KbsGauge renders.
KBS_MIN, KBS_MAX = 300, 900
KBS_BANDS = (
    ("Poor", 300, 450),
    ("Fair", 450, 600),
    ("Good", 600, 750),
    ("Excellent", 750, 900),
)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def index_to_kbs(index_score: Optional[float]) -> Optional[int]:
    """Map the 0-100 agronomic index onto the 300-900 KBS scale."""
    v = _num(index_score)
    if v is None:
        return None
    v = max(0.0, min(100.0, v))
    return int(round(KBS_MIN + (KBS_MAX - KBS_MIN) * v / 100.0))


def kbs_band(kbs: Optional[int]) -> Optional[Dict]:
    if kbs is None:
        return None
    for name, lo, hi in KBS_BANDS:
        if lo <= kbs < hi or (name == "Excellent" and kbs >= hi):
            return {"name": name, "min": lo, "max": hi}
    return {"name": KBS_BANDS[0][0], "min": KBS_BANDS[0][1], "max": KBS_BANDS[0][2]}


def _report_id(farmer_id: str, assessment_date: Any) -> str:
    stamp = str(assessment_date or "")[:19].replace("-", "").replace(":", "").replace("T", "")
    digest = hashlib.sha256(f"{farmer_id}|{stamp}".encode("utf-8")).hexdigest()[:8]
    return f"KBS-{stamp[:8] or '00000000'}-{digest.upper()}"


def _content_hash(payload: Dict) -> str:
    """
    Deterministic hash over the report content, for the integrity line.

    Excludes generated_at so the same assessment always hashes the same — a
    hash that changes on every render proves nothing.
    """
    body = {k: v for k, v in payload.items() if k not in ("generated_at", "integrity")}
    blob = json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _trend(history: Optional[List[Dict]], current_kbs: Optional[int]) -> Optional[Dict]:
    """
    Movement against the previous assessment.

    Returns None on a first assessment. A trend tile showing "0" or "—" against
    a baseline that does not exist would invent a history (rule P-1).
    """
    if not history or current_kbs is None:
        return None
    prior = [h for h in history if h.get("index_score") is not None]
    if len(prior) < 2:
        return None
    previous = prior[-2]
    prev_kbs = index_to_kbs(previous.get("index_score"))
    if prev_kbs is None:
        return None
    return {
        "previous_kbs": prev_kbs,
        "previous_date": previous.get("assessment_date"),
        "delta": current_kbs - prev_kbs,
        "direction": (
            "up" if current_kbs > prev_kbs
            else "down" if current_kbs < prev_kbs else "flat"
        ),
    }


def _ndvi_series(evidence: Optional[Dict]) -> Optional[Dict]:
    """
    The parcel's own NDVI trajectory, with provenance per point.

    No district median: that requires a warm peer cohort, and a synthesised
    comparison line would be a fabrication in the most visually persuasive part
    of the report.
    """
    if not isinstance(evidence, dict):
        return None
    series = evidence.get("series") or {}
    dates = series.get("dates") or []
    ndvi = series.get("ndvi") or []
    if not dates or not ndvi or len(dates) != len(ndvi):
        return None
    completeness = (evidence.get("series_completeness") or {}).get("ndvi") or {}
    return {
        "dates": dates,
        "ndvi": ndvi,
        "signal_source": series.get("signal_source"),
        "n_present": completeness.get("n_present"),
        "n_total": completeness.get("n"),
        "comparison_available": False,
        "comparison_note": (
            "No peer comparison is shown: a district median needs a warm "
            "cohort of assessed parcels in this zone."
        ),
    }


def build_report_payload(
    assessment: Dict,
    *,
    evidence: Optional[Dict] = None,
    score_history: Optional[List[Dict]] = None,
    farmer: Optional[Dict] = None,
    prepared_by: Optional[Dict] = None,
) -> Dict:
    """
    Assemble the report contract.

    `farmer` and `prepared_by` are supplied by the caller rather than read from
    the database, so PII handling stays a decision made at the edge, where the
    masking policy lives.
    """
    risk = assessment.get("risk_assessment") or assessment.get("farmer_level") or {}
    status = str(assessment.get("status", "")).upper()

    index_score = _num(risk.get("index_score"))
    kbs = index_to_kbs(index_score)
    band = kbs_band(kbs)
    subs = risk.get("sub_indices") or {}

    payload: Dict[str, Any] = {
        "payload_version": REPORT_PAYLOAD_VERSION,
        "generated_at": utc_now(),
        "farmer_id": assessment.get("farmer_id"),
        "assessment_date": as_utc(assessment.get("assessment_date")),
        "report_id": _report_id(
            str(assessment.get("farmer_id", "")), assessment.get("assessment_date")
        ),
        "status": status,

        # ── Identity: only what the caller passed in ──────────────────────
        "farmer": farmer or {},
        "prepared_by": prepared_by or {},

        # ── Score ────────────────────────────────────────────────────────
        "score": {
            "kbs": kbs,
            "scale_min": KBS_MIN,
            "scale_max": KBS_MAX,
            "band": band,
            "bands": [{"name": n, "min": lo, "max": hi} for n, lo, hi in KBS_BANDS],
            "index_score": index_score,
            "raw_index": _num(risk.get("raw_index")),
            "risk_category": risk.get("risk_category"),
            "confidence_gate": _num(risk.get("confidence_gate")),
            "index_version": risk.get("index_version") or assessment.get("index_version"),
            # Never a loan amount. Stated in the payload so a renderer cannot
            # accidentally present it as one.
            "positioning": "agronomic_risk_index",
            "no_repayment_calibration": True,
        },
        "trend": _trend(score_history, kbs),

        # ── Sub-indices with their grounded captions ─────────────────────
        "sub_indices": [
            {
                "key": key,
                "score": sub_index_score(subs.get(key)),
                "weight": _num((risk.get("weights") or {}).get(key)),
                "is_weakest": key in (risk.get("weak_sub_indices") or []),
                "caption": (risk.get("driver_captions") or {}).get(key),
            }
            for key in ("landuse", "vigor", "stability", "weather")
            if key in subs
        ],
        "data_confidence": {
            "score": sub_index_score(subs.get("data_confidence")),
            "gate": _num(risk.get("confidence_gate")),
            "caption": (risk.get("driver_captions") or {}).get("data_confidence"),
        },
        "reason_codes": risk.get("reason_codes") or [],

        # ── Evidence ─────────────────────────────────────────────────────
        "ndvi_trajectory": _ndvi_series(evidence),
        "land_cover": assessment.get("land_cover"),
        "parcel_viability": assessment.get("parcel_viability"),
        "data_sufficiency": assessment.get("data_sufficiency"),
        "crop_verification": (assessment.get("cropping_analysis") or {}).get(
            "crop_verification"
        ),
        "footprint": risk.get("footprint"),

        # ── Narrative, and where it came from ────────────────────────────
        "narrative": {
            "text": (assessment.get("ai_enrichment") or {}).get("english_narrative"),
            "source": (assessment.get("ai_enrichment") or {}).get("narrative_source"),
            "translated": (assessment.get("ai_enrichment") or {}).get(
                "translated_narrative"
            ),
            "translation_language": (assessment.get("ai_enrichment") or {}).get(
                "translation_language"
            ),
            "model": (assessment.get("ai_enrichment") or {}).get("model_snapshot"),
        },

        # ── Provenance ───────────────────────────────────────────────────
        "methodology": {
            "pipeline_version": assessment.get("pipeline_version"),
            "index_version": assessment.get("index_version"),
            "signal_version": (assessment.get("signal_quality_summary") or {}).get(
                "signal_version"
            ),
            "satellite_provider": assessment.get("satellite_provider"),
            "cloud_mask_version": assessment.get("cloud_mask_version"),
            "window": (evidence or {}).get("window"),
            "weights": risk.get("weights"),
            "weights_note": "Expert-weighted (AHP-style); provisional, pending sign-off.",
        },

        # Panels the design has that we cannot honestly fill. Declared rather
        # than silently missing, so a renderer knows the difference between
        # "no data yet" and "we do not do this".
        "omitted": {
            "policy_action": (
                "No policy engine exists. A recommendation, committee threshold "
                "or re-assessment date would be invented."
            ),
            "peer_comparison": (
                "Requires a warm cohort of assessed parcels in this agro-zone."
            ),
            "review_workflow": "No reviewer workflow exists.",
        },
    }

    # Captions are recomputed if the stored assessment predates them, so an
    # older record still renders rather than showing empty explanation slots.
    if not any(s.get("caption") for s in payload["sub_indices"]):
        caps = build_driver_captions(risk)
        for s in payload["sub_indices"]:
            s["caption"] = caps.get(s["key"])
        payload["data_confidence"]["caption"] = caps.get("data_confidence")

    payload["integrity"] = {
        "content_sha256": _content_hash(payload),
        "note": "Hash covers report content, excluding the generation timestamp.",
    }
    return payload
