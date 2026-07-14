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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import logging

from config import PipelineConfig

logger = logging.getLogger(__name__)


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
    # Season
    season_label:       str   = ""
    season_type:        str   = ""
    activity_number:    int   = 0

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
            'activity_number':    self.activity_number,
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
            sowing_date_hint, crop_hint, agro_profile: accepted for pipeline API
                compatibility but not yet applied to detection (logged if set).

        Returns:
            List[CropCycle] sorted chronologically.
        """
        if sowing_date_hint or crop_hint or agro_profile:
            logger.info(
                "Cycle hints present but not yet applied "
                "(sowing_date_hint=%r crop_hint=%r agro_profile=%s)",
                sowing_date_hint,
                crop_hint,
                "set" if agro_profile else None,
            )
        _ = kwargs
        self.last_detection_meta = {
            'hints_received': bool(sowing_date_hint or crop_hint or agro_profile),
            'hints_applied': False,
        }

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

        # 3. Sort by date
        paired   = sorted(zip(dt_dates, ndvi_values, evi_values, ndmi_values), key=lambda x: x[0])
        dt_dates = [p[0] for p in paired]
        ndvi_arr = np.array([p[1] for p in paired], dtype=float)
        evi_arr  = np.array([p[2] for p in paired], dtype=float)
        ndmi_arr = np.array([p[3] for p in paired], dtype=float)

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
        harvest_ndvi = float(getattr(P, "CROP_CYCLE_HARVEST_LOW_NDVI", 0.36))

        reg_dates, reg_ndvi, reg_evi, reg_ndmi = self._regularise_and_impute(
            dt_dates, ndvi_arr, evi_arr, ndmi_arr, cloud_gaps, step
        )

        cvi         = self._build_cvi(reg_ndvi, reg_evi, reg_ndmi)
        cvi_smooth  = self._smooth(cvi,      window=7)
        ndvi_smooth = self._smooth(reg_ndvi, window=7)

        def run_pass(p_floor: float, r_floor: float) -> List[CropCycle]:
            return self._detect_cycles(
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
            )

        cycles = run_pass(peak_cvi, min_rise)
        years_span = max(0.25, (dt_dates[-1] - dt_dates[0]).days / 365.25)
        expected = max(1, int(np.ceil(years_span * float(getattr(P, "CROP_CYCLE_EXPECTED_CYCLES_PER_YEAR", 1.0)))))

        if (
            getattr(P, "CROP_CYCLE_ADAPTIVE_SECOND_PASS", True)
            and len(cycles) < max(1, int(np.ceil(years_span * 0.65)))
        ):
            c2 = run_pass(max(0.22, peak_cvi - 0.05), max(0.07, min_rise - 0.02))
            if len(c2) > len(cycles):
                cycles = c2
                self.last_detection_meta["adaptive_pass"] = "second"

        if (
            getattr(P, "CROP_CYCLE_DENSITY_PASS", True)
            and len(cycles) < expected
            and len(cycles) < max(2, expected)
        ):
            scale = float(getattr(P, "CROP_CYCLE_DENSITY_PEAK_PROMINENCE_SCALE", 0.58))
            c3 = run_pass(max(0.20, peak_cvi * scale), max(0.065, min_rise - 0.035))
            if len(c3) > len(cycles):
                cycles = c3
                self.last_detection_meta["density_pass"] = True

        self.last_detection_meta.update({
            "observations": len(dt_dates),
            "cycles_found": len(cycles),
            "years_span": round(years_span, 2),
            "expected_cycles_ceiling": expected,
            "thresholds": {
                "low_cvi": low_cvi,
                "peak_cvi_used": peak_cvi,
                "min_rise_used": min_rise,
                "min_days": min_days,
                "max_days": max_days,
                "max_days_after_peak": max_after,
            },
        })

        # Annotate
        for idx, c in enumerate(cycles, 1):
            c.activity_number = idx
            c.season_label    = self._season_label(c.sowing_date)
            c.season_type     = self._assign_season_type(c.sowing_date, c.harvest_date)

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
    ) -> List[CropCycle]:
        """
        For each prominent CVI peak:
          Walk BACK for sowing (CVI < low_cvi), FWRD for harvest (CVI / NDVI / trough).
        """
        n    = len(cvi_smooth)
        bd   = max(float(bin_days), 1.0)
        peak_win = float(getattr(PipelineConfig, "CROP_CYCLE_PEAK_WIN_DAYS", self.PEAK_WIN))
        wbin = max(3, int(peak_win / bd))
        mbin = max(3, int(min_days / bd))
        xbin = int(max_days / bd)
        harv_max_off = max(mbin + 1, int(max_days_after_peak / bd) + 1)

        raw_peaks: List[Tuple[int, float]] = []
        for i in range(wbin, n - wbin):
            v = float(cvi_smooth[i])
            if v < peak_cvi:
                continue
            if v >= float(np.max(cvi_smooth[i - wbin: i + wbin + 1])) - 0.005:
                raw_peaks.append((i, v))

        min_sep = max(2, int(getattr(PipelineConfig, "CROP_CYCLE_GREENUP_MIN_GRID_SEP", 3)))
        clean_peaks: List[int] = []
        for pi, pv in raw_peaks:
            if clean_peaks and (pi - clean_peaks[-1]) < max(mbin, min_sep):
                if pv > float(cvi_smooth[clean_peaks[-1]]):
                    clean_peaks[-1] = pi
            else:
                clean_peaks.append(pi)

        logger.debug("  %d candidate peak(s) after deduplication", len(clean_peaks))

        cycles: List[CropCycle] = []
        used_up_to = -1

        for peak_idx in clean_peaks:
            if peak_idx <= used_up_to:
                continue

            peak_cvi_v  = float(cvi_smooth[peak_idx])
            peak_ndvi_v = float(ndvi_smooth[peak_idx])

            sow_idx = max(0, peak_idx - xbin)
            for j in range(peak_idx - mbin, max(0, peak_idx - xbin) - 1, -1):
                if float(cvi_smooth[j]) < low_cvi:
                    sow_idx = j
                    break

            h_hi = min(n - 1, peak_idx + harv_max_off)
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

            if cvi_rise_v < min_rise:
                logger.debug("  Peak @%d rejected: CVI rise %.3f < %.3f", peak_idx, cvi_rise_v, min_rise)
                continue
            if not (min_days <= duration <= max_days):
                logger.debug("  Peak @%d rejected: duration %dd out of [%d, %d]",
                             peak_idx, duration, min_days, max_days)
                continue

            cycle_gaps     = [g for g in cloud_gaps
                              if g.start_date >= reg_dates[sow_idx]
                              and g.end_date   <= reg_dates[harv_idx]]
            cloud_gap_days = sum(g.gap_days for g in cycle_gaps)

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
            ))
            used_up_to = harv_idx

        return cycles

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
          shape_score      25 pts — placeholder for future multi-index shape analysis
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

    @staticmethod
    def _season_label(sowing_date: datetime) -> str:
        """Human-readable season name from sowing month (North India)."""
        m, y = sowing_date.month, sowing_date.year
        if 6 <= m <= 9:           return f"Kharif {y}"
        if m in (10, 11, 12):     return f"Rabi {y}/{y + 1}"
        if m in (1, 2, 3):        return f"Rabi {y - 1}/{y}"
        return f"Zaid {y}"

    @staticmethod
    def _assign_season_type(sowing_date: datetime, harvest_date: datetime) -> str:
        """Structured season type: 'kharif' | 'rabi' | 'zaid' | 'cross_season'."""
        sm = sowing_date.month
        if sm in (6, 7, 8, 9):   return 'kharif'
        if sm in (10, 11, 12, 1): return 'rabi'
        if sm in (2, 3, 4, 5):   return 'zaid'
        months_span = (
            (harvest_date.year - sowing_date.year) * 12
            + harvest_date.month - sowing_date.month
        )
        return 'cross_season' if months_span > 7 else 'zaid'


__all__ = ['CropCycle', 'CropCycleDetector', 'CloudGap']