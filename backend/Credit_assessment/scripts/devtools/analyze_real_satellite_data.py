#!/usr/bin/env python3
"""
Analyze real satellite data to understand why only 1 cycle is detected
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from datetime import datetime, timedelta
from data_acquisition.satellite_collector import SatelliteDataCollector

def analyze_real_satellite_data():
    """Analyze actual satellite data from recent farmer processing"""
    
    print("=== Analyzing Real Satellite Data ===")
    
    # Use the same coordinates from recent farmer
    latitude = 29.0932
    longitude = 77.3568
    
    try:
        # Initialize satellite collector
        collector = SatelliteDataCollector()
        
        # Collect 1 year of data to analyze patterns
        satellite_data = collector.collect_historical_data(
            latitude=latitude,
            longitude=longitude,
            field_area_ha=1.0,
            start_date="2023-01-01",
            end_date="2023-12-31"
        )
        
        print(f"Collected {len(satellite_data)} data points for 2023")
        
        # Extract and analyze the data
        dates = sorted(satellite_data.keys())
        ndvi_values = []
        evi_values = []
        ndmi_values = []
        
        for date in dates:
            stats = satellite_data[date]
            ndvi_values.append(stats.get('NDVI_mean', np.nan))
            evi_values.append(stats.get('EVI_mean', np.nan))
            ndmi_values.append(stats.get('NDMI_mean', np.nan))
        
        ndvi_arr = np.array(ndvi_values)
        evi_arr = np.array(evi_values)
        ndmi_arr = np.array(ndmi_values)
        
        # Remove NaN values for analysis
        valid_mask = ~(np.isnan(ndvi_arr) | np.isnan(evi_arr) | np.isnan(ndmi_arr))
        valid_ndvi = ndvi_arr[valid_mask]
        valid_evi = evi_arr[valid_mask]
        valid_ndmi = ndmi_arr[valid_mask]
        valid_dates = [dates[i] for i in range(len(dates)) if valid_mask[i]]
        
        print(f"\nValid data points: {len(valid_ndvi)}/{len(dates)}")
        print(f"NDVI range: {np.min(valid_ndvi):.3f} - {np.max(valid_ndvi):.3f}")
        print(f"EVI range: {np.min(valid_evi):.3f} - {np.max(valid_evi):.3f}")
        print(f"NDMI range: {np.min(valid_ndmi):.3f} - {np.max(valid_ndmi):.3f}")
        
        # Look for obvious seasonal patterns
        print(f"\n=== Looking for Seasonal Patterns ===")
        
        # Find all significant rises (potential greenups)
        rises = []
        falls = []
        
        for i in range(1, len(valid_ndvi)):
            ndvi_change = valid_ndvi[i] - valid_ndvi[i-1]
            
            # Significant rise (>0.1)
            if ndvi_change > 0.1:
                rises.append({
                    'index': i,
                    'date': valid_dates[i],
                    'from_ndvi': valid_ndvi[i-1],
                    'to_ndvi': valid_ndvi[i],
                    'change': ndvi_change
                })
                print(f"RISE: {valid_dates[i]} NDVI {valid_ndvi[i-1]:.3f} -> {valid_ndvi[i]:.3f} (+{ndvi_change:.3f})")
            
            # Significant fall (>0.1)
            elif ndvi_change < -0.1:
                falls.append({
                    'index': i,
                    'date': valid_dates[i],
                    'from_ndvi': valid_ndvi[i-1],
                    'to_ndvi': valid_ndvi[i],
                    'change': ndvi_change
                })
                print(f"FALL: {valid_dates[i]} NDVI {valid_ndvi[i-1]:.3f} -> {valid_ndvi[i]:.3f} ({ndvi_change:.3f})")
        
        print(f"\nFound {len(rises)} significant rises and {len(falls)} significant falls")
        
        # Try to pair rises with falls to form cycles
        print(f"\n=== Attempting to Form Cycles ===")
        cycles = []
        
        for rise in rises:
            # Look for a fall after this rise
            for fall in falls:
                if fall['index'] > rise['index']:
                    # Check if this forms a reasonable cycle
                    cycle_duration = (fall['date'] - rise['date']).days
                    if 60 <= cycle_duration <= 200:  # Reasonable crop duration
                        cycles.append({
                            'sowing': rise['date'],
                            'harvest': fall['date'],
                            'duration_days': cycle_duration,
                            'peak_ndvi': np.max(valid_ndvi[rise['index']:fall['index']+1]),
                            'start_ndvi': rise['from_ndvi'],
                            'end_ndvi': fall['to_ndvi']
                        })
                        print(f"CYCLE: Sowing {rise['date']} -> Harvest {fall['date']} ({cycle_duration} days)")
                        break
        
        print(f"\nDetected {len(cycles)} potential cycles")
        for i, cycle in enumerate(cycles):
            print(f"  Cycle {i+1}: {cycle['sowing']} to {cycle['harvest']} ({cycle['duration_days']} days)")
            print(f"    NDVI: {cycle['start_ndvi']:.3f} -> {cycle['peak_ndvi']:.3f} -> {cycle['end_ndvi']:.3f}")
        
        # Calculate expected cycles for 3 years
        expected_cycles = len(cycles) * 3  # Assuming similar pattern each year
        print(f"\nExpected cycles over 3 years: {expected_cycles}")
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    analyze_real_satellite_data()
