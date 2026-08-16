"""
Crop Detector and Classifier — VERSION 4.1
============================================
Design philosophy (v4.x):
  PRIMARY GOAL: Detect CULTIVATION ACTIVITY and its INTENSITY.
  SECONDARY GOAL: Identify the crop type (best-effort; never blocks the pipeline).

Crop classification is now "enrichment-only":
  - If the ML model predicts a crop with high confidence → great, use it.
  - If classification fails, is low-confidence, or returns 'Unknown' →
    all downstream steps (performance analysis, credit scoring) continue
    using signal-based metrics instead of crop-specific benchmarks.
  - The cultivation_signal (0–100) measures farming intensity purely from
    NDVI shape: peak, area under curve, temporal variation, arc quality.
    This is the primary driver for credit scoring in v4.0.

Key changes from v3.0:
  1. analyze_cycles() decoupled from fixed kharif/rabi season keys.
     Cropping intensity computed from actual cycle date spans.
  2. cultivation_signal added to every season_result.
  3. ML failures handled gracefully — cycle is NOT dropped.
  4. dominant_crop and crops_detected populated where possible,
     left empty/Unknown when not — without affecting credit score.

v4.1 (Stage 5 ↔ Stage 4):
  - Temporal gates use cycle_based=True (cycles already validated by CropCycleDetector).
  - season_results carry peak_date, harvest_start_date, harvest_end_date, season_label.
  - Scene slicing uses optional padding around sowing→harvest for ML context only;
    reported start_date / end_date remain the Stage-4 sowing and final harvest dates.
"""

import numpy as np
import joblib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging


from config import PipelineConfig

from utils.india_geo_context import detector_ndvi_threshold, infer_agro_ecoregion

logger = logging.getLogger(__name__)


