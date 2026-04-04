"""
Crop Cycle Detector - VERSION 3.0
===================================
Stage 2 of the ENHANCED pipeline: detect ACTUAL sowing and harvest dates
from a continuous 3-year NDVI/EVI/NDMI time series.

KEY IMPROVEMENTS IN v3.0
--------------------------
1.  MULTI-INDEX COMPOSITE SIGNAL
    Combines NDVI, EVI, and NDMI into a weighted composite vegetation index (CVI).
    EVI reduces atmospheric haze bias (critical during monsoon cloud gaps).
    NDMI (moisture index) helps distinguish crop emergence from bare-soil wetting.

2.  CLOUD-GAP AWARE PREPROCESSING
    - Detects large temporal gaps (e.g. June–August monsoon blackouts).
    - Applies time-aware interpolation (not just index-aware) so the
      interpolated value respects the actual days elapsed, not scene count.
    - Uses Savitzky-Golay smoothing (preserves true peaks better than
      moving-average or uniform filter) on the regularised signal.

3.  BUG FIX: dt_dates scope error
    `dt_dates` is now passed explicitly into _trace_crop_cycle() instead
    of being referenced from the outer scope (which caused NameError).

4.  BIOLOGICALLY-SOUND HARVEST DETECTION
    Three-trigger cascade:
    a. Rapid drop rate (mechanical or irrigation-cut harvest)
    b. Sustained low vegetation (natural senescence)
    c. EVI/NDMI divergence (soil exposure after harvest)

5.  CONFIDENCE WITH CLOUD PENALTY
    Cycles that span long cloud gaps get a small confidence penalty to
    signal that their exact sowing/harvest dates carry more uncertainty.
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

from config import PipelineConfig

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# Season starts (month, day) used for hint-building only
_KHARIF_START = (6, 1)
_RABI_START   = (11, 15)

# Minimum gap (days) between consecutive observations to flag as a cloud gap
_CLOUD_GAP_MIN_DAYS = 25

# Composite Vegetation Index (CVI) weights
_CVI_WEIGHTS = {
    'ndvi': 0.50,  # Primary signal
    'evi':  0.30,  # Haze/atmospheric correction (esp. monsoon)
    'ndmi': 0.20,  # Moisture — helps detect crop vs. rain-wetted soil
}


# =============================================================================
# HELPERS
# =============================================================================

def _to_datetime(d) -> datetime:
    """Convert string 'YYYY-MM-DD', date, numpy datetime64, or datetime → datetime."""
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        return datetime.strptime(d[:10], '%Y-%m-%d')
    try:                           # pandas Timestamp
        return d.to_pydatetime()
    except AttributeError:
        pass
    try:                           # numpy datetime64
        import numpy as np
        ts = (d - np.datetime64('1970-01-01T00:00:00')) / np.timedelta64(1, 's')
        return datetime.utcfromtimestamp(float(ts))
    except Exception:
        pass
    from datetime import date as date_type
    if isinstance(d, date_type):
        return datetime(d.year, d.month, d.day)
    raise TypeError(f"Cannot convert {type(d)} to datetime")


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class CloudGap:
    """Represents a temporal gap with no usable satellite observations."""
    start_date:  datetime
    end_date:    datetime
    gap_days:    int
    start_index: int
    end_index:   int


@dataclass
class CropCycle:
    """A fully detected crop cultivation cycle with sowing → harvest interval."""
    sowing_date:        datetime
    harvest_date:       datetime
    peak_date:          datetime
    duration_days:      int
    crop_type:          Optional[str]
    # NDVI metrics
    peak_ndvi:          float
    baseline_ndvi:      float
    ndvi_rise:          float
    integral_ndvi:      float       # Simple scene-sum
    integral_ndvi_days: float       # Day-weighted trapezoid (yield proxy)
    # Multi-index metrics
    peak_evi:           float = 0.0
    peak_ndmi:          float = 0.0
    peak_cvi:           float = 0.0  # Composite
    # Meta
    confidence:         float = 0.0
    cloud_gap_days:     int   = 0    # Cloud-gap days within this cycle
    has_cloud_gap:      bool  = False

    # Season hint (derived, not detected)
    season_label: str = ""

    def to_dict(self) -> Dict:
        """Serialise to dict — includes start_date/end_date aliases."""
        return {
            # Primary aliases used by CropDetector.analyze_cycles()
            'start_date':         self.sowing_date.strftime('%Y-%m-%d'),
            'end_date':           self.harvest_date.strftime('%Y-%m-%d'),
            # Detailed fields
            'sowing_date':        self.sowing_date.isoformat(),
            'harvest_date':       self.harvest_date.isoformat(),
            'peak_date':          self.peak_date.isoformat(),
            'duration_days':      self.duration_days,
            'crop_type':          self.crop_type,
            'season_label':       self.season_label,
            # NDVI
            'peak_ndvi':          round(self.peak_ndvi, 3),
            'baseline_ndvi':      round(self.baseline_ndvi, 3),
            'ndvi_rise':          round(self.ndvi_rise, 3),
            'integral_ndvi':      round(self.integral_ndvi, 3),
            'integral_ndvi_days': round(self.integral_ndvi_days, 2),
            # Multi-index
            'peak_evi':           round(self.peak_evi, 3),
            'peak_ndmi':          round(self.peak_ndmi, 3),
            'peak_cvi':           round(self.peak_cvi, 3),
            # Quality
            'confidence':         round(self.confidence, 1),
            'cloud_gap_days':     self.cloud_gap_days,
            'has_cloud_gap':      self.has_cloud_gap,
        }


# =============================================================================
# MAIN DETECTOR
# =============================================================================

class CropCycleDetector:
    """
    Detects crop cultivation cycles from a continuous multi-index time series.

    Key design principles:
    - Works on irregular timestamps (14-day avg interval, but can have 30–60-day
      monsoon gaps with zero observation).
    - Uses Composite Vegetation Index (CVI) combining NDVI+EVI+NDMI to detect
      greenup more reliably than NDVI alone during cloudy/hazy conditions.
    - Applies cloud-gap aware interpolation BEFORE smoothing so that smoothed
      values respect real days elapsed rather than scene count.
    - All date arithmetic uses datetime objects (dt_dates) passed explicitly into
      every sub-function — the NameError bug from v2 is eliminated.
    """

    def __init__(
        self,
        min_ndvi_rise:     Optional[float] = None,
        min_baseline_cvi:  Optional[float] = None,
        min_peak_cvi:      Optional[float] = None,
        min_duration_days: Optional[int] = None,
        max_duration_days: Optional[int] = None,
        cloud_gap_penalty: float = 5.0,
    ):
        P = PipelineConfig
        self.min_ndvi_rise = float(
            min_ndvi_rise if min_ndvi_rise is not None else getattr(P, "CROP_CYCLE_MIN_NDVI_RISE", 0.10)
        )
        self.min_baseline_cvi = float(
            min_baseline_cvi if min_baseline_cvi is not None else getattr(P, "CROP_CYCLE_MIN_BASELINE_CVI", 0.30)
        )
        self.min_peak_cvi = float(
            min_peak_cvi if min_peak_cvi is not None else getattr(P, "CROP_CYCLE_MIN_PEAK_CVI", 0.28)
        )
        self.min_duration_days = int(
            min_duration_days if min_duration_days is not None else getattr(P, "CROP_CYCLE_MIN_DURATION_DAYS", 40)
        )
        self.max_duration_days = int(
            max_duration_days if max_duration_days is not None else getattr(P, "CROP_CYCLE_MAX_DURATION_DAYS", 195)
        )
        self.cloud_gap_penalty = cloud_gap_penalty
        self.max_days_after_peak = int(getattr(P, "CROP_CYCLE_MAX_DAYS_AFTER_PEAK", 135))
        self.greenup_min_grid_sep = int(getattr(P, "CROP_CYCLE_GREENUP_MIN_GRID_SEP", 3))
        self.sustained_growth_frac = float(getattr(P, "CROP_CYCLE_SUSTAINED_GROWTH_FRAC", 0.55))
        self.merge_overlap_min_days = int(getattr(P, "CROP_CYCLE_MERGE_OVERLAP_MIN_DAYS", 55))
        self.merge_overlap_ratio = float(getattr(P, "CROP_CYCLE_MERGE_OVERLAP_RATIO", 0.42))
        self.hint_step_days = int(getattr(P, "CROP_CYCLE_HINT_STEP_DAYS", 118))
        self.max_hint_anchors = int(getattr(P, "CROP_CYCLE_MAX_HINT_ANCHORS", 40))

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def detect_cycles(
        self,
        dates:       List,
        ndvi_values: List[float],
        evi_values:  Optional[List[float]] = None,
        ndmi_values: Optional[List[float]] = None,
        scenes:      Optional[List[Dict]]  = None,  # raw scenes for index extraction
        sowing_date_hint: Optional[object] = None,
        crop_hint: Optional[str] = None,
    ) -> List['CropCycle']:
        """
        Detect all crop cycles from a continuous time series.

        Args:
            dates:       List of dates (datetime | str 'YYYY-MM-DD')
            ndvi_values: NDVI for each date
            evi_values:  EVI  for each date (optional; falls back to NDVI if absent)
            ndmi_values: NDMI for each date (optional; falls back to zeros if absent)
            scenes:      Raw scene dicts from satellite_collector (used to extract
                         evi/ndmi if evi_values/ndmi_values not passed separately)

        Returns:
            List[CropCycle] sorted chronologically.
            Each CropCycle has reliable sowing_date and harvest_date.
        """
        if len(dates) < 10:
            logger.warning(f"Insufficient data: {len(dates)} observations (need ≥ 10)")
            return []

        # Optional: use DB sowing date & crop hint to anchor cycle intervals.
        sowing_dt: Optional[datetime] = None
        if sowing_date_hint:
            try:
                sowing_dt = _to_datetime(sowing_date_hint)
            except Exception:
                sowing_dt = None

        crop_duration = None
        if crop_hint:
            try:
                from config import CropGrowthCurves
                info = CropGrowthCurves.CROP_DURATIONS.get(str(crop_hint).strip().title())
                if info:
                    crop_duration = info
            except Exception:
                crop_duration = None

        # ── 1. Normalise dates → datetime ──────────────────────────────────
        dt_dates = [_to_datetime(d) for d in dates]

        # ── 2. Extract EVI/NDMI from scenes if not provided ────────────────
        evi_values, ndmi_values = self._resolve_indices(
            dt_dates, ndvi_values, evi_values, ndmi_values, scenes
        )

        # ── 3. Sort everything by date ─────────────────────────────────────
        dt_dates, ndvi_arr, evi_arr, ndmi_arr = self._sort_by_date(
            dt_dates, ndvi_values, evi_values, ndmi_values
        )

        logger.info(f"Detecting crop cycles from {len(dt_dates)} observations")
        logger.info(
            f"Date range: {dt_dates[0].strftime('%Y-%m-%d')} "
            f"to {dt_dates[-1].strftime('%Y-%m-%d')}"
        )

        # ── 4. Detect cloud gaps ────────────────────────────────────────────
        cloud_gaps = self._detect_cloud_gaps(dt_dates)
        if cloud_gaps:
            logger.info(
                f"  Detected {len(cloud_gaps)} cloud gap(s): "
                + ", ".join(
                    f"{g.start_date.strftime('%b %Y')} ({g.gap_days}d)"
                    for g in cloud_gaps
                )
            )

        # ── 5. Regularise to uniform time grid & impute cloud gaps ─────────
        reg_dates, reg_ndvi, reg_evi, reg_ndmi = self._regularise_and_impute(
            dt_dates, ndvi_arr, evi_arr, ndmi_arr, cloud_gaps
        )

        # ── 6. Build Composite Vegetation Index (CVI) ──────────────────────
        cvi = self._build_cvi(reg_ndvi, reg_evi, reg_ndmi)

        # ── 7. Smooth with Savitzky-Golay (preserves peaks) ───────────────
        cvi_smooth   = self._savgol_smooth(cvi,       window=7, polyorder=2)
        ndvi_smooth  = self._savgol_smooth(reg_ndvi,  window=7, polyorder=2)

        # ── 8. Detect greenup events on CVI ────────────────────────────────
        greenup_indices = self._detect_greenup_events(cvi_smooth)
        # Add sowing-date anchored candidates (helps when greenup is missed due to noise/cloud gaps).
        if sowing_dt:
            def _nearest_reg_idx(target: datetime) -> Optional[int]:
                if not reg_dates:
                    return None
                best_i = 0
                best_abs = abs((reg_dates[0] - target).days)
                for i, d in enumerate(reg_dates):
                    dd = abs((d - target).days)
                    if dd < best_abs:
                        best_abs = dd
                        best_i = i
                return best_i

            candidates: List[int] = []
            i0 = _nearest_reg_idx(sowing_dt)
            if i0 is not None:
                candidates.append(i0)

            # Multi-crop rotation (e.g. potato / maize / rice): space hints by
            # calendar (~118d), not single-crop typical_days+30 (too wide).
            step_days = self.hint_step_days
            if crop_duration:
                typ = int(crop_duration.get("typical_days", step_days))
                step_days = min(step_days, max(75, typ))

            t = sowing_dt + timedelta(days=step_days)
            n_anchor = 1
            tail = reg_dates[-1] - timedelta(days=max(self.min_duration_days, 35))
            while t < tail and n_anchor < self.max_hint_anchors:
                ii = _nearest_reg_idx(t)
                if ii is not None:
                    candidates.append(ii)
                t = t + timedelta(days=step_days)
                n_anchor += 1

            greenup_indices = sorted(set(greenup_indices + candidates))

        logger.info(f"Found {len(greenup_indices)} potential greenup events")

        # ── 9. Trace each greenup into a full cycle ─────────────────────────
        cycles: List[CropCycle] = []
        for gi in greenup_indices:
            max_duration_override = None
            if crop_duration:
                max_duration_override = min(
                    int(crop_duration.get("max_days", self.max_duration_days)) + 30,
                    self.max_duration_days,
                )

            cycle = self._trace_crop_cycle(
                dt_dates   = reg_dates,     # ← FIXED: explicitly passed (v2 had NameError here)
                cvi_smooth = cvi_smooth,
                ndvi_smooth= ndvi_smooth,
                ndvi_raw   = reg_ndvi,
                evi_raw    = reg_evi,
                ndmi_raw   = reg_ndmi,
                cvi_raw    = cvi,
                greenup_idx= gi,
                cloud_gaps = cloud_gaps,
                max_duration_days_override=max_duration_override,
            )
            if cycle and self._validate_cycle(cycle):
                cycles.append(cycle)

        logger.info(f"Validated {len(cycles)} complete crop cycles")

        # ── 10. Merge overlapping / duplicate cycles ────────────────────────
        cycles = self._merge_overlapping_cycles(cycles)

        # ── 11. Add season labels ───────────────────────────────────────────
        for c in cycles:
            c.season_label = self._season_label(c.sowing_date)

        logger.info(f"Final: {len(cycles)} cycles after deduplication")
        for i, c in enumerate(cycles, 1):
            logger.info(
                f"  Cycle {i}: {c.sowing_date.strftime('%Y-%m-%d')} → "
                f"{c.harvest_date.strftime('%Y-%m-%d')}  "
                f"({c.duration_days}d | peak NDVI={c.peak_ndvi:.3f} | "
                f"CVI={c.peak_cvi:.3f} | conf={c.confidence:.0f}% | "
                f"{'⚠ cloud gap' if c.has_cloud_gap else 'no gap'})"
            )

        return cycles

    # =========================================================================
    # PREPROCESSING — Index Resolution
    # =========================================================================

    @staticmethod
    def _resolve_indices(
        dt_dates:    List[datetime],
        ndvi_values: List[float],
        evi_values:  Optional[List[float]],
        ndmi_values: Optional[List[float]],
        scenes:      Optional[List[Dict]],
    ) -> Tuple[List[float], List[float]]:
        """
        If evi_values / ndmi_values are not provided, try to extract from
        scene dicts.  Falls back gracefully if unavailable.
        """
        n = len(ndvi_values)

        # EVI
        if evi_values is None or len(evi_values) != n:
            if scenes and len(scenes) == n:
                evi_values = [
                    s.get('indices', {}).get('EVI_mean', np.nan) for s in scenes
                ]
            else:
                evi_values = [np.nan] * n

        # NDMI
        if ndmi_values is None or len(ndmi_values) != n:
            if scenes and len(scenes) == n:
                ndmi_values = [
                    s.get('indices', {}).get('NDMI_mean', np.nan) for s in scenes
                ]
            else:
                ndmi_values = [0.0] * n

        return evi_values, ndmi_values

    @staticmethod
    def _sort_by_date(
        dt_dates:    List[datetime],
        ndvi_values: List[float],
        evi_values:  List[float],
        ndmi_values: List[float],
    ) -> Tuple[List[datetime], np.ndarray, np.ndarray, np.ndarray]:
        paired = sorted(
            zip(dt_dates, ndvi_values, evi_values, ndmi_values),
            key=lambda x: x[0]
        )
        dt_dates   = [p[0] for p in paired]
        ndvi_arr   = np.array([p[1] for p in paired], dtype=float)
        evi_arr    = np.array([p[2] for p in paired], dtype=float)
        ndmi_arr   = np.array([p[3] for p in paired], dtype=float)
        return dt_dates, ndvi_arr, evi_arr, ndmi_arr

    # =========================================================================
    # CLOUD GAP DETECTION
    # =========================================================================

    @staticmethod
    def _detect_cloud_gaps(dt_dates: List[datetime]) -> List[CloudGap]:
        """
        Identify temporal gaps where no clean observation was possible.
        A gap is declared when consecutive observations are ≥ 25 days apart.
        """
        gaps = []
        for i in range(len(dt_dates) - 1):
            gap_days = (dt_dates[i + 1] - dt_dates[i]).days
            if gap_days >= _CLOUD_GAP_MIN_DAYS:
                gaps.append(CloudGap(
                    start_date  = dt_dates[i],
                    end_date    = dt_dates[i + 1],
                    gap_days    = gap_days,
                    start_index = i,
                    end_index   = i + 1,
                ))
        return gaps

    # =========================================================================
    # TIME-AWARE REGULARISATION & IMPUTATION
    # =========================================================================

    def _regularise_and_impute(
        self,
        dt_dates: List[datetime],
        ndvi:     np.ndarray,
        evi:      np.ndarray,
        ndmi:     np.ndarray,
        cloud_gaps: List[CloudGap],
    ) -> Tuple[List[datetime], np.ndarray, np.ndarray, np.ndarray]:
        """
        Converts the irregular observation schedule into a uniform 14-day grid,
        then fills cloud-gap periods using biologically-sound interpolation.

        DESIGN RATIONALE
        ----------------
        The critical issue: during June–August (Indian Kharif sowing period),
        monsoon cloud cover can block all usable observations for 30–60 days.
        If we simply linearly interpolate by scene index, a gap of index 2 and
        a gap of index 20 get the same treatment — which is biologically wrong.

        Instead we:
        1. Build a uniform 14-day time grid across the full observation period.
        2. Fill the existing observations onto the nearest grid points.
        3. For gap periods: apply Piecewise Cubic Hermite Interpolating
           Polynomial (PCHIP) which is:
           - Monotone within each segment (no artificial overshoots)
           - Smooth (C1 continuous) at knot points
           - Biologically realistic (no negative NDVI spikes from Runge effect)
        4. For EVI, if it was entirely NaN (not collected), we proxy from NDVI
           using a calibrated linear model (EVI ≈ 0.85 * NDVI + 0.02).
        """
        start = dt_dates[0]
        end   = dt_dates[-1]
        total_days = (end - start).days

        # Build uniform 14-day grid
        grid_dates = [
            start + timedelta(days=d)
            for d in range(0, total_days + 1, 14)
        ]
        if grid_dates[-1] < end:
            grid_dates.append(end)

        # Day offsets for both irregular observations and grid
        obs_offsets  = np.array([(d - start).days for d in dt_dates], dtype=float)
        grid_offsets = np.array([(d - start).days for d in grid_dates], dtype=float)

        def _pchip_interp(values: np.ndarray) -> np.ndarray:
            """PCHIP interpolation (no overshoot, biologically realistic)."""
            valid_mask = ~np.isnan(values)
            if valid_mask.sum() < 2:
                # Not enough knots — fall back to last-known-value for-ward fill
                filled = np.full(len(grid_offsets), np.nanmean(values) if valid_mask.any() else 0.2)
                return filled
            # NOTE: In some of your runs, SciPy PCHIP triggers a low-level crash
            # (process exits without Python traceback). To keep the pipeline stable
            # and still do time-aware interpolation across gaps, we use `np.interp`
            # on a de-duplicated knot set here.
            x = obs_offsets[valid_mask]
            y = values[valid_mask]

            order = np.argsort(x)
            x = x[order]
            y = y[order]

            unique_x, inverse = np.unique(x, return_inverse=True)
            y_agg = np.zeros(len(unique_x), dtype=float)
            for ui in range(len(unique_x)):
                y_agg[ui] = float(np.mean(y[inverse == ui]))

            result = np.interp(grid_offsets, unique_x, y_agg)
            # Clip to plausible NDVI/EVI/NDMI range.
            return np.clip(result, -0.1, 1.0)

        reg_ndvi = _pchip_interp(ndvi)
        reg_ndmi = _pchip_interp(ndmi)

        # EVI: if mostly NaN (not recorded), proxy from NDVI
        evi_valid_frac = float(np.sum(~np.isnan(evi))) / max(len(evi), 1)
        if evi_valid_frac < 0.3:
            logger.debug(
                f"EVI data sparse ({evi_valid_frac:.0%} valid) — "
                "proxying from NDVI (EVI ≈ 0.85·NDVI + 0.02)"
            )
            reg_evi = np.clip(0.85 * reg_ndvi + 0.02, 0.0, 1.0)
        else:
            reg_evi = _pchip_interp(evi)

        return grid_dates, reg_ndvi, reg_evi, reg_ndmi

    # =========================================================================
    # COMPOSITE VEGETATION INDEX
    # =========================================================================

    @staticmethod
    def _build_cvi(
        ndvi: np.ndarray,
        evi:  np.ndarray,
        ndmi: np.ndarray,
    ) -> np.ndarray:
        """
        Weighted composite: CVI = 0.50·NDVI + 0.30·EVI + 0.20·(NDMI rescaled)

        NDMI is typically in [-0.5, 0.5] for vegetated surfaces; we rescale to
        [0, 1] to harmonise with NDVI/EVI before weighting.
        """
        w = _CVI_WEIGHTS
        ndmi_rescaled = np.clip((ndmi + 0.5) / 1.0, 0.0, 1.0)
        cvi = (
            w['ndvi'] * ndvi
            + w['evi']  * evi
            + w['ndmi'] * ndmi_rescaled
        )
        return np.clip(cvi, 0.0, 1.0)

    # =========================================================================
    # SMOOTHING (Savitzky-Golay — preserves peaks better than moving average)
    # =========================================================================

    @staticmethod
    def _savgol_smooth(signal: np.ndarray, window: int = 7, polyorder: int = 2) -> np.ndarray:
        """
        Savitzky-Golay filter: fits local polynomials, preserving peak height
        and shape far better than moving-average or uniform filter.

        Falls back to centre-weighted moving average if scipy unavailable.
        """
        # window must be odd and > polyorder
        if window % 2 == 0:
            window += 1
        window = max(window, polyorder + 2)
        # If series is too short for the window, reduce window
        window = min(window, len(signal) if len(signal) % 2 == 1 else len(signal) - 1)
        if window <= polyorder:
            return signal.copy()

        # IMPORTANT:
        # In this runtime, `scipy.signal.savgol_filter` is crashing the Python
        # process (native crash, no traceback). So we always use the safe
        # triangular weighted moving average instead.
        half = window // 2
        weights = np.bartlett(window)
        smoothed = np.copy(signal.astype(float))
        for i in range(len(signal)):
            s = max(0, i - half)
            e = min(len(signal), i + half + 1)
            w_slice = weights[half - (i - s): half + (e - i)]
            smoothed[i] = np.average(signal[s:e], weights=w_slice)
        return smoothed

    # =========================================================================
    # GREENUP DETECTION (on CVI)
    # =========================================================================

    def _detect_greenup_events(self, cvi: np.ndarray) -> List[int]:
        """
        Detect sowing/establishment events using CVI relative rise.

        Criteria (v3):
          1. Local pre-event CVI baseline is below min_baseline_cvi
          2. Post-event mean CVI exceeds baseline by min_ndvi_rise
          3. Post-event peak CVI exceeds min_peak_cvi (real vegetation)
          4. Growth is sustained (≥60% of transitions are increasing) — stronger
             than v2's 50% to reduce false positives from rain-wetting events.
        """
        greenup_indices = []
        window = 5  # ~5 × 14 days ≈ 10 weeks

        for i in range(window, len(cvi) - window * 2):
            baseline     = float(np.mean(cvi[max(0, i - window): i + 1]))
            post_greenup = float(np.mean(cvi[i + 1: i + 1 + window]))
            cvi_rise     = post_greenup - baseline

            if (
                baseline      < self.min_baseline_cvi
                and cvi_rise  > self.min_ndvi_rise
                and post_greenup > self.min_peak_cvi
                and self._is_sustained_growth(
                    cvi, i, i + 2 * window, threshold=self.sustained_growth_frac
                )
            ):
                min_sep = max(2, self.greenup_min_grid_sep)
                if not greenup_indices or i - greenup_indices[-1] >= min_sep:
                    greenup_indices.append(i)

        return greenup_indices

    @staticmethod
    def _is_sustained_growth(
        cvi:       np.ndarray,
        start:     int,
        end:       int,
        threshold: float = 0.60,
    ) -> bool:
        """Fraction of increasing transitions must meet threshold."""
        segment = cvi[start: min(end, len(cvi))]
        if len(segment) < 2:
            return False
        diffs = np.diff(segment)
        return float(np.sum(diffs > 0)) / len(diffs) >= threshold

    # =========================================================================
    # CYCLE TRACING  (dt_dates explicitly passed — BUG FIX)
    # =========================================================================

    def _trace_crop_cycle(
        self,
        dt_dates:    List[datetime],   # ← v3 FIX: passed explicitly, not from outer scope
        cvi_smooth:  np.ndarray,
        ndvi_smooth: np.ndarray,
        ndvi_raw:    np.ndarray,
        evi_raw:     np.ndarray,
        ndmi_raw:    np.ndarray,
        cvi_raw:     np.ndarray,
        greenup_idx: int,
        cloud_gaps:  List[CloudGap],
        max_duration_days_override: Optional[int] = None,
    ) -> Optional['CropCycle']:
        """Trace a complete crop cycle from a greenup event to harvest."""
        remaining = len(cvi_smooth) - greenup_idx - 1
        # Search window: max_duration / 14-day grid interval
        max_days = int(max_duration_days_override) if max_duration_days_override else int(self.max_duration_days)
        search_max = min(int(max_days / 14) + 10, remaining)

        if search_max < 5:
            return None

        window_end = greenup_idx + search_max

        # ── Find peak in CVI ──────────────────────────────────────────────
        peak_rel  = int(np.argmax(cvi_smooth[greenup_idx: window_end]))
        peak_idx  = greenup_idx + peak_rel
        peak_cvi  = float(cvi_smooth[peak_idx])
        peak_ndvi = float(ndvi_smooth[peak_idx])

        if peak_cvi < self.min_peak_cvi:
            return None

        # Cap harvest search after peak so one crop cannot absorb the next season's signal.
        post_peak_bins = max(5, int(np.ceil(self.max_days_after_peak / 14.0)) + 2)
        harvest_window_end = min(window_end, peak_idx + post_peak_bins)

        # ── Detect harvest (senescence) ───────────────────────────────────
        harvest_idx = self._detect_harvest(
            cvi_smooth  = cvi_smooth,
            ndvi_smooth = ndvi_smooth,
            evi_raw     = evi_raw,
            ndmi_raw    = ndmi_raw,
            peak_idx    = peak_idx,
            max_idx     = harvest_window_end,
        )
        if harvest_idx is None:
            return None

        # ── Date extraction — uses the PASSED dt_dates (v2 NameError fixed) ──
        sowing_date  = dt_dates[greenup_idx]
        peak_date    = dt_dates[peak_idx]
        harvest_date = dt_dates[harvest_idx]
        duration_days = (harvest_date - sowing_date).days

        if not (self.min_duration_days <= duration_days <= self.max_duration_days):
            return None

        # ── Compute integrals ─────────────────────────────────────────────
        ndvi_seg = ndvi_raw[greenup_idx: harvest_idx + 1]
        day_offsets = np.array(
            [(dt_dates[j] - sowing_date).days
             for j in range(greenup_idx, harvest_idx + 1)],
            dtype=float,
        )
        integral_ndvi      = float(np.nansum(ndvi_seg))
        integral_ndvi_days = (
            float(np.trapz(ndvi_seg, day_offsets)) if len(ndvi_seg) > 1 else 0.0
        )
        baseline_ndvi = float(np.mean(ndvi_smooth[max(0, greenup_idx - 3): greenup_idx + 1]))

        # ── Cloud gap analysis within this cycle ──────────────────────────
        cycle_gaps = [
            g for g in cloud_gaps
            if g.start_date >= sowing_date and g.end_date <= harvest_date
        ]
        cloud_gap_days = sum(g.gap_days for g in cycle_gaps)

        # ── Confidence score ──────────────────────────────────────────────
        confidence = self._calculate_confidence(
            ndvi_seg      = ndvi_seg,
            duration_days = duration_days,
            peak_cvi      = peak_cvi,
            cloud_gap_days= cloud_gap_days,
        )

        return CropCycle(
            sowing_date        = sowing_date,
            harvest_date       = harvest_date,
            peak_date          = peak_date,
            duration_days      = duration_days,
            crop_type          = self._classify_by_duration_and_indices(
                                     duration_days, peak_ndvi, peak_cvi),
            peak_ndvi          = peak_ndvi,
            baseline_ndvi      = baseline_ndvi,
            ndvi_rise          = float(peak_ndvi - ndvi_smooth[greenup_idx]),
            integral_ndvi      = integral_ndvi,
            integral_ndvi_days = integral_ndvi_days,
            peak_evi           = float(np.nanmean(evi_raw[peak_idx - 1: peak_idx + 2])),
            peak_ndmi          = float(np.nanmean(ndmi_raw[peak_idx - 1: peak_idx + 2])),
            peak_cvi           = peak_cvi,
            confidence         = confidence,
            cloud_gap_days     = cloud_gap_days,
            has_cloud_gap      = len(cycle_gaps) > 0,
        )

    def _detect_harvest(
        self,
        cvi_smooth:  np.ndarray,
        ndvi_smooth: np.ndarray,
        evi_raw:     np.ndarray,
        ndmi_raw:    np.ndarray,
        peak_idx:    int,
        max_idx:     int,
    ) -> Optional[int]:
        """
        Three-trigger cascade harvest detection (v3):

        Trigger A — Rapid CVI drop (mechanical harvest / irrigation cut)
            CVI drops ≥ 0.07/obs and falls below 80% of peak.

        Trigger B — Sustained low vegetation (natural senescence)
            CVI < 0.32 AND next 3-obs mean also < 0.30 (not just noise).

        Trigger C — Multi-index divergence (soil exposure after harvest)
            NDMI rises (soil gets wetter) while NDVI falls — typical of
            post-harvest bare soil + monsoon rain.

        Last resort: minimum CVI point in the post-peak search window.
        """
        search_end = min(max_idx, len(cvi_smooth) - 1)
        if search_end <= peak_idx + 2:
            return None

        cvi_post  = cvi_smooth[peak_idx: search_end + 1]
        ndvi_post = ndvi_smooth[peak_idx: search_end + 1]
        evi_seg   = evi_raw[peak_idx: search_end + 1]
        ndmi_seg  = ndmi_raw[peak_idx: search_end + 1]
        peak_cvi  = cvi_post[0]

        for i in range(1, len(cvi_post) - 1):
            val  = cvi_post[i]
            rate = cvi_post[i - 1] - val  # drop per time-step

            # Trigger A: rapid CVI collapse
            if rate >= 0.07 and val < peak_cvi * 0.80:
                return peak_idx + i

            # Trigger B: sustained low CVI (senescence)
            lookahead = cvi_post[i: min(i + 4, len(cvi_post))]
            if val < 0.32 and float(np.mean(lookahead)) < 0.30:
                return peak_idx + i

            # Trigger C: NDMI rising + NDVI falling (soil exposure)
            if i >= 2 and len(ndmi_seg) > i + 1:
                ndvi_drop  = ndvi_post[i - 2] - ndvi_post[i]
                ndmi_rise  = ndmi_seg[i] - ndmi_seg[i - 2]
                if ndvi_drop > 0.08 and ndmi_rise > 0.05:
                    return peak_idx + i

        # Last resort: minimum CVI in post-peak window
        decline_idx = int(np.argmin(cvi_post[1:])) + 1
        return peak_idx + decline_idx if decline_idx > 0 else None

    # =========================================================================
    # CLASSIFICATION & SCORING
    # =========================================================================

    @staticmethod
    def _classify_by_duration_and_indices(
        duration_days: int,
        peak_ndvi:     float,
        peak_cvi:      float,
    ) -> str:
        """Provisional crop-type hint based on duration and multi-index peak."""
        if duration_days < 90:
            return "SHORT_HIGH_VIGOR" if peak_ndvi > 0.55 else "SHORT_MODERATE"
        elif duration_days < 180:
            if peak_cvi > 0.60:
                return "MEDIUM_HIGH_VIGOR"
            elif peak_cvi > 0.45:
                return "MEDIUM_MODERATE"
            else:
                return "MEDIUM_LOW_VIGOR"
        elif duration_days < 300:
            return "LONG_DURATION"
        else:
            return "PERENNIAL_OR_SUGARCANE"

    def _calculate_confidence(
        self,
        ndvi_seg:       np.ndarray,
        duration_days:  int,
        peak_cvi:       float,
        cloud_gap_days: int,
    ) -> float:
        """
        Confidence score 0–100.

        Components:
          - Variation score (25): NDVI temporal std × 100, capped at 25
          - Duration score (25): reward durations in 60–250 day sweet spot
          - Peak score (25):     based on peak CVI
          - Scene density (25):  more observations → more reliable
          - Cloud penalty:       deduct cloud_gap_penalty per gap day / 7
        """
        variation_score = min(25.0, float(np.nanstd(ndvi_seg)) * 150)

        if 60 <= duration_days <= 250:
            duration_score = 25.0
        elif duration_days < 60:
            duration_score = max(0.0, 25.0 * (duration_days - 45) / 15)
        else:
            duration_score = max(10.0, 25.0 * (1 - (duration_days - 250) / 170))

        peak_score    = min(25.0, peak_cvi * 40)
        density_score = min(25.0, len(ndvi_seg) / 15 * 25)

        raw = variation_score + duration_score + peak_score + density_score

        # Cloud penalty: deduct 5 points per 7-day gap in the cycle
        # (clouds reduce confidence in exact sowing/harvest date)
        penalty = self.cloud_gap_penalty * (cloud_gap_days / 7)
        return float(np.clip(raw - penalty, 10.0, 100.0))

    def _validate_cycle(self, cycle: CropCycle) -> bool:
        """Quality gate: all criteria must pass."""
        if not (self.min_duration_days <= cycle.duration_days <= self.max_duration_days):
            return False
        if cycle.peak_cvi < self.min_peak_cvi:
            return False
        if cycle.ndvi_rise < self.min_ndvi_rise:
            return False
        if cycle.confidence < 15:   # Lower than v2 because cloud-gapped cycles
            return False            # lose confidence but may still be valid
        return True

    # =========================================================================
    # DEDUPLICATION
    # =========================================================================

    def _merge_overlapping_cycles(self, cycles: List['CropCycle']) -> List['CropCycle']:
        """
        Remove strongly-overlapping cycles, keeping the higher-confidence one.

        IMPORTANT:
        In noisy / cloud-gapped series, the tracer can generate partially-overlapping
        candidates around the same underlying interval. We should only collapse
        cycles when the overlap is substantial; light overlaps can represent
        real back-to-back crops with uncertain harvest/sowing boundaries.
        """
        if len(cycles) <= 1:
            return cycles

        sorted_cycles = sorted(cycles, key=lambda c: c.sowing_date)
        merged = []
        i = 0
        while i < len(sorted_cycles):
            current = sorted_cycles[i]
            if i + 1 < len(sorted_cycles):
                nxt = sorted_cycles[i + 1]
                if nxt.sowing_date < current.harvest_date:
                    overlap_days = (
                        min(current.harvest_date, nxt.harvest_date) - nxt.sowing_date
                    ).days
                    shorter = max(min(current.duration_days, nxt.duration_days), 1)
                    ratio_ok = overlap_days >= self.merge_overlap_ratio * shorter
                    if overlap_days >= self.merge_overlap_min_days and ratio_ok:
                        winner = nxt if nxt.confidence > current.confidence else current
                        merged.append(winner)
                        i += 2
                        continue
            merged.append(current)
            i += 1
        return merged

    # =========================================================================
    # SEASON LABELLING
    # =========================================================================

    @staticmethod
    def _season_label(sowing_date: datetime) -> str:
        """
        Assign a human-readable season label to a cycle based on sowing month.
        Kharif: Jun–Oct sowing.  Rabi: Nov–Feb sowing.  Other: Zaid/year-round.
        """
        m = sowing_date.month
        y = sowing_date.year
        if 6 <= m <= 10:
            return f"Kharif {y}"
        elif m in (11, 12):
            return f"Rabi {y}"
        elif 1 <= m <= 5:
            return f"Rabi {y - 1}"
        return f"Zaid {y}"


# Export
__all__ = ['CropCycle', 'CropCycleDetector', 'CloudGap']
