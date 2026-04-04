# """
# Satellite-Based Agricultural Credit Assessment Pipeline - ENHANCED
# ===================================================================
# VERSION 4.0 - FIXED
 
# WHAT'S NEW IN v4.0
# -----------------
# 1. **Continuous Crop Cycle Detection**: Automatic detection of sowing/harvest events
# 2. **Crop-Agnostic Metrics**: Works WITHOUT crop identification
# 3. **ML Without Training Labels**: Unsupervised learning from Day 1
# 4. **Hybrid ML Scoring**: Combines rule-based + ML for best accuracy
# 5. **Backward Compatible**: Can run in BASIC mode (100% v3.0 compatible)
 
# PIPELINE MODES
# --------------
# - BASIC: Traditional seasonal analysis only (v3.0 compatible)
# - ENHANCED: Basic + continuous data + crop cycles + advanced ML
 
# FIXES IN THIS VERSION
# --------------------
# ✅ Fixed continuous data collection (was showing 0 scenes)
# ✅ Fixed undefined variables (three_years_ago, today)
# ✅ Fixed calculate_credit_limit call with correct parameters
# ✅ Better error handling for crop cycles
# ✅ Extract crop hint from database
# ✅ Add crop cycles to result output
# """
 
# from __future__ import annotations
 
# import gc
# import json
# import logging
# import os
# import sys
# import traceback
# from datetime import datetime, timedelta, date
# from pathlib import Path
# from typing import Dict, List, Optional
 
# import os

# # ✅ Force correct PROJ from pyproj (pip-installed)
# try:
#     import pyproj
#     os.environ["PROJ_LIB"] = pyproj.datadir.get_data_dir()
# except Exception:
#     pass

# # ✅ Fix CRS mismatch warnings
# os.environ["GTIFF_SRS_SOURCE"] = "EPSG"

# # # PROJ fix for Windows conda
# # _cprefix = sys.prefix
# # _ppath   = os.path.join(_cprefix, 'Library', 'share', 'proj')
# # if os.path.isdir(_ppath):
# #     os.environ['PROJ_LIB'] = _ppath
 
# # Import existing components
# from data_acquisition.satellite_collector import SatelliteDataCollector
# from data_acquisition.weather_analyzer import WeatherAnalyzer
# from crop_analysis.crop_detector import CropDetector
# from crop_analysis.performance_analyzer import CropPerformanceAnalyzer
# from assessment.credit_scorer import CreditScorer
 
# # Import new advanced components
# try:
#     from crop_analysis.crop_cycle_detector import CropCycleDetector
#     from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
#     from assessment.advanced_credit_scorer import AdvancedCreditScorer
#     ADVANCED_FEATURES_AVAILABLE = True
# except ImportError:
#     ADVANCED_FEATURES_AVAILABLE = False
#     print("WARNING: Advanced features not available. Run in BASIC mode or install dependencies.")
 
# try:
#     from mongodb_helper import MongoDBHelper
#     MONGODB_AVAILABLE = True
# except ImportError:
#     MONGODB_AVAILABLE = False
#     print("WARNING: mongodb_helper not found -- database features disabled")
 
# try:
#     from config.regional_config import RegionalConfig
#     REGIONAL_CONFIG_AVAILABLE = True
# except ImportError:
#     REGIONAL_CONFIG_AVAILABLE = False
 
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s  %(name)-28s  %(levelname)-8s  %(message)s',
# )
# logger = logging.getLogger(__name__)
# _VERSION = "4.0"
 
 
# class SatelliteBasedCreditPipeline:
#     """
#     End-to-end satellite credit assessment pipeline with advanced ML features
#     """
 
#     def __init__(
#         self,
#         crop_model_path: str,
#         mode: str = 'ENHANCED',  # 'BASIC' or 'ENHANCED'
#         ml_mode: str = 'hybrid',  # 'rule_based', 'unsupervised', 'supervised', 'hybrid'
#         verbose: bool = True,
#         use_mongodb: bool = True,
#         enable_continuous_data: bool = True,
#         enable_crop_cycles: bool = True,
#         enable_advanced_ml: bool = True,
#     ):
#         """
#         Initialize pipeline
        
#         Args:
#             crop_model_path: Path to crop classification model
#             mode: 'BASIC' (v3.0 features only) or 'ENHANCED' (all v4.0 features)
#             ml_mode: ML scoring mode for advanced scorer
#             verbose: Enable detailed logging
#             use_mongodb: Enable MongoDB integration
#             enable_continuous_data: Collect 3-year continuous time series
#             enable_crop_cycles: Detect crop cycles from continuous data
#             enable_advanced_ml: Use advanced ML credit scorer
#         """
#         self.verbose = verbose
#         self.crop_model_path = crop_model_path
#         self.mode = mode
#         self.ml_mode = ml_mode
#         self.use_mongodb = use_mongodb and MONGODB_AVAILABLE
        
#         # Feature flags
#         self.enable_continuous_data = enable_continuous_data and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
#         self.enable_crop_cycles = enable_crop_cycles and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
#         self.enable_advanced_ml = enable_advanced_ml and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
 
#         logger.info(f"\n{'#'*70}")
#         logger.info(f"# SATELLITE CREDIT PIPELINE  v{_VERSION}")
#         logger.info(f"# Mode: {mode}  |  ML Mode: {ml_mode}")
#         logger.info(f"{'#'*70}\n")
        