class CropDetector:
    """
    Detects crop presence via temporal NDVI pattern analysis and
    classifies crop type using a pre-trained ML model with
    chronologically ordered features.

    ``analyze_cycles`` is the primary path with Stage-4 ``CropCycle`` intervals.
    """

    def __init__(
        self,
        crop_model_path: str,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        verbose: bool = True,
        state_lgd_code: Optional[str] = None,
        district_lgd_code: Optional[str] = None,
    ):
        self.verbose   = verbose
        self.latitude  = latitude
        self.longitude = longitude

        # Load ML model
        model_data          = joblib.load(crop_model_path)
        self.model          = model_data['model']
        self.label_encoder  = model_data['label_encoder']
        self.feature_names  = model_data['feature_names']
        self.crop_names     = model_data['crop_names']

        base_ndvi_thr = PipelineConfig.CROP_DETECTION_NDVI_THRESHOLD
        self.agro_ecoregion, self.agro_geo_profile = infer_agro_ecoregion(
            latitude,
            longitude,
            state_lgd_code=state_lgd_code,
        )
        self.ndvi_threshold = detector_ndvi_threshold(base_ndvi_thr, latitude, longitude)
        self.regional_thresholds = self.agro_geo_profile.copy()
        if district_lgd_code:
            self.regional_thresholds["district_lgd_code"] = district_lgd_code

        self.region = self.agro_ecoregion

        logger.info(
            "✔ CropDetector v4.1 initialized (Agro-region: %s | NDVI thr: %.2f)",
            self.region,
            self.ndvi_threshold,
        )
        logger.info(
            f"  ML feature scenes: {PipelineConfig.ML_FEATURE_SCENES}  |  "
            f"Crops: {len(self.crop_names)}",
        )

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def analyze_cropping_pattern(
        self,
        seasonal_data: List[Dict],
        merged_seasons: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Analyse cropping patterns across all season windows.

        If merged_seasons (cross-season resolved list) is available, use that.
        Otherwise fall back to raw seasonal_data.

        Args:
            seasonal_data:  raw per-season dicts from SatelliteDataCollector
            merged_seasons: cross-season resolved dicts (preferred)

        Returns:
            Dict with season_results, crops_detected, dominant_crop,
            cropping_intensity, seasons_with_crops, etc.
        """
        logger.info(f"\n{'='*70}")
        logger.info("ANALYZING CROPPING PATTERNS")
        logger.info(f"  Region: {self.region}  |  NDVI threshold: {self.ndvi_threshold:.2f}")
        logger.info(f"{'='*70}")

        # Use merged seasons if available (handles cross-season crops)
        analysis_units = merged_seasons if merged_seasons else seasonal_data

        season_results   = []
        crops_detected   = defaultdict(int)
        total_units      = len(analysis_units)
        units_with_crops = 0

        for unit in analysis_units:
            scenes = unit.get('scenes', [])
            label  = (f"{unit.get('season','?').upper()} "
                      f"{unit.get('year','?')}"
                      + (" [CROSS-SEASON]" if unit.get('is_cross_season') else ""))

            # Need minimum scenes
            if len(scenes) < PipelineConfig.MIN_OBSERVATIONS_PER_SEASON:
                season_results.append({
                    **self._base_result(unit),
                    'crop_detected': False,
                    'reason': f'Insufficient data ({len(scenes)} scenes)',
                })
                logger.info(f"  {label}: ⚠ Insufficient data ({len(scenes)} scenes)")
                continue

            # ── Temporal pattern detection ─────────────────────────────────
            detected, pattern_info = self._detect_crop_temporal(scenes)

            if detected:
                units_with_crops += 1
                try:
                    crop_pred = self._classify_crop_chronological(scenes)
                    crops_detected[crop_pred['crop']] += 1

                    result = {
                        **self._base_result(unit),
                        'crop_detected':    True,
                        'predicted_crop':   crop_pred['crop'],
                        'confidence':       crop_pred['confidence'],
                        'all_probabilities': crop_pred['all_probabilities'],
                        **pattern_info,
                    }
                    season_results.append(result)

                    logger.info(
                        f"  {label}: ✔ {crop_pred['crop']} "
                        f"({crop_pred['confidence']:.1%})  "
                        f"peak_ndvi={pattern_info['peak_ndvi']:.3f}  "
                        f"rise={pattern_info['ndvi_rise']:.3f}  "
                        f"cv={pattern_info['ndvi_cv']:.3f}"
                    )

                except Exception as e:
                    season_results.append({
                        **self._base_result(unit),
                        'crop_detected':  True,
                        'predicted_crop': 'Unknown',
                        **pattern_info,
                        'classification_error': str(e)[:100],
                    })
                    logger.warning(f"  {label}: ⚠ Classification failed: {str(e)[:60]}")

            else:
                season_results.append({
                    **self._base_result(unit),
                    'crop_detected': False,
                    **pattern_info,
                    'reason': pattern_info.get('rejection_reason', 'No crop pattern'),
                })
                logger.info(
                    f"  {label}: [FAIL] No crop  "
                    f"[peak={pattern_info['peak_ndvi']:.3f}  "
                    f"rise={pattern_info['ndvi_rise']:.3f}  "
                    f"cv={pattern_info['ndvi_cv']:.3f}]  "
                    f"→ {pattern_info.get('rejection_reason','')}"
                )

        # ── Cropping intensity ─────────────────────────────────────────────
        cropping_intensity = self._calculate_cropping_intensity(
            season_results, analysis_units
        )
        dominant_crop = (
            max(crops_detected.items(), key=lambda x: x[1])[0]
            if crops_detected else None
        )

        logger.info(f"\n📊 Summary:")
        logger.info(f"  Season units with crops: {units_with_crops}/{total_units}")
        logger.info(f"  Cropping intensity:      {cropping_intensity:.3f}")
        logger.info(f"  Dominant crop:           {dominant_crop or 'None'}")
        if crops_detected:
            for crop, cnt in sorted(crops_detected.items(), key=lambda x: -x[1]):
                logger.info(f"    {crop}: {cnt} season(s)")

        return {
            'season_results':        season_results,
            'crops_detected':        dict(crops_detected),
            'dominant_crop':         dominant_crop,
            'cropping_intensity':    cropping_intensity,
            'seasons_with_crops':    units_with_crops,
            'total_seasons_analyzed': total_units,
            'region':                self.region,
            'ndvi_threshold_used':   self.ndvi_threshold,
        }

    @staticmethod
    def _norm_cycle_date(value) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value[:10] if len(value) >= 10 else value
        if hasattr(value, 'strftime'):
            return value.strftime('%Y-%m-%d')
        return None

    @staticmethod
    def _cycle_scene_date_bounds(start_str: str, end_str: str) -> Tuple[str, str]:
        """Widen [sowing, harvest] slightly for gathering scenes (ML / temporal only)."""
        pad = int(
            getattr(PipelineConfig, 'CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS', 0) or 0
        )
        if pad <= 0:
            return start_str[:10], end_str[:10]
        s = datetime.strptime(start_str[:10], '%Y-%m-%d')
        e = datetime.strptime(end_str[:10], '%Y-%m-%d')
        d = timedelta(days=pad)
        return (s - d).strftime('%Y-%m-%d'), (e + d).strftime('%Y-%m-%d')

    @staticmethod
    def _collect_scenes_between(
        all_continuous_scenes: List[Dict],
        lo: str,
        hi: str,
    ) -> List[Dict]:
        rows: List[Dict] = []
        for s in all_continuous_scenes:
            if s.get('missing'):
                continue
            raw = s.get('date', '') or ''
            ds = raw[:10] if isinstance(raw, str) else str(raw)[:10]
            if len(ds) < 10:
                continue
            if lo <= ds <= hi:
                rows.append(s)
        return sorted(rows, key=lambda x: (x.get('date') or '')[:10])

    def _stage4_metadata(self, cycle, cycle_index: int) -> Dict:
        """Mirror Stage-4 cycle fields into season_results (dict or CropCycle object)."""
        meta: Dict = {
            'cycle_index':          cycle_index,
            'season_label':         '',
            # kharif / rabi / zaid, carried from the detected cycle. The
            # 'season' field on a season_results row is the positional cycle id
            # ('cycle_1', ...) and must never be treated as a season name.
            'season_type':          None,
            'peak_date':            None,
            'harvest_start_date':   None,
            'harvest_end_date':     None,
            'baseline_ndvi':        None,
            'peak_cvi':             None,
            'integral_ndvi_days':   None,
            'has_cloud_gap':        None,
            'cloud_gap_days':       None,
            'crop_type_hint':       None,
        }
        if isinstance(cycle, dict):
            meta['season_label'] = cycle.get('season_label') or ''
            meta['season_type'] = cycle.get('season_type')
            meta['peak_date'] = self._norm_cycle_date(cycle.get('peak_date'))
            meta['harvest_start_date'] = self._norm_cycle_date(
                cycle.get('harvest_start_date')
            )
            meta['harvest_end_date'] = self._norm_cycle_date(
                cycle.get('harvest_end_date')
            )
            for k in (
                'baseline_ndvi', 'peak_cvi', 'integral_ndvi_days',
                'cloud_gap_days', 'has_cloud_gap',
            ):
                if k in cycle:
                    meta[k] = cycle.get(k)
            meta['crop_type_hint'] = cycle.get('crop_type')
        else:
            meta['season_label'] = getattr(cycle, 'season_label', '') or ''
            meta['season_type'] = getattr(cycle, 'season_type', None)
            meta['peak_date'] = self._norm_cycle_date(getattr(cycle, 'peak_date', None))
            meta['harvest_start_date'] = self._norm_cycle_date(
                getattr(cycle, 'harvest_start_date', None)
            )
            meta['harvest_end_date'] = self._norm_cycle_date(
                getattr(cycle, 'harvest_end_date', None)
            )
            meta['baseline_ndvi'] = getattr(cycle, 'baseline_ndvi', None)
            meta['peak_cvi'] = getattr(cycle, 'peak_cvi', None)
            meta['integral_ndvi_days'] = getattr(cycle, 'integral_ndvi_days', None)
            meta['has_cloud_gap'] = getattr(cycle, 'has_cloud_gap', None)
            meta['cloud_gap_days'] = getattr(cycle, 'cloud_gap_days', None)
            meta['crop_type_hint'] = getattr(cycle, 'crop_type', None)
        return meta

    def analyze_cycles(
        self,
        crop_cycles: List[Dict],
        all_continuous_scenes: List[Dict],
    ) -> Dict:
        """
        Cycle-based crop detection — aligned with Stage-4 sowing / harvest intervals.

        1. Uses each cycle's **sowing → final harvest** as the authoritative
           ``start_date`` / ``end_date`` in outputs (weather, performance, credit).
        2. Gathers real (non-placeholder) scenes in that window, optionally
           **padded** by ``CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS`` for ML + temporal
           features only.
        3. Runs relaxed temporal checks (``cycle_based=True``) then best-effort ML.
        4. Copies Stage-4 fields: ``peak_date``, ``harvest_start_date``,
           ``harvest_end_date``, ``season_label``, etc.

        Args:
            crop_cycles: ``CropCycle`` objects or dicts from ``CropCycle.to_dict()``.
            all_continuous_scenes: full continuous grid (may include ``missing`` bins).

        Returns:
            Dict compatible with ``analyze_cropping_pattern()`` output.
        """
        logger.info(f"\n{'='*70}")
        logger.info("CYCLE-BASED CROP DETECTION")
        logger.info(f"  Region: {self.region}  |  NDVI threshold: {self.ndvi_threshold:.2f}")
        logger.info(f"  Cycles to classify: {len(crop_cycles)}")
        logger.info(f"{'='*70}")

        # ── Normalise cycle list (accept dict or object) ────────────────────
        def _get(cyc, key: str):
            """
            Read a field from either:
              - a dict returned by `CropCycle.to_dict()`, or
              - a CropCycle object (sowing_date/harvest_date dataclass fields).
            """
            if isinstance(cyc, dict):
                return cyc.get(key)

            # CropCycle object compatibility: task wiring uses sowing/harvest,
            # while this analyzer expects start_date/end_date for filtering.
            if key == 'start_date':
                sd = getattr(cyc, 'start_date', None) or getattr(cyc, 'sowing_date', None)
                return sd.strftime('%Y-%m-%d') if hasattr(sd, 'strftime') else sd
            if key == 'end_date':
                hd = getattr(cyc, 'end_date', None) or getattr(cyc, 'harvest_date', None)
                return hd.strftime('%Y-%m-%d') if hasattr(hd, 'strftime') else hd

            return getattr(cyc, key, None)

        season_results  = []
        crops_detected  = defaultdict(int)
        units_with_crops = 0

        for i, cycle in enumerate(crop_cycles, 1):
            start_str  = _get(cycle, 'start_date')
            end_str    = _get(cycle, 'end_date')
            peak_ndvi  = float(_get(cycle, 'peak_ndvi') or 0.0)
            dur_days   = int(_get(cycle, 'duration_days') or 0)
            confidence = float(_get(cycle, 'confidence') or 0.0)
            s4         = self._stage4_metadata(cycle, i)
            label      = f"CYCLE {i} [{start_str} → {end_str}]"

            if not start_str or not end_str:
                continue

            lo, hi = self._cycle_scene_date_bounds(start_str, end_str)
            cycle_scenes = self._collect_scenes_between(all_continuous_scenes, lo, hi)

            logger.info(
                f"  {label}: {len(cycle_scenes)} scenes (window {lo}…{hi})  "
                f"peak_ndvi={peak_ndvi:.3f}  dur={dur_days}d"
            )

            if len(cycle_scenes) < PipelineConfig.MIN_OBSERVATIONS_PER_SEASON:
                season_results.append({
                    'season': f'cycle_{i}', 'year': int(start_str[:4]),
                    'start_date': start_str, 'end_date': end_str,
                    'crop_detected': False, 'predicted_crop': None,
                    'n_scenes': len(cycle_scenes),
                    'peak_ndvi': peak_ndvi, 'cycle_confidence': confidence,
                    'reason': f'Too few scenes ({len(cycle_scenes)})',
                    **{k: v for k, v in s4.items() if v is not None and k != 'cycle_index'},
                })
                continue

            # Stage-4 already validated the interval — relax duplicate temporal gates
            crop_present, pattern_info = self._detect_crop_temporal(
                cycle_scenes, cycle_based=True
            )

            if crop_present:
                units_with_crops += 1
                # ── cultivation_signal: crop-name-independent intensity score ──
                cultivation_signal = self._compute_cultivation_signal(
                    cycle_scenes, peak_ndvi, dur_days
                )

                # ── ML classification (best-effort; never blocks the cycle) ──
                predicted_crop = None
                crop_confidence = 0.0
                all_probs = {}
                classification_note = ''

                try:
                    crop_pred = self._classify_crop_chronological(cycle_scenes)
                    predicted_crop  = crop_pred['crop']
                    crop_confidence = crop_pred['confidence']
                    all_probs       = crop_pred['all_probabilities']
                    # Treat low-confidence predictions as Unclassified but keep name
                    if crop_confidence < 0.25 and predicted_crop not in (None, 'Unknown'):
                        classification_note = f'low_confidence ({crop_confidence:.0%})'
                    crops_detected[predicted_crop or 'Unclassified'] += 1
                except Exception as e:
                    predicted_crop  = None
                    classification_note = f'ml_error: {str(e)[:60]}'
                    logger.debug(f"    ML classification skipped: {str(e)[:60]}")

                season_results.append({
                    'season':              f'cycle_{i}',
                    'year':               int(start_str[:4]),
                    'start_date':          start_str,
                    'end_date':            end_str,
                    'crop_detected':       True,
                    # Primary signal (crop-name independent)
                    'cultivation_signal':  cultivation_signal,
                    # Classification (enrichment only)
                    'predicted_crop':      predicted_crop,
                    'crop_confidence':     round(crop_confidence, 3),
                    'classification_note': classification_note,
                    'all_probabilities':   all_probs,
                    # Cycle metadata
                    'cycle_confidence':    confidence,
                    'duration_days':       dur_days,
                    'is_cycle_based':      True,
                    'scenes':              cycle_scenes,
                    **pattern_info,
                    **{k: v for k, v in s4.items() if v is not None and k != 'cycle_index'},
                })
                logger.info(
                    f"    \u2714 Cultivation detected  "
                    f"signal={cultivation_signal:.0f}/100  "
                    f"peak={pattern_info['peak_ndvi']:.3f}  "
                    + (f"crop={predicted_crop} ({crop_confidence:.0%})"
                       if predicted_crop else "crop=Unclassified (ML skipped)")
                )
            else:
                season_results.append({
                    'season': f'cycle_{i}', 'year': int(start_str[:4]),
                    'start_date': start_str, 'end_date': end_str,
                    'crop_detected': False, 'predicted_crop': None,
                    'cycle_confidence': confidence,
                    **pattern_info,
                    'reason': pattern_info.get('rejection_reason', 'No crop pattern'),
                    **{k: v for k, v in s4.items() if v is not None and k != 'cycle_index'},
                })
                logger.info(f"    [FAIL] No crop [{pattern_info.get('rejection_reason','')}]")

        dominant_crop = (
            max(crops_detected.items(), key=lambda x: x[1])[0]
            if crops_detected else None
        )

        # ── Cropping intensity from cycle DATE SPANS (not fixed kharif/rabi) ──
        # v4.0: intensity = fraction of the total observation period actually
        # under cultivation, averaged as crop-years.
        cropping_intensity = self._calculate_intensity_from_cycles(
            season_results, all_continuous_scenes
        )

        logger.info(f"\n\U0001f4ca Cycle Detection Summary:")
        logger.info(f"  Cycles with crops: {units_with_crops}/{len(crop_cycles)}")
        logger.info(f"  Dominant crop:     {dominant_crop or 'Unclassified'}")
        logger.info(f"  Intensity:         {cropping_intensity:.3f}")
        if crops_detected:
            for crop, cnt in sorted(crops_detected.items(), key=lambda x: -x[1]):
                logger.info(f"    {crop}: {cnt} cycle(s)")

        # Average cultivation signal across cycles with crops
        sig_vals = [
            r.get('cultivation_signal', 0)
            for r in season_results
            if r.get('crop_detected')
        ]
        avg_cultivation_signal = round(float(np.mean(sig_vals)), 1) if sig_vals else 0.0
        logger.info(f"  Avg cultivation signal: {avg_cultivation_signal}/100")

        return {
            'season_results':           season_results,
            'crops_detected':           dict(crops_detected),
            'dominant_crop':            dominant_crop,
            'cropping_intensity':       cropping_intensity,
            # Stage-2 metric (0-100) expected by Stage-6/AI features
            'cultivation_signal':      avg_cultivation_signal,
            'avg_cultivation_signal':   avg_cultivation_signal,
            'seasons_with_crops':       units_with_crops,
            'total_seasons_analyzed':   len(crop_cycles),
            'region':                   self.region,
            'ndvi_threshold_used':      self.ndvi_threshold,
            'detection_mode':           'cycle_based',
        }

    # =========================================================================
    # TEMPORAL PATTERN CROP DETECTION
    # =========================================================================

    def _detect_crop_temporal(
        self,
        scenes: List[Dict],
        cycle_based: bool = False,
    ) -> Tuple[bool, Dict]:
        """
        Detect crop presence using multi-signal temporal pattern analysis.

        v2 KEY CHANGE — Adaptive rise threshold for missing early-season scenes:
          When the NDVI peak occurs in the first 25% of available scenes,
          it means the satellite missed the greenup phase (cloudy early season),
          so we cannot fairly penalise for a low "rise" — instead we rely on:
            • High peak NDVI (clear vegetation signal)
            • Arc score (mid > late confirms senescence)
            • CV (temporal variation consistent with cultivation)

        Args:
            scenes:       List of scene dicts with 'date' and 'indices' keys
            cycle_based:  If True (called from analyze_cycles), relax gates
                          since CropCycleDetector already validated the cycle

        Returns:
            (crop_present: bool, pattern_info: dict)
        """
        if not scenes:
            return False, self._empty_pattern()

        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        ndvi_values   = [s['indices'].get('NDVI_mean', 0.0) for s in sorted_scenes]
        n             = len(ndvi_values)

        # ── Basic stats ────────────────────────────────────────────────────
        peak_ndvi  = float(np.max(ndvi_values))
        avg_ndvi   = float(np.mean(ndvi_values))
        std_ndvi   = float(np.std(ndvi_values))
        ndvi_cv    = std_ndvi / max(avg_ndvi, 0.01)
        peak_scene = int(np.argmax(ndvi_values))

        # ── Adaptive rise calculation ───────────────────────────────────────
        # If peak is in first quarter, early-season data is likely missing
        early_missing = (peak_scene < max(1, n // 4))

        q          = max(1, n // 4)
        early_mean = float(np.mean(ndvi_values[:q]))
        ndvi_rise  = peak_ndvi - early_mean

        frac_above = float(np.mean([v > self.ndvi_threshold for v in ndvi_values]))
        arc_score  = self._compute_arc_score(ndvi_values)

        # ── Decision gates ─────────────────────────────────────────────────
        cfg     = PipelineConfig
        reasons = []

        # Gate 1: peak must clear regional threshold
        if peak_ndvi <= self.ndvi_threshold:
            reasons.append(
                f"peak_ndvi {peak_ndvi:.3f} ≤ threshold {self.ndvi_threshold:.3f}"
            )

        # Gate 2: enough green scenes (relaxed for cycle-based path)
        min_frac = cfg.MIN_FRACTION_ABOVE_THRESHOLD * (0.6 if cycle_based else 1.0)
        if frac_above < min_frac:
            reasons.append(
                f"only {frac_above:.0%} scenes above threshold (need ≥{min_frac:.0%})"
            )

        # Gate 3: NDVI rise — SKIP if early-season scenes are missing
        # (in that case, rely on arc + CV as proxies)
        if early_missing:
            # Use arc + CV as proxy for rise validation
            if arc_score < 0.3 and ndvi_cv < cfg.MIN_NDVI_CV_FOR_CROP:
                reasons.append(
                    f"early scenes missing: arc={arc_score:.2f} cv={ndvi_cv:.3f} "
                    f"— insufficient vegetation signal"
                )
        else:
            if ndvi_rise < cfg.MIN_NDVI_RISE:
                reasons.append(
                    f"ndvi_rise {ndvi_rise:.3f} < min {cfg.MIN_NDVI_RISE:.3f}"
                )

        # Gate 4: temporal variation (skip upper bound for cycle-based path
        # since cycle detector already smoothed out erratic scenes)
        if ndvi_cv < cfg.MIN_NDVI_CV_FOR_CROP:
            reasons.append(
                f"cv {ndvi_cv:.3f} too flat (min {cfg.MIN_NDVI_CV_FOR_CROP:.3f})"
            )
        if not cycle_based and ndvi_cv > cfg.MAX_NDVI_CV_FOR_CROP:
            reasons.append(
                f"cv {ndvi_cv:.3f} too erratic (max {cfg.MAX_NDVI_CV_FOR_CROP:.3f})"
            )

        crop_detected = len(reasons) == 0

        pattern_info = {
            'peak_ndvi':         round(peak_ndvi,   4),
            'avg_ndvi':          round(avg_ndvi,    4),
            'ndvi_std':          round(std_ndvi,    4),
            'ndvi_cv':           round(ndvi_cv,     4),
            'ndvi_rise':         round(ndvi_rise,   4),
            'early_mean_ndvi':   round(early_mean,  4),
            'frac_above_thresh': round(frac_above,  4),
            'arc_score':         round(arc_score,   4),
            'n_scenes':          n,
            'early_season_missing': early_missing,
            'rejection_reason':  '; '.join(reasons) if reasons else '',
        }

        return crop_detected, pattern_info

    @staticmethod
    def _compute_arc_score(ndvi_values: List[float]) -> float:
        """
        Score how well the NDVI time-series follows a crop growth arc
        (rise → peak → decline).  Returns 0.0–1.0.

        Method:
          - Split series into thirds: early / mid / late
          - Ideal: mid > early  AND  mid > late  (bell shape)
          - Score = fraction of ideal conditions met
        """
        n = len(ndvi_values)
        if n < 3:
            return 0.5

        t = max(1, n // 3)
        early = float(np.mean(ndvi_values[:t]))
        mid   = float(np.mean(ndvi_values[t: 2*t]))
        late  = float(np.mean(ndvi_values[2*t:]))

        score = 0.0
        if mid > early:   score += 0.50   # proper growth phase
        if mid > late:    score += 0.50   # proper senescence
        return score

    # =========================================================================
    # ML CROP CLASSIFICATION — CHRONOLOGICAL FEATURES
    # =========================================================================

    def _classify_crop_chronological(self, scenes: List[Dict]) -> Dict:
        """
        Classify crop type using chronologically ordered scenes.

        FIXED v3.1 — Temporal interpolation instead of zero-padding:
          - Scenes mapped onto normalized 0–1 time axis.
          - Feature values at fixed grid positions obtained via linear interpolation.
          - Confidence calibrated using top-2 class probability gap.
        """
        n_feat   = PipelineConfig.ML_FEATURE_SCENES
        indices  = PipelineConfig.ML_FEATURE_INDICES

        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        n = len(sorted_scenes)

        t_obs  = np.linspace(0.0, 1.0, max(n, 1))
        t_grid = np.linspace(0.0, 1.0, n_feat)

        feature_dict = {}
        for idx_name in indices:
            obs_vals = np.array(
                [s.get('indices', {}).get(idx_name, 0.0) for s in sorted_scenes],
                dtype=float,
            )
            nan_mask = np.isnan(obs_vals)
            if nan_mask.any():
                x_valid = t_obs[~nan_mask]
                y_valid = obs_vals[~nan_mask]
                if len(x_valid) >= 2:
                    obs_vals[nan_mask] = np.interp(t_obs[nan_mask], x_valid, y_valid)
                else:
                    obs_vals = np.where(nan_mask, 0.0, obs_vals)

            if n >= 2:
                grid_vals = np.interp(t_grid, t_obs, obs_vals)
            elif n == 1:
                grid_vals = np.full(n_feat, obs_vals[0])
            else:
                grid_vals = np.zeros(n_feat)

            short_name = idx_name.replace('_mean', '')
            for t, val in enumerate(grid_vals):
                feature_dict[f"{short_name}_t{t+1:02d}"] = float(val)

        feature_vector = [feature_dict.get(fn, 0.0) for fn in self.feature_names]
        X = np.nan_to_num(np.array([feature_vector]), nan=0.0)

        prediction    = self.model.predict(X)[0]
        probabilities = self.model.predict_proba(X)[0]
        crop_name     = self.label_encoder.inverse_transform([prediction])[0]

        sorted_probs      = np.sort(probabilities)[::-1]
        raw_confidence    = float(probabilities.max())
        top2_gap          = float(sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) > 1 else raw_confidence
        calibrated_conf   = min(raw_confidence, raw_confidence * min(1.0, top2_gap / 0.10 + 0.5))

        return {
            'crop':              crop_name,
            'confidence':        round(calibrated_conf, 4),
            'raw_confidence':    round(raw_confidence, 4),
            'top2_gap':          round(top2_gap, 4),
            'all_probabilities': {
                c: float(p) for c, p in zip(self.crop_names, probabilities)
            },
            'n_scenes_used':     n,
            'feature_scenes':    n_feat,
            'method':            'temporal_interpolation',
        }


    # =========================================================================
    # CROPPING INTENSITY — Fixed calculation
    # =========================================================================

    def _calculate_cropping_intensity(
        self,
        season_results: List[Dict],
        analysis_units: List[Dict],
    ) -> float:
        """
        Compute cropping intensity as the fraction of available season slots
        that were actually cultivated, averaged across all crop-years.

        Crop-year = one Kharif + one Rabi pair.

        Rules:
          - A cross-season crop (e.g., Sugarcane spanning Kharif+Rabi)
            occupies BOTH slots but counts as 1 crop cultivated event
            → intensity for that year = 1.0 (land was used, just one long crop)
          - A farmer growing both Kharif AND Rabi crops
            → intensity = 1.0 (both slots used, two separate crops)
          - Only Kharif → intensity = 0.5
          - Only Rabi   → intensity = 0.5
          - Neither     → intensity = 0.0

        Return value range: 0.0 – 1.0
        (values > 1.0 only possible if relay/intercropping detected,
         which is beyond current scope)
        """
        if not season_results:
            return 0.0

        # Build a map: (season, year) → crop_detected
        detected_map = {}
        for r in season_results:
            # Handle cross-season results
            if r.get('is_cross_season'):
                for key in r.get('season_keys', []):
                    detected_map[key] = r['crop_detected']
            else:
                key = (r.get('season'), r.get('year'))
                detected_map[key] = r.get('crop_detected', False)

        # Collect all unique years present in analysis_units
        all_years = sorted(set(
            u.get('year') for u in analysis_units if u.get('year') is not None
        ))

        if not all_years:
            return 0.0

        year_intensities = []
        weights = PipelineConfig.INTENSITY_WEIGHTS

        for yr in all_years:
            kharif_grown = detected_map.get(('kharif', yr), False)
            rabi_grown   = detected_map.get(('rabi',   yr), False)

            # Also check if this year's Kharif is part of a cross-season event
            # that continues into the next year's Rabi
            yr_intensity = 0.0
            if kharif_grown:
                yr_intensity += weights['kharif']
            if rabi_grown:
                yr_intensity += weights['rabi']

            year_intensities.append(min(1.0, yr_intensity))

        return round(float(np.mean(year_intensities)), 3)

    # =========================================================================
    # CULTIVATION SIGNAL — Crop-name-independent intensity metric
    # =========================================================================

    @staticmethod
    def _compute_cultivation_signal(
        scenes:     List[Dict],
        peak_ndvi:  float,
        dur_days:   int,
    ) -> float:
        """
        Signal score (0–100) that measures farming intensity purely from NDVI
        shape, without any reference to crop name or species.

        Components:
          30 pts — Peak NDVI achievement   (how healthy is the canopy at max?)
          25 pts — Area under NDVI curve   (proxy for total biomass production)
          25 pts — Temporal dynamics (CV)  (inactive soil vs active crop cycle)
          20 pts — Growth arc quality      (rise → peak → fall shape)

        This score is the PRIMARY driver for credit scoring when crop
        classification is unavailable or unreliable.
        """
        if not scenes:
            return 0.0

        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))
        ndvi = np.array([s.get('indices', {}).get('NDVI_mean', 0.0)
                         for s in sorted_scenes], dtype=float)
        ndvi = np.clip(ndvi, 0.0, 1.0)
        n = len(ndvi)
        if n == 0:
            return 0.0

        # 1. Peak achievement (30 pts): peak NDVI of 0.80 = full 30
        peak_score = min(30.0, (peak_ndvi / 0.80) * 30.0)

        # 2. Area under curve (25 pts): cumulative NDVI / (n scenes * 0.65)
        #    A crop scene average of 0.65 = full 25 pts
        cum_ndvi    = float(np.sum(ndvi))
        area_score  = min(25.0, (cum_ndvi / max(n * 0.65, 0.1)) * 25.0)

        # 3. Temporal dynamics (25 pts): CV 0.20–0.45 is ideal cultivation
        mean_v = max(float(np.mean(ndvi)), 0.01)
        cv     = float(np.std(ndvi)) / mean_v
        if 0.20 <= cv <= 0.45:
            cv_score = 25.0
        elif cv < 0.20:
            cv_score = max(0.0, 25.0 * (cv / 0.20))
        else:
            cv_score = max(0.0, 25.0 * (1.0 - (cv - 0.45) / 0.35))

        # 4. Growth arc quality (20 pts): rise → peak → fall
        t = max(1, n // 3)
        early = float(np.mean(ndvi[:t]))
        mid   = float(np.mean(ndvi[t: 2 * t]))
        late  = float(np.mean(ndvi[2 * t:]))
        arc_score = 0.0
        if mid > early + 0.05:  arc_score += 10.0  # clear growth phase
        if mid > late  + 0.05:  arc_score += 10.0  # clear senescence
        elif mid > late:         arc_score += 5.0   # mild decline

        total = peak_score + area_score + cv_score + arc_score
        return round(float(np.clip(total, 0.0, 100.0)), 1)

    @staticmethod
    def _calculate_intensity_from_cycles(
        season_results: List[Dict],
        all_scenes:     List[Dict],
    ) -> float:
        """
        Compute cropping intensity as cycles per year over the observed window.
        This better reflects multi-cycle farming (e.g., 7 cycles in 3 years)
        and aligns with credit scoring thresholds (0-3+ range).
        """
        if not season_results or not all_scenes:
            return 0.0

        dates = sorted(s.get('date', '') for s in all_scenes if s.get('date'))
        if len(dates) < 2:
            return 0.0

        try:
            obs_start = datetime.strptime(dates[0],  '%Y-%m-%d')
            obs_end   = datetime.strptime(dates[-1], '%Y-%m-%d')
            total_days = max(1, (obs_end - obs_start).days)
        except Exception:
            return 0.0

        cycles_detected = 0
        for r in season_results:
            if r.get('crop_detected'):
                cycles_detected += 1

        years_observed = max(total_days / 365.25, 0.5)
        intensity = cycles_detected / years_observed
        return round(min(3.5, float(intensity)), 3)

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _base_result(unit: Dict) -> Dict:
        """Extract common fields from an analysis unit dict."""
        return {
            'season':          unit.get('season'),
            'year':            unit.get('year'),
            'start_date':      unit.get('start_date'),
            'end_date':        unit.get('end_date'),
            'is_cross_season': unit.get('is_cross_season', False),
            'cross_season_type': unit.get('cross_season_type', 'normal'),
            'season_keys':     unit.get('season_keys', []),
            'n_scenes':        len(unit.get('scenes', [])),
        }

    @staticmethod
    def _empty_pattern() -> Dict:
        return {
            'peak_ndvi': 0.0, 'avg_ndvi': 0.0, 'ndvi_std': 0.0,
            'ndvi_cv': 0.0, 'ndvi_rise': 0.0, 'early_mean_ndvi': 0.0,
            'frac_above_thresh': 0.0, 'arc_score': 0.0,
            'n_scenes': 0, 'rejection_reason': 'No scenes',
        }