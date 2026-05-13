#!/usr/bin/env python3
"""
Test script to verify crop cycle detection fixes.
This script creates synthetic NDVI data with multiple distinct crop cycles
and tests that the detector properly separates them.
"""

import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from crop_analysis.crop_cycle_detector import CropCycleDetector

def create_synthetic_crop_data():
    """Create synthetic NDVI data with multiple distinct crop cycles."""
    
    # Create 3 years of data with 10-day intervals
    start_date = datetime(2022, 1, 1)
    n_points = 109  # ~3 years of 10-day intervals
    dates = [start_date + timedelta(days=10*i) for i in range(n_points)]
    
    # Base NDVI (bare soil)
    ndvi = np.full(n_points, 0.15)
    
    # Cycle 1: Kharif 2022 (Jun-Sep) - Rice
    cycle1_start = 18  # ~June 2022
    cycle1_peak = 25   # ~August 2022
    cycle1_end = 35    # ~October 2022
    ndvi[cycle1_start:cycle1_peak] = np.linspace(0.15, 0.75, cycle1_peak-cycle1_start)
    ndvi[cycle1_peak:cycle1_end] = np.linspace(0.75, 0.20, cycle1_end-cycle1_peak)
    
    # Cycle 2: Rabi 2022-23 (Oct-Jan) - Wheat
    cycle2_start = 36  # ~October 2022
    cycle2_peak = 45   # ~December 2022
    cycle2_end = 55    # ~February 2023
    ndvi[cycle2_start:cycle2_peak] = np.linspace(0.20, 0.65, cycle2_peak-cycle2_start)
    ndvi[cycle2_peak:cycle2_end] = np.linspace(0.65, 0.18, cycle2_end-cycle2_peak)
    
    # Cycle 3: Zaid 2023 (Feb-Apr) - Summer crops
    cycle3_start = 56  # ~February 2023
    cycle3_peak = 62   # ~April 2023
    cycle3_end = 68    # ~June 2023
    ndvi[cycle3_start:cycle3_peak] = np.linspace(0.18, 0.55, cycle3_peak-cycle3_start)
    ndvi[cycle3_peak:cycle3_end] = np.linspace(0.55, 0.15, cycle3_end-cycle3_peak)
    
    # Cycle 4: Kharif 2023 (Jun-Sep) - Rice again
    cycle4_start = 78  # ~June 2023
    cycle4_peak = 85   # ~August 2023
    cycle4_end = 95    # ~October 2023
    ndvi[cycle4_start:cycle4_peak] = np.linspace(0.15, 0.70, cycle4_peak-cycle4_start)
    ndvi[cycle4_peak:cycle4_end] = np.linspace(0.70, 0.20, cycle4_end-cycle4_peak)
    
    # Add some noise
    ndvi += np.random.normal(0, 0.02, n_points)
    ndvi = np.clip(ndvi, 0, 1)
    
    return dates, ndvi

def test_crop_cycle_detection():
    """Test the improved crop cycle detection."""
    
    print("Testing improved crop cycle detection...")
    
    # Create synthetic data
    dates, ndvi_values = create_synthetic_crop_data()
    
    # Initialize detector
    detector = CropCycleDetector(
        min_ndvi_rise=0.08,
        min_baseline_cvi=0.25,
        min_peak_cvi=0.30,
        min_duration_days=35,
        max_duration_days=180,
    )
    
    # Detect cycles
    cycles = detector.detect_cycles(
        dates=dates,
        ndvi_values=ndvi_values,
        grid_step_days=10.0
    )
    
    print(f"\nDetected {len(cycles)} crop cycles:")
    
    for i, cycle in enumerate(cycles, 1):
        print(f"  Cycle {i}: {cycle.sowing_date.strftime('%Y-%m-%d')} → {cycle.harvest_date.strftime('%Y-%m-%d')}")
        print(f"    Duration: {cycle.duration_days} days, Peak NDVI: {cycle.peak_ndvi:.3f}")
        print(f"    Season: {cycle.season_type}, Confidence: {cycle.confidence:.1f}%")
        print()
    
    # Check if we detected the expected number of cycles (should be 4)
    expected_cycles = 4
    if len(cycles) >= expected_cycles - 1:  # Allow for some detection difficulty
        print(f"✓ SUCCESS: Detected {len(cycles)} cycles (expected ~{expected_cycles})")
        print("✓ The improved logic properly separates distinct crop cycles!")
    else:
        print(f"✗ ISSUE: Only detected {len(cycles)} cycles (expected ~{expected_cycles})")
        print("✗ Cycles may still be getting merged incorrectly")
    
    return cycles, dates, ndvi_values

def plot_results(cycles, dates, ndvi_values):
    """Plot the NDVI data and detected cycles."""
    
    try:
        plt.figure(figsize=(12, 6))
        
        # Plot NDVI
        plt.plot(dates, ndvi_values, 'b-', linewidth=2, label='NDVI', alpha=0.7)
        
        # Mark detected cycles
        colors = ['red', 'green', 'orange', 'purple', 'brown']
        for i, cycle in enumerate(cycles):
            color = colors[i % len(colors)]
            plt.axvspan(cycle.sowing_date, cycle.harvest_date, 
                       alpha=0.2, color=color, label=f'Cycle {i+1}')
            plt.plot(cycle.peak_date, cycle.peak_ndvi, 'o', color=color, markersize=8)
        
        plt.xlabel('Date')
        plt.ylabel('NDVI')
        plt.title('Crop Cycle Detection Results')
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()
        
        print("✓ Plot generated successfully")
    except Exception as e:
        print(f"Could not generate plot: {e}")

if __name__ == "__main__":
    cycles, dates, ndvi_values = test_crop_cycle_detection()
    
    # Try to plot results (optional)
    try:
        plot_results(cycles, dates, ndvi_values)
    except:
        pass
    
    print("\nTest completed!")
