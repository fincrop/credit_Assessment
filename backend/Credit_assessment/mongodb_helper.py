"""
MongoDB Helper
==============
Database operations for the satellite-based agricultural credit assessment
pipeline (index_v5).

This docstring describes what ``AssessmentSchema.build()`` ACTUALLY writes.
It previously documented several fields the builder has never emitted
(``recommended_credit_limit``, ``limit_per_hectare``, ``interest_rate``,
``repayment_months``, ``collateral_required``, a top-level ``extreme_events``
array, ``location.field_area_ha``, ``conditions``) — all removed with the v5
cutover, which produces an agronomic risk index and no repayment calibration.

COLLECTIONS
───────────
farm_info               one doc per farmer; identity, geometry, farms[], benefits
credit_assessments      one doc per pipeline run (see caveat below)
jobs                    assessment queue; also the de-facto store of the full
                        payload via jobs.result (see BACKEND-ENHANCEMENTS.md §5.1)
satellite_stats_cache   TTL'd satellite blobs, keyed by an opaque hash
weather_power_cache     TTL'd NASA POWER daily series
feature_store           append-only per-run feature snapshots (written, unread)
index_versions          registered index versions + weights (written, unread)
cohort_stats            peer distributions — NO WRITER EXISTS YET, so
                        PeerBenchmark permanently cold-starts

⚠ TWO SHAPES IN credit_assessments
   Single-farm runs go through AssessmentSchema.build() (below).
   Multi-farm runs are inserted RAW by save_multi_farm_assessment() and have a
   different key set — no top-level credit_score / risk_category, and
   assessment_date as an ISO string rather than a Date. Analytics aggregations
   in this module therefore mis-handle multi-plot farmers. Unification is
   planned; see BACKEND-ENHANCEMENTS.md §5.2 and Phase 5.

SCHEMA (credit_assessments — single-farm path)
──────────────────────────────────────────────
{
  farmer_id, assessment_date, pipeline_version, pipeline_profile,
  status, processing_time_s, pipeline_stages: [str],
  error, errors: [str], warnings: [str], traceback_head (FAILED runs only),

  location: { latitude, longitude, region }
  field_area_ha                                   ← document ROOT, not location

  credit_score, index_score, risk_category, credit_method, index_version,
  risk_assessment: { ... full RiskIndexEngine output, incl. sub_indices with
                     inputs/drivers, weights, reason_codes, calibration ... }

  component_scores: { landuse, vigor, stability, weather, data_confidence }
  weak_components: [str]

  cropping_summary: { seasons_with_crops, total_seasons_analyzed,
                      cropping_intensity, dominant_crop, crops_detected,
                      cross_season_events, ndvi_threshold_used, region }

  seasonal_ndvi: [ { season, season_type, year, start_date, end_date,
                     crop_detected, predicted_crop, confidence,
                     peak_ndvi, health_score, yield_score, n_scenes,
                     interval_indices: [ { date, ndvi, evi, ndmi, ndwi, psri,
                                           ndre, msavi2, nirv, kndvi, lswi,
                                           gcvi, missing, signal_source,
                                           bin_quality, cloud_cover } ] } ]

  weather_summary:   { total_extreme_events, critical_stage_events,
                       weather_risk_score, kharif/rabi/zaid_avg_rainfall_mm,
                       cycles_without_season_attribution,
                       max_recorded_temp_c, extreme_event_breakdown }
  weather_intervals: [ { cycle_id, crop, start/end_date, rainfall_total_mm,
                         temps, weather_risk, events: [...] } ]

  performance_summary: { avg_health_score, avg_yield_score,
                         avg_performance_score, n_seasons_scored,
                         n_complete_cycles, n_active_cycles }

  govt_benefits: { pm_kisan_enrolled, has_crop_insurance }   ← TRI-STATE.
      null means "unknown" and is preserved verbatim; it is NOT stripped by
      _clean_obj. Do not conflate null with false.

  ai_enrichment: { explainability: {...}, counterfactuals: {...} }
      NOTE: english_narrative / translated_narrative / model_snapshot are NOT
      persisted here today — they exist only on the live API response.

  saved_at: datetime
}

NOTE ON _clean_obj
──────────────────
Empty values (None / "" / [] / {}) are stripped recursively before insert, so an
absent key means "not computed" — it does NOT mean zero. The tri-state
govt_benefits block is explicitly exempted and restored after cleaning.

INDEXES — see _ensure_indexes() for the authoritative list.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure

logger = logging.getLogger(__name__)

# Secrets: set MONGODB_URI in the environment (see .env). Never commit credentials.
_DB_NAME = os.environ.get("MONGODB_DATABASE") or os.environ.get("MONGODB_DB") or "agristack"
_FARM_COLLECTION       = "farm_info"
_ASSESSMENT_COLLECTION = "credit_assessments"
_SAT_CACHE_COLLECTION  = "satellite_stats_cache"
_WEATHER_CACHE_COLLECTION = "weather_power_cache"

# Fallback only. The pipeline stamps its own version on every assessment; this is
# used solely when a caller hands us a payload with no version at all. Do NOT
# hardcode a value here that shadows the real one (was "3.0" while the pipeline
# ran 5.0, making every stored document mis-attributed).
_PIPELINE_VERSION_FALLBACK = "unknown"

# Benefit flags are tri-state (True / False / None-meaning-unknown). None is a
# meaningful value here, so these keys are exempt from the None-stripping in
# _clean_obj — collapsing "unknown" into "key absent" loses the distinction
# between "we asked and the answer was no" and "we never asked".
_TRISTATE_BENEFIT_KEYS = ("pm_kisan_enrolled", "has_crop_insurance")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _f(v, d: int = 4) -> Optional[float]:
    """Round float to d decimal places; return None if value is absent."""
    if v is None:
        return None
    try:
        return round(float(v), d)
    except (TypeError, ValueError):
        return None


def _clean_obj(value):
    """
    Recursively remove None, empty strings, empty lists/dicts.
    Keep False/0 values as they are semantically meaningful.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            cv = _clean_obj(v)
            if cv is None:
                continue
            if isinstance(cv, str) and cv.strip() == "":
                continue
            if isinstance(cv, (list, dict)) and len(cv) == 0:
                continue
            out[k] = cv
        return out or None
    if isinstance(value, list):
        out = []
        for item in value:
            cv = _clean_obj(item)
            if cv is None:
                continue
            if isinstance(cv, str) and cv.strip() == "":
                continue
            if isinstance(cv, (list, dict)) and len(cv) == 0:
                continue
            out.append(cv)
        return out or None
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Schema builder
# ─────────────────────────────────────────────────────────────────────────────