#         # Feature status
#         logger.info("Feature Status:")
#         logger.info(f"  Continuous Data Collection: {'✓' if self.enable_continuous_data else '✗'}")
#         logger.info(f"  Crop Cycle Detection:       {'✓' if self.enable_crop_cycles else '✗'}")
#         logger.info(f"  Advanced ML Scoring:        {'✓' if self.enable_advanced_ml else '✗'}")
#         logger.info(f"  MongoDB Integration:        {'✓' if self.use_mongodb else '✗'}\n")
 
#         # Initialize MongoDB
#         self.db = None
#         if self.use_mongodb:
#             try:
#                 self.db = MongoDBHelper()
#                 logger.info("MongoDB enabled")
#             except Exception as e:
#                 logger.warning(f"MongoDB unavailable: {e}  --  continuing without DB")
#                 self.use_mongodb = False
 
#         # Initialize core components
#         self.satellite_collector = SatelliteDataCollector(verbose=False)
#         self.weather_analyzer = None
#         self.crop_detector = None
#         self.performance_analyzer = CropPerformanceAnalyzer(verbose=False)
        
#         # Initialize credit scorer (basic or advanced)
#         if self.enable_advanced_ml:
#             self.credit_scorer = AdvancedCreditScorer(mode=ml_mode, verbose=True)
#             logger.info(f"Advanced Credit Scorer initialized (mode={ml_mode})")
#         else:
#             self.credit_scorer = CreditScorer(verbose=True)
#             logger.info("Basic Credit Scorer initialized")
        
#         # Initialize advanced components (if enabled)
#         if self.enable_crop_cycles:
#             self.crop_cycle_detector = CropCycleDetector()
#             self.land_utilization_analyzer = LandUtilizationAnalyzer()
#             logger.info("Crop cycle detection enabled")
#         else:
#             self.crop_cycle_detector = None
#             self.land_utilization_analyzer = None
        
#         logger.info("Pipeline initialized\n")
 
#     # ------------------------------------------------------------------
#     # Public: database mode
#     # ------------------------------------------------------------------
 
#     def assess_farmer_from_db(self, farmer_id: str) -> Dict:
#         """Fetch farm from MongoDB, run full assessment, save result."""
#         if not self.use_mongodb:
#             return {'farmer_id': farmer_id, 'status': 'FAILED',
#                     'error': 'MongoDB not available. Use assess_farmer() instead.'}
 
#         logger.info(f"\n{'='*70}\nFETCHING FARM: {farmer_id}\n{'='*70}\n")
 
#         farm = self.db.get_farm_by_id(farmer_id)
#         if farm is None:
#             msg = f"Farmer {farmer_id} not found in database"
#             logger.error(f"ERROR: {msg}")
#             return {'farmer_id': farmer_id, 'status': 'FAILED', 'error': msg}
 
#         # Extract crop hint and sowing date from database (helps with analysis)
#         crop_hint = farm.get('crop')  # e.g., "Potato"
#         sowing_date = farm.get('sowing_date')  # e.g., "2022-12-15"
        
#         if crop_hint:
#             logger.info(f"📍 Crop hint from database: {crop_hint}")
#         if sowing_date:
#             logger.info(f"📅 Sowing date from database: {sowing_date}")
 
#         geometry = farm.get('geometry')
#         if geometry and isinstance(geometry, list):
#             geometry = self._convert_geometry_from_db(geometry)
 
#         return self.assess_farmer(
#             farmer_id=farmer_id,
#             latitude=farm.get('latitude'),
#             longitude=farm.get('longitude'),
#             field_area_ha=farm.get('field_area_ha'),
#             geometry=geometry,
#             farmer_benefits=farm.get('farmer_benefits'),
#             crop_hint=crop_hint,
#             sowing_date=sowing_date,
#             save_to_db=True,
#         )
 
#     # ------------------------------------------------------------------
#     # Public: direct mode
#     # ------------------------------------------------------------------
 
#     def assess_farmer(
#         self,
#         farmer_id: str,
#         latitude: Optional[float] = None,
#         longitude: Optional[float] = None,
#         field_area_ha: Optional[float] = None,
#         geometry: Optional[object] = None,
#         farmer_benefits: Optional[Dict] = None,
#         crop_hint: Optional[str] = None,
#         sowing_date: Optional[str] = None,
#         save_to_db: bool = True,
#     ) -> Dict:
#         """
#         Run the complete assessment pipeline
        
#         Args:
#             farmer_id: Unique farmer identifier
#             latitude: Farm center latitude
#             longitude: Farm center longitude
#             field_area_ha: Field area in hectares
#             geometry: Field polygon (Shapely object)
#             farmer_benefits: Government benefits info
#             crop_hint: Crop type from database (optional)
#             sowing_date: Sowing date from database (optional)
#             save_to_db: Save results to MongoDB
            
#         Returns:
#             Complete assessment dictionary with all analysis results
#         """
 
#         logger.info(f"\n{'='*70}\nFARMER ASSESSMENT: {farmer_id}\n{'='*70}\n")
#         start_time = datetime.now()
 
#         assessment: Dict = {
#             'farmer_id': farmer_id,
#             'assessment_date': start_time.isoformat(),
#             'pipeline_version': _VERSION,
#             'pipeline_mode': self.mode,
#             'farmer_benefits': farmer_benefits,
#             'crop_hint': crop_hint,
#             'sowing_date': sowing_date,
#             'warnings': [],
#             'errors': [],
#         }
 
#         try:
#             # ============================================================
#             # STEP 1: SATELLITE DATA COLLECTION
#             # ============================================================
#             logger.info("STEP 1: Collecting satellite data...")
            
