# Satellite-Based Agricultural Credit Assessment Pipeline

A comprehensive system for assessing farmer creditworthiness using satellite imagery analysis, crop performance monitoring, and weather risk assessment.

## 🎯 Overview

This pipeline analyzes 3 years of historical satellite data to:
- Detect crop presence and classify crop types
- Assess crop health and estimate yield potential
- Analyze cropping intensity and patterns
- Evaluate weather risks
- Calculate credit scores and recommend loan limits

## 🚀 Installation

### Prerequisites
- Python 3.8+
- Active internet connection for satellite/weather data
- Trained crop classification model (.joblib)

### Install Dependencies

```bash
pip install -r requirements_advanced.txt
```

### Download Crop Classification Model

Place your trained crop classification model in the `models/` directory:
```
models/crop_classifier_model.joblib
```

## 💻 Usage

### Basic Usage

```python
from main import SatelliteBasedCreditPipeline

# Initialize pipeline
pipeline = SatelliteBasedCreditPipeline(
    crop_model_path="models/crop_classifier_model.joblib",
    mode='BASIC',
    verbose=True
)

# Option 1: Using lat/long (will calculate buffer automatically)
assessment = pipeline.assess_farmer(
    farmer_id="FARMER_001",
    latitude=18.5204,
    longitude=73.8567,
    field_area_ha=2.5,
    analysis_years=3
)

# Option 2: Using farm boundary geometry
from shapely.geometry import Polygon

farm_boundary = Polygon([
    [73.856, 18.520],
    [73.857, 18.520],
    [73.857, 18.521],
    [73.856, 18.521],
    [73.856, 18.520]
])

assessment = pipeline.assess_farmer(
    farmer_id="FARMER_001",
    geometry=farm_boundary,
    analysis_years=3
)

# Save results
pipeline.save_assessment(assessment, output_dir="outputs")
```

### Enhanced Mode (All v4.0 Features)

```python
# Initialize with ALL v4.0 features
pipeline = SatelliteBasedCreditPipeline(
    crop_model_path='models/crop_classifier_model.joblib',
    mode='ENHANCED',              # Use v4.0 features
    ml_mode='hybrid',             # Hybrid ML + rule-based
    use_mongodb=True,
    enable_continuous_data=True,  # 3-year time series
    enable_crop_cycles=True,      # Automatic cycle detection
    enable_advanced_ml=True,      # ML-based scoring
)

# Assess a farmer from database
result = pipeline.assess_farmer_from_db('farmer_12345')

print(f"Score: {result['credit_assessment']['credit_score']}/100")
print(f"Method: {result['credit_assessment']['method']}")

# Check new features
if 'crop_cycles' in result:
    cycles = result['crop_cycles']
    print(f"Crop Cycles: {cycles['cycles_count']}")
    print(f"Land Utilization: {cycles['utilization_metrics']['land_utilization_index']:.1%}")
```

### Using Individual Components

```python
# Import specific components
from crop_analysis import CropCycleDetector, LandUtilizationAnalyzer
from assessment import AdvancedCreditScorer, UnsupervisedFarmerSegmentation

# Use crop cycle detector
detector = CropCycleDetector()
cycles = detector.detect_cycles(dates, ndvi_values)

# Use advanced scorer
scorer = AdvancedCreditScorer(mode='hybrid')
score = scorer.calculate_credit_score(
    cropping_analysis=cropping_data,
    performance_analysis=performance_data,
    weather_analysis=weather_data
)
```

## 📊 Assessment Components

The pipeline returns a comprehensive assessment including:

1. **Satellite Data**: 3-year historical satellite observations
2. **Weather Analysis**: Extreme events and seasonal patterns
3. **Cropping Analysis**: Crop types, cropping intensity
4. **Performance Analysis**: Health scores and yield estimates
5. **Credit Assessment**: Credit score (0-100) and risk category
6. **Credit Recommendations**: Loan limits, interest rates, terms

## 📈 Credit Scoring Methodology

### Component Weights

