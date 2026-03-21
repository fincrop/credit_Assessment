"""
Crop Performance Analyzer
==========================
Analyzes crop health and yield potential from satellite NDVI observations.

VERSION 3.0 — Major Updates:
1. Temporal growth curve comparison:
   - Actual NDVI time-series compared against crop-specific expected curves
     from CropGrowthCurves (crop_parameters.py)
   - Curve alignment: time-normalizes actual observations to 0–100% of
     crop duration so early vs late sown crops are fairly compared
2. Health score (5 components instead of 4):
   - Peak achievement vs crop-specific expected peak (35 pts)
   - Average achievement vs crop-specific expected avg  (25 pts)
   - Growth curve fit — how well actual matches expected shape (20 pts)
   - Temporal consistency / noise level (10 pts)
   - Senescence timing — does crop decline at the right phase (10 pts)
3. Yield potential (uses crop-specific cumulative NDVI benchmark)
4. Canopy-type aware: LOW-canopy crops not penalised vs HIGH-canopy crops
5. Cross-season aware: merged/long-duration crops handled correctly
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
import logging

try:
    from config.crop_parameters import CropGrowthCurves
    CROP_PARAMS_AVAILABLE = True
except ImportError:
    try:
        from config.crop_parameters import CropGrowthCurves
        CROP_PARAMS_AVAILABLE = True
    except ImportError:
        CROP_PARAMS_AVAILABLE = False

logger = logging.getLogger(__name__)


class CropPerformanceAnalyzer:
    """
    Crop health and yield scoring using temporal NDVI pattern analysis
    and crop-specific growth curve benchmarks.
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        status = "with CropGrowthCurves" if CROP_PARAMS_AVAILABLE else "generic benchmarks"
        logger.info(f"✔ CropPerformanceAnalyzer v3.0 initialized ({status})")

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def analyze_performance(
        self,
        season_results: List[Dict],
        seasonal_data:  List[Dict],
    ) -> Dict:
        """
        Analyze crop health and yield potential for every detected crop event.

        Args:
            season_results: from CropDetector.analyze_cropping_pattern()
            seasonal_data:  raw seasonal dicts from SatelliteDataCollector
                            (used to look up scene lists for each season)

        Returns:
            Dict with seasonal_performance, average_health_score,
            average_yield_score, average_performance_score, n_seasons_analyzed
        """
        logger.info(f"\n{'='*70}")
        logger.info("ANALYZING CROP PERFORMANCE")
        logger.info(f"{'='*70}")

        performance_scores = []

        for result in season_results:
            if not result.get('crop_detected'):
                continue

            crop   = result.get('predicted_crop', 'Unknown')
            scenes = self._get_scenes_for_result(result, seasonal_data)

            if not scenes:
                logger.warning(f"  No scenes for {result.get('season')} {result.get('year')}")
                continue

            try:
                health_score, health_detail = self._calculate_health_score(scenes, crop)
                yield_score,  yield_detail  = self._estimate_yield_potential(scenes, crop)

                label = (f"{result.get('season','?').upper()} "
                         f"{result.get('year','?')}"
                         + (" [X-SEASON]" if result.get('is_cross_season') else ""))

                entry = {
                    'season':               result.get('season'),
                    'year':                 result.get('year'),
                    'crop':                 crop,
                    'is_cross_season':      result.get('is_cross_season', False),
                    'health_score':         health_score,
                    'yield_potential_score': yield_score,
                    'overall_performance':  round((health_score + yield_score) / 2, 1),
                    'health_detail':        health_detail,
                    'yield_detail':         yield_detail,
                    'n_scenes':             len(scenes),
                }
                performance_scores.append(entry)

                logger.info(
                    f"  {label} ({crop}):  "
                    f"Health={health_score:.1f}  "
                    f"[peak {health_detail['observed_peak']:.3f}/"
                    f"{health_detail['expected_peak']:.3f}  "
                    f"fit={health_detail['curve_fit_score']:.1f}]  "
                    f"Yield={yield_score:.1f}  "
                    f"[cum {yield_detail['cumulative_ndvi']:.2f}/"
                    f"{yield_detail['expected_cumulative']:.2f}]"
                )

            except Exception as e:
                logger.warning(
                    f"  Performance analysis failed "
                    f"({result.get('season')} {result.get('year')} {crop}): "
                    f"{str(e)[:80]}"
                )

        # Aggregate
        if performance_scores:
            avg_health = float(np.mean([p['health_score']          for p in performance_scores]))
            avg_yield  = float(np.mean([p['yield_potential_score'] for p in performance_scores]))
            avg_perf   = float(np.mean([p['overall_performance']   for p in performance_scores]))
        else:
            avg_health = avg_yield = avg_perf = 50.0

        logger.info(f"\n📊 Average Performance:")
        logger.info(f"  Health Score:    {avg_health:.1f}/100")
        logger.info(f"  Yield Potential: {avg_yield:.1f}/100")
        logger.info(f"  Overall:         {avg_perf:.1f}/100")
        logger.info(f"  Seasons scored:  {len(performance_scores)}")

        return {
            'seasonal_performance':      performance_scores,
            'average_health_score':      round(avg_health, 1),
            'average_yield_score':       round(avg_yield,  1),
            'average_performance_score': round(avg_perf,   1),
            'n_seasons_analyzed':        len(performance_scores),
        }

    # =========================================================================
    # HEALTH SCORE  (0–100)
    # =========================================================================

    def _calculate_health_score(
        self,
        scenes: List[Dict],
        crop:   str,
    ) -> Tuple[float, Dict]:
        """
        Health score breakdown (100 pts total):
          35 — Peak NDVI vs crop-specific expected peak
          25 — Average NDVI vs crop-specific expected average
          20 — Growth curve fit (actual shape vs expected NDVI curve)
          10 — Temporal consistency (low noise)
          10 — Senescence timing (decline in correct phase)
        """
        ndvi_values = self._chronological_ndvi(scenes)
        if not ndvi_values:
            return 50.0, self._empty_health_detail(crop)

        n = len(ndvi_values)

        # Crop-specific benchmarks
        if CROP_PARAMS_AVAILABLE and crop not in ('Unknown', None, ''):
            expected_peak = CropGrowthCurves.get_peak_ndvi(crop)
            expected_avg  = CropGrowthCurves.get_expected_avg_ndvi(crop)
            canopy_type   = CropGrowthCurves.get_canopy_type(crop)
            crop_duration = CropGrowthCurves.get_crop_duration(crop)
        else:
            expected_peak = 0.80
            expected_avg  = 0.60
            canopy_type   = 'MEDIUM'
            crop_duration = 120

        observed_peak = float(np.max(ndvi_values))
        observed_avg  = float(np.mean(ndvi_values))

        # LOW-canopy tolerance boost
        peak_tol = 1.10 if canopy_type == 'LOW' else 1.00

        # ── 1. Peak achievement (35 pts) ──────────────────────────────────
        peak_ratio = observed_peak / (expected_peak * peak_tol)
        peak_score = min(35.0, peak_ratio * 35.0)

        # ── 2. Average achievement (25 pts) ───────────────────────────────
        avg_ratio  = observed_avg / max(expected_avg, 0.01)
        avg_score  = min(25.0, avg_ratio * 25.0)

        # ── 3. Growth curve fit (20 pts) ──────────────────────────────────
        curve_fit_score = self._score_curve_fit(ndvi_values, crop, crop_duration)

        # ── 4. Temporal consistency (10 pts) ──────────────────────────────
        mean_v   = max(float(np.mean(ndvi_values)), 0.01)
        cv       = float(np.std(ndvi_values)) / mean_v
        # CV of 0.15–0.40 is healthy for a seasonal crop
        # Below 0.15 → too flat (bare soil / permanent veg)
        # Above 0.60 → too noisy (cloud contamination)
        if 0.15 <= cv <= 0.40:
            consistency_score = 10.0
        elif cv < 0.15:
            consistency_score = max(0.0, 10.0 - (0.15 - cv) * 80)
        else:
            consistency_score = max(0.0, 10.0 - (cv - 0.40) * 20)

        # ── 5. Senescence timing (10 pts) ─────────────────────────────────
        senescence_score = self._score_senescence(ndvi_values, canopy_type)

        health_score = (peak_score + avg_score + curve_fit_score
                        + consistency_score + senescence_score)
        health_score = round(min(100.0, max(0.0, health_score)), 1)

        detail = {
            'expected_peak':      round(expected_peak, 3),
            'observed_peak':      round(observed_peak, 3),
            'expected_avg':       round(expected_avg,  3),
            'observed_avg':       round(observed_avg,  3),
            'canopy_type':        canopy_type,
            'peak_score':         round(peak_score,         1),
            'avg_score':          round(avg_score,          1),
            'curve_fit_score':    round(curve_fit_score,    1),
            'consistency_score':  round(consistency_score,  1),
            'senescence_score':   round(senescence_score,   1),
            'cv':                 round(cv, 4),
            'n_scenes':           n,
        }

        return health_score, detail

    def _score_curve_fit(
        self,
        ndvi_values:   List[float],
        crop:          str,
        crop_duration: int,
    ) -> float:
        """
        Compare actual NDVI time-series shape against the expected growth
        curve from CropGrowthCurves via time-normalization.

        Method:
          - Map each actual scene to a 'days since sowing' position by
            distributing evenly across crop_duration
          - Get expected NDVI at that position from CropGrowthCurves
          - RMSE between actual and expected → convert to 0–20 score
        """
        if not CROP_PARAMS_AVAILABLE or crop in ('Unknown', None, '') or not ndvi_values:
            return 10.0   # neutral score if no benchmark

        n = len(ndvi_values)
        # Time-normalize: spread scenes across crop duration
        day_positions = np.linspace(0, crop_duration, n)

        expected_vals = []
        for day in day_positions:
            exp_ndvi, _ = CropGrowthCurves.get_expected_ndvi(crop, int(day))
            expected_vals.append(exp_ndvi)

        expected_arr = np.array(expected_vals)
        actual_arr   = np.array(ndvi_values)

        rmse = float(np.sqrt(np.mean((actual_arr - expected_arr) ** 2)))

        # RMSE → score:  0.00 → 20 pts,  0.15 → 10 pts,  ≥0.30 → 0 pts
        score = max(0.0, 20.0 * (1.0 - rmse / 0.30))
        return round(score, 1)

    def _score_senescence(self, ndvi_values: List[float], canopy_type: str) -> float:
        """
        Score the decline phase (last quarter of season).

        Non-perennial crops should show NDVI decline in the final quarter.
        Perennial crops should stay elevated (no harvest).
        """
        n = len(ndvi_values)
        if n < 4:
            return 5.0

        q = max(1, n // 4)
        mid_mean  = float(np.mean(ndvi_values[q: 3*q]))
        late_mean = float(np.mean(ndvi_values[3*q:]))

        if canopy_type == 'PERENNIAL':
            # Perennial: reward stable or growing late NDVI
            if late_mean >= mid_mean * 0.88:
                return 10.0
            else:
                return max(0.0, 10.0 - (mid_mean - late_mean) / mid_mean * 30)
        else:
            # Annual: reward clear decline (harvest signal)
            decline = mid_mean - late_mean
            if decline > 0.10:
                return 10.0
            elif decline > 0.05:
                return 7.0
            elif decline > 0.0:
                return 4.0
            else:
                return 2.0   # no decline — possibly mis-classified season

    # =========================================================================
    # YIELD POTENTIAL  (0–100)
    # =========================================================================

    def _estimate_yield_potential(
        self,
        scenes: List[Dict],
        crop:   str,
    ) -> Tuple[float, Dict]:
        """
        Estimate yield potential using cumulative NDVI as a proxy for
        intercepted PAR (photosynthetically active radiation) and biomass.

        Three components:
          60% — Cumulative NDVI ratio vs crop-specific expected cumulative
          25% — Peak NDVI ratio (captures peak productivity)
          15% — NDVI during critical growth stages (if curve data available)
        """
        ndvi_values = self._chronological_ndvi(scenes)

        if not ndvi_values:
            return 50.0, {'cumulative_ndvi': 0.0, 'expected_cumulative': 6.5,
                          'yield_ratio': 0.0, 'note': 'No NDVI data'}

        # Crop benchmarks
        if CROP_PARAMS_AVAILABLE and crop not in ('Unknown', None, ''):
            expected_cumulative = CropGrowthCurves.get_expected_cumulative_ndvi(crop)
            expected_peak       = CropGrowthCurves.get_peak_ndvi(crop)
            crop_duration       = CropGrowthCurves.get_crop_duration(crop)
        else:
            expected_cumulative = 6.5
            expected_peak       = 0.80
            crop_duration       = 120

        cumulative_ndvi = float(np.sum(ndvi_values))
        observed_peak   = float(np.max(ndvi_values))

        # ── Component 1: Cumulative NDVI ratio (60 pts) ───────────────────
        cum_ratio = cumulative_ndvi / max(expected_cumulative, 0.1)
        if cum_ratio >= 1.00:
            cum_score = 60.0
        elif cum_ratio >= 0.85:
            cum_score = 42.0 + (cum_ratio - 0.85) / 0.15 * 18.0
        elif cum_ratio >= 0.70:
            cum_score = 30.0 + (cum_ratio - 0.70) / 0.15 * 12.0
        elif cum_ratio >= 0.50:
            cum_score = 12.0 + (cum_ratio - 0.50) / 0.20 * 18.0
        else:
            cum_score = (cum_ratio / 0.50) * 12.0

        # ── Component 2: Peak NDVI ratio (25 pts) ─────────────────────────
        peak_ratio = observed_peak / max(expected_peak, 0.01)
        peak_score = min(25.0, peak_ratio * 25.0)

        # ── Component 3: Critical-stage NDVI (15 pts) ─────────────────────
        critical_score = self._score_critical_stages(ndvi_values, crop, crop_duration)

        yield_score = min(100.0, max(0.0,
                                     cum_score + peak_score + critical_score))
        yield_score = round(yield_score, 1)

        detail = {
            'cumulative_ndvi':     round(cumulative_ndvi,      3),
            'expected_cumulative': round(expected_cumulative,  3),
            'cum_ratio':           round(cum_ratio,            3),
            'observed_peak':       round(observed_peak,        3),
            'expected_peak':       round(expected_peak,        3),
            'peak_ratio':          round(peak_ratio,           3),
            'cum_score':           round(cum_score,            1),
            'peak_score':          round(peak_score,           1),
            'critical_score':      round(critical_score,       1),
            'n_scenes':            len(ndvi_values),
        }

        return yield_score, detail

    def _score_critical_stages(
        self,
        ndvi_values:   List[float],
        crop:          str,
        crop_duration: int,
    ) -> float:
        """
        Score NDVI at the expected critical growth stages (15 pts max).

        Uses CropGrowthCurves to identify which days are critical,
        then checks what the actual NDVI was at those time-normalized points.
        """
        if not CROP_PARAMS_AVAILABLE or crop in ('Unknown', None, '') or not ndvi_values:
            return 7.5   # neutral half-score

        n = len(ndvi_values)
        critical_stages = CropGrowthCurves.CRITICAL_STAGES.get(crop, [])
        curve           = CropGrowthCurves.EXPECTED_NDVI_CURVES.get(crop, [])

        if not critical_stages or not curve:
            return 7.5

        # Find days in curve that are critical stages
        critical_days = [
            day for day, ndvi, stage in curve
            if any(cs in stage for cs in critical_stages)
        ]

        if not critical_days:
            return 7.5

        # Time-normalize: map critical days to actual scene indices
        scores = []
        for crit_day in critical_days:
            # scene index corresponding to this day in the actual series
            frac  = crit_day / max(crop_duration, 1)
            idx   = int(frac * (n - 1))
            idx   = min(max(idx, 0), n - 1)

            actual_ndvi   = ndvi_values[idx]
            expected_ndvi, _ = CropGrowthCurves.get_expected_ndvi(crop, crit_day)

            stage_ratio = actual_ndvi / max(expected_ndvi, 0.01)
            scores.append(min(1.0, stage_ratio))

        if not scores:
            return 7.5

        mean_critical_ratio = float(np.mean(scores))
        return round(mean_critical_ratio * 15.0, 1)

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _chronological_ndvi(scenes: List[Dict]) -> List[float]:
        """Return NDVI values sorted chronologically."""
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        return [s['indices'].get('NDVI_mean', 0.0) for s in sorted_scenes]

    @staticmethod
    def _get_scenes_for_result(result: Dict, seasonal_data: List[Dict]) -> List[Dict]:
        """
        Retrieve the scene list for a crop result.

        For cross-season results, combine scenes from all constituent seasons.
        For normal results, look up by (season, year).
        """
        if result.get('is_cross_season') and result.get('season_keys'):
            # Cross-season: gather scenes from all constituent seasons
            all_scenes = []
            for season_key in result['season_keys']:
                season_name, year = season_key
                match = next(
                    (s for s in seasonal_data
                     if s.get('season') == season_name and s.get('year') == year),
                    None,
                )
                if match:
                    all_scenes.extend(match.get('scenes', []))
            # Sort combined list chronologically
            return sorted(all_scenes, key=lambda s: s.get('date', ''))

        # Normal single-season lookup
        season_name = result.get('season')
        year        = result.get('year')
        match = next(
            (s for s in seasonal_data
             if s.get('season') == season_name and s.get('year') == year),
            None,
        )
        return match.get('scenes', []) if match else []

    @staticmethod
    def _empty_health_detail(crop: str) -> Dict:
        return {
            'expected_peak': 0.80, 'observed_peak': 0.0,
            'expected_avg':  0.60, 'observed_avg':  0.0,
            'canopy_type': 'MEDIUM',
            'peak_score': 0.0, 'avg_score': 0.0,
            'curve_fit_score': 0.0, 'consistency_score': 0.0,
            'senescence_score': 0.0, 'cv': 0.0, 'n_scenes': 0,
            'note': 'No NDVI data available',
        }