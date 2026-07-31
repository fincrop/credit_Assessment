"""
Example Usage - Agricultural Credit Pipeline v4.0
=================================================

This script demonstrates all the new features in v4.0 and how to use them
with the organized project structure.

Run this after deployment to verify everything works correctly.
"""

import logging
import sys
from datetime import datetime, timedelta
import numpy as np

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def example_1_pipeline_init():
    """
    Example 1: Default pipeline (continuous satellite, cycles, advanced credit scorer).
    """
    print("\n" + "="*70)
    print("EXAMPLE 1: PIPELINE INITIALIZATION")
    print("="*70)

    from main import SatelliteBasedCreditPipeline

    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path='models/crop_classifier_model.joblib',
        ml_mode='hybrid',
        use_mongodb=False,
        verbose=True,
    )

    print("Pipeline initialized (continuous path, crop cycles, RiskIndexEngine).")
    return pipeline


def example_2_direct_assessment():
    """
    Example 2: Run assessment without MongoDB (lat/lon + area).
    """
    print("\n" + "="*70)
    print("EXAMPLE 2: DIRECT ASSESSMENT (no DB)")
    print("="*70)

    from main import SatelliteBasedCreditPipeline

    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path='models/crop_classifier_model.joblib',
        ml_mode='hybrid',
        use_mongodb=False,
        verbose=True,
    )

    result = pipeline.assess_farmer(
        farmer_id='example_farmer_001',
        latitude=19.0760,
        longitude=72.8777,
        field_area_ha=2.5,
        save_to_db=False,
    )

    print(f"\nRESULTS:")
    print(f"  Status: {result['status']}")
    if result['status'] == 'SUCCESS':
        print(f"  Credit Score: {result['credit_assessment']['credit_score']}/100")
        print(f"  Risk Category: {result['credit_assessment']['risk_category']}")
    print(f"  Processing Time: {result.get('processing_time_seconds', 0):.1f}s")

    return result


def example_3_crop_cycle_detection():
    """
    Example 3: Standalone crop cycle detection
    """
    print("\n" + "="*70)
    print("EXAMPLE 3: STANDALONE CROP CYCLE DETECTION")
    print("="*70)
    
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.land_utilization_analyzer import LandUtilizationAnalyzer
    
    # Create synthetic NDVI data (simulate 2 crops)
    print("\nGenerating synthetic NDVI data (2 crop cycles)...")
    
    start_date = datetime(2023, 1, 1)
    dates = [start_date + timedelta(weeks=i) for i in range(104)]  # 2 years
    
    # Simulate NDVI
    ndvi_values = []
    for i in range(len(dates)):
        base = 0.20 + np.random.normal(0, 0.03)
        
        # Crop 1: weeks 8-28 (Rice)
        if 8 <= i <= 28:
            growth = 0.55 * np.sin((i - 8) / 20 * np.pi)
            base += growth
        
        # Crop 2: weeks 40-58 (Wheat)
        if 40 <= i <= 58:
            growth = 0.48 * np.sin((i - 40) / 18 * np.pi)
            base += growth
        
        ndvi_values.append(np.clip(base, 0, 1))
    
    # Detect crop cycles
    detector = CropCycleDetector()
    cycles = detector.detect_cycles(dates, ndvi_values)
    
    print(f"\nDETECTED {len(cycles)} CROP CYCLES:")
    for i, cycle in enumerate(cycles, 1):
        print(f"\nCycle {i}:")
        print(f"  Sowing:   {cycle.sowing_date.strftime('%Y-%m-%d')}")
        print(f"  Harvest:  {cycle.harvest_date.strftime('%Y-%m-%d')}")
        print(f"  Duration: {cycle.duration_days} days")
        print(f"  Type:     {cycle.crop_type}")
        print(f"  Peak NDVI: {cycle.peak_ndvi:.3f}")
        print(f"  Confidence: {cycle.confidence:.1f}%")
    
    # Analyze land utilization
    analyzer = LandUtilizationAnalyzer()
    utilization = analyzer.analyze(cycles, start_date, dates[-1])
    
    print(f"\nLAND UTILIZATION ANALYSIS:")
    print(f"  Crop Intensity: {utilization['crop_intensity']:.2f} crops/year")
    print(f"  Land Utilization: {utilization['land_utilization_index']:.1%}")
    print(f"  Cropping Pattern: {utilization['cropping_pattern']}")
    
    return cycles, utilization