class AssessmentSchema:
    """
    Converts raw pipeline output → clean, structured MongoDB document.
    Excludes large arrays (raw band data). All floats rounded to 4 d.p.
    """

    @staticmethod
    def _shap_mongo_fallback(full: Optional[Dict]) -> Optional[Dict]:
        if not isinstance(full, dict):
            return None
        return {
            "method": full.get("method"),
            "credit_summary": (full.get("credit_summary") or "")[:900],
            "top_positive_drivers": (full.get("top_positive_drivers") or [])[:4],
            "top_negative_drivers": (full.get("top_negative_drivers") or [])[:4],
        }

    @staticmethod
    def _cf_mongo_fallback(full: Optional[Dict]) -> Optional[Dict]:
        if not isinstance(full, dict):
            return None
        slim = []
        for s in (full.get("scenarios") or [])[:5]:
            if isinstance(s, dict):
                slim.append(
                    {
                        "id": s.get("id"),
                        "title": s.get("title"),
                        "score_gain": s.get("score_gain"),
                        "component": s.get("component"),
                    }
                )
        return {
            "current_score": _f(full.get("current_score")),
            "projected_score_all_improvements": _f(
                full.get("projected_score_all_improvements")
            ),
            "scenarios": slim,
        }

    @staticmethod
    def build(raw: Dict) -> Dict:
        doc: Dict = {}

        # ── Identity & metadata ──────────────────────────────────────────
        doc['farmer_id']         = raw.get('farmer_id', 'UNKNOWN')
        doc['pipeline_version']  = raw.get('pipeline_version') or _PIPELINE_VERSION_FALLBACK
        doc['pipeline_profile']  = raw.get('pipeline_profile')
        doc['status']            = raw.get('status', 'UNKNOWN')

        # Failure detail. FAILED runs are now persisted (they previously left no
        # record at all), so the document must carry why it failed — otherwise a
        # gap in a farmer's history is indistinguishable from a gap in coverage.
        doc['error']    = raw.get('error')
        doc['errors']   = raw.get('errors') or []
        doc['warnings'] = raw.get('warnings') or []
        if raw.get('status') == 'FAILED':
            # Truncated: enough to identify the fault, not a full stack dump.
            doc['traceback_head'] = (raw.get('traceback') or '')[-2000:] or None
        doc['processing_time_s'] = _f(raw.get('processing_time_seconds', 0.0), 1)
        doc['pipeline_stages']   = raw.get('pipeline_stages', [])

        raw_date = raw.get('assessment_date')
        if isinstance(raw_date, str):
            try:
                doc['assessment_date'] = datetime.fromisoformat(raw_date)
            except ValueError:
                doc['assessment_date'] = datetime.utcnow()
        elif isinstance(raw_date, datetime):
            doc['assessment_date'] = raw_date
        else:
            doc['assessment_date'] = datetime.utcnow()

        # ── Location ─────────────────────────────────────────────────────
        loc = raw.get('location', {})
        ca  = raw.get('cropping_analysis', {})
        doc['location'] = {
            'latitude':     _f(loc.get('latitude')),
            'longitude':    _f(loc.get('longitude')),
            'region':       ca.get('region', raw.get('region', 'UNKNOWN')),
        }
        doc['field_area_ha'] = _f(raw.get('field_area_ha'))

        # ── Credit / risk result (denormalised to top-level) ───────────────
        credit = raw.get('credit_assessment', {}) or {}
        risk = raw.get('risk_assessment', {}) or {}
        doc['credit_score'] = _f(
            credit.get('credit_score', risk.get('index_score'))
        )
        doc['index_score'] = _f(risk.get('index_score', credit.get('credit_score')))
        doc['risk_category'] = credit.get(
            'risk_category', risk.get('risk_category', 'UNKNOWN')
        )
        doc['credit_method'] = credit.get(
            'method', risk.get('method', 'risk_index_v5_rule_based')
        )
        doc['index_version'] = raw.get('index_version') or risk.get(
            'index_version', 'index_v5'
        )
        doc['risk_assessment'] = risk or None

        # ── Component scores (shim scalars) ───────────────────────────────
        comp = credit.get('component_scores', {})
        doc['component_scores'] = {
            k: _f(v) for k, v in comp.items() if not isinstance(v, dict)
        }
        doc['weak_components'] = credit.get('weak_components', [])

        # ── Cropping summary ──────────────────────────────────────────────
        doc['cropping_summary'] = {
            'seasons_with_crops':     ca.get('seasons_with_crops', 0),
            'total_seasons_analyzed': ca.get('total_seasons_analyzed', 0),
            'cropping_intensity':     _f(ca.get('cropping_intensity')),
            'dominant_crop':          ca.get('dominant_crop'),
            'crops_detected':         ca.get('crops_detected', {}),
            'cross_season_events':    ca.get('cross_season_events', 0),
            'ndvi_threshold_used':    _f(ca.get('ndvi_threshold_used')),
            'region':                 ca.get('region', 'UNKNOWN'),
        }
        doc['crops_detected'] = ca.get('crops_detected', {})

        # ── Per-season NDVI time-series ───────────────────────────────────
        pa = raw.get('performance_analysis', {})
        doc['seasonal_ndvi'] = AssessmentSchema._build_seasonal_ndvi(
            ca.get('season_results', []),
            pa.get('seasonal_performance', []),
        )

        # ── Weather ───────────────────────────────────────────────────────
        wa = raw.get('weather_analysis', {})
        doc['weather_summary'] = AssessmentSchema._build_weather_summary(wa)
        doc['weather_intervals'] = AssessmentSchema._build_weather_intervals(
            wa.get('seasonal_weather', []),
            wa.get('cycle_risk_scores', []),
        )

        # ── Performance summary ───────────────────────────────────────────
        doc['performance_summary'] = {
            'avg_health_score':      _f(pa.get('average_health_score')),
            'avg_yield_score':       _f(pa.get('average_yield_score')),
            'avg_performance_score': _f(pa.get('average_performance_score')),
            'n_seasons_scored':      pa.get('n_seasons_analyzed', 0),
            'n_complete_cycles':     pa.get('n_complete_cycles', 0),
            'n_active_cycles':       pa.get('n_active_cycles', 0),
            'summary_note': (
                "Scores are averaged across detected crop intervals; "
                "active cycles are included with partial-season confidence."
            ),
        }

        # ── Govt benefits (tri-state: None = unknown) ─────────────────────
        benefits = raw.get('farmer_benefits') or {}
        doc['govt_benefits'] = {
            'pm_kisan_enrolled': (
                benefits['pm_kisan_enrolled']
                if 'pm_kisan_enrolled' in benefits else None
            ),
            'has_crop_insurance': (
                benefits['has_crop_insurance']
                if 'has_crop_insurance' in benefits else None
            ),
        }

        # Stage 12 — structured previews (full dict on API response / raw JSON)
        ai = raw.get('ai_enrichment')
        if isinstance(ai, dict):
            explainability = ai.get('explainability_mongo') or ai.get('explainability')
            counterfactuals = ai.get('counterfactuals_mongo') or ai.get('counterfactuals')
            doc['ai_enrichment'] = AssessmentSchema._build_ai_enrichment(
                explainability, counterfactuals
            )

        doc['saved_at'] = datetime.utcnow()

        # Snapshot the tri-state block before cleaning; _clean_obj strips None
        # recursively and would erase "unknown", which is a real answer here.
        tristate_benefits = doc.get('govt_benefits')

        cleaned = _clean_obj(doc)
        cleaned = cleaned if cleaned is not None else doc

        # Restore the tri-state block verbatim, explicit nulls included.
        if isinstance(tristate_benefits, dict):
            cleaned['govt_benefits'] = {
                k: tristate_benefits.get(k) for k in _TRISTATE_BENEFIT_KEYS
            }

        return cleaned

    # ── Sub-builders ─────────────────────────────────────────────────────

    @staticmethod
    def _build_seasonal_ndvi(
        season_results:   List[Dict],
        performance_list: List[Dict],
    ) -> List[Dict]:
        perf_idx = {
            (p.get('season'), p.get('year')): p
            for p in performance_list
        }
        rows = []
        for r in season_results:
            key  = (r.get('season'), r.get('year'))
            perf = perf_idx.get(key, {})
            rows.append({
                'season':            r.get('season'),
                'year':              r.get('year'),
                'start_date':        r.get('start_date'),
                'end_date':          r.get('end_date'),
                'crop_detected':     bool(r.get('crop_detected', False)),
                'predicted_crop':    r.get('predicted_crop'),
                'confidence':        _f(
                    r.get('confidence')
                    if r.get('confidence') is not None
                    else r.get('crop_confidence')
                ),
                'is_cross_season':   bool(r.get('is_cross_season', False)),
                'avg_ndvi':          _f(r.get('avg_ndvi')),
                'peak_ndvi':         _f(r.get('peak_ndvi')),
                'ndvi_rise':         _f(r.get('ndvi_rise')),
                'arc_score':         _f(r.get('arc_score')),
                'frac_above_thresh': _f(r.get('frac_above_thresh')),
                'health_score':      _f(perf.get('health_score')),
                'yield_score':       _f(perf.get('yield_potential_score')),
                'n_scenes':          r.get('n_scenes', 0),
                'interval_indices': AssessmentSchema._extract_interval_indices(
                    r.get('scenes', [])
                ),
            })
        rows.sort(key=lambda x: (
            x.get('year', 0),
            0 if x.get('season') == 'kharif' else 1,
        ))
        return rows

    @staticmethod
    def _build_weather_summary(wa: Dict) -> Dict:
        seasonal = wa.get('seasonal_weather', [])
        events   = wa.get('extreme_events', [])

        def _season_of(entry: Dict) -> str:
            """
            Resolve a weather entry to kharif/rabi/zaid, or '' when unattributable.

            The previous implementation substring-matched on entry['season'],
            which in the production (classification-off) path is 'cycle_1',
            'cycle_2', ... — never containing a season name. Both rainfall
            averages were therefore permanently null. Prefer the explicit
            season_type when the upstream cycle provides it; fall back to the
            label only when it actually names a season.
            """
            explicit = str(entry.get('season_type') or '').strip().lower()
            if explicit in ('kharif', 'rabi', 'zaid'):
                return explicit
            label = str(entry.get('season') or '').strip().lower()
            for name in ('kharif', 'rabi', 'zaid'):
                if name in label:
                    return name
            return ''

        attributed = [(s, _season_of(s)) for s in seasonal]
        kharif_rain = [
            s['total_rainfall_mm'] for s, sn in attributed
            if sn == 'kharif' and 'total_rainfall_mm' in s
        ]
        rabi_rain = [
            s['total_rainfall_mm'] for s, sn in attributed
            if sn == 'rabi' and 'total_rainfall_mm' in s
        ]
        zaid_rain = [
            s['total_rainfall_mm'] for s, sn in attributed
            if sn == 'zaid' and 'total_rainfall_mm' in s
        ]
        n_unattributed = sum(1 for _, sn in attributed if not sn)
        max_temps = [s['max_temp_c'] for s in seasonal if 'max_temp_c' in s]

        breakdown: Dict[str, int] = {}
        for e in events:
            raw_type = e.get('type', 'other')
            if 'heatwave' in raw_type:
                key = 'heatwave'
            elif 'cold' in raw_type:
                key = 'cold_wave'
            elif 'rainfall' in raw_type:
                key = 'heavy_rainfall'
            elif 'drought' in raw_type:
                key = 'drought'
            else:
                key = 'other'
            breakdown[key] = breakdown.get(key, 0) + 1

        return {
            'total_extreme_events':    len(events),
            'critical_stage_events':   sum(1 for e in events if e.get('crop_stage_critical')),
            'weather_risk_score':      _f(wa.get('weather_risk_score')),
            'weather_degraded':        bool(wa.get('weather_degraded', False)),
            'weather_data_status':     wa.get('weather_data_status', 'ok'),
            'kharif_avg_rainfall_mm':  _f(sum(kharif_rain) / len(kharif_rain)) if kharif_rain else None,
            'rabi_avg_rainfall_mm':    _f(sum(rabi_rain)   / len(rabi_rain))   if rabi_rain   else None,
            'zaid_avg_rainfall_mm':    _f(sum(zaid_rain)   / len(zaid_rain))   if zaid_rain   else None,
            # How many weather cycles could not be attributed to a season at all.
            # Non-zero means the per-season rainfall figures above cover only
            # part of the record — they are not a complete seasonal breakdown.
            'cycles_without_season_attribution': n_unattributed,
            'max_recorded_temp_c':     _f(max(max_temps))                       if max_temps   else None,
            'extreme_event_breakdown': breakdown,
        }

    @staticmethod
    def _build_extreme_events(raw_events: List[Dict]) -> List[Dict]:
        compact = []
        for e in raw_events:
            etype = e.get('type', 'unknown')
            if etype == 'heatwave':
                val = _f(e.get('max_temp_c'))
            elif etype == 'cold_wave':
                val = _f(e.get('min_temp_c'))
            elif 'rainfall' in etype:
                val = _f(e.get('rainfall_mm') or e.get('3day_total_mm'))
            elif etype == 'drought':
                val = _f(e.get('total_rain_mm'))
            else:
                val = None

            compact.append({
                'type':                etype,
                'severity':            e.get('severity', 'medium'),
                'date_or_start':       (e.get('start_date') or e.get('date') or e.get('end_date') or ''),
                'duration_days':       e.get('duration_days'),
                'value':               val,
                'crop_stage_critical': bool(e.get('crop_stage_critical', False)),
            })
        return compact

    # Persisted per-observation index columns.
    #   stored_key -> scene indices key emitted by SatelliteDataCollector
    # Previously this read 'SAVI_mean' and 'GCI_mean', which the collector has
    # never emitted (it produces MSAVI2_mean / GCVI_mean), so two of the five
    # stored columns were permanently null.
    _INTERVAL_INDEX_COLUMNS = (
        ('ndvi',   'NDVI_mean'),
        ('ndvi_std', 'NDVI_std'),
        ('evi',    'EVI_mean'),
        ('ndmi',   'NDMI_mean'),
        ('ndwi',   'NDWI_mean'),
        ('psri',   'PSRI_mean'),
        ('ndre',   'NDRE_mean'),
        ('msavi2', 'MSAVI2_mean'),
        ('nirv',   'NIRv_mean'),
        ('kndvi',  'kNDVI_mean'),
        ('lswi',   'LSWI_mean'),
        ('gcvi',   'GCVI_mean'),
    )

    @staticmethod
    def _extract_interval_indices(scenes: List[Dict]) -> List[Dict]:
        """
        Per-observation index values for the cycle window.

        Carries provenance alongside the values: an imputed or SAR-derived bin
        must not be indistinguishable from a directly observed one downstream.
        """
        rows: List[Dict] = []
        for s in scenes or []:
            date = s.get('date')
            idx = s.get('indices') or {}
            if not date or not isinstance(idx, dict):
                continue
            row: Dict = {'date': str(date)[:10]}
            for stored_key, scene_key in AssessmentSchema._INTERVAL_INDEX_COLUMNS:
                row[stored_key] = _f(idx.get(scene_key))
            # Provenance / quality — never inferred, only copied when present.
            row['missing'] = bool(s.get('missing', False))
            row['signal_source'] = s.get('signal_source')
            row['bin_quality'] = _f(s.get('bin_quality'))
            row['cloud_cover'] = _f(s.get('cloud_cover'), 1)
            rows.append(row)
        return rows

    @staticmethod
    def _build_weather_intervals(
        seasonal_weather: List[Dict],
        cycle_risk_scores: List[Dict],
    ) -> List[Dict]:
        risk_map = {
            (r.get('cycle_id') or ""): {
                'risk_score': _f(r.get('risk_score')),
                'n_events': r.get('n_events', 0),
            }
            for r in (cycle_risk_scores or [])
            if isinstance(r, dict)
        }
        out: List[Dict] = []
        for cycle in seasonal_weather or []:
            if not isinstance(cycle, dict):
                continue
            cid = cycle.get('cycle_id') or cycle.get('season')
            events = AssessmentSchema._build_extreme_events(cycle.get('extreme_events', []))
            # enrich events with stage and cycle timing fields when available
            full_events = []
            for e in cycle.get('extreme_events', []) or []:
                if not isinstance(e, dict):
                    continue
                compact = AssessmentSchema._build_extreme_events([e])[0]
                compact['stage_name'] = e.get('stage_name')
                compact['days_since_sowing'] = e.get('days_since_sowing')
                compact['impact_severity'] = e.get('impact_severity')
                compact['crop_impact_narrative'] = e.get('crop_impact_narrative')
                full_events.append(compact)
            out.append(
                {
                    'cycle_id': cid,
                    'crop': cycle.get('crop'),
                    'start_date': cycle.get('start_date'),
                    'end_date': cycle.get('end_date'),
                    'duration_days': cycle.get('duration_days'),
                    'rainfall_total_mm': _f(cycle.get('total_rainfall_mm')),
                    'avg_temp_c': _f(cycle.get('avg_temp_c')),
                    'max_temp_c': _f(cycle.get('max_temp_c')),
                    'min_temp_c': _f(cycle.get('min_temp_c')),
                    'weather_risk': risk_map.get(cid, {}).get('risk_score'),
                    'event_count': len(events),
                    'events': full_events or events,
                }
            )
        return out

    @staticmethod
    def _build_ai_enrichment(
        explainability: Optional[Dict],
        counterfactuals: Optional[Dict],
    ) -> Dict:
        out: Dict = {}
        if isinstance(explainability, dict):
            out['explainability'] = {
                'method': explainability.get('method'),
                'shap_available': explainability.get('shap_available'),
                'base_value': _f(explainability.get('base_value')),
                'credit_summary': explainability.get('credit_summary'),
                'top_positive_drivers': (explainability.get('top_positive_drivers') or [])[:6],
                'top_negative_drivers': (explainability.get('top_negative_drivers') or [])[:6],
            }
        if isinstance(counterfactuals, dict):
            out['counterfactuals'] = {
                'current_score': _f(counterfactuals.get('current_score')),
                'current_risk_category': counterfactuals.get('current_risk_category'),
                'projected_score_all_improvements': _f(
                    counterfactuals.get('projected_score_all_improvements')
                ),
                'scenarios': (counterfactuals.get('scenarios') or [])[:6],
                'improvement_roadmap': counterfactuals.get('improvement_roadmap'),
            }
        return out


