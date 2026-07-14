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

    print("Pipeline initialized (continuous path, crop cycles, AdvancedCreditScorer).")
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


def example_4_unsupervised_segmentation():
    """
    Example 4: Farmer segmentation without labels
    """
    print("\n" + "="*70)
    print("EXAMPLE 4: UNSUPERVISED FARMER SEGMENTATION")
    print("="*70)
    
    from assessment.unsupervised_segmentation import UnsupervisedFarmerSegmentation
    
    # Create mock farmer assessments
    print("\nGenerating 50 mock farmer assessments...")
    
    assessments = []
    for i in range(50):
        assessment = {
            'cropping_analysis': {
                'cropping_intensity': np.random.uniform(0.5, 3.0),
                'crop_detection_score': np.random.uniform(40, 95),
                'crops_detected': {f'crop_{j}': 1 for j in range(np.random.randint(1, 4))}
            },
            'performance_analysis': {
                'average_performance_score': np.random.uniform(40, 90),
                'average_yield_score': np.random.uniform(35, 85)
            },
            'weather_analysis': {
                'weather_risk_score': np.random.uniform(20, 70),
                'total_extreme_events': np.random.randint(0, 5)
            }
        }
        assessments.append(assessment)
    
    # Segment farmers
    segmenter = UnsupervisedFarmerSegmentation(n_segments=5)
    segments, profiles = segmenter.fit(assessments)
    
    print(f"\nDISCOVERED {len(profiles)} FARMER SEGMENTS:")
    for profile in profiles:
        print(f"\nSegment {profile['segment_id']}: {profile['name']}")
        print(f"  Count: {profile['count']} farmers")
        print(f"  Avg Intensity: {profile['average_features']['cropping_intensity']:.2f}")
        print(f"  Avg Performance: {profile['average_features']['avg_performance_score']:.1f}")
    
    return segments, profiles


def example_5_advanced_credit_scorer():
    """
    Example 5: Advanced credit scorer modes
    """
    print("\n" + "="*70)
    print("EXAMPLE 5: ADVANCED CREDIT SCORER")
    print("="*70)
    
    from assessment.advanced_credit_scorer import AdvancedCreditScorer
    
    # Test different modes
    modes = ['rule_based', 'unsupervised', 'hybrid']
    
    for mode in modes:
        print(f"\nTesting {mode.upper()} mode:")
        try:
            scorer = AdvancedCreditScorer(mode=mode, verbose=False)
            print(f"  [OK] {mode} scorer initialized successfully")
        except Exception as e:
            print(f"  [FAIL] Failed: {e}")
    
    return True


def example_6_module_imports():
    """
    Example 6: Verify all module imports
    """
    print("\n" + "="*70)
    print("EXAMPLE 6: MODULE IMPORT VERIFICATION")
    print("="*70)
    
    modules_to_test = [
        ('main', 'SatelliteBasedCreditPipeline'),
        ('data_acquisition.satellite_collector', 'SatelliteDataCollector'),
        ('data_acquisition.weather_analyzer', 'WeatherAnalyzer'),
        ('crop_analysis.crop_detector', 'CropDetector'),
        ('crop_analysis.performance_analyzer', 'CropPerformanceAnalyzer'),
        ('crop_analysis.crop_cycle_detector', 'CropCycleDetector'),
        ('crop_analysis.land_utilization_analyzer', 'LandUtilizationAnalyzer'),
        ('assessment.advanced_credit_scorer', 'AdvancedCreditScorer'),
        ('assessment.unsupervised_segmentation', 'UnsupervisedFarmerSegmentation'),
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
    print("# AGRICULTURAL CREDIT PIPELINE v4.0 - EXAMPLE USAGE")
    print("# (Organized Project Structure)")
    print("#"*70)
    
    try:
        # Example 6: Module imports (run first)
        if not example_6_module_imports():
            print("\n⚠ WARNING: Some modules failed to import.")
            print("Make sure you're running from the project root directory.")
            print("Try: export PYTHONPATH=$PYTHONPATH:$(pwd)")
            return
        
        example_1_pipeline_init()

        example_2_direct_assessment()
        
        # Example 3: Crop cycle detection
        example_3_crop_cycle_detection()
        
        # Example 4: Farmer segmentation
        example_4_unsupervised_segmentation()
        
        # Example 5: Advanced scorer
        example_5_advanced_credit_scorer()
        
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
        print("1. Make sure you're in the project root directory")
        print("2. Set PYTHONPATH: export PYTHONPATH=$PYTHONPATH:$(pwd)")
        print("3. Install dependencies: pip install -r requirements_advanced.txt")
        print("4. Check that models/crop_classifier_model.joblib exists")
        print("#"*70)


if __name__ == "__main__":
    main()
