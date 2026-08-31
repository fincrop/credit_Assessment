"""
Pipeline Configuration
======================
Central configuration for the satellite-based agronomic risk-index pipeline
(index_v5). Package root is ``backend/Credit_assessment``.

Continuous path: season-aware lookback (Kharif / Rabi / Zaid anchors),
RiskIndexEngine sub-index weights, SAR/signal/phenology/weather pillar knobs.
"""

from pathlib import Path
from typing import Union
import os

# Directory containing main.py / config.py / api/ (Docker WORKDIR=/app and local cwd).
PACKAGE_ROOT = Path(__file__).resolve().parent


def _resolve_repo_root(package_root: Path) -> Path:
    """Monorepo root when layout is ``…/backend/Credit_assessment``; else package root.

    In Docker the image context is only ``backend/Credit_assessment`` (WORKDIR ``/app``),
    so ``parents[1]`` does not exist — fall back to PACKAGE_ROOT.
    """
    # Local/dev monorepo: <repo>/backend/Credit_assessment
    if (
        package_root.name == "Credit_assessment"
        and package_root.parent.name == "backend"
        and len(package_root.parents) > 1
    ):
        return package_root.parents[1]
    return package_root


# Monorepo root (contains frontend/ and backend/) when present; otherwise PACKAGE_ROOT.
REPO_ROOT = _resolve_repo_root(PACKAGE_ROOT)


def resolve_package_path(path: Union[str, Path]) -> Path:
    """Resolve a path relative to PACKAGE_ROOT; absolute paths are unchanged."""
    p = Path(path)
    if p.is_absolute():
        return p
    return (PACKAGE_ROOT / p).resolve()


# Tier-1 XGBoost bundle (147 features, 18 crops, spatially-blocked CV).
# Override via env CROP_MODEL_PATH if needed.
DEFAULT_CROP_MODEL_PATH = "models/crop_classifier_tier1_v1.joblib"


def crop_classification_enabled(default: bool = True) -> bool:
    """Read ENABLE_CROP_CLASSIFICATION (true unless explicitly disabled)."""
    raw = os.environ.get(
        "ENABLE_CROP_CLASSIFICATION",
        "true" if default else "false",
    ).strip().lower()
    return raw in ("1", "true", "yes")


