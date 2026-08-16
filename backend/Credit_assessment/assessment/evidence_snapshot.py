"""
Evidence snapshot
=================
Builds the ``assessment_evidence`` document: the observational record behind a
score.

WHY THIS EXISTS
───────────────
The pipeline computes far more than it stores. Before this module, the full
per-bin index series, per-cycle phenology, per-season stress events and the
agronomic weather indicators survived in exactly two places:

* ``jobs.result`` — a queue collection with no schema and no retention policy,
  which the frontend reads as a fallback; and
* ``satellite_stats_cache`` — a **30-day TTL** blob keyed by an opaque hash with
  no queryable ``farmer_id``.

So after 30 days the evidence behind a score was gone permanently. That makes
three things impossible: explaining an old score, comparing a farmer to their
own history, and — most importantly — validating the model retrospectively once
field data eventually exists. You cannot check a past assessment against ground
truth you collect later if you did not keep what the assessment saw.

This document is the durable record. It is written once per scored parcel and is
never mutated.

SHAPE
─────
Series are stored **columnar** (one array per index, parallel to ``dates``)
rather than as a list of per-bin dicts. That is roughly 5-10x smaller for the
same information and is the natural shape for charting.

Every series carries its provenance alongside it — ``signal_source`` marks each
bin as observed, fused, SAR-derived or imputed, and ``missing`` marks gaps.
A consumer must never have to guess whether a value was measured or invented.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from assessment.legacy_credit_shim import sub_index_score
from utils.mongo_encoding import count_nulls, to_mongo, utc_now

logger = logging.getLogger(__name__)

EVIDENCE_SCHEMA_VERSION = "evidence_v1"

# continuous_data key -> series name in the stored document.
# Mirrors SatelliteDataCollector output; anything absent is simply skipped.
_SERIES_MAP = (
    ("ndvi_values",   "ndvi"),
    ("evi_values",    "evi"),
    ("ndmi_values",   "ndmi"),
    ("ndwi_values",   "ndwi"),
    ("psri_values",   "psri"),
    ("ndre_values",   "ndre"),
    ("msavi2_values", "msavi2"),
    ("nirv_values",   "nirv"),
    ("kndvi_values",  "kndvi"),
    ("lswi_values",   "lswi"),
    ("gcvi_values",   "gcvi"),
    ("rvi_values",    "rvi"),          # SAR, GEE path only
    ("vs_values",     "vs"),           # composite signal, pre-smoothing
    ("vs_smooth",     "vs_smooth"),    # Whittaker output
    ("bin_quality",   "bin_quality"),
    ("signal_source", "signal_source"),
)

# Per-season performance keys worth keeping. yield_detail / health_detail /
# anomaly_events are the expensive, explainable parts that were being reduced to
# three integer counts before reaching storage.
_PERF_KEYS = (
    "season", "season_type", "year", "crop", "is_active_cycle",
    "yield_potential_score", "health_score", "overall_performance",
    "scoring_method", "yield_index_basis", "cultivation_signal",
    "performance_narrative", "yield_detail", "health_detail",
    "anomaly_events", "peer_benchmarking", "stress_baseline",
)

# Per-cycle weather keys worth keeping. weather_indicators holds GDD, PET, water
# balance, dry/wet spells and SPI/SPEI — none of which reached storage before.
_WEATHER_KEYS = (
    "cycle_id", "season_type", "season_label", "crop",
    "start_date", "end_date", "duration_days",
    "total_rainfall_mm", "avg_temp_c", "max_temp_c", "min_temp_c",
    "avg_humidity", "avg_wind_ms", "rainfall_vs_norm_pct",
    "weather_indicators", "weather_thresholds_used", "weather_threshold_mode",
    "resilience", "extreme_events",
)


def _pick(src: Optional[Dict], keys) -> Dict:
    """Copy the listed keys when present. Absent keys stay absent, not null."""
    if not isinstance(src, dict):
        return {}
    return {k: src[k] for k in keys if k in src and src[k] is not None}


def _series_block(continuous: Dict) -> Dict:
    """Columnar series plus a per-series completeness count."""
    dates = continuous.get("dates") or []
    n = len(dates)
    series: Dict[str, Any] = {"dates": list(dates)}
    completeness: Dict[str, Any] = {}

    for src_key, name in _SERIES_MAP:
        values = continuous.get(src_key)
        if values is None:
            continue
        values = list(values)
        if n and len(values) != n:
            # Never store a series that is not aligned to the date axis — a
            # silently misaligned chart is worse than a missing one.
            logger.warning(
                "evidence: series %r length %d != %d dates; skipping",
                name, len(values), n,
            )
            continue
        encoded = to_mongo(values)
        series[name] = encoded
        # Rule P-3: no statistic without its sample size. NaN encodes to None,
        # so this is the count of bins that carried no observation.
        if name not in ("signal_source",):
            nulls = count_nulls(encoded)
            completeness[name] = {
                "n": n,
                "n_present": n - nulls,
                "fraction_present": round((n - nulls) / n, 4) if n else None,
            }

    return {"series": series, "series_completeness": completeness}


def _provenance(continuous: Dict, dates_len: int) -> Dict:
    """
    How much of this record was observed rather than reconstructed.

    signal_source values: optical | fused | sar | imputed.
    """
    sources = continuous.get("signal_source") or []
    counts: Dict[str, int] = {}
    for s in sources:
        key = str(s) if s is not None else "unknown"
        counts[key] = counts.get(key, 0) + 1

    observed = counts.get("optical", 0) + counts.get("fused", 0)
    return {
        "signal_source_counts": counts,
        "n_bins": dates_len,
        "n_observed_bins": observed,
        "observed_fraction": (
            round(observed / dates_len, 4) if dates_len else None
        ),
        "n_imputed_bins": counts.get("imputed", 0),
        "n_sar_only_bins": counts.get("sar", 0),
    }


def build_evidence_document(
    assessment: Dict,
    *,
    assessment_id: Optional[str] = None,
    plot_key: Optional[str] = None,
) -> Optional[Dict]:
    """
    Assemble the durable evidence record for one scored parcel.

    Returns None when there is nothing worth storing (no satellite series and no
    cycles) — an empty evidence document would imply we looked and found
    nothing, which is a different claim from not having looked.
    """
    satellite = assessment.get("satellite_data") or {}
    continuous = satellite.get("continuous_data") or {}
    dates = continuous.get("dates") or []
    cycles = ((assessment.get("crop_cycles") or {}).get("cycles")) or []

    if not dates and not cycles:
        return None

    perf = assessment.get("performance_analysis") or {}
    weather = assessment.get("weather_analysis") or {}
    cropping = assessment.get("cropping_analysis") or {}
    risk = assessment.get("risk_assessment") or {}

    doc: Dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "assessment_id": assessment_id,
        "farmer_id": assessment.get("farmer_id"),
        "plot_key": plot_key,
        "index_version": assessment.get("index_version") or risk.get("index_version"),
        "pipeline_version": assessment.get("pipeline_version"),
        "created_at": utc_now(),
        "assessment_date": assessment.get("assessment_date"),

        # What was looked at, and where.
        "window": {
            "start_date": continuous.get("start_date"),
            "end_date": continuous.get("end_date"),
            "interval_days": continuous.get("interval_days"),
            "n_bins": len(dates),
        },
        "location": assessment.get("location"),
        "field_area_ha": assessment.get("field_area_ha"),
        "geospatial_prep": assessment.get("geospatial_prep"),
        "satellite_provider": assessment.get("satellite_provider"),
        "cloud_mask_version": assessment.get("cloud_mask_version"),
        "indices_available": assessment.get("indices_available"),
        "indices_sparse": assessment.get("indices_sparse"),
        "signal_quality_summary": assessment.get("signal_quality_summary")
                                  or continuous.get("signal_quality_summary"),

        # Phenology: full cycle objects including SOS/POS/EOS and fit quality.
        "cycles": cycles,
        "cycle_detection_diag": assessment.get("cycle_detection_diag"),
        "utilization_metrics": (assessment.get("crop_cycles") or {}).get(
            "utilization_metrics"
        ),

        # Crop context, with its provenance — a declared crop is not a detected one.
        "crop_context": {
            "predicted_crop": cropping.get("dominant_crop"),
            "crop_label_source": cropping.get("crop_label_source"),
            "detection_mode": cropping.get("detection_mode"),
            "classification_enabled": cropping.get("classification_enabled"),
            "crop_intelligence_source": assessment.get("crop_intelligence_source"),
        },

        # The explainable detail that previously collapsed to integer counts.
        "performance_seasons": [
            _pick(p, _PERF_KEYS) for p in (perf.get("seasonal_performance") or [])
        ],
        "weather_cycles": [
            _pick(w, _WEATHER_KEYS) for w in (weather.get("seasonal_weather") or [])
        ],
        "weather_meta": {
            "analysis_mode": weather.get("analysis_mode"),
            "weather_sources_used": weather.get("weather_sources_used"),
            "weather_fetch_stats": weather.get("weather_fetch_stats"),
            "forward_exposure": weather.get("forward_exposure"),
            "backward_resilience": weather.get("backward_resilience"),
            "weather_data_status": weather.get("weather_data_status"),
        },

        # Score reproducibility: the exact inputs the engine saw.
        "subindex_inputs": (risk.get("calibration") or {}).get("subindex_inputs"),
        "cohort_key": assessment.get("cohort_key"),

        "warnings": assessment.get("warnings") or [],
    }

    doc.update(_series_block(continuous))
    doc["provenance"] = _provenance(continuous, len(dates))

    return to_mongo(doc)


def build_score_history_entry(
    assessment: Dict,
    *,
    assessment_id: Optional[str] = None,
    plot_key: Optional[str] = None,
) -> Optional[Dict]:
    """
    One row per scored parcel per run, for trend queries.

    Deliberately tiny and flat so it can be scanned cheaply. Returns None when
    there is no score — a failed run has no point on the trend line, and
    inventing one (or plotting a zero) would misrepresent the history.
    """
    risk = assessment.get("risk_assessment") or {}
    score = risk.get("index_score")
    if score is None:
        return None

    return to_mongo({
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "assessment_id": assessment_id,
        "farmer_id": assessment.get("farmer_id"),
        "plot_key": plot_key,
        "scope": "plot" if plot_key else "farmer",
        "assessment_date": assessment.get("assessment_date"),
        "created_at": utc_now(),
        "index_score": score,
        "raw_index": risk.get("raw_index"),
        "risk_category": risk.get("risk_category"),
        "confidence_gate": risk.get("confidence_gate"),
        "index_version": risk.get("index_version") or assessment.get("index_version"),
        # Stored per-row so an old score stays interpretable after the weights
        # change — otherwise a historical point cannot be explained.
        "weights": risk.get("weights"),
        "sub_index_scores": {
            k: sub_index_score(v)
            for k, v in (risk.get("sub_indices") or {}).items()
            if sub_index_score(v) is not None
        },
        "weak_sub_indices": risk.get("weak_sub_indices") or [],
        "field_area_ha": assessment.get("field_area_ha"),
    })
