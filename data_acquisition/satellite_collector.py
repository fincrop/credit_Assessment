# """
# Satellite Data Collector
# =========================
# Collects Sentinel-2 satellite imagery for historical crop analysis.

# VERSION 3.0 — Major Updates:
# 1. Season windows: 10 seasons (5 Kharif + 5 Rabi), no Summer, no overlaps
#    - Kharif: Jun 1 – Nov 30
#    - Rabi:   Dec 1 – May 31  (extended from Apr 30)
# 2. Scenes per season: 25 target / 30 max (was 18/24)
# 3. Cloud cover threshold: 60% (was 80%)
# 4. Scene ordering: ALWAYS chronological (never cloud-cover sorted for ML)
#    - Within each time-bin, pick lowest-cloud scene
#    - Final list sorted by date ascending
# 5. Cross-season continuity detection:
#    - Detects if a long-duration crop (Sugarcane, Banana, Tur…) spans
#      two consecutive season windows and flags them as one crop event
#    - Prevents double-counting of the same crop as two separate plantings
# 6. Late-sown crop handling:
#    - A crop sown in Aug/Sep (late Kharif) with harvest in Feb/Mar
#      appears across both a Kharif AND the following Rabi window
#    - Cross-season merger identifies this via NDVI continuity and merges
#      the two windows into a single crop record
# """

# import numpy as np
# import pandas as pd
# from datetime import datetime, timedelta, date
# from pathlib import Path
# from typing import Dict, List, Tuple, Optional, Union
# import logging
# import gc

# try:
#     import pystac_client
#     import planetary_computer
#     import rasterio
#     from rasterio.windows import from_bounds, Window
#     from rasterio.enums import Resampling
#     from rasterio.warp import transform_bounds
#     SATELLITE_AVAILABLE = True
# except ImportError:
#     SATELLITE_AVAILABLE = False
#     print("⚠ Satellite libraries not available")

# try:
#     #     REGIONAL_CONFIG_AVAILABLE = True
# except ImportError:
#     REGIONAL_CONFIG_AVAILABLE = False

# from config import PipelineConfig
# from utils.geometry_utils import GeometryUtils
# from utils.data_processing import DataProcessor

# logger = logging.getLogger(__name__)


# class SatelliteDataCollector:
#     """
#     Collects Sentinel-2 satellite imagery for the previous N seasons
#     and performs cross-season continuity analysis to correctly identify
#     long-duration and late-sown crops.
#     """

#     def __init__(self, verbose: bool = True):
#         if not SATELLITE_AVAILABLE:
#             raise ImportError(
#                 "Install: pip install pystac-client planetary-computer rasterio shapely"
#             )

#         self.stac_url  = PipelineConfig.STAC_API_URL
#         self.catalog   = pystac_client.Client.open(self.stac_url)
#         self.verbose   = verbose
#         self.band_info = {
#             band: info['resolution']
#             for band, info in PipelineConfig.SENTINEL2_BANDS.items()
#         }

#         self.target_scenes = PipelineConfig.TARGET_SCENES_PER_SEASON
#         self.max_scenes    = PipelineConfig.MAX_SCENES_PER_SEASON
#         self.min_scenes    = PipelineConfig.MIN_SCENES_PER_SEASON
#         self.max_cloud     = PipelineConfig.MAX_CLOUD_COVER
#         self.max_cloud_rabi = 60  # Stricter for dry season
#         self.max_cloud_kharif = 80  # More lenient for monsoon
#         # self.max_cloud = PipelineConfig.max_cloud_cover  # Keep for backward compatibility
 


#         logger.info("✔ SatelliteDataCollector v3.0 initialized")
#         logger.info(f"  Seasons: {list(PipelineConfig.SEASONS.keys())}  |  "
#                     f"Target scenes: {self.target_scenes}  |  "
#                     f"Max cloud: {self.max_cloud}%")

#     # =========================================================================
#     # PUBLIC API
#     # =========================================================================

#     def collect_historical_data(
#         self,
#         latitude:     Optional[float]  = None,
#         longitude:    Optional[float]  = None,
#         field_area_ha: Optional[float] = None,
#         geometry:     Optional[object] = None,
#         num_seasons:  int = None,
#     ) -> Dict:
#         """
#         Collect satellite data for the previous N seasons across
#         Kharif and Rabi windows, then run cross-season continuity
#         analysis to detect long-duration / late-sown crops.

#         Args:
#             latitude, longitude: Field centre point
#             field_area_ha: Field area (used for bounding box if point given)
#             geometry: Shapely Polygon or GeoJSON (alternative to lat/lon)
#             num_seasons: Number of seasons to analyse (default from config)

#         Returns:
#             Dict with keys:
#               location, bbox, field_area_ha,
#               seasonal_data  — raw per-season results
#               merged_seasons — cross-season continuity resolved
#               collection_date, summary
#         """
#         if num_seasons is None:
#             num_seasons = PipelineConfig.NUM_SEASONS

#         logger.info(f"\n{'='*70}")
#         logger.info(f"COLLECTING SATELLITE DATA — {num_seasons} SEASONS")
#         logger.info(f"{'='*70}")

#         # ── Determine bounding box ─────────────────────────────────────────
#         if geometry is not None:
#             bbox          = GeometryUtils.calculate_bbox_from_geometry(geometry)
#             centroid_lat, centroid_lon = GeometryUtils.calculate_centroid_from_geometry(geometry)
#             field_area    = GeometryUtils.calculate_area_from_geometry(geometry)
#             logger.info(f"Input: geometry polygon  |  Centroid ({centroid_lat:.4f}, {centroid_lon:.4f})")
#             logger.info(f"Computed area: {field_area:.2f} ha")

#         elif latitude is not None and longitude is not None:
#             field_area_ha = field_area_ha or 1.0
#             bbox          = GeometryUtils.calculate_bbox_from_point(latitude, longitude, field_area_ha)
#             centroid_lat, centroid_lon = latitude, longitude
#             field_area    = field_area_ha
#             logger.info(f"Input: point ({latitude:.4f}, {longitude:.4f})  |  Area {field_area_ha:.2f} ha")
#         else:
#             raise ValueError("Must provide (latitude, longitude) or geometry")

#         if not GeometryUtils.validate_bbox(bbox):
#             raise ValueError(f"Invalid bounding box: {bbox}")

#         logger.info(f"BBox: {[round(v, 5) for v in bbox]}")

#         # ── Generate season windows ────────────────────────────────────────
#         season_windows = self._generate_season_windows(num_seasons)
#         logger.info(f"Season windows: {len(season_windows)}")
#         for w in season_windows:
#             logger.info(f"  {w['season']:7s} {w['year']}  {w['start_date']} → {w['end_date']}")

#         # ── Collect per-season data ────────────────────────────────────────
#         seasonal_data = []
#         for i, win in enumerate(season_windows):
#             logger.info(f"\n[{i+1}/{len(season_windows)}] "
#                         f"{win['season'].upper()} {win['year']}  "
#                         f"({win['start_date']} → {win['end_date']})")
#             try:
#                 result = self._collect_season_data(
#                     bbox,
#                     win['start_date'], win['end_date'],
#                     win['season'], win['year'],
#                 )
#                 seasonal_data.append(result)
#             except Exception as e:
#                 logger.error(f"  [FAIL] Failed: {str(e)[:80]}")
#                 seasonal_data.append({
#                     'season':     win['season'],
#                     'year':       win['year'],
#                     'start_date': win['start_date'],
#                     'end_date':   win['end_date'],
#                     'scenes':     [],
#                     'error':      str(e),
#                 })
#             gc.collect()

#         # ── Cross-season continuity analysis ──────────────────────────────
#         merged_seasons = self._cross_season_analysis(seasonal_data)

#         # ── Summary ───────────────────────────────────────────────────────
#         total_scenes = sum(len(s.get('scenes', [])) for s in seasonal_data)
#         seasons_ok   = sum(1 for s in seasonal_data if len(s.get('scenes', [])) >= self.min_scenes)
#         long_crops   = sum(1 for m in merged_seasons if m.get('is_cross_season'))

#         logger.info(f"\n{'='*70}")
#         logger.info(f"COLLECTION COMPLETE")
#         logger.info(f"  Seasons with sufficient data: {seasons_ok}/{len(seasonal_data)}")
#         logger.info(f"  Total scenes collected:       {total_scenes}")
#         logger.info(f"  Avg scenes/season:            {total_scenes/max(seasons_ok,1):.1f}")
#         logger.info(f"  Cross-season crops detected:  {long_crops}")
#         logger.info(f"{'='*70}")

#         return {
#             'location':        {'latitude': centroid_lat, 'longitude': centroid_lon},
#             'bbox':            bbox,
#             'field_area_ha':   field_area,
#             'num_seasons':     num_seasons,
#             'seasonal_data':   seasonal_data,
#             'merged_seasons':  merged_seasons,
#             'collection_date': datetime.now().isoformat(),
#             'summary': {
#                 'total_scenes':         total_scenes,
#                 'seasons_with_data':    seasons_ok,
#                 'cross_season_crops':   long_crops,
#                 'avg_scenes_per_season': round(total_scenes / max(seasons_ok, 1), 1),
#             },
#         }

#     def collect_satellite_data(
#         self,
#         latitude:     Optional[float]  = None,
#         longitude:    Optional[float]  = None,
#         field_area_ha: Optional[float] = None,
#         geometry:     Optional[object] = None,
#         start_date:   Optional[str] = None,
#         end_date:     Optional[str] = None,
#         interval_days: int = 7
#     ) -> Dict:
#         """
#         Collect continuous satellite data over a date range (non-seasonal).
#         This is used for crop cycle detection from continuous time-series.

#         Args:
#             latitude, longitude: Field centre point
#             field_area_ha: Field area (used for bounding box if point given)
#             geometry: Shapely Polygon or GeoJSON (alternative to lat/lon)
#             start_date: Start date (YYYY-MM-DD format)
#             end_date: End date (YYYY-MM-DD format)
#             interval_days: Target days between observations (default 7)

#         Returns:
#             Dict with keys:
#               location, bbox, field_area_ha,
#               scenes  — list of processed scenes with indices
#               dates   — list of observation dates
#               ndvi_values — list of NDVI mean values
#               evi_values  — list of EVI mean values
#               start_date, end_date, total_days, collection_date
#         """
#         logger.info(f"\n{'='*70}")
#         logger.info(f"COLLECTING CONTINUOUS SATELLITE DATA")
#         logger.info(f"  Period: {start_date} → {end_date}")
#         logger.info(f"  Target interval: {interval_days} days")
#         logger.info(f"{'='*70}")

#         # ── Determine bounding box ─────────────────────────────────────────
#         if geometry is not None:
#             bbox          = GeometryUtils.calculate_bbox_from_geometry(geometry)
#             centroid_lat, centroid_lon = GeometryUtils.calculate_centroid_from_geometry(geometry)
#             field_area    = GeometryUtils.calculate_area_from_geometry(geometry)
#             logger.info(f"Input: geometry polygon  |  Centroid ({centroid_lat:.4f}, {centroid_lon:.4f})")
#             logger.info(f"Computed area: {field_area:.2f} ha")

#         elif latitude is not None and longitude is not None:
#             field_area_ha = field_area_ha or 1.0
#             bbox          = GeometryUtils.calculate_bbox_from_point(latitude, longitude, field_area_ha)
#             centroid_lat, centroid_lon = latitude, longitude
#             field_area    = field_area_ha
#             logger.info(f"Input: point ({latitude:.4f}, {longitude:.4f})  |  Area {field_area_ha:.2f} ha")
#         else:
#             raise ValueError("Must provide (latitude, longitude) or geometry")

#         if not GeometryUtils.validate_bbox(bbox):
#             raise ValueError(f"Invalid bounding box: {bbox}")

#         logger.info(f"BBox: {[round(v, 5) for v in bbox]}")

