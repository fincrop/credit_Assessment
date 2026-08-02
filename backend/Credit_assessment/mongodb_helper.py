"""
MongoDB Helper
==============
Database operations for the satellite-based agricultural credit assessment pipeline.

VERSION 3.0 — Structured schema with analytics queries

COLLECTIONS
───────────
farm_info
    One document per physical farm / farmer.
    Holds identity, location, geometry, and farmer-benefit flags.

credit_assessments
    One document per pipeline run.
    Structured for quick look-up (top-level credit fields) AND
    deep analytical queries (seasonal_ndvi, weather events, component scores).
    Raw satellite pixel/band arrays are excluded to keep docs ≤ 1 MB.

SCHEMA (credit_assessments)
────────────────────────────
{
  farmer_id, assessment_date, pipeline_version, status, processing_time_s,

  location: { latitude, longitude, region, field_area_ha }

  credit_score, risk_category,                    ← TOP-LEVEL for fast queries
  recommended_credit_limit, limit_per_hectare,
  interest_rate, repayment_months, collateral_required,

  component_scores: { crop_detection, crop_performance, yield_potential,
                      cropping_intensity, weather_risk, govt_benefits }
  weak_components: [str]

  cropping_summary: { seasons_with_crops, total_seasons_analyzed,
                      cropping_intensity, dominant_crop, crops_detected,
                      cross_season_events, ndvi_threshold_used, region }

  seasonal_ndvi: [                                ← per-season records
    { season, year, start_date, end_date,
      crop_detected, predicted_crop, confidence, is_cross_season,
      avg_ndvi, peak_ndvi, ndvi_rise, arc_score, frac_above_thresh,
      health_score, yield_score, n_scenes }
  ]

  weather_summary: { total_extreme_events, critical_stage_events,
                     weather_risk_score, kharif_avg_rainfall_mm,
                     rabi_avg_rainfall_mm, max_recorded_temp_c,
                     extreme_event_breakdown }

  extreme_events: [
    { type, severity, date_or_start, duration_days, value,
      crop_stage_critical }
  ]

  performance_summary: { avg_health_score, avg_yield_score,
                         avg_performance_score, n_seasons_scored }

  govt_benefits: { pm_kisan_enrolled, has_crop_insurance } | null
  conditions: [str], warnings: [str], errors: [str]
  saved_at: datetime
}

INDEXES
───────
credit_assessments:
  { farmer_id: 1, assessment_date: -1 }
  { risk_category: 1 }
  { credit_score: 1 }
  { assessment_date: -1 }
  { location.region: 1, risk_category: 1 }

farm_info:
  { farmer_id: 1 }  UNIQUE
  { status: 1 }
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
_PIPELINE_VERSION      = "3.0"


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
        doc['pipeline_version']  = _PIPELINE_VERSION
        doc['status']            = raw.get('status', 'UNKNOWN')
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
        cleaned = _clean_obj(doc)
        return cleaned or doc

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

        kharif_rain = [
            s['total_rainfall_mm'] for s in seasonal
            if 'kharif' in s.get('season', '') and 'total_rainfall_mm' in s
        ]
        rabi_rain = [
            s['total_rainfall_mm'] for s in seasonal
            if 'rabi' in s.get('season', '') and 'total_rainfall_mm' in s
        ]
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

    @staticmethod
    def _extract_interval_indices(scenes: List[Dict]) -> List[Dict]:
        rows: List[Dict] = []
        for s in scenes or []:
            date = s.get('date')
            idx = s.get('indices') or {}
            if not date or not isinstance(idx, dict):
                continue
            rows.append(
                {
                    'date': str(date)[:10],
                    'ndvi': _f(idx.get('NDVI_mean')),
                    'evi': _f(idx.get('EVI_mean')),
                    'ndmi': _f(idx.get('NDMI_mean')),
                    'savi': _f(idx.get('SAVI_mean')),
                    'gci': _f(idx.get('GCI_mean')),
                }
            )
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
            logger.debug("MongoDB indexes verified")
        except Exception as e:
            logger.warning("Index creation note: %s", e)

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

    def save_assessment(self, raw_assessment: Dict) -> Optional[str]:
        """
        Build structured document from raw pipeline output and insert.

        This is the PRIMARY write method — always use this, not direct insert.
        Returns the MongoDB document _id as a string.
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
            logger.error(f"❌ save_assessment: {e}")
            return None

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
                    'avg_credit_limit':  {'$avg': '$recommended_credit_limit'},
                    'avg_field_area_ha': {'$avg': '$location.field_area_ha'},
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
            logger.debug("save_multi_farm_assessment failed: %s", e)
            return None

    # ── Connection management ─────────────────────────────────────────────

    def close(self):
        if self.client:
            self.client.close()
            logger.info("🔌 MongoDB connection closed")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()