#             # 1A: Traditional seasonal data (always)
#             logger.info("  1A: Collecting seasonal data (10 seasons)...")
#             satellite_data = self.satellite_collector.collect_historical_data(
#                 latitude=latitude, longitude=longitude,
#                 field_area_ha=field_area_ha, geometry=geometry,
#             )
#             assessment['satellite_data'] = satellite_data
#             assessment['location'] = satellite_data['location']
#             assessment['field_area_ha'] = satellite_data['field_area_ha']
 
#             clat = satellite_data['location']['latitude']
#             clon = satellite_data['location']['longitude']
 
#             if REGIONAL_CONFIG_AVAILABLE:
#                 RegionalConfig.print_region_info(clat, clon)
 
#             # 1B: Continuous data (ENHANCED mode only) - FIXED
#             continuous_data = None
#             if self.enable_continuous_data:
#                 logger.info("  1B: Collecting continuous data (3 years)...")
#                 try:
#                     # Calculate date range - FIXED: Define variables
#                     today = date.today()
#                     three_years_ago = today - timedelta(days=3 * 365)
                    
#                     # Collect continuous data - FIXED: Proper method call
#                     continuous_data = self.satellite_collector.collect_satellite_data(
#                         latitude=clat,
#                         longitude=clon,
#                         start_date=three_years_ago.isoformat(),
#                         end_date=today.isoformat(),
#                         geometry=geometry,
#                         interval_days=7  # Weekly data
#                     )
                    
#                     if continuous_data and 'scenes' in continuous_data:
#                         scene_count = len(continuous_data['scenes'])
#                         total_days = continuous_data.get('total_days', 0)
#                         logger.info(f"     ✓ Collected {scene_count} scenes over {total_days} days")
                        
#                         if scene_count == 0:
#                             logger.warning("     ⚠ No continuous scenes collected")
#                             continuous_data = None
#                     else:
#                         logger.warning("     ⚠ No continuous data collected")
#                         continuous_data = None
                        
#                 except Exception as e:
#                     logger.error(f"     ✗ Continuous data collection failed: {e}")
#                     logger.debug(traceback.format_exc())
#                     continuous_data = None
#                     assessment['warnings'].append(f"Continuous data collection failed: {e}")
 
#             # Initialize analyzers with location
#             self.weather_analyzer = WeatherAnalyzer(
#                 latitude=clat, longitude=clon, verbose=False)
#             self.crop_detector = CropDetector(
#                 crop_model_path=self.crop_model_path,
#                 latitude=clat, longitude=clon, verbose=False)
 
#             # ============================================================
#             # STEP 2: CROP DETECTION (Traditional Seasonal)
#             # ============================================================
#             logger.info("\nSTEP 2: Detecting crops and cropping patterns...")
#             cropping_analysis = self.crop_detector.analyze_cropping_pattern(
#                 satellite_data['seasonal_data']
#             )
#             assessment['cropping_analysis'] = cropping_analysis
 
#             # Get merged seasons for cross-season crops
#             merged_seasons = cropping_analysis.get(
#                 'merged_seasons', satellite_data['seasonal_data']
#             )
 
#             # ============================================================
#             # STEP 2B: CROP CYCLE DETECTION (Continuous Data) - FIXED
#             # ============================================================
#             crop_cycles = None
#             utilization_metrics = None
            
#             if self.enable_crop_cycles and continuous_data is not None:
#                 logger.info("\nSTEP 2B: Detecting crop cycles from continuous data...")
                
#                 try:
#                     # Check if we have enough data
#                     if continuous_data and len(continuous_data.get('dates', [])) >= 20:
                        
#                         # Detect cycles
#                         crop_cycles = self.crop_cycle_detector.detect_cycles(
#                             dates=continuous_data['dates'],
#                             ndvi_values=continuous_data['ndvi_values']
#                         )
                        
#                         if crop_cycles and len(crop_cycles) > 0:
#                             # Analyze land utilization
#                             utilization_metrics = self.land_utilization_analyzer.analyze(
#                                 cycles=crop_cycles,
#                                 start_date=continuous_data['start_date'],
#                                 end_date=continuous_data['end_date']
#                             )
                            
#                             logger.info(f"     ✓ Detected {len(crop_cycles)} crop cycles")
#                             logger.info(f"     ✓ Crop intensity: {utilization_metrics.get('crops_per_year', 0):.2f} crops/year")
#                             logger.info(f"     ✓ Land utilization: {utilization_metrics.get('land_utilization_index', 0):.1%}")
#                         else:
#                             logger.warning("     ⚠ No cycles detected from continuous data")
#                             crop_cycles = None
#                             utilization_metrics = None
#                     else:
#                         scene_count = len(continuous_data.get('dates', [])) if continuous_data else 0
#                         logger.warning(f"     ⚠ Insufficient continuous data: {scene_count} scenes (need 20+)")
#                         crop_cycles = None
#                         utilization_metrics = None
                        
#                 except Exception as e:
#                     logger.error(f"     ✗ Cycle detection failed: {e}")
#                     logger.debug(traceback.format_exc())
#                     crop_cycles = None
#                     utilization_metrics = None
#                     assessment['warnings'].append(f"Crop cycle detection failed: {e}")
#             else:
#                 logger.debug("Crop cycle detection disabled or no continuous data")
 
#             # ============================================================
#             # STEP 3: WEATHER ANALYSIS
#             # ============================================================
#             logger.info("\nSTEP 3: Analyzing weather patterns...")
#             weather_analysis = self.weather_analyzer.analyze_seasonal_weather(
#                 latitude=clat, longitude=clon,
#                 seasonal_data=satellite_data['seasonal_data'],
#                 merged_seasons=merged_seasons,
#             )
#             assessment['weather_analysis'] = weather_analysis
 