#         # ── Search and select scenes ───────────────────────────────────────
#         candidates = self._search_scenes(bbox, start_date, end_date)
#         logger.info(f"Candidates found: {len(candidates)}")

#         if not candidates:
#             logger.warning("No satellite data available for the period")
#             return {
#                 'location': {'latitude': centroid_lat, 'longitude': centroid_lon},
#                 'bbox': bbox,
#                 'field_area_ha': field_area,
#                 'scenes': [],
#                 'dates': [],
#                 'ndvi_values': [],
#                 'evi_values': [],
#                 'ndmi_values': [],
#                 'start_date': start_date,
#                 'end_date': end_date,
#                 'total_days': (datetime.strptime(end_date, '%Y-%m-%d') - 
#                               datetime.strptime(start_date, '%Y-%m-%d')).days,
#                 'collection_date': datetime.now().isoformat()
#             }

#         # Calculate target number of scenes based on interval
#         total_days = (datetime.strptime(end_date, '%Y-%m-%d') - 
#                      datetime.strptime(start_date, '%Y-%m-%d')).days
#         target_scenes = min(150, max(20, total_days // interval_days))
        
#         # Select scenes with temporal distribution
#         selected = self._select_scenes_chronological(candidates, target_scenes)
#         logger.info(f"Selected: {len(selected)} scenes (chronological)")

#         # ── Download and process scenes ────────────────────────────────────
#         processed_scenes = []
#         dates = []
#         ndvi_values = []
#         evi_values = []
#         ndmi_values = []
        
#         for i, item in enumerate(selected):
#             try:
#                 scene_date = item.datetime.strftime('%Y-%m-%d') if item.datetime else start_date
#                 cloud_pct = float(item.properties.get('eo:cloud_cover', 0))
                
#                 if i % 10 == 0:  # Log every 10th scene to reduce clutter
#                     logger.info(f"  [{i+1:03d}/{len(selected)}] {scene_date}  cloud={cloud_pct:.1f}%")

#                 band_data = self._download_bands(item, bbox)

#                 if len(band_data) >= 4:
#                     indices = self._calculate_indices(band_data)
#                     processed_scenes.append({
#                         'date': scene_date,
#                         'cloud_cover': cloud_pct,
#                         'indices': indices
#                     })
#                     dates.append(scene_date)
#                     ndvi_values.append(indices.get('NDVI_mean', 0.0))
#                     evi_values.append(indices.get('EVI_mean', 0.0))
#                     ndmi_values.append(indices.get('NDMI_mean', 0.0))
#                 else:
#                     if i % 10 == 0:
#                         logger.warning(f"  ⚠ Insufficient bands ({len(band_data)}), skipped")
                    
#             except Exception as e:
#                 if i % 10 == 0:
#                     logger.warning(f"  ⚠ Processing failed: {str(e)[:50]}")
#                 continue
                
#             gc.collect()

#         logger.info(f"\nContinuous collection complete: {len(processed_scenes)} scenes")
#         if ndvi_values:
#             logger.info(f"  NDVI range: {min(ndvi_values):.3f} - {max(ndvi_values):.3f}")
#         if dates:
#             logger.info(f"  Date range: {dates[0]} → {dates[-1]}")

#         return {
#             'location': {'latitude': centroid_lat, 'longitude': centroid_lon},
#             'bbox': bbox,
#             'field_area_ha': field_area,
#             'scenes': processed_scenes,
#             'dates': dates,
#             'ndvi_values': ndvi_values,
#             'evi_values': evi_values,
#             'ndmi_values': ndmi_values,
#             'start_date': start_date,
#             'end_date': end_date,
#             'total_days': total_days,
#             'collection_date': datetime.now().isoformat()
#         }

#     # =========================================================================
#     # SEASON WINDOW GENERATION
#     # =========================================================================

#     def _generate_season_windows(self, num_seasons: int) -> List[Dict]:
#         """
#         Generate the last `num_seasons` season windows, alternating
#         between Kharif and Rabi in reverse-chronological order then
#         re-sorted oldest-first.

#         Kharif : Jun  1 – Nov 30  (contained within one calendar year)
#         Rabi   : Dec  1 – May 31  (spans two calendar years)

#         Only completed seasons are included (end_date < today).
#         """
#         windows = []
#         today   = datetime.now().date()
#         season_defs = PipelineConfig.SEASONS   # kharif, rabi

#         # We iterate backwards season by season until we have enough
#         # Start from current year and work back
#         current_year = today.year
#         attempts     = 0
#         max_attempts = num_seasons * 3   # safety

#         # Build a flat list of (season_name, anchor_year) going backwards
#         # Anchor year for kharif = the calendar year it falls in
#         # Anchor year for rabi   = the year when Dec starts (i.e. first year)

#         # Determine current position in season cycle
#         # If today is Jun–Nov → we are in Kharif of current_year
#         # If today is Dec     → we are at start of Rabi (Dec current_year)
#         # If today is Jan–May → we are in Rabi that started Dec of prev year

#         candidate_slots = []
#         for year_offset in range(PipelineConfig.MAX_YEARS_BACK + 1):
#             yr = current_year - year_offset
#             for season_name in ['kharif', 'rabi']:
#                 cfg = season_defs[season_name]
#                 if season_name == 'kharif':
#                     start_date = date(yr, cfg['start_month'], cfg['start_day'])
#                     end_date   = date(yr, cfg['end_month'],   cfg['end_day'])
#                     label_year = yr
#                 else:
#                     # Rabi: Dec yr → May yr+1
#                     start_date = date(yr,   cfg['start_month'], cfg['start_day'])
#                     end_date   = date(yr+1, cfg['end_month'],   cfg['end_day'])
#                     label_year = yr   # label by the Dec start year

#                 candidate_slots.append({
#                     'season':     season_name,
#                     'year':       label_year,
#                     'start_date': start_date.strftime('%Y-%m-%d'),
#                     'end_date':   end_date.strftime('%Y-%m-%d'),
#                     '_end_date_obj': end_date,
#                 })

#         # Filter to only completed seasons, sort newest-first, take N
#         completed = [s for s in candidate_slots if s['_end_date_obj'] < today]

#         # Deduplicate (same season+year could appear from multiple year offsets)
#         seen = set()
#         unique_completed = []
#         for s in completed:
#             key = (s['season'], s['year'])
#             if key not in seen:
#                 seen.add(key)
#                 unique_completed.append(s)

#         # Sort newest-first, take num_seasons, then reverse to oldest-first
#         unique_completed.sort(key=lambda x: x['_end_date_obj'], reverse=True)
#         selected = unique_completed[:num_seasons]
#         selected.sort(key=lambda x: x['start_date'])   # oldest first

#         # Clean up helper field
#         for s in selected:
#             del s['_end_date_obj']

#         return selected

#     # =========================================================================
#     # PER-SEASON DATA COLLECTION
#     # =========================================================================

#     def _collect_season_data(
#         self,
#         bbox:       List[float],
#         start_date: str,
#         end_date:   str,
#         season:     str,
#         year:       int,
#     ) -> Dict:
#         """
#         Collect and process all Sentinel-2 scenes for a single season window.

#         Scene selection strategy:
#           1. Search all scenes with cloud < MAX_CLOUD_COVER (up to 150 candidates)
#           2. Divide season into equal time-bins (target_scenes bins)
#           3. Within each bin: pick the scene with lowest cloud cover
#           4. Fill any remaining slots from leftover scenes (lowest cloud)
#           5. Sort final list chronologically (CRITICAL for ML feature ordering)
#         """
#         # Search
#         candidates = self._search_scenes(bbox, start_date, end_date)
#         logger.info(f"  Candidates found: {len(candidates)}")

#         if not candidates:
#             return self._empty_season(season, year, start_date, end_date,
#                                       'No satellite data available')

#         # Select with temporal distribution, keep chronological order
#         selected = self._select_scenes_chronological(candidates, self.target_scenes)
#         logger.info(f"  Selected: {len(selected)} scenes (chronological)")

#         if len(selected) < self.min_scenes:
#             logger.warning(f"  ⚠ Only {len(selected)} scenes — below minimum ({self.min_scenes})")

#         # Download and process
#         processed_scenes = []
#         for i, item in enumerate(selected):
#             try:
#                 scene_date   = item.datetime.strftime('%Y-%m-%d') if item.datetime else start_date
#                 cloud_pct    = float(item.properties.get('eo:cloud_cover', 0))
#                 logger.info(f"    [{i+1:02d}/{len(selected)}] {scene_date}  cloud={cloud_pct:.1f}%")

#                 band_data = self._download_bands(item, bbox)

#                 if len(band_data) >= 4:
#                     indices = self._calculate_indices(band_data)
#                     processed_scenes.append({
#                         'date':        scene_date,
#                         'cloud_cover': cloud_pct,
#                         'bands':       {k: DataProcessor.calculate_array_stats(v)
#                                         for k, v in band_data.items()},
#                         'indices':     indices,
#                     })
#                     # Log all 6 spectral indices
#                     logger.info(f"       ✔ NDVI={indices.get('NDVI_mean', 0):.3f}  "
#                                 f"EVI={indices.get('EVI_mean', 0):.3f}  "
#                                 f"NDMI={indices.get('NDMI_mean', 0):.3f}  "
#                                 f"PSRI={indices.get('PSRI_mean', 0):.3f}  "
#                                 f"NDRE={indices.get('NDRE_mean', 0):.3f}  "
#                                 f"NDWI={indices.get('NDWI_mean', 0):.3f}")
                    
#                     # Debug: Show which bands were downloaded (only for first scene)
#                     if i == 0:
#                         available_bands = sorted(band_data.keys())
#                         logger.info(f"       📊 Available bands: {', '.join(available_bands)}")
#                 else:
#                     logger.warning(f"       ⚠ Insufficient bands ({len(band_data)}), skipped")

#             except Exception as e:
#                 logger.warning(f"       [FAIL] Scene failed: {str(e)[:60]}")
#                 continue

#         # Log temporal gap diagnostics
#         self._log_temporal_gaps(processed_scenes)

#         return {
#             'season':     season,
#             'year':       year,
#             'start_date': start_date,
#             'end_date':   end_date,
#             'scenes':     processed_scenes,
#             'n_scenes':   len(processed_scenes),
#             # Quick stats for cross-season analysis
#             'ndvi_start': self._window_mean_ndvi(processed_scenes, 'start'),
#             'ndvi_end':   self._window_mean_ndvi(processed_scenes, 'end'),
#             'ndvi_peak':  max((s['indices'].get('NDVI_mean', 0)
#                                for s in processed_scenes), default=0.0),
#         }

#     # =========================================================================
#     # SCENE SELECTION — Temporal distribution, chronological output
#     # =========================================================================

#     def _select_scenes_chronological(
#         self,
#         candidates:    List,
#         target_scenes: int,
#     ) -> List:
#         """
#         Select up to `target_scenes` scenes with good temporal distribution,
#         then return them sorted by date (oldest first).

#         Algorithm:
#           - Sort candidates by date
#           - Divide date range into `target_scenes` equal bins
#           - Pick the scene with LOWEST cloud cover within each bin
#           - If bins are empty (sparse data), fill from remaining candidates
#           - Final output is always date-sorted (chronological)
#         """
#         if not candidates:
#             return []

#         # Sort all candidates chronologically first
#         dated = [(c.datetime, c) for c in candidates if c.datetime]
#         dated.sort(key=lambda x: x[0])

#         if not dated:
#             return candidates[:target_scenes]

#         if len(dated) <= target_scenes:
#             return [c for _, c in dated]

#         min_dt = dated[0][0]
#         max_dt = dated[-1][0]
#         span_days = max(1, (max_dt - min_dt).total_seconds() / 86400)

#         bin_size = span_days / target_scenes
#         selected = []
#         used_dts = set()

#         for i in range(target_scenes):
#             bin_start = min_dt + timedelta(days=i * bin_size)
#             bin_end   = min_dt + timedelta(days=(i + 1) * bin_size)

