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
        if errors:
            raise ValueError(f"Config errors: {errors}")
        return True


PipelineConfig.validate_config()