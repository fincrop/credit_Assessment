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

# ============================================================================
# Continue with normal imports
# ============================================================================
 
import gc
import json
import logging
import traceback
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings('ignore', category=InconsistentVersionWarning)
except ImportError:
    pass

# Import existing components
from data_acquisition.satellite_collector import SatelliteDataCollector
from data_acquisition.weather_analyzer import WeatherAnalyzer
from crop_analysis.crop_detector import CropDetector
from crop_analysis.crop_cycle_detector import CropCycleDetector
from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
from crop_analysis.performance_analyzer import CropPerformanceAnalyzer
from assessment.advanced_credit_scorer import AdvancedCreditScorer
from config import PipelineConfig

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

    # ------------------------------------------------------------------
    # Public: database mode
    # ------------------------------------------------------------------

    def assess_farmer_from_db(self, farmer_id: str) -> Dict:
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

        return self.assess_farmer(
            farmer_id=farmer_id,
            latitude=farm.get('latitude'),
            longitude=farm.get('longitude'),
            field_area_ha=farm.get('field_area_ha'),
            geometry=geometry,
            farmer_benefits=farm.get('farmer_benefits'),
            crop_hint=crop_hint,
            sowing_date=sowing_date,
            save_to_db=True,
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
        save_to_db: bool = True,
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
            save_to_db: Save results to MongoDB
            
        Returns:
            Complete assessment dictionary with all analysis results
        """
 
        farmer_id = (farmer_id or "").strip()
        if not farmer_id:
            return self._failed_assessment_shell('', 'farmer_id is required')

        if isinstance(crop_hint, str):
            crop_hint = crop_hint.strip() or None
        if isinstance(sowing_date, str):
            sowing_date = sowing_date.strip() or None

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
            satellite_data = self.satellite_collector.collect_historical_data(
                latitude=latitude,
                longitude=longitude,
                field_area_ha=field_area_ha,
                geometry=geometry,
            )
            assessment['satellite_data'] = satellite_data
            assessment['location'] = satellite_data['location']
            assessment['field_area_ha'] = satellite_data['field_area_ha']

            clat = satellite_data['location']['latitude']
            clon = satellite_data['location']['longitude']

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
            self.crop_detector = CropDetector(
                crop_model_path=self.crop_model_path,
                latitude=clat, longitude=clon, verbose=False
            )

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
                crop_cycles = self.crop_cycle_detector.detect_cycles(
                    dates=continuous_data['dates'],
                    ndvi_values=continuous_data['ndvi_values'],
                    evi_values=continuous_data.get('evi_values'),
                    ndmi_values=continuous_data.get('ndmi_values'),
                    scenes=continuous_data.get('scenes'),
                    sowing_date_hint=sowing_date,
                    crop_hint=crop_hint,
                    grid_step_days=float(grid_step),
                )

                if crop_cycles:
                    logger.info(f"  ✓ Detected {len(crop_cycles)} crop cycles")

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
                            "  ✓ Land utilization: %.1f%%",
                            100.0 * float(utilization_metrics.get('land_utilization_index', 0) or 0),
                        )

                assessment['pipeline_stages'].append('4_cycles')
            else:
                logger.warning(
                    "Skipping cycle detection: only %d grid points (need 10+)",
                    len(continuous_data.get('dates', [])),
                )

            # ============================================================
            # STEP 3: Dynamic Crop Classification (cycle-based)
            # ============================================================
            logger.info("\nSTEP 3: Classifying cycles...")
            cropping_analysis = self.crop_detector.analyze_cycles(
                crop_cycles=crop_cycles,
                all_continuous_scenes=continuous_data.get('scenes', []),
            )
            assessment['cropping_analysis'] = cropping_analysis
            assessment['pipeline_stages'].append('5_crops')

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
                field_area_ha=satellite_data['field_area_ha'],
                cropping_analysis=cropping_analysis,
                performance_analysis=performance_analysis,
                farmer_benefits=farmer_benefits,
            )

            assessment['credit_recommendations'] = credit_recommendations
            assessment['pipeline_stages'].append('9_limit')

            # ============================================================
            # STEP 8: Add crop cycles to output
            # ============================================================
            assessment['crop_cycles'] = {
                'detected': bool(crop_cycles),
                'cycles_count': len(crop_cycles),
                'utilization_metrics': utilization_metrics or {},
                'method': 'continuous_detection',
                'cycles': [c.to_dict() for c in crop_cycles] if crop_cycles else [],
            }
            assessment['pipeline_stages'].append('10_payload')

            # Optional Stage 12: LLM narrative / translation (env + AI_CONFIG)
            try:
                from ai_integration.enrichment import enrich_assessment_with_ai

                if enrich_assessment_with_ai(assessment):
                    assessment['pipeline_stages'].append('12_ai')
                    logger.info("  ✓ AI enrichment attached (see assessment['ai_enrichment'])")
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
                    logger.info("\nSTEP 11: Saving to MongoDB...")
                    self.db.save_assessment(assessment)
                    assessment['pipeline_stages'].append('11_persist')
                    logger.info("  ✓ Saved successfully")
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
    parser.add_argument('--ml-mode', type=str, default='hybrid',
                       choices=['rule_based', 'unsupervised', 'supervised', 'hybrid'],
                       help='ML scoring mode (default: hybrid)')
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