#             bin_candidates = [
#                 c for dt, c in dated
#                 if bin_start <= dt < bin_end and dt not in used_dts
#             ]

#             if bin_candidates:
#                 best = min(bin_candidates,
#                            key=lambda c: c.properties.get('eo:cloud_cover', 100))
#                 selected.append(best)
#                 used_dts.add(best.datetime)

#         # Fill remaining slots from unused candidates (lowest cloud first)
#         if len(selected) < target_scenes:
#             remaining = [c for _, c in dated if c.datetime not in used_dts]
#             remaining.sort(key=lambda c: c.properties.get('eo:cloud_cover', 100))
#             needed = min(
#                 self.max_scenes - len(selected),
#                 target_scenes - len(selected),
#             )
#             selected.extend(remaining[:needed])

#         # Cap at max_scenes
#         selected = selected[:self.max_scenes]

#         # Final chronological sort (CRITICAL — ML features must be time-ordered)
#         selected.sort(key=lambda c: c.datetime if c.datetime else datetime.min)

#         return selected

#     # =========================================================================
#     # CROSS-SEASON CONTINUITY ANALYSIS
#     # =========================================================================

#     def _cross_season_analysis(self, seasonal_data: List[Dict]) -> List[Dict]:
#         """
#         Analyse consecutive season pairs to detect:

#         Case A — Long-duration crop (Sugarcane, Banana, Tur…):
#           Crop is sown in season-A and still growing / just harvesting in
#           season-B. Identified by HIGH NDVI at the END of season-A AND
#           HIGH NDVI at the START of season-B (i.e. no clear harvest dip).

#         Case B — Late-sown crop (Late Kharif or early Rabi):
#           e.g. Cotton sown Aug → harvest Feb; Wheat sown Feb → harvest Jun.
#           These cross the season boundary. Identified by:
#             - Significant NDVI RISE within season-A that hasn't peaked yet
#               at season end  → AND
#             - NDVI continuing high at start of season-B before dropping
#           In this case the two windows together represent ONE crop, not two.

#         Case C — Normal two separate crops:
#           NDVI drops clearly between season-A end and season-B start
#           (harvest dip visible) → two independent crops.

#         Returns:
#           merged_seasons — a list of dicts, one per 'crop event'.
#           Each dict has:
#             season_keys      : list of (season, year) pairs it covers
#             is_cross_season  : True if spans more than one window
#             cross_season_type: 'long_duration' | 'late_sown' | 'normal'
#             scenes           : combined chronological scene list
#             start_date, end_date: full span
#         """
#         if not seasonal_data:
#             return []

#         merged    = []
#         skip_next = False

#         CONTINUITY = PipelineConfig.CROSS_SEASON_NDVI_CONTINUITY
#         DROP       = PipelineConfig.CROSS_SEASON_NDVI_DROP_FOR_NEW_CROP

#         for i, season in enumerate(seasonal_data):
#             if skip_next:
#                 skip_next = False
#                 continue

#             if i + 1 >= len(seasonal_data):
#                 # Last season — no pair possible
#                 merged.append(self._single_season_record(season))
#                 continue

#             next_season = seasonal_data[i + 1]

#             # Only analyse consecutive Kharif→Rabi or Rabi→Kharif pairs
#             if not self._are_consecutive(season, next_season):
#                 merged.append(self._single_season_record(season))
#                 continue

#             end_ndvi   = season.get('ndvi_end',   0.0)
#             start_ndvi = next_season.get('ndvi_start', 0.0)
#             ndvi_drop  = end_ndvi - start_ndvi

#             # ── Case A / B: Continuity — same crop spanning both seasons ──
#             if end_ndvi >= CONTINUITY and start_ndvi >= CONTINUITY:
#                 # High NDVI on both sides → same crop continuing
#                 cross_type = 'long_duration'
#                 logger.info(
#                     f"  ⟳ Cross-season LONG-DURATION detected: "
#                     f"{season['season']} {season['year']} → "
#                     f"{next_season['season']} {next_season['year']}  "
#                     f"(end_NDVI={end_ndvi:.3f}, start_NDVI={start_ndvi:.3f})"
#                 )
#                 merged.append(self._merged_season_record(season, next_season, cross_type))
#                 skip_next = True

#             elif end_ndvi >= CONTINUITY and start_ndvi >= (CONTINUITY * 0.7) and ndvi_drop < DROP:
#                 # End of A is high, start of B is moderately high, small drop
#                 # → late-sown crop still filling across boundary
#                 cross_type = 'late_sown'
#                 logger.info(
#                     f"  ⟳ Cross-season LATE-SOWN detected: "
#                     f"{season['season']} {season['year']} → "
#                     f"{next_season['season']} {next_season['year']}  "
#                     f"(end_NDVI={end_ndvi:.3f}, start_NDVI={start_ndvi:.3f}, "
#                     f"drop={ndvi_drop:.3f})"
#                 )
#                 merged.append(self._merged_season_record(season, next_season, cross_type))
#                 skip_next = True

#             else:
#                 # ── Case C: Clear harvest gap → normal separate crops ──
#                 merged.append(self._single_season_record(season))

#         return merged

#     # ─── Helpers for cross-season records ────────────────────────────────────

#     @staticmethod
#     def _are_consecutive(a: Dict, b: Dict) -> bool:
#         """Return True if season b immediately follows season a."""
#         try:
#             end_a   = datetime.strptime(a['end_date'],   '%Y-%m-%d')
#             start_b = datetime.strptime(b['start_date'], '%Y-%m-%d')
#             gap_days = (start_b - end_a).days
#             return 0 <= gap_days <= 5   # allow up to 5-day buffer
#         except Exception:
#             return False

#     @staticmethod
#     def _single_season_record(season: Dict) -> Dict:
#         return {
#             'season_keys':       [(season['season'], season['year'])],
#             'is_cross_season':   False,
#             'cross_season_type': 'normal',
#             'season':            season['season'],
#             'year':              season['year'],
#             'start_date':        season['start_date'],
#             'end_date':          season['end_date'],
#             'scenes':            season.get('scenes', []),
#             'n_scenes':          season.get('n_scenes', 0),
#             'ndvi_peak':         season.get('ndvi_peak', 0.0),
#         }

#     @staticmethod
#     def _merged_season_record(a: Dict, b: Dict, cross_type: str) -> Dict:
#         """Merge two consecutive season dicts into one crop-event record."""
#         combined_scenes = sorted(
#             a.get('scenes', []) + b.get('scenes', []),
#             key=lambda s: s.get('date', ''),
#         )
#         return {
#             'season_keys':       [(a['season'], a['year']), (b['season'], b['year'])],
#             'is_cross_season':   True,
#             'cross_season_type': cross_type,
#             'season':            f"{a['season']}+{b['season']}",
#             'year':              a['year'],
#             'start_date':        a['start_date'],
#             'end_date':          b['end_date'],
#             'scenes':            combined_scenes,
#             'n_scenes':          len(combined_scenes),
#             'ndvi_peak':         max(
#                 a.get('ndvi_peak', 0.0),
#                 b.get('ndvi_peak', 0.0),
#             ),
#         }

#     # =========================================================================
#     # SCENE SEARCH
#     # =========================================================================

#     # def _search_scenes(self, bbox, start_date, end_date) -> List:
#     #     try:
#     #         search = self.catalog.search(
#     #             collections=[PipelineConfig.SENTINEL2_COLLECTION],
#     #             bbox=bbox,
#     #             datetime=f"{start_date}/{end_date}",
#     #             query={"eo:cloud_cover": {"lt": self.max_cloud}},
#     #             limit=150,   # increased candidate pool
#     #         )
#     #         items = list(search.get_items())
#     #         # Return date-sorted (chronological) — cloud sorting done per-bin
#     #         items.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
#     #         return items
#     #     except Exception as e:
#     #         logger.error(f"  Scene search failed: {str(e)}")
#     #         return []

#     def _search_scenes(self, bbox, start_date, end_date, season_type='rabi') -> List:
#         """
#         Search for Sentinel-2 scenes with season-adaptive cloud threshold.
        
#         Args:
#             bbox: Bounding box [lon_min, lat_min, lon_max, lat_max]
#             start_date: Start date string
#             end_date: End date string
#             season_type: 'kharif' or 'rabi' for adaptive cloud threshold
            
#         Returns:
#             List of STAC items sorted chronologically
#         """
#         # Use adaptive cloud threshold
#         if season_type.lower() == 'kharif':
#             cloud_limit = self.max_cloud_kharif
#             logger.debug(f"Using Kharif cloud threshold: {cloud_limit}%")
#         else:
#             cloud_limit = self.max_cloud_rabi
#             logger.debug(f"Using Rabi cloud threshold: {cloud_limit}%")
        
#         try:
#             search = self.catalog.search(
#                 collections=[PipelineConfig.SENTINEL2_COLLECTION],
#                 bbox=bbox,
#                 datetime=f"{start_date}/{end_date}",
#                 query={"eo:cloud_cover": {"lt": cloud_limit}},
#                 limit=150,
#             )
#             items = list(search.get_items())
#             # Return date-sorted (chronological)
#             items.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
#             logger.info(f"      Found {len(items)} scenes (cloud <{cloud_limit}%)")
#             return items
#         except Exception as e:
#             logger.error(f"  Scene search failed: {str(e)}")
#             return []



# # ==============================================================================
# # CHANGE 4: Add continuous data collection method (NEW METHOD)
# # ==============================================================================
# # Add this NEW method after collect_seasonal_data:
 
#     def collect_satellite_data(
#         self,
#         latitude: float,
#         longitude: float,
#         start_date: Union[str, date],
#         end_date: Union[str, date],
#         geometry=None,
#         interval_days: int = 7
#     ) -> Dict:
#         """
#         Collect continuous (non-seasonal) satellite data for crop cycle detection.
        
#         Args:
#             latitude: Center latitude
#             longitude: Center longitude  
#             start_date: Start date (YYYY-MM-DD or date object)
#             end_date: End date (YYYY-MM-DD or date object)
#             geometry: Optional field polygon
#             interval_days: Target interval between scenes (default: 7 days weekly)
            
#         Returns:
#             Dictionary with continuous time series data
#         """
#         logger.info(f"Collecting continuous data: {start_date} to {end_date}")
        
#         # Convert dates
#         if isinstance(start_date, str):
#             start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
#         if isinstance(end_date, str):
#             end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        
#         # Calculate bounding box
#         bbox = self._get_bbox(latitude, longitude, geometry, 1.0)
        
#         # Search for all scenes in period
#         items = self._search_scenes(
#             bbox, 
#             start_date.strftime("%Y-%m-%d"),
#             end_date.strftime("%Y-%m-%d"),
#             season_type='continuous'  # Use moderate cloud threshold
#         )
        
#         if not items:
#             logger.warning(f"No scenes found for continuous collection")
#             return {'scenes': [], 'dates': [], 'ndvi_values': []}
        
#         logger.info(f"Found {len(items)} candidate scenes")
        
#         # Select scenes at regular intervals
#         selected_scenes = self._select_interval_scenes(items, interval_days)
        
#         logger.info(f"Selected {len(selected_scenes)} scenes (target interval: {interval_days} days)")
        
#         # Download and process scenes
#         scenes_data = []
#         dates = []
#         ndvi_values = []
        
#         for idx, item in enumerate(selected_scenes, 1):
#             scene_date = item.datetime.date() if item.datetime else None
#             if not scene_date:
#                 continue
            
#             logger.debug(f"  [{idx:03d}/{len(selected_scenes)}] {scene_date}")
            
#             # Download bands
#             bands = self._download_bands(item, bbox)
#             if not bands or len(bands) < 2:
#                 logger.warning(f"    ⚠ Insufficient bands, skipped")
#                 continue
            
#             # Calculate indices
#             indices = self._calculate_indices(bands)
            
