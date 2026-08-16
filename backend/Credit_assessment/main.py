"""
Satellite-based agronomic risk pipeline (v5.0 / index_v5).

Single production path: continuous Sentinel-2, crop cycles, cycle-aligned weather and
performance, RiskIndexEngine. Architecture: BACKEND-ARCHITECTURE-v5.md.
"""
 
from __future__ import annotations

# ============================================================================
# FIX: PROJ/GDAL database version mismatch (Windows conda environments)
# ============================================================================
import os
import sys
from pathlib import Path


# Set PROJ_LIB and GDAL_DATA to conda environment paths
_cprefix = sys.prefix
_proj_path = os.path.join(_cprefix, 'Library', 'share', 'proj')
if os.path.isdir(_proj_path):
    os.environ['PROJ_LIB'] = _proj_path
    
_gdal_path = os.path.join(_cprefix, 'Library', 'share', 'gdal')
if os.path.isdir(_gdal_path):
    os.environ['GDAL_DATA'] = _gdal_path

# Suppress PROJ/GDAL warnings after setting correct paths
import warnings
warnings.filterwarnings('ignore', category=RuntimeWarning, module='rasterio')
warnings.filterwarnings('ignore', message='.*PROJ.*')
warnings.filterwarnings('ignore', message='.*EPSG.*')

# KMeans + MKL on Windows: avoids sklearn memory-leak warning when chunks < threads
if sys.platform == 'win32':
    os.environ.setdefault('OMP_NUM_THREADS', '1')

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass

# ============================================================================
# Continue with normal imports
# ============================================================================
 
import gc
import json
import logging
import traceback
import hashlib
import inspect
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

# Pickled crop model: sklearn emits unpickle version skew warnings without needing
# a top-level sklearn import (keeps editors/pyright happy when sklearn path differs).
warnings.filterwarnings(
    "ignore",
    message=r"Trying to unpersist estimator\b.*",
)
warnings.filterwarnings(
    "ignore",
    message=r".*[Uu]npickle estimator.*",
    category=UserWarning,
)

# Import existing components
from data_acquisition.satellite_collector import SatelliteDataCollector
from data_acquisition.weather_analyzer import WeatherAnalyzer
from crop_analysis.crop_detector import CropDetector
from crop_analysis.crop_cycle_detector import CropCycleDetector
from crop_analysis.land_cover_gate import (
    classify_land_cover,
    confidence_gate_penalty,
    FLAG as LC_FLAG,
    REJECT as LC_REJECT,
)
from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
from crop_analysis.performance_analyzer import CropPerformanceAnalyzer
from assessment.risk_index_engine import RiskIndexEngine, INDEX_VERSION
from assessment.legacy_credit_shim import legacy_credit_shim
from assessment.evidence_snapshot import (
    build_evidence_document,
    build_score_history_entry,
)
from assessment.data_sufficiency import INSUFFICIENT, assess_data_sufficiency
from assessment.parcel_viability import (
    MARGINAL,
    NOT_VIABLE,
    assess_parcel_viability,
)
from utils.peer_benchmark import PeerBenchmark
from config import PipelineConfig
from utils.farmer_benefits import merge_farmer_benefits, normalize_farmer_benefits
from utils.india_geo_context import infer_agro_ecoregion

try:
    from mongodb_helper import MongoDBHelper
    MONGODB_AVAILABLE = True