def example_4_risk_index_engine():
    """
    Example 4: Agronomic risk index (index_v5)
    """
    print("\n" + "="*70)
    print("EXAMPLE 4: RISK INDEX ENGINE")
    print("="*70)

    from assessment.risk_index_engine import RiskIndexEngine
    from assessment.legacy_credit_shim import legacy_credit_shim

    engine = RiskIndexEngine(verbose=False)
    assessment = {
        "field_area_ha": 1.0,
        "farmer_benefits": {"pm_kisan_enrolled": True},
        "signal_quality_summary": {"valid_bin_fraction": 0.8},
        "cropping_analysis": {"cropping_intensity": 1.5, "cycles_per_year": 1.5},
        "performance_analysis": {
            "average_performance_score": 70.0,
            "average_yield_score": 65.0,
            "n_complete_cycles": 2,
        },
        "weather_analysis": {"weather_risk_score": 30.0},
        "crop_cycles": {"cycles_count": 2, "utilization_metrics": {}},
    }
    risk = engine.score(assessment)
    shim = legacy_credit_shim(risk)
    print(f"  Index: {risk['index_score']:.1f} ({risk['risk_category']})")
    print(f"  Shim credit_score: {shim['credit_score']:.1f}")
    print(f"  Component scalars: {list(shim['component_scores'].keys())}")
    return risk, shim


def example_5_module_imports():
    """
    Example 5: Verify all module imports
    """
    print("\n" + "="*70)
    print("EXAMPLE 5: MODULE IMPORT VERIFICATION")
    print("="*70)
    
    modules_to_test = [
        ('main', 'SatelliteBasedCreditPipeline'),
        ('data_acquisition.satellite_collector', 'SatelliteDataCollector'),
        ('data_acquisition.weather_analyzer', 'WeatherAnalyzer'),
        ('crop_analysis.crop_detector', 'CropDetector'),
        ('crop_analysis.performance_analyzer', 'CropPerformanceAnalyzer'),
        ('crop_analysis.crop_cycle_detector', 'CropCycleDetector'),
        ('crop_analysis.land_utilization_analyzer', 'LandUtilizationAnalyzer'),
        ('assessment.risk_index_engine', 'RiskIndexEngine'),
        ('assessment.legacy_credit_shim', 'legacy_credit_shim'),
        ('utils.peer_benchmark', 'PeerBenchmark'),
    ]
    
    print("\nTesting module imports...")
    success_count = 0
    
    for module_path, class_name in modules_to_test:
        try:
            module = __import__(module_path, fromlist=[class_name])
            getattr(module, class_name)
            print(f"  [OK] {module_path}.{class_name}")
            success_count += 1
        except Exception as e:
            print(f"  [FAIL] {module_path}.{class_name} - {e}")
    
    print(f"\nImport test: {success_count}/{len(modules_to_test)} successful")
    return success_count == len(modules_to_test)


def main():
    """
    Run all examples
    """
    print("\n" + "#"*70)
    print("# AGRONOMIC RISK PIPELINE v5.0 (index_v5) - EXAMPLE USAGE")
    print("#"*70)
    
    try:
        if not example_5_module_imports():
            print("\nWARNING: Some modules failed to import.")
            print("Run from backend/Credit_assessment with PYTHONPATH set.")
            return
        
        example_1_pipeline_init()
        example_2_direct_assessment()
        example_3_crop_cycle_detection()
        example_4_risk_index_engine()
        
        print("\n" + "#"*70)
        print("# ALL EXAMPLES COMPLETED SUCCESSFULLY!")
        print("#"*70)
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        
        print("\n" + "#"*70)
        print("# TROUBLESHOOTING:")
        print("#"*70)
        print("1. cd backend/Credit_assessment")
        print("2. pip install -r requirements.txt")
        print("3. Check that models/crop_classifier_model.joblib exists")
        print("#"*70)


if __name__ == "__main__":
    main()