#             # Store data
#             scene_info = {
#                 'date': scene_date.isoformat(),
#                 'indices': indices,
#                 'cloud_cover': item.properties.get('eo:cloud_cover', 0)
#             }
#             scenes_data.append(scene_info)
#             dates.append(scene_date)
#             ndvi_values.append(indices.get('NDVI_mean', 0))
            
#             logger.debug(f"    ✔ NDVI={indices.get('NDVI_mean', 0):.3f}")
        
#         logger.info(f"[OK] Continuous collection complete: {len(scenes_data)} scenes")
        
#         return {
#             'scenes': scenes_data,
#             'dates': [d.isoformat() for d in dates],
#             'ndvi_values': ndvi_values,
#             'start_date': start_date.isoformat(),
#             'end_date': end_date.isoformat(),
#             'total_days': (end_date - start_date).days,
#             'interval_days': interval_days
#         }
    
#     def _select_interval_scenes(self, items: List, target_interval_days: int) -> List:
#         """
#         Select scenes at approximately regular intervals.
        
#         Args:
#             items: List of STAC items (chronologically sorted)
#             target_interval_days: Target days between scenes
            
#         Returns:
#             List of selected items
#         """
#         if not items:
#             return []
        
#         selected = []
#         last_date = None
        
#         for item in items:
#             scene_date = item.datetime.date() if item.datetime else None
#             if not scene_date:
#                 continue
            
#             # Always include first scene
#             if last_date is None:
#                 selected.append(item)
#                 last_date = scene_date
#                 continue
            
#             # Check if enough days have passed
#             days_since_last = (scene_date - last_date).days
            
#             if days_since_last >= target_interval_days:
#                 selected.append(item)
#                 last_date = scene_date
        
#         return selected
 

#     # =========================================================================
#     # BAND DOWNLOAD & INDEX CALCULATION
#     # =========================================================================

#     def _download_bands(self, item, bbox_wgs84, target_resolution=10) -> Dict:
#         band_data = {}
#         failed_bands = []
        
#         # Log available bands in the scene
#         available_in_scene = [b for b in self.band_info if b in item.assets]
        
#         for band in self.band_info:
#             if band not in item.assets:
#                 failed_bands.append(f"{band}(not in assets)")
#                 continue
#             try:
#                 asset = planetary_computer.sign(item.assets[band])
#                 with rasterio.open(asset.href) as src:
#                     bbox_t = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84)
#                     window = from_bounds(*bbox_t, transform=src.transform)
#                     src_win = Window(0, 0, src.width, src.height)
#                     window  = window.intersection(src_win)

#                     if window.width <= 0 or window.height <= 0:
#                         failed_bands.append(f"{band}(no overlap)")
#                         continue

#                     window = Window(
#                         int(np.floor(window.col_off)),
#                         int(np.floor(window.row_off)),
#                         int(np.ceil(window.width)),
#                         int(np.ceil(window.height)),
#                     )
                    
#                     # Relax size requirements for 20m bands on small fields
#                     native_res = self.band_info.get(band, 20)
#                     min_window_size = 2 if native_res == 20 else 5
                    
#                     if window.width < min_window_size or window.height < min_window_size:
#                         failed_bands.append(f"{band}(too small: {window.width}x{window.height})")
#                         continue
#                     out_h, out_w = DataProcessor.resample_to_resolution(
#                         np.zeros((int(window.height), int(window.width))),
#                         native_res, target_resolution,
#                     )
#                     data = src.read(
#                         1, window=window,
#                         out_shape=(out_h, out_w),
#                         resampling=Resampling.bilinear,
#                     ).astype(np.float32)

#                     data = DataProcessor.clean_satellite_data(data, nodata_value=src.nodata)
                    
#                     # For very small fields with 20m bands, relax validation
#                     if native_res == 20 and (out_h * out_w) < 10:
#                         # Very small field - accept if we have ANY valid data
#                         min_ratio = 0.05  # 5% valid pixels for 20m bands on small fields
#                     else:
#                         min_ratio = PipelineConfig.MIN_VALID_PIXEL_RATIO
                    
#                     if DataProcessor.validate_band_data(data, min_valid_ratio=min_ratio):
#                         band_data[band] = data
#                     else:
#                         valid_pct = 100 * np.sum(~np.isnan(data)) / data.size if data.size > 0 else 0
#                         failed_bands.append(f"{band}(invalid data: {valid_pct:.1f}% valid, need {min_ratio*100:.0f}%)")
#             except Exception as e:
#                 failed_bands.append(f"{band}(error)")
#                 continue
        
#         # Log failed bands if any critical bands are missing
#         if failed_bands:
#             critical_failed = [b for b in failed_bands if any(cb in b for cb in ['B05', 'B06', 'B11', 'B12'])]
#             if critical_failed:
#                 logger.warning(f"       ⚠ Critical 20m bands failed: {', '.join(critical_failed[:3])}")
#                 logger.debug(f"       All failed: {', '.join(failed_bands)}")
        
#         return band_data

#     # def _calculate_indices(self, bands: Dict) -> Dict:
#     #     indices = {}
#     #     if 'B08' in bands and 'B04' in bands:
#     #         nir, red = DataProcessor.align_arrays([bands['B08'], bands['B04']])
#     #         ndvi = DataProcessor.calculate_ndvi(nir, red)
#     #         indices['NDVI_mean'] = float(np.nanmean(ndvi))
#     #         indices['NDVI_std']  = float(np.nanstd(ndvi))
#     #         indices['NDVI_p90']  = float(np.nanpercentile(ndvi, 90))

#     #     if all(b in bands for b in ['B08', 'B04', 'B02']):
#     #         nir, red, blue = DataProcessor.align_arrays(
#     #             [bands['B08'], bands['B04'], bands['B02']]
#     #         )
#     #         evi = DataProcessor.calculate_evi(nir, red, blue)
#     #         indices['EVI_mean'] = float(np.nanmean(evi))

#     #     if 'B08' in bands and 'B11' in bands:
#     #         nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B11']])
#     #         ndmi = DataProcessor.calculate_ndmi(nir, swir)
#     #         indices['NDMI_mean'] = float(np.nanmean(ndmi))

#     #     return indices

#     def _calculate_indices(self, bands: Dict) -> Dict:
#         """
#         Calculate vegetation indices from bands.
        
#         Adds:
#         - NDVI (Normalized Difference Vegetation Index)
#         - EVI (Enhanced Vegetation Index)
#         - NDMI (Normalized Difference Moisture Index) - FIXED
#         - PSRI (Plant Senescence Reflectance Index) - NEW
#         - NDRE (Normalized Difference Red Edge) - NEW
#         - NDWI (Normalized Difference Water Index) - NEW
#         """
#         indices = {}
#         eps = 1e-10  # Small epsilon to avoid division by zero
        
#         # NDVI: (NIR - Red) / (NIR + Red)
#         if 'B08' in bands and 'B04' in bands:
#             nir, red = DataProcessor.align_arrays([bands['B08'], bands['B04']])
#             ndvi = DataProcessor.calculate_ndvi(nir, red)
#             indices['NDVI_mean'] = float(np.nanmean(ndvi))
#             indices['NDVI_std'] = float(np.nanstd(ndvi))
#             indices['NDVI_p90'] = float(np.nanpercentile(ndvi, 90))
#             indices['NDVI_median'] = float(np.nanmedian(ndvi))
#         else:
#             indices['NDVI_mean'] = 0.0
#             indices['NDVI_std'] = 0.0
#             indices['NDVI_p90'] = 0.0
#             indices['NDVI_median'] = 0.0
 
#         # EVI: 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)
#         if all(b in bands for b in ['B08', 'B04', 'B02']):
#             nir, red, blue = DataProcessor.align_arrays(
#                 [bands['B08'], bands['B04'], bands['B02']]
#             )
#             evi = DataProcessor.calculate_evi(nir, red, blue)
#             indices['EVI_mean'] = float(np.nanmean(evi))
#         else:
#             indices['EVI_mean'] = 0.0
 
#         # NDMI: (NIR - SWIR1) / (NIR + SWIR1) - FIXED
#         # SWIR is B11 (SWIR-1, 1610nm) or B12 (SWIR-2, 2190nm)
#         if 'B08' in bands and 'B11' in bands:
#             nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B11']])
#             # Ensure valid data
#             valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
#             if np.sum(valid_mask) > 0:
#                 ndmi = DataProcessor.calculate_ndmi(nir, swir)
#                 indices['NDMI_mean'] = float(np.nanmean(ndmi))
#             else:
#                 indices['NDMI_mean'] = 0.0
#                 logger.debug("NDMI: B11 available but all pixels invalid")
#         elif 'B08' in bands and 'B12' in bands:
#             # Fallback to B12 if B11 not available
#             nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B12']])
#             valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
#             if np.sum(valid_mask) > 0:
#                 ndmi = DataProcessor.calculate_ndmi(nir, swir)
#                 indices['NDMI_mean'] = float(np.nanmean(ndmi))
#                 logger.debug(f"NDMI: Using B12 fallback, value={indices['NDMI_mean']:.3f}")
#             else:
#                 indices['NDMI_mean'] = 0.0
#                 logger.debug("NDMI: B12 fallback but all pixels invalid")
#         else:
#             indices['NDMI_mean'] = 0.0
#             available = [b for b in ['B08', 'B11', 'B12'] if b in bands]
#             logger.debug(f"NDMI: Cannot calculate, available bands={available}")
 
#         # PSRI: (Red - Blue) / Red Edge 2
#         # Formula: (B04 - B02) / B06
#         if all(b in bands for b in ['B04', 'B02', 'B06']):
#             red, blue, re2 = DataProcessor.align_arrays(
#                 [bands['B04'], bands['B02'], bands['B06']]
#             )
#             valid_mask = (~np.isnan(red)) & (~np.isnan(blue)) & (~np.isnan(re2)) & (re2 > 0)
#             if np.sum(valid_mask) > 0:
#                 psri = (red - blue) / (re2 + eps)
#                 # Clip to reasonable range
#                 psri = np.clip(psri, -1, 1)
#                 indices['PSRI_mean'] = float(np.nanmean(psri))
#             else:
#                 indices['PSRI_mean'] = 0.0
#         else:
#             indices['PSRI_mean'] = 0.0
 
#         # NDRE: (NIR - Red Edge 1) / (NIR + Red Edge 1)
#         # Formula: (B08 - B05) / (B08 + B05)
#         if all(b in bands for b in ['B08', 'B05']):
#             nir, re1 = DataProcessor.align_arrays([bands['B08'], bands['B05']])
#             valid_mask = (~np.isnan(nir)) & (~np.isnan(re1)) & (nir > 0) & (re1 > 0)
#             if np.sum(valid_mask) > 0:
#                 ndre = (nir - re1) / (nir + re1 + eps)
#                 indices['NDRE_mean'] = float(np.nanmean(ndre))
#             else:
#                 indices['NDRE_mean'] = 0.0
#         else:
#             indices['NDRE_mean'] = 0.0
 
#         # NDWI: (Green - NIR) / (Green + NIR)
#         # Formula: (B03 - B08) / (B03 + B08)
#         if all(b in bands for b in ['B03', 'B08']):
#             green, nir = DataProcessor.align_arrays([bands['B03'], bands['B08']])
#             valid_mask = (~np.isnan(green)) & (~np.isnan(nir)) & (green > 0) & (nir > 0)
#             if np.sum(valid_mask) > 0:
#                 ndwi = (green - nir) / (green + nir + eps)
#                 indices['NDWI_mean'] = float(np.nanmean(ndwi))
#             else:
#                 indices['NDWI_mean'] = 0.0
#         else:
#             indices['NDWI_mean'] = 0.0
 
#         return indices
 

#     # =========================================================================
#     # HELPERS
#     # =========================================================================

#     @staticmethod
#     def _window_mean_ndvi(scenes: List[Dict], position: str) -> float:
#         """Return mean NDVI of the first or last 3 scenes (start / end window)."""
#         if not scenes:
#             return 0.0
#         n = min(3, len(scenes))
#         subset = scenes[:n] if position == 'start' else scenes[-n:]
#         vals = [s['indices'].get('NDVI_mean', 0.0) for s in subset]
#         return float(np.mean(vals)) if vals else 0.0