# ─────────────────────────────────────────────────────────────────────────────
# MongoDBHelper
# ─────────────────────────────────────────────────────────────────────────────

class MongoDBHelper:
    """
    All MongoDB I/O for the credit assessment pipeline.

    Usage:
        db = MongoDBHelper()
        db.save_assessment(pipeline_result)
        db.close()

        # or as context manager
        with MongoDBHelper() as db:
            db.save_assessment(pipeline_result)
    """

    def __init__(self, connection_string: Optional[str] = None):
        self.uri = (connection_string or os.environ.get("MONGODB_URI") or "").strip()
        if not self.uri:
            raise ValueError(
                "MongoDB URI is not configured. Set the MONGODB_URI environment variable "
                "(see .env) or pass MongoDBHelper(connection_string=...)."
            )
        self.client      = None
        self.db          = None
        self.farms       = None
        self.assessments = None
        self.satellite_cache = None
        self.weather_power_cache = None
        # Pillar 3/5/9: versioned feature store, index versions, peer cohort stats
        self.feature_store = None
        self.index_versions = None
        self.cohort_stats = None
        # Job queue. Written and polled by api/app.py, worker.py and
        # api/job_runner.py directly; held here so its indexes get created.
        self.jobs = None
        self._connect()

    # ── Connection ────────────────────────────────────────────────────────

    def _connect(self):
        try:
            self.client = MongoClient(self.uri, serverSelectionTimeoutMS=8000)
            self.client.admin.command('ping')
            self.db          = self.client[_DB_NAME]
            self.farms       = self.db[_FARM_COLLECTION]
            self.assessments = self.db[_ASSESSMENT_COLLECTION]
            self.satellite_cache = self.db[_SAT_CACHE_COLLECTION]
            self.weather_power_cache = self.db[_WEATHER_CACHE_COLLECTION]
            # Pillar 3/5/9 collections (created lazily on first write)
            self.feature_store = self.db["feature_store"]
            self.index_versions = self.db["index_versions"]
            self.cohort_stats = self.db["cohort_stats"]
            self.jobs = self.db["jobs"]
            self._ensure_indexes()
            logger.info("✅ MongoDB connected  (pipeline v5.0 schema / index_v5)")
        except ConnectionFailure as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ MongoDB error: {e}")
            raise

    @staticmethod
    def _create_index_safe(collection, keys, **kwargs) -> None:
        """
        Create index; ignore MongoDB code 85 (IndexOptionsConflict) when the same
        key pattern already exists under another name (e.g. legacy status_index).
        """
        try:
            collection.create_index(keys, **kwargs)
        except OperationFailure as e:
            if getattr(e, "code", None) == 85:
                logger.debug(
                    "Skipping index %s: same keys already indexed (%s)",
                    kwargs.get("name", keys),
                    getattr(e, "details", {}).get("errmsg", str(e))[:120],
                )
            else:
                raise

    def _ensure_indexes(self):
        try:
            # farm_info
            self._create_index_safe(
                self.farms,
                [('farmer_id', ASCENDING)],
                unique=True,
                name='farmer_id_unique',
            )
            self._create_index_safe(
                self.farms, [('status', ASCENDING)], name='status_idx'
            )

            # credit_assessments
            self._create_index_safe(
                self.assessments,
                [('farmer_id', ASCENDING), ('assessment_date', DESCENDING)],
                name='farmer_history',
            )
            self._create_index_safe(
                self.assessments,
                [('risk_category', ASCENDING)],
                name='risk_category_idx',
            )
            self._create_index_safe(
                self.assessments,
                [('credit_score', ASCENDING)],
                name='credit_score_idx',
            )
            self._create_index_safe(
                self.assessments,
                [('assessment_date', DESCENDING)],
                name='assessment_date_idx',
            )
            self._create_index_safe(
                self.assessments,
                [('location.region', ASCENDING), ('risk_category', ASCENDING)],
                name='region_risk_idx',
            )
            # satellite_stats_cache
            self._create_index_safe(
                self.satellite_cache,
                [('cache_key', ASCENDING)],
                unique=True,
                name='sat_cache_key_unique',
            )
            self._create_index_safe(
                self.satellite_cache,
                [('expires_at', ASCENDING)],
                expireAfterSeconds=0,
                name='sat_cache_ttl',
            )
            # weather_power_cache (NASA POWER daily series)
            self._create_index_safe(
                self.weather_power_cache,
                [('cache_key', ASCENDING)],
                unique=True,
                name='weather_power_cache_key_unique',
            )
            self._create_index_safe(
                self.weather_power_cache,
                [('expires_at', ASCENDING)],
                expireAfterSeconds=0,
                name='weather_power_cache_ttl',
            )

            # ── jobs ──────────────────────────────────────────────────────
            # This collection had NO indexes at all, yet worker.py polls
            #   find({'status': 'QUEUED'}).sort('created_at')
            # every 2 seconds — a full collection scan plus an in-memory sort,
            # 30x/minute, forever. Because jobs.result embeds a multi-MB
            # payload, that scan also drags every result blob through memory.
            # Cost grows linearly with total job history.
            self._create_index_safe(
                self.jobs,
                [('status', ASCENDING), ('created_at', ASCENDING)],
                name='jobs_status_created',
            )
            # Stuck-job reaper: {'status': 'RUNNING', 'started_at': {'$lt': ...}}
            self._create_index_safe(
                self.jobs,
                [('status', ASCENDING), ('started_at', ASCENDING)],
                name='jobs_status_started',
            )
            # Frontend "latest result for this farmer" lookups.
            self._create_index_safe(
                self.jobs,
                [('farmer_id', ASCENDING), ('completed_at', DESCENDING)],
                name='jobs_farmer_completed',
            )

            # Optional jobs TTL. DEFAULT OFF — enabling it deletes job history,
            # and the frontend still falls back to jobs.result for the live
            # view, so a short TTL would silently break dashboards. Set
            # JOBS_TTL_DAYS to a positive integer to opt in.
            jobs_ttl_days = 0
            try:
                jobs_ttl_days = int(os.environ.get('JOBS_TTL_DAYS', '0') or 0)
            except ValueError:
                logger.warning("JOBS_TTL_DAYS is not an integer; TTL not applied")
            if jobs_ttl_days > 0:
                self._create_index_safe(
                    self.jobs,
                    [('created_at', ASCENDING)],
                    expireAfterSeconds=jobs_ttl_days * 24 * 60 * 60,
                    name='jobs_ttl',
                )
                logger.info("jobs TTL enabled: %d days", jobs_ttl_days)

            # ── credit_assessments: cover the actual query shape ──────────
            # The frontend filters {farmer_id, status} then sorts by date;
            # 'farmer_history' only covers the farmer_id prefix, forcing an
            # in-memory sort.
            self._create_index_safe(
                self.assessments,
                [
                    ('farmer_id', ASCENDING),
                    ('status', ASCENDING),
                    ('assessment_date', DESCENDING),
                ],
                name='farmer_status_history',
            )

            # ── satellite cache: make per-farmer eviction possible ────────
            self._create_index_safe(
                self.satellite_cache,
                [('metadata.farmer_id', ASCENDING)],
                name='sat_cache_farmer',
            )

            # ── feature_store / cohort_stats / index_versions ─────────────
            self._create_index_safe(
                self.feature_store,
                [('farmer_id', ASCENDING), ('created_at', DESCENDING)],
                name='feature_store_farmer',
            )
            self._create_index_safe(
                self.feature_store,
                [('index_version', ASCENDING), ('features.cohort_key', ASCENDING)],
                name='feature_store_cohort',
            )
            self._create_index_safe(
                self.cohort_stats,
                [('cohort_key', ASCENDING)],
                unique=True,
                name='cohort_key_unique',
            )
            self._create_index_safe(
                self.index_versions,
                [('index_version', ASCENDING)],
                unique=True,
                name='index_version_unique',
            )

            logger.debug("MongoDB indexes verified")
        except Exception as e:
            # Index creation must never block startup, but it must be visible —
            # a silently unindexed jobs collection degrades the whole service.
            logger.warning("Index creation incomplete: %s", e)

    # ── Satellite stats cache ──────────────────────────────────────────────

    def get_satellite_stats_cache(self, cache_key: str) -> Optional[Dict]:
        try:
            doc = self.satellite_cache.find_one({'cache_key': cache_key})
            if not doc:
                return None
            self.satellite_cache.update_one(
                {'_id': doc['_id']},
                {'$set': {'last_accessed_at': datetime.utcnow()}}
            )
            return doc.get('satellite_data')
        except Exception as e:
            logger.warning("satellite cache read failed: %s", e)
            return None

    def upsert_satellite_stats_cache(
        self,
        cache_key: str,
        satellite_data: Dict,
        metadata: Optional[Dict] = None,
        ttl_days: int = 30,
    ) -> bool:
        try:
            now = datetime.utcnow()
            expires_at = now if ttl_days <= 0 else datetime.fromtimestamp(
                now.timestamp() + (ttl_days * 24 * 60 * 60)
            )
            doc = {
                'cache_key': cache_key,
                'satellite_data': satellite_data,
                'updated_at': now,
                'last_accessed_at': now,
                'expires_at': expires_at,
            }
            if metadata:
                doc['metadata'] = metadata
            self.satellite_cache.update_one(
                {'cache_key': cache_key},
                {'$set': doc, '$setOnInsert': {'created_at': now}},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.warning("satellite cache write failed: %s", e)
            return False

    def delete_satellite_stats_cache(self, farmer_id: Optional[str] = None) -> int:
        """
        Delete cached satellite blobs for one farmer, or all of them.

        Returns the number of documents deleted.

        NOTE the field path: upsert_satellite_stats_cache stores the farmer id
        under ``metadata.farmer_id``, not at the document root. A root-level
        ``{'farmer_id': ...}`` filter matches nothing and silently reports
        success — which is what the devtool did before this method existed.
        """
        if self.satellite_cache is None:
            logger.warning("delete_satellite_stats_cache: no cache collection")
            return 0
        criteria = {'metadata.farmer_id': farmer_id} if farmer_id else {}
        result = self.satellite_cache.delete_many(criteria)
        logger.info(
            "Satellite cache: deleted %d entries (%s)",
            result.deleted_count, farmer_id or 'ALL',
        )
        return result.deleted_count

    # ── NASA POWER weather cache ───────────────────────────────────────────

    def get_weather_power_cache(self, cache_key: str) -> Optional[Dict]:
        """Return cached POWER payload (orient=split dict) or None."""
        try:
            if self.weather_power_cache is None:
                return None
            doc = self.weather_power_cache.find_one({'cache_key': cache_key})
            if not doc:
                return None
            self.weather_power_cache.update_one(
                {'_id': doc['_id']},
                {'$set': {'last_accessed_at': datetime.utcnow()}},
            )
            return doc.get('power_data')
        except Exception as e:
            logger.warning("weather_power_cache read failed: %s", e)
            return None

    def upsert_weather_power_cache(
        self,
        cache_key: str,
        power_data: Dict,
        metadata: Optional[Dict] = None,
        ttl_days: int = 30,
    ) -> bool:
        try:
            if self.weather_power_cache is None:
                return False
            now = datetime.utcnow()
            expires_at = now if ttl_days <= 0 else datetime.fromtimestamp(
                now.timestamp() + (ttl_days * 24 * 60 * 60)
            )
            doc = {
                'cache_key': cache_key,
                'power_data': power_data,
                'updated_at': now,
                'last_accessed_at': now,
                'expires_at': expires_at,
            }
            if metadata:
                doc['metadata'] = metadata
            self.weather_power_cache.update_one(
                {'cache_key': cache_key},
                {'$set': doc, '$setOnInsert': {'created_at': now}},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.warning("weather_power_cache write failed: %s", e)
            return False

    def is_connected(self) -> bool:
        if not self.client:
            return False
        try:
            self.client.admin.command('ping')
            return True
        except Exception:
            return False

    # ── Farm CRUD ─────────────────────────────────────────────────────────

    def get_farm_by_id(self, farmer_id: str) -> Optional[Dict]:
        try:
            farm = self.farms.find_one({'farmer_id': farmer_id})
            if farm:
                farm['_id'] = str(farm['_id'])
            return farm
        except Exception as e:
            logger.error(f"❌ get_farm_by_id({farmer_id}): {e}")
            return None

    def get_all_active_farms(self, limit: int = 100) -> List[Dict]:
        try:
            farms = list(self.farms.find({'status': 'active'}).limit(limit))
            for f in farms:
                f['_id'] = str(f['_id'])
            logger.info(f"✅ Retrieved {len(farms)} active farms")
            return farms
        except Exception as e:
            logger.error(f"❌ get_all_active_farms: {e}")
            return []

    def add_farm(self, farm_data: Dict) -> Optional[str]:
        if 'farmer_id' not in farm_data:
            logger.error("❌ add_farm: farmer_id required")
            return None
        try:
            now = datetime.utcnow()
            farm_data.setdefault('status',     'active')
            farm_data.setdefault('created_at', now)
            farm_data['updated_at'] = now
            result = self.farms.insert_one(farm_data)
            logger.info(f"✅ Farm added: {farm_data['farmer_id']}")
            return str(result.inserted_id)
        except DuplicateKeyError:
            logger.error(f"❌ Farm already exists: {farm_data.get('farmer_id')}")
            return None
        except Exception as e:
            logger.error(f"❌ add_farm: {e}")
            return None

    def update_farm(self, farmer_id: str, update_data: Dict) -> bool:
        try:
            update_data['updated_at'] = datetime.utcnow()
            result = self.farms.update_one(
                {'farmer_id': farmer_id}, {'$set': update_data}
            )
            ok = result.modified_count > 0
            logger.info(f"{'✅' if ok else '⚠'} Farm {'updated' if ok else 'not modified'}: {farmer_id}")
            return ok
        except Exception as e:
            logger.error(f"❌ update_farm({farmer_id}): {e}")
            return False

    def count_farms(self, criteria: Optional[Dict] = None) -> int:
        try:
            return self.farms.count_documents(criteria or {})
        except Exception as e:
            logger.error(f"❌ count_farms: {e}")
            return 0

    # ── Assessment writes ─────────────────────────────────────────────────

    def save_assessment(self, raw_assessment: Dict) -> str:
        """
        Build structured document from raw pipeline output and insert.

        This is the PRIMARY write method — always use this, not direct insert.
        Returns the MongoDB document _id as a string.

        RAISES on failure rather than returning None. A dropped assessment is a
        data-loss event, and every caller already wraps this in a try/except that
        records the failure on the assessment; swallowing the exception here made
        a failed write indistinguishable from a successful one.
        """
        try:
            doc    = AssessmentSchema.build(raw_assessment)
            result = self.assessments.insert_one(doc)
            db_id  = str(result.inserted_id)
            logger.info(
                f"✅ Assessment saved  farmer={doc['farmer_id']}  "
                f"score={doc.get('credit_score')}  "
                f"risk={doc.get('risk_category')}  id={db_id}"
            )
            return db_id
        except Exception as e:
            logger.error(
                "❌ save_assessment FAILED for farmer=%s — assessment NOT persisted: %s",
                raw_assessment.get('farmer_id', 'UNKNOWN'), e,
            )
            raise

    def upsert_latest_assessment(self, raw_assessment: Dict) -> Optional[str]:
        """
        Replace the most recent assessment for a farmer (one-doc-per-farmer mode).
        Useful when you want the DB to hold only the latest result per farmer.
        """
        try:
            doc    = AssessmentSchema.build(raw_assessment)
            result = self.assessments.replace_one(
                {'farmer_id': doc['farmer_id']}, doc, upsert=True
            )
            db_id = str(result.upserted_id) if result.upserted_id else 'replaced'
            logger.info(f"✅ Assessment upserted  farmer={doc['farmer_id']}")
            return db_id
        except Exception as e:
            logger.error(f"❌ upsert_latest_assessment: {e}")
            return None

    # ── Assessment reads ──────────────────────────────────────────────────

    def get_assessments_by_farmer(
        self, farmer_id: str, limit: int = 10
    ) -> List[Dict]:
        """Return assessments for a farmer, newest first."""
        try:
            docs = list(
                self.assessments.find({'farmer_id': farmer_id})
                .sort('assessment_date', DESCENDING)
                .limit(limit)
            )
            for d in docs:
                d['_id'] = str(d['_id'])
            return docs
        except Exception as e:
            logger.error(f"❌ get_assessments_by_farmer({farmer_id}): {e}")
            return []

    def get_latest_assessment(self, farmer_id: str) -> Optional[Dict]:
        results = self.get_assessments_by_farmer(farmer_id, limit=1)
        return results[0] if results else None

    def get_assessments_by_risk(
        self,
        risk_category: str,
        limit: int = 100,
        region: Optional[str] = None,
    ) -> List[Dict]:
        query: Dict = {'risk_category': risk_category}
        if region:
            query['location.region'] = region
        try:
            docs = list(self.assessments.find(query).limit(limit))
            for d in docs:
                d['_id'] = str(d['_id'])
            logger.info(f"✅ {len(docs)} {risk_category} assessments")
            return docs
        except Exception as e:
            logger.error(f"❌ get_assessments_by_risk: {e}")
            return []

    def get_assessments_by_score_range(
        self,
        min_score: float = 0.0,
        max_score: float = 100.0,
        limit: int = 100,
    ) -> List[Dict]:
        try:
            docs = list(
                self.assessments.find(
                    {'credit_score': {'$gte': min_score, '$lte': max_score}}
                ).limit(limit)
            )
            for d in docs:
                d['_id'] = str(d['_id'])
            return docs
        except Exception as e:
            logger.error(f"❌ get_assessments_by_score_range: {e}")
            return []

    def get_farmer_score_trend(
        self, farmer_id: str, limit: int = 10
    ) -> List[Dict]:
        """
        Return (date, score, risk_category) tuples oldest→newest.
        Ready for chart rendering.
        """
        try:
            docs = list(
                self.assessments.find(
                    {'farmer_id': farmer_id},
                    {
                        '_id': 0,
                        'assessment_date':             1,
                        'credit_score':                1,
                        'risk_category':               1,
                        'cropping_summary.dominant_crop': 1,
                    },
                )
                .sort('assessment_date', DESCENDING)
                .limit(limit)
            )
            docs.reverse()
            return docs
        except Exception as e:
            logger.error(f"❌ get_farmer_score_trend({farmer_id}): {e}")
            return []

    # ── Analytics aggregations ────────────────────────────────────────────

    def get_portfolio_statistics(self, region: Optional[str] = None) -> Dict:
        """
        Count, average score/limit/intensity grouped by risk category.
        Optionally filtered to a single region.
        """
        match = {'$match': {'location.region': region}} if region else None
        pipeline = []
        if match:
            pipeline.append(match)
        pipeline += [
            {
                '$group': {
                    '_id':               '$risk_category',
                    'count':             {'$sum': 1},
                    'avg_credit_score':  {'$avg': '$credit_score'},
                    'avg_index_score':   {'$avg': '$index_score'},
                    # 'recommended_credit_limit' was removed with the v5 cutover
                    # (the index carries no repayment calibration), and
                    # field_area_ha is stored at the document root, not under
                    # location — both aggregations previously returned null.
                    'avg_field_area_ha': {'$avg': '$field_area_ha'},
                    'avg_intensity':     {'$avg': '$cropping_summary.cropping_intensity'},
                    'avg_weather_risk':  {'$avg': '$weather_summary.weather_risk_score'},
                }
            },
            {'$sort': {'avg_credit_score': DESCENDING}},
        ]
        try:
            results = list(self.assessments.aggregate(pipeline))
            total   = self.assessments.count_documents(
                {'location.region': region} if region else {}
            )
            by_risk = {}
            for r in results:
                cat = r.pop('_id')
                by_risk[cat] = {
                    k: (round(v, 2) if isinstance(v, float) else v)
                    for k, v in r.items()
                }
            return {
                'total_assessments': total,
                'region_filter':     region,
                'by_risk_category':  by_risk,
            }
        except Exception as e:
            logger.error(f"❌ get_portfolio_statistics: {e}")
            return {'total_assessments': 0, 'by_risk_category': {}}

    def get_regional_breakdown(self) -> List[Dict]:
        """Per-region counts and average scores — for geo-heatmap dashboards."""
        pipeline = [
            {
                '$group': {
                    '_id':             '$location.region',
                    'count':           {'$sum': 1},
                    'avg_score':       {'$avg': '$credit_score'},
                    'avg_intensity':   {'$avg': '$cropping_summary.cropping_intensity'},
                    'low_risk_count':  {'$sum': {'$cond': [{'$eq': ['$risk_category', 'LOW']},  1, 0]}},
                    'high_risk_count': {'$sum': {'$cond': [{'$in': ['$risk_category', ['HIGH', 'VERY_HIGH']]}, 1, 0]}},
                }
            },
            {'$sort': {'count': DESCENDING}},
        ]
        try:
            return list(self.assessments.aggregate(pipeline))
        except Exception as e:
            logger.error(f"❌ get_regional_breakdown: {e}")
            return []

    def get_crop_performance_report(self) -> List[Dict]:
        """
        Average health/yield scores PER CROP TYPE across all stored seasons.
        Enables comparison: 'Cotton avg health score vs Wheat avg health score'.
        """
        pipeline = [
            {'$unwind': '$seasonal_ndvi'},
            {'$match': {'seasonal_ndvi.crop_detected': True}},
            {
                '$group': {
                    '_id':           '$seasonal_ndvi.predicted_crop',
                    'count':         {'$sum': 1},
                    'avg_health':    {'$avg': '$seasonal_ndvi.health_score'},
                    'avg_yield':     {'$avg': '$seasonal_ndvi.yield_score'},
                    'avg_peak_ndvi': {'$avg': '$seasonal_ndvi.peak_ndvi'},
                    'avg_arc_score': {'$avg': '$seasonal_ndvi.arc_score'},
                }
            },
            {'$sort': {'count': DESCENDING}},
        ]
        try:
            results = list(self.assessments.aggregate(pipeline))
            for r in results:
                for k in ('avg_health', 'avg_yield', 'avg_peak_ndvi', 'avg_arc_score'):
                    if r.get(k) is not None:
                        r[k] = round(r[k], 2)
            return results
        except Exception as e:
            logger.error(f"❌ get_crop_performance_report: {e}")
            return []

    def get_weather_impact_summary(self) -> List[Dict]:
        """
        Average credit score grouped by whether extreme weather events hit
        critical crop growth stages. Validates the critical-stage penalty weight.
        """
        pipeline = [
            {
                '$project': {
                    'credit_score':        1,
                    'risk_category':       1,
                    'has_critical_events': {
                        '$gt': ['$weather_summary.critical_stage_events', 0]
                    },
                    'weather_risk_score': '$weather_summary.weather_risk_score',
                }
            },
            {
                '$group': {
                    '_id':              '$has_critical_events',
                    'count':            {'$sum': 1},
                    'avg_credit_score': {'$avg': '$credit_score'},
                    'avg_weather_risk': {'$avg': '$weather_risk_score'},
                }
            },
        ]
        try:
            results = list(self.assessments.aggregate(pipeline))
            for r in results:
                r['has_critical_stage_events'] = r.pop('_id')
                for k in ('avg_credit_score', 'avg_weather_risk'):
                    if r.get(k) is not None:
                        r[k] = round(r[k], 2)
            return results
        except Exception as e:
            logger.error(f"❌ get_weather_impact_summary: {e}")
            return []

    def get_assessment_statistics(self) -> Dict:
        """Backwards-compatible alias for get_portfolio_statistics()."""
        return self.get_portfolio_statistics()

    # ── Pillar 3/5/9: feature store, index versions, peer cohort stats ─────

    def get_cohort_stats(self) -> Dict:
        """
        Return accumulated peer-cohort distributions keyed by cohort_key
        (agro-zone|season|crop-family), consumed by PeerBenchmark.from_mongo().
        Shape: {cohort_key: {metric: {"mean","std","n","percentiles"?}}}.
        Empty dict = cold start (PeerBenchmark falls back to internal scoring).
        """
        if self.cohort_stats is None:
            return {}
        try:
            out: Dict = {}
            for doc in self.cohort_stats.find({}):
                key = doc.get("cohort_key")
                if key:
                    out[key] = doc.get("metrics", {})
            return out
        except Exception as e:
            logger.debug("get_cohort_stats failed: %s", e)
            return {}

    def upsert_cohort_stat(self, cohort_key: str, metrics: Dict) -> bool:
        """Upsert a cohort's metric distributions (used by a future accumulation job)."""
        if self.cohort_stats is None or not cohort_key:
            return False
        try:
            self.cohort_stats.update_one(
                {"cohort_key": cohort_key},
                {"$set": {"cohort_key": cohort_key, "metrics": metrics,
                          "updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.debug("upsert_cohort_stat failed: %s", e)
            return False

    def save_feature_snapshot(self, farmer_id: str, features: Dict,
                              index_version: str = "index_v5") -> bool:
        """
        Persist the per-parcel raw feature/sub-index snapshot for a farmer so the
        future validation/calibration layer can retro-score history. Append-only
        (keyed by farmer + version + timestamp).
        """
        if self.feature_store is None:
            return False
        try:
            self.feature_store.insert_one({
                "farmer_id": farmer_id,
                "index_version": index_version,
                "features": features,
                "created_at": datetime.now(timezone.utc),
            })
            return True
        except Exception as e:
            logger.debug("save_feature_snapshot failed: %s", e)
            return False

    def save_index_version(self, index_version: str, weights: Dict,
                           notes: Optional[str] = None) -> bool:
        """Register an index version's weight vector / formula provenance."""
        if self.index_versions is None:
            return False
        try:
            self.index_versions.update_one(
                {"index_version": index_version},
                {"$set": {"index_version": index_version, "weights": weights,
                          "notes": notes or "", "updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            return True
        except Exception as e:
            logger.debug("save_index_version failed: %s", e)
            return False

    def save_multi_farm_assessment(self, farmer_id: str, farmer_result: Dict) -> Optional[str]:
        """
        INSERT a farmer-level (multi-plot) assessment as history (audit trail),
        mirroring single-farm save_assessment — NOT an upsert. Dashboard reads
        job.result for the live view.

        RAISES on write failure (the caller logs it). Note this path bypasses
        AssessmentSchema entirely and inserts the aggregator output raw, so the
        resulting document has a different key set from single-farm documents —
        see BACKEND-ENHANCEMENTS.md §5.2; unification is Phase 5 work.
        """
        if self.assessments is None:
            logger.debug("save_multi_farm_assessment: no assessments collection")
            return None
        try:
            doc = dict(farmer_result)
            doc["farmer_id"] = farmer_id
            doc["assessment_type"] = "multi_farm"
            doc["created_at"] = datetime.now(timezone.utc)
            res = self.assessments.insert_one(doc)
            return str(res.inserted_id)
        except Exception as e:
            logger.error(
                "❌ save_multi_farm_assessment FAILED for farmer=%s — "
                "assessment NOT persisted: %s", farmer_id, e,
            )
            raise

    # ── Connection management ─────────────────────────────────────────────

    def close(self):
        if self.client:
            self.client.close()
            logger.info("🔌 MongoDB connection closed")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()