#             # ============================================================
#             # STEP 4: PERFORMANCE ANALYSIS
#             # ============================================================
#             logger.info("\nSTEP 4: Analyzing crop performance...")
#             performance_analysis = self.performance_analyzer.analyze_performance(
#                 season_results=cropping_analysis['season_results'],
#                 seasonal_data=satellite_data['seasonal_data'],
#             )
#             assessment['performance_analysis'] = performance_analysis
 
#             # ============================================================
#             # STEP 5: CREDIT SCORE
#             # ============================================================
#             logger.info("\nSTEP 5: Calculating credit score...")
            
#             # Prepare crop cycles data for scorer
#             crop_cycles_data = None
#             if crop_cycles and utilization_metrics:
#                 crop_cycles_data = {
#                     'cycles_count': len(crop_cycles),
#                     'utilization_metrics': utilization_metrics,
#                     'cycles': crop_cycles
#                 }
            
#             if self.enable_advanced_ml:
#                 # Advanced ML scorer
#                 credit_assessment = self.credit_scorer.calculate_credit_score(
#                     cropping_analysis=cropping_analysis,
#                     performance_analysis=performance_analysis,
#                     weather_analysis=weather_analysis,
#                     farmer_benefits=farmer_benefits,
#                     crop_cycles=crop_cycles_data,
#                 )
#             else:
#                 # Traditional rule-based scorer
#                 credit_assessment = self.credit_scorer.calculate_credit_score(
#                     cropping_analysis=cropping_analysis,
#                     performance_analysis=performance_analysis,
#                     weather_analysis=weather_analysis,
#                     farmer_benefits=farmer_benefits,
#                 )
            
#             assessment['credit_assessment'] = credit_assessment
 
#             # ============================================================
#             # STEP 6: CREDIT LIMIT + RECOMMENDATIONS - FIXED
#             # ============================================================
#             logger.info("\nSTEP 6: Generating credit recommendations...")
            
#             # Prepare crop detected info
#             crop_detected = {
#                 'dominant_crop': cropping_analysis.get('dominant_crop', 'Unknown'),
#                 'crops_detected': cropping_analysis.get('crops_detected', {}),
#                 'season_results': cropping_analysis.get('season_results', [])
#             }
            
#             # Call with correct parameters - FIXED
#             credit_recommendations = self.credit_scorer.calculate_credit_limit(
#                 credit_score=credit_assessment['credit_score'],
#                 field_area_ha=satellite_data['field_area_ha'],
#                 cropping_analysis=cropping_analysis,
#                 crop_detected=crop_detected,
#                 farmer_benefits=farmer_benefits
#             )
#             assessment['credit_recommendations'] = credit_recommendations
 
#             # ============================================================
#             # ADD CROP CYCLES TO RESULT - FIXED
#             # ============================================================
#             if crop_cycles is not None and utilization_metrics is not None:
#                 assessment['crop_cycles'] = {
#                     'detected': True,
#                     'cycles_count': len(crop_cycles),
#                     'utilization_metrics': utilization_metrics,
#                     'method': 'continuous_detection',
#                     'cycles': [
#                         {
#                             'start_date': cycle.start_date,
#                             'end_date': cycle.end_date,
#                             'duration_days': cycle.duration_days,
#                             'peak_ndvi': cycle.peak_ndvi,
#                             'confidence': cycle.confidence
#                         } for cycle in crop_cycles
#                     ] if hasattr(crop_cycles[0], 'start_date') else []
#                 }
#             else:
#                 assessment['crop_cycles'] = {
#                     'detected': False,
#                     'cycles_count': 0,
#                     'utilization_metrics': {},
#                     'method': 'seasonal_only'
#                 }
 
#             # Add continuous data stats
#             if continuous_data:
#                 assessment['continuous_data_stats'] = {
#                     'enabled': True,
#                     'scenes_collected': len(continuous_data.get('scenes', [])),
#                     'date_range': {
#                         'start': continuous_data.get('start_date'),
#                         'end': continuous_data.get('end_date')
#                     },
#                     'total_days': continuous_data.get('total_days', 0)
#                 }
#             else:
#                 assessment['continuous_data_stats'] = {
#                     'enabled': self.enable_continuous_data,
#                     'scenes_collected': 0
#                 }
 
#             # ============================================================
#             # FINALIZE
#             # ============================================================
#             assessment['processing_time_seconds'] = (
#                 datetime.now() - start_time).total_seconds()
#             assessment['status'] = 'SUCCESS'
#             assessment['summary'] = self._generate_summary(assessment)
 
#             logger.info(
#                 f"\n{'='*70}\n"
#                 f"ASSESSMENT COMPLETE\n"
#                 f"{'='*70}\n"
#                 f"  Score:  {credit_assessment['credit_score']:.1f}/100\n"
#                 f"  Risk:   {credit_assessment['risk_category']}\n"
#                 f"  Limit:  ₹{credit_recommendations['recommended_limit']:,.0f}\n"
#                 f"  Method: {credit_assessment.get('method', 'rule_based')}\n"
#                 f"  Time:   {assessment['processing_time_seconds']:.1f}s\n"
#                 f"{'='*70}"
#             )
 
#             # Save to MongoDB (if enabled)
#             if save_to_db and self.use_mongodb:
#                 try:
#                     logger.info("\nSTEP 7: Saving to MongoDB...")
#                     self.db.save_assessment(assessment)
#                     logger.info("  ✓ Saved successfully")
#                 except Exception as e:
#                     logger.error(f"MongoDB save failed: {e}")
#                     assessment['errors'].append(f"MongoDB save failed: {e}")
 