#     @staticmethod
#     def _empty_season(season, year, start_date, end_date, reason='') -> Dict:
#         return {
#             'season': season, 'year': year,
#             'start_date': start_date, 'end_date': end_date,
#             'scenes': [], 'n_scenes': 0,
#             'ndvi_start': 0.0, 'ndvi_end': 0.0, 'ndvi_peak': 0.0,
#             'warning': reason,
#         }

#     def _log_temporal_gaps(self, scenes: List[Dict]):
#         if len(scenes) < 2:
#             return
#         dates = [datetime.strptime(s['date'], '%Y-%m-%d') for s in scenes]
#         gaps  = [(dates[i+1] - dates[i]).days for i in range(len(dates)-1)]
#         if gaps:
#             avg_gap = sum(gaps) / len(gaps)
#             max_gap = max(gaps)
#             logger.info(f"  Temporal gaps: avg={avg_gap:.1f}d  max={max_gap}d  "
#                         f"{'⚠ GAP>21d' if max_gap > PipelineConfig.MAX_GAP_DAYS else 'OK'}")


"""
Satellite Data Collector (continuous 3-year grid)
=================================================
- Window start: agricultural anchors **15 June** (Kharif) / **15 October** (Rabi), ~3 years back.
- Fixed ``CONTINUOUS_SCENE_INTERVAL_DAYS`` bins; lowest-cloud Sentinel-2 L2A per bin.
- Empty bins → placeholders (NaN indices); Stage 4 imputes.
- STAC cloud cap: looser Jun–Oct (Kharif + monsoon tail), stricter Nov–May (Rabi).
"""

import numpy as np
from datetime import datetime, timedelta, date, timezone
from typing import Any, Dict, List, Tuple, Optional
import logging
import gc
import time
import os
import json
import base64
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import pystac_client
    import planetary_computer
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    SATELLITE_AVAILABLE = True
except ImportError:
    SATELLITE_AVAILABLE = False
    print("WARNING: Satellite libraries not available")

try:
    import ee
    GEE_AVAILABLE = True
except ImportError:
    ee = None
    GEE_AVAILABLE = False

# Fixed: Proper indentation and added placeholder import
try:
    from config import REGIONAL_CONFIG
    REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False

from config import PipelineConfig
from utils.geometry_utils import GeometryUtils
from utils.data_processing import DataProcessor

logger = logging.getLogger(__name__)


