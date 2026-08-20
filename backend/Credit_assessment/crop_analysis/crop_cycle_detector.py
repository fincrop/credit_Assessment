"""
Crop Cycle Detector - v4.0 (Clean)
====================================
Detects crop sowing and harvest dates from a continuous NDVI/EVI/NDMI time series.

SIGNAL PROFILE (Indian kharif / rabi)
--------------------------------------
  Sowing  → CVI rises from ~0.15–0.25 (bare soil) to ~0.45–0.65 (canopy closure)
  Peak    → CVI maximum
  Harvest → CVI returns to ~0.15–0.25 (stubble / bare soil)

ALGORITHM  (single pass, no threshold gymnastics)
--------------------------------------------------
  1. Regularise irregular observations onto a uniform time grid.
  2. Impute cloud gaps with context-aware interpolation.
     Short gaps  → linear interpolation.
     Long gaps   → hat profile when post-gap signal is declining
                   (preserves a Kharif peak hidden inside a monsoon blackout).
  3. Build Composite Vegetation Index:  CVI = 0.5·NDVI + 0.3·EVI + 0.2·NDMI
  4. Smooth with a Bartlett weighted moving average.
  5. Find all prominent local CVI maxima (each = one crop cycle).
  6. For every peak:
       • Walk BACK  → last bin where CVI < LOW_THRESH  = sowing date
       • Walk FWRD  → first bin where CVI < LOW_THRESH = harvest date
  7. Validate: minimum rise, plausible duration.
  8. Assign season labels.
"""

import numpy as np
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

from config import PipelineConfig

try:
    from scipy.optimize import curve_fit as _curve_fit
    _SCIPY_OPT = True
except Exception:  # pragma: no cover
    _SCIPY_OPT = False

logger = logging.getLogger(__name__)


def _pheno_fit_enabled() -> bool:
    """
    Whether double-logistic phenology refinement should run.

    Kill switch for environments where the underlying LAPACK routines are
    unreliable. Env var wins so it can be flipped without a redeploy.
    """
    import os as _os
    if _os.environ.get("PHENO_FIT_DISABLE", "").strip().lower() in ("1", "true", "yes"):
        return False
    return bool(getattr(PipelineConfig, "PHENO_FIT_ENABLED", True))


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Flag gaps ≥ 25 days as cloud blackouts (typical monsoon pattern)
_CLOUD_GAP_MIN_DAYS = 25

# Composite Vegetation Index weights
_CVI_W = {'ndvi': 0.50, 'evi': 0.30, 'ndmi': 0.20}


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CloudGap:
    """A temporal window with no usable satellite observations."""
    start_date:  datetime
    end_date:    datetime
    gap_days:    int
    start_index: int
    end_index:   int


@dataclass
class CropCycle:
    """One detected crop cultivation cycle."""
    sowing_date:        datetime
    harvest_date:       datetime
    peak_date:          datetime
    duration_days:      int
    crop_type:          Optional[str]
    # Vegetation metrics
    peak_ndvi:          float
    baseline_ndvi:      float
    ndvi_rise:          float
    integral_ndvi:      float        # scene sum
    integral_ndvi_days: float        # day-weighted trapezoid (yield proxy)
    peak_evi:           float = 0.0
    peak_ndmi:          float = 0.0
    peak_cvi:           float = 0.0
    # Quality
    confidence:         float = 0.0
    cloud_gap_days:     int   = 0
    has_cloud_gap:      bool  = False
    # Observation provenance for this cycle.
    #
    # Cloud gaps are gap-filled before detection, and the long-gap filler
    # synthesises a hat-shaped peak when the signal is declining afterwards.
    # That reconstruction is often the right call — but a cycle whose PEAK sits
    # on a reconstructed bin is an inference, not an observation, and nothing
    # downstream could previously tell the two apart.
    peak_observed:      bool  = True
    observed_fraction:  float = 1.0
    n_observed_bins:    int   = 0
    n_bins:             int   = 0
    # 'annual'    — a sown/harvested crop cycle
    # 'perennial' — one production YEAR of an orchard / plantation, which has no
    #               sowing or harvest in the annual sense. Consumers that reason
    #               about sowing dates or cycles-per-year must branch on this.
    cycle_kind:         str   = "annual"
    # Season
    season_label:       str   = ""
    season_type:        str   = ""
    activity_number:    int   = 0
    # Pillar 2 — double-logistic phenometrics + provenance
    phenology:          Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            # Primary aliases used by downstream pipeline
            'start_date':         self.sowing_date.strftime('%Y-%m-%d'),
            'end_date':           self.harvest_date.strftime('%Y-%m-%d'),
            'sowing_date':        self.sowing_date.isoformat(),
            'harvest_date':       self.harvest_date.isoformat(),
            'peak_date':          self.peak_date.isoformat(),
            'duration_days':      self.duration_days,
            'crop_type':          self.crop_type,
            'season_label':       self.season_label,
            'season_type':        self.season_type,
            'peak_ndvi':          round(self.peak_ndvi, 3),
            'baseline_ndvi':      round(self.baseline_ndvi, 3),
            'ndvi_rise':          round(self.ndvi_rise, 3),
            'integral_ndvi':      round(self.integral_ndvi, 3),
            'integral_ndvi_days': round(self.integral_ndvi_days, 2),
            'peak_evi':           round(self.peak_evi, 3),
            'peak_ndmi':          round(self.peak_ndmi, 3),
            'peak_cvi':           round(self.peak_cvi, 3),
            'confidence':         round(self.confidence, 1),
            'cloud_gap_days':     self.cloud_gap_days,
            'has_cloud_gap':      self.has_cloud_gap,
            # Observation provenance — see the dataclass fields.
            'peak_observed':      self.peak_observed,
            'observed_fraction':  round(self.observed_fraction, 3),
            'n_observed_bins':    self.n_observed_bins,
            'n_bins':             self.n_bins,
            'cycle_kind':         self.cycle_kind,
            'activity_number':    self.activity_number,
            'phenology':          self.phenology or {},
        }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _to_datetime(d) -> datetime:
    """Convert str 'YYYY-MM-DD', pandas Timestamp, numpy datetime64, or date → datetime."""
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        return datetime.strptime(d[:10], '%Y-%m-%d')
    try:
        return d.to_pydatetime()          # pandas Timestamp
    except AttributeError:
        pass
    try:                                   # numpy datetime64
        ts = (d - np.datetime64('1970-01-01T00:00:00')) / np.timedelta64(1, 's')
        return datetime.utcfromtimestamp(float(ts))
    except Exception:
        pass
    from datetime import date as date_type
    if isinstance(d, date_type):
        return datetime(d.year, d.month, d.day)
    raise TypeError(f"Cannot convert {type(d)} to datetime")