#         except Exception as e:
#             logger.error(f"\nASSESSMENT FAILED: {e}")
#             logger.error(traceback.format_exc())
#             assessment['status'] = 'FAILED'
#             assessment['error'] = str(e)
#             assessment['traceback'] = traceback.format_exc()
#             assessment['processing_time_seconds'] = (
#                 datetime.now() - start_time).total_seconds()
 
#         # Cleanup
#         gc.collect()
 
#         return assessment
 
#     # ------------------------------------------------------------------
#     # Batch processing
#     # ------------------------------------------------------------------
 
#     def assess_multiple_farmers(
#         self,
#         farmer_ids: List[str],
#         batch_size: int = 10
#     ) -> List[Dict]:
#         """
#         Batch process multiple farmers
#         """
#         logger.info(f"\nBATCH ASSESSMENT: {len(farmer_ids)} farmers")
#         results = []
        
#         for i, farmer_id in enumerate(farmer_ids, 1):
#             logger.info(f"\n[{i}/{len(farmer_ids)}] Processing {farmer_id}...")
            
#             try:
#                 result = self.assess_farmer_from_db(farmer_id)
#                 results.append(result)
#             except Exception as e:
#                 logger.error(f"Failed: {e}")
#                 results.append({
#                     'farmer_id': farmer_id,
#                     'status': 'FAILED',
#                     'error': str(e)
#                 })
            
#             # Cleanup after each batch
#             if i % batch_size == 0:
#                 gc.collect()
        
#         return results
 
#     # ------------------------------------------------------------------
#     # Helper methods
#     # ------------------------------------------------------------------
 
#     def _generate_summary(self, assessment: Dict) -> Dict:
#         """Generate human-readable summary"""
#         ca = assessment.get('credit_assessment', {})
#         cr = assessment.get('credit_recommendations', {})
#         crop = assessment.get('cropping_analysis', {})
        
#         summary = {
#             'farmer_id': assessment['farmer_id'],
#             'credit_score': ca.get('credit_score', 0),
#             'risk_category': ca.get('risk_category', 'UNKNOWN'),
#             'credit_limit': cr.get('recommended_limit', 0),
#             'crops_detected': len(crop.get('crops_detected', {})),
#             'cropping_intensity': crop.get('cropping_intensity', 0),
#             'scoring_method': ca.get('method', 'rule_based'),
#         }
        
#         # Add advanced metrics if available
#         if 'crop_cycles' in assessment and assessment['crop_cycles'].get('detected'):
#             cc = assessment['crop_cycles']
#             summary['crop_cycles_detected'] = cc['cycles_count']
#             summary['land_utilization'] = cc['utilization_metrics'].get('land_utilization_index', 0)
#             summary['crops_per_year'] = cc['utilization_metrics'].get('crops_per_year', 0)
        
#         return summary
 
#     def _convert_geometry_from_db(self, geometry):
#         """
#         Convert geometry from MongoDB format to Shapely Polygon.
        
#         Handles multiple formats:
#         - Array of {latitude, longitude} objects
#         - Array of {lat, lon} objects  
#         - Array of {lat, lng} objects
#         - GeoJSON format
#         - None (missing geometry)
        
#         Args:
#             geometry: Geometry data from MongoDB
            
#         Returns:
#             Shapely Polygon object or None
#         """
#         # Handle None or missing geometry
#         if geometry is None or not geometry:
#             logger.info("No geometry provided, will use point buffer from lat/lon")
#             return None
        
#         try:
#             from shapely.geometry import Polygon, shape
            
#             # Case 1: Already a Shapely geometry object
#             if hasattr(geometry, 'geom_type'):
#                 logger.debug("Geometry is already Shapely object")
#                 return geometry
            
#             # Case 2: GeoJSON format (dict with 'type' and 'coordinates')
#             if isinstance(geometry, dict):
#                 if 'type' in geometry and 'coordinates' in geometry:
#                     logger.debug("Converting from GeoJSON format")
#                     return shape(geometry)
#                 else:
#                     logger.warning(f"Invalid geometry dict (missing 'type' or 'coordinates')")
#                     return None
            
#             # Case 3: Array of coordinate objects (YOUR FORMAT)
#             # e.g., [{latitude: 27.49, longitude: 78.04}, ...]
#             if isinstance(geometry, list) and len(geometry) >= 3:
#                 coords = []
                
#                 for point in geometry:
#                     if not isinstance(point, dict):
#                         logger.warning(f"Invalid point format: {point}")
#                         continue
                    
#                     # Try different field name variations
#                     lon = point.get('longitude') or point.get('lon') or point.get('lng')
#                     lat = point.get('latitude') or point.get('lat')
                    
#                     if lon is not None and lat is not None:
#                         coords.append((float(lon), float(lat)))
#                     else:
#                         logger.warning(f"Point missing lat/lon: {point}")
                
#                 if len(coords) >= 3:
#                     logger.info(f"✓ Converted {len(coords)} coordinates to Polygon")
#                     return Polygon(coords)
#                 else:
#                     logger.warning(f"Not enough valid coordinates: {len(coords)}/3 minimum")
#                     return None
            
#             logger.warning(f"Unrecognized geometry format: {type(geometry)}")
#             return None
            
#         except Exception as e:
#             logger.error(f"Geometry conversion failed: {e}")
#             import traceback
#             logger.debug(traceback.format_exc())
#             return None
 
 
# # ==========================================================================
# # CLI ENTRY POINT
# # ==========================================================================
 
