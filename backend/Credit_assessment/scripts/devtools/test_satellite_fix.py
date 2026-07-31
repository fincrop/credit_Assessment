#!/usr/bin/env python3
"""
Test script to verify satellite data collection is working correctly
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from data_acquisition.satellite_collector import SatelliteDataCollector
from mongodb_helper import MongoDBHelper

def test_satellite_data():
    """Test satellite data collection for a single farmer"""
    
    # Test farmer coordinates (from recent runs)
    latitude = 29.1375
    longitude = 77.2206
    
    print("Testing satellite data collection...")
    print(f"Location: {latitude}, {longitude}")
    
    try:
        # Initialize satellite collector
        collector = SatelliteDataCollector()
        
        # Collect just 1 year of data to test
        satellite_data = collector.collect_historical_data(
            latitude=latitude,
            longitude=longitude,
            field_area_ha=1.0,
            start_date="2023-01-01",
            end_date="2023-12-31"
        )
        
        print(f"Collected {len(satellite_data)} data points")
        
        # Check first few entries
        for i, (date, stats) in enumerate(list(satellite_data.items())[:5]):
            print(f"Entry {i}: {date}")
            print(f"  NDVI_mean: {stats.get('NDVI_mean', 'N/A')}")
            print(f"  EVI_mean: {stats.get('EVI_mean', 'N/A')}")
            print(f"  NDMI_mean: {stats.get('NDMI_mean', 'N/A')}")
            print()
        
        # Check if we have valid data (not NaN)
        valid_ndvi = [v for v in satellite_data.values() if v.get('NDVI_mean') and not str(v.get('NDVI_mean')).lower() == 'nan']
        print(f"Valid NDVI entries: {len(valid_ndvi)}/{len(satellite_data)}")
        
        if len(valid_ndvi) > 0:
            print("✅ SUCCESS: Satellite data collection is working correctly!")
        else:
            print("❌ ISSUE: Still getting NaN values in satellite data")
            
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_satellite_data()
