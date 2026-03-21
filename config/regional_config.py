"""
Regional Configuration
======================
Region-specific thresholds for different agricultural zones in India.

This addresses the critical issue where one-size-fits-all thresholds fail for:
- Semi-arid Gujarat cotton (NDVI 0.25-0.5) vs Dense Punjab wheat (NDVI 0.7-0.9)
- Normal Gujarat summer heat (45°C) vs Damaging heat in other regions
"""

from typing import Dict, Tuple, List
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class RegionalConfig:
    """Region-specific agricultural parameters for India"""
    
    # ========================================================================
    # REGIONAL BOUNDARIES
    # ========================================================================
    
    REGIONS = {
        'INDO_GANGETIC_PLAIN': {
            'bounds': (24.0, 31.0, 75.0, 88.0),  # (lat_min, lat_max, lon_min, lon_max)
            'description': 'Punjab, Haryana, UP, Bihar - High productivity wheat/rice belt',
            'states': ['Punjab', 'Haryana', 'Uttar Pradesh', 'Bihar']
        },
        'WESTERN_INDIA': {
            'bounds': (20.0, 26.0, 68.0, 76.0),  # Gujarat, Rajasthan
            'description': 'Semi-arid region - Cotton, groundnut, mustard, pearl millet',
            'states': ['Gujarat', 'Rajasthan']
        },
        'CENTRAL_INDIA': {
            'bounds': (18.0, 26.0, 74.0, 82.0),  # Maharashtra, MP
            'description': 'Deccan plateau - Soybean, cotton, pulses',
            'states': ['Maharashtra', 'Madhya Pradesh']
        },
        'SOUTHERN_INDIA': {
            'bounds': (8.0, 18.0, 74.0, 80.0),  # Karnataka, TN, AP
            'description': 'Rice, millets, sugarcane, cotton',
            'states': ['Karnataka', 'Tamil Nadu', 'Andhra Pradesh', 'Telangana']
        },
        'EASTERN_INDIA': {
            'bounds': (20.0, 28.0, 82.0, 92.0),  # Odisha, WB, Jharkhand
            'description': 'Rice-dominant, high rainfall zone',
            'states': ['Odisha', 'West Bengal', 'Jharkhand', 'Chhattisgarh']
        }
    }
    
    # ========================================================================
    # REGION-SPECIFIC CROP DETECTION THRESHOLDS
    # ========================================================================
    
    CROP_DETECTION_THRESHOLDS = {
        'INDO_GANGETIC_PLAIN': {
            'min_ndvi': 0.20,           # Dense irrigated crops
            'peak_expected': 0.60,      # Wheat/Rice achieve high NDVI
            'min_peak_for_crop': 0.50,  # Peak must exceed this
            'description': 'High-density irrigated wheat and rice',
            'typical_crops': ['Wheat', 'Rice', 'Sugarcane']
        },
        'WESTERN_INDIA': {
            'min_ndvi': 0.25,           # ← CRITICAL FIX for your Gujarat farm!
            'peak_expected': 0.60,      # Cotton/groundnut moderate peaks
            'min_peak_for_crop': 0.30,  # Lower peak acceptable
            'description': 'Semi-arid, widely-spaced dryland crops',
            'typical_crops': ['Cotton', 'Groundnut', 'Mustard', 'Pearl Millet']
        },
        'CENTRAL_INDIA': {
            'min_ndvi': 0.25,           # Rainfed crops
            'peak_expected': 0.70,      # Soybean, cotton
            'min_peak_for_crop': 0.45,
            'description': 'Mixed rainfed agriculture',
            'typical_crops': ['Soybean', 'Cotton', 'Wheat', 'Gram']
        },
        'SOUTHERN_INDIA': {
            'min_ndvi': 0.25,           # Rice + dryland mix
            'peak_expected': 0.75,      # Irrigated rice high, millets moderate
            'min_peak_for_crop': 0.50,
            'description': 'Irrigated and rainfed crop mix',
            'typical_crops': ['Rice', 'Ragi', 'Cotton', 'Sugarcane']
        },
        'EASTERN_INDIA': {
            'min_ndvi': 0.20,           # Dense rice cultivation
            'peak_expected': 0.80,      # High rainfall supports dense growth
            'min_peak_for_crop': 0.55,
            'description': 'High rainfall, rice-dominant region',
            'typical_crops': ['Rice', 'Jute', 'Vegetables']
        },
        'DEFAULT': {
            'min_ndvi': 0.20,           # Conservative fallback
            'peak_expected': 0.70,
            'min_peak_for_crop': 0.45,
            'description': 'Fallback for unclassified regions',
            'typical_crops': ['Mixed crops']
        }
    }
    
    # ========================================================================
    # REGION-SPECIFIC WEATHER THRESHOLDS
    # ========================================================================
    
    WEATHER_THRESHOLDS = {
        'INDO_GANGETIC_PLAIN': {
            'heatwave_temp': 42,        # Plains get hot but crops irrigated
            'heatwave_min_days': 3,
            'cold_wave_temp': 5,        # Sensitive to winter cold
            'cold_wave_min_days': 3,
            'drought_days': 45,         # Irrigated, less drought-sensitive
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 120,
            'heavy_rain_3day_mm': 200
        },
        'WESTERN_INDIA': {
            'heatwave_temp': 45,        # ← CRITICAL FIX: Cotton tolerates 40-45°C!
            'heatwave_min_days': 5,     # ← Needs sustained extreme heat
            'cold_wave_temp': 8,        # Mild winters, rare cold damage
            'cold_wave_min_days': 3,
            'drought_days': 60,         # ← Drought-adapted crops (cotton, groundnut)
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 150,  # Rare heavy rain is truly extreme
            'heavy_rain_3day_mm': 250
        },
        'CENTRAL_INDIA': {
            'heatwave_temp': 43,
            'heatwave_min_days': 3,
            'cold_wave_temp': 10,
            'cold_wave_min_days': 3,
            'drought_days': 40,         # Rainfed crops moderately sensitive
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 100,
            'heavy_rain_3day_mm': 200
        },
        'SOUTHERN_INDIA': {
            'heatwave_temp': 40,        # Coastal moderation
            'heatwave_min_days': 3,
            'cold_wave_temp': 12,       # No cold waves in south
            'cold_wave_min_days': 5,
            'drought_days': 35,
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 100,
            'heavy_rain_3day_mm': 200
        },
        'EASTERN_INDIA': {
            'heatwave_temp': 40,
            'heatwave_min_days': 3,
            'cold_wave_temp': 8,
            'cold_wave_min_days': 3,
            'drought_days': 30,         # High rainfall, drought rare
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 150,  # Accustomed to heavy monsoon
            'heavy_rain_3day_mm': 250
        },
        'DEFAULT': {
            'heatwave_temp': 42,
            'heatwave_min_days': 3,
            'cold_wave_temp': 10,
            'cold_wave_min_days': 3,
            'drought_days': 40,
            'drought_daily_mm': 2,
            'heavy_rain_single_mm': 120,
            'heavy_rain_3day_mm': 200
        }
    }
    
    # ========================================================================
    # TEMPORAL COVERAGE PARAMETERS
    # ========================================================================
    
    TEMPORAL_COVERAGE = {
        'MIN_SCENES_PER_SEASON': 8,       # Minimum for valid analysis
        'TARGET_SCENES_PER_SEASON': 18,   # Target: ~1 per 10 days
        'MAX_SCENES_PER_SEASON': 24,      # Maximum to process
        'IDEAL_GAP_DAYS': 10,             # Ideal gap between observations
        'MAX_GAP_DAYS': 21                # Maximum acceptable gap
    }
    
    # ========================================================================
    # HELPER METHODS
    # ========================================================================
    
    @classmethod
    def get_region(cls, latitude: float, longitude: float) -> str:
        """
        Determine agricultural region from coordinates.
        
        Args:
            latitude: Field latitude
            longitude: Field longitude
            
        Returns:
            Region name (e.g., 'WESTERN_INDIA', 'DEFAULT')
        """
        for region_name, region_info in cls.REGIONS.items():
            lat_min, lat_max, lon_min, lon_max = region_info['bounds']
            
            if (lat_min <= latitude <= lat_max and 
                lon_min <= longitude <= lon_max):
                logger.info(f"✓ Location classified as: {region_name}")
                logger.info(f"  {region_info['description']}")
                return region_name
        
        logger.warning(f"Location ({latitude:.4f}, {longitude:.4f}) not in predefined regions - using DEFAULT")
        return 'DEFAULT'
    
    @classmethod
    def get_crop_threshold(cls, latitude: float, longitude: float) -> Dict:
        """
        Get region-appropriate NDVI threshold.
        
        Args:
            latitude: Field latitude
            longitude: Field longitude
            
        Returns:
            Dictionary with min_ndvi, peak_expected, etc.
        """
        region = cls.get_region(latitude, longitude)
        return cls.CROP_DETECTION_THRESHOLDS[region]
    
    @classmethod
    def get_weather_thresholds(cls, latitude: float, longitude: float) -> Dict:
        """
        Get region-appropriate weather thresholds.
        
        Args:
            latitude: Field latitude
            longitude: Field longitude
            
        Returns:
            Dictionary with weather threshold values
        """
        region = cls.get_region(latitude, longitude)
        return cls.WEATHER_THRESHOLDS[region]
    
    @classmethod
    def print_region_info(cls, latitude: float, longitude: float):
        """Print diagnostic information about the region."""
        region = cls.get_region(latitude, longitude)
        
        print(f"\n{'='*70}")
        print(f"REGIONAL CLASSIFICATION: {region}")
        print(f"{'='*70}")
        print(f"Location: ({latitude:.4f}°N, {longitude:.4f}°E)")
        
        region_data = cls.REGIONS.get(region, {})
        print(f"\nDescription: {region_data.get('description', 'N/A')}")
        
        if 'states' in region_data:
            print(f"States: {', '.join(region_data['states'])}")
        
        crop_thresh = cls.get_crop_threshold(latitude, longitude)
        print(f"\n--- CROP DETECTION THRESHOLDS ---")
        print(f"Minimum NDVI for crop: {crop_thresh['min_ndvi']:.2f}")
        print(f"Expected peak NDVI: {crop_thresh['peak_expected']:.2f}")
        print(f"Minimum peak required: {crop_thresh['min_peak_for_crop']:.2f}")
        print(f"Rationale: {crop_thresh['description']}")
        print(f"Typical crops: {', '.join(crop_thresh['typical_crops'])}")
        
        weather_thresh = cls.get_weather_thresholds(latitude, longitude)
        print(f"\n--- WEATHER THRESHOLDS ---")
        print(f"Heatwave: >{weather_thresh['heatwave_temp']}°C for {weather_thresh['heatwave_min_days']}+ days")
        print(f"Cold Wave: <{weather_thresh['cold_wave_temp']}°C for {weather_thresh['cold_wave_min_days']}+ days")
        print(f"Drought: {weather_thresh['drought_days']} consecutive days with <{weather_thresh['drought_daily_mm']}mm rain")
        print(f"Heavy Rain: >{weather_thresh['heavy_rain_single_mm']}mm in 1 day or {weather_thresh['heavy_rain_3day_mm']}mm in 3 days")
        print(f"{'='*70}\n")
    
    @classmethod
    def select_temporally_distributed_scenes(cls, scenes: List, target_scenes: int = 18) -> List:
        """
        Select scenes with good temporal distribution instead of just lowest cloud cover.
        
        This fixes the issue where all 12 scenes might be clustered in 2-3 weeks,
        leaving large gaps in the seasonal coverage.
        
        Args:
            scenes: List of STAC items already sorted by cloud cover
            target_scenes: Target number of scenes to return
            
        Returns:
            List of selected scenes with good temporal coverage
        """
        if len(scenes) <= target_scenes:
            return scenes
        
        # Get all dates
        dates = [item.datetime for item in scenes if item.datetime]
        
        if not dates:
            return scenes[:target_scenes]
        
        min_date = min(dates)
        max_date = max(dates)
        season_days = (max_date - min_date).days
        
        if season_days < 7:
            # Very short season, just return best by cloud cover
            return scenes[:target_scenes]
        
        # Create time bins (divide season into equal periods)
        n_bins = min(target_scenes, max(season_days // 7, target_scenes))
        bin_size_days = season_days / n_bins
        
        selected = []
        selected_dates = set()
        
        for i in range(n_bins):
            bin_start = min_date + timedelta(days=i * bin_size_days)
            bin_end = min_date + timedelta(days=(i + 1) * bin_size_days)
            
            # Find scenes in this time bin
            bin_scenes = [
                scene for scene in scenes 
                if scene.datetime and bin_start <= scene.datetime < bin_end
            ]
            
            if bin_scenes:
                # Pick scene with lowest cloud cover in this bin
                best = min(bin_scenes, key=lambda s: s.properties.get("eo:cloud_cover", 100))
                
                # Avoid exact duplicates (same datetime)
                if best.datetime not in selected_dates:
                    selected.append(best)
                    selected_dates.add(best.datetime)
        
        # If we didn't get enough scenes (sparse data), fill with best remaining
        if len(selected) < target_scenes:
            remaining = [s for s in scenes if s.datetime not in selected_dates]
            needed = target_scenes - len(selected)
            selected.extend(remaining[:needed])
        
        # Sort by date for cleaner processing
        selected.sort(key=lambda s: s.datetime if s.datetime else datetime.min)
        
        logger.info(f"  Selected {len(selected)} scenes with temporal distribution")
        
        if selected:
            date_gaps = []
            for i in range(len(selected) - 1):
                if selected[i].datetime and selected[i+1].datetime:
                    gap = (selected[i+1].datetime - selected[i].datetime).days
                    date_gaps.append(gap)
            
            if date_gaps:
                logger.info(f"  Date gaps: avg={sum(date_gaps)/len(date_gaps):.1f} days, max={max(date_gaps)} days")
        
        return selected


# # Example usage and testing
# if __name__ == "__main__":
#     print("\n" + "="*70)
#     print("REGIONAL CONFIGURATION TEST")
#     print("="*70)
    
#     # Test with your Gujarat farm
#     print("\n1. GUJARAT FARM (Your test case)")
#     gujarat_lat, gujarat_lon = 21.0546, 71.4212
#     RegionalConfig.print_region_info(gujarat_lat, gujarat_lon)
    
#     # Test with Punjab farm
#     print("\n2. PUNJAB FARM (Wheat belt)")
#     punjab_lat, punjab_lon = 30.7333, 76.7794
#     RegionalConfig.print_region_info(punjab_lat, punjab_lon)
    
#     # Test with Maharashtra farm
#     print("\n3. MAHARASHTRA FARM (Soybean belt)")
#     maha_lat, maha_lon = 20.5937, 78.9629
#     RegionalConfig.print_region_info(maha_lat, maha_lon)