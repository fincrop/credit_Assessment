"""
Pipeline Configuration
======================
Central configuration for the satellite-based credit assessment pipeline.

VERSION 3.0 — Major Updates:
- Seasons: Kharif + Rabi only (Summer removed, no overlaps)
- Rabi extended Dec 1 – May 31 (captures late-harvest crops)
- Analysis window: previous 10 seasons (not calendar years)
- Scenes per season: 25 target / 30 max (was 18/24)
- Cloud cover threshold: 60% (tightened from 80%)
- Scene ordering: chronological always
- Long-duration crop detection parameters added
- Cross-season continuity detection parameters added
"""


class PipelineConfig:
    """Central configuration for the pipeline"""

    # ========================================================================
    # SEASON DEFINITIONS  (2 seasons only — Kharif & Rabi)
    # ========================================================================
    # Changes from v2:
    #   - Summer season REMOVED (was Mar-Jul, heavily overlapped both seasons)
    #   - Rabi START moved to Dec 1 (was Nov 1) → eliminates Nov overlap
    #   - Rabi END extended to May 31 (was Apr 30) → captures late harvests
    #     e.g. Potato sown Jan → harvest May; Onion sown Feb → harvest Jun
    # Non-overlapping calendar:
    #   Kharif:  Jun 1  → Nov 30   (183 days)
    #   Rabi:    Dec 1  → May 31   (182 days)
    # ========================================================================

    SEASONS = {
        'kharif': {
            'start_month': 5,
            'start_day':   15,
            'end_month':   10,
            'end_day':     15,
            'description': 'Monsoon season — Jun 1 to oct 30',
            'primary_crops': [
                'Rice', 'Cotton', 'Soyabean', 'Maize', 'Bajra',
                'Jowar', 'Groundnut', 'Tur', 'Sugarcane', 'Banana',
                'Papaya', 'Pomegranate',
            ],
        },
        'rabi': {
            'start_month': 10,
            'start_day':   15,
            'end_month':   5,
            'end_day':     15,
            'description': 'Winter/Spring season — Nov 1 to May 31',
            'primary_crops': [
                'Wheat', 'Gram', 'Mustard', 'Potato', 'Onion',
                'Sunflower', 'Tobacco', 'Chilli', 'Cabbage', 'Grapes',
            ],
        },
    }

    # ========================================================================
    # ANALYSIS WINDOW — 10 seasons
    # ========================================================================
    # 10 seasons = 5 kharif + 5 rabi = ~5 complete crop years of history.
    # Moving from "N calendar years" to "N seasons" gives consistent data
    # volume regardless of when the assessment runs in the year.
    # Long-duration crops (Sugarcane ~330 days) can span 2 consecutive
    # seasons; handled by cross-season merger in satellite_collector.
    # ========================================================================

    NUM_SEASONS        = 6   # Seasons to analyse
    MAX_YEARS_BACK     =  4   # Safety cap (never go beyond 6 years)

    # ========================================================================
    # LONG-DURATION CROP THRESHOLDS
    # ========================================================================
    # Crops whose typical duration exceeds ~180 days (one full season window).
    # The cross-season merger checks NDVI continuity at season boundaries
    # to decide if adjacent seasons contain the same continuing crop or
    # two distinct crops.

    LONG_DURATION_CROPS = {
        'Sugarcane':   330,
        'Banana':      330,
        'Papaya':      300,
        'Pomegranate': 180,
        'Tur':         180,
        'Cotton':      180,
        'Tobacco':     160,
    }

    # If NDVI at the END of season-A is still >= this value AND NDVI at the
    # START of season-B is also >= this value, seasons are considered to
    # contain the SAME continuing crop (not two separate crops).
    CROSS_SEASON_NDVI_CONTINUITY = 0.38

    # Minimum NDVI DROP between season-A end and season-B start that
    # confirms a harvest/replanting event (two distinct crops).
    CROSS_SEASON_NDVI_DROP_FOR_NEW_CROP = 0.15

    # ========================================================================
    # SATELLITE DATA PARAMETERS
    # ========================================================================

    TARGET_SCENES_PER_SEASON    = 25   # ideal scenes per season window
    MAX_SCENES_PER_SEASON       = 30   # hard cap
    MIN_SCENES_PER_SEASON       =  5   # minimum for valid analysis
    MIN_OBSERVATIONS_PER_SEASON =  5   # alias used in crop_detector

    MAX_CLOUD_COVER       = 60.0   # % — tightened from 80 for NDVI accuracy
    MIN_VALID_PIXEL_RATIO = 0.20   # minimum fraction of valid pixels per scene

    IDEAL_GAP_DAYS = 7    # ~1 scene/week
    MAX_GAP_DAYS   = 21   # warn if any gap > 3 weeks

    TARGET_RESOLUTION_M = 10   # all bands resampled to 10m

    # Continuous 3-year Sentinel-2: pick ONE best-quality scene per time step (lowest cloud).
    # ~365/interval ≈ 36–37 scenes/year → ~110 over three years when bins are filled.
    CONTINUOUS_SCENE_INTERVAL_DAYS = 10
    # Legacy: ignore interval logic; subsample with _select_scenes_temporal_distribution.
    CONTINUOUS_LEGACY_SCENE_SUBSAMPLE = False
    CONTINUOUS_SCENE_TARGET_MIN = 30   # only if LEGACY True
    CONTINUOUS_SCENE_TARGET_MAX = 500  # only if LEGACY True
    # Parallel COG reads: too many threads can trigger Azure/GDAL flakes; tune per host.
    # Use 0 for auto: min(24, max(4, (CPU count) × 3)).
    # Parallel COG reads often *slow* work and cause TIFF tile errors (concurrent Azure/GDAL).
    # Defaults: one scene at a time, years one after another (fastest stable on most networks).
    SATELLITE_DOWNLOAD_SCENE_PARALLEL = False
    SATELLITE_DOWNLOAD_MAX_WORKERS = 8    # only if SCENE_PARALLEL True (keep low)
    # Kept for compatibility; parallel multi-year download removed from code (unstable).
    SATELLITE_DOWNLOAD_MAX_YEAR_WORKERS = 1
    SATELLITE_BAND_READ_RETRIES = 4       # transient TIFFReadEncodedTile / partial HTTP
    SATELLITE_BAND_RETRY_DELAY_SEC = 1.0
    # Fetch multiple band COGs in parallel *within one scene* (separate URLs; often faster than
    # strictly sequential reads). Safer than SATELLITE_DOWNLOAD_SCENE_PARALLEL (many scenes).
    SATELLITE_BAND_DOWNLOAD_PARALLEL = True
    # Up to 7 (6 reflectance + B11) parallel COG reads per scene when B11 is included.
    SATELLITE_BAND_DOWNLOAD_MAX_WORKERS = 8
    # Split the lookback window into calendar-year STAC queries (smaller, more reliable).
    SATELLITE_STAC_SPLIT_BY_YEAR = True
    SATELLITE_STAC_YEAR_SEARCH_WORKERS = 4   # parallel year searches (1 = sequential)
    SATELLITE_STAC_SEARCH_RETRIES = 2        # retry failed year search (API timeouts)
    SATELLITE_STAC_SEARCH_RETRY_DELAY_SEC = 2.0
    # Process selected scenes grouped by calendar year (orderly logs); still one COG stream.
    CONTINUOUS_DOWNLOAD_BATCH_BY_YEAR = True
    SATELLITE_INTER_YEAR_PAUSE_SEC = 0.0     # pause between calendar-year batches (0 = none)

    # Fallback detection threshold (overridden per region by RegionalConfig)
    CROP_DETECTION_NDVI_THRESHOLD = 0.20

    SENTINEL2_BANDS = {
        "B02": {"resolution": 10, "name": "Blue"},
        "B03": {"resolution": 10, "name": "Green"},
        "B04": {"resolution": 10, "name": "Red"},
        "B05": {"resolution": 20, "name": "Red Edge 1"},
        "B06": {"resolution": 20, "name": "Red Edge 2"},
        "B07": {"resolution": 20, "name": "Red Edge 3"},
        "B08": {"resolution": 10, "name": "NIR"},
        "B8A": {"resolution": 20, "name": "Narrow NIR"},
        "B11": {"resolution": 20, "name": "SWIR 1"},
        "B12": {"resolution": 20, "name": "SWIR 2"},
    }

    # ========================================================================
    # CROP DETECTION — Temporal pattern parameters
    # ========================================================================
    # Replaces single peak-NDVI threshold with multi-signal pattern check.

    MIN_NDVI_RISE                = 0.10   # min absolute rise from start→peak
    MIN_FRACTION_ABOVE_THRESHOLD = 0.25   # ≥25% of scenes must exceed threshold
    MIN_NDVI_CV_FOR_CROP         = 0.08   # min temporal variation (not bare soil)
    MAX_NDVI_CV_FOR_CROP         = 0.80   # max variation (not cloud noise)

    # ========================================================================
    # ML CLASSIFICATION — Feature extraction
    # ========================================================================

    ML_FEATURE_SCENES  = 15                            # chronological scenes
    ML_FEATURE_INDICES = ['NDVI_mean', 'EVI_mean', 'NDMI_mean']

    # ========================================================================
    # CROP CYCLE DETECTION (continuous series -> sowing / harvest windows)
    # ========================================================================
    # Irrigated triple-crop (e.g. Rabi veg -> Zaid maize -> Kharif rice) needs
    # shorter per-cycle caps than sugarcane-style defaults; the old 420d max let
    # harvest "last resort" pick a trough months later and merge real cycles.
    CROP_CYCLE_MIN_NDVI_RISE = 0.10
    CROP_CYCLE_MIN_BASELINE_CVI = 0.30
    CROP_CYCLE_MIN_PEAK_CVI = 0.28
    CROP_CYCLE_MIN_DURATION_DAYS = 40
    # Raise to 360+ only for long-duration crops (e.g. sugarcane) on the same field.
    CROP_CYCLE_MAX_DURATION_DAYS = 195
    # Harvest triggers are only scanned this many calendar days after the CVI peak
    # (14-day grid => ~10 bins). Prevents one crop from absorbing the next peaks.
    CROP_CYCLE_MAX_DAYS_AFTER_PEAK = 135
    # Minimum grid-point spacing between greenup events (~3 * 14d ≈ 42d).
    CROP_CYCLE_GREENUP_MIN_GRID_SEP = 3
    CROP_CYCLE_SUSTAINED_GROWTH_FRAC = 0.55
    # Merge two cycles only if overlap is large vs calendar AND vs shorter duration
    # (duplicate detections), not adjacent crops with fuzzy boundaries.
    CROP_CYCLE_MERGE_OVERLAP_MIN_DAYS = 55
    CROP_CYCLE_MERGE_OVERLAP_RATIO = 0.42
    # DB sowing_date: repeat anchor along rotation (~3 crops / yr), not crop-specific typical+30.
    CROP_CYCLE_HINT_STEP_DAYS = 118
    CROP_CYCLE_MAX_HINT_ANCHORS = 40

    # ========================================================================
    # WEATHER PARAMETERS
    # ========================================================================

    WEATHER_PARAMETERS       = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS2M"]
    WEATHER_API_TIMEOUT      = 120
    WEATHER_API_MAX_RETRIES  = 3

    HEATWAVE_THRESHOLD_C      = 40
    HEATWAVE_MIN_DAYS         =  3
    COLD_WAVE_THRESHOLD_C     = 10
    COLD_WAVE_MIN_DAYS        =  3
    HEAVY_RAIN_SINGLE_DAY_MM  = 100
    HEAVY_RAIN_3DAY_MM        = 200
    DROUGHT_MIN_DAYS          =  30
    DROUGHT_DAILY_RAINFALL_MM =   2

    # ========================================================================
    # CREDIT SCORING WEIGHTS  (must sum to 100)
    # ========================================================================

    CREDIT_WEIGHTS = {
        'crop_detection':    35,   # temporal pattern presence (was 40)
        'crop_performance':  30,   # health score from NDVI curves
        'yield_potential':   15,   # cumulative NDVI yield proxy
        'cropping_intensity': 12,  # seasons utilised (was 10)
        'weather_risk':       5,   # inverted risk score (was 3)
        'govt_benefits':      3,   # PM-KISAN + insurance (was 2)
    }
    assert sum(CREDIT_WEIGHTS.values()) == 100, "CREDIT_WEIGHTS must sum to 100"

    # ========================================================================
    # RISK CATEGORIES & LOAN TERMS
    # ========================================================================

    RISK_THRESHOLDS = {
        'LOW':       (70, 101),
        'MEDIUM':    (50,  70),
        'HIGH':      (30,  50),
        'VERY_HIGH': ( 0,  30),
    }

    CREDIT_LIMITS_PER_HA = {
        'LOW':       80000,
        'MEDIUM':    50000,
        'HIGH':      30000,
        'VERY_HIGH': 15000,
    }

    INTEREST_RATES = {
        'LOW':        7.0,
        'MEDIUM':     9.5,
        'HIGH':      12.0,
        'VERY_HIGH': 15.0,
    }

    REPAYMENT_PERIODS = {
        'LOW':       12,
        'MEDIUM':     9,
        'HIGH':       6,
        'VERY_HIGH':  6,
    }

    # ========================================================================
    # CROP CLASSIFICATION
    # ========================================================================

    SUPPORTED_CROPS = [
        'Bajra', 'Banana', 'Cabbage', 'Chilli', 'Cotton',
        'Gram', 'Grapes', 'Groundnut', 'Jowar', 'Maize',
        'Mustard', 'Onion', 'Others', 'Papaya', 'Pomegranate',
        'Potato', 'Rice', 'Soyabean', 'Sugarcane', 'Sunflower',
        'Tobacco', 'Tur', 'Wheat',
    ]

    HIGH_VALUE_CROPS = [
        'Chilli', 'Cotton', 'Grapes', 'Groundnut', 'Mustard',
        'Onion', 'Papaya', 'Pomegranate', 'Potato',
        'Sunflower', 'Tobacco', 'Tur',
    ]
    HIGH_VALUE_MULTIPLIER = 1.15

    # ========================================================================
    # CROPPING INTENSITY
    # ========================================================================
    # Intensity = fraction of season slots actually cultivated.
    # With 2 seasons per year: max intensity = 1.0 per year if both grown.
    # Long-duration crops spanning 2 seasons count as 1 crop but occupy
    # both season slots (flagged by cross-season merger).

    INTENSITY_WEIGHTS = {
        'kharif': 0.50,
        'rabi':   0.50,
    }

    # ========================================================================
    # HEALTH SCORING
    # ========================================================================

    HEALTH_CATEGORIES = {
        'Excellent': (85, 101),
        'Good':      (70,  85),
        'Moderate':  (55,  70),
        'Poor':      (35,  55),
        'Critical':  ( 0,  35),
    }

    # ========================================================================
    # GEOMETRY
    # ========================================================================

    MIN_FIELD_BUFFER_KM = 0.5
    EARTH_RADIUS_KM     = 6371.0
    KM_PER_DEGREE_LAT   = 111.0

    # ========================================================================
    # LOGGING / OUTPUT
    # ========================================================================

    LOG_LEVEL  = "INFO"
    LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ASSESSMENT_FILENAME_TEMPLATE = "assessment_{farmer_id}_{timestamp}.json"

    # ========================================================================
    # API ENDPOINTS
    # ========================================================================

    STAC_API_URL         = "https://planetarycomputer.microsoft.com/api/stac/v1"
    SENTINEL2_COLLECTION = "sentinel-2-l2a"
    NASA_POWER_BASE_URL  = "https://power.larc.nasa.gov/api/temporal/daily/point"
    NASA_POWER_COMMUNITY = "AG"

    # ========================================================================
    # AI INTEGRATION CONFIGURATION
    # ========================================================================
    # API keys are read from environment variables — never hardcode them here.
    # Set them before running:
    #   $env:GROQ_API_KEY   = "gsk_..."     (Windows PowerShell)
    #   $env:SARVAM_API_KEY = "sk-..."

    import os as _os

    AI_CONFIG = {
        # Groq — English report generation
        "groq": {
            "api_key":    _os.environ.get("GROQ_API_KEY"),          # None → disable
            "model":      _os.environ.get("GROQ_MODEL", "llama-3.1-70b-versatile"),
            "api_url":    "https://api.groq.com/openai/v1/chat/completions",
            "timeout":    30,
            "max_tokens": 400,
            "enabled":    bool(_os.environ.get("GROQ_API_KEY")),
        },
        # SarvamAI — Indian language translation
        "sarvam": {
            "api_key":         _os.environ.get("SARVAM_API_KEY"),   # None → disable
            "api_url":         "https://api.sarvam.ai/translate",
            "default_language": "hi",                                # Hindi first
            "supported_languages": ["hi", "te", "mr", "ta", "kn", "gu", "pa", "bn", "or", "ml"],
            "timeout":         30,
            "enabled":         bool(_os.environ.get("SARVAM_API_KEY")),
        },
        # SHAP — Feature explainability (runs only with --explain flag)
        "shap": {
            "enabled":                True,
            "top_n_features":         5,
            "use_tree_explainer":     True,   # Fast for RF/XGBoost
        },
        # Counterfactual engine
        "counterfactual": {
            "enabled":        True,
            "max_scenarios":  3,
        },
    }


    # ========================================================================
    # VALIDATION
    # ========================================================================

    @classmethod
    def validate_config(cls):
        errors = []
        if sum(cls.CREDIT_WEIGHTS.values()) != 100:
            errors.append("CREDIT_WEIGHTS must sum to 100")
        if cls.TARGET_SCENES_PER_SEASON > cls.MAX_SCENES_PER_SEASON:
            errors.append("TARGET > MAX scenes")
        if cls.MIN_SCENES_PER_SEASON > cls.TARGET_SCENES_PER_SEASON:
            errors.append("MIN > TARGET scenes")
        if cls.CONTINUOUS_SCENE_TARGET_MIN > cls.CONTINUOUS_SCENE_TARGET_MAX:
            errors.append("CONTINUOUS_SCENE_TARGET_MIN > MAX")
        if getattr(cls, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10) < 1:
            errors.append("CONTINUOUS_SCENE_INTERVAL_DAYS must be >= 1")
        if getattr(cls, "SATELLITE_STAC_YEAR_SEARCH_WORKERS", 1) < 1:
            errors.append("SATELLITE_STAC_YEAR_SEARCH_WORKERS must be >= 1")
        if getattr(cls, "SATELLITE_DOWNLOAD_MAX_YEAR_WORKERS", 1) < 1:
            errors.append("SATELLITE_DOWNLOAD_MAX_YEAR_WORKERS must be >= 1")
        if errors:
            raise ValueError(f"Config errors: {errors}")
        return True




