"""
Crop Performance Analyzer â€” VERSION 4.0
=========================================
Dual-Path Architecture:

  PATH A â€” BASIC / CROP-SPECIFIC (unchanged from v3.0)
    Selected when: crop classification is reliable (confidence >= 0.25,
    not Unknown). Uses CropGrowthCurves benchmark curves: peak NDVI, expected
    averages, growth arc shape, senescence timing, critical stage NDVI.
    Fully parameterised per crop.

  PATH B â€” ENHANCED / CROP-AGNOSTIC (new in v4.0)
    Selected when: crop is Unknown, low-confidence, or classification failed.
    Also used when the cycle is ACTIVE (currently growing).
    Requires only the chronological scene list â€” no crop identity needed.

    Health components (100 pts):
      30 â€” Arc quality:      rise â†’ peak â†’ fall trajectory shape
      25 â€” Biomass level:    composite vegetation index (NDVI+EVI+NDMI)
      20 â€” Stability:        anomaly severity (IQR-based fluctuation detector)
      15 â€” Growth momentum:  rate and smoothness of the rising phase
      10 â€” Canopy duration:  fraction of cycle with adequate canopy cover

    Yield potential (0â€“100 %):
      35 â€” Biomass accumulation (CVI area under curve vs adaptive baseline)
      30 â€” Anomaly-free fraction (penalise disruptive events)
      20 â€” Peak biomass quality
      15 â€” Temporal consistency (low noise in peak phase)

    Active cycles: last quarter capped, projection note added.

  Every performance entry carries:
    - scoring_method: 'crop_specific' | 'enhanced' | 'signal_only'
    - performance_narrative: plain English for AI/lender reports
    - anomaly_events: list of detected stress events with stage + magnitude
"""

import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

from config import PipelineConfig

try:
    from utils.peer_benchmark import PeerBenchmark
    PEER_BENCHMARK_AVAILABLE = True
except Exception:  # pragma: no cover
    PeerBenchmark = None
    PEER_BENCHMARK_AVAILABLE = False

try:
    from config import CropGrowthCurves
    CROP_PARAMS_AVAILABLE = True
except ImportError:
    CROP_PARAMS_AVAILABLE = False

logger = logging.getLogger(__name__)

TODAY = datetime.now().date()