class SatelliteDataCollector:
    """
    Sentinel-2 continuous pipeline: ~3-year window from season anchors (15 Jun / 15 Oct),
    fixed N-day bins, one STAC pick per bin (or NaN placeholder). Downstream imputes gaps.
    """

    def __init__(self, verbose: bool = True):
        if not SATELLITE_AVAILABLE and not GEE_AVAILABLE:
            raise ImportError(
                "Install either STAC deps (pystac-client planetary-computer rasterio) "
                "or GEE deps (earthengine-api)."
            )

        self.provider = os.environ.get("SATELLITE_PROVIDER", "gee").strip().lower()
        self.use_gee = self.provider == "gee" and GEE_AVAILABLE
        self.gee_ready = False

        self.stac_url = PipelineConfig.STAC_API_URL
        self.catalog = None
        if not self.use_gee and SATELLITE_AVAILABLE:
            self.catalog = pystac_client.Client.open(self.stac_url)

        self.verbose = verbose
        self.band_info = {
            band: info['resolution']
            for band, info in PipelineConfig.SENTINEL2_BANDS.items()
        }
        # Cloud thresholds - season-adaptive
        self.max_cloud_kharif = 80  # Monsoon - more lenient
        self.max_cloud_rabi = 60    # Dry season - stricter
        self.max_cloud_continuous = 70  # Continuous mode - balanced

        # GDAL emits TIFF tile noise at INFO during remote COG partial reads; keep our logs readable.
        logging.getLogger("rasterio._env").setLevel(logging.WARNING)
        logging.getLogger("rasterio").setLevel(logging.WARNING)

        if self.use_gee:
            self.gee_ready = self._initialize_gee()
            if not self.gee_ready:
                logger.warning("GEE requested but initialization failed; falling back to STAC.")
                self.use_gee = False

        # If we started with GEE, `self.catalog` was never opened; open STAC after GEE fallback.
        if not self.use_gee and self.catalog is None and SATELLITE_AVAILABLE:
            self.catalog = pystac_client.Client.open(self.stac_url)

        if not self.use_gee and self.catalog is None:
            raise ImportError("No satellite backend available. Install STAC or GEE dependencies.")

        logger.info("SatelliteDataCollector initialized (provider=%s)", "gee" if self.use_gee else "stac")

    # =========================================================================
    # NEW DEFAULT METHOD: Continuous 3-Year Collection
    # =========================================================================

    def _initialize_gee(self) -> bool:
        if not GEE_AVAILABLE:
            logger.error("earthengine-api is not installed.")
            return False
        try:
            project = os.environ.get("GEE_PROJECT", "").strip() or None
            key_path = os.environ.get("GEE_SA_KEY_PATH", "").strip()
            key_payload: Optional[Dict[str, Any]] = None

            # Render / dashboards often paste the full JSON into GEE_SA_KEY_PATH by mistake.
            # That is not a filesystem path; open() raises OSError (e.g. Errno 36 name too long).
            if key_path and key_path.lstrip().startswith("{"):
                try:
                    key_payload = json.loads(key_path)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "GEE_SA_KEY_PATH looks like JSON but is not valid JSON. "
                        "Use GEE_SERVICE_ACCOUNT_JSON or GEE_SERVICE_ACCOUNT_B64 for inline JSON, "
                        "or set GEE_SA_KEY_PATH to a real path to a .json key file."
                    ) from exc
                _fd, _tmp = tempfile.mkstemp(prefix="gee_sa_", suffix=".json", text=True)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    json.dump(key_payload, _fh)
                key_path = _tmp
                logger.info(
                    "GEE: GEE_SA_KEY_PATH was inline JSON; using a temporary credentials file"
                )

            b64 = os.environ.get("GEE_SERVICE_ACCOUNT_B64", "").strip()
            raw_json = os.environ.get("GEE_SERVICE_ACCOUNT_JSON", "").strip()
            if b64:
                raw_json = base64.b64decode(b64).decode("utf-8")
            if raw_json:
                try:
                    key_payload = json.loads(raw_json)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "GEE_SERVICE_ACCOUNT_JSON (or GEE_SERVICE_ACCOUNT_B64 decoded) is not valid JSON"
                    ) from exc
                fd, tmp_path = tempfile.mkstemp(prefix="gee_sa_", suffix=".json", text=True)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(key_payload, fh)
                key_path = tmp_path
                logger.info("GEE: using credentials from GEE_SERVICE_ACCOUNT_JSON / GEE_SERVICE_ACCOUNT_B64")

            if key_path:
                service_account = os.environ.get("GEE_SERVICE_ACCOUNT", "").strip() or None
                if not service_account:
                    if key_payload is None:
                        with open(key_path, "r", encoding="utf-8") as fh:
                            key_payload = json.load(fh)
                    service_account = (key_payload.get("client_email") or "").strip()
                if not service_account:
                    raise ValueError("GEE service account email missing. Set GEE_SERVICE_ACCOUNT.")
                credentials = ee.ServiceAccountCredentials(service_account, key_path)
                ee.Initialize(credentials=credentials, project=project)
            else:
                ee.Initialize(project=project)
            logger.info("GEE initialized successfully.")
            return True
        except Exception as e:
            logger.error("Failed to initialize GEE: %s", e)
            return False

    def collect_historical_data(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        field_area_ha: Optional[float] = None,
        geometry: Optional[object] = None,
    ) -> Dict:
        """
        Collect ~3 years of Sentinel-2 as a fixed-interval series (see PipelineConfig
        CONTINUOUS_SCENE_INTERVAL_DAYS). Season-aligned start; missing bins are NaN.
        """
        return self._collect_continuous_approach(
            latitude, longitude, field_area_ha, geometry
        )

    def _collect_continuous_approach(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        field_area_ha: Optional[float] = None,
        geometry: Optional[object] = None,
    ) -> Dict:
        """
        NEW: Continuous 3-year data collection with parallel downloading.
        This is now the DEFAULT and recommended approach.
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"CONTINUOUS DATA COLLECTION - 3 YEARS")
        logger.info(f"{'='*70}")

        # ── Determine bounding box ─────────────────────────────────────────
        if geometry is not None:
            bbox = GeometryUtils.calculate_bbox_from_geometry(geometry)
            centroid_lat, centroid_lon = GeometryUtils.calculate_centroid_from_geometry(geometry)
            field_area = GeometryUtils.calculate_area_from_geometry(geometry)
            logger.info(f"Input: geometry polygon | Centroid ({centroid_lat:.4f}, {centroid_lon:.4f})")
        elif latitude is not None and longitude is not None:
            field_area_ha = field_area_ha or 1.0
            bbox = GeometryUtils.calculate_bbox_from_point(latitude, longitude, field_area_ha)
            centroid_lat, centroid_lon = latitude, longitude
            field_area = field_area_ha
            logger.info(f"Input: point ({latitude:.4f}, {longitude:.4f}) | Area {field_area_ha:.2f} ha")
        else:
            raise ValueError("Must provide (latitude, longitude) or geometry")

        if not GeometryUtils.validate_bbox(bbox):
            raise ValueError(f"Invalid bounding box: {bbox}")

        # ── Rolling window: season anchors (15 Jun / 15 Oct), ~3 years to today ──
        today = date.today()
        snapped_start = self._snap_to_season_start(today, lookback_years=3)
        start_date = snapped_start.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')

        total_days_approx = (today - snapped_start).days
        logger.info(
            "Window %s → %s (~%d d); anchor=%s (Kharif=Jun15, Rabi=Oct15)",
            start_date,
            end_date,
            total_days_approx,
            start_date,
        )
        logger.info("BBox: %s", [round(v, 5) for v in bbox])

        if self.use_gee and self.gee_ready:
            return self._collect_continuous_gee(
                bbox=bbox,
                centroid_lat=centroid_lat,
                centroid_lon=centroid_lon,
                field_area=field_area,
                snapped_start=snapped_start,
                today=today,
                start_date=start_date,
                end_date=end_date,
            )

        # ── Search for all scenes in 3-year period ─────────────────────────
        split_years = bool(getattr(PipelineConfig, "SATELLITE_STAC_SPLIT_BY_YEAR", True))
        logger.info(
            "\nSearching for satellite scenes..."
            + (" (parallel by calendar year)" if split_years else "")
        )
        if split_years:
            candidates = self._search_scenes_multi_year_parallel(bbox, start_date, end_date)
        else:
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
            mid = date.fromordinal((d0.toordinal() + d1.toordinal()) // 2)
            candidates = self._search_scenes_continuous(
                bbox, start_date, end_date, cloud_lt=self._cloud_lt_for_calendar_day(mid)
            )
        logger.info(f"Found {len(candidates)} candidate scenes (before same-day dedupe)")
        candidates = self._deduplicate_scenes_by_day(candidates)

        if not candidates:
            logger.warning("No satellite data available for the period")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        interval = max(1, int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)))
        slots = self._build_interval_slots(candidates, snapped_start, today, interval)
        n_bins = len(slots)
        n_pick = sum(1 for _, it in slots if it is not None)
        n_miss = n_bins - n_pick
        logger.info(
            "%d-day bins: total=%d  STAC pick=%d  empty=%d  (unique-day candidates=%d)",
            interval,
            n_bins,
            n_pick,
            n_miss,
            len(candidates),
        )

        to_fetch: List[Tuple[Any, str]] = [
            (it, b0.strftime("%Y-%m-%d")) for b0, it in slots if it is not None
        ]
        if not to_fetch:
            logger.warning("No bins contained STAC items; widen cloud or check AOI")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        logger.info(
            "Reading %d COG scene(s) (one stream at a time; grid date = bin start)...",
            len(to_fetch),
        )
        by_anchor = self._download_anchor_scene_pairs(to_fetch, bbox)

        processed_scenes: List[Dict] = []
        for b0, it in slots:
            ds = b0.strftime("%Y-%m-%d")
            if it is None:
                processed_scenes.append(self._missing_scene_placeholder(b0))
            elif ds in by_anchor:
                processed_scenes.append(by_anchor[ds])
            else:
                processed_scenes.append(self._missing_scene_placeholder(b0))

        n_ok = sum(1 for s in processed_scenes if not s.get("missing"))
        logger.info("Grid merge: slots=%d  downloaded_ok=%d  placeholders=%d", len(slots), n_ok, len(slots) - n_ok)
        if n_ok == 0:
            logger.error("No scenes produced valid indices")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        # ── Extract time-series data ───────────────────────────────────────
        dates = [s['date'] for s in processed_scenes]
        ndvi_values = [s['indices'].get('NDVI_mean', np.nan) for s in processed_scenes]
        evi_values = [s['indices'].get('EVI_mean', np.nan) for s in processed_scenes]
        ndmi_values = [s['indices'].get('NDMI_mean', np.nan) for s in processed_scenes]
        psri_values = [s['indices'].get('PSRI_mean', np.nan) for s in processed_scenes]
        ndre_values = [s['indices'].get('NDRE_mean', np.nan) for s in processed_scenes]
        ndwi_values = [s['indices'].get('NDWI_mean', np.nan) for s in processed_scenes]

        # ── Summary stats ──────────────────────────────────────────────────
        valid_ndvi = [v for v in ndvi_values if not np.isnan(v)]
        if valid_ndvi:
            logger.info(f"\nNDVI statistics:")
            logger.info(f"  Range: {min(valid_ndvi):.3f} - {max(valid_ndvi):.3f}")
            logger.info(f"  Mean: {np.mean(valid_ndvi):.3f}")
            logger.info(f"  Scenes with valid NDVI: {len(valid_ndvi)}/{len(ndvi_values)}")

        total_days = (today - snapped_start).days

        logger.info("%s", "=" * 70)
        logger.info("CONTINUOUS COLLECTION COMPLETE")
        logger.info("  Grid slots: %d  |  interval: %d d  |  valid NDVI: %d", len(processed_scenes), interval, len(valid_ndvi))
        logger.info("  Span: %s → %s  (%d d)", dates[0], dates[-1], total_days)
        logger.info("%s", "=" * 70)

        return {
            'mode': 'continuous',
            'location': {'latitude': centroid_lat, 'longitude': centroid_lon},
            'bbox': bbox,
            'field_area_ha': field_area,
            'continuous_data': {
                'scenes': processed_scenes,
                'dates': dates,
                'ndvi_values': ndvi_values,
                'evi_values': evi_values,
                'ndmi_values': ndmi_values,
                'psri_values': psri_values,
                'ndre_values': ndre_values,
                'ndwi_values': ndwi_values,
                'start_date': start_date,
                'end_date': end_date,
                'total_days': total_days,
                'interval_days': interval,
                'scene_count': len(processed_scenes),
                'valid_observations': n_ok,
                'missing_observations': len(processed_scenes) - n_ok,
            },
            'collection_date': datetime.now().isoformat(),
            'summary': {
                'total_scenes': len(processed_scenes),
                'valid_ndvi_scenes': len(valid_ndvi),
                'missing_slots': len(processed_scenes) - n_ok,
                'interval_days': interval,
                'date_range': f"{dates[0]} to {dates[-1]}",
                'collection_mode': 'continuous_3year_grid',
            },
        }

    # =========================================================================
    # SEASON-ALIGNED DATE SNAPPING
    # =========================================================================

    @staticmethod
    def _snap_to_season_start(reference_date: date, lookback_years: int = 3) -> date:
        """
        Earliest date for the rolling window: latest agricultural anchor on or before
        ``reference_date - lookback_years`` (approximate calendar span).

        Anchors (Indian cropping calendar):
          Kharif: 15 June
          Rabi:   15 October
        """
        SEASON_STARTS = [
            (6, 15),   # Kharif
            (10, 15),  # Rabi
        ]

        # Build a list of all season-start dates from (lookback_years+1) years
        # back to today, then find the one that is at least lookback_years back
        # and most recent.
        cutoff = reference_date - timedelta(days=lookback_years * 365)

        candidates = []
        for year_offset in range(lookback_years + 2):
            yr = reference_date.year - year_offset
            for month, day in SEASON_STARTS:
                try:
                    candidate = date(yr, month, day)
                    if candidate <= cutoff:
                        candidates.append(candidate)
                except ValueError:
                    pass  # shouldn't happen with fixed month/day

        # Return the most recent candidate that is still at/before the cutoff
        # (to guarantee at least lookback_years of data)
        if candidates:
            return max(candidates)

        # Ultimate fallback: plain arithmetic
        return cutoff

    # =========================================================================
    # GEE continuous collection
    # =========================================================================

    def _collect_continuous_gee(
        self,
        bbox: List[float],
        centroid_lat: float,
        centroid_lon: float,
        field_area: float,
        snapped_start: date,
        today: date,
        start_date: str,
        end_date: str,
    ) -> Dict:
        interval = max(1, int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)))
        aoi = ee.Geometry.Rectangle(bbox, proj="EPSG:4326", geodesic=False)
        logger.info("Searching for satellite scenes from GEE...")
        scenes_by_day = self._fetch_gee_scene_stats(aoi, start_date, end_date)

        if not scenes_by_day:
            logger.warning("No GEE scenes available for the period")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        slots = self._build_date_slots_with_stats(scenes_by_day, snapped_start, today, interval)
        processed_scenes: List[Dict] = []
        for b0, stats in slots:
            if stats is None:
                processed_scenes.append(self._missing_scene_placeholder(b0))
                continue
            ndvi_m = stats.get("NDVI_mean", np.nan)
            if not np.isfinite(self._safe_float(ndvi_m)):
                processed_scenes.append(self._missing_scene_placeholder(b0))
                continue
            processed_scenes.append(
                {
                    "date": b0.strftime("%Y-%m-%d"),
                    "missing": False,
                    "cloud_cover": stats.get("cloud_cover"),
                    "indices": {
                        "NDVI_mean": stats.get("NDVI_mean", np.nan),
                        "NDVI_std": stats.get("NDVI_std", np.nan),
                        "NDVI_p90": stats.get("NDVI_p90", np.nan),
                        "EVI_mean": stats.get("EVI_mean", np.nan),
                        "NDMI_mean": stats.get("NDMI_mean", np.nan),
                        "PSRI_mean": stats.get("PSRI_mean", np.nan),
                        "NDRE_mean": stats.get("NDRE_mean", np.nan),
                        "NDWI_mean": stats.get("NDWI_mean", np.nan),
                    },
                    "bands_available": ["B02", "B03", "B04", "B05", "B06", "B08", "B11"],
                    "acquisition_date": stats.get("date"),
                }
            )

        n_ok = sum(1 for s in processed_scenes if not s.get("missing"))
        if n_ok == 0:
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        dates = [s["date"] for s in processed_scenes]
        ndvi_values = [s["indices"].get("NDVI_mean", np.nan) for s in processed_scenes]
        evi_values = [s["indices"].get("EVI_mean", np.nan) for s in processed_scenes]
        ndmi_values = [s["indices"].get("NDMI_mean", np.nan) for s in processed_scenes]
        psri_values = [s["indices"].get("PSRI_mean", np.nan) for s in processed_scenes]
        ndre_values = [s["indices"].get("NDRE_mean", np.nan) for s in processed_scenes]
        ndwi_values = [s["indices"].get("NDWI_mean", np.nan) for s in processed_scenes]
        valid_ndvi = [v for v in ndvi_values if not np.isnan(v)]
        total_days = (today - snapped_start).days

        return {
            "mode": "continuous",
            "location": {"latitude": centroid_lat, "longitude": centroid_lon},
            "bbox": bbox,
            "field_area_ha": field_area,
            "continuous_data": {
                "scenes": processed_scenes,
                "dates": dates,
                "ndvi_values": ndvi_values,
                "evi_values": evi_values,
                "ndmi_values": ndmi_values,
                "psri_values": psri_values,
                "ndre_values": ndre_values,
                "ndwi_values": ndwi_values,
                "start_date": start_date,
                "end_date": end_date,
                "total_days": total_days,
                "interval_days": interval,
                "scene_count": len(processed_scenes),
                "valid_observations": n_ok,
                "missing_observations": len(processed_scenes) - n_ok,
            },
            "collection_date": datetime.now().isoformat(),
            "summary": {
                "total_scenes": len(processed_scenes),
                "valid_ndvi_scenes": len(valid_ndvi),
                "missing_slots": len(processed_scenes) - n_ok,
                "interval_days": interval,
                "date_range": f"{dates[0]} to {dates[-1]}",
                "collection_mode": "continuous_3year_grid_gee",
            },
        }

    @staticmethod
    def _safe_float(v: Any) -> float:
        try:
            if v is None:
                return np.nan
            return float(v)
        except (TypeError, ValueError):
            return np.nan

    def _gee_scene_preferred(self, prev: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Same calendar day: keep usable spectra over masked; then lower cloud %."""
        pn = np.isfinite(self._safe_float(prev.get("NDVI_mean")))
        nn = np.isfinite(self._safe_float(new.get("NDVI_mean")))
        if nn and not pn:
            return True
        if pn and not nn:
            return False
        pc, nc = self._safe_float(prev.get("cloud_cover")), self._safe_float(new.get("cloud_cover"))
        if np.isfinite(nc) and np.isfinite(pc):
            return nc < pc
        return bool(np.isfinite(nc))

    def _build_date_slots_with_stats(
        self,
        scenes_by_day: Dict[str, Dict[str, Any]],
        range_start: date,
        range_end: date,
        interval_days: int,
    ) -> List[Tuple[date, Optional[Dict[str, Any]]]]:
        slots: List[Tuple[date, Optional[Dict[str, Any]]]] = []
        cur = range_start
        while cur <= range_end:
            bin_end = cur + timedelta(days=interval_days)
            candidates: List[Tuple[str, Dict[str, Any]]] = []
            for ds, s in scenes_by_day.items():
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                if cur <= d <= range_end and d < bin_end:
                    candidates.append((ds, s))
            if candidates:
                def _usable_ndvi(s: Dict[str, Any]) -> bool:
                    return np.isfinite(self._safe_float(s.get("NDVI_mean")))

                usable = [c for c in candidates if _usable_ndvi(c[1])]
                pool = usable if usable else candidates
                best = min(pool, key=lambda x: self._safe_float(x[1].get("cloud_cover")))
                slots.append((cur, best[1]))
            else:
                slots.append((cur, None))
            cur = bin_end
        return slots

    def _fetch_gee_scene_stats(
        self,
        aoi,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Dict[str, Any]]:
        if not self.gee_ready:
            return {}

        cloud_cap = float(self.max_cloud_continuous)

        def _scene_to_feature(img):
            # Simplified processing to avoid concurrent aggregations
            qa = img.select("QA60")
            cloud_mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            scl = img.select("SCL")
            scl_mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
            masked = img.updateMask(cloud_mask).updateMask(scl_mask).divide(10000)

            # Only compute essential indices to reduce aggregations
            ndvi = masked.normalizedDifference(["B8", "B4"]).rename("NDVI")
            evi = masked.expression(
                "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
                {"NIR": masked.select("B8"), "RED": masked.select("B4"), "BLUE": masked.select("B2")},
            ).rename("EVI")
            ndmi = masked.normalizedDifference(["B8", "B11"]).rename("NDMI")

            # Simplified: only mean values, no complex aggregations
            indices = ee.Image.cat([ndvi, evi, ndmi])
            
            # Use single reduceRegion call to minimize concurrent operations
            stats = indices.reduceRegion(
                reducer=ee.Reducer.mean().combine(
                    ee.Reducer.stdDev(), None, True
                ).combine(
                    ee.Reducer.percentile([90]), None, True
                ),
                geometry=aoi,
                scale=10,
                maxPixels=1e8,
                bestEffort=True
            )

            props = ee.Dictionary(stats).combine(
                ee.Dictionary({
                    "date": img.date().format("YYYY-MM-dd"),
                    "cloud_cover": img.get("CLOUDY_PIXEL_PERCENTAGE"),
                })
            )
            return ee.Feature(None, props)

        try:
            features = []
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
            
            # Sequential year-by-year processing to avoid GEE rate limits
            years_to_process = list(range(d0.year, d1.year + 1))
            logger.info(f"Processing years sequentially: {years_to_process}")
            
            for year in years_to_process:
                year_start = max(d0, date(year, 1, 1))
                year_end = min(d1, date(year, 12, 31))
                
                logger.info(f"Processing year {year}: {year_start} to {year_end}")
                
                # Retry logic for each year's data with much longer delays
                max_retries = 2  # Reduce retries to avoid overwhelming GEE
                for attempt in range(max_retries):
                    try:
                        # Add substantial delay between years to avoid rate limiting
                        if year > d0.year and attempt == 0:
                            inter_year_delay = 10.0  # Much longer delay between years
                            logger.info(f"Waiting {inter_year_delay}s between year {year-1} and {year}")
                            time.sleep(inter_year_delay)
                        
                        if attempt > 0:
                            delay = 15.0 * (2 ** attempt)  # Much longer delays for retries
                            logger.warning(f"Year {year} GEE retry {attempt + 1}/{max_retries}, waiting {delay:.1f}s")
                            time.sleep(delay)
                        
                        # Simplify: limit collection to fewer scenes to reduce aggregations
                        year_coll = (
                            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                            .filterBounds(aoi)
                            .filterDate(year_start.strftime("%Y-%m-%d"), year_end.strftime("%Y-%m-%d"))
                            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_cap))
                            .limit(50)  # Limit scenes to reduce processing load
                        )
                        
                        year_features = year_coll.map(_scene_to_feature).getInfo().get("features", [])
                        features.extend(year_features)
                        logger.info(f"Year {year}: extracted {len(year_features)} scenes")
                        break  # Success, move to next year
                        
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "concurrent aggregations" in error_msg or "too many concurrent" in error_msg:
                            if attempt < max_retries - 1:
                                logger.warning(f"Year {year} concurrent aggregation error (attempt {attempt + 1}/{max_retries}): {e}")
                                continue
                            else:
                                logger.error(f"Year {year} failed after {max_retries} attempts: {e}")
                                raise e
                        elif "rate limit" in error_msg or "quota" in error_msg:
                            if attempt < max_retries - 1:
                                delay = 10.0 * (2 ** attempt)  # Aggressive delay for rate limits
                                logger.warning(f"Year {year} rate limit (attempt {attempt + 1}/{max_retries}), waiting {delay:.1f}s")
                                time.sleep(delay)
                                continue
                            else:
                                logger.error(f"Year {year} rate limit exceeded after {max_retries} attempts: {e}")
                                raise e
                        else:
                            # Non-rate-limit error, re-raise immediately
                            logger.error(f"Year {year} scene extraction failed: {e}")
                            raise e
                
        except Exception as e:
            logger.error("GEE scene extraction failed: %s", e)
            return {}

        by_day: Dict[str, Dict[str, Any]] = {}
        logger.info("GEE: merged %d raw features into daily stats", len(features))

        for f in features:
            p = f.get("properties", {})
            ds = p.get("date")
            if not ds:
                continue
            
            ndvi = self._safe_float(p.get("NDVI_mean"))
            evi = self._safe_float(p.get("EVI_mean"))
            ndmi = self._safe_float(p.get("NDMI_mean"))
            # Fully masked / invalid reduceRegion — do not store as a real observation.
            if not (np.isfinite(ndvi) or np.isfinite(evi)):
                continue
            
            if len(by_day) < 5 and self.verbose:
                logger.debug("GEE scene %s: NDVI=%s EVI=%s NDMI=%s", ds, ndvi, evi, ndmi)
            
            scene_stats = {
                "date": ds,
                "cloud_cover": self._safe_float(p.get("cloud_cover")),
                "NDVI_mean": ndvi,
                "NDVI_std": self._safe_float(p.get("NDVI_stdDev")),
                "NDVI_p90": self._safe_float(p.get("NDVI_p90")),
                "EVI_mean": evi,
                "NDMI_mean": ndmi,
                "PSRI_mean": self._safe_float(p.get("PSRI_mean")),
                "NDRE_mean": self._safe_float(p.get("NDRE_mean")),
                "NDWI_mean": self._safe_float(p.get("NDWI_mean")),
            }
            prev = by_day.get(ds)
            if prev is None or self._gee_scene_preferred(prev, scene_stats):
                by_day[ds] = scene_stats
        return by_day

    # =========================================================================
    # STAC search (year-split) + per-anchor downloads: _download_anchor_scene_pairs
    # =========================================================================

    @staticmethod
    def _yearly_date_segments(d0: date, d1: date) -> List[Tuple[date, date]]:
        """Split [d0, d1] into contiguous calendar-year segments."""
        if d0 > d1:
            return []
        out: List[Tuple[date, date]] = []
        cur = d0
        while cur <= d1:
            y = cur.year
            end_of_year = date(y, 12, 31)
            seg_end = min(end_of_year, d1)
            out.append((cur, seg_end))
            if seg_end >= d1:
                break
            cur = date(y + 1, 1, 1)
        return out

    def _search_scenes_multi_year_parallel(
        self,
        bbox: List[float],
        start_date: str,
        end_date: str,
    ) -> List:
        """
        Run one STAC query per calendar-year segment. Queries run in parallel
        (bounded by SATELLITE_STAC_YEAR_SEARCH_WORKERS) so long lookbacks do not
        rely on a single huge search response.
        """
        d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
        d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
        windows = self._yearly_date_segments(d0, d1)
        if not windows:
            return []

        logger.info(
            "STAC year segments: "
            + ", ".join(f"{a.isoformat()}→{b.isoformat()}" for a, b in windows)
        )

        def fetch_segment(seg: Tuple[date, date]) -> List:
            a, b = seg
            mid = date.fromordinal((a.toordinal() + b.toordinal()) // 2)
            cloud = self._cloud_lt_for_calendar_day(mid)
            return self._search_scenes_continuous(
                bbox,
                a.strftime("%Y-%m-%d"),
                b.strftime("%Y-%m-%d"),
                cloud_lt=cloud,
            )

        if len(windows) == 1:
            return fetch_segment(windows[0])

        nw = int(getattr(PipelineConfig, "SATELLITE_STAC_YEAR_SEARCH_WORKERS", 4))
        nw = max(1, min(nw, len(windows)))

        merged: List = []
        if nw == 1:
            for w in windows:
                merged.extend(fetch_segment(w))
        else:
            with ThreadPoolExecutor(max_workers=nw) as ex:
                futures = [ex.submit(fetch_segment, w) for w in windows]
                for fut in as_completed(futures):
                    try:
                        merged.extend(fut.result())
                    except Exception as e:
                        logger.warning(f"Parallel STAC segment failed: {e}")

        merged.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
        return merged

    def _cloud_lt_for_calendar_day(self, d: date) -> float:
        """STAC eo:cloud_cover upper bound: looser Jun–Oct (Kharif + monsoon tail)."""
        if d.month in (6, 7, 8, 9, 10):
            return float(self.max_cloud_kharif)
        return float(self.max_cloud_rabi)

    def _download_anchor_scene_pairs(
        self,
        pairs: List[Tuple[Any, str]],
        bbox: List[float],
    ) -> Dict[str, Dict]:
        """
        Download/process each (STAC item, grid_anchor_date) preserving anchor keys.
        One scene at a time; batches log lines by anchor year.
        """
        from collections import defaultdict

        if not pairs:
            return {}
        by_y: Dict[int, List[Tuple[Any, str]]] = defaultdict(list)
        for item, anchor_ds in pairs:
            by_y[int(anchor_ds[:4])].append((item, anchor_ds))
        out: Dict[str, Dict] = {}
        pause = float(getattr(PipelineConfig, "SATELLITE_INTER_YEAR_PAUSE_SEC", 0) or 0)
        years = sorted(by_y.keys())
        for yi, y in enumerate(years):
            batch = by_y[y]
            logger.info("  Year %s: reading %d scene(s)...", y, len(batch))
            for j, (item, anchor_ds) in enumerate(batch):
                r = self._download_and_process_single_scene(
                    item, bbox, j, len(batch), output_date=anchor_ds
                )
                if r is not None:
                    out[anchor_ds] = r
            if pause > 0 and yi < len(years) - 1:
                time.sleep(pause)
        return out

    def _download_and_process_single_scene(
        self,
        item,
        bbox: List[float],
        scene_num: int,
        total_scenes: int,
        output_date: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Download band windows for one STAC item and compute index statistics.
        Used from sequential loops and optionally from a per-scene thread pool.
        """
        try:
            acq = item.datetime.strftime('%Y-%m-%d') if item.datetime else None
            scene_date = output_date or acq
            if not scene_date:
                return None

            cloud_pct = float(item.properties.get('eo:cloud_cover', 0))
            
            # Download bands
            band_data = self._download_bands(item, bbox)
            
            if len(band_data) < 2:  # Need at least NIR and Red for NDVI
                return None
            
            # Calculate indices
            indices = self._calculate_indices(band_data)
            
            # Validate - must have NDVI at minimum
            if np.isnan(indices.get('NDVI_mean', np.nan)):
                return None
            
            rec: Dict = {
                'date': scene_date,
                'missing': False,
                'cloud_cover': cloud_pct,
                'indices': indices,
                'bands_available': list(band_data.keys()),
            }
            if output_date and acq and acq != output_date:
                rec['acquisition_date'] = acq
            return rec
            
        except Exception as e:
            logger.debug(f"Scene {scene_num} failed: {str(e)[:40]}")
            return None

    # =========================================================================
    # SCENE SEARCH & SELECTION (With Proper Cloud Thresholds)
    # =========================================================================

    def _search_scenes_continuous(
        self,
        bbox: List[float],
        start_date: str,
        end_date: str,
        cloud_lt: Optional[float] = None,
    ) -> List:
        """
        Search for scenes; cloud cap from ``cloud_lt`` or balanced default.
        Retries transient STAC/API failures (see SATELLITE_STAC_SEARCH_RETRIES).
        """
        retries = max(0, int(getattr(PipelineConfig, "SATELLITE_STAC_SEARCH_RETRIES", 0)))
        delay = float(getattr(PipelineConfig, "SATELLITE_STAC_SEARCH_RETRY_DELAY_SEC", 2.0))
        cap = float(
            cloud_lt if cloud_lt is not None else self.max_cloud_continuous
        )

        for attempt in range(retries + 1):
            try:
                search = self.catalog.search(
                    collections=[PipelineConfig.SENTINEL2_COLLECTION],
                    bbox=bbox,
                    datetime=f"{start_date}/{end_date}",
                    query={"eo:cloud_cover": {"lt": cap}},
                    limit=500,  # page size; items() paginates across all matches
                )
                items = list(search.items())
                items.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
                return items
            except Exception as e:
                if attempt < retries:
                    logger.warning(
                        f"STAC search retry {attempt + 1}/{retries} "
                        f"({start_date}..{end_date}): {e}"
                    )
                    time.sleep(delay * (attempt + 1))
                    continue
                logger.error(f"Scene search failed ({start_date}..{end_date}): {e}")
                return []
        return []

    def _deduplicate_scenes_by_day(self, items: List) -> List:
        """
        Keep only one best scene per calendar day (lowest cloud cover).
        STAC often returns overlapping tiles for the same date/field; de-duplicating
        by day preserves temporal continuity while avoiding redundant downloads.
        """
        if not items:
            return []

        best_by_day: Dict[str, object] = {}
        for it in items:
            dt = getattr(it, "datetime", None)
            if not dt:
                continue
            day = dt.strftime("%Y-%m-%d")
            cloud = float(it.properties.get("eo:cloud_cover", 100))
            prev = best_by_day.get(day)
            if prev is None:
                best_by_day[day] = it
            else:
                prev_cloud = float(prev.properties.get("eo:cloud_cover", 100))
                if cloud < prev_cloud:
                    best_by_day[day] = it

        deduped = list(best_by_day.values())
        deduped.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
        dropped = max(0, len(items) - len(deduped))
        if dropped > 0:
            logger.info(
                f"Deduplicated same-day scenes: {len(items)} -> {len(deduped)} "
                f"(dropped {dropped})"
            )
        return deduped

    @staticmethod
    def _observation_date(item) -> Optional[date]:
        """Calendar date (UTC) of the STAC item observation."""
        dt = getattr(item, "datetime", None)
        if not dt:
            return None
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()

    @staticmethod
    def _nan_index_bundle() -> Dict:
        """Placeholder index dict aligned with _calculate_indices keys."""
        return {
            'NDVI_mean': np.nan,
            'NDVI_std': np.nan,
            'NDVI_p90': np.nan,
            'EVI_mean': np.nan,
            'NDMI_mean': np.nan,
            'PSRI_mean': np.nan,
            'NDRE_mean': np.nan,
            'NDWI_mean': np.nan,
        }

    @staticmethod
    def _missing_scene_placeholder(bin_start: date) -> Dict:
        """No usable STAC scene in this bin — NaNs for downstream imputation."""
        return {
            'date': bin_start.strftime('%Y-%m-%d'),
            'missing': True,
            'cloud_cover': None,
            'indices': SatelliteDataCollector._nan_index_bundle(),
            'bands_available': [],
        }

    def _build_interval_slots(
        self,
        candidates: List,
        range_start: date,
        range_end: date,
        interval_days: int,
    ) -> List[Tuple[date, Optional[Any]]]:
        """
        Partition [range_start, range_end] into half-open [t, t+interval_days) bins.
        Returns (bin_start, best_item_or_None) for every bin through range_end.
        """
        if interval_days < 1 or range_start > range_end:
            return []

        slots: List[Tuple[date, Optional[Any]]] = []
        cur = range_start
        while cur <= range_end:
            bin_end = cur + timedelta(days=interval_days)
            pool = [
                c for c in candidates
                if (d := self._observation_date(c)) is not None
                and cur <= d <= range_end
                and d < bin_end
            ]
            best: Optional[Any] = None
            if pool:
                best = min(
                    pool,
                    key=lambda c: float(c.properties.get('eo:cloud_cover', 100)),
                )
            slots.append((cur, best))
            cur = bin_end
        return slots

    # =========================================================================
    # BAND DOWNLOAD & INDEX CALCULATION (FIXED)
    # =========================================================================

    def _fetch_one_band_array(
        self,
        item,
        bbox_wgs84: List[float],
        band: str,
        target_resolution: int = 10,
    ) -> Optional[np.ndarray]:
        """
        Read and validate a single band COG window. Returns None if missing or invalid.
        Each band is a separate HTTPS COG — reads can run in parallel (see _download_bands).
        """
        if band not in item.assets:
            return None
        retries = max(0, int(getattr(PipelineConfig, "SATELLITE_BAND_READ_RETRIES", 0)))
        delay = float(getattr(PipelineConfig, "SATELLITE_BAND_RETRY_DELAY_SEC", 0.5))

        for attempt in range(retries + 1):
            try:
                asset = planetary_computer.sign(item.assets[band])
                with rasterio.open(asset.href) as src:
                    bbox_t = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84)
                    window = from_bounds(*bbox_t, transform=src.transform)
                    src_win = Window(0, 0, src.width, src.height)
                    window = window.intersection(src_win)

                    if window.width <= 0 or window.height <= 0:
                        return None

                    window = Window(
                        int(np.floor(window.col_off)),
                        int(np.floor(window.row_off)),
                        int(np.ceil(window.width)),
                        int(np.ceil(window.height)),
                    )

                    native_res = self.band_info.get(band, 20)
                    min_window_size = 2 if native_res == 20 else 5
                    if window.width < min_window_size or window.height < min_window_size:
                        return None

                    out_h, out_w = DataProcessor.resample_to_resolution(
                        np.zeros((int(window.height), int(window.width))),
                        native_res, target_resolution,
                    )
                    data = src.read(
                        1,
                        window=window,
                        out_shape=(out_h, out_w),
                        resampling=Resampling.bilinear,
                    ).astype(np.float32)
                    data = DataProcessor.clean_satellite_data(data, nodata_value=src.nodata)

                    if native_res == 20 and (out_h * out_w) < 10:
                        min_ratio = 0.05
                    else:
                        min_ratio = PipelineConfig.MIN_VALID_PIXEL_RATIO
                    if DataProcessor.validate_band_data(data, min_valid_ratio=min_ratio):
                        return data
                    return None
            except Exception as e:
                if attempt < retries:
                    time.sleep(delay * (attempt + 1))
                    continue
                logger.warning(
                    f"Band read failed after {attempt + 1} attempt(s); "
                    f"skipping band={band} item={getattr(item, 'id', 'unknown')}: {e}"
                )
                return None
        return None

    def _download_bands(self, item, bbox_wgs84, target_resolution=10) -> Dict:
        """
        Load all bands needed for vegetation indices.

        Sentinel-2 STAC items expose one COG URL per band; there is no single combined asset.
        Indices are computed once per scene after all bands are in memory — not one download
        pass per index.
        """
        band_data: Dict[str, np.ndarray] = {}
        # All bands needed for indices: fetch B11 in parallel with reflectance (separate COG).
        parallel_bands = ("B02", "B03", "B04", "B05", "B06", "B08", "B11")
        use_parallel = bool(getattr(PipelineConfig, "SATELLITE_BAND_DOWNLOAD_PARALLEL", False))
        nw = int(getattr(PipelineConfig, "SATELLITE_BAND_DOWNLOAD_MAX_WORKERS", 6))
        nw = max(1, nw)

        if use_parallel and nw > 1:
            workers = min(nw, len(parallel_bands))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(
                        self._fetch_one_band_array, item, bbox_wgs84, b, target_resolution
                    ): b
                    for b in parallel_bands
                }
                for fut in as_completed(futures):
                    b = futures[fut]
                    try:
                        arr = fut.result()
                        if arr is not None:
                            band_data[b] = arr
                    except Exception as e:
                        logger.debug(f"Band {b} parallel fetch failed: {e}")
        else:
            for b in parallel_bands:
                arr = self._fetch_one_band_array(item, bbox_wgs84, b, target_resolution)
                if arr is not None:
                    band_data[b] = arr

        # NDMI fallback when B11 missing or invalid for this window.
        if "B11" not in band_data:
            swir12 = self._fetch_one_band_array(item, bbox_wgs84, "B12", target_resolution)
            if swir12 is not None:
                band_data["B12"] = swir12

        return band_data

    def _calculate_indices(self, bands: Dict) -> Dict:
        """
        Calculate vegetation indices from bands.
        FIXED: Returns NaN instead of 0.0 for missing data.
        """
        indices = {}
        eps = 1e-10
        
        # NDVI: (NIR - Red) / (NIR + Red)
        if 'B08' in bands and 'B04' in bands:
            nir, red = DataProcessor.align_arrays([bands['B08'], bands['B04']])
            ndvi = DataProcessor.calculate_ndvi(nir, red)
            indices['NDVI_mean'] = float(np.nanmean(ndvi))
            indices['NDVI_std'] = float(np.nanstd(ndvi))
            indices['NDVI_p90'] = float(np.nanpercentile(ndvi, 90))
        else:
            indices['NDVI_mean'] = np.nan
            indices['NDVI_std'] = np.nan
            indices['NDVI_p90'] = np.nan
 
        # EVI: 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)
        if all(b in bands for b in ['B08', 'B04', 'B02']):
            nir, red, blue = DataProcessor.align_arrays(
                [bands['B08'], bands['B04'], bands['B02']]
            )
            evi = DataProcessor.calculate_evi(nir, red, blue)
            indices['EVI_mean'] = float(np.nanmean(evi))
        else:
            indices['EVI_mean'] = np.nan
 
        # NDMI: (NIR - SWIR) / (NIR + SWIR)
        if 'B08' in bands and 'B11' in bands:
            nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B11']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
            if np.sum(valid_mask) > 0:
                ndmi = DataProcessor.calculate_ndmi(nir, swir)
                indices['NDMI_mean'] = float(np.nanmean(ndmi))
            else:
                indices['NDMI_mean'] = np.nan
        elif 'B08' in bands and 'B12' in bands:
            nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B12']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
            if np.sum(valid_mask) > 0:
                ndmi = DataProcessor.calculate_ndmi(nir, swir)
                indices['NDMI_mean'] = float(np.nanmean(ndmi))
            else:
                indices['NDMI_mean'] = np.nan
        else:
            indices['NDMI_mean'] = np.nan
 
        # PSRI: (Red - Blue) / Red Edge 2
        if all(b in bands for b in ['B04', 'B02', 'B06']):
            red, blue, re2 = DataProcessor.align_arrays(
                [bands['B04'], bands['B02'], bands['B06']]
            )
            valid_mask = (~np.isnan(red)) & (~np.isnan(blue)) & (~np.isnan(re2)) & (re2 > 0)
            if np.sum(valid_mask) > 0:
                psri = (red - blue) / (re2 + eps)
                psri = np.clip(psri, -1, 1)
                indices['PSRI_mean'] = float(np.nanmean(psri))
            else:
                indices['PSRI_mean'] = np.nan
        else:
            indices['PSRI_mean'] = np.nan
 
        # NDRE: (NIR - Red Edge 1) / (NIR + Red Edge 1)
        if all(b in bands for b in ['B08', 'B05']):
            nir, re1 = DataProcessor.align_arrays([bands['B08'], bands['B05']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(re1)) & (nir > 0) & (re1 > 0)
            if np.sum(valid_mask) > 0:
                ndre = (nir - re1) / (nir + re1 + eps)
                indices['NDRE_mean'] = float(np.nanmean(ndre))
            else:
                indices['NDRE_mean'] = np.nan
        else:
            indices['NDRE_mean'] = np.nan
 
        # NDWI: (Green - NIR) / (Green + NIR)
        if all(b in bands for b in ['B03', 'B08']):
            green, nir = DataProcessor.align_arrays([bands['B03'], bands['B08']])
            valid_mask = (~np.isnan(green)) & (~np.isnan(nir)) & (green > 0) & (nir > 0)
            if np.sum(valid_mask) > 0:
                ndwi = (green - nir) / (green + nir + eps)
                indices['NDWI_mean'] = float(np.nanmean(ndwi))
            else:
                indices['NDWI_mean'] = np.nan
        else:
            indices['NDWI_mean'] = np.nan
 
        return indices

    # =========================================================================
    # FALLBACK: Old Seasonal Approach (Backward Compatibility)
    # =========================================================================

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _empty_continuous_result(self, lat: float, lon: float, area: float) -> Dict:
        """Return empty result structure for continuous mode."""
        return {
            'mode': 'continuous',
            'location': {'latitude': lat, 'longitude': lon},
            'field_area_ha': area,
            'continuous_data': {
                'scenes': [],
                'dates': [],
                'ndvi_values': [],
                'evi_values': [],
                'ndmi_values': [],
                'psri_values': [],
                'ndre_values': [],
                'ndwi_values': [],
                'scene_count': 0,
            },
            'collection_date': datetime.now().isoformat(),
            'summary': {
                'total_scenes': 0,
                'collection_mode': 'continuous_3year',
                'error': 'No data available',
            },
        }