except ImportError:
    MONGODB_AVAILABLE = False
    print("WARNING: mongodb_helper not found -- database features disabled")
 
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(name)-28s  %(levelname)-8s  %(message)s',
)
logger = logging.getLogger(__name__)
_VERSION = "5.0"
 
 
class SatelliteBasedCreditPipeline:
    """End-to-end satellite agronomic risk assessment (index_v5)."""

    def __init__(
        self,
        crop_model_path: str,
        ml_mode: str = 'rule_based',
        verbose: bool = True,
        use_mongodb: bool = True,
    ):
        """
        Args:
            crop_model_path: Path to crop classification model (joblib).
            ml_mode: Reserved (crop ML challenger); scoring is RiskIndexEngine.
            verbose: Detailed logging for the pipeline shell.
            use_mongodb: Connect to MongoDB for Stage 0 / persistence when available.
        """
        self.verbose = verbose
        self.crop_model_path = crop_model_path
        self.ml_mode = ml_mode
        self.use_mongodb = use_mongodb and MONGODB_AVAILABLE

        logger.info(f"\n{'#'*70}")
        logger.info(f"# SATELLITE RISK PIPELINE  v{_VERSION} ({INDEX_VERSION})")
        logger.info(f"# Scorer: RiskIndexEngine")
        logger.info(f"{'#'*70}\n")
        logger.info(
            "Integrations: MongoDB %s",
            "on" if self.use_mongodb else "off",
        )
 
        # Initialize MongoDB
        self.db = None
        if self.use_mongodb:
            try:
                self.db = MongoDBHelper()
                logger.info("MongoDB enabled")
                try:
                    self.db.save_index_version(
                        INDEX_VERSION,
                        dict(getattr(PipelineConfig, "SUBINDEX_WEIGHTS", {}) or {}),
                        notes="pipeline startup registration",
                    )
                except Exception as e:
                    logger.debug("save_index_version at startup skipped: %s", e)
            except Exception as e:
                logger.warning(f"MongoDB unavailable: {e}  --  continuing without DB")
                self.use_mongodb = False
 
        # Initialize core components
        self.satellite_collector = SatelliteDataCollector(verbose=False)
        self.weather_analyzer = None
        self.crop_detector = None
        self.performance_analyzer = CropPerformanceAnalyzer(verbose=False)

        self.risk_index_engine = RiskIndexEngine(verbose=True)
        if self.db is not None and hasattr(self.db, "get_cohort_stats"):
            self.peer_benchmark = PeerBenchmark.from_mongo(self.db)
            logger.info("RiskIndexEngine + PeerBenchmark (MongoDBHelper) initialized")
        else:
            self.peer_benchmark = PeerBenchmark.from_mongo(None)
            if self.use_mongodb:
                logger.warning(
                    "PeerBenchmark: MongoDBHelper missing get_cohort_stats — cold-start"
                )
            else:
                logger.info("RiskIndexEngine + PeerBenchmark (cold-start) initialized")

        self.crop_cycle_detector = CropCycleDetector()
        self.land_utilization_analyzer = LandUtilizationAnalyzer()

        logger.info("Pipeline initialized\n")

    @staticmethod
    def _failed_assessment_shell(farmer_id: str, error: str) -> Dict:
        """Minimal consistent payload when the run stops before Stage 1 completes."""
        now = datetime.now()
        return {
            'farmer_id': farmer_id,
            'status': 'FAILED',
            'error': error,
            'pipeline_version': _VERSION,
            'pipeline_profile': 'index_v5',
            'index_version': INDEX_VERSION,
            'assessment_date': now.isoformat(),
            'processing_time_seconds': 0.0,
            'pipeline_stages': [],
            'warnings': [],
            'errors': [error],
        }

    @staticmethod
    def _bucket_cache_end_date(end_date: str) -> str:
        """
        Stabilize satellite cache keys within an ISO week so re-runs / multi-plot
        jobs reuse cache instead of missing every calendar day.
        """
        try:
            d = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
        except Exception:
            return str(end_date)[:10]
        monday = d - timedelta(days=d.weekday())
        return monday.strftime("%Y-%m-%d")

    @staticmethod
    def _build_satellite_cache_key(
        farmer_id: str,
        latitude: Optional[float],
        longitude: Optional[float],
        field_area_ha: Optional[float],
        geometry: Optional[object],
        interval_days: int,
        start_date: str,
        end_date: str,
        plot_key: Optional[str] = None,
    ) -> str:
        raw = {
            'farmer_id': farmer_id,
            'plot_key': (plot_key or '').strip() or None,
            'latitude': latitude,
            'longitude': longitude,
            'field_area_ha': field_area_ha,
            'geometry': geometry,
            'interval_days': interval_days,
            'start_date': start_date,
            'end_date': SatelliteBasedCreditPipeline._bucket_cache_end_date(end_date),
            'provider': os.environ.get('SATELLITE_PROVIDER', 'gee').strip().lower() or 'gee',
            'pipeline_version': _VERSION,
        }
        raw_json = json.dumps(raw, sort_keys=True, default=str)
        return hashlib.sha256(raw_json.encode('utf-8')).hexdigest()

    # ------------------------------------------------------------------
    # Helper: build mock cropping_analysis when classification is off
    # ------------------------------------------------------------------

    @staticmethod
    def _scenes_for_cycle_window(
        all_scenes: List[Dict],
        start: str,
        end: str,
    ) -> List[Dict]:
        """Bins from the continuous grid whose ``date`` falls in [start, end] (inclusive)."""
        if not all_scenes or not start or not end:
            return []
        out: List[Dict] = []
        for s in all_scenes:
            if s.get('missing'):
                continue
            d = (s.get('date') or '')[:10]
            if len(d) < 10:
                continue
            if start <= d <= end:
                out.append(s)
        return sorted(out, key=lambda x: x.get('date', ''))

    @staticmethod
    def _build_unclassified_analysis(
        crop_cycles,
        continuous_data: Dict,
        registry_crop: Optional[str] = None,
    ) -> Dict:
        """
        Build a mock ``cropping_analysis`` dict that mimics the schema returned by
        ``CropDetector.analyze_cycles()`` without running the ML classifier.

        - Every cycle detected by CropCycleDetector is represented.
        - ``predicted_crop`` is ``None`` unless ``registry_crop`` is provided
          (self-reported / registry, unverified).
        - ``start_date`` / ``end_date`` / ``duration_days`` are copied verbatim.
        - ``cultivation_signal`` is derived from ``peak_ndvi`` as a proxy.
        - ``season_type`` / ``season_label`` are carried through from the cycle so
          downstream consumers can attribute a cycle to kharif/rabi/zaid. The
          ``season`` field remains the positional ``cycle_N`` id.

        NOTE: this path does NOT unlock the crop-specific Stage 06/07 scoring
        paths. ``crop_confidence`` is 0.0 here and ``is_crop_reliable`` in
        performance_analyzer requires >= 0.25, so a registry crop currently sets
        the label only. Gating a registry crop on observed-phenology consistency
        is planned (see BACKEND-ENHANCEMENTS.md task 4.4).
        """

        def _get(cyc, key: str):
            """Reads a field from a CropCycle object or its to_dict() representation."""
            if isinstance(cyc, dict):
                return cyc.get(key)
            if key == 'start_date':
                v = getattr(cyc, 'start_date', None) or getattr(cyc, 'sowing_date', None)
                return v.strftime('%Y-%m-%d') if hasattr(v, 'strftime') else v
            if key == 'end_date':
                v = getattr(cyc, 'end_date', None) or getattr(cyc, 'harvest_date', None)
                return v.strftime('%Y-%m-%d') if hasattr(v, 'strftime') else v
            return getattr(cyc, key, None)

        registry_crop = (registry_crop or "").strip() or None
        season_results: List[Dict] = []
        n_cycles = len(crop_cycles)

        for i, cycle in enumerate(crop_cycles, 1):
            start_str  = _get(cycle, 'start_date')
            end_str    = _get(cycle, 'end_date')
            dur_days   = int(_get(cycle, 'duration_days') or 0)
            peak_ndvi  = float(_get(cycle, 'peak_ndvi') or 0.0)
            confidence = float(_get(cycle, 'confidence') or 0.0)

            if not start_str or not end_str:
                continue

            # Proxy cultivation_signal from peak_ndvi (0–100 scale)
            cultivation_signal = round(min(100.0, max(0.0, peak_ndvi * 120.0)), 1)

            cycle_scenes = SatelliteBasedCreditPipeline._scenes_for_cycle_window(
                continuous_data.get('scenes') or [], start_str, end_str
            )

            row = {
                'season':               f'cycle_{i}',
                'season_type':          _get(cycle, 'season_type'),
                'season_label':         _get(cycle, 'season_label'),
                'year':                 int(start_str[:4]),
                'start_date':           start_str,
                'end_date':             end_str,
                'crop_detected':        True,
                'cultivation_signal':   cultivation_signal,
                'predicted_crop':       registry_crop,
                'crop_confidence':      0.0,
                'crop_label_source':    (
                    'registry_self_report' if registry_crop else 'unclassified'
                ),
                'classification_note':  (
                    'registry_crop_as_predicted_crop'
                    if registry_crop
                    else 'classification_skipped_by_feature_flag'
                ),
                'all_probabilities':    {},
                'cycle_confidence':     confidence,
                'duration_days':        dur_days,
                'is_cycle_based':       True,
                'scenes':               cycle_scenes,
                'peak_ndvi':            round(peak_ndvi, 4),
                'n_scenes':             len(cycle_scenes),
            }
            season_results.append(row)

        # ``cropping_intensity`` / ``cycles_per_year`` for credit (0–3.5)
        # ``land_utilization_fraction`` = cultivated-days / calendar-days
        try:
            s_dt = datetime.strptime(continuous_data['start_date'], '%Y-%m-%d')
            e_dt = datetime.strptime(continuous_data['end_date'],   '%Y-%m-%d')
            total_days = max((e_dt - s_dt).days, 1)
            cultivated = sum(int(_get(c, 'duration_days') or 0) for c in crop_cycles)
            years_span = max(total_days / 365.25, 0.25)
            cycles_per_year = round(min(3.5, n_cycles / years_span), 3)
            land_utilization_fraction = round(min(1.0, cultivated / float(total_days)), 4)
        except Exception:
            years_span = 3.0
            cycles_per_year = round(min(3.5, n_cycles / years_span), 3)
            land_utilization_fraction = 0.0

        sig_vals   = [r['cultivation_signal'] for r in season_results]
        avg_signal = round(float(sum(sig_vals) / max(len(sig_vals), 1)), 1) if sig_vals else 0.0

        crops_detected = {}
        if n_cycles:
            key = registry_crop or 'Unclassified'
            crops_detected = {key: n_cycles}

        return {
            'season_results':           season_results,
            'crops_detected':           crops_detected,
            'dominant_crop':            registry_crop,
            'cropping_intensity':       cycles_per_year,  # alias kept for scorers
            'cycles_per_year':          cycles_per_year,
            'land_utilization_fraction': land_utilization_fraction,
            'cultivated_day_fraction':  land_utilization_fraction,  # legacy alias
            'cultivation_signal':       avg_signal,
            'avg_cultivation_signal':   avg_signal,
            'seasons_with_crops':       len(season_results),
            'total_seasons_analyzed':   n_cycles,
            'region':                   'UNCLASSIFIED',
            'ndvi_threshold_used':      0.0,
            'detection_mode':           (
                'cycle_based_registry_crop' if registry_crop
                else 'cycle_based_unclassified'
            ),
            'classification_enabled':   False,
            'crop_label_source':        (
                'registry_self_report' if registry_crop else 'unclassified'
            ),
            # 'annual' | 'perennial'. Read by RiskIndexEngine._sub_landuse to
            # pick the scoring basis: cycles-per-year is meaningless for an
            # orchard, where the signal is canopy persistence instead.
            'cycle_kind':               (
                'perennial'
                if any(
                    str(_get(c, 'cycle_kind') or 'annual').lower() == 'perennial'
                    for c in crop_cycles
                )
                else 'annual'
            ),
        }

    @staticmethod
    def _slim_satellite_for_cache(satellite_data: Dict) -> Dict:
        """Persist index grid + meta only (drop accidental fat blobs)."""
        if not isinstance(satellite_data, dict):
            return satellite_data
        out = dict(satellite_data)
        cd = out.get('continuous_data')
        if isinstance(cd, dict):
            slim_cd = dict(cd)
            scenes = slim_cd.get('scenes') or []
            slim_scenes = []
            for s in scenes:
                if not isinstance(s, dict):
                    continue
                slim_scenes.append({
                    'date': s.get('date'),
                    'missing': s.get('missing', False),
                    'cloud_cover': s.get('cloud_cover'),
                    'indices': dict(s.get('indices') or {}),
                })
            slim_cd['scenes'] = slim_scenes
            out['continuous_data'] = slim_cd
        return out

    # ------------------------------------------------------------------
    # Public: database mode
    # ------------------------------------------------------------------

    def assess_farmer_from_db(
        self,
        farmer_id: str,
        farmer_benefits_override: Optional[Dict] = None,
        enable_crop_classification: bool = False,
        force_fresh_satellite: bool = False,
        save_to_db: bool = True,
    ) -> Dict:
        """
        Fetch farm from MongoDB, run the full assessment, and (by default) save.

        save_to_db=False runs read-only. Needed by diagnostics such as the score
        drift report, which must not write into the very collection it is
        measuring against — a re-run that persisted would become its own
        baseline on the next pass.
        """
        farmer_id = (farmer_id or "").strip()
        if not farmer_id:
            return self._failed_assessment_shell('', 'farmer_id is required')

        if not self.use_mongodb:
            return self._failed_assessment_shell(
                farmer_id,
                'MongoDB not available. Use assess_farmer() instead.',
            )

        logger.info(f"\n{'='*70}\nFETCHING FARM: {farmer_id}\n{'='*70}\n")

        farm = self.db.get_farm_by_id(farmer_id)
        if farm is None:
            msg = f"Farmer {farmer_id} not found in database"
            logger.error(f"ERROR: {msg}")
            return self._failed_assessment_shell(farmer_id, msg)
 
        # Extract crop hint and sowing date from database (helps with analysis)
        crop_hint = farm.get('crop')  # e.g., "Potato"
        sowing_date = farm.get('sowing_date')  # e.g., "2022-12-15"
        
        if crop_hint:
            logger.info("Crop hint from database: %s", crop_hint)
        if sowing_date:
            logger.info("Sowing date from database: %s", sowing_date)

        geometry = self._convert_geometry_from_db(farm.get("geometry"))

        db_benefits = farm.get('farmer_benefits') if isinstance(farm.get('farmer_benefits'), dict) else {}
        merged_benefits = merge_farmer_benefits(db_benefits, farmer_benefits_override)

        return self.assess_farmer(
            farmer_id=farmer_id,
            latitude=farm.get('latitude'),
            longitude=farm.get('longitude'),
            field_area_ha=farm.get('field_area_ha'),
            geometry=geometry,
            farmer_benefits=merged_benefits,
            crop_hint=crop_hint,
            sowing_date=sowing_date,
            farm_metadata={
                'state_lgd_code': farm.get('state_lgd_code'),
                'district_lgd_code': farm.get('district_lgd_code'),
            },
            save_to_db=save_to_db,
            enable_crop_classification=enable_crop_classification,
            force_fresh_satellite=force_fresh_satellite,
        )
 
    # ------------------------------------------------------------------
    # Public: direct mode
    # ------------------------------------------------------------------
 
    def assess_farmer(
        self,
        farmer_id: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        field_area_ha: Optional[float] = None,
        geometry: Optional[object] = None,
        farmer_benefits: Optional[Dict] = None,
        crop_hint: Optional[str] = None,
        sowing_date: Optional[str] = None,
        farm_metadata: Optional[Dict] = None,
        save_to_db: bool = True,
        enable_crop_classification: bool = False,
        force_fresh_satellite: bool = False,
        skip_ai_enrichment: bool = False,
    ) -> Dict:
        """
        Run the complete assessment pipeline
        
        Args:
            farmer_id: Unique farmer identifier
            latitude: Farm center latitude
            longitude: Farm center longitude
            field_area_ha: Field area in hectares
            geometry: Field polygon (Shapely object)
            farmer_benefits: Government benefits info
            crop_hint: Crop type from database (optional)
            sowing_date: Sowing date from database (optional)
            farm_metadata: Optional keys state_lgd_code, district_lgd_code (Agristack ingest)
            save_to_db: Save results to MongoDB
            enable_crop_classification: When False (default), bypasses the ML crop
                classifier entirely. Cycle dates are preserved; crop names are set to
                Unclassified (or registry crop when a hint exists) and downstream
                scoring uses crop-agnostic PATH B unless a registry crop unlocks
                crop-specific paths.
            force_fresh_satellite: Skip satellite cache for this assessment only
                (env SATELLITE_FORCE_FRESH remains a process-wide override).
            skip_ai_enrichment: When True, skip STEP 8 (used for multi-farm per-plot
                runs; enrichment can run once on the farmer aggregate).
            
        Returns:
            Complete assessment dictionary with all analysis results
        """
 
        farmer_id = (farmer_id or "").strip()
        if not farmer_id:
            return self._failed_assessment_shell('', 'farmer_id is required')

        farm_md_raw = farm_metadata if isinstance(farm_metadata, dict) else {}
        farm_md = {
            k: farm_md_raw.get(k)
            for k in (
                'state_lgd_code',
                'district_lgd_code',
                'farm_id',
                'plot_key',
            )
            if farm_md_raw.get(k) not in (None, '')
        }

        if isinstance(crop_hint, str):
            crop_hint = crop_hint.strip() or None
        if isinstance(sowing_date, str):
            sowing_date = sowing_date.strip() or None

        if farmer_benefits is None:
            pass  # scorer treats missing benefits as neutral (unknown)
        elif isinstance(farmer_benefits, dict):
            farmer_benefits = normalize_farmer_benefits(farmer_benefits)
        else:
            farmer_benefits = {}

        logger.info(f"\n{'='*70}\nFARMER ASSESSMENT: {farmer_id}\n{'='*70}\n")
        start_time = datetime.now()

        assessment: Dict = {
            'farmer_id': farmer_id,
            'assessment_date': start_time.isoformat(),
            'pipeline_version': _VERSION,
            'pipeline_profile': 'index_v5',
            'index_version': INDEX_VERSION,
            'ml_mode': self.ml_mode,
            'pipeline_stages': ['1_shell'],
            'farmer_benefits': farmer_benefits,
            'crop_hint': crop_hint,
            'sowing_date': sowing_date,
            'warnings': [],
            'errors': [],
        }
 
        try:
            # ── PARCEL VIABILITY ─────────────────────────────────────────
            # Can this parcel be honestly measured at 10 m at all? Runs BEFORE
            # the satellite pull: there is no point spending quota on a footprint
            # of four pixels, where the AOI mean is mostly the neighbouring field.
            viability = assess_parcel_viability(
                registered_ha=field_area_ha,
                geometry_ha=(geometry.area_ha if hasattr(geometry, 'area_ha') else None),
                farmer_id=farmer_id,
            )
            assessment['parcel_viability'] = viability

            if viability['outcome'] == NOT_VIABLE:
                assessment['status'] = 'INSUFFICIENT_DATA'
                assessment['insufficient_reason'] = viability['reason']
                assessment['processing_time_seconds'] = (
                    datetime.now() - start_time
                ).total_seconds()
                logger.warning("Assessment stopped: %s", viability['reason'])
                if save_to_db and self.use_mongodb:
                    try:
                        self.db.save_assessment(assessment)
                    except Exception as e:
                        logger.error("Could not persist viability result: %s", e)
                gc.collect()
                return assessment

            if viability['outcome'] == MARGINAL:
                assessment['warnings'].append(
                    f"Parcel viability: {viability['reason']}"
                )

            # ============================================================
            # STEP 1: SATELLITE DATA COLLECTION (Continuous, season-aligned)
            # ============================================================
            logger.info("STEP 1: Collecting continuous satellite data...")
            today = date.today()
            snapped_start = self.satellite_collector._snap_to_season_start(today, lookback_years=3)
            interval_days = max(
                1,
                int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)),
            )
            cache_key = self._build_satellite_cache_key(
                farmer_id=farmer_id,
                latitude=latitude,
                longitude=longitude,
                field_area_ha=field_area_ha,
                geometry=geometry,
                interval_days=interval_days,
                start_date=snapped_start.strftime('%Y-%m-%d'),
                end_date=today.strftime('%Y-%m-%d'),
                plot_key=(farm_md.get("plot_key") or farm_md.get("farm_id")),
            )

            satellite_data = None
            # Opt-in bypass: SATELLITE_FORCE_FRESH=1|true|yes skips cache reads.
            # Per-assessment force_fresh_satellite also skips (appeal / refresh).
            force_fresh_download = force_fresh_satellite or (
                os.environ.get("SATELLITE_FORCE_FRESH", "").strip().lower()
                in ("1", "true", "yes")
            )
            if force_fresh_download:
                logger.info(
                    "STEP 1: Skipping cache read (force_fresh_satellite=%s env=%s)",
                    force_fresh_satellite,
                    os.environ.get("SATELLITE_FORCE_FRESH", ""),
                )
            if not force_fresh_download and self.use_mongodb and self.db:
                satellite_data = self.db.get_satellite_stats_cache(cache_key)
                if satellite_data:
                    logger.info("STEP 1: Loaded satellite stats from cache")

            if not satellite_data:
                satellite_data = self.satellite_collector.collect_historical_data(
                    latitude=latitude,
                    longitude=longitude,
                    field_area_ha=field_area_ha,
                    geometry=geometry,
                )
                if self.use_mongodb and self.db:
                    # Slim cache: drop heavy raw blobs if present; keep grid series.
                    cache_payload = self._slim_satellite_for_cache(satellite_data)
                    self.db.upsert_satellite_stats_cache(
                        cache_key=cache_key,
                        satellite_data=cache_payload,
                        metadata={
                            'farmer_id': farmer_id,
                            'provider': satellite_data.get('satellite_provider')
                                or (os.environ.get('SATELLITE_PROVIDER', 'gee').strip().lower() or 'gee'),
                        },
                        ttl_days=int(os.environ.get('SATELLITE_CACHE_TTL_DAYS', '30')),
                    )

            assessment['satellite_data'] = satellite_data
            assessment['location'] = satellite_data['location']
            gp = satellite_data.get('geospatial_prep')
            if isinstance(gp, dict) and gp:
                assessment['geospatial_prep'] = dict(gp)
            else:
                assessment['geospatial_prep'] = {
                    'snap_logic_version': getattr(
                        PipelineConfig, 'SNAP_LOGIC_VERSION', 'v1_fixed_anchors'
                    ),
                    'window_start': snapped_start.strftime('%Y-%m-%d'),
                    'window_end': today.strftime('%Y-%m-%d'),
                    'geometry_source': 'unknown',
                }
            assessment['satellite_provider'] = satellite_data.get(
                'satellite_provider',
                os.environ.get('SATELLITE_PROVIDER', 'gee').strip().lower() or 'gee',
            )
            assessment['indices_available'] = list(
                satellite_data.get('indices_available') or []
            )
            assessment['indices_sparse'] = list(
                satellite_data.get('indices_sparse') or []
            )
            assessment['cloud_mask_version'] = satellite_data.get(
                'cloud_mask_version', 'scl_qa60_v1'
            )

            geometry_derived_ha = satellite_data.get('field_area_ha')
            assessment['field_area_ha_geometry'] = geometry_derived_ha
            registered_ha = None
            if field_area_ha is not None:
                try:
                    registered_ha = float(field_area_ha)
                    if registered_ha <= 0:
                        registered_ha = None
                except (TypeError, ValueError):
                    registered_ha = None

            if registered_ha is not None:
                assessment['field_area_ha_registered'] = registered_ha
                assessment['field_area_ha'] = registered_ha
                logger.info(
                    "Field area: using registered %.4f ha (geometry-derived %.4f ha for reference)",
                    registered_ha,
                    float(geometry_derived_ha or 0.0),
                )
            else:
                assessment['field_area_ha_registered'] = None
                assessment['field_area_ha'] = geometry_derived_ha

            clat = satellite_data['location']['latitude']
            clon = satellite_data['location']['longitude']

            eco_key, eco_profile = infer_agro_ecoregion(
                clat,
                clon,
                farm_md.get('state_lgd_code'),
            )
            assessment['agro_geo_context'] = {
                'ecoregion': eco_key,
                **{k: v for k, v in eco_profile.items() if k != 'narrative'},
                'narrative': eco_profile.get('narrative', ''),
            }
            if isinstance(assessment.get('geospatial_prep'), dict):
                assessment['geospatial_prep']['eco_region'] = eco_key

            continuous_data = satellite_data.get('continuous_data') or {}
            scenes_list = continuous_data.get('scenes') or []
            n_valid = sum(1 for s in scenes_list if not s.get('missing'))
            if not scenes_list:
                raise ValueError("No continuous satellite grid was built for this field.")
            if n_valid < 12:
                raise ValueError(
                    f"Too few usable satellite observations ({n_valid}); "
                    "need at least 12 clear bins for analysis."
                )

            # ── LAND COVER GATE ──────────────────────────────────────────
            # Is this parcel farmland at all? Runs post-acquisition (so it can
            # use the observed series) and pre-analysis (so we never spend
            # effort producing a credit signal for a lake).
            #
            # The verdict is stamped on the assessment either way — including
            # on a pass — so a lender can see the evidence, not just the answer.
            if bool(getattr(PipelineConfig, 'LANDCOVER_GATE_ENABLED', True)):
                land_cover = classify_land_cover(
                    continuous_data,
                    n_cycles=None,          # cycles are not detected yet
                    field_area_ha=assessment.get('field_area_ha'),
                    location={'latitude': clat, 'longitude': clon},
                    agro_profile=eco_profile,
                    registry_crop=crop_hint,
                    # So the gate can tell whether it measured the farmer's
                    # actual boundary or a substituted buffer.
                    geospatial_prep=assessment.get('geospatial_prep'),
                )
                assessment['land_cover'] = land_cover
                assessment['pipeline_stages'].append('2b_land_cover')

                if land_cover['outcome'] == LC_REJECT:
                    # Distinct terminal state, NOT a failure: nothing went
                    # wrong, we simply decline to score non-agricultural land.
                    # Surfacing this as FAILED would make "we refuse to score a
                    # lake" indistinguishable from "Earth Engine timed out".
                    assessment['status'] = 'REJECTED_NOT_AGRICULTURAL'
                    assessment['rejection_reason'] = land_cover['reason']
                    assessment['rejection_class'] = land_cover['class']
                    assessment['processing_time_seconds'] = (
                        datetime.now() - start_time
                    ).total_seconds()
                    logger.warning(
                        "Assessment stopped: %s (%s, confidence %.2f)",
                        land_cover['reason'], land_cover['class'],
                        land_cover['confidence'],
                    )
                    if save_to_db and self.use_mongodb:
                        try:
                            self.db.save_assessment(assessment)
                        except Exception as e:
                            logger.error("Could not persist rejection: %s", e)
                    gc.collect()
                    return assessment

                if land_cover['outcome'] == LC_FLAG:
                    assessment['warnings'].append(
                        f"Land cover flagged: {land_cover['reason']}"
                    )

            # Initialize analyzers with location
            self.weather_analyzer = WeatherAnalyzer(
                latitude=clat,
                longitude=clon,
                verbose=False,
                interval_days=continuous_data.get("interval_days"),
                mongo_helper=self.db if self.use_mongodb else None,
            )
            if enable_crop_classification:
                self.crop_detector = CropDetector(
                    crop_model_path=self.crop_model_path,
                    latitude=clat,
                    longitude=clon,
                    verbose=False,
                    state_lgd_code=farm_md.get('state_lgd_code'),
                    district_lgd_code=farm_md.get('district_lgd_code'),
                )
            else:
                logger.info(
                    "Crop classification DISABLED — CropDetector model load skipped."
                )
                self.crop_detector = None

            # Add continuous data stats
            assessment['continuous_data_stats'] = {
                'grid_slots': len(scenes_list),
                'valid_observations': n_valid,
                'missing_observations': len(scenes_list) - n_valid,
                'interval_days': continuous_data.get('interval_days'),
                'date_range': {
                    'start': continuous_data.get('start_date'),
                    'end': continuous_data.get('end_date'),
                },
                'total_days': continuous_data.get('total_days', 0),
            }
            assessment['pipeline_stages'].append('2_satellite')
            assessment['pipeline_stages'].append('3_analysers')

            # ============================================================
            # STEP 2: Dynamic Crop Cycle Detection
            # ============================================================
            logger.info("\nSTEP 2: Detecting crop cycles from continuous data...")
            crop_cycles = []
            utilization_metrics = None

            if n_valid < 20:
                logger.warning(
                    "Low valid observation count: %d clear bins (prefer 20+ for robust cycles)",
                    n_valid,
                )
            if len(continuous_data.get('dates', [])) >= 10:
                grid_step = continuous_data.get('interval_days')
                if grid_step is None:
                    grid_step = getattr(
                        PipelineConfig, 'CONTINUOUS_SCENE_INTERVAL_DAYS', 10
                    )
                _detect = self.crop_cycle_detector.detect_cycles
                _cycle_kwargs = {
                    'dates': continuous_data['dates'],
                    'ndvi_values': continuous_data['ndvi_values'],
                    'evi_values': continuous_data.get('evi_values'),
                    'ndmi_values': continuous_data.get('ndmi_values'),
                    'scenes': continuous_data.get('scenes'),
                    'grid_step_days': float(grid_step),
                    'sowing_date_hint': sowing_date,
                    'crop_hint': crop_hint,
                    'agro_profile': eco_profile,
                    # The Whittaker-smoothed composite. It was already being
                    # computed and stored, and then read by nothing — the
                    # detector re-derived its own signal from the UNSMOOTHED
                    # VS_mean and applied a 70-day moving average, which
                    # flattens any crop shorter than about 90 days.
                    'composite_smooth_values': continuous_data.get('vs_smooth'),
                }
                _sig = inspect.signature(_detect)
                _params = _sig.parameters
                if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in _params.values()):
                    crop_cycles = _detect(**_cycle_kwargs)
                else:
                    _allowed = {name for name in _params if name != 'self'}
                    crop_cycles = _detect(
                        **{k: v for k, v in _cycle_kwargs.items() if k in _allowed}
                    )
                _meta = getattr(self.crop_cycle_detector, 'last_detection_meta', None)
                assessment['cycle_detection_diag'] = dict(_meta or {})

                # ── DATA SUFFICIENCY ─────────────────────────────────────
                # Did we see this parcel well enough to make any claim?
                #
                # Zero cycles is only a finding if we were actually looking.
                # A contiguous blind stretch longer than a crop cycle could
                # have hidden an entire season, and scoring that parcel
                # VERY_HIGH would deny a farmer credit on the strength of the
                # satellite's cloud luck rather than on their land.
                if bool(getattr(PipelineConfig, 'DATA_SUFFICIENCY_ENABLED', True)):
                    sufficiency = assess_data_sufficiency(
                        continuous_data,
                        n_cycles=len(crop_cycles or []),
                        field_area_ha=assessment.get('field_area_ha'),
                    )
                    assessment['data_sufficiency'] = sufficiency

                    if sufficiency['outcome'] == INSUFFICIENT:
                        assessment['status'] = 'INSUFFICIENT_DATA'
                        assessment['insufficient_reason'] = sufficiency['reason']
                        assessment['processing_time_seconds'] = (
                            datetime.now() - start_time
                        ).total_seconds()
                        logger.warning(
                            "Assessment stopped: %s", sufficiency['reason']
                        )
                        if save_to_db and self.use_mongodb:
                            try:
                                self.db.save_assessment(assessment)
                            except Exception as e:
                                logger.error(
                                    "Could not persist insufficient-data result: %s", e
                                )
                        gc.collect()
                        return assessment

                if crop_cycles:
                    logger.info(f"  [OK] Detected {len(crop_cycles)} crop cycles")

                    start_dt = datetime.strptime(
                        continuous_data['start_date'], '%Y-%m-%d'
                    )
                    end_dt = datetime.strptime(
                        continuous_data['end_date'], '%Y-%m-%d'
                    )
                    utilization_metrics = self.land_utilization_analyzer.analyze(
                        cycles=crop_cycles,
                        start_date=start_dt,
                        end_date=end_dt,
                    )
                    if utilization_metrics:
                        logger.info(
                            "  [OK] Land utilization: %.1f%%",
                            100.0 * float(utilization_metrics.get('land_utilization_index', 0) or 0),
                        )

                assessment['pipeline_stages'].append('4_cycles')
            else:
                logger.warning(
                    "Skipping cycle detection: only %d grid points (need 10+)",
                    len(continuous_data.get('dates', [])),
                )

            # ============================================================
            # STEP 3: Crop Classification (optional, controlled by flag)
            # ============================================================
            if enable_crop_classification:
                logger.info("\nSTEP 3: Classifying cycles via ML model...")
                cropping_analysis = self.crop_detector.analyze_cycles(
                    crop_cycles=crop_cycles,
                    all_continuous_scenes=continuous_data.get('scenes', []),
                )
                classification_mode = 'satellite_ndvi_ml_classifier'
                classification_note = (
                    'Crop type inferred from satellite NDVI time-series classifier. '
                    'Discrepancies vs registry sowing records are expected unless a '
                    'crop hint was stored on the farmer record.'
                )
                logger.info(
                    "  \u2713 Classification complete (%d cycles)", len(crop_cycles)
                )
            else:
                # Classification is OFF — build a structurally identical dict from raw
                # CropCycle objects. Exact sowing/harvest dates are preserved.
                # Downstream steps (weather, performance, credit) are fully unaffected.
                logger.info(
                    "\nSTEP 3: Crop classification SKIPPED (enable_crop_classification=False)."
                    " Building generic cycle analysis from %d detected cycle(s)...",
                    len(crop_cycles),
                )
                cropping_analysis = self._build_unclassified_analysis(
                    crop_cycles, continuous_data, registry_crop=crop_hint
                )
                if crop_hint:
                    classification_mode = 'cycle_dates_registry_crop'
                    classification_note = (
                        f'Registry crop "{crop_hint}" used as predicted_crop '
                        '(source=registry_self_report). ML classifier was off.'
                    )
                else:
                    classification_mode = 'cycle_dates_only_no_classification'
                    classification_note = (
                        'Crop classification was disabled. Activity count and dates are '
                        'derived from CropCycleDetector; crop names are Unclassified. '
                        'Credit scoring uses crop-agnostic signal-based metrics.'
                    )
                logger.info(
                    "  \u2713 Generic analysis built — %d cycle(s) preserved with exact dates, "
                    "crop_name=Unclassified.",
                    len(crop_cycles),
                )

            assessment['cropping_analysis'] = cropping_analysis
            assessment['pipeline_stages'].append('5_crops')
            assessment['crop_intelligence_source'] = {
                'dominant_crop_and_cycles':  classification_mode,
                'classification_enabled':    enable_crop_classification,
                'registry_crop_hint':        crop_hint,
                'registry_sowing_hint':      sowing_date,
                'note':                      classification_note,
            }

            # ============================================================
            # STEP 4: Context-Aware Weather Analysis (cycle-aligned)
            # ============================================================
            logger.info("\nSTEP 4: Analyzing cycle-aligned weather...")
            weather_analysis = self.weather_analyzer.analyze_cycle_weather(
                latitude=clat,
                longitude=clon,
                crop_cycles_analysis=cropping_analysis,
            )
            assessment['weather_analysis'] = weather_analysis
            assessment['pipeline_stages'].append('6_weather')

            # ============================================================
            # STEP 5: Dynamic Crop Performance Evaluation (cycle-based)
            # ============================================================
            logger.info("\nSTEP 5: Evaluating crop performance...")

            # Pillar 3/5: cohort key (agro-zone x season x crop-family) for peer
            # benchmarking + risk index. Defensive; peer engine cold-starts anyway.
            cohort_key = None
            _geo = assessment.get('geospatial_prep', {}) or {}
            _agro = _geo.get('eco_region') or _geo.get('agro_climatic_zone') or 'NA'
            _seasons = [getattr(c, 'season_type', '') for c in crop_cycles] if crop_cycles else []
            _season = max(set(_seasons), key=_seasons.count) if _seasons else 'NA'
            cohort_key = PeerBenchmark.cohort_key(_agro, _season, 'NA')

            performance_analysis = self.performance_analyzer.analyze_performance(
                season_results=cropping_analysis.get('season_results', []),
                seasonal_data=[],
                cohort_key=cohort_key,
                peer_benchmark=self.peer_benchmark,
            )
            assessment['performance_analysis'] = performance_analysis
            assessment['pipeline_stages'].append('7_performance')

            # ============================================================
            # STEP 6: Agronomic risk index (index_v5) + credit_assessment shim
            # ============================================================
            logger.info("\nSTEP 6: Calculating agronomic risk index...")

            assessment.setdefault('cropping_analysis', cropping_analysis)
            # Feed land-utilization into cropping_analysis so landuse sub-index
            # can apply fallow penalty / intensity from the utilization analyzer.
            if utilization_metrics and isinstance(cropping_analysis, dict):
                fallow = (utilization_metrics.get('fallow_analysis') or {})
                if fallow.get('fallow_fraction') is not None:
                    cropping_analysis['fallow_fraction'] = fallow.get('fallow_fraction')
                if utilization_metrics.get('crops_per_year') is not None:
                    cropping_analysis.setdefault(
                        'cycles_per_year', utilization_metrics.get('crops_per_year')
                    )
                    cropping_analysis.setdefault(
                        'cropping_intensity', utilization_metrics.get('crops_per_year')
                    )
                if utilization_metrics.get('land_utilization_index') is not None:
                    cropping_analysis['land_utilization_index'] = (
                        utilization_metrics.get('land_utilization_index')
                    )
                assessment['cropping_analysis'] = cropping_analysis

            assessment['farmer_benefits'] = farmer_benefits
            assessment['signal_quality_summary'] = (
                continuous_data.get('signal_quality_summary', {}) or {}
            )
            assessment['cohort_key'] = cohort_key
            assessment['pipeline_profile'] = 'index_v5'
            assessment['index_version'] = INDEX_VERSION

            risk_assessment = self.risk_index_engine.score(
                assessment, cohort_key=cohort_key
            )
            assessment['risk_assessment'] = risk_assessment
            credit_assessment = legacy_credit_shim(risk_assessment)
            assessment['credit_assessment'] = credit_assessment
            assessment['pipeline_stages'].append('8_risk_index')

            # ============================================================
            # STEP 7: Assemble payload (cycles + utilization on assessment)
            # ============================================================
            logger.info("\nSTEP 7: Assembling assessment payload...")
            assessment['crop_cycles'] = {
                'detected': bool(crop_cycles),
                'cycles_count': len(crop_cycles),
                'utilization_metrics': utilization_metrics or {},
                'method': 'continuous_detection',
                'cycles': [c.to_dict() for c in crop_cycles] if crop_cycles else [],
            }
            assessment['pipeline_stages'].append('9_payload')

            # ============================================================
            # STEP 8: AI enrichment (SHAP / counterfactuals / optional LLM)
            # ============================================================
            if not skip_ai_enrichment:
                try:
                    from ai_integration.enrichment import enrich_assessment_with_ai

                    logger.info("\nSTEP 8: AI enrichment (explainability)...")
                    if enrich_assessment_with_ai(assessment):
                        assessment['pipeline_stages'].append('10_ai')
                        logger.info("  [OK] AI enrichment attached (see assessment['ai_enrichment'])")
                except ImportError:
                    pass
            else:
                logger.info("\nSTEP 8: AI enrichment skipped (skip_ai_enrichment=True)")

            # ============================================================
            # FINALIZE
            # ============================================================
            assessment['processing_time_seconds'] = (
                datetime.now() - start_time).total_seconds()
            assessment['status'] = 'SUCCESS'
            assessment['summary'] = self._generate_summary(assessment)
 
            logger.info(
                f"\n{'='*70}\n"
                f"ASSESSMENT COMPLETE\n"
                f"{'='*70}\n"
                f"  Index:  {risk_assessment['index_score']:.1f}/100\n"
                f"  Risk:   {risk_assessment['risk_category']}\n"
                f"  Gate:   {risk_assessment.get('confidence_gate')}\n"
                f"  Method: {risk_assessment.get('method', INDEX_VERSION)}\n"
                f"  Time:   {assessment['processing_time_seconds']:.1f}s\n"
                f"{'='*70}"
            )
 
            # Save to MongoDB (if enabled)
            if save_to_db and self.use_mongodb:
                try:
                    logger.info("\nSTEP 9: Saving to MongoDB...")
                    assessment_id = self.db.save_assessment(assessment)
                    # None on the single-farm path; the multi-farm assessor
                    # passes a stable plot key through farm_metadata.
                    plot_key = (farm_metadata or {}).get('plot_key')

                    # Durable evidence record + trend row.
                    #
                    # Everything below this point was previously discarded: the
                    # per-bin index series survived only in a 30-day cache blob,
                    # and the phenology / stress-event / weather-indicator
                    # detail only in jobs.result. Both are non-fatal — a missing
                    # evidence document must not fail a good assessment — but
                    # both are logged loudly if they fail.
                    self.db.save_evidence(
                        build_evidence_document(
                            assessment,
                            assessment_id=assessment_id,
                            plot_key=plot_key,
                        )
                    )
                    self.db.save_score_history(
                        build_score_history_entry(
                            assessment,
                            assessment_id=assessment_id,
                            plot_key=plot_key,
                        )
                    )

                    # Feature snapshot for future cohort / calibration jobs
                    cal = (risk_assessment.get('calibration') or {})
                    seasonal = (performance_analysis.get('seasonal_performance') or [])
                    nirv_aucs = []
                    for sp in seasonal:
                        if not isinstance(sp, dict):
                            continue
                        yd = sp.get('yield_detail') or {}
                        if isinstance(yd, dict) and yd.get('nirv_auc_mean') is not None:
                            nirv_aucs.append(yd.get('nirv_auc_mean'))
                        elif sp.get('nirv_auc_mean') is not None:
                            nirv_aucs.append(sp.get('nirv_auc_mean'))
                    self.db.save_feature_snapshot(
                        assessment.get('farmer_id'),
                        {
                            "subindex_inputs": cal.get("subindex_inputs") or {},
                            "cohort_key": cohort_key,
                            "nirv_auc_mean_by_cycle": nirv_aucs,
                            "index_score": risk_assessment.get("index_score"),
                            "confidence_gate": risk_assessment.get("confidence_gate"),
                        },
                        index_version=INDEX_VERSION,
                    )
                    assessment['pipeline_stages'].append('11_persist')
                    logger.info("  [OK] Saved successfully")
                except Exception as e:
                    logger.error(f"MongoDB save failed: {e}")
                    assessment['errors'].append(f"MongoDB save failed: {e}")
 
        except Exception as e:
            logger.error(f"\nASSESSMENT FAILED: {e}")
            logger.error(traceback.format_exc())
            assessment['status'] = 'FAILED'
            assessment['error'] = str(e)
            assessment['traceback'] = traceback.format_exc()
            assessment['processing_time_seconds'] = (
                datetime.now() - start_time).total_seconds()
 
        # Persist FAILED runs too.
        #
        # The success-path save above lives inside the try block, after
        # status='SUCCESS' — so before this, a failed assessment produced no
        # database record whatsoever and failure history simply did not exist.
        # That makes "score dropped, why?" unanswerable and hides systematic
        # breakage (a dead satellite provider looks identical to no demand).
        if save_to_db and self.use_mongodb and assessment.get('status') == 'FAILED':
            try:
                logger.info("Persisting FAILED assessment for audit trail...")
                self.db.save_assessment(assessment)
                assessment.setdefault('pipeline_stages', []).append('11_persist_failed')
            except Exception as persist_err:
                # Do not mask the original failure with a persistence failure.
                logger.error(
                    "Could not persist FAILED assessment for %s: %s",
                    assessment.get('farmer_id'), persist_err,
                )
                assessment.setdefault('errors', []).append(
                    f"Failed-assessment persist failed: {persist_err}"
                )

        # Cleanup
        gc.collect()

        return assessment
 
    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------
 
    def assess_multiple_farmers(
        self,
        farmer_ids: List[str],
        batch_size: int = 10
    ) -> List[Dict]:
        """
        Batch process multiple farmers
        """
        logger.info(f"\nBATCH ASSESSMENT: {len(farmer_ids)} farmers")
        results = []
        
        for i, farmer_id in enumerate(farmer_ids, 1):
            logger.info(f"\n[{i}/{len(farmer_ids)}] Processing {farmer_id}...")
            
            try:
                result = self.assess_farmer_from_db(farmer_id)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed: {e}")
                results.append({
                    'farmer_id': farmer_id,
                    'status': 'FAILED',
                    'error': str(e)
                })
            
            # Cleanup after each batch
            if i % batch_size == 0:
                gc.collect()
        
        return results
 
    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------
 
    def _generate_summary(self, assessment: Dict) -> Dict:
        """Generate human-readable summary"""
        ca = assessment.get('credit_assessment', {})
        ra = assessment.get('risk_assessment', {})
        crop = assessment.get('cropping_analysis', {})
        
        summary = {
            'farmer_id': assessment['farmer_id'],
            'credit_score': ca.get('credit_score', ra.get('index_score', 0)),
            'index_score': ra.get('index_score', ca.get('credit_score', 0)),
            'risk_category': ca.get('risk_category', ra.get('risk_category', 'UNKNOWN')),
            'index_version': assessment.get('index_version', ra.get('index_version', INDEX_VERSION)),
            'crops_detected': len(crop.get('crops_detected', {})),
            'cropping_intensity': crop.get('cropping_intensity', 0),
            'scoring_method': ca.get('method', ra.get('method', INDEX_VERSION)),
        }
        
        # Add advanced metrics if available
        if 'crop_cycles' in assessment and assessment['crop_cycles'].get('detected'):
            cc = assessment['crop_cycles']
            summary['crop_cycles_detected'] = cc['cycles_count']
            summary['land_utilization'] = cc['utilization_metrics'].get('land_utilization_index', 0)
            summary['crops_per_year'] = cc['utilization_metrics'].get('crops_per_year', 0)
        
        return summary
 
    @staticmethod
    def _convert_geometry_from_db(geometry):
        """
        MongoDB farm geometry -> Shapely geometry for the collector.

        Supports: Shapely object, GeoJSON dict, or list of dict vertices
        ({latitude,longitude} / {lat,lon} / {lat,lng}). Missing/invalid -> None
        (pipeline falls back to point + field_area_ha).
        """
        if geometry is None or geometry == {} or geometry == []:
            logger.debug("No field polygon in DB; using lat/lon + area if present")
            return None

        try:
            from shapely.geometry import Polygon, shape

            if hasattr(geometry, "geom_type"):
                return geometry

            if isinstance(geometry, dict):
                if "type" in geometry and "coordinates" in geometry:
                    return shape(geometry)
                logger.warning("Geometry dict is not valid GeoJSON (need type + coordinates)")
                return None

            if isinstance(geometry, list) and len(geometry) >= 3:
                coords = []
                for point in geometry:
                    if not isinstance(point, dict):
                        continue
                    lon = point.get("longitude") or point.get("lon") or point.get("lng")
                    lat = point.get("latitude") or point.get("lat")
                    if lon is not None and lat is not None:
                        coords.append((float(lon), float(lat)))
                if len(coords) >= 3:
                    logger.info("Field polygon: %d vertices from DB", len(coords))
                    return Polygon(coords)
                logger.warning("Polygon needs >= 3 vertices; got %d valid", len(coords))
                return None

            logger.warning("Unrecognized geometry type: %s", type(geometry).__name__)
            return None

        except Exception as e:
            logger.error("Geometry conversion failed: %s", e)
            logger.debug(traceback.format_exc())
            return None
 
 