| Component | Weight | Description |
|-----------|--------|-------------|
| Crop Detection | 40% | Crop presence and consistency |
| Crop Performance | 30% | Health scores across seasons |
| Yield Potential | 15% | Expected productivity |
| Cropping Intensity | 10% | Land utilization efficiency |
| Weather Risk | 3% | Extreme weather events |
| Govt Benefits | 2% | PM-KISAN, insurance enrollment |

### Risk Categories

- **LOW** (70-100): ₹80,000/ha @ 7% interest
- **MEDIUM** (50-70): ₹50,000/ha @ 9.5% interest
- **HIGH** (30-50): ₹30,000/ha @ 12% interest
- **VERY HIGH** (0-30): ₹15,000/ha @ 15% interest

## 🌾 Supported Crops

Cotton, Gram (Chickpea), Maize, Mustard, Onion, Potato, Rice, Soybean, Wheat

Each crop has:
- Expected NDVI growth curves
- Critical growth stages
- Yield parameters (baseline, typical, optimal, max)
- Seasonal characteristics

## 🛰️ Data Sources

- **Satellite**: Sentinel-2 L2A (Planetary Computer)
- **Weather**: NASA POWER API
- **Crop Classification**: Custom Random Forest model

## 📋 Output Format

```json
{
  "farmer_id": "FARMER_001",
  "credit_score": 72.3,
  "risk_category": "LOW",
  "recommended_credit_limit": 185000,
  "cropping_analysis": {
    "dominant_crop": "Wheat",
    "cropping_intensity": 0.95,
    "seasons_with_crops": 8
  },
  "performance_analysis": {
    "average_health_score": 78.5,
    "average_yield_score": 82.1
  },
  "weather_analysis": {
    "total_extreme_events": 2,
    "weather_risk_score": 35.2
  }
}
```

## 📁 Project Structure

```
project/
├── main.py                          # Main pipeline
├── example_usage.py                 # Usage examples
├── requirements_advanced.txt        # Dependencies
│
├── data_acquisition/
│   ├── __init__.py
│   ├── satellite_collector.py
│   └── weather_analyzer.py
│
├── crop_analysis/
│   ├── __init__.py
│   ├── crop_detector.py
│   ├── performance_analyzer.py
│   ├── crop_cycle_detector.py
│   └── land_utilization_analyzer.py
│
├── assessment/
│   ├── __init__.py
│   ├── credit_scorer.py
│   ├── advanced_credit_scorer.py
│   └── unsupervised_segmentation.py
│
├── config/
│   ├── __init__.py
│   ├── pipeline_config.py
│   └── regional_config.py
│
├── utils/
│   ├── __init__.py
│   ├── geometry_utils.py
│   └── data_processing.py
│
└── models/
    └── crop_classifier_model.joblib
```

## 🔧 Configuration

Edit `config/pipeline_config.py` to customize:
- Analysis periods and seasons
- NDVI thresholds
- Credit scoring weights
- Risk category thresholds
- Interest rates and credit limits

## 🐛 Troubleshooting

### ModuleNotFoundError: No module named 'crop_analysis'

**Solution:**
```bash
# Check __init__.py exists
ls -l crop_analysis/__init__.py

# Set PYTHONPATH
export PYTHONPATH=$PYTHONPATH:$(pwd)

# Test import
python -c "from crop_analysis import CropCycleDetector; print('OK')"
```

### ImportError: cannot import name 'CropCycleDetector'

**Solution:**
```bash
# Check file exists
ls -l crop_analysis/crop_cycle_detector.py

# Check __init__.py includes it
cat crop_analysis/__init__.py | grep CropCycleDetector
```

### ImportError: No module named 'xgboost'

**Solution:**
```bash
# Install xgboost
pip install xgboost>=1.5.0

# Or install all dependencies
pip install -r requirements_advanced.txt
```

## 🤝 Contributing

This is a production system. Contact the AgriStack team for contributions.

## 📄 License

Proprietary - AgriStack Team

## 👥 Authors

AgriStack Team - Version 4.0

---

**Last Updated:** March 2026  
**Status:** Production Ready ✅