class CropPerformanceAnalyzer:
    """
    Crop health and yield scoring.
    Dual-path: CROP-SPECIFIC benchmarks (BASIC) or CROP-AGNOSTIC
    chronological trajectory analysis (ENHANCED).
    """

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        # Pillar 3 state (set per-run by analyze_performance; safe defaults here
        # so the enhanced methods are callable standalone / in unit tests).
        self._peer = None
        self._cohort_key = None
        self._parcel_stage_baseline: Dict = {}
        status = "with CropGrowthCurves" if CROP_PARAMS_AVAILABLE else "generic benchmarks"
        logger.info("CropPerformanceAnalyzer v4.0 initialized (%s)", status)

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def analyze_performance(
        self,
        season_results: List[Dict],
        seasonal_data:  List[Dict],
        cohort_key: Optional[str] = None,
        peer_benchmark=None,
    ) -> Dict:
        """
        Analyze crop health and yield potential for every detected crop event.

        Scoring method selection per cycle:
          crop_specific  â†’ crop reliable + CROP_PARAMS_AVAILABLE + scenes
          enhanced       â†’ scenes available (crop-agnostic trajectory analysis)
          signal_only    â†’ no scenes (cultivation_signal only)

        Args:
            season_results: from CropDetector.analyze_cycles()
            seasonal_data:  raw seasonal dicts from SatelliteDataCollector

        Returns:
            Dict with seasonal_performance, averages, n_seasons_analyzed
        """
        logger.info(f"\n{'='*70}")
        logger.info("ANALYZING CROP PERFORMANCE  (v4.0 Dual-Path)")
        logger.info(f"{'='*70}")

        # Pillar 3: peer benchmark + per-parcel per-stage stress baseline.
        if peer_benchmark is not None:
            self._peer = peer_benchmark
        elif PEER_BENCHMARK_AVAILABLE:
            self._peer = PeerBenchmark()  # cold-start; falls back to internal score
        else:
            self._peer = None
        self._cohort_key = cohort_key
        self._parcel_stage_baseline = self._build_parcel_stage_baseline(season_results)

        performance_scores = []

        for result in season_results:
            if not result.get('crop_detected'):
                continue

            crop               = result.get('predicted_crop') or 'Unknown'
            scenes             = self._get_scenes_for_result(result, seasonal_data)
            cultivation_signal = result.get('cultivation_signal')
            crop_confidence    = result.get('crop_confidence', 0.0)
            classification_note = result.get('classification_note', '')
            end_date_str        = result.get('end_date', '')
            duration_days       = result.get('duration_days', 0)

            # Detect active (currently growing) cycle
            is_active_cycle = False
            try:
                end_d = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                if end_d >= TODAY:
                    is_active_cycle = True
            except Exception:
                pass

            is_crop_reliable = (
                crop not in ('Unknown', None, '')
                and crop_confidence >= 0.25
                and 'error' not in classification_note
                and CROP_PARAMS_AVAILABLE
            )

            label = (
                f"{result.get('season','?').upper()} "
                f"{result.get('year','?')}"
                + (" [ACTIVE]" if is_active_cycle else "")
                + (" [X-SEASON]" if result.get('is_cross_season') else "")
            )

            # â”€â”€ PATH SELECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not scenes:
                # No per-cycle scene list — use detector metrics (peak NDVI, duration,
                # cycle confidence) blended with cultivation_signal. Avoids treating
                # peak-only proxies as full trajectory scores.
                if cultivation_signal is not None:
                    peak_ndvi = float(result.get('peak_ndvi') or 0.0)
                    cconf = float(result.get('cycle_confidence') or 0.0)
                    h_score, h_detail, y_score, y_detail = self._cycle_detector_proxy_scores(
                        peak_ndvi=peak_ndvi,
                        cycle_confidence=cconf,
                        duration_days=duration_days,
                        cultivation_signal=float(cultivation_signal),
                        crop=crop,
                    )
                    narrative = self._build_narrative(
                        crop, 'signal_only', h_score, y_score, [], is_active_cycle
                    )
                    entry = {
                        'season':               result.get('season'),
                        'year':                 result.get('year'),
                        'start_date':           result.get('start_date'),
                        'end_date':             end_date_str,
                        'crop':                 crop,
                        'cultivation_signal':   cultivation_signal,
                        'cycle_confidence':     cconf,
                        'peak_ndvi':            peak_ndvi,
                        'is_crop_name_reliable': False,
                        'is_active_cycle':      is_active_cycle,
                        'is_cross_season':      result.get('is_cross_season', False),
                        'health_score':         h_score,
                        'yield_potential_score': y_score,
                        'yield_potential_pct':  f"{y_score:.0f}%",
                        'overall_performance':  round((h_score + y_score) / 2, 1),
                        'health_detail':        h_detail,
                        'yield_detail':         y_detail,
                        'anomaly_events':       [],
                        'n_scenes':             0,
                        'scoring_method':       'signal_only',
                        'performance_narrative': narrative,
                    }
                    performance_scores.append(entry)
                    logger.info(
                        f"  {label}: signal_only  Health={h_score:.1f}  "
                        f"Yield={y_score:.1f}%"
                    )
                else:
                    logger.warning("  %s: no scenes and no signal - skipped", label)
                continue

            # â”€â”€ PATH A: CROP-SPECIFIC (BASIC) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if is_crop_reliable and not is_active_cycle:
                try:
                    h_score, h_detail = self._calculate_health_score(scenes, crop)
                    y_score, y_detail = self._estimate_yield_potential(scenes, crop)
                    anomaly_events    = []
                    method            = 'crop_specific'
                except Exception as e:
                    logger.warning(f"  {label}: BASIC path failed ({e}), falling to ENHANCED")
                    is_crop_reliable  = False

            # â”€â”€ PATH B: ENHANCED / CROP-AGNOSTIC â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            if not is_crop_reliable or is_active_cycle:
                try:
                    h_score, h_detail, anomaly_events = self._enhanced_health_score(
                        scenes, duration_days, is_active_cycle
                    )
                    y_score, y_detail = self._enhanced_yield_potential(
                        scenes, duration_days, is_active_cycle, anomaly_events
                    )
                    method = 'enhanced'
                except Exception as e:
                    logger.warning(f"  {label}: ENHANCED path failed ({e})")
                    h_score, h_detail = self._signal_based_health(
                        cultivation_signal or 50, crop
                    )
                    y_score, y_detail = self._signal_based_yield(
                        cultivation_signal or 50, crop
                    )
                    anomaly_events = []
                    method = 'signal_only'

            narrative = self._build_narrative(
                crop, method, h_score, y_score, anomaly_events, is_active_cycle
            )

            entry = {
                'season':               result.get('season'),
                'year':                 result.get('year'),
                'start_date':           result.get('start_date'),
                'end_date':             end_date_str,
                'crop':                 crop,
                'cultivation_signal':   cultivation_signal,
                'is_crop_name_reliable': is_crop_reliable,
                'is_active_cycle':      is_active_cycle,
                'is_cross_season':      result.get('is_cross_season', False),
                'health_score':         h_score,
                'yield_potential_score': y_score,
                'yield_potential_pct':  f"{y_score:.0f}%",
                'overall_performance':  round((h_score + y_score) / 2, 1),
                'health_detail':        h_detail,
                'yield_detail':         y_detail,
                'anomaly_events':       anomaly_events,
                'n_scenes':             len(scenes),
                'scoring_method':       method,
                'performance_narrative': narrative,
            }
            performance_scores.append(entry)

            crit_anomalies = sum(1 for a in anomaly_events if a.get('impact') == 'HIGH')
            rel = "ok" if is_crop_reliable else "?"
            logger.info(
                "  %s (%s %s):  Health=%.1f  Yield=%.1f%%  Anomalies=%d (HIGH=%d)  method=%s",
                label,
                crop,
                rel,
                h_score,
                y_score,
                len(anomaly_events),
                crit_anomalies,
                method,
            )
            for ev in anomaly_events:
                if ev.get('impact') == 'HIGH':
                    logger.info(
                        "    [!] [%s] %s: %s",
                        ev["stage"],
                        ev["type"],
                        ev.get("description", "")[:70],
                    )

        # â”€â”€ AGGREGATE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Exclude active cycles from averages (partial data)
        complete = [p for p in performance_scores if not p.get('is_active_cycle')]
        active   = [p for p in performance_scores if p.get('is_active_cycle')]

        if complete:
            avg_health = float(np.mean([p['health_score']          for p in complete]))
            avg_yield  = float(np.mean([p['yield_potential_score'] for p in complete]))
            avg_perf   = float(np.mean([p['overall_performance']   for p in complete]))
        elif performance_scores:
            avg_health = float(np.mean([p['health_score']          for p in performance_scores]))
            avg_yield  = float(np.mean([p['yield_potential_score'] for p in performance_scores]))
            avg_perf   = float(np.mean([p['overall_performance']   for p in performance_scores]))
        else:
            avg_health = avg_yield = avg_perf = 50.0

        logger.info("\nPerformance summary:")
        logger.info("  Complete cycles scored: %d", len(complete))
        logger.info("  Active cycles:          %d", len(active))
        logger.info("  Average Health Score:   %.1f/100", avg_health)
        logger.info("  Average Yield Proxy:    %.1f%%", avg_yield)
        logger.info("  Overall:                %.1f/100", avg_perf)

        crop_family = self._infer_crop_family_band(season_results, performance_scores)

        return {
            'seasonal_performance':      performance_scores,
            'average_health_score':      round(avg_health, 1),
            'average_yield_score':       round(avg_yield,  1),
            'average_yield_proxy':       round(avg_yield,  1),
            'average_performance_score': round(avg_perf,   1),
            'n_seasons_analyzed':        len(performance_scores),
            'n_complete_cycles':         len(complete),
            'n_active_cycles':           len(active),
            'assessment_timing': {
                'has_active_cycles': len(active) > 0,
                'n_active_cycles': len(active),
                'n_complete_cycles': len(complete),
                'yield_proxy_partial': len(active) > 0,
                'note': (
                    'In-progress cycles are AUC-capped; mid-season assessments '
                    'may look lower than post-harvest on the same trajectory.'
                    if active else
                    'All scored cycles appear complete for the assessment window.'
                ),
            },
            'crop_family_band': crop_family,
            # Pillar 3 — peer benchmarking + per-parcel stress baseline provenance
            'peer_benchmarking': {
                'cohort_key': self._cohort_key,
                'engine': 'peer_benchmark_v1' if self._peer is not None else None,
                'n_cycles_peer_scored': sum(
                    1 for p in performance_scores
                    if (p.get('yield_detail') or {}).get('yield_index_basis') == 'peer_nirv'
                ),
            },
            'stress_baseline': {
                'per_parcel_per_stage': bool(self._parcel_stage_baseline),
                'stages': sorted(self._parcel_stage_baseline.keys()),
            },
        }

    @staticmethod
    def _infer_crop_family_band(
        season_results: List[Dict],
        performance_scores: List[Dict],
    ) -> Dict:
        """Coarse vigor band without full ML — named crop or phenology heuristic."""
        named = [
            (r.get('predicted_crop') or '').strip()
            for r in (season_results or [])
            if (r.get('predicted_crop') or '').strip()
        ]
        if named:
            cereal = {'Rice', 'Wheat', 'Maize', 'Bajra', 'Jowar', 'Ragi'}
            pulse = {'Gram', 'Tur', 'Moong', 'Urad', 'Soyabean', 'Groundnut', 'Mustard'}
            horti = {
                'Banana', 'Papaya', 'Pomegranate', 'Mango', 'Grapes',
                'Potato', 'Onion', 'Tomato', 'Chilli', 'Sugarcane', 'Cotton',
            }
            hit = named[0]
            if hit in cereal:
                band = 'cereal_paddy_like'
            elif hit in pulse:
                band = 'pulse_oilseed_like'
            elif hit in horti:
                band = 'horticulture_high_value'
            else:
                band = 'named_other'
            return {'band': band, 'source': 'predicted_crop', 'example_crop': hit}

        peaks: List[float] = []
        durs: List[float] = []
        for p in performance_scores or []:
            try:
                pk = p.get('peak_ndvi')
                if pk is None:
                    pk = p.get('peak_cvi')
                if pk is not None:
                    peaks.append(float(pk))
            except (TypeError, ValueError):
                pass
            try:
                durs.append(float(p.get('duration_days') or 0))
            except (TypeError, ValueError):
                pass
        for r in season_results or []:
            try:
                peaks.append(float(r.get('peak_ndvi') or 0))
            except (TypeError, ValueError):
                pass
            try:
                durs.append(float(r.get('duration_days') or 0))
            except (TypeError, ValueError):
                pass

        avg_peak = float(np.mean(peaks)) if peaks else 0.0
        pos = [d for d in durs if d > 0]
        avg_dur = float(np.mean(pos)) if pos else 0.0

        if avg_dur >= 250 or avg_peak >= 0.65:
            band = 'horticulture_or_long_duration'
        elif avg_peak >= 0.45:
            band = 'cereal_paddy_like'
        elif avg_peak >= 0.28:
            band = 'pulse_oilseed_like'
        else:
            band = 'low_canopy_or_sparse'

        return {
            'band': band,
            'source': 'phenology_heuristic',
            'avg_peak_ndvi': round(avg_peak, 3),
            'avg_duration_days': round(avg_dur, 1),
        }

    # =========================================================================
    # PATH B — ENHANCED HEALTH SCORE  (crop-agnostic, 0–100)
    # =========================================================================

    def _enhanced_health_score(
        self,
        scenes:         List[Dict],
        duration_days:  int,
        is_active:      bool,
    ) -> Tuple[float, Dict, List[Dict]]:
        """
        Crop-agnostic health score from chronological multi-index trajectory.

        Components (100 pts):
          30 â€” Arc quality:      does the series show rise â†’ peak â†’ fall?
          25 â€” Biomass level:    composite vegetation index (CVI) magnitude
          20 â€” Stability:        how severe / frequent are anomalous drops?
          15 â€” Growth momentum:  rate and smoothness of the rising phase
          10 â€” Canopy duration:  fraction of cycle with CVI > 0.3 (active canopy)

        Multi-index: CVI = 0.5Â·NDVI + 0.3Â·EVI + 0.2Â·NDMI
          â†’ falls back to NDVI-only if EVI/NDMI unavailable.
        """
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))

        # Extract multi-index series
        cvi_series, ndvi_series, dates = self._extract_multi_index(sorted_scenes)
        n = len(cvi_series)

        if n < 3:
            return 50.0, {'note': 'Too few scenes for enhanced analysis', 'n_scenes': n}, []

        cvi = np.array(cvi_series, dtype=float)
        ndvi = np.array(ndvi_series, dtype=float)

        # If active cycle, only evaluate the observed portion
        # (don't penalise fall-phase absence)
        eval_cvi = cvi
        eval_ndvi = ndvi
        active_note = ''
        if is_active:
            # Treat everything as observed; arc score uses 2-phase (rise+peak)
            active_note = ' [partial: actively growing â€” fall phase not yet observed]'

        # â”€â”€ 1. Arc quality (30 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        arc_score = self._score_arc_quality(eval_cvi, is_active)

        # â”€â”€ 2. Biomass level (25 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        peak_cvi   = float(np.max(eval_cvi))
        mean_cvi   = float(np.mean(eval_cvi))
        # calibrated to CVI scale: 0.75 peak = full biomass score
        biomass_peak_score = min(15.0, (peak_cvi / 0.75) * 15.0)
        biomass_avg_score  = min(10.0, (mean_cvi / 0.55) * 10.0)
        biomass_score      = biomass_peak_score + biomass_avg_score

        # â”€â”€ 3. Stability / anomaly detection (20 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # ── 3. Stability / anomaly detection (20 pts) ──────────────────────
        _, lswi_series = self._extract_nirv_lswi(sorted_scenes)
        eval_lswi = (np.array(lswi_series, dtype=float)
                     if len(lswi_series) == n else None)
        anomaly_events = self._detect_index_anomalies(
            eval_cvi, eval_ndvi, dates, n,
            lswi=eval_lswi,
            stage_baseline=self._parcel_stage_baseline,
        )
        stability_score = self._score_stability(eval_cvi, anomaly_events)

        # â”€â”€ 4. Growth momentum (15 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        momentum_score = self._score_growth_momentum(eval_cvi, is_active)

        # â”€â”€ 5. Canopy duration (10 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        active_canopy_frac = float(np.sum(eval_cvi > 0.30)) / max(n, 1)
        canopy_score       = min(10.0, active_canopy_frac * 10.0)

        health_score = round(min(100.0, max(0.0,
            arc_score + biomass_score + stability_score
            + momentum_score + canopy_score
        )), 1)

        detail = {
            'scoring_type':       'enhanced_crop_agnostic',
            'n_scenes':           n,
            'is_active_cycle':    is_active,
            'active_note':        active_note,
            'peak_cvi':           round(peak_cvi,  3),
            'mean_cvi':           round(mean_cvi,  3),
            'peak_ndvi':          round(float(np.max(ndvi)), 3),
            'mean_ndvi':          round(float(np.mean(ndvi)), 3),
            'cv_cvi':             round(float(np.std(eval_cvi)) / max(float(np.mean(eval_cvi)), 0.01), 4),
            'arc_score':          round(arc_score,        1),
            'biomass_score':      round(biomass_score,    1),
            'stability_score':    round(stability_score,  1),
            'momentum_score':     round(momentum_score,   1),
            'canopy_score':       round(canopy_score,     1),
            'active_canopy_frac': round(active_canopy_frac, 3),
            'n_anomalies':        len(anomaly_events),
            'n_high_impact':      sum(1 for a in anomaly_events if a.get('impact') == 'HIGH'),
        }

        return health_score, detail, anomaly_events

    def _score_arc_quality(self, cvi: np.ndarray, is_active: bool) -> float:
        """
        Score the rise â†’ peak â†’ fall trajectory (30 pts).

        For a complete cycle (is_active=False):
          10 pts â€” clear rise phase  (first third avg < peak)
          10 pts â€” peak quality      (peak is well-defined and high)
          10 pts â€” gradual decline   (last third < peak phase)

        For active cycles (fall not yet observable):
          15 pts â€” rise strength
          15 pts â€” peak quality achieved so far
        """
        n = len(cvi)
        if n < 3:
            return 15.0
        t = max(1, n // 3)

        early   = float(np.mean(cvi[:t]))
        mid     = float(np.max(cvi[t: 2 * t]) if len(cvi[t: 2 * t]) > 0 else np.max(cvi))
        late    = float(np.mean(cvi[2 * t:])  if len(cvi[2 * t:]) > 0 else cvi[-1])
        overall = float(np.max(cvi))

        if is_active:
            rise_score  = min(15.0, max(0.0, (mid - early) / max(early + 0.01, 0.01) * 15.0 * 2))
            peak_score  = min(15.0, (overall / 0.65) * 15.0)
            return round(rise_score + peak_score, 1)

        # Complete cycle
        rise_score    = min(10.0, max(0.0, (mid - early) / max(overall, 0.01) * 15.0))
        peak_quality  = min(10.0, (overall / 0.70) * 10.0)
        decline_score = 0.0
        if mid > late:
            decline_delta = mid - late
            if decline_delta   > 0.20: decline_score = 10.0
            elif decline_delta > 0.10: decline_score = 7.0
            elif decline_delta > 0.05: decline_score = 4.0
            else:                      decline_score = 2.0

        return round(min(30.0, rise_score + peak_quality + decline_score), 1)

    def _score_growth_momentum(self, cvi: np.ndarray, is_active: bool) -> float:
        """
        Score rate and smoothness of rising phase (15 pts).

        Momentum = max slope in the first half of the series.
        Smoothness = consecutive rises (no reversals) as fraction of rise phase.
        """
        n = len(cvi)
        if n < 3:
            return 7.5

        half = cvi[: max(n // 2, 2)]
        diffs = np.diff(half)

        if len(diffs) == 0:
            return 7.5

        max_slope     = float(np.max(diffs)) if len(diffs) > 0 else 0.0
        positive_frac = float(np.sum(diffs > 0)) / max(len(diffs), 1)

        slope_score   = min(8.0, (max_slope / 0.08) * 8.0)
        smooth_score  = min(7.0, positive_frac * 7.0)

        return round(min(15.0, slope_score + smooth_score), 1)

    def _score_stability(
        self,
        cvi: np.ndarray,
        anomaly_events: List[Dict],
    ) -> float:
        """
        Score stability / low anomaly level (20 pts).

        Penalty per anomaly:
          HIGH impact   â†’ 5.0 pts deducted
          MEDIUM impact â†’ 2.5 pts deducted
          LOW impact    â†’ 1.0 pts deducted
        Starts at 20 pts.
        """
        dh = float(getattr(PipelineConfig, 'PERFORMANCE_STABILITY_DEDUCT_HIGH', 2.0))
        dm = float(getattr(PipelineConfig, 'PERFORMANCE_STABILITY_DEDUCT_MEDIUM', 1.0))
        dl = float(getattr(PipelineConfig, 'PERFORMANCE_STABILITY_DEDUCT_LOW', 0.35))
        score = 20.0
        for a in anomaly_events:
            impact = a.get('impact', 'LOW')
            if impact == 'HIGH':
                score -= dh
            elif impact == 'MEDIUM':
                score -= dm
            else:
                score -= dl
        return round(max(0.0, score), 1)

    # =========================================================================
    # ANOMALY DETECTION  (IQR-based, multi-index)
    # =========================================================================

    def _detect_index_anomalies(
        self,
        cvi:   np.ndarray,
        ndvi:  np.ndarray,
        dates: List[str],
        n:     int,
        lswi:  Optional[np.ndarray] = None,
        stage_baseline: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        Detect stress/anomaly events from the multi-index series using IQR.

        An anomaly is flagged when:
          - A single-scene CVI drops > 1.5Ã— IQR below Q1 (outlier drop)
          - OR 3+ consecutive scenes all below Q1 (sustained depression)

        Each anomaly is tagged with:
          type:        'sudden_drop' | 'sustained_depression' | 'high_volatility'
          stage:       VEGETATIVE / FLOWERING / GRAIN_FILL / RIPENING
          magnitude:   how far below Q1 (CVI units)
          impact:      HIGH / MEDIUM / LOW  based on stage + magnitude
          description: plain English for reports
        """
        anomalies: List[Dict] = []

        if n < 5:
            return anomalies

        q1  = float(np.percentile(cvi, 25))
        q3  = float(np.percentile(cvi, 75))
        iqr = max(q3 - q1, 0.05)    # floor to avoid near-zero IQR
        iqr_f = float(getattr(PipelineConfig, 'PERFORMANCE_ANOMALY_IQR_FACTOR', 2.0))
        sustained_min = int(getattr(PipelineConfig, 'PERFORMANCE_ANOMALY_SUSTAINED_MIN_SCENES', 4))
        cv_med = float(getattr(PipelineConfig, 'PERFORMANCE_VOLATILITY_CV_MEDIUM', 0.48))
        cv_hi = float(getattr(PipelineConfig, 'PERFORMANCE_VOLATILITY_CV_HIGH', 0.62))
        hi_mult = float(getattr(PipelineConfig, 'PERFORMANCE_IMPACT_HIGH_MAG_IQR_MULT', 1.25))

        # --- Stage labelling at scene position ----------------------------
        def _stage(idx: int) -> str:
            frac = idx / max(n - 1, 1)
            if   frac < 0.25: return 'VEGETATIVE'
            elif frac < 0.50: return 'FLOWERING'
            elif frac < 0.75: return 'GRAIN_FILL'
            else:             return 'RIPENING'

        def _impact(stage: str, magnitude: float) -> str:
            if stage in ('FLOWERING', 'GRAIN_FILL') and magnitude > hi_mult * iqr:
                return 'HIGH'
            if magnitude > 0.65 * iqr:
                return 'MEDIUM'
            if magnitude > 0.35 * iqr:
                return 'MEDIUM'
            return 'LOW'

        # --- Sudden drops (outlier below Q1 - k·IQR; k>1.5 reduces false positives)
        lower_fence = q1 - iqr_f * iqr
        for i in range(n):
            if cvi[i] < lower_fence:
                mag   = q1 - cvi[i]
                stage = _stage(i)
                imp   = _impact(stage, mag)
                anomalies.append({
                    'type':        'sudden_drop',
                    'scene_index': i,
                    'date':        dates[i] if i < len(dates) else '',
                    'stage':       stage,
                    'cvi_value':   round(float(cvi[i]), 3),
                    'magnitude':   round(mag, 3),
                    'impact':      imp,
                    'description': (
                        f"Sudden vegetation drop at {dates[i] if i < len(dates) else f'scene {i}'} "
                        f"(CVI={cvi[i]:.3f}, -{mag:.3f} below baseline) during {stage}. "
                        + ("Indicates likely stress event during critical period."
                           if imp == 'HIGH' else
                           "Possible cloud contamination or brief water stress.")
                    ),
                })

        # --- Sustained depression (3+ consecutive scenes below Q1) -------
        below_q1 = cvi < q1
        i = 0
        while i < n:
            if below_q1[i]:
                j = i
                while j < n and below_q1[j]:
                    j += 1
                run_len = j - i
                if run_len >= sustained_min:
                    mid_i  = (i + j) // 2
                    mag    = float(q1 - np.mean(cvi[i:j]))
                    stage  = _stage(mid_i)
                    imp    = _impact(stage, mag)
                    anomalies.append({
                        'type':        'sustained_depression',
                        'scene_index': mid_i,
                        'start_idx':   i,
                        'end_idx':     j - 1,
                        'date':        dates[mid_i] if mid_i < len(dates) else '',
                        'stage':       stage,
                        'duration_scenes': run_len,
                        'cvi_mean':    round(float(np.mean(cvi[i:j])), 3),
                        'magnitude':   round(mag, 3),
                        'impact':      imp,
                        'description': (
                            f"{run_len}-scene sustained low-vegetation period "
                            f"({dates[i] if i < len(dates) else f'idx {i}'} â†’ "
                            f"{dates[j-1] if j-1 < len(dates) else f'idx {j-1}'}) "
                            f"CVI avg={np.mean(cvi[i:j]):.3f} during {stage}. "
                            + ("Sustained stress suggests drought, disease, or waterlogging."
                               if imp == 'HIGH' else
                               "Below-average canopy cover over multiple scenes.")
                        ),
                    })
                i = j
            else:
                i += 1

        # --- High volatility in peak phase --------------------------------
        peak_idx = int(np.argmax(cvi))
        t = max(1, n // 4)
        peak_window = cvi[max(0, peak_idx - t): min(n, peak_idx + t + 1)]
        if len(peak_window) >= 3:
            cv_peak = float(np.std(peak_window)) / max(float(np.mean(peak_window)), 0.01)
            if cv_peak > cv_med:
                stage = _stage(peak_idx)
                anomalies.append({
                    'type':        'high_volatility',
                    'scene_index': peak_idx,
                    'date':        dates[peak_idx] if peak_idx < len(dates) else '',
                    'stage':       stage,
                    'cv_peak':     round(cv_peak, 3),
                    'magnitude':   round(cv_peak, 3),
                    'impact':      'HIGH' if cv_peak > cv_hi else 'MEDIUM',
                    'description': (
                        f"High vegetation volatility (CV={cv_peak:.2f}) around peak "
                        f"during {stage}. Erratic indices suggest repeated stress "
                        f"events (hail, flooding, disease) near canopy maximum."
                    ),
                })

        # --- Pillar 3: departure from the parcel's OWN per-stage baseline ----
        # Stage-weighted z-score stress vs the parcel's 3-yr normal (not just
        # within-cycle IQR). Flowering/grain-fill departures are HIGH impact.
        if stage_baseline:
            z_med = float(getattr(PipelineConfig, 'STRESS_BASELINE_Z_MEDIUM', 1.5))
            z_hi = float(getattr(PipelineConfig, 'STRESS_BASELINE_Z_HIGH', 2.5))
            already = {a.get('scene_index') for a in anomalies}
            for i in range(n):
                stage = _stage(i)
                base = (stage_baseline.get(stage) or {}).get('cvi')
                if not base:
                    continue
                z = (float(cvi[i]) - base['mean']) / max(base['std'], 1e-3)
                if z <= -z_med and i not in already:
                    critical = stage in ('FLOWERING', 'GRAIN_FILL')
                    imp = 'HIGH' if (z <= -z_hi and critical) else ('MEDIUM' if z <= -z_hi else 'LOW' if not critical else 'MEDIUM')
                    anomalies.append({
                        'type':        'baseline_departure',
                        'scene_index': i,
                        'date':        dates[i] if i < len(dates) else '',
                        'stage':       stage,
                        'z_score':     round(float(z), 2),
                        'cvi_value':   round(float(cvi[i]), 3),
                        'baseline_mean': round(base['mean'], 3),
                        'magnitude':   round(base['mean'] - float(cvi[i]), 3),
                        'impact':      imp,
                        'description': (
                            f"Vegetation {abs(z):.1f}σ below this parcel's own {stage} "
                            f"norm (CVI={cvi[i]:.3f} vs baseline {base['mean']:.3f}). "
                            + ("Stress during a yield-critical stage."
                               if critical else "Below the parcel's typical level.")
                        ),
                    })

        # --- Pillar 3: water stress from LSWI drop vs parcel stage baseline ---
        if lswi is not None and stage_baseline is not None and len(lswi) == n:
            for i in range(n):
                if not np.isfinite(lswi[i]):
                    continue
                stage = _stage(i)
                base = (stage_baseline.get(stage) or {}).get('lswi')
                if not base:
                    continue
                z = (float(lswi[i]) - base['mean']) / max(base['std'], 1e-3)
                wz = float(getattr(PipelineConfig, 'WATER_STRESS_Z', 1.8))
                if z <= -wz:
                    critical = stage in ('FLOWERING', 'GRAIN_FILL')
                    anomalies.append({
                        'type':        'water_stress',
                        'scene_index': i,
                        'date':        dates[i] if i < len(dates) else '',
                        'stage':       stage,
                        'z_score':     round(float(z), 2),
                        'lswi_value':  round(float(lswi[i]), 3),
                        'baseline_mean': round(base['mean'], 3),
                        'magnitude':   round(base['mean'] - float(lswi[i]), 3),
                        'impact':      'HIGH' if (z <= -2.5 and critical) else 'MEDIUM',
                        'description': (
                            f"Canopy water (LSWI) {abs(z):.1f}σ below the parcel's {stage} "
                            f"norm — likely water stress during {stage}."
                        ),
                    })

        # Deduplicate: remove sudden_drops already covered by sustained blocks
        sustained_ranges = set()
        for a in anomalies:
            if a['type'] == 'sustained_depression':
                for k in range(a['start_idx'], a['end_idx'] + 1):
                    sustained_ranges.add(k)

        anomalies = [
            a for a in anomalies
            if not (a['type'] == 'sudden_drop'
                    and a['scene_index'] in sustained_ranges)
        ]

        return anomalies

    # =========================================================================
    # PATH B â€” ENHANCED YIELD POTENTIAL  (0â€“100 %)
    # =========================================================================

    def _enhanced_yield_potential(
        self,
        scenes:        List[Dict],
        duration_days: int,
        is_active:     bool,
        anomaly_events: List[Dict],
    ) -> Tuple[float, Dict]:
        """
        Estimate yield potential % from CVI accumulation and anomaly penalties.

        Components:
          35 â€” Biomass accumulation ratio  (CVI AUC vs adaptive baseline)
          30 â€” Anomaly-free fraction       (fraction of scenes without anomalies)
          20 â€” Peak biomass quality        (CVI peak vs reference)
          15 â€” Peak-phase consistency      (low noise near peak)

        Active cycles: AUC score capped at 85 (incomplete accumulation);
        a projection note is added.
        """
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        cvi_series, ndvi_series, dates = self._extract_multi_index(sorted_scenes)
        n = len(cvi_series)

        if n < 3:
            return 50.0, {'note': 'Too few scenes', 'estimation_method': 'enhanced_fallback'}

        cvi  = np.array(cvi_series, dtype=float)
        ndvi = np.array(ndvi_series, dtype=float)
        nirv_series, _ = self._extract_nirv_lswi(sorted_scenes)
        nirv = np.array(nirv_series, dtype=float)

        # Adaptive CVI baseline based on observed peak
        peak_cvi = float(np.max(cvi))
        if   peak_cvi >= 0.65: baseline_avg = 0.45   # high-canopy
        elif peak_cvi >= 0.40: baseline_avg = 0.30   # medium-canopy
        else:                  baseline_avg = 0.20   # low-canopy / sparse

        # â”€â”€ 1. Biomass accumulation (35 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        observed_auc       = float(np.sum(cvi))
        adaptive_baseline  = baseline_avg * n
        auc_ratio          = observed_auc / max(adaptive_baseline, 0.1)

        if auc_ratio   >= 1.00: auc_score = 35.0
        elif auc_ratio >= 0.85: auc_score = 26.0 + (auc_ratio - 0.85) / 0.15 * 9.0
        elif auc_ratio >= 0.70: auc_score = 17.0 + (auc_ratio - 0.70) / 0.15 * 9.0
        elif auc_ratio >= 0.50: auc_score =  6.0 + (auc_ratio - 0.50) / 0.20 * 11.0
        else:                   auc_score = (auc_ratio / 0.50) * 6.0

        auc_score = min(35.0, auc_score)
        if is_active:
            auc_score = min(auc_score, 29.75)   # cap = 85% of 35 pts for partial cycles

        # â”€â”€ 2. Anomaly-free fraction (30 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        anomalous_indices = set()
        for a in anomaly_events:
            if a['type'] == 'sustained_depression':
                for k in range(a.get('start_idx', a['scene_index']),
                               a.get('end_idx',   a['scene_index']) + 1):
                    anomalous_indices.add(k)
            else:
                # Weight high-impact anomalies as if they affect Â±1 scene
                idx = a['scene_index']
                anomalous_indices.update([max(0, idx - 1), idx, min(n - 1, idx + 1)]
                                         if a.get('impact') == 'HIGH'
                                         else [idx])

        clean_frac  = 1.0 - (len(anomalous_indices) / max(n, 1))
        anomaly_score = min(30.0, max(0.0, clean_frac * 30.0))

        # â”€â”€ 3. Peak biomass quality (20 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Reference: CVI 0.75 â†’ 20 pts  (high-biomass wheat/rice equivalent)
        peak_score = min(20.0, (peak_cvi / 0.75) * 20.0)

        # â”€â”€ 4. Peak-phase consistency (15 pts) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        peak_idx    = int(np.argmax(cvi))
        t           = max(1, n // 4)
        peak_window = cvi[max(0, peak_idx - t): min(n, peak_idx + t + 1)]
        if len(peak_window) >= 2:
            cv_peak     = float(np.std(peak_window)) / max(float(np.mean(peak_window)), 0.01)
            consist_score = min(15.0, max(0.0, (1.0 - cv_peak / 0.60) * 15.0))
        else:
            consist_score = 7.5

        internal_score = round(min(100.0, max(0.0,
            auc_score + anomaly_score + peak_score + consist_score
        )), 1)

        # ── Pillar 3: NIRv-AUC yield-potential metric (decoupled from the
        # detection composite) + peer-relative percentile. The NIRv metric is
        # always computed & stored so cohort_stats can accumulate; the peer
        # percentile becomes the score once the cohort has enough samples,
        # otherwise we fall back to the internal self-calibrated score.
        nirv_finite = nirv[np.isfinite(nirv)]
        nirv_auc = float(np.sum(nirv_finite)) if nirv_finite.size else None
        nirv_auc_mean = (float(np.mean(nirv_finite))
                         if nirv_finite.size >= max(3, n // 2) else None)

        peer_pct, peer_meta = None, {"cohort_source": "unavailable"}
        if self._peer is not None and nirv_auc_mean is not None and not is_active:
            try:
                peer_pct, peer_meta = self._peer.percentile(
                    "nirv_auc_mean", nirv_auc_mean, self._cohort_key
                )
            except Exception:
                peer_pct, peer_meta = None, {"cohort_source": "error"}

        if peer_pct is not None:
            total_yield = round(float(peer_pct), 1)
            yield_index_basis = "peer_nirv"
        else:
            total_yield = internal_score
            yield_index_basis = "internal_cvi_auc"

        active_suffix = ' [projection â€” cycle still active]' if is_active else ''
        detail = {
            'estimation_method':   f'enhanced_crop_agnostic{active_suffix}',
            'n_scenes':            n,
            'is_active_cycle':     is_active,
            'yield_potential_pct': f"{total_yield:.0f}%",
            'yield_index_basis':   yield_index_basis,          # peer_nirv | internal_cvi_auc
            'internal_cvi_auc_score': internal_score,
            'nirv_auc':            (None if nirv_auc is None else round(nirv_auc, 4)),
            'nirv_auc_mean':       (None if nirv_auc_mean is None else round(nirv_auc_mean, 4)),
            'peer_percentile':     peer_pct,
            'peer_meta':           peer_meta,
            'quantified_yield':    None,   # reserved: crop coefficient x potential (later)
            'observed_cvi_auc':    round(observed_auc, 3),
            'adaptive_baseline':   round(adaptive_baseline, 3),
            'auc_ratio':           round(auc_ratio, 3),
            'peak_cvi':            round(peak_cvi, 3),
            'peak_ndvi':           round(float(np.max(ndvi)), 3),
            'clean_fraction':      round(clean_frac, 3),
            'n_anomalous_scenes':  len(anomalous_indices),
            'auc_score':           round(auc_score,      1),
            'anomaly_score':       round(anomaly_score,  1),
            'peak_score':          round(peak_score,     1),
            'consist_score':       round(consist_score,  1),
        }

        return total_yield, detail

    # =========================================================================
    # PERFORMANCE NARRATIVE  (plain English for AI / lender reports)
    # =========================================================================

    @staticmethod
    def _build_narrative(
        crop:           str,
        method:         str,
        health_score:   float,
        yield_score:    float,
        anomaly_events: List[Dict],
        is_active:      bool,
    ) -> str:
        """
        Builds a 2â€“4 sentence plain-English performance summary.
        """
        # Treat 'Unknown' and 'Unclassified' (classification disabled) identically
        _unlabelled = ('Unknown', 'Unclassified', None, '')
        crop_label = crop if crop and crop not in _unlabelled else 'the crop'

        # Overall condition description
        if   health_score >= 80: condition = "excellent"
        elif health_score >= 65: condition = "good"
        elif health_score >= 50: condition = "moderate"
        elif health_score >= 35: condition = "below average"
        else:                    condition = "poor"

        if   yield_score >= 85: yield_desc = "very high yield potential (>85%)"
        elif yield_score >= 70: yield_desc = f"good yield potential ({yield_score:.0f}%)"
        elif yield_score >= 55: yield_desc = f"moderate yield potential ({yield_score:.0f}%)"
        elif yield_score >= 40: yield_desc = f"reduced yield potential ({yield_score:.0f}%)"
        else:                   yield_desc = f"low yield potential ({yield_score:.0f}%)"

        active_clause = " (cycle currently active â€” projection based on observed data so far)" if is_active else ""

        sentences = [
            f"Overall condition of {crop_label}: {condition} "
            f"(Health={health_score:.0f}/100, {yield_desc}){active_clause}."
        ]

        # Anomaly summary
        high_impact = [a for a in anomaly_events if a.get('impact') == 'HIGH']
        medium_impact = [a for a in anomaly_events if a.get('impact') == 'MEDIUM']

        if high_impact:
            stages = list(dict.fromkeys(a['stage'] for a in high_impact))
            sentences.append(
                f"{len(high_impact)} high-impact stress event(s) detected during "
                f"{', '.join(stages)} â€” these significantly affect yield and credit risk."
            )
        if medium_impact and not high_impact:
            sentences.append(
                f"{len(medium_impact)} moderate stress event(s) observed "
                f"with limited yield impact."
            )
        if not anomaly_events:
            sentences.append(
                "No significant anomalies detected â€” crop trajectory is smooth and consistent."
            )

        # Method note
        if method == 'crop_specific':
            sentences.append(
                f"Scoring based on crop-specific benchmark curves for {crop_label}."
            )
        elif method == 'enhanced':
            sentences.append(
                "Scoring based on multi-index trajectory analysis "
                "(crop-agnostic; applicable for any cultivated crop)."
            )

        return " ".join(sentences)

    # =========================================================================
    # PATH A â€” HEALTH SCORE  (crop-specific, 0â€“100)
    # =========================================================================

    def _calculate_health_score(
        self,
        scenes: List[Dict],
        crop:   str,
    ) -> Tuple[float, Dict]:
        """
        Health score breakdown (100 pts total):
          35 â€” Peak NDVI vs crop-specific expected peak
          25 â€” Average NDVI vs crop-specific expected average
          20 â€” Growth curve fit (actual shape vs expected NDVI curve)
          10 â€” Temporal consistency (low noise)
          10 â€” Senescence timing (decline in correct phase)
        """
        ndvi_values = self._chronological_ndvi(scenes)
        if not ndvi_values:
            return 50.0, self._empty_health_detail(crop)

        n = len(ndvi_values)

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

        peak_tol   = 1.10 if canopy_type == 'LOW' else 1.00
        peak_ratio = observed_peak / (expected_peak * peak_tol)
        peak_score = min(35.0, peak_ratio * 35.0)

        avg_ratio  = observed_avg / max(expected_avg, 0.01)
        avg_score  = min(25.0, avg_ratio * 25.0)

        curve_fit_score = self._score_curve_fit(ndvi_values, crop, crop_duration)

        mean_v = max(float(np.mean(ndvi_values)), 0.01)
        cv     = float(np.std(ndvi_values)) / mean_v
        if   0.15 <= cv <= 0.40: consistency_score = 10.0
        elif cv < 0.15:          consistency_score = max(0.0, 10.0 - (0.15 - cv) * 80)
        else:                    consistency_score = max(0.0, 10.0 - (cv - 0.40) * 20)

        senescence_score = self._score_senescence(ndvi_values, canopy_type)

        health_score = round(min(100.0, max(0.0,
            peak_score + avg_score + curve_fit_score
            + consistency_score + senescence_score
        )), 1)

        detail = {
            'scoring_type':       'crop_specific',
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
        if not CROP_PARAMS_AVAILABLE or crop in ('Unknown', None, '') or not ndvi_values:
            return 10.0
        n = len(ndvi_values)
        day_positions = np.linspace(0, crop_duration, n)
        expected_vals = [CropGrowthCurves.get_expected_ndvi(crop, int(d))[0]
                         for d in day_positions]
        rmse = float(np.sqrt(np.mean(
            (np.array(ndvi_values) - np.array(expected_vals)) ** 2
        )))
        return round(max(0.0, 20.0 * (1.0 - rmse / 0.30)), 1)

    def _score_senescence(self, ndvi_values: List[float], canopy_type: str) -> float:
        n = len(ndvi_values)
        if n < 4:
            return 5.0
        q         = max(1, n // 4)
        mid_mean  = float(np.mean(ndvi_values[q: 3 * q]))
        late_mean = float(np.mean(ndvi_values[3 * q:]))

        if canopy_type == 'PERENNIAL':
            return 10.0 if late_mean >= mid_mean * 0.88 \
                else max(0.0, 10.0 - (mid_mean - late_mean) / mid_mean * 30)

        decline = mid_mean - late_mean
        if   decline > 0.10: return 10.0
        elif decline > 0.05: return 7.0
        elif decline > 0.0:  return 4.0
        else:                return 2.0

    # =========================================================================
    # PATH A â€” YIELD POTENTIAL  (crop-specific, 0â€“100)
    # =========================================================================

    def _estimate_yield_potential(
        self,
        scenes: List[Dict],
        crop:   str,
    ) -> Tuple[float, Dict]:
        """
        Yield potential via cumulative NDVI vs crop benchmark.
        Three components: cumulative (60%) + peak (25%) + critical stage (15%).
        """
        ndvi_values = self._chronological_ndvi(scenes)
        if not ndvi_values:
            return 50.0, {
                'cumulative_ndvi': 0.0,
                'note': 'No NDVI data',
                'yield_index_basis': 'crop_curve_ndvi',
                'yield_potential_pct': '50%',
            }

        if CROP_PARAMS_AVAILABLE and crop not in ('Unknown', None, ''):
            expected_cumulative = CropGrowthCurves.get_expected_cumulative_ndvi(crop)
            expected_peak       = CropGrowthCurves.get_peak_ndvi(crop)
            crop_duration       = CropGrowthCurves.get_crop_duration(crop)
            estimation_method   = 'crop_specific'
        else:
            observed_peak_hint  = float(np.max(ndvi_values))
            n_obs               = len(ndvi_values)
            if   observed_peak_hint >= 0.70:
                expected_peak, expected_cumulative, crop_duration = 0.80, max(5.5, n_obs * 0.42), 150
            elif observed_peak_hint >= 0.45:
                expected_peak, expected_cumulative, crop_duration = 0.55, max(3.5, n_obs * 0.30), 100
            else:
                expected_peak, expected_cumulative, crop_duration = 0.40, max(2.0, n_obs * 0.20), 80
            estimation_method = 'generic_biomass'

        cumulative_ndvi = float(np.sum(ndvi_values))
        observed_peak   = float(np.max(ndvi_values))
        cum_ratio       = cumulative_ndvi / max(expected_cumulative, 0.1)

        if   cum_ratio >= 1.00: cum_score = 60.0
        elif cum_ratio >= 0.85: cum_score = 42.0 + (cum_ratio - 0.85) / 0.15 * 18.0
        elif cum_ratio >= 0.70: cum_score = 30.0 + (cum_ratio - 0.70) / 0.15 * 12.0
        elif cum_ratio >= 0.50: cum_score = 12.0 + (cum_ratio - 0.50) / 0.20 * 18.0
        else:                   cum_score = (cum_ratio / 0.50) * 12.0

        peak_ratio = observed_peak / max(expected_peak, 0.01)
        peak_score = min(25.0, peak_ratio * 25.0)

        critical_score = self._score_critical_stages(ndvi_values, crop, crop_duration)

        yield_score = round(min(100.0, max(0.0,
            cum_score + peak_score + critical_score
        )), 1)

        detail = {
            'scoring_type':        'crop_specific',
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
            'estimation_method':   estimation_method,
            'yield_potential_pct': f"{yield_score:.0f}%",
            'yield_index_basis':   'crop_curve_ndvi',  # never a peer percentile
        }

        return yield_score, detail

    def _score_critical_stages(
        self,
        ndvi_values:   List[float],
        crop:          str,
        crop_duration: int,
    ) -> float:
        if not CROP_PARAMS_AVAILABLE or crop in ('Unknown', None, '') or not ndvi_values:
            return 7.5
        n               = len(ndvi_values)
        critical_stages = CropGrowthCurves.CRITICAL_STAGES.get(crop, [])
        curve           = CropGrowthCurves.EXPECTED_NDVI_CURVES.get(crop, [])
        if not critical_stages or not curve:
            return 7.5
        critical_days = [day for day, ndvi, stage in curve
                         if any(cs in stage for cs in critical_stages)]
        if not critical_days:
            return 7.5
        scores = []
        for crit_day in critical_days:
            frac  = crit_day / max(crop_duration, 1)
            idx   = min(max(int(frac * (n - 1)), 0), n - 1)
            exp_ndvi, _ = CropGrowthCurves.get_expected_ndvi(crop, crit_day)
            scores.append(min(1.0, ndvi_values[idx] / max(exp_ndvi, 0.01)))
        return round(float(np.mean(scores)) * 15.0, 1) if scores else 7.5

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _extract_multi_index(
        sorted_scenes: List[Dict],
    ) -> Tuple[List[float], List[float], List[str]]:
        """
        Extract CVI (Composite Vegetation Index), NDVI, and dates from scenes.
        CVI = 0.5Â·NDVI + 0.3Â·EVI + 0.2Â·NDMI  (falls back if EVI/NDMI missing).
        """
        cvi_vals, ndvi_vals, dates = [], [], []
        for s in sorted_scenes:
            idx   = s.get('indices', {})
            ndvi  = float(idx.get('NDVI_mean', idx.get('NDVI', 0.0)))
            evi   = float(idx.get('EVI_mean',  idx.get('EVI',  ndvi)))   # fallback to NDVI
            ndmi  = float(idx.get('NDMI_mean', idx.get('NDMI', ndvi * 0.7)))  # rough proxy
            cvi   = 0.5 * ndvi + 0.3 * evi + 0.2 * ndmi
            cvi_vals.append(max(0.0, cvi))
            ndvi_vals.append(max(0.0, ndvi))
            dates.append(s.get('date', ''))
        return cvi_vals, ndvi_vals, dates

    @staticmethod
    def _extract_nirv_lswi(sorted_scenes: List[Dict]) -> Tuple[List[float], List[float]]:
        """
        Extract NIRv (yield-potential backbone) and LSWI (water) series from
        scenes. Missing values -> NaN so callers can compute finite fractions.
        """
        nirv_vals, lswi_vals = [], []
        for s in sorted_scenes:
            idx = s.get('indices', {}) or {}
            nirv = idx.get('NIRv_mean', idx.get('NIRv'))
            lswi = idx.get('LSWI_mean', idx.get('LSWI'))
            # LSWI falls back to NDMI (same formula) when LSWI absent.
            if lswi is None:
                lswi = idx.get('NDMI_mean', idx.get('NDMI'))
            nirv_vals.append(float(nirv) if nirv is not None else np.nan)
            lswi_vals.append(float(lswi) if lswi is not None else np.nan)
        return nirv_vals, lswi_vals

    @staticmethod
    def _stage_for_fraction(frac: float) -> str:
        if frac < 0.25:
            return 'VEGETATIVE'
        if frac < 0.50:
            return 'FLOWERING'
        if frac < 0.75:
            return 'GRAIN_FILL'
        return 'RIPENING'

    def _build_parcel_stage_baseline(self, season_results: List[Dict]) -> Dict:
        """
        Build the parcel's OWN per-growth-stage baseline distribution of CVI and
        LSWI across ALL its cycles (the 3-yr record). Stress in Stage 6 is then
        measured as a departure from this parcel-specific, stage-specific normal
        (z-score), not only within-cycle IQR. Returns {} when too little data.
        """
        buckets: Dict[str, Dict[str, List[float]]] = {}
        for res in season_results or []:
            scenes = res.get('scenes') or []
            if not scenes:
                continue
            scenes = sorted(scenes, key=lambda s: s.get('date', ''))
            cvi_series, _, _ = self._extract_multi_index(scenes)
            nirv_series, lswi_series = self._extract_nirv_lswi(scenes)
            n = len(cvi_series)
            if n < 3:
                continue
            for i in range(n):
                stage = self._stage_for_fraction(i / max(n - 1, 1))
                b = buckets.setdefault(stage, {'cvi': [], 'lswi': []})
                if np.isfinite(cvi_series[i]):
                    b['cvi'].append(float(cvi_series[i]))
                if i < len(lswi_series) and np.isfinite(lswi_series[i]):
                    b['lswi'].append(float(lswi_series[i]))

        baseline: Dict[str, Dict[str, Dict[str, float]]] = {}
        for stage, chans in buckets.items():
            entry: Dict[str, Dict[str, float]] = {}
            for ch, vals in chans.items():
                if len(vals) >= 4:  # need a few samples for a meaningful mean/std
                    arr = np.asarray(vals, dtype=float)
                    entry[ch] = {
                        'mean': float(np.mean(arr)),
                        'std': float(max(np.std(arr), 1e-3)),
                        'n': int(arr.size),
                    }
            if entry:
                baseline[stage] = entry
        return baseline

    @staticmethod
    def _chronological_ndvi(scenes: List[Dict]) -> List[float]:
        """Return NDVI values sorted chronologically."""
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        return [s.get('indices', {}).get('NDVI_mean',
                s.get('indices', {}).get('NDVI', 0.0))
                for s in sorted_scenes]

    @staticmethod
    def _get_scenes_for_result(result: Dict, seasonal_data: List[Dict]) -> List[Dict]:
        """
        Retrieve the scene list for a crop result.
        Priority:
          1. Inline 'scenes' (cycle-based detection via analyze_cycles())
          2. Cross-season: combine constituent seasons
          3. Normal: look up by (season, year) in seasonal_data
        """
        if 'scenes' in result and result['scenes']:
            return sorted(result['scenes'], key=lambda s: s.get('date', ''))

        if result.get('is_cross_season') and result.get('season_keys'):
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
            return sorted(all_scenes, key=lambda s: s.get('date', ''))

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

    # =========================================================================
    # CYCLE-DETECTOR PROXY  (no scenes — use Stage-4 cycle metrics)
    # =========================================================================

    @staticmethod
    def _cycle_detector_proxy_scores(
        peak_ndvi: float,
        cycle_confidence: float,
        duration_days: int,
        cultivation_signal: float,
        crop: str,
    ) -> Tuple[float, Dict, float, Dict]:
        """
        Health / yield when we have no sub-cycle scene list (classification off or
        scenes not wired), but CropCycleDetector did supply peak NDVI, duration, and
        a detector confidence. Blends with the legacy cultivation_signal curve so
        scores stay comparable year-to-year.
        """
        pq = float(np.clip((peak_ndvi - 0.20) / 0.42, 0.0, 1.0))
        dur = max(0, int(duration_days or 0))
        if dur < 42:
            dq = float(np.clip(dur / 42.0, 0.15, 1.0))
        elif dur <= 185:
            dq = 1.0
        else:
            dq = float(np.clip(1.0 - (dur - 185) / 200.0, 0.55, 1.0))

        conf = float(np.clip(cycle_confidence / 100.0, 0.0, 1.0))
        struct = 38.0 + 34.0 * pq + 16.0 * dq
        struct *= 0.68 + 0.32 * conf

        legacy_h, _ = CropPerformanceAnalyzer._signal_based_health(cultivation_signal, crop)
        legacy_y, _ = CropPerformanceAnalyzer._signal_based_yield(cultivation_signal, crop)

        health = round(float(np.clip(0.58 * struct + 0.42 * legacy_h, 34.0, 94.0)), 1)
        yield_s = round(float(np.clip(0.55 * (struct + 4.0) + 0.45 * legacy_y, 32.0, 93.0)), 1)

        h_detail = {
            'scoring_type':       'signal_only',
            'peak_ndvi':          round(peak_ndvi, 4),
            'cycle_confidence':   round(cycle_confidence, 1),
            'duration_days':      dur,
            'structure_score':      round(struct, 1),
            'legacy_blend':       0.42,
            'n_scenes':           0,
            'note':               'Cycle-detector metrics (peak, duration, confidence) — no per-bin scene trajectory',
        }
        y_detail = {
            'scoring_type':        'signal_only',
            'yield_potential_pct': f"{yield_s:.0f}%",
            'yield_index_basis':   'signal_proxy',  # never a peer percentile
            'peak_ndvi':           round(peak_ndvi, 4),
            'duration_days':       dur,
            'structure_score':     round(struct + 4.0, 1),
            'legacy_blend':        0.45,
            'n_scenes':            0,
            'estimation_method':   'cycle_proxy',
            'note':                'Yield potential from cycle length + peak vigor (scenes not attached)',
        }
        return health, h_detail, yield_s, y_detail

    # =========================================================================
    # SIGNAL-BASED FALLBACK  (no scene data at all)
    # =========================================================================

    @staticmethod
    def _signal_based_health(
        cultivation_signal: float,
        crop: str,
    ) -> Tuple[float, Dict]:
        """Derive health estimate from cultivation_signal when no scene data available."""
        health = round(min(100.0, max(0.0, cultivation_signal * 0.85 + 5.0)), 1)
        detail = {
            'scoring_type':       'signal_only',
            'expected_peak':      'N/A',
            'observed_peak':      'N/A',
            'canopy_type':        'UNKNOWN',
            'arc_score':          round(cultivation_signal * 0.30, 1),
            'biomass_score':      round(cultivation_signal * 0.25, 1),
            'stability_score':    round(cultivation_signal * 0.20, 1),
            'momentum_score':     round(cultivation_signal * 0.15, 1),
            'canopy_score':       round(cultivation_signal * 0.10, 1),
            'n_scenes':           0,
            'note':               'Signal-based only (no scene data)',
        }
        return health, detail

    @staticmethod
    def _signal_based_yield(
        cultivation_signal: float,
        crop: str,
    ) -> Tuple[float, Dict]:
        """Derive yield-potential estimate from cultivation_signal."""
        yield_score = round(min(100.0, max(0.0, cultivation_signal * 0.80 + 10.0)), 1)
        detail = {
            'scoring_type':        'signal_only',
            'yield_potential_pct': f"{yield_score:.0f}%",
            'yield_index_basis':   'signal_proxy',  # never a peer percentile
            'auc_score':           round(cultivation_signal * 0.35, 1),
            'anomaly_score':       round(cultivation_signal * 0.30, 1),
            'peak_score':          round(cultivation_signal * 0.20, 1),
            'consist_score':       round(cultivation_signal * 0.15, 1),
            'n_scenes':            0,
            'estimation_method':   'signal_based',
            'note':                'Yield estimated from cultivation signal (no scene data)',
        }
        return yield_score, detail