"""
Crop-Specific Growth Curves and Yield Parameters
=================================================
Expected NDVI trajectories and yield parameters for 23 crops:
Bajra, Banana, Cabbage, Chilli, Cotton, Gram, Grapes, Groundnut,
Jowar, Maize, Mustard, Onion, Others, Papaya, Pomegranate, Potato,
Rice, Soyabean, Sugarcane, Sunflower, Tobacco, Tur, Wheat

These curves are used for:
1. Health assessment (comparing actual vs expected NDVI growth)
2. Yield estimation (baseline/optimal/max yields)
3. Growth stage identification
4. Credit scoring via CropPerformanceAnalyzer

NDVI Curve Format: List of (days_since_sowing, expected_NDVI, stage_name)
- All curves are India-specific and based on typical field conditions
- NDVI values reflect satellite-observable canopy density, NOT just leaf area
- Crops with inherently low canopy (Groundnut, Bajra early) will have lower peaks
  than dense canopy crops (Rice, Sugarcane) — this is INTENTIONAL
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class CropGrowthCurves:
    """
    Crop-specific growth curve definitions based on agronomic knowledge
    and typical Indian agricultural practices.

    Covers all 23 crops supported by the ML classifier:
    Bajra, Banana, Cabbage, Chilli, Cotton, Gram, Grapes, Groundnut,
    Jowar, Maize, Mustard, Onion, Others, Papaya, Pomegranate, Potato,
    Rice, Soyabean, Sugarcane, Sunflower, Tobacco, Tur, Wheat
    """

    # ========================================================================
    # CROP SEASON DURATIONS (days from sowing/planting to harvest)
    # ========================================================================

    CROP_DURATIONS = {

        # ----- CEREALS & MILLETS -----

        'Bajra': {
            'min_days': 65,
            'typical_days': 85,
            'max_days': 110,
            'season': 'kharif',
            'description': 'Pearl millet — short duration, drought-tolerant millet'
        },
        'Jowar': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'both',   # Kharif & Rabi varieties exist
            'description': 'Sorghum — dual-purpose cereal (grain + fodder)'
        },
        'Maize': {
            'min_days': 80,
            'typical_days': 100,
            'max_days': 120,
            'season': 'both',
            'description': 'Short duration cereal'
        },
        'Rice': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 150,
            'season': 'kharif',
            'description': 'Wetland cereal'
        },
        'Wheat': {
            'min_days': 110,
            'typical_days': 130,
            'max_days': 150,
            'season': 'rabi',
            'description': 'Winter cereal'
        },

        # ----- OILSEEDS -----

        'Groundnut': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'kharif',
            'description': 'Peanut — kharif oilseed; low spreading canopy'
        },
        'Mustard': {
            'min_days': 110,
            'typical_days': 130,
            'max_days': 150,
            'season': 'rabi',
            'description': 'Rabi oilseed crop'
        },
        'Soyabean': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'kharif',
            'description': 'Kharif oilseed-pulse'
        },
        'Sunflower': {
            'min_days': 85,
            'typical_days': 100,
            'max_days': 120,
            'season': 'both',   # Rabi in South India; Kharif in North
            'description': 'Oilseed crop — tall with large single head'
        },
        'Tobacco': {
            'min_days': 130,
            'typical_days': 160,
            'max_days': 180,
            'season': 'rabi',
            'description': 'Rabi cash crop; dense leaf canopy at peak'
        },

        # ----- PULSES -----

        'Gram': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 140,
            'season': 'rabi',
            'description': 'Chickpea — rabi pulse crop'
        },
        'Tur': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 220,
            'season': 'kharif',
            'description': 'Pigeonpea (Arhar) — long duration kharif pulse; deep green canopy'
        },

        # ----- FIBER -----

        'Cotton': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 210,
            'season': 'kharif',
            'description': 'Long duration fiber crop'
        },

        # ----- VEGETABLES -----

        'Cabbage': {
            'min_days': 60,
            'typical_days': 80,
            'max_days': 100,
            'season': 'rabi',
            'description': 'Cole crop — compact rosette, moderate NDVI'
        },
        'Chilli': {
            'min_days': 120,
            'typical_days': 150,
            'max_days': 180,
            'season': 'both',
            'description': 'Long duration vegetable crop'
        },
        'Onion': {
            'min_days': 100,
            'typical_days': 120,
            'max_days': 150,
            'season': 'both',
            'description': 'Bulb vegetable — moderate canopy'
        },
        'Potato': {
            'min_days': 90,
            'typical_days': 110,
            'max_days': 130,
            'season': 'rabi',
            'description': 'Tuber crop — dense canopy during bulking'
        },

        # ----- FRUITS / PERENNIALS -----

        'Banana': {
            'min_days': 270,
            'typical_days': 330,
            'max_days': 365,
            'season': 'perennial',
            'description': 'Perennial fruit crop — evergreen, high NDVI year-round'
        },
        'Grapes': {
            'min_days': 120,
            'typical_days': 150,
            'max_days': 180,
            'season': 'rabi',   # Harvest typically Feb–Mar in Maharashtra
            'description': 'Perennial vine — pruned annually; seasonal NDVI cycle'
        },
        'Papaya': {
            'min_days': 240,
            'typical_days': 300,
            'max_days': 365,
            'season': 'perennial',
            'description': 'Perennial fruit — moderate canopy, high year-round NDVI'
        },
        'Pomegranate': {
            'min_days': 150,
            'typical_days': 180,
            'max_days': 210,
            'season': 'perennial',
            'description': 'Semi-deciduous perennial — moderate canopy NDVI'
        },

        # ----- SUGARCANE -----

        'Sugarcane': {
            'min_days': 270,
            'typical_days': 330,
            'max_days': 365,
            'season': 'kharif',   # Planted around March–June; 10–12 months crop
            'description': 'Long duration; very high NDVI at peak'
        },

        # ----- CATCHALL -----

        'Others': {
            'min_days': 90,
            'typical_days': 120,
            'max_days': 150,
            'season': 'both',
            'description': 'Miscellaneous / unclassified crops — generic curve used'
        },
    }

    # ========================================================================
    # EXPECTED NDVI TRAJECTORIES
    # ========================================================================
    # Format: List of (days_since_sowing, expected_NDVI, stage_name)
    #
    # Design principles:
    #   - Low-canopy crops (Groundnut, Bajra) have lower absolute peaks (~0.65-0.70)
    #   - Dense / irrigated crops (Rice, Sugarcane, Wheat) peak higher (0.85-0.90)
    #   - Perennials (Banana, Papaya) stay elevated throughout season
    #   - All senescence / harvest-ready values reflect real satellite signal decline
    # ========================================================================

    EXPECTED_NDVI_CURVES = {

        # ================================================================
        # CEREALS & MILLETS
        # ================================================================

        'Bajra': [
            # Rapid establishment in hot/dry conditions
            (0,   0.12, 'Germination'),
            (7,   0.22, 'Seedling'),
            (14,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Active Vegetative'),
            (35,  0.68, 'Grand Growth'),
            # Heading & Flowering
            (45,  0.75, 'Panicle Initiation'),
            (55,  0.78, 'Heading'),
            (60,  0.72, 'Flowering'),
            # Grain Fill & Maturity
            (70,  0.62, 'Grain Filling'),
            (80,  0.48, 'Dough Stage'),
            (85,  0.35, 'Harvest Ready'),
        ],

        'Jowar': [
            (0,   0.12, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.38, 'Early Vegetative'),
            (30,  0.55, 'Active Vegetative'),
            (45,  0.70, 'Grand Growth'),
            # Heading
            (55,  0.78, 'Panicle Initiation'),
            (65,  0.82, 'Heading'),
            (70,  0.80, 'Flowering'),
            # Grain Fill
            (80,  0.72, 'Early Grain Fill'),
            (90,  0.60, 'Mid Grain Fill'),
            (100, 0.45, 'Dough Stage'),
            (110, 0.32, 'Harvest Ready'),
        ],

        'Maize': [
            (0,   0.15, 'Germination'),
            (7,   0.25, 'Seedling'),
            (15,  0.40, 'Early Vegetative'),
            (25,  0.60, 'Active Vegetative'),
            (35,  0.75, 'Rapid Growth'),
            (45,  0.85, 'Tasseling'),
            (55,  0.88, 'Silking/Pollination'),
            (65,  0.85, 'Early Grain Fill'),
            (75,  0.78, 'Mid Grain Fill'),
            (85,  0.68, 'Late Grain Fill'),
            (95,  0.52, 'Physiological Maturity'),
            (100, 0.40, 'Harvest Ready'),
        ],

        'Rice': [
            (0,   0.12, 'Germination/Transplanting'),
            (10,  0.25, 'Seedling Establishment'),
            (20,  0.42, 'Early Tillering'),
            (30,  0.60, 'Active Tillering'),
            (40,  0.75, 'Maximum Tillering'),
            (50,  0.82, 'Panicle Initiation'),
            (60,  0.88, 'Booting'),
            (70,  0.90, 'Heading'),
            (80,  0.88, 'Flowering'),
            (90,  0.82, 'Early Grain Fill'),
            (100, 0.72, 'Mid Grain Fill'),
            (110, 0.58, 'Late Grain Fill'),
            (120, 0.40, 'Harvest Ready'),
        ],

        'Wheat': [
            (0,   0.12, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.40, 'Early Tillering'),
            (30,  0.58, 'Active Tillering'),
            (40,  0.72, 'Maximum Tillering'),
            (50,  0.82, 'Jointing'),
            (60,  0.88, 'Booting'),
            (70,  0.90, 'Heading'),
            (80,  0.88, 'Flowering'),
            (90,  0.82, 'Early Grain Fill'),
            (100, 0.75, 'Mid Grain Fill'),
            (110, 0.65, 'Late Grain Fill'),
            (120, 0.50, 'Dough Stage'),
            (130, 0.35, 'Harvest Ready'),
        ],

        # ================================================================
        # OILSEEDS
        # ================================================================

        'Groundnut': [
            # Spreading prostrate growth — never gets very dense canopy
            (0,   0.12, 'Germination'),
            (10,  0.20, 'Seedling'),
            (20,  0.32, 'Early Vegetative'),
            (30,  0.48, 'Active Vegetative'),
            (40,  0.60, 'Flowering'),
            # Pegging & Pod development — canopy spreads laterally
            (50,  0.68, 'Pegging'),
            (60,  0.72, 'Pod Initiation'),
            (70,  0.70, 'Pod Development'),
            (80,  0.65, 'Pod Filling'),
            # Maturity
            (95,  0.52, 'Pod Maturation'),
            (110, 0.38, 'Harvest Ready'),
        ],

        'Mustard': [
            (0,   0.12, 'Germination'),
            (7,   0.22, 'Seedling'),
            (15,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Rosette Formation'),
            (35,  0.68, 'Stem Elongation'),
            (45,  0.80, 'Early Flowering'),
            (55,  0.88, 'Peak Flowering'),
            (70,  0.85, 'Late Flowering'),
            (80,  0.78, 'Pod Formation'),
            (95,  0.70, 'Pod Filling'),
            (110, 0.58, 'Pod Maturation'),
            (120, 0.42, 'Late Maturity'),
            (130, 0.30, 'Harvest Ready'),
        ],

        'Soyabean': [
            (0,   0.15, 'Germination'),
            (7,   0.25, 'Emergence'),
            (15,  0.40, 'First Trifoliate'),
            (25,  0.60, 'Early Vegetative'),
            (35,  0.75, 'Rapid Vegetative'),
            (45,  0.85, 'Flowering Initiation'),
            (55,  0.88, 'Peak Flowering'),
            (60,  0.86, 'Pod Initiation'),
            (70,  0.82, 'Pod Development'),
            (80,  0.75, 'Seed Fill'),
            (90,  0.65, 'Seed Maturation'),
            (100, 0.50, 'Leaf Senescence'),
            (110, 0.35, 'Harvest Ready'),
        ],

        'Sunflower': [
            (0,   0.13, 'Germination'),
            (7,   0.22, 'Seedling'),
            (15,  0.38, 'Early Vegetative'),
            (25,  0.55, 'Rapid Vegetative'),
            (35,  0.70, 'Active Growth'),
            # Bud & Flowering
            (45,  0.80, 'Bud Formation'),
            (55,  0.85, 'Flowering'),
            (65,  0.82, 'Post-Flowering'),
            # Seed fill & Maturity
            (75,  0.72, 'Seed Filling'),
            (85,  0.60, 'Seed Maturation'),
            (95,  0.45, 'Physiological Maturity'),
            (100, 0.32, 'Harvest Ready'),
        ],

        'Tobacco': [
            # Transplanted seedlings — slow early establishment
            (0,   0.10, 'Transplanting'),
            (10,  0.18, 'Early Establishment'),
            (20,  0.32, 'Active Vegetative'),
            (35,  0.55, 'Rapid Leaf Growth'),
            # Topping & Full canopy
            (50,  0.75, 'Topping'),
            (65,  0.85, 'Peak Canopy'),
            (80,  0.88, 'Mature Leaf'),
            # Harvest (sequential leaf picking)
            (100, 0.78, 'Early Harvest'),
            (120, 0.62, 'Mid Harvest'),
            (140, 0.45, 'Late Harvest'),
            (160, 0.28, 'Harvest Complete'),
        ],

        # ================================================================
        # PULSES
        # ================================================================

        'Gram': [
            (0,   0.12, 'Germination'),
            (10,  0.20, 'Seedling'),
            (20,  0.35, 'Early Vegetative'),
            (40,  0.58, 'Active Vegetative'),
            (50,  0.68, 'Pre-Flowering'),
            (60,  0.78, 'Flowering'),
            (70,  0.82, 'Peak Flowering'),
            (80,  0.80, 'Pod Formation'),
            (90,  0.75, 'Pod Filling'),
            (100, 0.65, 'Pod Maturation'),
            (110, 0.50, 'Late Maturity'),
            (120, 0.35, 'Harvest Ready'),
        ],

        'Tur': [
            # Pigeonpea — slow-starting, long-season, deep green at peak
            (0,   0.12, 'Germination'),
            (15,  0.20, 'Seedling'),
            (30,  0.35, 'Early Vegetative'),
            (50,  0.55, 'Active Vegetative'),
            (70,  0.70, 'Rapid Growth'),
            (90,  0.80, 'Pre-Flowering'),
            (110, 0.85, 'Flowering'),
            (130, 0.82, 'Pod Formation'),
            (150, 0.78, 'Pod Filling'),
            (165, 0.68, 'Pod Maturation'),
            (180, 0.48, 'Late Maturity'),
            (190, 0.32, 'Harvest Ready'),
        ],

        # ================================================================
        # FIBER
        # ================================================================

        'Cotton': [
            (0,   0.15, 'Germination'),
            (10,  0.22, 'Seedling'),
            (20,  0.35, 'Early Vegetative'),
            (40,  0.55, 'Active Vegetative'),
            (60,  0.72, 'Peak Vegetative'),
            (80,  0.82, 'Flowering Initiation'),
            (100, 0.85, 'Peak Flowering'),
            (120, 0.80, 'Boll Development'),
            (140, 0.72, 'Boll Maturation'),
            (160, 0.58, 'Early Maturity'),
            (180, 0.40, 'Harvest Ready'),
        ],

        # ================================================================
        # VEGETABLES
        # ================================================================

        'Cabbage': [
            # Compact rosette — NDVI moderate, canopy closes early
            (0,   0.10, 'Transplanting'),
            (7,   0.18, 'Early Establishment'),
            (15,  0.30, 'Rosette Stage'),
            (25,  0.50, 'Leaf Formation'),
            (35,  0.65, 'Rapid Leaf Growth'),
            (45,  0.72, 'Head Initiation'),
            (55,  0.75, 'Head Development'),
            (65,  0.72, 'Head Filling'),
            (75,  0.65, 'Maturation'),
            (80,  0.55, 'Harvest Ready'),
        ],

        'Chilli': [
            (0,   0.10, 'Transplanting'),
            (10,  0.18, 'Establishment'),
            (20,  0.30, 'Early Vegetative'),
            (35,  0.50, 'Active Vegetative'),
            (50,  0.65, 'Branching'),
            (65,  0.75, 'Flowering'),
            (80,  0.78, 'Fruit Set'),
            (100, 0.75, 'Fruit Development'),
            (120, 0.70, 'Fruit Maturation'),
            (140, 0.62, 'Peak Harvest'),
            (150, 0.50, 'Late Harvest'),
        ],

        'Onion': [
            (0,   0.10, 'Germination'),
            (10,  0.18, 'Seedling'),
            (20,  0.30, 'Early Vegetative'),
            (30,  0.48, 'Active Vegetative'),
            (40,  0.62, 'Rapid Leaf Growth'),
            (50,  0.74, 'Peak Vegetative'),
            (60,  0.78, 'Maximum Leaf Area'),
            (70,  0.75, 'Bulb Initiation'),
            (80,  0.70, 'Bulb Development'),
            (90,  0.62, 'Bulb Maturation'),
            (100, 0.52, 'Bulb Sizing'),
            (110, 0.38, 'Late Maturity'),
            (120, 0.25, 'Harvest Ready'),
        ],

        'Potato': [
            (0,   0.10, 'Planting'),
            (10,  0.15, 'Sprouting'),
            (15,  0.25, 'Emergence'),
            (20,  0.38, 'Early Vegetative'),
            (30,  0.60, 'Active Vegetative'),
            (40,  0.78, 'Peak Vegetative'),
            (50,  0.85, 'Tuber Initiation'),
            (60,  0.86, 'Early Tuber Bulking'),
            (70,  0.85, 'Peak Tuber Bulking'),
            (85,  0.80, 'Late Tuber Bulking'),
            (95,  0.68, 'Early Maturity'),
            (105, 0.50, 'Senescence'),
            (110, 0.35, 'Harvest Ready'),
        ],

        # ================================================================
        # PERENNIAL FRUITS
        # ================================================================

        'Banana': [
            # Evergreen — high NDVI throughout; gradual rise as plant matures
            (0,   0.45, 'Planting / Ratoon'),
            (30,  0.60, 'Vegetative Establishment'),
            (60,  0.72, 'Active Growth'),
            (90,  0.80, 'Grand Growth'),
            (120, 0.85, 'Shooting'),
            (150, 0.88, 'Bunch Development'),
            (180, 0.88, 'Bunch Filling'),
            (210, 0.85, 'Pre-Harvest'),
            (240, 0.82, 'Harvest Period'),
            (270, 0.80, 'Ratoon Growth'),
            (330, 0.85, 'Peak Season'),
        ],

        'Grapes': [
            # Dormant after pruning → rapid flush → fruiting → senescence
            (0,   0.10, 'Post-Pruning / Dormant'),
            (15,  0.22, 'Bud Burst'),
            (30,  0.40, 'Shoot Growth'),
            (50,  0.62, 'Rapid Vegetative'),
            (70,  0.75, 'Pre-Flowering'),
            (85,  0.78, 'Flowering'),
            (100, 0.72, 'Berry Set'),
            (115, 0.68, 'Berry Development'),
            (130, 0.62, 'Veraison'),
            (145, 0.55, 'Ripening'),
            (155, 0.45, 'Harvest Ready'),
        ],

        'Papaya': [
            # Semi-perennial; NDVI relatively stable once established
            (0,   0.35, 'Transplanting'),
            (30,  0.50, 'Establishment'),
            (60,  0.65, 'Active Growth'),
            (90,  0.75, 'Pre-Flowering'),
            (120, 0.80, 'Flowering'),
            (150, 0.82, 'Fruit Set'),
            (180, 0.82, 'Fruit Development'),
            (210, 0.80, 'Fruit Maturation'),
            (240, 0.80, 'Peak Harvest'),
            (300, 0.78, 'Sustained Production'),
        ],

        'Pomegranate': [
            # Semi-deciduous — pruned, then flushes
            (0,   0.15, 'Post-Pruning'),
            (20,  0.28, 'Bud Break'),
            (40,  0.45, 'New Flush'),
            (60,  0.60, 'Vegetative Growth'),
            (80,  0.70, 'Pre-Flowering'),
            (100, 0.75, 'Flowering'),
            (120, 0.72, 'Fruit Set'),
            (140, 0.68, 'Fruit Development'),
            (160, 0.65, 'Fruit Coloring'),
            (180, 0.58, 'Harvest Ready'),
        ],

        # ================================================================
        # SUGARCANE
        # ================================================================

        'Sugarcane': [
            # Very long duration; builds up to very high NDVI
            (0,   0.15, 'Germination'),
            (20,  0.25, 'Seedling'),
            (40,  0.40, 'Early Tillering'),
            (60,  0.58, 'Active Tillering'),
            (90,  0.72, 'Grand Growth Start'),
            (120, 0.82, 'Grand Growth'),
            (150, 0.88, 'Peak Grand Growth'),
            (180, 0.90, 'Cane Elongation'),
            (210, 0.88, 'Maturation Start'),
            (240, 0.82, 'Ripening'),
            (270, 0.75, 'Late Ripening'),
            (300, 0.65, 'Pre-Harvest'),
            (330, 0.52, 'Harvest Ready'),
        ],

        # ================================================================
        # CATCHALL
        # ================================================================

        'Others': [
            # Generic moderate-canopy crop curve — conservative estimate
            (0,   0.12, 'Germination / Establishment'),
            (15,  0.25, 'Seedling'),
            (30,  0.42, 'Early Vegetative'),
            (50,  0.60, 'Active Vegetative'),
            (70,  0.72, 'Peak Vegetative'),
            (85,  0.75, 'Flowering / Fruiting'),
            (100, 0.70, 'Maturation'),
            (115, 0.55, 'Late Maturity'),
            (120, 0.38, 'Harvest Ready'),
        ],
    }

    # ========================================================================
    # YIELD PARAMETERS  (India-specific, tons per hectare)
    # ========================================================================
    # Sources: ICAR, Directorate of Economics & Statistics (MoAFW),
    #          state agriculture department reports
    # price_per_quintal: Approximate MSP or market price (₹/quintal)
    # ========================================================================

    YIELD_PARAMETERS = {

        # ----- CEREALS & MILLETS -----

        'Bajra': {
            'baseline_yield': 0.8,
            'typical_yield':  1.5,
            'optimal_yield':  2.5,
            'max_yield':      4.0,
            'price_per_quintal': 2350,
            'unit': 'tons'
        },
        'Jowar': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  2.0,
            'max_yield':      3.5,
            'price_per_quintal': 2970,
            'unit': 'tons'
        },
        'Maize': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      8.0,
            'price_per_quintal': 1850,
            'unit': 'tons'
        },
        'Rice': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      7.0,
            'price_per_quintal': 2183,
            'unit': 'tons'
        },
        'Wheat': {
            'baseline_yield': 2.5,
            'typical_yield':  3.5,
            'optimal_yield':  5.0,
            'max_yield':      7.5,
            'price_per_quintal': 2275,
            'unit': 'tons'
        },

        # ----- OILSEEDS -----

        'Groundnut': {
            'baseline_yield': 1.0,
            'typical_yield':  1.8,
            'optimal_yield':  2.8,
            'max_yield':      4.0,
            'price_per_quintal': 6377,
            'unit': 'tons'
        },
        'Mustard': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      2.5,
            'price_per_quintal': 5650,
            'unit': 'tons'
        },
        'Soyabean': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      3.0,
            'price_per_quintal': 4600,
            'unit': 'tons'
        },
        'Sunflower': {
            'baseline_yield': 0.6,
            'typical_yield':  1.0,
            'optimal_yield':  1.5,
            'max_yield':      2.5,
            'price_per_quintal': 6760,
            'unit': 'tons'
        },
        'Tobacco': {
            'baseline_yield': 1.0,
            'typical_yield':  1.6,
            'optimal_yield':  2.2,
            'max_yield':      3.0,
            'price_per_quintal': 16000,  # FCV tobacco; premium price
            'unit': 'tons'
        },

        # ----- PULSES -----

        'Gram': {
            'baseline_yield': 0.8,
            'typical_yield':  1.2,
            'optimal_yield':  1.8,
            'max_yield':      2.5,
            'price_per_quintal': 5440,
            'unit': 'tons'
        },
        'Tur': {
            'baseline_yield': 0.6,
            'typical_yield':  1.0,
            'optimal_yield':  1.6,
            'max_yield':      2.5,
            'price_per_quintal': 7000,
            'unit': 'tons'
        },

        # ----- FIBER -----

        'Cotton': {
            'baseline_yield': 1.8,
            'typical_yield':  2.5,
            'optimal_yield':  3.5,
            'max_yield':      5.0,
            'price_per_quintal': 6620,
            'unit': 'tons'
        },

        # ----- VEGETABLES -----

        'Cabbage': {
            'baseline_yield': 15.0,
            'typical_yield':  25.0,
            'optimal_yield':  35.0,
            'max_yield':      50.0,
            'price_per_quintal': 800,
            'unit': 'tons'
        },
        'Chilli': {
            'baseline_yield': 1.0,    # Dry chilli
            'typical_yield':  2.0,
            'optimal_yield':  3.5,
            'max_yield':      5.0,
            'price_per_quintal': 8000,
            'unit': 'tons'
        },
        'Onion': {
            'baseline_yield': 12.0,
            'typical_yield':  18.0,
            'optimal_yield':  25.0,
            'max_yield':      40.0,
            'price_per_quintal': 1200,
            'unit': 'tons'
        },
        'Potato': {
            'baseline_yield': 15.0,
            'typical_yield':  22.0,
            'optimal_yield':  30.0,
            'max_yield':      45.0,
            'price_per_quintal': 1000,
            'unit': 'tons'
        },

        # ----- PERENNIAL FRUITS -----

        'Banana': {
            'baseline_yield': 20.0,
            'typical_yield':  35.0,
            'optimal_yield':  50.0,
            'max_yield':      70.0,
            'price_per_quintal': 1500,
            'unit': 'tons'
        },
        'Grapes': {
            'baseline_yield': 10.0,
            'typical_yield':  20.0,
            'optimal_yield':  30.0,
            'max_yield':      40.0,
            'price_per_quintal': 4000,
            'unit': 'tons'
        },
        'Papaya': {
            'baseline_yield': 25.0,
            'typical_yield':  45.0,
            'optimal_yield':  65.0,
            'max_yield':      80.0,
            'price_per_quintal': 1200,
            'unit': 'tons'
        },
        'Pomegranate': {
            'baseline_yield': 5.0,
            'typical_yield':  10.0,
            'optimal_yield':  16.0,
            'max_yield':      22.0,
            'price_per_quintal': 8000,
            'unit': 'tons'
        },

        # ----- SUGARCANE -----

        'Sugarcane': {
            'baseline_yield': 50.0,
            'typical_yield':  70.0,
            'optimal_yield':  90.0,
            'max_yield':      120.0,
            'price_per_quintal': 340,   # FRP (Fair & Remunerative Price)
            'unit': 'tons'
        },

        # ----- CATCHALL -----

        'Others': {
            'baseline_yield': 1.0,
            'typical_yield':  2.0,
            'optimal_yield':  3.0,
            'max_yield':      5.0,
            'price_per_quintal': 2000,
            'unit': 'tons'
        },
    }

    # ========================================================================
    # CRITICAL GROWTH STAGES
    # Stages where NDVI deficit has the highest impact on final yield.
    # Used by performance_analyzer to weight stage-specific health penalties.
    # ========================================================================

    CRITICAL_STAGES = {
        'Bajra':       ['Heading', 'Grain Filling'],
        'Banana':      ['Bunch Development', 'Bunch Filling'],
        'Cabbage':     ['Head Development', 'Head Filling'],
        'Chilli':      ['Flowering', 'Fruit Set'],
        'Cotton':      ['Peak Flowering', 'Boll Development'],
        'Gram':        ['Flowering', 'Pod Formation'],
        'Grapes':      ['Berry Set', 'Berry Development'],
        'Groundnut':   ['Pegging', 'Pod Development'],
        'Jowar':       ['Heading', 'Early Grain Fill'],
        'Maize':       ['Silking/Pollination', 'Early Grain Fill'],
        'Mustard':     ['Peak Flowering', 'Pod Formation'],
        'Onion':       ['Bulb Development', 'Bulb Maturation'],
        'Others':      ['Peak Vegetative', 'Maturation'],
        'Papaya':      ['Fruit Set', 'Fruit Development'],
        'Pomegranate': ['Flowering', 'Fruit Development'],
        'Potato':      ['Tuber Initiation', 'Peak Tuber Bulking'],
        'Rice':        ['Flowering', 'Early Grain Fill'],
        'Soyabean':    ['Peak Flowering', 'Pod Development'],
        'Sugarcane':   ['Grand Growth', 'Cane Elongation'],
        'Sunflower':   ['Flowering', 'Seed Filling'],
        'Tobacco':     ['Peak Canopy', 'Mature Leaf'],
        'Tur':         ['Flowering', 'Pod Formation'],
        'Wheat':       ['Flowering', 'Early Grain Fill'],
    }

    # ========================================================================
    # CANOPY TYPE — affects how health score expected NDVI peak is set
    # LOW: spreading/sparse crops (Groundnut, Bajra, Cabbage, Onion)
    # MEDIUM: moderate canopy crops (Gram, Mustard, Chilli, Jowar, Others)
    # HIGH: dense/irrigated crops (Rice, Wheat, Maize, Sugarcane, Cotton)
    # PERENNIAL: always-on canopy (Banana, Papaya, Pomegranate, Grapes)
    # ========================================================================

    CANOPY_TYPE = {
        'Bajra':       'MEDIUM',
        'Banana':      'PERENNIAL',
        'Cabbage':     'LOW',
        'Chilli':      'MEDIUM',
        'Cotton':      'HIGH',
        'Gram':        'MEDIUM',
        'Grapes':      'PERENNIAL',
        'Groundnut':   'LOW',
        'Jowar':       'MEDIUM',
        'Maize':       'HIGH',
        'Mustard':     'HIGH',
        'Onion':       'LOW',
        'Others':      'MEDIUM',
        'Papaya':      'PERENNIAL',
        'Pomegranate': 'PERENNIAL',
        'Potato':      'HIGH',
        'Rice':        'HIGH',
        'Soyabean':    'HIGH',
        'Sugarcane':   'HIGH',
        'Sunflower':   'MEDIUM',
        'Tobacco':     'HIGH',
        'Tur':         'HIGH',
        'Wheat':       'HIGH',
    }

    # Expected cumulative NDVI (sum of ~10 scene observations across a season)
    # Used by performance_analyzer._estimate_yield_potential()
    # Derived from: peak_ndvi × number_of_peak_weeks + ramp-up + senescence
    EXPECTED_CUMULATIVE_NDVI = {
        'Bajra':       5.5,
        'Banana':      8.0,   # High because perennial stays elevated
        'Cabbage':     5.0,
        'Chilli':      6.5,
        'Cotton':      7.0,
        'Gram':        6.5,
        'Grapes':      5.5,
        'Groundnut':   5.5,
        'Jowar':       6.0,
        'Maize':       7.0,
        'Mustard':     7.0,
        'Onion':       5.5,
        'Others':      6.0,
        'Papaya':      7.5,
        'Pomegranate': 5.5,
        'Potato':      6.5,
        'Rice':        7.5,
        'Soyabean':    7.0,
        'Sugarcane':   8.5,   # Very long, very high
        'Sunflower':   6.0,
        'Tobacco':     7.0,
        'Tur':         7.5,
        'Wheat':       7.5,
    }

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    @classmethod
    def get_expected_ndvi(cls, crop: str, days_since_sowing: int) -> Tuple[float, str]:
        """
        Get expected NDVI value for a crop at specific days since sowing
        via linear interpolation between the defined curve points.

        Args:
            crop: Crop name (must match EXPECTED_NDVI_CURVES keys)
            days_since_sowing: Days elapsed since sowing / transplanting

        Returns:
            Tuple of (expected_ndvi: float, stage_name: str)
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            logger.warning(f"Crop '{crop}' not found in growth curves — using 'Others'")
            crop = 'Others'

        curve = cls.EXPECTED_NDVI_CURVES[crop]

        # Before first defined point
        if days_since_sowing <= curve[0][0]:
            return curve[0][1], curve[0][2]

        # After last defined point
        if days_since_sowing >= curve[-1][0]:
            return curve[-1][1], curve[-1][2]

        # Linear interpolation between surrounding points
        for i in range(len(curve) - 1):
            day1, ndvi1, stage1 = curve[i]
            day2, ndvi2, stage2 = curve[i + 1]

            if day1 <= days_since_sowing <= day2:
                if day2 == day1:
                    return ndvi1, stage1

                progress = (days_since_sowing - day1) / (day2 - day1)
                expected_ndvi = ndvi1 + (ndvi2 - ndvi1) * progress
                stage = stage1 if progress < 0.5 else stage2

                return float(expected_ndvi), stage

        return curve[-1][1], curve[-1][2]

    @classmethod
    def get_expected_curve(cls, crop: str, num_points: int = 100) -> np.ndarray:
        """
        Get the full expected NDVI curve for a crop as a smoothed array.

        Args:
            crop: Crop name
            num_points: Number of interpolated points

        Returns:
            np.ndarray of shape (num_points, 2) — columns: [days, expected_ndvi]
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CROP_DURATIONS:
            crop = 'Others'

        duration = cls.CROP_DURATIONS[crop]['typical_days']
        days = np.linspace(0, duration, num_points)

        ndvi_values = [cls.get_expected_ndvi(crop, int(d))[0] for d in days]

        return np.column_stack([days, ndvi_values])

    @classmethod
    def get_peak_ndvi(cls, crop: str) -> float:
        """
        Return the maximum expected NDVI for a crop from its growth curve.
        Used by performance_analyzer as the crop-specific 'expected_peak'.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            logger.warning(f"Crop '{crop}' not found — defaulting peak NDVI to 0.75")
            return 0.75

        return float(max(ndvi for _, ndvi, _ in cls.EXPECTED_NDVI_CURVES[crop]))

    @classmethod
    def get_expected_avg_ndvi(cls, crop: str) -> float:
        """
        Return the expected average NDVI across a full season for a crop.
        Used by performance_analyzer as the crop-specific 'expected_avg'.
        Calculated as simple average of all curve-point NDVI values.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.EXPECTED_NDVI_CURVES:
            return 0.60

        values = [ndvi for _, ndvi, _ in cls.EXPECTED_NDVI_CURVES[crop]]
        return float(np.mean(values))

    @classmethod
    def get_expected_cumulative_ndvi(cls, crop: str) -> float:
        """
        Return expected cumulative NDVI (sum across ~10 observations in a season).
        Used by performance_analyzer._estimate_yield_potential() instead of
        the old hardcoded 7.0 value.
        """
        crop = cls._normalize_crop_name(crop)
        return cls.EXPECTED_CUMULATIVE_NDVI.get(crop, 6.5)

    @classmethod
    def get_crop_duration(cls, crop: str) -> int:
        """Return typical crop duration in days from sowing to harvest."""
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CROP_DURATIONS:
            return 120

        return cls.CROP_DURATIONS[crop]['typical_days']

    @classmethod
    def get_canopy_type(cls, crop: str) -> str:
        """
        Return canopy type: 'LOW', 'MEDIUM', 'HIGH', or 'PERENNIAL'.
        Used by performance_analyzer to scale health score expectations.
        """
        crop = cls._normalize_crop_name(crop)
        return cls.CANOPY_TYPE.get(crop, 'MEDIUM')

    @classmethod
    def is_critical_stage(cls, crop: str, stage_name: str) -> bool:
        """Check if a growth stage name is critical for yield impact."""
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.CRITICAL_STAGES:
            return False

        return any(crit in stage_name for crit in cls.CRITICAL_STAGES[crop])

    @classmethod
    def get_yield_params(cls, crop: str) -> Dict:
        """
        Return yield parameters dict for a crop.
        Falls back to 'Others' if crop not found.
        """
        crop = cls._normalize_crop_name(crop)

        if crop not in cls.YIELD_PARAMETERS:
            logger.warning(f"Yield parameters not found for '{crop}' — using 'Others'")
            return cls.YIELD_PARAMETERS['Others'].copy()

        return cls.YIELD_PARAMETERS[crop].copy()

    @classmethod
    def validate_crop(cls, crop: str) -> bool:
        """Return True if crop is fully defined in this module."""
        crop = cls._normalize_crop_name(crop)
        return crop in cls.EXPECTED_NDVI_CURVES

    @classmethod
    def get_all_crops(cls) -> List[str]:
        """Return sorted list of all supported crop names."""
        return sorted(cls.EXPECTED_NDVI_CURVES.keys())

    @classmethod
    def get_crop_info(cls, crop: str) -> Dict:
        """
        Return a combined info dict for a crop including duration,
        canopy type, peak NDVI, expected avg NDVI, and yield params.
        Useful for logging / debugging.
        """
        crop = cls._normalize_crop_name(crop)

        if not cls.validate_crop(crop):
            crop = 'Others'

        return {
            'crop': crop,
            'duration': cls.CROP_DURATIONS.get(crop, {}),
            'canopy_type': cls.get_canopy_type(crop),
            'peak_ndvi': cls.get_peak_ndvi(crop),
            'expected_avg_ndvi': cls.get_expected_avg_ndvi(crop),
            'expected_cumulative_ndvi': cls.get_expected_cumulative_ndvi(crop),
            'yield_params': cls.get_yield_params(crop),
            'critical_stages': cls.CRITICAL_STAGES.get(crop, []),
        }

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    @classmethod
    def _normalize_crop_name(cls, crop: str) -> str:
        """
        Normalize crop name to handle minor spelling variants used by
        the ML model (e.g. 'Soybean' vs 'Soyabean').
        Extend this dict as new variants are encountered.
        """
        _ALIASES = {
            'Soybean':    'Soyabean',
            'soybean':    'Soyabean',
            'soyabean':   'Soyabean',
            'SOYABEAN':   'Soyabean',
            'cotton':     'Cotton',
            'wheat':      'Wheat',
            'rice':       'Rice',
            'maize':      'Maize',
            'bajra':      'Bajra',
            'jowar':      'Jowar',
            'tur':        'Tur',
            'gram':       'Gram',
            'groundnut':  'Groundnut',
            'mustard':    'Mustard',
            'sunflower':  'Sunflower',
            'tobacco':    'Tobacco',
            'sugarcane':  'Sugarcane',
            'onion':      'Onion',
            'potato':     'Potato',
            'cabbage':    'Cabbage',
            'chilli':     'Chilli',
            'banana':     'Banana',
            'grapes':     'Grapes',
            'papaya':     'Papaya',
            'pomegranate': 'Pomegranate',
            'others':     'Others',
        }
        return _ALIASES.get(crop, crop)

PipelineConfig.validate_config()