class PipelineConfig:
    """Central configuration for the pipeline"""

    # ========================================================================
    # SEASON DEFINITIONS  (2 seasons only — Kharif & Rabi)
    # ========================================================================
    # Calendar windows (used by seasonal helpers / crop lists):
    #   Kharif:  15 May → 15 Oct
    #   Rabi:    15 Oct → 15 May
    # Continuous satellite lookback uses SEASON_SNAP_ANCHORS
    # (15 Jun / 15 Oct / 15 Feb) — agricultural sowing anchors.
    # ========================================================================

    SEASONS = {
        'kharif': {
            'start_month': 5,
            'start_day':   15,
            'end_month':   10,
            'end_day':     15,
            'description': 'Monsoon season — 15 May to 15 Oct',
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
            'description': 'Winter/Spring season — 15 Oct to 15 May',
            'primary_crops': [
                'Wheat', 'Gram', 'Mustard', 'Potato', 'Onion',
                'Sunflower', 'Tobacco', 'Chilli', 'Cabbage', 'Grapes',
            ],
        },
    }

    # Rolling continuous-window snap anchors — Kharif / Rabi / Zaid.
    SEASON_SNAP_ANCHORS = (
        (6, 15),   # Kharif sowing anchor
        (10, 15),  # Rabi sowing anchor
        (2, 15),   # Zaid sowing anchor
    )

    # STAC / continuous cloud caps (%). Prefer these over ad-hoc literals.
    MAX_CLOUD_COVER_KHARIF = 80.0      # Jun–Oct STAC day filter
    MAX_CLOUD_COVER_RABI = 60.0        # Nov–May STAC day filter
    MAX_CLOUD_COVER_CONTINUOUS = 70.0  # GEE continuous collection filter

    # ========================================================================
    # ANALYSIS WINDOW (advisory — continuous path uses CONTINUOUS_LOOKBACK_YEARS)
    # ========================================================================

    NUM_SEASONS        = 6   # advisory only for continuous path
    MAX_YEARS_BACK     =  4   # advisory safety cap

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

    MAX_CLOUD_COVER       = 60.0   # legacy alias / docs; live caps: MAX_CLOUD_COVER_* above
    MIN_VALID_PIXEL_RATIO = 0.20   # minimum fraction of valid pixels per scene

    IDEAL_GAP_DAYS = 7    # ~1 scene/week
    MAX_GAP_DAYS   = 21   # warn if any gap > 3 weeks

    TARGET_RESOLUTION_M = 10   # all bands resampled to 10m

    # Continuous 3-year Sentinel-2: fixed calendar bins; lowest-cloud STAC item per bin, else NaN.
    CONTINUOUS_SCENE_INTERVAL_DAYS = 10
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
    # Pause between calendar-year GEE/STAC segments. 0 = fastest (was hardcoded 10s on GEE).
    # Override with env SATELLITE_INTER_YEAR_PAUSE_SEC if Earth Engine throttles.
    SATELLITE_INTER_YEAR_PAUSE_SEC = float(
        __import__("os").environ.get("SATELLITE_INTER_YEAR_PAUSE_SEC", "0") or "0"
    )

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
    # PILLAR 1 — signal / SAR / smoothing (index_v5)
    # ========================================================================
    INDEX_SET_OPTICAL = [
        "NDVI", "EVI", "NDMI", "NDWI", "PSRI", "NDRE",
        "MSAVI2", "NIRv", "LSWI", "GCVI", "kNDVI",
    ]
    ENABLE_INDEX_GCVI = True
    ENABLE_INDEX_KNDVI = True

    # ── Vegetation signal (VS) construction ─────────────────────────────
    #
    # SIGNAL_VERSION is stamped onto every assessment. Bump it whenever the
    # normalisation, backbone or weights change, so a stored score can always be
    # traced to the signal definition that produced it.
    #
    # v2 (2026-08) changed two things from v1:
    #
    #   1. Normalisation: per-parcel min-max -> FIXED physical ranges.
    #      v1 rescaled each index over the parcel's own 3-year extremes, so every
    #      parcel — fertile, barren or paved — produced a series spanning ~0 to 1.
    #      Absolute thresholds applied to that are meaningless, `peak_cvi` was not
    #      comparable between farms, and noise on dead ground was stretched into
    #      apparent crop cycles.
    #
    #   2. Backbone: kNDVI -> NDVI.
    #      kNDVI = tanh(NDVI^2) discards the SIGN of NDVI: open water at -0.30 and
    #      sparse crop at +0.30 both map to 0.0876. Carrying that at weight 0.50
    #      made the detection signal unable to distinguish water from vegetation.
    #      kNDVI is still computed and stored — it is a fine vigor index — it is
    #      just not the discrimination backbone.
    SIGNAL_VERSION = "signal_v2_fixed_range"
    SIGNAL_NORMALIZATION = "fixed_range"      # "fixed_range" | "per_parcel_minmax" (legacy)
    SIGNAL_COMPOSITE_BACKBONE = "NDVI"
    SIGNAL_COMPOSITE_WEIGHTS = {"NDVI": 0.50, "EVI": 0.30, "NDMI": 0.20}
    YIELD_POTENTIAL_INDEX = "NIRv"

    # Physical bounds used by the fixed-range normaliser, as (lo, hi).
    #
    # These are the established value ranges each index takes over land, not
    # tuned parameters — chosen so the endpoints correspond to recognisable
    # ground conditions rather than to anything in our data:
    #   NDVI  -0.20 open water / non-vegetated .. 0.90 dense closed canopy
    #   EVI   -0.10 .. 0.80  (EVI runs lower than NDVI at the top of the range)
    #   NDMI  -0.50 dry/bare .. 0.60 high canopy moisture
    #   kNDVI  0.00 .. 0.76  (= tanh(1); retained for completeness, not in the
    #                         default blend)
    # Values outside a range are clipped, which is meaningful: NDVI -0.4 over
    # water clamps to 0.0, i.e. "no vegetation".
    INDEX_PHYSICAL_RANGES = {
        "NDVI":   (-0.20, 0.90),
        "EVI":    (-0.10, 0.80),
        "NDMI":   (-0.50, 0.60),
        "kNDVI":  (0.00, 0.76),
        "NIRv":   (0.00, 0.45),
        "LSWI":   (-0.50, 0.60),
        "MSAVI2": (-0.20, 0.90),
        "GCVI":   (0.00, 8.00),
        "NDRE":   (-0.20, 0.70),
        "PSRI":   (-0.20, 0.40),
        "NDWI":   (-1.00, 1.00),
    }

    SIGNAL_SMOOTHER = "whittaker"  # "whittaker" | "savgol"
    WHITTAKER_LAMBDA = 8.0
    WHITTAKER_DIFF_ORDER = 2
    SAVGOL_WINDOW = 7
    SAVGOL_POLYORDER = 2

    # Scenes fetched per calendar year from GEE before binning.
    #
    # Was a hardcoded 50 applied WITHOUT a sort, so it took the chronologically
    # first 50 and discarded the rest of the year — the source of the 240-day
    # "cloud gaps" that pushed real farms into INSUFFICIENT_DATA. Downstream
    # keeps at most one scene per 10-day bin (~37/year), so this only needs to
    # sit comfortably above that to give every bin a candidate. Higher values
    # cost GEE aggregations; lower values silently lose coverage.
    GEE_MAX_SCENES_PER_YEAR = 120

    CLOUD_MASK_VERSION = "csplus_scl_v2"
    USE_CLOUD_SCORE_PLUS = True
    CLOUD_SCORE_PLUS_BAND = "cs_cdf"
    CLOUD_SCORE_PLUS_THRESHOLD = 0.60

    BIN_QUALITY_MIN_AREA_HA = 0.20
    BIN_MIN_VALID_PIXEL_RATIO = 0.20
    # Assumed valid-pixel fraction when the imagery backend does not report one
    # (the GEE path uses bestEffort and returns no pixel count). Deliberately
    # below 1.0: treating an unknown as a perfect observation inflates the
    # confidence gate. signal_quality_summary records how many bins used this
    # assumption vs a measured value.
    BIN_QUALITY_UNKNOWN_VPF = 0.85

    SAR_ENABLED = True
    SAR_COLLECTION = "COPERNICUS/S1_GRD"
    SAR_POLARISATIONS = ("VV", "VH")
    SAR_ORBIT_PREFERENCE = "ASCENDING"
    SAR_INPUT_IS_DB = True
    SAR_FUSION_MIN_PAIRS = 6
    SAR_FUSION_QUALITY_FLOOR = 0.0

    SIGNAL_SOURCE_LABELS = {0: "optical", 1: "fused", 2: "sar", 3: "imputed"}

    CONTINUOUS_LOOKBACK_YEARS = 3
    SEASON_AWARE_WINDOW = True

    # ========================================================================
    # PILLAR 2 — phenology engine
    # ========================================================================
    # Double-logistic phenology refinement. Set False (or env PHENO_FIT_DISABLE=1)
    # where the platform's LAPACK build is unreliable — curve_fit can abort the
    # process at the native level, which no Python except can catch, and losing
    # the whole assessment to an optional refinement step is not an acceptable
    # trade. Cycles still carry walked sowing/harvest dates when this is off.
    PHENO_FIT_ENABLED = True
    PHENO_AMP_FRACTION = 0.20
    PHENO_FIT_MIN_R2 = 0.60
    PHENO_PREMONSOON_LOW = 0.30
    PHENO_POSTMONSOON_HIGH = 0.45
    LONG_DURATION_PAD_DAYS = 60

    # ========================================================================
    # PILLAR 3 — stress & yield-potential / peer benchmark
    # ========================================================================
    PEER_BENCHMARK_ENABLE = True
    PEER_BENCHMARK_MIN_COHORT_N = 20
    PEER_BENCHMARK_USE_PRIORS = False
    STRESS_BASELINE_Z_MEDIUM = 1.5
    STRESS_BASELINE_Z_HIGH = 2.5
    WATER_STRESS_Z = 1.8

    # Legacy performance anomaly knobs (still read by performance_analyzer)
    PERFORMANCE_ANOMALY_IQR_FACTOR = 2.0
    PERFORMANCE_ANOMALY_SUSTAINED_MIN_SCENES = 4
    PERFORMANCE_VOLATILITY_CV_MEDIUM = 0.48
    PERFORMANCE_VOLATILITY_CV_HIGH = 0.62
    PERFORMANCE_IMPACT_HIGH_MAG_IQR_MULT = 1.25
    PERFORMANCE_STABILITY_DEDUCT_HIGH = 2.0
    PERFORMANCE_STABILITY_DEDUCT_MEDIUM = 1.0
    PERFORMANCE_STABILITY_DEDUCT_LOW = 0.35

    # ========================================================================
    # ML CLASSIFICATION — Feature extraction
    # ========================================================================

    ML_FEATURE_SCENES  = 15                            # chronological scenes
    ML_FEATURE_INDICES = ['NDVI_mean', 'EVI_mean', 'NDMI_mean']

    # Stage 5: when slicing scenes for a Stage-4 cycle, expand the date window by this
    # many days on each side (ML + temporal features only; season_results dates stay exact).
    CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS = 5

    # ========================================================================
    # LAND COVER GATE — is this parcel farmland at all?
    # ========================================================================
    # Runs after the satellite pull and before cycle detection. See
    # crop_analysis/land_cover_gate.py. Thresholds below are on RAW index
    # values, never the normalised composite.
    #
    # These are not tuned parameters — we have no ground truth to tune against.
    # Each corresponds to a documented surface behaviour, and the temporal
    # stream (which needs no external calibration) carries the decisions that
    # spectral evidence alone cannot support.
    # ── Declared-crop verification ───────────────────────────────────────
    # A registry crop name is self-reported and unverified. Rather than trusting
    # it (a wrong label swings 45% of the index) or ignoring it (which left the
    # ICAR reference curves and per-stage weather analysis permanently dead), we
    # check it against the phenology actually observed. See
    # crop_analysis/crop_verification.py.
    CROP_VERIFY_ENABLED = True
    # How far outside the reference duration band a cycle may fall and still
    # count as consistent. Real sowing dates vary with monsoon onset, and our
    # own dates are quantised to 10-day bins, so a tight band would reject
    # genuine matches.
    CROP_VERIFY_DURATION_TOLERANCE = 0.30
    # Peak canopy is checked as a FLOOR only — outperforming the reference curve
    # is not evidence against a declaration.
    CROP_VERIFY_PEAK_NDVI_TOLERANCE = 0.25
    CROP_VERIFY_MIN_CYCLES = 1
    CROP_VERIFY_MIN_CONSISTENT_SHARE = 0.5
    # Ceiling on the confidence a verified declaration may earn. A match is
    # corroboration, not measurement: wheat and mustard both run ~130 days in
    # rabi and both peak near 0.8, so agreement makes a declaration plausible
    # without establishing it. Sits above the 0.25 gate that unlocks
    # crop-specific scoring, and well below anything that reads as a detection.
    CROP_VERIFY_MAX_CONFIDENCE = 0.55

    # ── Parcel viability ─────────────────────────────────────────────────
    # Can this parcel be honestly measured at 10 m? See
    # assessment/parcel_viability.py. A Sentinel-2 pixel is 0.01 ha.
    #
    # A geometry audit over the live database found 60 of 113 parcels under 20
    # pixels and 24 under FIVE. At four pixels the AOI mean is mostly the
    # neighbouring field or road, whatever the boundary says.
    #
    # HARD floor = the minimum FUNDABLE plot size, set by lending policy at
    # 0.15 ha (15 pixels). Below this we decline rather than spend quota and
    # return a confident number about a plot nobody would lend against anyway.
    # This is a BUSINESS threshold; change it when the lending policy changes.
    PARCEL_MIN_PIXELS_HARD = 15          # 0.15 ha — fundable floor
    #
    # RELIABLE floor is a PHYSICAL threshold, and the two are different things.
    # For a roughly square parcel of N pixels the boundary ring is about
    # 4*sqrt(N)-4 pixels, and Sentinel-2 geolocation error is itself ~10 m — so
    # boundary pixels are contaminated by whatever is next door:
    #     15 px  -> ~11 boundary px  (~73% of the parcel)
    #     50 px  -> ~24 boundary px  (~48%)
    #    150 px  -> ~45 boundary px  (~30%)
    # There is no size at which this vanishes for smallholder plots at 10 m.
    # 50 px is where it stops dominating, so between the fundable floor and
    # here a parcel is scored but flagged and confidence-discounted, rather
    # than presented as though it were cleanly measured.
    PARCEL_MIN_PIXELS_RELIABLE = 50      # 0.50 ha
    PARCEL_MARGINAL_GATE_PENALTY = 0.85

    # Polygon area vs registered area. Among AgriStack-ingested parcels only 7
    # of 106 fell in this range; ratios spanned 0.022 to 1841 in both
    # directions, which is scatter, not a unit error — the polygon and the
    # registered area describe different parcels. That is an INGEST defect;
    # here we only record that they disagree, because we cannot know which is
    # right and guessing would move the error rather than remove it.
    PARCEL_AREA_RATIO_MIN = 0.8
    PARCEL_AREA_RATIO_MAX = 1.25

    # Confidence-gate discount when the score was measured over a SUBSTITUTED
    # footprint. Geometry QA failure is non-fatal: the collector falls back to a
    # circular buffer around the centroid, so the assessment may describe land
    # near the parcel rather than the parcel. Observed on a real farm — QA
    # failed on an area ratio of 3.21 and ~7 ha of surrounding fields stood in
    # for a 0.45 ha holding. The score is still produced (refusing would deny a
    # farmer over a data-entry problem) but must not read as confidently as one
    # measured over the real boundary.
    GEOMETRY_SUBSTITUTED_GATE_PENALTY = 0.85

    # ── Data sufficiency ─────────────────────────────────────────────────
    # Can we make any claim about this parcel? See assessment/data_sufficiency.py.
    #
    # Only bites when NO cycles were detected. The reasoning is physical, not
    # tuned: a contiguous unobserved stretch longer than the shortest crop cycle
    # could have hidden an entire season, so "no cycles" from such a record is
    # ignorance, not a finding. Reporting VERY_HIGH there denies a farmer credit
    # on the strength of the satellite's cloud luck.
    DATA_SUFFICIENCY_ENABLED = True
    # 0 = derive from CROP_CYCLE_MIN_DURATION_DAYS (the honest default: a gap
    # long enough to hide a whole cycle).
    DATA_SUFFICIENCY_BLIND_GAP_DAYS = 0
    DATA_SUFFICIENCY_MIN_OBSERVED_FRACTION = 0.35

    LANDCOVER_GATE_ENABLED = True

    # Minimum usable observations before a verdict is attempted. Below this the
    # parcel is FLAGGED as UNKNOWN, never rejected — we cannot reject a parcel
    # we were unable to look at.
    LANDCOVER_MIN_OBSERVATIONS = 8

    # Confidence bands (decision D-1).
    #   >= reject_confidence and non-agricultural  -> REJECTED, no score
    #   >= flag_confidence                          -> scored, flagged, gate discounted
    #   below                                       -> flagged as UNKNOWN
    LANDCOVER_REJECT_CONFIDENCE = 0.75
    LANDCOVER_FLAG_CONFIDENCE = 0.45
    # Data-confidence gate multiplier for a flagged parcel: a plot we are unsure
    # about must not score as if it were clean cropland.
    LANDCOVER_FLAG_GATE_PENALTY = 0.85

    # NDVI reference levels (raw scale).
    #   bare soil    below ~0.20 there is no meaningful canopy
    #   vegetated    above ~0.45 a canopy has clearly formed
    #   evergreen    above ~0.55 sustained year-round indicates woody cover
    LANDCOVER_NDVI_BARE_SOIL = 0.20
    LANDCOVER_NDVI_VEGETATED = 0.45
    LANDCOVER_NDVI_EVERGREEN = 0.55
    # Minimum p90-p10 NDVI swing for "this land is worked". Cropland greens up
    # and senesces; water, rock and rooftops do not.
    LANDCOVER_MIN_NDVI_AMPLITUDE = 0.18
    # A perennial planting essentially never returns to bare ground.
    LANDCOVER_EVERGREEN_BARE_FRACTION = 0.05

    # Fraction of observations that must be positive to call a surface
    # water / built-up. A majority, not a single date — one flooded scene is a
    # paddy transplant, not a lake.
    LANDCOVER_WATER_FRACTION = 0.60
    LANDCOVER_BUILTUP_FRACTION = 0.60
    # Bare Soil Index level indicating exposed soil or rock rather than a
    # cultivable surface. Note we do not attempt to finely separate a quarry
    # from a rooftop — the evidence does not support it and both are rejected
    # either way.
    LANDCOVER_BSI_BARE = 0.35

    # Third-party LULC (ESA WorldCover / Dynamic World). OFF pending rule P-6:
    # record the published per-class accuracy for cropland over South Asia
    # before relying on it. 10 m global products are weakest on sub-hectare
    # fields, which is exactly our population — so the headline global figure
    # must not be assumed to apply.
    # ========================================================================
    # PERENNIAL / PLANTATION DETECTION
    # ========================================================================
    # Orchards, banana and ratooned sugarcane never return to bare ground, so
    # the annual sow->peak->harvest logic cannot represent them: with no trough
    # the walked window always exceeds the duration cap and every candidate is
    # rejected. That produced zero cycles, which downstream reads as
    # crop_intensity 0 and fallow 1.0 — a mango grove scored as abandoned land.
    #
    # Thresholds describe a canopy that stays up year-round. Being permissive is
    # the right bias: a woodlot reaching this branch will score poorly on vigor
    # and stability regardless, whereas refusing to represent an orchard makes a
    # fundable farmer unscoreable (decision D-6).
    # ── Season assignment ────────────────────────────────────────────────
    # A cycle is assigned to the season it spent the MOST DAYS in, not the one
    # it happened to be sown in. Below this share no season dominates and the
    # cycle is labelled 'cross_season' (which the previous month-map logic could
    # never actually produce).
    SEASON_MIN_DOMINANT_SHARE = 0.55
    # Southern India's cropping calendar runs later than the north — the
    # north-east monsoon arrives Oct-Dec, shifting rabi. This is the coarsest
    # defensible regionalisation; it is applied only when latitude is known, and
    # a finer state-level calendar would be a clear improvement once available.
    SEASON_SOUTH_LATITUDE = 16.0
    SEASON_SOUTH_SHIFT_DAYS = 30

    PERENNIAL_DETECTION_ENABLED = True
    PERENNIAL_MIN_BINS = 18                # ~6 months of 10-day bins
    PERENNIAL_MIN_VEGETATION_VS = 0.50     # p10 of VS: a canopy is always present
    PERENNIAL_MAX_BARE_FRACTION = 0.05     # essentially never returns to bare soil
    PERENNIAL_MAX_AMPLITUDE = 0.30         # limited seasonal swing (vs a sown crop)

    # Sub-index scores when there is NO evidence of cultivation at all (zero
    # detected cycles). Deliberately low, not neutral: "we observed nothing
    # growing here" is a negative finding, not a missing one. Previously these
    # fell back to 50 (vigor) and 84 (stability, which rewards the absence of
    # stress events — and nothing stressful happens to a parking lot), so
    # barren land scored well on its two strongest pillars.
    VIGOR_NO_EVIDENCE_SCORE = 25.0
    STABILITY_NO_EVIDENCE_SCORE = 25.0

    LANDCOVER_USE_EXTERNAL_LULC = False
    LANDCOVER_EXTERNAL_LULC_SOURCE = None
    LANDCOVER_EXTERNAL_LULC_ACCURACY = None   # {"cropland_producers": .., "region": ..}

    # ========================================================================
    # CROP CYCLE DETECTION (continuous series -> sowing / harvest windows)
    # ========================================================================
    # Irrigated triple-crop (e.g. Rabi veg -> Zaid maize -> Kharif rice) needs
    # shorter per-cycle caps than sugarcane-style defaults; the old 420d max let
    # harvest "last resort" pick a trough months later and merge real cycles.
    # ── Thresholds on the VS scale (see SIGNAL_VERSION) ──────────────────
    #
    # These operate on the composite vegetation signal, NOT on raw NDVI. With
    # signal_v2's fixed-range normalisation the scale is physically meaningful
    # and identical on every parcel; representative surfaces map as follows
    # (NDVI shown for orientation):
    #
    #     open water            NDVI -0.30   VS 0.16
    #     concrete / rooftop    NDVI  0.05   VS 0.23
    #     dry bare soil         NDVI  0.12   VS 0.29
    #     moist / just-sown     NDVI  0.18   VS 0.36
    #     early vegetative      NDVI  0.32   VS 0.46
    #     mid vegetative        NDVI  0.45   VS 0.57
    #     full crop canopy      NDVI  0.72   VS 0.79
    #     dense forest          NDVI  0.85   VS 0.91
    #
    # ⚠ Under signal_v1 these numbers meant something entirely different: the
    # signal was rescaled to each parcel's own extremes, so VS was a within-plot
    # rank, not a canopy measure. The v1 values (baseline 0.30, peak 0.28) were
    # also INVERTED — the "returned to bare soil" floor sat ABOVE the "this is a
    # peak" floor, so any peak in [0.28, 0.30) terminated its own sow/harvest
    # walk immediately and was then rejected on duration.
    #
    # Do not retune these without re-checking them against the table above.

    # Rise from sowing baseline to peak required to call it a cycle.
    # 0.15 ~ bare soil (0.29) -> early vegetative (0.46): a canopy actually formed.
    CROP_CYCLE_MIN_NDVI_RISE = 0.15
    # "Back to bare ground" level, used to walk back to sowing and forward to
    # harvest. Set just above dry bare soil so a harvested field crosses it.
    CROP_CYCLE_MIN_BASELINE_CVI = 0.35
    # Minimum peak for a cycle to count as a crop. Just under mid-vegetative:
    # below this the parcel never developed a canopy worth calling a crop.
    CROP_CYCLE_MIN_PEAK_CVI = 0.55
    # Rise threshold on the VS scale. Previously READ BUT NEVER DEFINED, so the
    # detector silently fell back to max(0.08, MIN_NDVI_RISE * 0.88).
    CROP_CYCLE_MIN_CVI_RISE = 0.15
    # Hard floors for the relaxation passes. When too few cycles are found the
    # detector re-runs with looser gates; these bound how loose it may get.
    # The peak floor sits at early-vegetative (VS 0.46) — below that we would be
    # calling bare soil (0.29) or a rooftop (0.23) a crop peak, which is exactly
    # the over-detection the relaxation passes are prone to. Relaxation may make
    # us miss a marginal crop; it must never invent one.
    CROP_CYCLE_RELAXED_PEAK_FLOOR = 0.46
    CROP_CYCLE_RELAXED_RISE_FLOOR = 0.10
    # Density pass may go below the adaptive floor to catch shorter rabi /
    # pulse peaks (NDVI ~0.40) that never reach mid-vegetative VS 0.46.
    # Prominence + min_rise still reject barren wobble.
    CROP_CYCLE_DENSITY_PEAK_FLOOR = 0.38
    # NDVI-anchored fallback (the series plotted on the dashboard).
    # 0.38 NDVI is early vegetative — pulses and stressed cereals peak near 0.5.
    CROP_CYCLE_NDVI_PEAK_FLOOR = 0.38
    CROP_CYCLE_NDVI_MIN_PROMINENCE = 0.08
    CROP_CYCLE_NDVI_MIN_RISE = 0.08
    CROP_CYCLE_NDVI_MAX_HALF_DAYS = 120
    CROP_CYCLE_NDVI_MAX_DURATION_DAYS = 240
    # Minimum prominence: how far a peak must stand above its higher flanking
    # trough to count as a growth event rather than a wobble on a plateau.
    # There was no prominence test at all, so two noise wiggles 45 days apart on
    # a flat signal both qualified as crop peaks.
    CROP_CYCLE_MIN_PROMINENCE = 0.10
    CROP_CYCLE_MIN_DURATION_DAYS = 40
    # Raise to 360+ only for long-duration crops (e.g. sugarcane) on the same field.
    CROP_CYCLE_MAX_DURATION_DAYS = 195
    # Harvest triggers are only scanned this many calendar days after the CVI peak
    # (e.g. 10-day grid => ~13 bins). Prevents one crop from absorbing the next peaks.
    CROP_CYCLE_MAX_DAYS_AFTER_PEAK = 135
    # Minimum grid-point spacing between greenup events (~3 bins × interval_days).
    CROP_CYCLE_GREENUP_MIN_GRID_SEP = 3
    CROP_CYCLE_SUSTAINED_GROWTH_FRAC = 0.55
    # Merge two cycles only if overlap is large vs calendar AND vs shorter duration
    # (duplicate detections), not adjacent crops with fuzzy boundaries.
    # Higher ratio / min days = harder to merge adjacent candidates (keeps more
    # distinct rotations when harvest/sowing boundaries are fuzzy).
    CROP_CYCLE_MERGE_OVERLAP_MIN_DAYS = 60
    CROP_CYCLE_MERGE_OVERLAP_RATIO = 0.52

    # When strict CVI-greenup tracing yields zero cycles despite ≥~100 valid bins:
    # 1) adaptive second pass lowers gates using parcel-specific CVI percentiles +
    #    optional India eco-region priors from `utils.india_geo_context`.
    # 2) peak-anchored fallback places greenups near NDVI prominence peaks when
    #    monsoon/smoothing suppresses textbook greenups (common in Indo-Gangetic).
    CROP_CYCLE_ADAPTIVE_SECOND_PASS = True
    CROP_CYCLE_PEAK_ANCHORED_FALLBACK = True
    CROP_CYCLE_PERM_CONF_FLOOR = 10.0
    CROP_CYCLE_PERMISSIVE_DURATION_PAD = 52
    # Union CVI-greenup scans at multiple window sizes (captures sharper rises).
    CROP_CYCLE_MULTI_WINDOW_GREENUP = True
    # If validated cycles < ceil(years * EXPECTED_CYCLES_PER_YEAR), run one more
    # ultra-relaxed threshold + second peak scan (prominence scaled down).
    CROP_CYCLE_DENSITY_PASS = True
    CROP_CYCLE_EXPECTED_CYCLES_PER_YEAR = 1.5
    CROP_CYCLE_DENSITY_PEAK_PROMINENCE_SCALE = 0.58
    # CVI windows (grid bins) combined for greenup union when multi-window is on.
    CROP_CYCLE_GREENUP_WINDOWS = (5, 4, 3)
    # Min NDVI prominence (peak minus left trough) required for fallback peaks
    CROP_CYCLE_FALLBACK_PROMINENCE_FLOOR = 0.032
    # DB sowing_date: repeat anchor along rotation (~3 crops / yr), not crop-specific typical+30.
    CROP_CYCLE_HINT_STEP_DAYS = 118
    CROP_CYCLE_MAX_HINT_ANCHORS = 40

    # Sowing — NDVI-led (low baseline → rise over 2–3 bins); transplant path via EVI/NDMI
    CROP_CYCLE_SOW_BASELINE_MAX = 0.24
    CROP_CYCLE_SOW_CROSS_MIN = 0.27
    CROP_CYCLE_SOW_MIN_RISE_STEPS = 3
    CROP_CYCLE_SOW_NOISE_DROP_TOL = 0.018
    CROP_CYCLE_SOW_TRANSPLANT_BASELINE_MAX = 0.40
    CROP_CYCLE_SOW_TRANSPLANT_EVI_DELTA = 0.020
    CROP_CYCLE_SOW_TRANSPLANT_NDMI_DELTA = 0.014

    # Harvest — post-peak decline, cross low NDVI, then low plateau / minimum
    CROP_CYCLE_HARVEST_LOW_NDVI = 0.36
    CROP_CYCLE_HARVEST_DECLINE_STEPS = 3
    CROP_CYCLE_HARVEST_DECLINE_MIN_DROP = 0.018
    CROP_CYCLE_HARVEST_PEAK_DROP_FRAC = 0.06
    CROP_CYCLE_HARVEST_STABLE_MAX_STD = 0.022
    CROP_CYCLE_HARVEST_STABLE_RUN = 3

    # Chronological imputation grid: match `CONTINUOUS_SCENE_INTERVAL_DAYS` when
    # `detect_cycles(..., grid_step_days=...)` is passed from main (recommended).
    CYCLE_GRID_STEP_DAYS = 10
    # Only linear interpolation across NaN runs shorter than this (calendar days).
    CYCLE_IMPUTE_SHORT_GAP_MAX_DAYS = 48
    # Longer NaN runs use pre/post context (e.g. May plateau + Sep decline => hat in Kharif).
    CYCLE_IMPUTE_LONG_GAP_MIN_DAYS = 36
    CYCLE_IMPUTE_POST_DECLINE_LOOK = 6
    CYCLE_IMPUTE_DECLINE_DELTA = 0.028

    # ========================================================================
    # WEATHER PARAMETERS
    # ========================================================================

    WEATHER_PARAMETERS       = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "WS2M"]
    WEATHER_API_TIMEOUT      = 120
    WEATHER_API_MAX_RETRIES  = 3

    # Extreme-event thresholds: default is per-cycle percentiles (local climate + season).
    WEATHER_USE_DYNAMIC_THRESHOLDS = True
    WEATHER_DYNAMIC_MIN_OBS = 21
    WEATHER_DYNAMIC_HEAT_PERCENTILE = 91.0
    WEATHER_DYNAMIC_HEAT_DELTA_C = 1.8
    WEATHER_DYNAMIC_HEAT_FLOOR_C = 36.0
    WEATHER_DYNAMIC_HEAT_CAP_C = 44.0
    WEATHER_DYNAMIC_COLD_PERCENTILE = 10.0
    WEATHER_DYNAMIC_COLD_DELTA_C = 1.5
    WEATHER_DYNAMIC_COLD_FLOOR_C = 4.0
    WEATHER_DYNAMIC_COLD_CAP_C = 10.0
    WEATHER_HEAVY_RAIN_WET_PERCENTILE = 93.0
    WEATHER_HEAVY_RAIN_FLOOR_MM = 42.0
    WEATHER_HEAVY_3DAY_PERCENTILE = 90.0
    WEATHER_HEAVY_3DAY_FLOOR_MM = 85.0
    WEATHER_DROUGHT_DRY_PERCENTILE = 22.0
    WEATHER_DROUGHT_DAILY_FLOOR_MM = 0.25
    WEATHER_DROUGHT_DAILY_CEILING_MM = 6.0
    WEATHER_DROUGHT_MIN_DAY_FRAC_OF_CYCLE = 0.13
    WEATHER_DROUGHT_MIN_DAYS_FLOOR = 12
    WEATHER_DROUGHT_MIN_DAYS_CAP = 42
    WEATHER_DROUGHT_INTERVAL_FACTOR = 1.2

    HEATWAVE_MIN_DAYS = 3
    COLD_WAVE_MIN_DAYS = 3
    HEATWAVE_THRESHOLD_C = 40
    COLD_WAVE_THRESHOLD_C = 10
    HEAVY_RAIN_SINGLE_DAY_MM = 100
    HEAVY_RAIN_3DAY_MM = 200
    DROUGHT_MIN_DAYS = 30
    DROUGHT_DAILY_RAINFALL_MM = 2

    # ========================================================================
    # PILLAR 4 — weather engine (multi-source + resilience)
    # ========================================================================
    # IMD requires optional ``imdlib`` + local gridded files when enabled.
    WEATHER_SOURCE_PRIORITY = ["power"]  # e.g. ["imd","era5","chirps","power"]
    WEATHER_IMD_ENABLE = False
    WEATHER_IMD_DATA_DIR = None
    WEATHER_ERA5_ENABLE = False
    WEATHER_CHIRPS_ENABLE = False
    WEATHER_WET_DAY_MM = 2.5
    GDD_BASE_C = 10.0
    GDD_CAP_C = 35.0
    HEAT_STRESS_TMAX_C = 38.0
    COLD_STRESS_TMIN_C = 8.0
    MONSOON_ONSET_3DAY_MM = 15.0
    MONSOON_NORMAL_ONSET_DOY = 160
    RESILIENCE_DRY_SPELL_DAYS = 21
    RESILIENCE_HEAT_DAYS = 7
    RESILIENCE_VIGOR_LO = 0.30
    RESILIENCE_VIGOR_HI = 0.70

    # ========================================================================
    # PILLAR 5 — risk index (index_v5) — must sum to 100
    # ========================================================================
    SUBINDEX_WEIGHTS = {
        "landuse": 30.0,
        "vigor": 25.0,
        "stability": 20.0,
        "weather": 25.0,
    }
    CONFIDENCE_GATE_MIN = 0.60
    BENEFITS_BONUS_PER_FLAG = 2.0
    BENEFITS_BONUS_MAX = 4.0

    # Multi-farm orchestration (MultiFarmAssessor + FarmerAggregator)
    MULTI_FARM_ENABLED = True
    MULTI_FARM_MAX_PLOTS = 12  # cost guard: max plots scored per farmer
    TENURE_LEASE_FACTOR = 0.35  # cultivator-but-not-ROR-owner discount
    PORTFOLIO_BONUS_MAX = 8.0
    DIVERSIFICATION_BONUS_MAX = 5.0

    # Stability sub-index anomaly penalties (points deducted before gate)
    CREDIT_ANOMALY_PENALTY_HIGH = 1.8
    CREDIT_ANOMALY_PENALTY_MEDIUM = 0.55
    CREDIT_ANOMALY_PENALTY_LOW = 0.15
    CREDIT_ANOMALY_PENALTY_MAX = 16.0

    # ========================================================================
    # RISK CATEGORIES (index score bands)
    # ========================================================================

    RISK_THRESHOLDS = {
        'LOW':       (70, 101),
        'MEDIUM':    (50,  70),
        'HIGH':      (30,  50),
        'VERY_HIGH': ( 0,  30),
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
    # Point-only farms: buffer from √(area) with a small floor (~15 Sentinel-2
    # pixels). Legacy 0.5 km floor pulled in too much neighbour NDVI for
    # sub-hectare holdings. Set FIELD_BUFFER_LEGACY=1 to restore 0.5 km.

    MIN_FIELD_BUFFER_KM = 0.15
    FIELD_BUFFER_PADDING_KM = 0.02
    FIELD_BUFFER_MAX_KM = 2.0
    GEOMETRY_AREA_RATIO_MIN = 0.5
    GEOMETRY_AREA_RATIO_MAX = 2.0
    SNAP_LOGIC_VERSION = "v1_fixed_anchors"
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

    # Master kill switch for Stage 08 enrichment (SHAP / CF / Groq / Sarvam).
    # Set AI_ENRICHMENT_ENABLE=0 to skip the whole block without per-feature flags.
    AI_ENRICHMENT_ENABLE = _os.environ.get(
        "AI_ENRICHMENT_ENABLE", "1"
    ).strip().lower() not in ("0", "false", "no", "off")

    # Stuck RUNNING jobs older than this many minutes are marked FAILED (reaper).
    JOB_RUNNING_TIMEOUT_MINUTES = int(
        _os.environ.get("JOB_RUNNING_TIMEOUT_MINUTES", "45") or "45"
    )

    AI_CONFIG = {
        # Groq — English report generation
        "groq": {
            "api_key":    _os.environ.get("GROQ_API_KEY"),
            # Keep in sync with ai_integration/groq_report_generator._DEFAULT_MODEL.
            # This previously defaulted to "llama-3.1-70b-versatile", which Groq
            # has retired — an operator copying .env.example verbatim got a 404
            # on every call and silently fell back to the deterministic report.
            "model":      _os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            "api_url":    "https://api.groq.com/openai/v1/chat/completions",
            "timeout":    30,
            # NOTE: the generator reads GROQ_* env vars directly rather than this
            # block, so max_tokens here is advisory only (it sends 650).
            "max_tokens": 400,
            # Same as Sarvam: on when a key is present. GROQ_ENABLE=0 still
            # forces it off; GROQ_ENABLE=1 with no key stays off.
            "enabled":    (
                bool(_os.environ.get("GROQ_API_KEY"))
                if _os.environ.get("GROQ_ENABLE", "").strip().lower()
                not in ("0", "false", "no", "off")
                else False
            ),
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
        # SHAP — rule-based attribution runs in enrich_assessment_with_ai when enabled
        "shap": {
            "enabled":                True,
            "top_n_features":         5,
            "use_tree_explainer":     True,   # Used only if a model handle is passed
        },
        # Counterfactual engine — runs in enrich_assessment_with_ai when enabled
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
        weights = getattr(cls, "SUBINDEX_WEIGHTS", {}) or {}
        expected_keys = {"landuse", "vigor", "stability", "weather"}
        if set(weights.keys()) != expected_keys:
            errors.append(
                f"SUBINDEX_WEIGHTS keys must be {sorted(expected_keys)}, got {sorted(weights)}"
            )
        wsum = sum(float(v) for v in weights.values()) if weights else 0.0
        if abs(wsum - 100.0) > 0.01:
            errors.append(f"SUBINDEX_WEIGHTS must sum to 100 (got {wsum})")
        gate = float(getattr(cls, "CONFIDENCE_GATE_MIN", 0.6))
        if not (0.0 < gate <= 1.0):
            errors.append("CONFIDENCE_GATE_MIN must be in (0, 1]")
        if cls.TARGET_SCENES_PER_SEASON > cls.MAX_SCENES_PER_SEASON:
            errors.append("TARGET > MAX scenes")
        if cls.MIN_SCENES_PER_SEASON > cls.TARGET_SCENES_PER_SEASON:
            errors.append("MIN > TARGET scenes")
        if getattr(cls, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10) < 1:
            errors.append("CONTINUOUS_SCENE_INTERVAL_DAYS must be >= 1")
        if getattr(cls, "SATELLITE_STAC_YEAR_SEARCH_WORKERS", 1) < 1:
            errors.append("SATELLITE_STAC_YEAR_SEARCH_WORKERS must be >= 1")
        for _nm in (
            "WEATHER_DYNAMIC_HEAT_PERCENTILE",
            "WEATHER_DYNAMIC_COLD_PERCENTILE",
            "WEATHER_HEAVY_RAIN_WET_PERCENTILE",
            "WEATHER_HEAVY_3DAY_PERCENTILE",
            "WEATHER_DROUGHT_DRY_PERCENTILE",
        ):
            v = float(getattr(cls, _nm, 50.0))
            if not (0.0 <= v <= 100.0):
                errors.append(f"{_nm} must be in [0, 100]")
        if float(getattr(cls, "WEATHER_DYNAMIC_HEAT_FLOOR_C", 0)) > float(
            getattr(cls, "WEATHER_DYNAMIC_HEAT_CAP_C", 50)
        ):
            errors.append("WEATHER_DYNAMIC_HEAT_FLOOR_C must be <= WEATHER_DYNAMIC_HEAT_CAP_C")
        if float(getattr(cls, "WEATHER_DYNAMIC_COLD_FLOOR_C", 0)) > float(
            getattr(cls, "WEATHER_DYNAMIC_COLD_CAP_C", 20)
        ):
            errors.append("WEATHER_DYNAMIC_COLD_FLOOR_C must be <= WEATHER_DYNAMIC_COLD_CAP_C")
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