def _parse_sowing_hint(sowing_date_hint: Optional[str]) -> Optional[datetime]:
    """Parse YYYY-MM-DD sowing hint; return None if absent or unparseable."""
    if not sowing_date_hint:
        return None
    try:
        return datetime.strptime(str(sowing_date_hint).strip()[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


# How far an agro/eco prior may move the configured peak threshold.
#
# ⚠ These were ABSOLUTE bounds of [0.20, 0.35], written for signal_v1's
# per-parcel rank scale. Under signal_v2's absolute scale 0.35 is BARE SOIL, so
# the prior silently dragged the configured 0.55 gate down to bare-soil level —
# re-creating the very threshold inversion signal_v2 removed, and causing real
# farms to return zero cycles. Observed in the field: "Soft agro prior →
# peak_cvi 0.550 → 0.350" followed by "Found 0 crop cycle(s)".
#
# The bound is now RELATIVE to whatever the configured threshold is, so it
# cannot go stale when the scale changes again. A regional prior should nudge
# detection, never redefine what counts as vegetation.
_SOFT_PEAK_ADJUST_MAX_FRAC = 0.20   # prior may move the gate by at most ±20%
_SOW_HINT_WINDOW_DAYS = 21
_ECO_PEAK_FLOOR_REF = 0.15  # typical cycle_min_peak_floor in india_geo_context

# ── Pillar 2: phenology / seasonal-inference defaults (overridable in config) ──
_PHENO_AMP_FRACTION = 0.20      # SOS/EOS at 20% of fitted amplitude
_PHENO_FIT_MIN_R2 = 0.60        # accept double-logistic refinement above this
_PHENO_PREMONSOON_LOW = 0.30    # Apr–May signal below this = bare/pre-sowing
_PHENO_POSTMONSOON_HIGH = 0.45  # Sep–Oct signal above this = crop was present
# Extra harvest scan / duration padding for declared long-duration crops.
_LONG_DURATION_PAD_DAYS = 60


def _soft_apply_agro_profile(
    peak_cvi: float,
    expected_cycles_per_year: float,
    agro_profile: Optional[Dict],
) -> Tuple[float, float, Dict[str, Any]]:
    """
    Soft-nudge peak-CVI floor and expected-cycle ceiling from agro_profile.

    Recognises explicit keys (peak_cvi_floor, min_peak_cvi, expected_cycles_per_year)
    and india_geo_context eco keys (ndvi_threshold_delta, cycle_min_peak_floor).

    The adjusted floor is bounded RELATIVE to the incoming configured threshold
    (±_SOFT_PEAK_ADJUST_MAX_FRAC), and additionally never allowed at or below
    the bare-soil baseline. A regional prior may nudge detection; it may not
    redefine what counts as vegetation.
    """
    applied: Dict[str, Any] = {}
    if not agro_profile:
        return peak_cvi, expected_cycles_per_year, applied

    configured = float(peak_cvi)
    p = peak_cvi
    exp = expected_cycles_per_year

    for key in ('peak_cvi_floor', 'min_peak_cvi'):
        raw = agro_profile.get(key)
        if raw is None:
            continue
        try:
            target = float(raw)
        except (TypeError, ValueError):
            continue
        # Blend halfway toward the profile target (soft, not hard replace)
        p = 0.5 * p + 0.5 * target
        applied[key] = round(target, 4)

    delta_raw = agro_profile.get('ndvi_threshold_delta')
    if delta_raw is not None:
        try:
            delta = float(delta_raw)
            # Same sign as CropDetector's regional NDVI gate (humid -> slightly
            # lower peak floor). NOTE: this delta is on the raw NDVI scale but is
            # being applied to a VS-scale threshold — a unit mismatch. Scaled by
            # the NDVI span so the nudge is proportionate rather than assuming
            # the two scales are interchangeable.
            ndvi_lo, ndvi_hi = getattr(
                PipelineConfig, "INDEX_PHYSICAL_RANGES", {}
            ).get("NDVI", (-0.20, 0.90))
            span = float(ndvi_hi) - float(ndvi_lo)
            scaled = delta / span if span > 0 else delta
            p = p + scaled
            applied['ndvi_threshold_delta'] = round(delta, 4)
            applied['ndvi_threshold_delta_scaled'] = round(scaled, 4)
        except (TypeError, ValueError):
            pass

    floor_raw = agro_profile.get('cycle_min_peak_floor')
    if floor_raw is not None:
        try:
            floor = float(floor_raw)
            # Relative to eco reference: lower floor → small reduction in peak CVI gate
            p = p + (floor - _ECO_PEAK_FLOOR_REF) * 0.5
            applied['cycle_min_peak_floor'] = round(floor, 4)
        except (TypeError, ValueError):
            pass

    exp_raw = agro_profile.get('expected_cycles_per_year')
    if exp_raw is not None:
        try:
            exp = float(exp_raw)
            applied['expected_cycles_per_year'] = round(exp, 3)
        except (TypeError, ValueError):
            pass

    if applied:
        # Bound relative to the configured threshold, and never at or below the
        # bare-soil baseline — otherwise a regional prior can make bare ground
        # qualify as a crop peak.
        lo = configured * (1.0 - _SOFT_PEAK_ADJUST_MAX_FRAC)
        hi = configured * (1.0 + _SOFT_PEAK_ADJUST_MAX_FRAC)
        baseline = float(getattr(PipelineConfig, "CROP_CYCLE_MIN_BASELINE_CVI", 0.35))
        lo = max(lo, baseline * 1.10)
        p = float(np.clip(p, min(lo, hi), max(lo, hi)))
        applied['peak_cvi_before'] = round(configured, 4)
        applied['peak_cvi_after'] = round(p, 4)
        applied['peak_cvi_bounds'] = [round(lo, 4), round(hi, 4)]
        if 'narrative' in agro_profile and agro_profile.get('narrative'):
            applied['narrative'] = str(agro_profile['narrative'])[:120]

    return p, exp, applied


# ─────────────────────────────────────────────────────────────────────────────
# DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class CropCycleDetector:
    """
    Detects crop cultivation cycles from a continuous multi-index time series.

    Thresholds default from ``PipelineConfig`` (``CROP_CYCLE_*``) so behaviour matches
    the rest of the pipeline; class attributes below are fallbacks only.
    """

    # Fallbacks if config keys are absent
    LOW_CVI  = 0.28
    PEAK_CVI = 0.38
    MIN_RISE = 0.12
    MIN_DAYS = 45
    MAX_DAYS = 220
    PEAK_WIN = 21     # days

    def __init__(self, cloud_gap_penalty: float = 4.0):
        self.cloud_gap_penalty = cloud_gap_penalty
        self.last_detection_meta: Dict[str, Any] = {}

    # ─────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────


    def detect_cycles(
        self,
        dates:        List,
        ndvi_values:  List[float],
        evi_values:   Optional[List[float]] = None,
        ndmi_values:  Optional[List[float]] = None,
        scenes:       Optional[List[Dict]]  = None,
        grid_step_days: Optional[float]     = None,
        sowing_date_hint: Optional[str]     = None,
        crop_hint: Optional[str]            = None,
        agro_profile: Optional[Dict]        = None,
        composite_values: Optional[List[float]] = None,
        composite_smooth_values: Optional[List[float]] = None,
        **kwargs: Any,
    ) -> List[CropCycle]:
        """
        Detect all crop cycles from a continuous vegetation time series.

        Args:
            dates:          Observation dates (datetime or 'YYYY-MM-DD' string).
            ndvi_values:    NDVI per observation.
            evi_values:     EVI  per observation (optional; proxy used if absent).
            ndmi_values:    NDMI per observation (optional; zeroed if absent).
            scenes:         Raw scene dicts for EVI/NDMI extraction (optional).
            grid_step_days: Uniform grid bin size in days (default 10).
            sowing_date_hint: Optional YYYY-MM-DD soft prior for sow walk-back.
            crop_hint:      Optional registry crop name (logged; not hard-applied).
            agro_profile:   Optional eco / threshold priors (soft peak-CVI nudge).

        Returns:
            List[CropCycle] sorted chronologically.
        """
        _ = kwargs
        _ = crop_hint  # accepted for API compat; not used as a hard override
        applied_knobs: Dict[str, Any] = {}
        sow_hint_dt = _parse_sowing_hint(sowing_date_hint)

        self.last_detection_meta = {
            'hints_received': bool(sowing_date_hint or crop_hint or agro_profile),
            'hints_applied': False,
            'applied_knobs': {},
        }

        if sowing_date_hint or crop_hint or agro_profile:
            logger.info(
                "Cycle soft priors present "
                "(sowing_date_hint=%r crop_hint=%r agro_profile=%s)",
                sowing_date_hint,
                crop_hint,
                "set" if agro_profile else None,
            )

        if len(dates) < 10:
            logger.warning("Insufficient data: %d observations (need ≥ 10)", len(dates))
            return []

        # 1. Normalise dates
        dt_dates = [_to_datetime(d) for d in dates]

        # 2. Resolve EVI / NDMI (from scene dicts if arrays not supplied)
        n = len(ndvi_values)
        if evi_values is None or len(evi_values) != n:
            evi_values = (
                [s.get('indices', {}).get('EVI_mean', np.nan) for s in scenes]
                if scenes and len(scenes) == n else [np.nan] * n
            )
        if ndmi_values is None or len(ndmi_values) != n:
            ndmi_values = (
                [s.get('indices', {}).get('NDMI_mean', np.nan) for s in scenes]
                if scenes and len(scenes) == n else [0.0] * n
            )

        # Pillar 2: prefer the Pillar-1 composite signal (kNDVI backbone, SAR-fused,
        # smoothed) as the detection signal. Explicit arg wins; else read VS_mean
        # off the scenes; else fall back to the internal CVI blend.
        if composite_values is None and scenes and len(scenes) == n:
            vs = [(s.get("indices", {}) or {}).get("VS_mean") for s in scenes]
            if any(v is not None for v in vs):
                composite_values = vs
        if composite_values is not None and len(composite_values) == n:
            comp_values = [np.nan if v is None else float(v) for v in composite_values]
        else:
            comp_values = [np.nan] * n

        # Pre-smoothed composite (Whittaker, from the collector). Preferred over
        # re-smoothing here: Whittaker preserves the shape of short cycles that a
        # broad moving average erases.
        if (
            composite_smooth_values is not None
            and len(composite_smooth_values) == n
            and any(v is not None for v in composite_smooth_values)
        ):
            comp_smooth_values = [
                np.nan if v is None else float(v) for v in composite_smooth_values
            ]
        else:
            comp_smooth_values = None

        # 3. Sort by date
        _smooth_in = comp_smooth_values if comp_smooth_values is not None else [np.nan] * n
        paired   = sorted(
            zip(dt_dates, ndvi_values, evi_values, ndmi_values, comp_values, _smooth_in),
            key=lambda x: x[0],
        )
        dt_dates = [p[0] for p in paired]
        ndvi_arr = np.array([p[1] for p in paired], dtype=float)
        evi_arr  = np.array([p[2] for p in paired], dtype=float)
        ndmi_arr = np.array([p[3] for p in paired], dtype=float)
        comp_arr = np.array([p[4] for p in paired], dtype=float)
        comp_smooth_arr = (
            np.array([p[5] for p in paired], dtype=float)
            if comp_smooth_values is not None else None
        )

        logger.info(
            "Detecting crop cycles from %d observations  %s → %s",
            len(dt_dates),
            dt_dates[0].strftime('%Y-%m-%d'),
            dt_dates[-1].strftime('%Y-%m-%d'),
        )

        P = PipelineConfig
        step = float(grid_step_days or getattr(P, "CYCLE_GRID_STEP_DAYS", 10))

        # Calendar gaps + long stretches without usable NDVI (e.g. monsoon)
        calendar_gaps = self._detect_cloud_gaps(dt_dates)
        sparse_gaps   = self._detect_sparse_ndvi_gaps(dt_dates, ndvi_arr)
        cloud_gaps    = calendar_gaps + sparse_gaps
        if cloud_gaps:
            logger.info(
                "  %d data/cloud gap(s): %s",
                len(cloud_gaps),
                ", ".join(f"{g.start_date.strftime('%b %Y')} ({g.gap_days}d)" for g in cloud_gaps[:8])
                + (" …" if len(cloud_gaps) > 8 else ""),
            )

        # Thresholds from PipelineConfig (single source of truth with Stage 4 docs)
        low_cvi   = float(getattr(P, "CROP_CYCLE_MIN_BASELINE_CVI", self.LOW_CVI))
        peak_cvi  = float(getattr(P, "CROP_CYCLE_MIN_PEAK_CVI", self.PEAK_CVI))
        min_rise  = float(
            getattr(P, "CROP_CYCLE_MIN_CVI_RISE", None)
            or max(0.08, float(getattr(P, "CROP_CYCLE_MIN_NDVI_RISE", self.MIN_RISE)) * 0.88)
        )
        min_days  = int(getattr(P, "CROP_CYCLE_MIN_DURATION_DAYS", self.MIN_DAYS))
        max_days  = int(getattr(P, "CROP_CYCLE_MAX_DURATION_DAYS", self.MAX_DAYS))
        max_after = int(getattr(P, "CROP_CYCLE_MAX_DAYS_AFTER_PEAK", 135))
        # NOTE: harvest_ndvi is on the RAW NDVI scale (compared against
        # ndvi_smooth), unlike low_cvi/peak_cvi which are on the VS scale.
        harvest_ndvi = float(getattr(P, "CROP_CYCLE_HARVEST_LOW_NDVI", 0.36))
        # Floors for the relaxation passes below. Bound how far the gates may be
        # loosened when too few cycles are found — relaxation may miss a marginal
        # crop, but must never let bare soil or a rooftop qualify as a peak.
        relaxed_peak_floor = float(getattr(P, "CROP_CYCLE_RELAXED_PEAK_FLOOR", 0.46))
        relaxed_rise_floor = float(getattr(P, "CROP_CYCLE_RELAXED_RISE_FLOOR", 0.10))

        # Sanity: the "returned to bare ground" level must sit BELOW the "this is
        # a peak" level. If they invert, every sow/harvest walk terminates
        # immediately and cycles are rejected on duration instead — which is what
        # signal_v1 did (baseline 0.30 > peak 0.28).
        if low_cvi >= peak_cvi:
            logger.error(
                "Cycle thresholds inverted: baseline %.3f >= peak %.3f. "
                "Detection will under-report. Check PipelineConfig.",
                low_cvi, peak_cvi,
            )

        # Pillar 2: crop-family long-duration branch. A declared long-duration
        # crop (sugarcane/banana/…) raises the duration + harvest-scan caps so a
        # real 300+ day cycle is not split/rejected by the default 195-day gate.
        long_map = getattr(P, "LONG_DURATION_CROPS", {}) or {}
        crop_norm = (crop_hint or "").strip().title()
        long_duration_crop = None
        if crop_norm in long_map:
            typical = int(long_map[crop_norm])
            pad = int(getattr(P, "LONG_DURATION_PAD_DAYS", _LONG_DURATION_PAD_DAYS))
            if typical + pad > max_days:
                long_duration_crop = crop_norm
                max_days = typical + pad
                max_after = max(max_after, int(typical * 0.9))
                logger.info(
                    "Long-duration crop '%s' → max_days=%d, max_days_after_peak=%d",
                    crop_norm, max_days, max_after,
                )

        years_span = max(0.25, (dt_dates[-1] - dt_dates[0]).days / 365.25)
        expected_rate = float(getattr(P, "CROP_CYCLE_EXPECTED_CYCLES_PER_YEAR", 1.0))

        # Soft agro / eco priors on peak floor + expected rate
        peak_cvi_base = peak_cvi
        peak_cvi, expected_rate, agro_knobs = _soft_apply_agro_profile(
            peak_cvi, expected_rate, agro_profile
        )
        if agro_knobs:
            applied_knobs.update(agro_knobs)
            applied_knobs['peak_cvi_before'] = round(peak_cvi_base, 4)
            logger.info(
                "  Soft agro prior → peak_cvi %.3f → %.3f  expected_rate=%.2f",
                peak_cvi_base, peak_cvi, expected_rate,
            )

        reg_dates, reg_ndvi, reg_evi, reg_ndmi = self._regularise_and_impute(
            dt_dates, ndvi_arr, evi_arr, ndmi_arr, cloud_gaps, step
        )

        # Detection signal: Pillar-1 composite VS if present & dense enough,
        # else the internal CVI blend. Peak/sow/harvest logic is unchanged.
        signal_source_used = "cvi_ndvi_evi_ndmi"
        cvi = None
        cvi_smooth = None

        # Preferred: the Whittaker-smoothed composite from the collector. It is
        # already gap-filled and shape-preserving, so re-applying a broad moving
        # average here would only blur it — and that blurring is what removed
        # short-duration crops (Bajra ~85d, Cabbage ~80d) from detection.
        if comp_smooth_arr is not None and np.isfinite(comp_smooth_arr).any():
            reg_cs = self._regularise_single(dt_dates, comp_smooth_arr, reg_dates, step)
            if reg_cs is not None and len(reg_cs) == len(reg_dates):
                cvi_smooth = np.clip(reg_cs, 0.0, 1.0)
                cvi = cvi_smooth
                signal_source_used = "composite_vs_whittaker"

        if cvi is None and np.isfinite(comp_arr).any():
            reg_comp = self._regularise_single(dt_dates, comp_arr, reg_dates, step)
            if reg_comp is not None and len(reg_comp) == len(reg_dates):
                cvi = np.clip(reg_comp, 0.0, 1.0)
                signal_source_used = "composite_vs"
        if cvi is None:
            cvi = self._build_cvi(reg_ndvi, reg_evi, reg_ndmi)

        if cvi_smooth is None:
            cvi_smooth = self._smooth(cvi, window=7)
        ndvi_smooth = self._smooth(reg_ndvi, window=7)

        # Which grid bins carry a real observation, before any gap filling?
        # Built from the raw (pre-imputation) NDVI snapped onto the same grid, so
        # a bin is "observed" only if an actual scene landed in it. Used to mark
        # cycles whose peak was reconstructed rather than seen.
        try:
            obs_offsets = np.array(
                [(d - dt_dates[0]).days for d in dt_dates], dtype=float
            )
            snapped = self._snap_to_grid(
                obs_offsets, ndvi_arr, len(reg_dates), step
            )
            observed_mask = np.isfinite(snapped)
        except Exception as e:  # never let provenance bookkeeping break detection
            logger.debug("observed-bin mask unavailable: %s", e)
            observed_mask = np.ones(len(reg_dates), dtype=bool)

        def run_pass(p_floor: float, r_floor: float) -> Tuple[List[CropCycle], int]:
            hits = {'count': 0}
            out = self._detect_cycles(
                cvi_smooth, ndvi_smooth,
                reg_ndvi, reg_evi, reg_ndmi,
                reg_dates, cloud_gaps, step,
                low_cvi=low_cvi,
                peak_cvi=p_floor,
                min_rise=r_floor,
                min_days=min_days,
                max_days=max_days,
                max_days_after_peak=max_after,
                harvest_ndvi_low=harvest_ndvi,
                sowing_hint=sow_hint_dt,
                sow_bias_hits=hits,
                observed_mask=observed_mask,
            )
            return out, int(hits.get('count', 0))

        cycles, sow_bias_n = run_pass(peak_cvi, min_rise)
        expected = max(1, int(np.ceil(years_span * expected_rate)))

        if (
            getattr(P, "CROP_CYCLE_ADAPTIVE_SECOND_PASS", True)
            and len(cycles) < max(1, int(np.ceil(years_span * 0.65)))
        ):
            c2, bias2 = run_pass(
                max(relaxed_peak_floor, peak_cvi - 0.05),
                max(relaxed_rise_floor, min_rise - 0.02),
            )
            if len(c2) > len(cycles):
                cycles, sow_bias_n = c2, bias2
                self.last_detection_meta["adaptive_pass"] = "second"

        if (
            getattr(P, "CROP_CYCLE_DENSITY_PASS", True)
            and len(cycles) < expected
            and len(cycles) < max(2, expected)
        ):
            scale = float(getattr(P, "CROP_CYCLE_DENSITY_PEAK_PROMINENCE_SCALE", 0.58))
            density_floor = float(getattr(P, "CROP_CYCLE_DENSITY_PEAK_FLOOR", 0.38))
            c3, bias3 = run_pass(
                max(density_floor, peak_cvi * scale),
                max(relaxed_rise_floor, min_rise - 0.035),
            )
            if len(c3) > len(cycles):
                cycles, sow_bias_n = c3, bias3
                self.last_detection_meta["density_pass"] = True

        cvi_cycle_n = len(cycles)

        # NDVI-anchored fallback. Config advertised this for years; it was
        # never wired. CVI/VS peaks at 0.55 miss real crops that only reach
        # NDVI ~0.45–0.55, and CVI duration gates reject long plateaus (kharif
        # that stays green 4–5 months). The NDVI series the dashboard shows is
        # the honest signal — always try it and keep the richer set.
        if bool(getattr(P, "CROP_CYCLE_PEAK_ANCHORED_FALLBACK", True)):
            ndvi_cycles = self._detect_ndvi_anchored(
                cvi_smooth, ndvi_smooth, reg_ndvi, reg_evi, reg_ndmi,
                reg_dates, cloud_gaps, step,
                min_days=min_days,
                max_days=max(max_days, int(getattr(P, "CROP_CYCLE_NDVI_MAX_DURATION_DAYS", 220))),
                observed_mask=observed_mask,
            )
            if len(ndvi_cycles) > len(cycles):
                logger.info(
                    "NDVI-anchored fallback recovered %d cycle(s) (CVI pass had %d)",
                    len(ndvi_cycles), cvi_cycle_n,
                )
                cycles = ndvi_cycles
                self.last_detection_meta["ndvi_anchored_pass"] = True

        # ── Perennial / plantation branch ────────────────────────────────
        #
        # A crop that never returns to bare ground cannot be found by the annual
        # logic above, and the failure is silent and total: with no trough, the
        # sow walk-back defaults to peak-190d while harvest lands at peak+40d,
        # giving 230 days against a 195-day cap — so EVERY candidate is rejected
        # on duration. A productive orchard therefore returned zero cycles, and
        # zero cycles reads downstream as crop_intensity 0 and fallow 1.0:
        # a mango grove scored as abandoned land.
        #
        # The relaxation passes could never rescue it either, because they relax
        # peak_cvi and min_rise — never low_cvi, which is the constraint that
        # actually failed.
        #
        # So treat "never troughs, but persistently green" as its own SIGNAL.
        # Emitted as one production cycle per agronomic year, which keeps every
        # downstream stage (performance, weather, land-utilization) working
        # unchanged while being the agronomically right unit for a perennial.
        perennial_used = False
        if (
            not cycles
            and bool(getattr(P, "PERENNIAL_DETECTION_ENABLED", True))
        ):
            perennial = self._detect_perennial(
                cvi_smooth, ndvi_smooth, reg_ndvi, reg_evi, reg_ndmi,
                reg_dates, step, low_cvi=low_cvi, observed_mask=observed_mask,
            )
            if perennial:
                cycles = perennial
                perennial_used = True
                logger.info(
                    "Perennial/plantation pattern detected — %d production "
                    "year(s). Signal never returns to bare ground.", len(cycles),
                )

        if sow_bias_n > 0 and sow_hint_dt is not None:
            applied_knobs['sowing_date_hint'] = sow_hint_dt.strftime('%Y-%m-%d')
            applied_knobs['sow_hint_window_days'] = _SOW_HINT_WINDOW_DAYS
            applied_knobs['sow_bias_applied_count'] = sow_bias_n

        hints_applied = bool(agro_knobs or sow_bias_n > 0)

        self.last_detection_meta.update({
            "hints_applied": hints_applied,
            "applied_knobs": applied_knobs,
            "observations": len(dt_dates),
            "cycles_found": len(cycles),
            "years_span": round(years_span, 2),
            "expected_cycles_ceiling": expected,
            "perennial_detected": perennial_used,
            "cycle_kind": "perennial" if perennial_used else "annual",
            "thresholds": {
                "low_cvi": low_cvi,
                "peak_cvi_used": peak_cvi,
                "min_rise_used": min_rise,
                "min_days": min_days,
                "max_days": max_days,
                "max_days_after_peak": max_after,
            },
        })

        # Pillar 2: refine each cycle's SOS/POS/EOS via double-logistic on the
        # detection signal (may adjust sow/peak/harvest before labelling).
        for c in cycles:
            try:
                self._refine_cycle_phenology(c, reg_dates, cvi_smooth)
            except Exception as e:
                logger.debug("phenology refine failed: %s", e)
                if c.phenology is None:
                    c.phenology = {"fit_ok": False, "reason": "exception"}

        # Annotate. Season is assigned once, from a single implementation, using
        # BOTH endpoints and the regional calendar — so season_type and
        # season_label can no longer contradict each other.
        season_ctx = dict(agro_profile or {})
        season_ctx.setdefault("latitude", (agro_profile or {}).get("latitude"))
        for idx, c in enumerate(cycles, 1):
            c.activity_number = idx
            if c.cycle_kind == "perennial":
                # A perennial spans every season; forcing it into one would be
                # a fabrication. Labelled by production year instead.
                c.season_type = "perennial"
                c.season_label = f"Production year {c.sowing_date.year}"
            else:
                c.season_type, c.season_label = self.assign_season(
                    c.sowing_date, c.harvest_date, season_ctx
                )

        logger.info("Found %d crop cycle(s)", len(cycles))
        for c in cycles:
            logger.info(
                "  [%s] %s → %s  (%dd | peak CVI=%.3f | conf=%.0f%%)",
                c.season_label,
                c.sowing_date.strftime('%Y-%m-%d'),
                c.harvest_date.strftime('%Y-%m-%d'),
                c.duration_days,
                c.peak_cvi,
                c.confidence,
            )

        # Pillar 2: cloud-gap seasonal inference + missed-kharif flagging.
        try:
            seasonal_inference = self._infer_seasonal_presence(reg_dates, cvi_smooth)
        except Exception as e:
            logger.debug("seasonal inference failed: %s", e)
            seasonal_inference = {"per_year": {}, "error": str(e)[:80]}

        suspected: List[int] = []
        for yr, info in (seasonal_inference.get("per_year") or {}).items():
            if info.get("classification") == "kharif_sown_in_gap":
                has_cycle = any(
                    c.peak_date.year == int(yr) and 6 <= c.peak_date.month <= 11
                    for c in cycles
                )
                if not has_cycle:
                    suspected.append(int(yr))
        if suspected:
            logger.info("Cloud-gap inference: suspected unresolved kharif year(s): %s", suspected)

        n_fit = sum(1 for c in cycles if (c.phenology or {}).get("fit_ok"))
        self.last_detection_meta.update({
            "signal_source_used": signal_source_used,
            "long_duration_crop": long_duration_crop,
            "max_days_used": max_days,
            "phenology_engine": "double_logistic",
            "cycles_phenology_fit": n_fit,
            "seasonal_inference": seasonal_inference,
            "suspected_unresolved_kharif_years": suspected,
        })

        return cycles

    # ─────────────────────────────────────────────────────
    # CORE: PEAK → SOW → HARVEST  (single pass)
    # ─────────────────────────────────────────────────────

    def _detect_cycles(
        self,
        cvi_smooth:  np.ndarray,
        ndvi_smooth: np.ndarray,
        reg_ndvi:    np.ndarray,
        reg_evi:     np.ndarray,
        reg_ndmi:    np.ndarray,
        reg_dates:   List[datetime],
        cloud_gaps:  List[CloudGap],
        bin_days:    float,
        *,
        low_cvi: float,
        peak_cvi: float,
        min_rise: float,
        min_days: int,
        max_days: int,
        max_days_after_peak: int,
        harvest_ndvi_low: float,
        sowing_hint: Optional[datetime] = None,
        sow_bias_hits: Optional[Dict[str, int]] = None,
        observed_mask: Optional[np.ndarray] = None,
    ) -> List[CropCycle]:
        """
        For each prominent CVI peak:
          Walk BACK for sowing (CVI < low_cvi), FWRD for harvest (CVI / NDVI / trough).
          Optional sowing_hint soft-biases sow index toward ±21d of the hint when peak is nearby.
        """
        n    = len(cvi_smooth)
        bd   = max(float(bin_days), 1.0)
        peak_win = float(getattr(PipelineConfig, "CROP_CYCLE_PEAK_WIN_DAYS", self.PEAK_WIN))
        wbin = max(3, int(peak_win / bd))
        mbin = max(3, int(min_days / bd))
        xbin = int(max_days / bd)
        harv_max_off = max(mbin + 1, int(max_days_after_peak / bd) + 1)

        # Minimum prominence: how far a peak must rise above the higher of its
        # two flanking troughs to count as a real growth event rather than a
        # wobble on a plateau.
        #
        # There was NO prominence criterion — the test below is a bare
        # local-maximum with a 0.005 tolerance, so two noise wiggles 45 days
        # apart on an otherwise flat signal both qualified. A config key for
        # this (CROP_CYCLE_FALLBACK_PROMINENCE_FLOOR) existed and was never read.
        min_prom = float(getattr(PipelineConfig, "CROP_CYCLE_MIN_PROMINENCE", 0.10))
        # Prominence must be measured against the troughs that BOUND a cycle,
        # not the immediate neighbourhood. Over the ±30-day peak-detection
        # window a broad 140-day seasonal peak barely descends at all, so a
        # narrow window would reject real crops. Half the maximum cycle length
        # reaches the bare-soil periods either side.
        pbin = max(wbin, xbin // 2)

        raw_peaks: List[Tuple[int, float]] = []
        for i in range(wbin, n - wbin):
            v = float(cvi_smooth[i])
            if v < peak_cvi:
                continue
            if v < float(np.max(cvi_smooth[i - wbin: i + wbin + 1])) - 0.005:
                continue
            # The peak must stand clear of the higher of its two flanking
            # troughs, not merely be the largest value nearby.
            left = cvi_smooth[max(0, i - pbin): i + 1]
            right = cvi_smooth[i: min(n, i + pbin + 1)]
            left_min = float(np.nanmin(left))
            right_min = float(np.nanmin(right))
            # A crop only has to drop on ONE side (sow or harvest). Measuring
            # against the higher col rejects plateau kharif that stays green
            # into the series edge or a shallow rabi col.
            drop = max(v - left_min, v - right_min)
            if drop < min_prom:
                logger.debug(
                    "  Peak @%d rejected: drop %.3f < %.3f",
                    i, drop, min_prom,
                )
                continue
            raw_peaks.append((i, v))

        min_sep = max(2, int(getattr(PipelineConfig, "CROP_CYCLE_GREENUP_MIN_GRID_SEP", 3)))
        clean_peaks: List[int] = []
        for pi, pv in raw_peaks:
            if clean_peaks and (pi - clean_peaks[-1]) < max(mbin, min_sep):
                if pv > float(cvi_smooth[clean_peaks[-1]]):
                    clean_peaks[-1] = pi
            else:
                clean_peaks.append(pi)

        clean_peaks = self._merge_plateau_peaks(clean_peaks, cvi_smooth, min_prom)

        logger.debug("  %d candidate peak(s) after deduplication", len(clean_peaks))

        cycles: List[CropCycle] = []
        used_up_to = -1

        for pi_i, peak_idx in enumerate(clean_peaks):
            if peak_idx <= used_up_to:
                continue

            peak_cvi_v  = float(cvi_smooth[peak_idx])
            peak_ndvi_v = float(ndvi_smooth[peak_idx])

            sow_idx, sow_biased = self._resolve_sow_index(
                cvi_smooth=cvi_smooth,
                reg_dates=reg_dates,
                peak_idx=peak_idx,
                low_cvi=low_cvi,
                mbin=mbin,
                xbin=xbin,
                max_days=max_days,
                sowing_hint=sowing_hint,
            )
            if sow_biased and sow_bias_hits is not None:
                sow_bias_hits['count'] = int(sow_bias_hits.get('count', 0)) + 1
            if used_up_to >= 0:
                sow_idx = max(sow_idx, used_up_to + 1)

            # Do not let harvest walk into the next crop. Searching 135 days
            # past a kharif peak routinely finds the rabi trough *after* the
            # next peak and then marks that peak as already used.
            next_peak = clean_peaks[pi_i + 1] if pi_i + 1 < len(clean_peaks) else None
            h_hi = min(n - 1, peak_idx + harv_max_off)
            if next_peak is not None:
                h_hi = min(h_hi, int(next_peak) - 1)

            harv_idx = h_hi
            found_low = False
            for j in range(peak_idx + mbin, h_hi + 1):
                if float(cvi_smooth[j]) < low_cvi:
                    harv_idx = j
                    found_low = True
                    break
            if not found_low:
                for j in range(peak_idx + mbin, h_hi + 1):
                    if float(ndvi_smooth[j]) < harvest_ndvi_low:
                        harv_idx = j
                        found_low = True
                        break
            if not found_low and peak_idx + mbin <= h_hi:
                seg = cvi_smooth[peak_idx + mbin: h_hi + 1]
                if len(seg) > 0:
                    harv_idx = int(np.argmin(seg)) + peak_idx + mbin

            sow_baseline = float(np.nanmean(cvi_smooth[max(0, sow_idx - 2): sow_idx + 2]))
            cvi_rise_v   = peak_cvi_v - sow_baseline
            ndvi_rise_v  = peak_ndvi_v - float(np.nanmean(ndvi_smooth[max(0, sow_idx - 2): sow_idx + 2]))
            duration     = (reg_dates[harv_idx] - reg_dates[sow_idx]).days

            if not (min_days <= duration <= max_days):
                sow2, harv2 = self._trough_window(
                    ndvi_smooth, peak_idx,
                    lo=used_up_to + 1,
                    hi=(int(next_peak) - 1) if next_peak is not None else n - 1,
                    mbin=mbin,
                    half_bins=max(mbin, int(120 / bd)),
                )
                duration2 = (reg_dates[harv2] - reg_dates[sow2]).days
                if min_days <= duration2 <= max_days:
                    sow_idx, harv_idx, duration = sow2, harv2, duration2
                    sow_baseline = float(np.nanmean(cvi_smooth[max(0, sow_idx - 2): sow_idx + 2]))
                    cvi_rise_v = peak_cvi_v - sow_baseline
                    ndvi_rise_v = peak_ndvi_v - float(
                        np.nanmean(ndvi_smooth[max(0, sow_idx - 2): sow_idx + 2])
                    )
                else:
                    logger.debug(
                        "  Peak @%d rejected: duration %dd out of [%d, %d]",
                        peak_idx, duration, min_days, max_days,
                    )
                    continue

            min_rise_ok = cvi_rise_v >= min_rise or ndvi_rise_v >= min_rise
            if not min_rise_ok:
                logger.debug("  Peak @%d rejected: CVI rise %.3f < %.3f", peak_idx, cvi_rise_v, min_rise)
                continue

            # Cloud gaps OVERLAPPING the cycle, not merely contained in it.
            # The containment test previously used here excluded any gap that
            # straddled the sowing date — which in India is the single most
            # common case (monsoon onset). So has_cloud_gap read False on
            # exactly the cycles that most needed flagging.
            c_start, c_end = reg_dates[sow_idx], reg_dates[harv_idx]
            cycle_gaps     = [g for g in cloud_gaps
                              if g.start_date <= c_end and g.end_date >= c_start]
            cloud_gap_days = sum(g.gap_days for g in cycle_gaps)

            # Observation provenance over the cycle window.
            if observed_mask is not None and len(observed_mask) == n:
                seg_mask = observed_mask[sow_idx: harv_idx + 1]
                n_obs = int(np.count_nonzero(seg_mask))
                n_win = int(len(seg_mask))
                peak_observed = bool(observed_mask[peak_idx])
            else:
                n_obs = n_win = int(harv_idx - sow_idx + 1)
                peak_observed = True

            if not peak_observed:
                logger.info(
                    "  Cycle peaking %s sits on a RECONSTRUCTED bin "
                    "(%d/%d bins observed) — flagged as inferred.",
                    reg_dates[peak_idx].strftime('%Y-%m-%d'), n_obs, n_win,
                )

            ndvi_seg    = reg_ndvi[sow_idx: harv_idx + 1]
            day_offsets = np.array(
                [(reg_dates[j] - reg_dates[sow_idx]).days
                 for j in range(sow_idx, harv_idx + 1)],
                dtype=float,
            )

            cycles.append(CropCycle(
                sowing_date         = reg_dates[sow_idx],
                harvest_date        = reg_dates[harv_idx],
                peak_date           = reg_dates[peak_idx],
                duration_days       = duration,
                crop_type           = self._crop_type(duration, peak_ndvi_v, peak_cvi_v),
                peak_ndvi           = peak_ndvi_v,
                baseline_ndvi       = float(np.nanmean(ndvi_smooth[max(0, sow_idx - 3): sow_idx + 1])),
                ndvi_rise           = ndvi_rise_v,
                integral_ndvi       = float(np.nansum(ndvi_seg)),
                integral_ndvi_days  = float(np.trapz(ndvi_seg, day_offsets)) if len(ndvi_seg) > 1 else 0.0,
                peak_evi            = float(np.nanmean(reg_evi [peak_idx - 1: peak_idx + 2])),
                peak_ndmi           = float(np.nanmean(reg_ndmi[peak_idx - 1: peak_idx + 2])),
                peak_cvi            = peak_cvi_v,
                confidence          = self._confidence(ndvi_seg, duration, peak_cvi_v, cloud_gap_days),
                cloud_gap_days      = cloud_gap_days,
                has_cloud_gap       = len(cycle_gaps) > 0,
                peak_observed       = peak_observed,
                observed_fraction   = (n_obs / n_win) if n_win else 0.0,
                n_observed_bins     = n_obs,
                n_bins              = n_win,
            ))
            used_up_to = harv_idx

        return cycles

    @staticmethod
    def _merge_plateau_peaks(
        peaks: List[int],
        signal: np.ndarray,
        min_drop: float,
    ) -> List[int]:
        """Keep one peak per green plateau; split only when the canopy actually drops."""
        if len(peaks) < 2:
            return peaks
        out: List[int] = [peaks[0]]
        for pi in peaks[1:]:
            prev = out[-1]
            lo, hi = (prev, pi) if prev <= pi else (pi, prev)
            seg = signal[lo: hi + 1]
            if not seg.size or not np.isfinite(seg).any():
                out.append(pi)
                continue
            valley = float(np.nanmin(seg))
            ha = float(signal[prev])
            hb = float(signal[pi])
            if min(ha, hb) - valley < min_drop:
                if hb > ha:
                    out[-1] = pi
            else:
                out.append(pi)
        return out

    @staticmethod
    def _trough_window(
        signal: np.ndarray,
        peak_idx: int,
        *,
        lo: int,
        hi: int,
        mbin: int,
        half_bins: int,
    ) -> Tuple[int, int]:
        """Nearest troughs either side of a peak, clamped to [lo, hi].

        Uses the valley closest to the peak that is within a small band of the
        window minimum. Taking the global argmin over ±120 days stretches a
        long kharif plateau into the neighbouring fallow and then fails the
        duration cap — which is how a clearly cropped field returned no cycle.
        """
        n = len(signal)
        left0 = max(int(lo), peak_idx - half_bins, 0)
        left1 = max(left0, peak_idx - mbin)
        right0 = min(n - 1, peak_idx + mbin)
        right1 = min(int(hi), peak_idx + half_bins, n - 1)
        slack = 0.04

        sow = left0
        if left1 >= left0:
            seg = signal[left0: left1 + 1]
            if seg.size and np.isfinite(seg).any():
                vmin = float(np.nanmin(seg))
                sow = left0 + int(np.nanargmin(seg))
                for j in range(left1, left0 - 1, -1):
                    v = float(signal[j])
                    if np.isfinite(v) and v <= vmin + slack:
                        sow = j
                        break

        harv = right1
        if right1 >= right0:
            seg = signal[right0: right1 + 1]
            if seg.size and np.isfinite(seg).any():
                vmin = float(np.nanmin(seg))
                harv = right0 + int(np.nanargmin(seg))
                for j in range(right0, right1 + 1):
                    v = float(signal[j])
                    if np.isfinite(v) and v <= vmin + slack:
                        harv = j
                        break

        if sow >= peak_idx:
            sow = max(0, peak_idx - mbin)
        if harv <= peak_idx:
            harv = min(n - 1, peak_idx + mbin)
        return sow, harv

    def _detect_ndvi_anchored(
        self,
        cvi_smooth: np.ndarray,
        ndvi_smooth: np.ndarray,
        reg_ndvi: np.ndarray,
        reg_evi: np.ndarray,
        reg_ndmi: np.ndarray,
        reg_dates: List[datetime],
        cloud_gaps: List[CloudGap],
        bin_days: float,
        *,
        min_days: int,
        max_days: int,
        observed_mask: Optional[np.ndarray] = None,
    ) -> List[CropCycle]:
        """
        Detect growing seasons from the NDVI curve (what the dashboard plots).

        A canopy that peaks at NDVI 0.45–0.55 is still a crop — pulses, millet,
        and stressed cereals sit there. CVI/VS gating at 0.55 never sees them.
        Bounding sow/harvest by the flanking troughs (not an absolute bare-soil
        crossing) keeps long green plateaus from failing the duration cap.
        """
        P = PipelineConfig
        n = len(reg_dates)
        if n < 12:
            return []
        bd = max(float(bin_days), 1.0)
        peak_floor = float(getattr(P, "CROP_CYCLE_NDVI_PEAK_FLOOR", 0.38))
        min_prom = float(getattr(P, "CROP_CYCLE_NDVI_MIN_PROMINENCE", 0.08))
        min_rise = float(getattr(P, "CROP_CYCLE_NDVI_MIN_RISE", 0.08))
        wbin = max(2, int(21 / bd))
        mbin = max(3, int(min_days / bd))
        half = max(mbin, int(int(getattr(P, "CROP_CYCLE_NDVI_MAX_HALF_DAYS", 120)) / bd))
        min_sep = max(mbin, int(55 / bd))

        raw: List[int] = []
        for i in range(wbin, n - wbin):
            v = float(ndvi_smooth[i])
            if not np.isfinite(v) or v < peak_floor:
                continue
            neigh = ndvi_smooth[i - wbin: i + wbin + 1]
            if v < float(np.nanmax(neigh)) - 0.005:
                continue
            left = ndvi_smooth[max(0, i - half): i + 1]
            right = ndvi_smooth[i: min(n, i + half + 1)]
            drop = max(v - float(np.nanmin(left)), v - float(np.nanmin(right)))
            if drop < min_prom:
                continue
            raw.append(i)

        peaks: List[int] = []
        for pi in raw:
            if peaks and (pi - peaks[-1]) < min_sep:
                if float(ndvi_smooth[pi]) > float(ndvi_smooth[peaks[-1]]):
                    peaks[-1] = pi
            else:
                peaks.append(pi)
        peaks = self._merge_plateau_peaks(peaks, ndvi_smooth, min_prom)

        cycles: List[CropCycle] = []
        used = -1
        for k, peak_idx in enumerate(peaks):
            if peak_idx <= used:
                continue
            next_peak = peaks[k + 1] if k + 1 < len(peaks) else None
            sow_idx, harv_idx = self._trough_window(
                ndvi_smooth, peak_idx,
                lo=used + 1,
                hi=(int(next_peak) - 1) if next_peak is not None else n - 1,
                mbin=mbin,
                half_bins=half,
            )
            duration = (reg_dates[harv_idx] - reg_dates[sow_idx]).days
            peak_ndvi_v = float(ndvi_smooth[peak_idx])
            peak_cvi_v = float(cvi_smooth[peak_idx])
            base = float(np.nanmean(ndvi_smooth[max(0, sow_idx - 2): sow_idx + 2]))
            ndvi_rise_v = peak_ndvi_v - base
            if ndvi_rise_v < min_rise:
                continue
            if not (min_days <= duration <= max_days):
                continue

            c_start, c_end = reg_dates[sow_idx], reg_dates[harv_idx]
            cycle_gaps = [
                g for g in cloud_gaps
                if g.start_date <= c_end and g.end_date >= c_start
            ]
            if observed_mask is not None and len(observed_mask) == n:
                seg_mask = observed_mask[sow_idx: harv_idx + 1]
                n_obs = int(np.count_nonzero(seg_mask))
                n_win = int(len(seg_mask))
                peak_observed = bool(observed_mask[peak_idx])
            else:
                n_obs = n_win = int(harv_idx - sow_idx + 1)
                peak_observed = True

            ndvi_seg = reg_ndvi[sow_idx: harv_idx + 1]
            day_offsets = np.array(
                [(reg_dates[j] - reg_dates[sow_idx]).days
                 for j in range(sow_idx, harv_idx + 1)],
                dtype=float,
            )
            cycles.append(CropCycle(
                sowing_date=reg_dates[sow_idx],
                harvest_date=reg_dates[harv_idx],
                peak_date=reg_dates[peak_idx],
                duration_days=duration,
                crop_type=self._crop_type(duration, peak_ndvi_v, peak_cvi_v),
                peak_ndvi=peak_ndvi_v,
                baseline_ndvi=float(np.nanmean(ndvi_smooth[max(0, sow_idx - 3): sow_idx + 1])),
                ndvi_rise=ndvi_rise_v,
                integral_ndvi=float(np.nansum(ndvi_seg)),
                integral_ndvi_days=(
                    float(np.trapz(ndvi_seg, day_offsets)) if len(ndvi_seg) > 1 else 0.0
                ),
                peak_evi=float(np.nanmean(reg_evi[peak_idx - 1: peak_idx + 2])),
                peak_ndmi=float(np.nanmean(reg_ndmi[peak_idx - 1: peak_idx + 2])),
                peak_cvi=peak_cvi_v,
                confidence=self._confidence(ndvi_seg, duration, peak_cvi_v, sum(g.gap_days for g in cycle_gaps)),
                cloud_gap_days=sum(g.gap_days for g in cycle_gaps),
                has_cloud_gap=len(cycle_gaps) > 0,
                peak_observed=peak_observed,
                observed_fraction=(n_obs / n_win) if n_win else 0.0,
                n_observed_bins=n_obs,
                n_bins=n_win,
            ))
            used = harv_idx
        return cycles

    def _detect_perennial(
        self,
        cvi_smooth:  np.ndarray,
        ndvi_smooth: np.ndarray,
        reg_ndvi:    np.ndarray,
        reg_evi:     np.ndarray,
        reg_ndmi:    np.ndarray,
        reg_dates:   List[datetime],
        bin_days:    float,
        *,
        low_cvi: float,
        observed_mask: Optional[np.ndarray] = None,
    ) -> List[CropCycle]:
        """
        Detect a perennial planting and express it as annual production cycles.

        Trigger: the signal is persistently vegetated and essentially never
        returns to bare ground. That is the defining behaviour of an orchard,
        banana or ratooned sugarcane — and it is exactly the pattern the annual
        detector cannot represent.

        NOT the same as "flat and green" in general: dense natural forest looks
        similar. Separating the two is the land-cover gate's job (which has the
        spectral evidence and a registry hint); here we only decide whether the
        SHAPE is perennial. Being permissive is the right bias — a woodlot that
        reaches this point will score poorly on vigor and stability anyway,
        whereas refusing to represent an orchard makes it unscoreable.

        Returns one CropCycle per agronomic year so every downstream stage
        (performance, weather alignment, land utilization) works unchanged.
        """
        P = PipelineConfig
        n = len(reg_dates)
        if n < int(getattr(P, "PERENNIAL_MIN_BINS", 18)):
            return []

        finite = np.isfinite(cvi_smooth)
        if finite.sum() < n * 0.5:
            return []
        vals = cvi_smooth[finite]

        veg_floor = float(getattr(P, "PERENNIAL_MIN_VEGETATION_VS", 0.50))
        max_bare = float(getattr(P, "PERENNIAL_MAX_BARE_FRACTION", 0.05))
        max_amp = float(getattr(P, "PERENNIAL_MAX_AMPLITUDE", 0.30))

        frac_bare = float(np.mean(vals < low_cvi))
        p10 = float(np.percentile(vals, 10))
        p90 = float(np.percentile(vals, 90))
        amplitude = p90 - p10

        # Persistently vegetated, essentially never bare, limited seasonal swing.
        if not (p10 >= veg_floor and frac_bare <= max_bare and amplitude <= max_amp):
            logger.debug(
                "Not perennial: p10=%.3f (need >=%.2f) bare_frac=%.3f (need <=%.2f) "
                "amplitude=%.3f (need <=%.2f)",
                p10, veg_floor, frac_bare, max_bare, amplitude, max_amp,
            )
            return []

        # Slice into agronomic years from the record start.
        bins_per_year = max(1, int(round(365.0 / max(bin_days, 1.0))))
        cycles: List[CropCycle] = []
        start = 0
        while start < n - 1:
            end = min(n - 1, start + bins_per_year - 1)
            # Trailing stub shorter than half a year is folded into the previous
            # year rather than emitted as a partial production cycle.
            if (end - start + 1) < bins_per_year * 0.5 and cycles:
                break

            seg = slice(start, end + 1)
            seg_cvi = cvi_smooth[seg]
            seg_ndvi = reg_ndvi[seg]
            if not np.isfinite(seg_cvi).any():
                start = end + 1
                continue

            peak_off = int(np.nanargmax(seg_cvi))
            peak_idx = start + peak_off
            duration = (reg_dates[end] - reg_dates[start]).days

            day_offsets = np.array(
                [(reg_dates[j] - reg_dates[start]).days for j in range(start, end + 1)],
                dtype=float,
            )

            if observed_mask is not None and len(observed_mask) == n:
                seg_mask = observed_mask[seg]
                n_obs, n_win = int(np.count_nonzero(seg_mask)), int(len(seg_mask))
                peak_observed = bool(observed_mask[peak_idx])
            else:
                n_obs = n_win = int(end - start + 1)
                peak_observed = True

            cycles.append(CropCycle(
                # A perennial has no sowing or harvest in the annual sense.
                # These carry the production-year window so downstream date
                # handling works; cycle_kind marks what they actually mean.
                sowing_date        = reg_dates[start],
                harvest_date       = reg_dates[end],
                peak_date          = reg_dates[peak_idx],
                duration_days      = duration,
                crop_type          = "PERENNIAL",
                peak_ndvi          = float(np.nanmax(seg_ndvi)),
                baseline_ndvi      = float(np.nanpercentile(seg_ndvi, 10)),
                ndvi_rise          = float(
                    np.nanmax(seg_ndvi) - np.nanpercentile(seg_ndvi, 10)
                ),
                integral_ndvi      = float(np.nansum(seg_ndvi)),
                integral_ndvi_days = (
                    float(np.trapz(seg_ndvi, day_offsets)) if len(seg_ndvi) > 1 else 0.0
                ),
                peak_evi           = float(np.nanmean(reg_evi[seg])),
                peak_ndmi          = float(np.nanmean(reg_ndmi[seg])),
                peak_cvi           = float(np.nanmax(seg_cvi)),
                confidence         = self._perennial_confidence(seg_cvi, n_obs, n_win),
                cloud_gap_days     = 0,
                has_cloud_gap      = False,
                peak_observed      = peak_observed,
                observed_fraction  = (n_obs / n_win) if n_win else 0.0,
                n_observed_bins    = n_obs,
                n_bins             = n_win,
                cycle_kind         = "perennial",
            ))
            start = end + 1

        return cycles

    @staticmethod
    def _perennial_confidence(seg_cvi: np.ndarray, n_obs: int, n_win: int) -> float:
        """
        Confidence in a perennial production year.

        Rewards a HIGH and STEADY canopy — the opposite of the annual case,
        where variation is the signal. Also scales with how much of the window
        was actually observed rather than reconstructed.
        """
        vals = seg_cvi[np.isfinite(seg_cvi)]
        if vals.size == 0:
            return 10.0
        mean_v = float(np.mean(vals))
        cv = float(np.std(vals) / mean_v) if mean_v > 0 else 1.0
        level = float(np.clip(mean_v / 0.75, 0.0, 1.0)) * 55.0
        steadiness = float(np.clip(1.0 - cv * 3.0, 0.0, 1.0)) * 30.0
        coverage = (n_obs / n_win if n_win else 0.0) * 15.0
        return float(np.clip(level + steadiness + coverage, 10.0, 100.0))

    @staticmethod
    def _resolve_sow_index(
        cvi_smooth: np.ndarray,
        reg_dates: List[datetime],
        peak_idx: int,
        low_cvi: float,
        mbin: int,
        xbin: int,
        max_days: int,
        sowing_hint: Optional[datetime] = None,
    ) -> Tuple[int, bool]:
        """
        Walk back from peak for sowing; optionally soft-bias toward sowing_hint ±21d.

        Returns (sow_idx, biased) where biased is True only when a hint-window
        candidate was preferred over the default walk-back result.
        """
        lo_bound = max(0, peak_idx - xbin)
        hi_bound = peak_idx - mbin

        default_sow = lo_bound
        crossed = False
        for j in range(hi_bound, lo_bound - 1, -1):
            if float(cvi_smooth[j]) < low_cvi:
                default_sow = j
                crossed = True
                break

        if not crossed and hi_bound >= lo_bound:
            # RELATIVE TROUGH FALLBACK.
            #
            # Intensively double-cropped land never returns to bare soil: one
            # crop follows another with barely any fallow interval, and after
            # 10-day binning plus smoothing the trough never crosses low_cvi.
            # Requiring an absolute bare-soil crossing then walked sowing all
            # the way back to peak - max_days, producing a duration over the cap
            # and rejecting the cycle outright.
            #
            # Observed on farmer 14322905350 (western UP wheat-rice): NDVI
            # amplitude 0.41, peaking at 0.78, never below 0.20 — unmistakably
            # cropped, and ZERO cycles detected. The bias fell hardest on the
            # most productive farms, which is the worst direction to be wrong in.
            #
            # Standard phenology practice takes the trough BETWEEN cycles rather
            # than an absolute threshold. The rise from that trough is still
            # checked against min_rise by the caller, so a flat signal cannot
            # manufacture a cycle this way — it only stops a real one being
            # thrown away for not going bare.
            segment = cvi_smooth[lo_bound: hi_bound + 1]
            if segment.size and np.isfinite(segment).any():
                default_sow = lo_bound + int(np.nanargmin(segment))

        if sowing_hint is None:
            return default_sow, False

        peak_dt = reg_dates[peak_idx]
        days_after_hint = (peak_dt - sowing_hint).days
        # Peak is "nearby" the sow hint if it falls in a plausible post-sow window
        if not (0 <= days_after_hint <= max_days + _SOW_HINT_WINDOW_DAYS):
            return default_sow, False

        lo = max(0, peak_idx - xbin)
        hi = peak_idx - mbin
        best_j: Optional[int] = None
        best_dist: Optional[int] = None
        for j in range(hi, lo - 1, -1):
            if float(cvi_smooth[j]) >= low_cvi:
                continue
            dist = abs((reg_dates[j] - sowing_hint).days)
            if dist > _SOW_HINT_WINDOW_DAYS:
                continue
            if best_dist is None or dist < best_dist:
                best_j, best_dist = j, dist

        if best_j is None or best_j == default_sow:
            return default_sow, False
        return best_j, True

    # ─────────────────────────────────────────────────────
    # PREPROCESSING
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _detect_cloud_gaps(dt_dates: List[datetime]) -> List[CloudGap]:
        """Flag consecutive observation gaps ≥ _CLOUD_GAP_MIN_DAYS days."""
        gaps = []
        for i in range(len(dt_dates) - 1):
            gap = (dt_dates[i + 1] - dt_dates[i]).days
            if gap >= _CLOUD_GAP_MIN_DAYS:
                gaps.append(CloudGap(
                    start_date=dt_dates[i], end_date=dt_dates[i + 1],
                    gap_days=gap, start_index=i, end_index=i + 1,
                ))
        return gaps

    @staticmethod
    def _detect_sparse_ndvi_gaps(dt_dates: List[datetime], ndvi: np.ndarray) -> List[CloudGap]:
        """Large calendar gaps between bins that still have finite NDVI (missing grid slots)."""
        gaps: List[CloudGap] = []
        valid_ix = [i for i in range(len(dt_dates)) if np.isfinite(ndvi[i])]
        for k in range(len(valid_ix) - 1):
            i0, i1 = valid_ix[k], valid_ix[k + 1]
            gap_days = (dt_dates[i1] - dt_dates[i0]).days
            if gap_days >= _CLOUD_GAP_MIN_DAYS:
                gaps.append(CloudGap(
                    start_date=dt_dates[i0],
                    end_date=dt_dates[i1],
                    gap_days=gap_days,
                    start_index=i0,
                    end_index=i1,
                ))
        return gaps

    def _regularise_and_impute(
        self,
        dt_dates:   List[datetime],
        ndvi:       np.ndarray,
        evi:        np.ndarray,
        ndmi:       np.ndarray,
        cloud_gaps: List[CloudGap],
        step_days:  float,
    ) -> Tuple[List[datetime], np.ndarray, np.ndarray, np.ndarray]:
        """
        Map irregular observations onto a uniform step_days grid, then fill gaps.

        Short gaps  (< 48 d): linear interpolation between known bins.
        Long gaps   (≥ 36 d): hat-shaped profile when post-gap CVI is declining
                               (Kharif peak hidden inside a monsoon blackout).
        """
        P            = PipelineConfig
        step         = float(step_days)
        short_max    = float(getattr(P, "CYCLE_IMPUTE_SHORT_GAP_MAX_DAYS",  48))
        long_min     = float(getattr(P, "CYCLE_IMPUTE_LONG_GAP_MIN_DAYS",   36))
        decline_look = int(  getattr(P, "CYCLE_IMPUTE_POST_DECLINE_LOOK",    6))
        decline_delta= float(getattr(P, "CYCLE_IMPUTE_DECLINE_DELTA",       0.028))

        start, end = dt_dates[0], dt_dates[-1]
        grid_dates = [start + timedelta(days=d) for d in range(0, (end - start).days + 1, int(step))]
        if grid_dates[-1] < end:
            grid_dates.append(end)

        obs_offsets  = np.array([(d - start).days for d in dt_dates],   dtype=float)
        grid_offsets = np.array([(d - start).days for d in grid_dates], dtype=float)
        n_grid = len(grid_dates)

        # Snap observations to nearest grid bin (mean if multiple hit same bin)
        reg_ndvi = self._snap_to_grid(obs_offsets, ndvi, n_grid, step)
        reg_ndmi = self._snap_to_grid(obs_offsets, ndmi, n_grid, step)

        evi_frac     = float(np.sum(np.isfinite(evi))) / max(len(evi), 1)
        use_evi_proxy = evi_frac < 0.30
        reg_evi = (self._snap_to_grid(obs_offsets, evi, n_grid, step)
                   if not use_evi_proxy else np.full(n_grid, np.nan))

        # Fill edges with linear extrapolation (not flat clamp)
        reg_ndvi = self._extrapolate_edges(reg_ndvi)
        reg_ndmi = self._extrapolate_edges(reg_ndmi, fill=0.0)
        if not use_evi_proxy:
            reg_evi = self._extrapolate_edges(reg_evi)

        # Fill short gaps
        reg_ndvi = self._fill_short(reg_ndvi, grid_offsets, step, short_max)
        reg_ndmi = self._fill_short(reg_ndmi, grid_offsets, step, short_max)
        if not use_evi_proxy:
            reg_evi = self._fill_short(reg_evi, grid_offsets, step, short_max)

        # Fill long gaps with context-shaped profiles
        reg_ndvi = self._fill_long(reg_ndvi, grid_offsets, step, long_min, decline_look, decline_delta)
        reg_ndmi = self._fill_long(reg_ndmi, grid_offsets, step, long_min, decline_look, decline_delta, ref=reg_ndvi)
        if not use_evi_proxy:
            reg_evi = self._fill_long(reg_evi, grid_offsets, step, long_min, decline_look, decline_delta, ref=reg_ndvi)

        # Second short pass — clean up any residuals left after long-gap fill
        reg_ndvi = self._fill_short(reg_ndvi, grid_offsets, step, short_max)
        reg_ndmi = self._fill_short(reg_ndmi, grid_offsets, step, short_max)
        if not use_evi_proxy:
            reg_evi = self._fill_short(reg_evi, grid_offsets, step, short_max)

        # Replace any remaining NaNs with series mean
        def _fill_mean(arr: np.ndarray, fallback: float) -> np.ndarray:
            z = arr.copy()
            bad = ~np.isfinite(z)
            if np.any(bad):
                z[bad] = float(np.nanmean(z[np.isfinite(z)])) if np.any(np.isfinite(z)) else fallback
            return z

        reg_ndvi = _fill_mean(reg_ndvi, 0.22)
        reg_ndmi = _fill_mean(reg_ndmi, 0.00)
        reg_evi  = (_fill_mean(reg_evi, 0.20)
                    if not use_evi_proxy
                    else np.clip(0.85 * reg_ndvi + 0.02, 0.0, 1.0))

        if use_evi_proxy:
            logger.debug("EVI sparse — using proxy: EVI ≈ 0.85·NDVI + 0.02")

        return (
            grid_dates,
            np.clip(reg_ndvi, -0.1,  1.0),
            np.clip(reg_evi,   0.0,  1.0),
            np.clip(reg_ndmi, -0.6,  0.8),
        )

    # ── Grid helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _snap_to_grid(
        obs_offsets: np.ndarray,
        values:      np.ndarray,
        n_grid:      int,
        step:        float,
    ) -> np.ndarray:
        """Bin irregular observations onto the nearest uniform grid cell (mean per bin)."""
        acc_sum = np.zeros(n_grid, dtype=float)
        acc_cnt = np.zeros(n_grid, dtype=int)
        for off, v in zip(obs_offsets, values):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(fv):
                continue
            gi = int(np.clip(int(round(off / step)), 0, n_grid - 1))
            acc_sum[gi] += fv
            acc_cnt[gi] += 1
        out = np.full(n_grid, np.nan, dtype=float)
        m = acc_cnt > 0
        out[m] = acc_sum[m] / acc_cnt[m]
        return out

    @staticmethod
    def _extrapolate_edges(arr: np.ndarray, fill: float = 0.22) -> np.ndarray:
        """Extend leading/trailing NaN bins using the local slope of the first/last valid pair."""
        a = arr.copy()
        good = np.where(np.isfinite(a))[0]
        if len(good) == 0:
            a[:] = fill
            return a
        fi, li = int(good[0]), int(good[-1])
        if fi > 0:
            slope = float(a[fi + 1] - a[fi]) if fi + 1 < len(a) and np.isfinite(a[fi + 1]) else 0.0
            for j in range(fi - 1, -1, -1):
                a[j] = a[j + 1] - slope
        if li < len(a) - 1:
            slope = float(a[li] - a[li - 1]) if li > 0 and np.isfinite(a[li - 1]) else 0.0
            for j in range(li + 1, len(a)):
                a[j] = a[j - 1] + slope
        return a

    @staticmethod
    def _iter_nan_runs(arr: np.ndarray):
        """Yield (lo, hi) inclusive index pairs for each contiguous NaN segment."""
        n, i = len(arr), 0
        while i < n:
            if np.isfinite(arr[i]):
                i += 1
                continue
            lo = i
            while i < n and not np.isfinite(arr[i]):
                i += 1
            yield lo, i - 1

    def _fill_short(
        self,
        arr:          np.ndarray,
        grid_offsets: np.ndarray,
        step:         float,
        short_max:    float,
    ) -> np.ndarray:
        """Linear interpolation for NaN runs shorter than short_max days."""
        a = arr.copy()
        for lo, hi in self._iter_nan_runs(a):
            if lo == 0 or hi == len(a) - 1:
                continue
            span = float(grid_offsets[hi] - grid_offsets[lo] + step)
            if span > short_max:
                continue
            n = hi - lo + 1
            a[lo: hi + 1] = np.linspace(float(a[lo - 1]), float(a[hi + 1]), n + 2)[1:-1]
        return a

    def _fill_long(
        self,
        arr:          np.ndarray,
        grid_offsets: np.ndarray,
        step:         float,
        long_min:     float,
        decline_look: int,
        decline_delta: float,
        ref:          Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Context-aware fill for long NaN runs (monsoon cloud blackouts).

        When the signal *after* the gap is declining (post-peak senescence typical
        of Kharif rice), a hat-shaped profile is imputed so the peak inside the gap
        is preserved for cycle detection.  Otherwise a straight line is used.
        """
        a = arr.copy()
        r = a if ref is None else ref
        for lo, hi in list(self._iter_nan_runs(a)):
            if lo == 0 or hi == len(a) - 1:
                continue
            span = float(grid_offsets[hi] - grid_offsets[lo] + step)
            if span < long_min:
                continue
            y0, y1 = float(a[lo - 1]), float(a[hi + 1])
            n = hi - lo + 1
            xs = np.linspace(0.0, 1.0, n + 2)[1:-1]

            # Detect declining post-gap signal
            post = [float(r[k]) for k in range(hi + 1, min(hi + 1 + decline_look, len(r)))
                    if np.isfinite(r[k])]
            declining = (
                len(post) >= 3
                and float(np.mean(post[-2:])) < float(np.mean(post[:2])) - decline_delta
            )

            if declining:
                base  = max(y0, y1, 0.18)
                pk    = min(0.82, base + min(0.22, 0.35 * max(0.0, 0.55 - min(y0, y1))))
                pt    = 0.50 if y1 < y0 + 0.08 else 0.58
                fill  = np.where(
                    xs <= pt,
                    y0 + (pk - y0) * (xs / max(pt, 1e-6)),
                    pk + (y1 - pk) * ((xs - pt) / max(1.0 - pt, 1e-6)),
                )
            else:
                fill = np.linspace(y0, y1, n)

            a[lo: hi + 1] = np.clip(fill, -0.1, 1.0)
        return a

    # ─────────────────────────────────────────────────────
    # CVI + SMOOTHING
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _build_cvi(
        reg_ndvi: np.ndarray,
        reg_evi:  np.ndarray,
        reg_ndmi: np.ndarray,
    ) -> np.ndarray:
        """CVI = 0.5·NDVI + 0.3·EVI + 0.2·NDMI  (clipped to [0, 1])."""
        w = _CVI_W
        return np.clip(w['ndvi'] * reg_ndvi + w['evi'] * reg_evi + w['ndmi'] * reg_ndmi, 0.0, 1.0)

    @staticmethod
    def _smooth(signal: np.ndarray, window: int = 7) -> np.ndarray:
        """
        Bartlett (triangular) weighted moving average.

        Chosen over scipy savgol_filter (native-crash risk in this runtime) and
        uniform moving average (which flattens true peaks).
        """
        if window % 2 == 0:
            window += 1
        window = min(window, len(signal) if len(signal) % 2 == 1 else len(signal) - 1)
        if window < 3:
            return signal.copy().astype(float)
        half    = window // 2
        weights = np.bartlett(window)
        out     = signal.copy().astype(float)
        for i in range(len(signal)):
            s = max(0, i - half)
            e = min(len(signal), i + half + 1)
            w = weights[half - (i - s): half + (e - i)]
            out[i] = np.average(signal[s:e], weights=w)
        return out

    # ─────────────────────────────────────────────────────
    # PILLAR 2 — double-logistic phenometrics + cloud-gap inference
    # ─────────────────────────────────────────────────────

    def _regularise_single(
        self,
        dt_dates: List[datetime],
        values: np.ndarray,
        reg_dates: List[datetime],
        step: float,
    ) -> Optional[np.ndarray]:
        """
        Snap one extra series (e.g. the Pillar-1 composite VS) onto the SAME grid
        produced by _regularise_and_impute, then short-gap + edge fill. Returns
        None if the series is too sparse to be trusted as the detection signal.
        """
        try:
            start = dt_dates[0]
            obs_off = np.array([(d - start).days for d in dt_dates], dtype=float)
            n_grid = len(reg_dates)
            reg = self._snap_to_grid(obs_off, np.asarray(values, dtype=float), n_grid, step)
            if np.isfinite(reg).sum() < max(6, n_grid // 6):
                return None
            reg = self._extrapolate_edges(reg)
            grid_off = np.array([(d - start).days for d in reg_dates], dtype=float)
            short_max = float(getattr(PipelineConfig, "CYCLE_IMPUTE_SHORT_GAP_MAX_DAYS", 48))
            reg = self._fill_short(reg, grid_off, step, short_max)
            bad = ~np.isfinite(reg)
            if bad.any():
                reg[bad] = float(np.nanmean(reg[np.isfinite(reg)])) if np.isfinite(reg).any() else 0.2
            return np.clip(reg, -0.1, 1.0)
        except Exception as e:
            logger.debug("composite regularise failed: %s", e)
            return None

    @staticmethod
    def _double_logistic(t, base, amp, sos, m_s, eos, m_a):
        """Beck-style double-logistic: green-up minus senescence."""
        up = 1.0 / (1.0 + np.exp(-m_s * (t - sos)))
        down = 1.0 / (1.0 + np.exp(-m_a * (t - eos)))
        return base + amp * (up - down)

    @classmethod
    def _fit_double_logistic(cls, t: np.ndarray, y: np.ndarray):
        """
        Fit the double-logistic to one cycle window. Returns (params, r2) or (None, None).

        Can be disabled via PHENO_FIT_ENABLED / env PHENO_FIT_DISABLE=1.

        WHY that switch exists: curve_fit descends into LAPACK, and a broken or
        mismatched BLAS/LAPACK build can abort the process at the native level
        (observed: Windows 0xc06d007f inside dgelsd) — which no Python `except`
        can catch. Phenology refinement is an enhancement, not a prerequisite
        for scoring, so it must be possible to turn off without losing the
        assessment. Cycles still get walked SOS/EOS dates; only the fitted
        refinement is skipped.
        """
        if not _SCIPY_OPT or not _pheno_fit_enabled():
            return None, None
        t = np.asarray(t, dtype=float)
        y = np.asarray(y, dtype=float)
        good = np.isfinite(t) & np.isfinite(y)
        if good.sum() < 6:
            return None, None
        t, y = t[good], y[good]
        base0 = float(np.nanmin(y))
        amp0 = float(max(np.nanmax(y) - base0, 0.05))
        span = float(t[-1] - t[0]) or 1.0
        p0 = [base0, amp0, t[0] + 0.25 * span, 0.10, t[0] + 0.75 * span, 0.10]
        lb = [-0.2, 0.02, t[0] - span, 0.005, t[0], 0.005]
        ub = [1.0, 1.6, t[-1], 2.0, t[-1] + span, 2.0]
        try:
            popt, _ = _curve_fit(cls._double_logistic, t, y, p0=p0, bounds=(lb, ub), maxfev=10000)
        except BaseException as e:
            # Broad on purpose: a fit that cannot converge, overflows, or trips a
            # linear-algebra error must degrade to "no refinement", never
            # propagate out of an optional enrichment step.
            logger.debug("double-logistic fit failed (%s): %s", type(e).__name__, e)
            return None, None
        yhat = cls._double_logistic(t, *popt)
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-9
        r2 = 1.0 - ss_res / ss_tot
        keys = ["base", "amp", "sos", "m_s", "eos", "m_a"]
        return dict(zip(keys, [float(v) for v in popt])), float(r2)

    @classmethod
    def _phenometrics_from_fit(cls, params: Dict, t_lo: float, t_hi: float, amp_frac: float) -> Dict:
        """Derive SOS/POS/EOS offsets by crossing amp_frac of amplitude on the fitted curve."""
        t = np.linspace(t_lo, t_hi, 240)
        f = cls._double_logistic(
            t, params["base"], params["amp"], params["sos"],
            params["m_s"], params["eos"], params["m_a"],
        )
        base, amp = params["base"], params["amp"]
        thr = base + amp_frac * amp
        pos_i = int(np.argmax(f))
        sos_t = None
        for i in range(1, pos_i + 1):
            if f[i - 1] < thr <= f[i]:
                sos_t = float(t[i]); break
        if sos_t is None:
            sos_t = float(params["sos"])
        eos_t = None
        for i in range(pos_i + 1, len(t)):
            if f[i - 1] >= thr > f[i]:
                eos_t = float(t[i]); break
        if eos_t is None:
            eos_t = float(params["eos"])
        # Baseline-subtracted integral over SOS->EOS ("small integral" in the
        # TIMESAT sense): the productive signal above the parcel's own floor,
        # which is what a biomass proxy should measure. The cycle's existing
        # integral_ndvi_days is computed over the WALKED window on raw NDVI and
        # is not baseline-subtracted, so it carries the soil background with it.
        in_season = (t >= sos_t) & (t <= eos_t)
        small_integral = (
            float(np.trapz(np.clip(f[in_season] - base, 0.0, None), t[in_season]))
            if in_season.sum() > 1 else 0.0
        )

        return {
            "sos_offset": sos_t, "pos_offset": float(t[pos_i]), "eos_offset": eos_t,
            "base": float(base), "amp": float(amp), "peak_fitted": float(f[pos_i]),
            # Length of season, named. Previously only implicit in the dates.
            "los_days": float(max(0.0, eos_t - sos_t)),
            # Green-up and senescence rates. These were FITTED by the
            # double-logistic (params m_s / m_a) and then discarded — the single
            # cheapest high-value loss in this file. They describe how fast the
            # canopy established and how fast it senesced, which distinguishes a
            # vigorous establishment from a struggling one even when peak NDVI
            # ends up similar. Units: signal units per day.
            "greenup_rate": float(params["m_s"]) * float(amp) / 4.0,
            "senescence_rate": float(params["m_a"]) * float(amp) / 4.0,
            "greenup_slope_param": float(params["m_s"]),
            "senescence_slope_param": float(params["m_a"]),
            "small_integral": small_integral,
        }

    def _refine_cycle_phenology(
        self,
        cycle: "CropCycle",
        reg_dates: List[datetime],
        signal: np.ndarray,
        pad_bins: int = 3,
    ) -> None:
        """
        Fit a double-logistic around one detected cycle and, when the fit is
        confident, refine sowing/peak/harvest to amplitude-based SOS/POS/EOS.
        Always attaches ``cycle.phenology`` (with fit_ok flag). Walked dates are
        preserved as a fallback and for audit.
        """
        amp_frac = float(getattr(PipelineConfig, "PHENO_AMP_FRACTION", _PHENO_AMP_FRACTION))
        min_r2 = float(getattr(PipelineConfig, "PHENO_FIT_MIN_R2", _PHENO_FIT_MIN_R2))

        offs = np.array([(d - reg_dates[0]).days for d in reg_dates], dtype=float)

        def nearest_idx(dt: datetime) -> int:
            return int(np.argmin(np.abs(offs - (dt - reg_dates[0]).days)))

        s_i, h_i = nearest_idx(cycle.sowing_date), nearest_idx(cycle.harvest_date)
        lo = max(0, s_i - pad_bins)
        hi = min(len(reg_dates) - 1, h_i + pad_bins)
        walked = {
            "walked_sowing": cycle.sowing_date.strftime("%Y-%m-%d"),
            "walked_harvest": cycle.harvest_date.strftime("%Y-%m-%d"),
        }
        if hi - lo < 5:
            cycle.phenology = {"fit_ok": False, "reason": "window_too_short", **walked}
            return

        base_date = reg_dates[lo]
        t = np.array([(reg_dates[i] - base_date).days for i in range(lo, hi + 1)], dtype=float)
        y = np.asarray(signal[lo:hi + 1], dtype=float)
        params, r2 = self._fit_double_logistic(t, y)
        if params is None or r2 is None or r2 < min_r2:
            cycle.phenology = {
                "fit_ok": False, "method": "double_logistic",
                "r2": (None if r2 is None else round(r2, 3)), **walked,
            }
            return

        m = self._phenometrics_from_fit(params, float(t[0]), float(t[-1]), amp_frac)
        max_off = (reg_dates[hi] - base_date).days

        def _to_date(offset: float) -> datetime:
            return base_date + timedelta(days=float(np.clip(offset, 0, max_off)))

        sos_d, pos_d, eos_d = _to_date(m["sos_offset"]), _to_date(m["pos_offset"]), _to_date(m["eos_offset"])
        walked_dur = max((cycle.harvest_date - cycle.sowing_date).days, 1)
        fit_dur = (eos_d - sos_d).days
        ordering_ok = sos_d < pos_d < eos_d and fit_dur >= 30
        ratio = fit_dur / walked_dur
        # Adopt fitted dates as operative ONLY when they agree with the walked
        # window (0.6×–1.4× duration). Otherwise the fit is kept as annotation
        # but the tuned walked dates remain authoritative — avoids the
        # double-logistic silently compressing/expanding a good cycle.
        adopt = bool(ordering_ok and 0.6 <= ratio <= 1.4)
        cycle.phenology = {
            "fit_ok": True, "method": "double_logistic", "r2": round(float(r2), 3),
            "sos": sos_d.strftime("%Y-%m-%d"), "pos": pos_d.strftime("%Y-%m-%d"),
            "eos": eos_d.strftime("%Y-%m-%d"),
            "amplitude": round(m["amp"], 3), "base": round(m["base"], 3),
            "amp_threshold_frac": amp_frac,
            "fit_duration_days": fit_dur, "walked_duration_days": walked_dur,
            # Standard phenology metrics, previously computed and discarded.
            "los_days": round(m["los_days"], 1),
            "greenup_rate": round(m["greenup_rate"], 5),
            "senescence_rate": round(m["senescence_rate"], 5),
            "small_integral": round(m["small_integral"], 3),
            "refined_dates_adopted": adopt, **walked,
        }
        # Goodness of fit belongs in the cycle's confidence. It was computed
        # right here and never used, so `confidence` reflected peak height and
        # variation only, with no measure of how well the curve actually
        # described the observations.
        cycle.confidence = float(np.clip(
            cycle.confidence * (0.80 + 0.20 * float(np.clip(r2, 0.0, 1.0))),
            10.0, 100.0,
        ))
        if adopt:
            cycle.sowing_date, cycle.peak_date, cycle.harvest_date = sos_d, pos_d, eos_d
            cycle.duration_days = fit_dur

    @classmethod
    def _infer_seasonal_presence(cls, reg_dates: List[datetime], signal: np.ndarray) -> Dict[str, Any]:
        """
        Cloud-gap-robust kharif inference from pre-/post-monsoon signal.

        For each crop year in the series compare pre-monsoon (Apr–May) and
        post-monsoon (Sep–Oct) mean signal to classify what happened in the
        cloudy Jun–Aug window:
          pre LOW  & post HIGH  -> kharif sown in the gap
          pre HIGH & post HIGH  -> summer / long-duration crop already present
                                    (no *fresh* kharif sowing)
          pre LOW  & post LOW   -> no kharif
        This informs / flags; it never fabricates a cycle.
        """
        P = PipelineConfig
        low = float(getattr(P, "PHENO_PREMONSOON_LOW", _PHENO_PREMONSOON_LOW))
        high = float(getattr(P, "PHENO_POSTMONSOON_HIGH", _PHENO_POSTMONSOON_HIGH))
        arr = np.asarray(signal, dtype=float)

        def _win_mean(y: int, m0: int, d0: int, m1: int, d1: int) -> Optional[float]:
            lo_d, hi_d = date(y, m0, d0), date(y, m1, d1)
            vals = [
                arr[i] for i, dd in enumerate(reg_dates)
                if lo_d <= dd.date() <= hi_d and np.isfinite(arr[i])
            ]
            return float(np.mean(vals)) if vals else None

        per_year: Dict[str, Any] = {}
        for yr in sorted({d.year for d in reg_dates}):
            pre = _win_mean(yr, 4, 1, 5, 31)
            mid = _win_mean(yr, 7, 1, 8, 31)
            post = _win_mean(yr, 9, 1, 10, 31)
            if pre is not None and post is not None and pre < low and post >= high:
                label = "kharif_sown_in_gap"
            elif pre is not None and pre >= high and (post is None or post >= high):
                label = "summer_or_long_duration_present"
            elif (pre is None or pre < low) and (post is None or post < low):
                label = "no_kharif"
            else:
                label = "ambiguous"
            per_year[str(yr)] = {
                "pre_monsoon_apr_may": None if pre is None else round(pre, 3),
                "monsoon_jul_aug": None if mid is None else round(mid, 3),
                "post_monsoon_sep_oct": None if post is None else round(post, 3),
                "classification": label,
            }
        return {
            "thresholds": {"pre_low": low, "post_high": high},
            "per_year": per_year,
        }

    # ─────────────────────────────────────────────────────
    # SCORING + LABELLING
    # ─────────────────────────────────────────────────────

    def _confidence(
        self,
        ndvi_seg:       np.ndarray,
        duration_days:  int,
        peak_cvi:       float,
        cloud_gap_days: int,
    ) -> float:
        """
        Confidence score 0–100.
          variation_score  25 pts — temporal NDVI variance
          duration_score   25 pts — reward mid-range duration vs ``CROP_CYCLE_MAX_DURATION_DAYS``
          peak_score       25 pts — peak CVI strength
          shape_score      25 pts — arc shape proxy (peak strength + temporal variance)
        Cloud-gap penalty: –cloud_gap_penalty per 7-day gap.
        """
        variation = min(25.0, float(np.nanstd(ndvi_seg)) * 150)

        mx_dur = int(getattr(PipelineConfig, "CROP_CYCLE_MAX_DURATION_DAYS", 220))
        if 60 <= duration_days <= mx_dur:
            dur = 25.0
        elif duration_days < 60:
            dur = max(0.0, 25.0 * (duration_days - 40) / 20)
        else:
            dur = max(10.0, 25.0 * (1.0 - (duration_days - mx_dur) / 120))

        peak   = min(25.0, peak_cvi * 40)
        shape  = min(25.0, peak_cvi * 25 + float(np.nanstd(ndvi_seg)) * 50)

        raw     = variation + dur + peak + shape
        penalty = self.cloud_gap_penalty * (cloud_gap_days / 7)
        return float(np.clip(raw - penalty, 10.0, 100.0))

    @staticmethod
    def _crop_type(duration_days: int, peak_ndvi: float, peak_cvi: float) -> str:
        """Provisional crop-type hint from duration and vegetation indices."""
        if duration_days < 90:
            return "SHORT_HIGH_VIGOR" if peak_ndvi > 0.55 else "SHORT_MODERATE"
        if duration_days < 180:
            if peak_cvi > 0.60: return "MEDIUM_HIGH_VIGOR"
            if peak_cvi > 0.45: return "MEDIUM_MODERATE"
            return "MEDIUM_LOW_VIGOR"
        return "LONG_DURATION"

    # ─────────────────────────────────────────────────────
    # SEASON ASSIGNMENT — single canonical implementation
    # ─────────────────────────────────────────────────────
    #
    # This replaces two functions that disagreed with each other. _season_label
    # called Feb-Mar "Rabi" while _assign_season_type called it "Zaid", so the
    # same cycle carried contradictory season fields. Both keyed off the sowing
    # MONTH alone: harvest_date was accepted and ignored, making a crop sown in
    # September and harvested in February "100% kharif". 'cross_season' was
    # unreachable because the month branches above it covered all twelve.
    #
    # Two further problems this fixes:
    #   * No region adjustment, despite latitude / LGD code / eco-region all
    #     being in scope at the call site. Punjab wheat and Tamil Nadu samba
    #     paddy were assigned by the same North-India month map, and southern
    #     cropping calendars are genuinely shifted.
    #   * Assignment by sowing month alone misclassifies any cycle that
    #     straddles a boundary. Overlap is the honest basis: a season is the
    #     one the crop actually spent most of its life in.

    @staticmethod
    def _season_windows(year: int, region_shift_days: int = 0) -> List[Tuple[str, datetime, datetime]]:
        """
        (season_type, start, end) windows for one agronomic year.

        Base windows follow the standard Indian cropping calendar:
            kharif  monsoon-sown, Jun -> Oct
            rabi    post-monsoon, Oct -> Mar
            zaid    short summer, Mar -> Jun
        region_shift_days moves them for regions whose calendar runs later
        (southern India, where the north-east monsoon drives a later rabi).
        """
        d = timedelta(days=int(region_shift_days))
        return [
            ("kharif", datetime(year, 6, 1) + d, datetime(year, 10, 15) + d),
            ("rabi",   datetime(year, 10, 15) + d, datetime(year + 1, 3, 15) + d),
            ("zaid",   datetime(year, 3, 15) + d, datetime(year, 6, 1) + d),
        ]

    @classmethod
    def _region_shift_days(cls, agro_profile: Optional[Dict]) -> int:
        """
        Calendar shift for this location, in days.

        Southern India's cropping calendar runs materially later than the north
        (the north-east monsoon arrives Oct-Dec). Derived from latitude when
        available — the coarsest defensible adjustment, and far better than
        applying one national calendar. Returns 0 when we cannot tell, which
        reproduces the previous behaviour rather than guessing.
        """
        P = PipelineConfig
        if not isinstance(agro_profile, dict):
            return 0
        lat = agro_profile.get("latitude")
        try:
            lat = float(lat)
        except (TypeError, ValueError):
            return 0
        south_lat = float(getattr(P, "SEASON_SOUTH_LATITUDE", 16.0))
        shift = int(getattr(P, "SEASON_SOUTH_SHIFT_DAYS", 30))
        return shift if lat < south_lat else 0

    @classmethod
    def assign_season(
        cls,
        sowing_date: datetime,
        harvest_date: datetime,
        agro_profile: Optional[Dict] = None,
    ) -> Tuple[str, str]:
        """
        Assign (season_type, season_label) by OVERLAP, not by sowing month.

        The season a cycle belongs to is the one it spent the most days in.
        Returns 'cross_season' when no single season holds a clear majority —
        which, unlike the previous implementation, is genuinely reachable.
        """
        if harvest_date < sowing_date:
            harvest_date = sowing_date

        shift = cls._region_shift_days(agro_profile)
        total_days = max((harvest_date - sowing_date).days, 1)

        overlap: Dict[str, int] = {"kharif": 0, "rabi": 0, "zaid": 0}
        for yr in range(sowing_date.year - 1, harvest_date.year + 2):
            for season, w_start, w_end in cls._season_windows(yr, shift):
                lo = max(sowing_date, w_start)
                hi = min(harvest_date, w_end)
                if hi > lo:
                    overlap[season] += (hi - lo).days

        best = max(overlap, key=overlap.get)
        best_days = overlap[best]
        if best_days <= 0:
            best = "kharif"
            best_days = total_days

        share = best_days / total_days
        min_share = float(getattr(PipelineConfig, "SEASON_MIN_DOMINANT_SHARE", 0.55))
        season_type = best if share >= min_share else "cross_season"

        # Label uses the agronomic year the cycle STARTED in. Rabi spans a
        # calendar boundary, so it is written 2023/2024 in the usual way.
        anchor = sowing_date
        if season_type == "rabi" or (season_type == "cross_season" and best == "rabi"):
            y = anchor.year if anchor.month >= 10 else anchor.year - 1
            label = f"Rabi {y}/{y + 1}"
        elif season_type == "zaid":
            label = f"Zaid {anchor.year}"
        elif season_type == "cross_season":
            label = f"Cross-season {anchor.year}"
        else:
            label = f"Kharif {anchor.year}"
        return season_type, label

    @classmethod
    def _season_label(cls, sowing_date: datetime, harvest_date: Optional[datetime] = None,
                      agro_profile: Optional[Dict] = None) -> str:
        """Backwards-compatible wrapper. Prefer assign_season."""
        return cls.assign_season(sowing_date, harvest_date or sowing_date, agro_profile)[1]

    @classmethod
    def _assign_season_type(cls, sowing_date: datetime, harvest_date: datetime,
                            agro_profile: Optional[Dict] = None) -> str:
        """Backwards-compatible wrapper. Prefer assign_season."""
        return cls.assign_season(sowing_date, harvest_date, agro_profile)[0]


__all__ = ['CropCycle', 'CropCycleDetector', 'CloudGap']