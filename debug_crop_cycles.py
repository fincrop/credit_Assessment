#!/usr/bin/env python3
"""
Debug script to test CVI calculation and greenup detection
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import numpy as np
from datetime import datetime, timedelta
from crop_analysis.crop_cycle_detector import CropCycleDetector

def create_test_satellite_data():
    """Create realistic test satellite data that should trigger greenup detection"""
    
    # Create 100 days of data with clear crop cycle pattern
    dates = []
    ndvi_values = []
    evi_values = []
    ndmi_values = []
    
    base_date = datetime(2023, 1, 1)
    
    for i in range(100):
        date = base_date + timedelta(days=i)
        dates.append(date)
        
        # Simulate a clear crop cycle:
        # Days 0-20: Bare soil (low NDVI/EVI/NDMI)
        # Days 20-40: Sowing and establishment (rapid rise)
        # Days 40-60: Peak growth (high values)
        # Days 60-80: Harvest decline (rapid fall)
        # Days 80-100: Post-harvest (low values)
        
        if i < 20:
            # Bare soil
            ndvi = 0.15 + np.random.normal(0, 0.02)
            evi = 0.10 + np.random.normal(0, 0.02)
            ndmi = -0.05 + np.random.normal(0, 0.02)
        elif i < 40:
            # Greenup period - rapid rise
            progress = (i - 20) / 20
            ndvi = 0.15 + 0.45 * progress + np.random.normal(0, 0.02)
            evi = 0.10 + 0.35 * progress + np.random.normal(0, 0.02)
            ndmi = -0.05 + 0.25 * progress + np.random.normal(0, 0.02)
        elif i < 60:
            # Peak growth
            ndvi = 0.60 + np.random.normal(0, 0.03)
            evi = 0.45 + np.random.normal(0, 0.03)
            ndmi = 0.20 + np.random.normal(0, 0.02)
        elif i < 80:
            # Harvest decline
            progress = (i - 60) / 20
            ndvi = 0.60 - 0.40 * progress + np.random.normal(0, 0.02)
            evi = 0.45 - 0.30 * progress + np.random.normal(0, 0.02)
            ndmi = 0.20 - 0.20 * progress + np.random.normal(0, 0.02)
        else:
            # Post-harvest bare soil
            ndvi = 0.20 + np.random.normal(0, 0.02)
            evi = 0.15 + np.random.normal(0, 0.02)
            ndmi = 0.00 + np.random.normal(0, 0.02)
        
        # Clip to valid ranges
        ndvi = np.clip(ndvi, -0.1, 1.0)
        evi = np.clip(evi, 0.0, 1.0)
        ndmi = np.clip(ndmi, -0.6, 0.8)
        
        ndvi_values.append(ndvi)
        evi_values.append(evi)
        ndmi_values.append(ndmi)
    
    return dates, np.array(ndvi_values), np.array(evi_values), np.array(ndmi_values)

def test_crop_cycle_detection():
    """Test crop cycle detection with synthetic data"""
    
    print("=== Testing Crop Cycle Detection ===")
    
    # Create test data
    dates, ndvi, evi, ndmi = create_test_satellite_data()
    
    print(f"Created {len(dates)} days of test data")
    print(f"NDVI range: {np.min(ndvi):.3f} - {np.max(ndvi):.3f}")
    print(f"EVI range: {np.min(evi):.3f} - {np.max(evi):.3f}")
    print(f"NDMI range: {np.min(ndmi):.3f} - {np.max(ndmi):.3f}")
    
    # Initialize crop cycle detector
    detector = CropCycleDetector()
    
    # Test CVI calculation
    print("\n=== Testing CVI Calculation ===")
    cvi = detector._build_cvi(ndvi, evi, ndmi)
    print(f"CVI range: {np.min(cvi):.3f} - {np.max(cvi):.3f}")
    
    # Test greenup detection
    print("\n=== Testing Greenup Detection ===")
    greenup_indices = detector._detect_greenup_events_with_window(cvi, window=5)
    print(f"Greenup indices found: {greenup_indices}")
    
    if greenup_indices:
        print(f"Expected greenup around day 20-30, found at indices: {greenup_indices}")
        for idx in greenup_indices:
            if idx < len(dates):
                print(f"  Greenup at day {idx}: {dates[idx]}")
    else:
        print("❌ NO GREENUPS DETECTED - This indicates a problem!")
    
    # Test with multiple windows
    print("\n=== Testing Multi-Window Greenup ===")
    multi_greenups = detector._union_multi_window_greenups(cvi)
    print(f"Multi-window greenups: {multi_greenups}")

if __name__ == "__main__":
    test_crop_cycle_detection()