# ==========================================================================
# CLI ENTRY POINT
# ==========================================================================
 
def main():
    """Command-line interface"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Satellite-Based Agronomic Risk Assessment Pipeline v5.0'
    )
    parser.add_argument('--farmer-id', type=str, help='Farmer ID to assess')
    parser.add_argument('--ml-mode', type=str, default='rule_based',
                       choices=['rule_based', 'unsupervised', 'supervised', 'hybrid'],
                       help='Credit scorer mode (default: rule_based; ML blend controlled in config)')
    parser.add_argument('--model-path', type=str, default='models/crop_classifier_model.joblib',
                       help='Path to crop classification model')
    parser.add_argument('--batch-file', type=str, help='JSON file with farmer IDs for batch processing')
    
    args = parser.parse_args()
    
    # Initialize pipeline
    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=args.model_path,
        ml_mode=args.ml_mode,
        verbose=True,
        use_mongodb=True,
    )
    
    # Single farmer assessment
    if args.farmer_id:
        result = pipeline.assess_farmer_from_db(args.farmer_id)
        
        if result['status'] == 'SUCCESS':
            print(json.dumps(result['summary'], indent=2))
        else:
            print(f"ERROR: {result.get('error', 'Unknown error')}")
            if 'traceback' in result:
                print(f"\n{result['traceback']}")
    
    # Batch assessment
    elif args.batch_file:
        with open(args.batch_file) as f:
            farmer_ids = json.load(f)
        results = pipeline.assess_multiple_farmers(farmer_ids)
        print(f"Processed {len(results)} farmers")
        print(f"Success: {sum(1 for r in results if r['status'] == 'SUCCESS')}")
        print(f"Failed: {sum(1 for r in results if r['status'] == 'FAILED')}")
    
    else:
        parser.print_help()
 
 
if __name__ == "__main__":
    main()