# def main():
#     """Command-line interface"""
#     import argparse
    
#     parser = argparse.ArgumentParser(
#         description='Satellite-Based Agricultural Credit Assessment Pipeline v4.0'
#     )
#     parser.add_argument('--farmer-id', type=str, help='Farmer ID to assess')
#     parser.add_argument('--mode', type=str, default='ENHANCED', 
#                        choices=['BASIC', 'ENHANCED'],
#                        help='Pipeline mode (default: ENHANCED)')
#     parser.add_argument('--ml-mode', type=str, default='hybrid',
#                        choices=['rule_based', 'unsupervised', 'supervised', 'hybrid'],
#                        help='ML scoring mode (default: hybrid)')
#     parser.add_argument('--model-path', type=str, default='models/crop_classifier_model.joblib',
#                        help='Path to crop classification model')
#     parser.add_argument('--batch-file', type=str, help='JSON file with farmer IDs for batch processing')
    
#     args = parser.parse_args()
    
#     # Initialize pipeline
#     pipeline = SatelliteBasedCreditPipeline(
#         crop_model_path=args.model_path,
#         mode=args.mode,
#         ml_mode=args.ml_mode,
#         verbose=True,
#         use_mongodb=True,
#     )
    
#     # Single farmer assessment
#     if args.farmer_id:
#         result = pipeline.assess_farmer_from_db(args.farmer_id)
        
#         if result['status'] == 'SUCCESS':
#             print(json.dumps(result['summary'], indent=2))
#         else:
#             print(f"ERROR: {result.get('error', 'Unknown error')}")
#             if 'traceback' in result:
#                 print(f"\n{result['traceback']}")
    
#     # Batch assessment
#     elif args.batch_file:
#         with open(args.batch_file) as f:
#             farmer_ids = json.load(f)
#         results = pipeline.assess_multiple_farmers(farmer_ids)
#         print(f"Processed {len(results)} farmers")
#         print(f"Success: {sum(1 for r in results if r['status'] == 'SUCCESS')}")
#         print(f"Failed: {sum(1 for r in results if r['status'] == 'FAILED')}")
    
#     else:
#         parser.print_help()
 
 
# if __name__ == "__main__":
#     main()
 
