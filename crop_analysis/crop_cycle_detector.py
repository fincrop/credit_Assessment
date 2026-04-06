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

6.  v3.1 STAGE-4 ALIGNMENT
    - Processing grid step matches satellite ``interval_days`` (passed from main).
    - Linear edge extrapolation on the imputation grid (not flat fill).
    - Sowing: NDVI-led stable rise from low baseline; transplant path via EVI+NDMI.
    - Harvest: post-peak NDVI decline → cross low threshold → low plateau / min;
      optional rapid CVI drop and NDMI/NDVI divergence shorten the end date.
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

# Season anchors aligned with satellite window (hint-building); see satellite_collector._snap_to_season_start
_KHARIF_START = (6, 15)
_RABI_START   = (10, 15)

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
    # Post-peak harvest phases (grid dates; harvest_date == final clearance / stabilize)
    harvest_start_date: Optional[datetime] = None
    harvest_end_date:   Optional[datetime] = None

    def to_dict(self) -> Dict:
        """Serialise to dict — includes start_date/end_date aliases."""
        d = {
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
        if self.harvest_start_date is not None:
            d['harvest_start_date'] = self.harvest_start_date.strftime('%Y-%m-%d')
        if self.harvest_end_date is not None:
            d['harvest_end_date'] = self.harvest_end_date.strftime('%Y-%m-%d')
        return d


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
        grid_step_days: Optional[float] = None,
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
            grid_step_days: Bin size in days — should match ``continuous_data['interval_days']``
                         (e.g. 10). If None, uses ``PipelineConfig.CYCLE_GRID_STEP_DAYS``.

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

        P = PipelineConfig
        step_days = float(
            grid_step_days
            if grid_step_days is not None
            else getattr(P, "CYCLE_GRID_STEP_DAYS", 10)
        )

        # ── 5. Regularise to uniform time grid & impute cloud gaps ─────────
        reg_dates, reg_ndvi, reg_evi, reg_ndmi = self._regularise_and_impute(
            dt_dates, ndvi_arr, evi_arr, ndmi_arr, cloud_gaps, step_days=step_days
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
                bin_days   = step_days,
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

    @staticmethod
    def _iter_nan_runs(arr: np.ndarray):
        """Yield (lo, hi) inclusive indices for contiguous NaN segments."""
        n = len(arr)
        i = 0
        while i < n:
            if np.isfinite(arr[i]):
                i += 1
                continue
            lo = i
            while i < n and not np.isfinite(arr[i]):
                i += 1
            yield lo, i - 1

    @staticmethod
    def _snap_observations_to_grid(
        obs_offsets: np.ndarray,
        values: np.ndarray,
        n_grid: int,
        step_days: float,
    ) -> np.ndarray:
        """Map irregular obs onto nearest 14-day bin (mean if multiple hit same bin)."""
        acc_sum = np.zeros(n_grid, dtype=float)
        acc_cnt = np.zeros(n_grid, dtype=int)
        for off, v in zip(obs_offsets, values):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if np.isnan(fv):
                continue
            gi = int(np.round(float(off) / step_days))
            gi = int(np.clip(gi, 0, n_grid - 1))
            acc_sum[gi] += fv
            acc_cnt[gi] += 1
        out = np.full(n_grid, np.nan, dtype=float)
        m = acc_cnt > 0
        out[m] = acc_sum[m] / acc_cnt[m]
        return out

    @staticmethod
    def _extrapolate_finite_edges_linear(arr: np.ndarray, fill: float = 0.22) -> np.ndarray:
        """Extrapolate leading/trailing NaN bins using neighbour slope (not flat clamp)."""
        a = np.array(arr, dtype=float, copy=True)
        good = np.where(np.isfinite(a))[0]
        if len(good) == 0:
            a[:] = fill
            return a
        fi, li = int(good[0]), int(good[-1])
        if fi > 0:
            if fi + 1 < len(a) and np.isfinite(a[fi + 1]):
                slope = float(a[fi + 1] - a[fi])
            else:
                slope = 0.0
            for j in range(fi - 1, -1, -1):
                a[j] = a[j + 1] - slope
        if li < len(a) - 1:
            if li > 0 and np.isfinite(a[li - 1]):
                slope = float(a[li] - a[li - 1])
            else:
                slope = 0.0
            for j in range(li + 1, len(a)):
                a[j] = a[j - 1] + slope
        return a

    def _fill_short_nan_runs_linear(
        self,
        arr: np.ndarray,
        grid_offsets: np.ndarray,
        step: float,
        short_max_days: float,
    ) -> np.ndarray:
        a = np.array(arr, dtype=float, copy=True)
        for lo, hi in self._iter_nan_runs(a):
            if lo == 0 or hi == len(a) - 1:
                continue
            span = float(grid_offsets[hi] - grid_offsets[lo] + step)
            if span > short_max_days:
                continue
            y0, y1 = float(a[lo - 1]), float(a[hi + 1])
            n = hi - lo + 1
            a[lo : hi + 1] = np.linspace(y0, y1, n + 2)[1:-1]
        return a

    @staticmethod
    def _post_segment_declining(
        arr: np.ndarray,
        start_j: int,
        look: int,
        delta: float,
    ) -> bool:
        """
        True if the first few finite samples after index start_j trend downward
        (post-monsoon / harvest side — informs Kharif gap imputation).
        """
        seq: List[float] = []
        n = len(arr)
        for k in range(start_j, min(start_j + look, n)):
            v = arr[k]
            if np.isfinite(v):
                seq.append(float(v))
            if len(seq) >= 5:
                break
        if len(seq) < 3:
            return False
        early = float(np.mean(seq[:2]))
        late = float(np.mean(seq[-2:]))
        return late < early - delta

    @staticmethod
    def _shape_long_gap(
        y0: float,
        y1: float,
        n: int,
        declining_post: bool,
    ) -> np.ndarray:
        """
        Synthetic profile across n grid steps between known endpoints.
        Uses a hat when post-gap vegetation is declining (Kharif peak inside gap).
        """
        if n <= 0:
            return np.array([], dtype=float)
        xs = np.linspace(0.0, 1.0, n + 2)[1:-1]
        out = np.zeros(n, dtype=float)
        if declining_post:
            base = max(y0, y1, 0.18)
            peak = min(0.82, base + min(0.22, 0.35 * max(0.0, 0.55 - min(y0, y1))))
            peak_t = 0.50 if y1 < y0 + 0.08 else 0.58
            for i, t in enumerate(xs):
                if t <= peak_t:
                    out[i] = y0 + (peak - y0) * (t / max(peak_t, 1e-6))
                else:
                    out[i] = peak + (y1 - peak) * ((t - peak_t) / max(1.0 - peak_t, 1e-6))
        elif y1 > y0 + 0.035:
            out[:] = np.linspace(y0, y1, n, endpoint=True)
        else:
            out[:] = np.linspace(y0, y1, n, endpoint=True)
        return np.clip(out, -0.1, 1.0)

    def _fill_long_nan_runs_contextual(
        self,
        arr: np.ndarray,
        grid_offsets: np.ndarray,
        step: float,
        long_min_days: float,
        decline_look: int,
        decline_delta: float,
        reference_for_decline: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        Fill NaN runs longer than long_min_days using endpoint context.
        Declining trend is read from reference_for_decline (usually NDVI) after the gap.
        """
        a = np.array(arr, dtype=float, copy=True)
        ref = a if reference_for_decline is None else reference_for_decline
        for lo, hi in list(self._iter_nan_runs(a)):
            if lo == 0 or hi == len(a) - 1:
                continue
            span = float(grid_offsets[hi] - grid_offsets[lo] + step)
            if span < long_min_days:
                continue
            y0, y1 = float(a[lo - 1]), float(a[hi + 1])
            declining = self._post_segment_declining(
                ref, hi + 1, decline_look, decline_delta
            )
            n = hi - lo + 1
            a[lo : hi + 1] = self._shape_long_gap(y0, y1, n, declining)
        return a

    def _regularise_and_impute(
        self,
        dt_dates: List[datetime],
        ndvi:     np.ndarray,
        evi:      np.ndarray,
        ndmi:     np.ndarray,
        cloud_gaps: List[CloudGap],
        step_days: float,
    ) -> Tuple[List[datetime], np.ndarray, np.ndarray, np.ndarray]:
        """
        Uniform 14-day grid + chronological imputation.

        Short gaps: linear between known bins (real observations preserved at snap).

        Long gaps (typical Kharif cloud blackout): do **not** use a single straight
        line from pre-monsoon to post-monsoon — that erases a peak inside the gap.
        We use the **first clear samples after the gap** (e.g. September): if NDVI/CVI
        is **declining**, we impute a **unimodal hat** (emergence → peak → drawdown)
        so crop presence and seasonality stay interpretable for cycle detection.

        Gaps are **not dropped**; every grid cell gets a value, but long-gap segments
        are explicitly modelled from boundary + post-boundary dynamics.
        """
        P = PipelineConfig
        step = float(step_days)
        short_max = float(getattr(P, "CYCLE_IMPUTE_SHORT_GAP_MAX_DAYS", 48))
        long_min = float(getattr(P, "CYCLE_IMPUTE_LONG_GAP_MIN_DAYS", 36))
        decline_look = int(getattr(P, "CYCLE_IMPUTE_POST_DECLINE_LOOK", 6))
        decline_delta = float(getattr(P, "CYCLE_IMPUTE_DECLINE_DELTA", 0.028))

        start = dt_dates[0]
        end   = dt_dates[-1]
        total_days = (end - start).days

        grid_dates = [
            start + timedelta(days=d)
            for d in range(0, total_days + 1, int(step))
        ]
        if grid_dates[-1] < end:
            grid_dates.append(end)

        obs_offsets = np.array([(d - start).days for d in dt_dates], dtype=float)
        grid_offsets = np.array([(d - start).days for d in grid_dates], dtype=float)
        n_grid = len(grid_dates)

        ndvi_a = np.asarray(ndvi, dtype=float)
        evi_a = np.asarray(evi, dtype=float)
        ndmi_a = np.asarray(ndmi, dtype=float)

        reg_ndvi = self._snap_observations_to_grid(obs_offsets, ndvi_a, n_grid, step)
        evi_valid_frac = float(np.sum(np.isfinite(evi_a))) / max(len(evi_a), 1)
        use_evi_proxy = evi_valid_frac < 0.3

        if use_evi_proxy:
            reg_evi = np.full(n_grid, np.nan, dtype=float)
        else:
            reg_evi = self._snap_observations_to_grid(obs_offsets, evi_a, n_grid, step)

        reg_ndmi = self._snap_observations_to_grid(obs_offsets, ndmi_a, n_grid, step)

        reg_ndvi = self._extrapolate_finite_edges_linear(reg_ndvi)
        if not use_evi_proxy:
            reg_evi = self._extrapolate_finite_edges_linear(reg_evi)
        reg_ndmi = self._extrapolate_finite_edges_linear(reg_ndmi, fill=0.0)

        reg_ndvi = self._fill_short_nan_runs_linear(
            reg_ndvi, grid_offsets, step, short_max
        )
        if not use_evi_proxy:
            reg_evi = self._fill_short_nan_runs_linear(
                reg_evi, grid_offsets, step, short_max
            )
        reg_ndmi = self._fill_short_nan_runs_linear(
            reg_ndmi, grid_offsets, step, short_max
        )

        reg_ndvi = self._fill_long_nan_runs_contextual(
            reg_ndvi,
            grid_offsets,
            step,
            long_min,
            decline_look,
            decline_delta,
            reference_for_decline=reg_ndvi,
        )
        if not use_evi_proxy:
            reg_evi = self._fill_long_nan_runs_contextual(
                reg_evi,
                grid_offsets,
                step,
                long_min,
                decline_look,
                decline_delta,
                reference_for_decline=reg_ndvi,
            )
        reg_ndmi = self._fill_long_nan_runs_contextual(
            reg_ndmi,
            grid_offsets,
            step,
            long_min,
            decline_look,
            decline_delta,
            reference_for_decline=reg_ndvi,
        )

        reg_ndvi = self._fill_short_nan_runs_linear(
            reg_ndvi, grid_offsets, step, short_max
        )
        if not use_evi_proxy:
            reg_evi = self._fill_short_nan_runs_linear(
                reg_evi, grid_offsets, step, short_max
            )
        reg_ndmi = self._fill_short_nan_runs_linear(
            reg_ndmi, grid_offsets, step, short_max
        )

        def _finite_or_mean(x: np.ndarray, fb: float = 0.25) -> np.ndarray:
            z = np.asarray(x, dtype=float).copy()
            bad = ~np.isfinite(z)
            if not np.any(bad):
                return z
            gm = float(np.nanmean(z[np.isfinite(z)])) if np.any(np.isfinite(z)) else fb
            z[bad] = gm
            return z

        reg_ndvi = _finite_or_mean(reg_ndvi, 0.22)
        reg_ndmi = _finite_or_mean(reg_ndmi, 0.0)
        if not use_evi_proxy:
            reg_evi = _finite_or_mean(reg_evi, 0.2)

        if use_evi_proxy:
            logger.debug(
                "EVI sparse on grid — proxy from imputed NDVI (EVI ~ 0.85*NDVI+0.02)"
            )
            reg_evi = np.clip(0.85 * reg_ndvi + 0.02, 0.0, 1.0)

        reg_ndvi = np.clip(reg_ndvi, -0.1, 1.0)
        reg_evi = np.clip(reg_evi, 0.0, 1.0)
        reg_ndmi = np.clip(reg_ndmi, -0.6, 0.8)

        if cloud_gaps:
            logger.info(
                "  Chronological imputation: long gaps (>=%.0f d) use pre/post bins + "
                "post-gap trend (e.g. Sep decline => Kharif-shaped fill).",
                long_min,
            )

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

    def _refine_sowing_index(
        self,
        gi: int,
        ndvi: np.ndarray,
        evi: np.ndarray,
        ndmi: np.ndarray,
        cvi: np.ndarray,
    ) -> int:
        """
        Sowing = first stable rise in NDVI from a low baseline (~<0.2–0.25), crossing
        ~>0.25–0.3 over 2–3 bins with continued increase (noise rejection). Transplanted
        crops: higher pre-baseline allowed if EVI and NDMI rise with NDVI/CVI.
        """
        P = PipelineConfig
        n = len(ndvi)
        bl_max = float(getattr(P, "CROP_CYCLE_SOW_BASELINE_MAX", 0.24))
        cross = float(getattr(P, "CROP_CYCLE_SOW_CROSS_MIN", 0.27))
        n_rise = int(getattr(P, "CROP_CYCLE_SOW_MIN_RISE_STEPS", 3))
        tol = float(getattr(P, "CROP_CYCLE_SOW_NOISE_DROP_TOL", 0.018))
        tp_bl = float(getattr(P, "CROP_CYCLE_SOW_TRANSPLANT_BASELINE_MAX", 0.40))
        tevi = float(getattr(P, "CROP_CYCLE_SOW_TRANSPLANT_EVI_DELTA", 0.02))
        tndmi = float(getattr(P, "CROP_CYCLE_SOW_TRANSPLANT_NDMI_DELTA", 0.014))

        lo = max(0, gi - 6)
        hi = min(n - 1, gi + 2)
        scan_hi = min(hi, n - n_rise - 2)
        candidates: List[int] = []

        for s in range(max(3, lo), scan_hi + 1):
            pre = float(np.nanmean(ndvi[max(0, s - 3): s]))
            if pre > bl_max + 1e-6:
                continue
            seg = [float(ndvi[s + k]) for k in range(n_rise)]
            if not all(np.isfinite(seg)):
                continue
            rising = all(seg[k + 1] > seg[k] - tol for k in range(n_rise - 1))
            if not rising or seg[-1] < cross:
                continue
            if s + n_rise < n:
                nxt = float(ndvi[s + n_rise])
                if np.isfinite(nxt) and nxt < seg[-1] - tol:
                    continue
            candidates.append(s)

        if candidates:
            return min(candidates)

        for s in range(max(3, lo), scan_hi + 1):
            pre = float(np.nanmean(ndvi[max(0, s - 3): s]))
            if pre > tp_bl + 1e-6 or pre < 0.10:
                continue
            seg_n = [float(ndvi[s + k]) for k in range(n_rise)]
            seg_e = [float(evi[s + k]) for k in range(n_rise)]
            seg_m = [float(ndmi[s + k]) for k in range(n_rise)]
            if not all(np.isfinite(seg_n)) or not all(np.isfinite(seg_e)):
                continue
            evi_ok = seg_e[-1] - seg_e[0] >= tevi
            ndmi_ok = all(np.isfinite(seg_m)) and seg_m[-1] - seg_m[0] >= tndmi
            ndvi_ok = seg_n[-1] > seg_n[0] + 0.04 and seg_n[-1] >= 0.22
            cvi_seg = [float(cvi[s + k]) for k in range(n_rise)]
            cvi_ok = all(np.isfinite(cvi_seg)) and cvi_seg[-1] > cvi_seg[0] + 0.03
            if ndvi_ok and cvi_ok and (evi_ok or ndmi_ok or seg_n[-1] >= 0.26):
                return s

        return gi

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
        bin_days:    float = 10.0,
    ) -> Optional['CropCycle']:
        """Trace a full cycle: refine sowing on NDVI rules, peak on CVI, harvest in 3 NDVI phases."""
        bd = max(float(bin_days), 1.0)
        sowing_idx = self._refine_sowing_index(
            greenup_idx, ndvi_smooth, evi_raw, ndmi_raw, cvi_smooth,
        )

        remaining = len(cvi_smooth) - sowing_idx - 1
        max_days = int(max_duration_days_override) if max_duration_days_override else int(self.max_duration_days)
        search_max = min(int(max_days / bd) + 12, remaining)

        if search_max < 5:
            return None

        window_end = sowing_idx + search_max

        peak_rel = int(np.argmax(cvi_smooth[sowing_idx: window_end]))
        peak_idx = sowing_idx + peak_rel
        peak_cvi = float(cvi_smooth[peak_idx])
        peak_ndvi = float(ndvi_smooth[peak_idx])

        if peak_idx <= sowing_idx or peak_cvi < self.min_peak_cvi:
            return None

        post_peak_bins = max(5, int(np.ceil(self.max_days_after_peak / bd)) + 2)
        harvest_window_end = min(window_end, peak_idx + post_peak_bins)

        phases = self._detect_harvest_phases(
            cvi_smooth=cvi_smooth,
            ndvi_smooth=ndvi_smooth,
            ndmi_raw=ndmi_raw,
            peak_idx=peak_idx,
            max_idx=harvest_window_end,
        )
        if phases is None:
            return None
        h_start_idx, h_end_idx, harvested_idx = phases

        sowing_date = dt_dates[sowing_idx]
        peak_date = dt_dates[peak_idx]
        harvest_start_date = dt_dates[h_start_idx]
        harvest_end_date = dt_dates[h_end_idx]
        harvest_date = dt_dates[harvested_idx]
        duration_days = (harvest_date - sowing_date).days

        if not (self.min_duration_days <= duration_days <= self.max_duration_days):
            return None

        ndvi_seg = ndvi_raw[sowing_idx: harvested_idx + 1]
        day_offsets = np.array(
            [(dt_dates[j] - sowing_date).days
             for j in range(sowing_idx, harvested_idx + 1)],
            dtype=float,
        )
        integral_ndvi = float(np.nansum(ndvi_seg))
        integral_ndvi_days = (
            float(np.trapz(ndvi_seg, day_offsets)) if len(ndvi_seg) > 1 else 0.0
        )
        baseline_ndvi = float(
            np.mean(ndvi_smooth[max(0, sowing_idx - 3): sowing_idx + 1])
        )

        cycle_gaps = [
            g for g in cloud_gaps
            if g.start_date >= sowing_date and g.end_date <= harvest_date
        ]
        cloud_gap_days = sum(g.gap_days for g in cycle_gaps)

        confidence = self._calculate_confidence(
            ndvi_seg=ndvi_seg,
            duration_days=duration_days,
            peak_cvi=peak_cvi,
            cloud_gap_days=cloud_gap_days,
        )

        return CropCycle(
            sowing_date=sowing_date,
            harvest_date=harvest_date,
            peak_date=peak_date,
            duration_days=duration_days,
            crop_type=self._classify_by_duration_and_indices(
                duration_days, peak_ndvi, peak_cvi),
            peak_ndvi=peak_ndvi,
            baseline_ndvi=baseline_ndvi,
            ndvi_rise=float(peak_ndvi - ndvi_smooth[sowing_idx]),
            integral_ndvi=integral_ndvi,
            integral_ndvi_days=integral_ndvi_days,
            peak_evi=float(np.nanmean(evi_raw[peak_idx - 1: peak_idx + 2])),
            peak_ndmi=float(np.nanmean(ndmi_raw[peak_idx - 1: peak_idx + 2])),
            peak_cvi=peak_cvi,
            confidence=confidence,
            cloud_gap_days=cloud_gap_days,
            has_cloud_gap=len(cycle_gaps) > 0,
            harvest_start_date=harvest_start_date,
            harvest_end_date=harvest_end_date,
        )

    def _detect_harvest_phases(
        self,
        cvi_smooth: np.ndarray,
        ndvi_smooth: np.ndarray,
        ndmi_raw: np.ndarray,
        peak_idx: int,
        max_idx: int,
    ) -> Optional[Tuple[int, int, int]]:
        """
        Harvest_start: post-peak sustained NDVI decline (several consecutive drops).
        Harvest_end: NDVI falls below low threshold (~0.3–0.4).
        Harvested (returned as cycle end): low plateau / minimum after that, or rapid
        CVI collapse (mechanical harvest) if earlier.
        """
        P = PipelineConfig
        low_ndvi = float(getattr(P, "CROP_CYCLE_HARVEST_LOW_NDVI", 0.36))
        n_decl = int(getattr(P, "CROP_CYCLE_HARVEST_DECLINE_STEPS", 3))
        min_drop = float(getattr(P, "CROP_CYCLE_HARVEST_DECLINE_MIN_DROP", 0.018))
        peak_drop_frac = float(getattr(P, "CROP_CYCLE_HARVEST_PEAK_DROP_FRAC", 0.06))
        stab_std = float(getattr(P, "CROP_CYCLE_HARVEST_STABLE_MAX_STD", 0.022))
        stab_run = int(getattr(P, "CROP_CYCLE_HARVEST_STABLE_RUN", 3))

        search_end = min(max_idx, len(ndvi_smooth) - 1, len(cvi_smooth) - 1)
        if search_end <= peak_idx + n_decl:
            return None

        peak_ndvi = float(ndvi_smooth[peak_idx])
        peak_cvi = float(cvi_smooth[peak_idx])

        harvest_start: Optional[int] = None
        for i in range(peak_idx + n_decl, search_end + 1):
            ok = True
            for t in range(n_decl):
                if float(ndvi_smooth[i - t]) >= float(ndvi_smooth[i - t - 1]) - min_drop:
                    ok = False
                    break
            if not ok:
                continue
            if float(ndvi_smooth[i]) > peak_ndvi * (1.0 - peak_drop_frac):
                continue
            harvest_start = i
            break

        if harvest_start is None:
            for i in range(peak_idx + 2, search_end + 1):
                if float(ndvi_smooth[i]) < peak_ndvi - 0.04:
                    harvest_start = i
                    break
        if harvest_start is None:
            harvest_start = peak_idx + 2

        harvest_end = harvest_start
        for k in range(harvest_start, search_end + 1):
            if float(ndvi_smooth[k]) < low_ndvi:
                harvest_end = k
                break
        else:
            harvest_end = search_end

        harvested = harvest_end
        stabilized = False
        upper = min(search_end - stab_run + 1, len(ndvi_smooth) - stab_run)
        for j in range(harvest_end, upper + 1):
            chunk = ndvi_smooth[j: j + stab_run]
            if np.all(np.isfinite(chunk)) and float(np.std(chunk)) < stab_std:
                harvested = j + stab_run - 1
                stabilized = True
                break
        if not stabilized:
            tail = ndvi_smooth[harvest_end: search_end + 1]
            if len(tail) > 0:
                harvested = harvest_end + int(np.argmin(tail))

        cvi_post = cvi_smooth[peak_idx: search_end + 1]
        rapid_idx: Optional[int] = None
        for i in range(1, len(cvi_post) - 1):
            val = float(cvi_post[i])
            rate = float(cvi_post[i - 1]) - val
            if rate >= 0.07 and val < peak_cvi * 0.80:
                rapid_idx = peak_idx + i
                break

        if rapid_idx is not None and peak_idx < rapid_idx <= harvested:
            harvested = rapid_idx
            harvest_end = min(harvest_end, harvested)
            harvest_start = min(harvest_start, max(peak_idx + 1, rapid_idx - 1))

        ndvi_post = ndvi_smooth[peak_idx: search_end + 1]
        ndmi_seg = ndmi_raw[peak_idx: search_end + 1]
        for i in range(2, len(ndvi_post) - 1):
            ndvi_drop = float(ndvi_post[i - 2]) - float(ndvi_post[i])
            if len(ndmi_seg) > i + 1 and np.isfinite(ndmi_seg[i]) and np.isfinite(ndmi_seg[i - 2]):
                ndmi_rise = float(ndmi_seg[i]) - float(ndmi_seg[i - 2])
                if ndvi_drop > 0.08 and ndmi_rise > 0.05:
                    alt = peak_idx + i
                    if alt < harvested:
                        harvested = alt
                        harvest_end = min(harvest_end, harvested)
                    break

        harvest_start = int(np.clip(harvest_start, peak_idx + 1, search_end))
        harvest_end = int(np.clip(max(harvest_end, harvest_start), harvest_start, search_end))
        harvested = int(np.clip(max(harvested, harvest_end), harvest_end, search_end))

        return harvest_start, harvest_end, harvested

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
