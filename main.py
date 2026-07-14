"""
Satellite-based agricultural credit pipeline (v4.0).

Single production path: continuous Sentinel-2, crop cycles, cycle-aligned weather and
performance, and AdvancedCreditScorer. See PIPELINE_STAGES.md for the stage map.
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
from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
from crop_analysis.performance_analyzer import CropPerformanceAnalyzer
from assessment.advanced_credit_scorer import AdvancedCreditScorer
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
_VERSION = "4.0"
 
 
class SatelliteBasedCreditPipeline:
    """End-to-end satellite credit assessment: continuous data, cycles, rule-based credit scoring."""

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
            ml_mode: Requested scorer mode; ML blend is off unless CREDIT_SCORE_ML_BLEND_ENABLED.
            verbose: Detailed logging for the pipeline shell.
            use_mongodb: Connect to MongoDB for Stage 0 / persistence when available.
        """
        self.verbose = verbose
        self.crop_model_path = crop_model_path
        self.ml_mode = ml_mode
        self.use_mongodb = use_mongodb and MONGODB_AVAILABLE

        logger.info(f"\n{'#'*70}")
        logger.info(f"# SATELLITE CREDIT PIPELINE  v{_VERSION}")
        logger.info(f"# Credit scoring: {ml_mode}")
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
            except Exception as e:
                logger.warning(f"MongoDB unavailable: {e}  --  continuing without DB")
                self.use_mongodb = False
 
        # Initialize core components
        self.satellite_collector = SatelliteDataCollector(verbose=False)
        self.weather_analyzer = None
        self.crop_detector = None
        self.performance_analyzer = CropPerformanceAnalyzer(verbose=False)
        
        self.credit_scorer = AdvancedCreditScorer(mode=ml_mode, verbose=True)
        logger.info("Advanced Credit Scorer initialized (ml_mode=%s)", ml_mode)

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
            'pipeline_profile': 'continuous_v4',
            'assessment_date': now.isoformat(),
            'processing_time_seconds': 0.0,
            'pipeline_stages': [],
            'warnings': [],
            'errors': [error],
        }

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
    ) -> str:
        raw = {
            'farmer_id': farmer_id,
            'latitude': latitude,
            'longitude': longitude,
            'field_area_ha': field_area_ha,
            'geometry': geometry,
            'interval_days': interval_days,
            'start_date': start_date,
            'end_date': end_date,
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
    def _build_unclassified_analysis(crop_cycles, continuous_data: Dict) -> Dict:
        """
        Build a mock ``cropping_analysis`` dict that mimics the schema returned by
        ``CropDetector.analyze_cycles()`` without running the ML classifier.

        - Every cycle detected by CropCycleDetector is represented.
        - ``predicted_crop`` is ``None`` → downstream uses crop-agnostic PATH B.
        - ``start_date`` / ``end_date`` / ``duration_days`` are copied verbatim.
        - ``cultivation_signal`` is derived from ``peak_ndvi`` as a proxy.
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

            season_results.append({
                'season':               f'cycle_{i}',
                'year':                 int(start_str[:4]),
                'start_date':           start_str,
                'end_date':             end_str,
                'crop_detected':        True,
                'cultivation_signal':   cultivation_signal,
                # Classification skipped — performance analyzer will use PATH B
                'predicted_crop':       None,
                'crop_confidence':      0.0,
                'classification_note':  'classification_skipped_by_feature_flag',
                'all_probabilities':    {},
                'cycle_confidence':     confidence,
                'duration_days':        dur_days,
                'is_cycle_based':       True,
                'scenes':               cycle_scenes,
                'peak_ndvi':            round(peak_ndvi, 4),
                'n_scenes':             len(cycle_scenes),
            })

        # ``cropping_intensity`` for credit/limit must be **cycles per year** (0–3.5),
        # not cultivated-days / calendar-days (that fraction is land-use cover).
        try:
            s_dt = datetime.strptime(continuous_data['start_date'], '%Y-%m-%d')
            e_dt = datetime.strptime(continuous_data['end_date'],   '%Y-%m-%d')
            total_days = max((e_dt - s_dt).days, 1)
            cultivated = sum(int(_get(c, 'duration_days') or 0) for c in crop_cycles)
            years_span = max(total_days / 365.25, 0.25)
            cropping_intensity = round(min(3.5, n_cycles / years_span), 3)
            cultivated_day_fraction = round(min(1.0, cultivated / float(total_days)), 4)
        except Exception:
            years_span = 3.0
            cropping_intensity = round(min(3.5, n_cycles / years_span), 3)
            cultivated_day_fraction = 0.0

        sig_vals   = [r['cultivation_signal'] for r in season_results]
        avg_signal = round(float(sum(sig_vals) / max(len(sig_vals), 1)), 1) if sig_vals else 0.0

        return {
            'season_results':           season_results,
            'crops_detected':           {'Unclassified': n_cycles} if n_cycles else {},
            'dominant_crop':            None,            # no crop known; credit scorer crop_mult = 1.00
            'cropping_intensity':       cropping_intensity,
            'cultivated_day_fraction':  cultivated_day_fraction,
            'cultivation_signal':       avg_signal,
            'avg_cultivation_signal':   avg_signal,
            'seasons_with_crops':       len(season_results),
            'total_seasons_analyzed':   n_cycles,
            'region':                   'UNCLASSIFIED',
            'ndvi_threshold_used':      0.0,
            'detection_mode':           'cycle_based_unclassified',
            'classification_enabled':   False,
        }

    # ------------------------------------------------------------------
    # Public: database mode
    # ------------------------------------------------------------------

    def assess_farmer_from_db(
        self,
        farmer_id: str,
        farmer_benefits_override: Optional[Dict] = None,
        enable_crop_classification: bool = False,
    ) -> Dict:
        """Fetch farm from MongoDB, run full assessment, save result."""
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
            save_to_db=True,
            enable_crop_classification=enable_crop_classification,
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
                Unclassified and downstream scoring uses crop-agnostic PATH B.
            
        Returns:
            Complete assessment dictionary with all analysis results
        """
 
        farmer_id = (farmer_id or "").strip()
        if not farmer_id:
            return self._failed_assessment_shell('', 'farmer_id is required')

        farm_md_raw = farm_metadata if isinstance(farm_metadata, dict) else {}
        farm_md = {
            k: farm_md_raw.get(k)
            for k in ('state_lgd_code', 'district_lgd_code')
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
            'pipeline_profile': 'continuous_v4',
            'ml_mode': self.ml_mode,
            'pipeline_stages': ['1_shell'],
            'farmer_benefits': farmer_benefits,
            'crop_hint': crop_hint,
            'sowing_date': sowing_date,
            'warnings': [],
            'errors': [],
        }
 
        try:
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
            )

            satellite_data = None
            # Opt-in bypass: SATELLITE_FORCE_FRESH=1|true|yes skips cache reads.
            force_fresh_download = os.environ.get(
                "SATELLITE_FORCE_FRESH", ""
            ).strip().lower() in ("1", "true", "yes")
            if force_fresh_download:
                logger.info("STEP 1: SATELLITE_FORCE_FRESH set — skipping cache read")
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
                    self.db.upsert_satellite_stats_cache(
                        cache_key=cache_key,
                        satellite_data=satellite_data,
                        metadata={
                            'farmer_id': farmer_id,
                            'provider': os.environ.get('SATELLITE_PROVIDER', 'gee').strip().lower() or 'gee',
                        },
                        ttl_days=int(os.environ.get('SATELLITE_CACHE_TTL_DAYS', '30')),
                    )

            assessment['satellite_data'] = satellite_data
            assessment['location'] = satellite_data['location']

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

            # Initialize analyzers with location
            self.weather_analyzer = WeatherAnalyzer(
                latitude=clat,
                longitude=clon,
                verbose=False,
                interval_days=continuous_data.get("interval_days"),
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
                    crop_cycles, continuous_data
                )
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
            performance_analysis = self.performance_analyzer.analyze_performance(
                season_results=cropping_analysis.get('season_results', []),
                seasonal_data=[],
            )
            assessment['performance_analysis'] = performance_analysis
            assessment['pipeline_stages'].append('7_performance')

            # ============================================================
            # STEP 6: Credit Scoring
            # ============================================================
            logger.info("\nSTEP 6: Calculating credit score...")

            avg_cycle_duration_days = (
                sum(getattr(c, 'duration_days', 0) for c in crop_cycles) / len(crop_cycles)
                if crop_cycles else 0.0
            )

            crop_cycles_data = {
                'cycles_count': len(crop_cycles),
                'n_complete_cycles': performance_analysis.get('n_complete_cycles', 0),
                'n_active_cycles': performance_analysis.get('n_active_cycles', 0),
                'avg_cycle_duration_days': avg_cycle_duration_days,
                'utilization_metrics': utilization_metrics or {},
            }

            credit_assessment = self.credit_scorer.calculate_credit_score(
                cropping_analysis=cropping_analysis,
                performance_analysis=performance_analysis,
                weather_analysis=weather_analysis,
                farmer_benefits=farmer_benefits,
                crop_cycles=crop_cycles_data,
            )
            assessment['credit_assessment'] = credit_assessment
            assessment['pipeline_stages'].append('8_credit')

            # ============================================================
            # STEP 7: Credit Limit + Recommendations
            # ============================================================
            logger.info("\nSTEP 7: Generating credit recommendations...")

            credit_recommendations = self.credit_scorer.calculate_credit_limit(
                credit_score=credit_assessment['credit_score'],
                field_area_ha=assessment['field_area_ha'],
                cropping_analysis=cropping_analysis,
                performance_analysis=performance_analysis,
                farmer_benefits=farmer_benefits,
            )

            assessment['credit_recommendations'] = credit_recommendations
            assessment['pipeline_stages'].append('9_limit')

            # ============================================================
            # STEP 8: Assemble payload (cycles + utilization on assessment)
            # ============================================================
            logger.info("\nSTEP 8: Assembling assessment payload...")
            assessment['crop_cycles'] = {
                'detected': bool(crop_cycles),
                'cycles_count': len(crop_cycles),
                'utilization_metrics': utilization_metrics or {},
                'method': 'continuous_detection',
                'cycles': [c.to_dict() for c in crop_cycles] if crop_cycles else [],
            }
            assessment['pipeline_stages'].append('10_payload')

            # ============================================================
            # STEP 9: AI enrichment (SHAP / counterfactuals / optional LLM)
            # ============================================================
            try:
                from ai_integration.enrichment import enrich_assessment_with_ai

                logger.info("\nSTEP 9: AI enrichment (explainability)...")
                if enrich_assessment_with_ai(assessment):
                    assessment['pipeline_stages'].append('12_ai')
                    logger.info("  [OK] AI enrichment attached (see assessment['ai_enrichment'])")
            except ImportError:
                pass

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
                f"  Score:  {credit_assessment['credit_score']:.1f}/100\n"
                f"  Risk:   {credit_assessment['risk_category']}\n"
                f"  Limit:  ₹{credit_recommendations['recommended_limit']:,.0f}\n"
                f"  Method: {credit_assessment.get('method', 'rule_based')}\n"
                f"  Time:   {assessment['processing_time_seconds']:.1f}s\n"
                f"{'='*70}"
            )
 
            # Save to MongoDB (if enabled)
            if save_to_db and self.use_mongodb:
                try:
                    logger.info("\nSTEP 10: Saving to MongoDB...")
                    self.db.save_assessment(assessment)
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
        cr = assessment.get('credit_recommendations', {})
        crop = assessment.get('cropping_analysis', {})
        
        summary = {
            'farmer_id': assessment['farmer_id'],
            'credit_score': ca.get('credit_score', 0),
            'risk_category': ca.get('risk_category', 'UNKNOWN'),
            'credit_limit': cr.get('recommended_limit', 0),
            'crops_detected': len(crop.get('crops_detected', {})),
            'cropping_intensity': crop.get('cropping_intensity', 0),
            'scoring_method': ca.get('method', 'rule_based'),
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
        description='Satellite-Based Agricultural Credit Assessment Pipeline v4.0'
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