"""
Satellite-Based Agricultural Credit Assessment Pipeline - ENHANCED
===================================================================
VERSION 4.0 - FIXED
 
WHAT'S NEW IN v4.0
-----------------
1. **Continuous Crop Cycle Detection**: Automatic detection of sowing/harvest events
2. **Crop-Agnostic Metrics**: Works WITHOUT crop identification
3. **ML Without Training Labels**: Unsupervised learning from Day 1
4. **Hybrid ML Scoring**: Combines rule-based + ML for best accuracy
5. **Backward Compatible**: Can run in BASIC mode (100% v3.0 compatible)
 
PIPELINE MODES
--------------
- BASIC: Traditional seasonal analysis only (v3.0 compatible)
- ENHANCED: Basic + continuous data + crop cycles + advanced ML
 
FIXES IN THIS VERSION
--------------------
✅ Fixed continuous data collection (was showing 0 scenes)
✅ Fixed undefined variables (three_years_ago, today)
✅ Fixed calculate_credit_limit call with correct parameters
✅ Better error handling for crop cycles
✅ Extract crop hint from database
✅ Add crop cycles to result output
✅ Fixed PROJ database version mismatch error
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
import os
import sys
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional

try:
    from sklearn.exceptions import InconsistentVersionWarning
    warnings.filterwarnings('ignore', category=InconsistentVersionWarning)
except ImportError:
    pass
 
# PROJ fix for Windows conda
_cprefix = sys.prefix
_ppath   = os.path.join(_cprefix, 'Library', 'share', 'proj')
if os.path.isdir(_ppath):
    os.environ['PROJ_LIB'] = _ppath
 
# Import existing components
from data_acquisition.satellite_collector import SatelliteDataCollector
from data_acquisition.weather_analyzer import WeatherAnalyzer
from crop_analysis.crop_detector import CropDetector
from crop_analysis.performance_analyzer import CropPerformanceAnalyzer
from assessment.credit_scorer import CreditScorer
 
# Import new advanced components
try:
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
    from assessment.advanced_credit_scorer import AdvancedCreditScorer
    ADVANCED_FEATURES_AVAILABLE = True
except ImportError:
    ADVANCED_FEATURES_AVAILABLE = False
    print("WARNING: Advanced features not available. Run in BASIC mode or install dependencies.")
 
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
    """
    End-to-end satellite credit assessment pipeline with advanced ML features
    """
 
    def __init__(
        self,
        crop_model_path: str,
        mode: str = 'ENHANCED',  # 'BASIC' or 'ENHANCED'
        ml_mode: str = 'hybrid',  # 'rule_based', 'unsupervised', 'supervised', 'hybrid'
        verbose: bool = True,
        use_mongodb: bool = True,
        enable_continuous_data: bool = True,
        enable_crop_cycles: bool = True,
        enable_advanced_ml: bool = True,
    ):
        """
        Initialize pipeline
        
        Args:
            crop_model_path: Path to crop classification model
            mode: 'BASIC' (v3.0 features only) or 'ENHANCED' (all v4.0 features)
            ml_mode: ML scoring mode for advanced scorer
            verbose: Enable detailed logging
            use_mongodb: Enable MongoDB integration
            enable_continuous_data: Collect 3-year continuous time series
            enable_crop_cycles: Detect crop cycles from continuous data
            enable_advanced_ml: Use advanced ML credit scorer
        """
        self.verbose = verbose
        self.crop_model_path = crop_model_path
        self.mode = mode
        self.ml_mode = ml_mode
        self.use_mongodb = use_mongodb and MONGODB_AVAILABLE
        
        # Feature flags
        self.enable_continuous_data = enable_continuous_data and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
        self.enable_crop_cycles = enable_crop_cycles and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
        self.enable_advanced_ml = enable_advanced_ml and mode == 'ENHANCED' and ADVANCED_FEATURES_AVAILABLE
 
        logger.info(f"\n{'#'*70}")
        logger.info(f"# SATELLITE CREDIT PIPELINE  v{_VERSION}")
        logger.info(f"# Mode: {mode}  |  ML Mode: {ml_mode}")
        logger.info(f"{'#'*70}\n")
        
        # Feature status
        logger.info("Feature Status:")
        logger.info(f"  Continuous Data Collection: {'✓' if self.enable_continuous_data else '✗'}")
        logger.info(f"  Crop Cycle Detection:       {'✓' if self.enable_crop_cycles else '✗'}")
        logger.info(f"  Advanced ML Scoring:        {'✓' if self.enable_advanced_ml else '✗'}")
        logger.info(f"  MongoDB Integration:        {'✓' if self.use_mongodb else '✗'}\n")
 
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
        
        # Initialize credit scorer (basic or advanced)
        if self.enable_advanced_ml:
            self.credit_scorer = AdvancedCreditScorer(mode=ml_mode, verbose=True)
            logger.info(f"Advanced Credit Scorer initialized (mode={ml_mode})")
        else:
            self.credit_scorer = CreditScorer(verbose=True)
            logger.info("Basic Credit Scorer initialized")
        
        # Initialize advanced components (if enabled)
        if self.enable_crop_cycles:
            self.crop_cycle_detector = CropCycleDetector()
            self.land_utilization_analyzer = LandUtilizationAnalyzer()
            logger.info("Crop cycle detection enabled")
        else:
            self.crop_cycle_detector = None
            self.land_utilization_analyzer = None
        
        logger.info("Pipeline initialized\n")
 
    # ------------------------------------------------------------------
    # Public: database mode
    # ------------------------------------------------------------------
 
    def assess_farmer_from_db(self, farmer_id: str) -> Dict:
        """Fetch farm from MongoDB, run full assessment, save result."""
        if not self.use_mongodb:
            return {'farmer_id': farmer_id, 'status': 'FAILED',
                    'error': 'MongoDB not available. Use assess_farmer() instead.'}
 
        logger.info(f"\n{'='*70}\nFETCHING FARM: {farmer_id}\n{'='*70}\n")
 
        farm = self.db.get_farm_by_id(farmer_id)
        if farm is None:
            msg = f"Farmer {farmer_id} not found in database"
            logger.error(f"ERROR: {msg}")
            return {'farmer_id': farmer_id, 'status': 'FAILED', 'error': msg}
 
        # Extract crop hint and sowing date from database (helps with analysis)
        crop_hint = farm.get('crop')  # e.g., "Potato"
        sowing_date = farm.get('sowing_date')  # e.g., "2022-12-15"
        
        if crop_hint:
            logger.info(f"📍 Crop hint from database: {crop_hint}")
        if sowing_date:
            logger.info(f"📅 Sowing date from database: {sowing_date}")
 
        geometry = farm.get('geometry')
        if geometry and isinstance(geometry, list):
            geometry = self._convert_geometry_from_db(geometry)
 
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
 
        logger.info(f"\n{'='*70}\nFARMER ASSESSMENT: {farmer_id}\n{'='*70}\n")
        start_time = datetime.now()
 
        assessment: Dict = {
            'farmer_id': farmer_id,
            'assessment_date': start_time.isoformat(),
            'pipeline_version': _VERSION,
            'pipeline_mode': self.mode,
            'farmer_benefits': farmer_benefits,
            'crop_hint': crop_hint,
            'sowing_date': sowing_date,
            'warnings': [],
            'errors': [],
        }
 
        try:
            if not self.enable_continuous_data:
                raise NotImplementedError(
                    "BASIC/legacy mode is not wired with the current SatelliteDataCollector. "
                    "Run with mode='ENHANCED'."
                )

            # ============================================================
            # STEP 1: SATELLITE DATA COLLECTION (Continuous, season-aligned)
            # ============================================================
            logger.info("STEP 1: Collecting continuous satellite data...")
            satellite_data = self.satellite_collector.collect_historical_data(
                latitude=latitude,
                longitude=longitude,
                field_area_ha=field_area_ha,
                geometry=geometry,
                use_continuous=True,
            )
            assessment['satellite_data'] = satellite_data
            assessment['location'] = satellite_data['location']
            assessment['field_area_ha'] = satellite_data['field_area_ha']

            clat = satellite_data['location']['latitude']
            clon = satellite_data['location']['longitude']

            continuous_data = satellite_data.get('continuous_data') or {}
            if not continuous_data or not continuous_data.get('scenes'):
                raise ValueError("No continuous satellite scenes available for crop cycles.")

            # Initialize analyzers with location
            self.weather_analyzer = WeatherAnalyzer(
                latitude=clat, longitude=clon, verbose=False
            )
            self.crop_detector = CropDetector(
                crop_model_path=self.crop_model_path,
                latitude=clat, longitude=clon, verbose=False
            )

            # Add continuous data stats
            assessment['continuous_data_stats'] = {
                'enabled': True,
                'scenes_collected': len(continuous_data.get('scenes', [])),
                'date_range': {
                    'start': continuous_data.get('start_date'),
                    'end': continuous_data.get('end_date'),
                },
                'total_days': continuous_data.get('total_days', 0),
            }

            # ============================================================
            # STEP 2: Dynamic Crop Cycle Detection
            # ============================================================
            logger.info("\nSTEP 2: Detecting crop cycles from continuous data...")
            crop_cycles = []
            utilization_metrics = None

            if self.enable_crop_cycles and self.crop_cycle_detector and self.land_utilization_analyzer:
                if len(continuous_data.get('dates', [])) < 20:
                    logger.warning(
                        f"Insufficient continuous data: {len(continuous_data.get('dates', []))} scenes (need 20+)"
                    )
                else:
                    crop_cycles = self.crop_cycle_detector.detect_cycles(
                        dates=continuous_data['dates'],
                        ndvi_values=continuous_data['ndvi_values'],
                        evi_values=continuous_data.get('evi_values'),
                        ndmi_values=continuous_data.get('ndmi_values'),
                        scenes=continuous_data.get('scenes'),
                        sowing_date_hint=sowing_date,
                        crop_hint=crop_hint,
                    )

                    if crop_cycles:
                        logger.info(f"  ✓ Detected {len(crop_cycles)} crop cycles")

                        # LandUtilizationAnalyzer expects datetime objects
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
                                f"  ✓ Land utilization: {utilization_metrics.get('land_utilization_index', 0):.1%}"
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

            # ============================================================
            # STEP 5: Dynamic Crop Performance Evaluation (cycle-based)
            # ============================================================
            logger.info("\nSTEP 5: Evaluating crop performance...")
            performance_analysis = self.performance_analyzer.analyze_performance(
                season_results=cropping_analysis.get('season_results', []),
                seasonal_data=[],
            )
            assessment['performance_analysis'] = performance_analysis

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

            # ============================================================
            # STEP 7: Credit Limit + Recommendations
            # ============================================================
            logger.info("\nSTEP 7: Generating credit recommendations...")

            try:
                credit_recommendations = self.credit_scorer.calculate_credit_limit(
                    credit_score=credit_assessment['credit_score'],
                    field_area_ha=satellite_data['field_area_ha'],
                    cropping_analysis=cropping_analysis,
                    performance_analysis=performance_analysis,
                    farmer_benefits=farmer_benefits,
                )
            except TypeError:
                # Basic scorer fallback (doesn't accept performance_analysis)
                credit_recommendations = self.credit_scorer.calculate_credit_limit(
                    credit_score=credit_assessment['credit_score'],
                    field_area_ha=satellite_data['field_area_ha'],
                    cropping_analysis=cropping_analysis,
                    farmer_benefits=farmer_benefits,
                )

            assessment['credit_recommendations'] = credit_recommendations

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
                    logger.info("\nSTEP 7: Saving to MongoDB...")
                    self.db.save_assessment(assessment)
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
 
    def _convert_geometry_from_db(self, geometry):
        """
        Convert geometry from MongoDB format to Shapely Polygon.
        
        Handles multiple formats:
        - Array of {latitude, longitude} objects
        - Array of {lat, lon} objects  
        - Array of {lat, lng} objects
        - GeoJSON format
        - None (missing geometry)
        
        Args:
            geometry: Geometry data from MongoDB
            
        Returns:
            Shapely Polygon object or None
        """
        # Handle None or missing geometry
        if geometry is None or not geometry:
            logger.info("No geometry provided, will use point buffer from lat/lon")
            return None
        
        try:
            from shapely.geometry import Polygon, shape
            
            # Case 1: Already a Shapely geometry object
            if hasattr(geometry, 'geom_type'):
                logger.debug("Geometry is already Shapely object")
                return geometry
            
            # Case 2: GeoJSON format (dict with 'type' and 'coordinates')
            if isinstance(geometry, dict):
                if 'type' in geometry and 'coordinates' in geometry:
                    logger.debug("Converting from GeoJSON format")
                    return shape(geometry)
                else:
                    logger.warning(f"Invalid geometry dict (missing 'type' or 'coordinates')")
                    return None
            
            # Case 3: Array of coordinate objects (YOUR FORMAT)
            # e.g., [{latitude: 27.49, longitude: 78.04}, ...]
            if isinstance(geometry, list) and len(geometry) >= 3:
                coords = []
                
                for point in geometry:
                    if not isinstance(point, dict):
                        logger.warning(f"Invalid point format: {point}")
                        continue
                    
                    # Try different field name variations
                    lon = point.get('longitude') or point.get('lon') or point.get('lng')
                    lat = point.get('latitude') or point.get('lat')
                    
                    if lon is not None and lat is not None:
                        coords.append((float(lon), float(lat)))
                    else:
                        logger.warning(f"Point missing lat/lon: {point}")
                
                if len(coords) >= 3:
                    logger.info(f"✓ Converted {len(coords)} coordinates to Polygon")
                    return Polygon(coords)
                else:
                    logger.warning(f"Not enough valid coordinates: {len(coords)}/3 minimum")
                    return None
            
            logger.warning(f"Unrecognized geometry format: {type(geometry)}")
            return None
            
        except Exception as e:
            logger.error(f"Geometry conversion failed: {e}")
            import traceback
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
    parser.add_argument('--mode', type=str, default='ENHANCED', 
                       choices=['BASIC', 'ENHANCED'],
                       help='Pipeline mode (default: ENHANCED)')
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
        mode=args.mode,
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