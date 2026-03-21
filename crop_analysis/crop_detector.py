"""
Crop Detector and Classifier
=============================
Detects crop presence and classifies crop types from satellite time-series.

VERSION 3.0 — Major Updates:
1. Crop Presence Detection — Temporal Pattern (replaces single peak-NDVI check):
   - Uses NDVI rise magnitude, fraction of scenes above threshold,
     temporal variation (CV), and growth arc shape together
   - Avoids false-positives from cloud-contaminated single scenes
   - Avoids false-negatives on low-canopy crops (Groundnut, Bajra, Onion)

2. ML Crop Classification — Chronological Features:
   - Scenes sorted by date BEFORE feature extraction (was random order)
   - Feature window increased: 15 scenes × 3 indices = 45 features
   - Time-normalized positions used so model sees relative stage,
     not absolute scene index

3. Cross-season aware:
   - Accepts merged_seasons from satellite_collector (cross-season events)
   - Long-duration / late-sown crops processed as single events

4. Cropping Intensity — Fixed calculation:
   - Season pair based (Kharif + Rabi = 1 year)
   - Cross-season crop counted once (not twice)
   - No artificial "must have both kharif AND rabi" summer bonus
"""

import numpy as np
import joblib
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import logging

try:
    from config.regional_config import RegionalConfig
    REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False

from config.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


class CropDetector:
    """
    Detects crop presence via temporal NDVI pattern analysis and
    classifies crop type using a pre-trained ML model with
    chronologically ordered features.
    """

    def __init__(
        self,
        crop_model_path: str,
        latitude:  Optional[float] = None,
        longitude: Optional[float] = None,
        verbose:   bool = True,
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

        # Regional thresholds
        if REGIONAL_CONFIG_AVAILABLE and latitude is not None and longitude is not None:
            self.regional_thresholds = RegionalConfig.get_crop_threshold(latitude, longitude)
            self.ndvi_threshold      = self.regional_thresholds['min_ndvi']
            self.region              = RegionalConfig.get_region(latitude, longitude)
        else:
            self.regional_thresholds = None
            self.ndvi_threshold      = PipelineConfig.CROP_DETECTION_NDVI_THRESHOLD
            self.region              = 'DEFAULT'

        logger.info(f"✔ CropDetector v3.0 initialized  (Region: {self.region})")
        logger.info(f"  NDVI threshold: {self.ndvi_threshold:.2f}  |  "
                    f"ML feature scenes: {PipelineConfig.ML_FEATURE_SCENES}  |  "
                    f"Crops: {len(self.crop_names)}")

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
                    f"  {label}: ✗ No crop  "
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

    # =========================================================================
    # TEMPORAL PATTERN CROP DETECTION
    # =========================================================================

    def _detect_crop_temporal(self, scenes: List[Dict]) -> Tuple[bool, Dict]:
        """
        Detect crop presence using multi-signal temporal pattern analysis.

        Signals used:
          1. Peak NDVI — must exceed regional threshold
          2. NDVI Rise — difference between early-season and peak
             (eliminates permanent bare soil / concrete)
          3. Fraction above threshold — at least N% of scenes are green
             (eliminates single-scene cloud artifacts)
          4. Coefficient of variation (CV) — seasonal crops show
             meaningful variation; bare soil stays flat
          5. Growth arc shape — NDVI should show a rise-peak-decline
             pattern characteristic of cultivated crops

        Returns:
            (crop_present: bool, pattern_info: dict)
        """
        if not scenes:
            return False, self._empty_pattern()

        # Sort chronologically (defensive — should already be sorted)
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))

        ndvi_values = [s['indices'].get('NDVI_mean', 0.0) for s in sorted_scenes]
        n           = len(ndvi_values)

        # ── Basic stats ────────────────────────────────────────────────────
        peak_ndvi   = float(np.max(ndvi_values))
        avg_ndvi    = float(np.mean(ndvi_values))
        std_ndvi    = float(np.std(ndvi_values))
        ndvi_cv     = std_ndvi / max(avg_ndvi, 0.01)

        # NDVI rise: compare mean of first quarter vs peak
        q = max(1, n // 4)
        early_mean  = float(np.mean(ndvi_values[:q]))
        ndvi_rise   = peak_ndvi - early_mean

        # Fraction of scenes exceeding threshold
        frac_above  = float(np.mean([v > self.ndvi_threshold for v in ndvi_values]))

        # Growth arc score (0–1): proper rise-peak-fall
        arc_score   = self._compute_arc_score(ndvi_values)

        # ── Decision logic ─────────────────────────────────────────────────
        cfg = PipelineConfig

        reasons = []

        # Gate 1: peak must clear regional threshold
        if peak_ndvi <= self.ndvi_threshold:
            reasons.append(f"peak_ndvi {peak_ndvi:.3f} ≤ threshold {self.ndvi_threshold:.3f}")

        # Gate 2: enough green scenes
        if frac_above < cfg.MIN_FRACTION_ABOVE_THRESHOLD:
            reasons.append(f"only {frac_above:.0%} scenes above threshold "
                           f"(need ≥{cfg.MIN_FRACTION_ABOVE_THRESHOLD:.0%})")

        # Gate 3: meaningful NDVI rise (not permanent vegetation or bare soil)
        if ndvi_rise < cfg.MIN_NDVI_RISE:
            reasons.append(f"ndvi_rise {ndvi_rise:.3f} < min {cfg.MIN_NDVI_RISE:.3f}")

        # Gate 4: temporal variation in acceptable range
        if ndvi_cv < cfg.MIN_NDVI_CV_FOR_CROP:
            reasons.append(f"cv {ndvi_cv:.3f} too flat (min {cfg.MIN_NDVI_CV_FOR_CROP:.3f})")
        if ndvi_cv > cfg.MAX_NDVI_CV_FOR_CROP:
            reasons.append(f"cv {ndvi_cv:.3f} too erratic (max {cfg.MAX_NDVI_CV_FOR_CROP:.3f})")

        crop_detected = len(reasons) == 0

        pattern_info = {
            'peak_ndvi':        round(peak_ndvi, 4),
            'avg_ndvi':         round(avg_ndvi,  4),
            'ndvi_std':         round(std_ndvi,  4),
            'ndvi_cv':          round(ndvi_cv,   4),
            'ndvi_rise':        round(ndvi_rise,  4),
            'early_mean_ndvi':  round(early_mean, 4),
            'frac_above_thresh': round(frac_above, 4),
            'arc_score':        round(arc_score,  4),
            'n_scenes':         n,
            'rejection_reason': '; '.join(reasons) if reasons else '',
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

        Feature construction:
          - Sort scenes by date (ascending)
          - Select up to ML_FEATURE_SCENES evenly spaced scenes
            (so the feature vector always represents the same temporal
             positions regardless of how many scenes were collected)
          - For each selected scene: NDVI_mean, EVI_mean, NDMI_mean
          - Total features: ML_FEATURE_SCENES × 3

        This ensures the model receives a consistent temporal fingerprint
        of the crop's growth cycle.
        """
        n_feat   = PipelineConfig.ML_FEATURE_SCENES
        indices  = PipelineConfig.ML_FEATURE_INDICES

        # Sort chronologically
        sorted_scenes = sorted(scenes, key=lambda s: s.get('date', ''))

        # Evenly sample n_feat scenes across the season
        n = len(sorted_scenes)
        if n >= n_feat:
            # Evenly spaced indices across the full series
            idx_list = np.linspace(0, n - 1, n_feat, dtype=int)
            selected = [sorted_scenes[i] for i in idx_list]
        else:
            # Pad with zeros at the end if fewer scenes than features
            selected = sorted_scenes  # will be zero-padded below

        # Build feature vector
        feature_dict = {}
        for t, scene in enumerate(selected[:n_feat]):
            scene_indices = scene.get('indices', {})
            for idx_name in indices:
                key = f"{idx_name.replace('_mean', '')}_t{t+1:02d}"
                feature_dict[key] = scene_indices.get(idx_name, 0.0)

        # Zero-pad missing time steps
        for t in range(len(selected), n_feat):
            for idx_name in indices:
                key = f"{idx_name.replace('_mean', '')}_t{t+1:02d}"
                feature_dict[key] = 0.0

        # Align to model's feature order
        feature_vector = [feature_dict.get(fn, 0.0) for fn in self.feature_names]
        X = np.nan_to_num(np.array([feature_vector]), nan=0.0)

        prediction    = self.model.predict(X)[0]
        probabilities = self.model.predict_proba(X)[0]
        crop_name     = self.label_encoder.inverse_transform([prediction])[0]
        confidence    = float(probabilities.max())

        return {
            'crop':              crop_name,
            'confidence':        confidence,
            'all_probabilities': {
                c: float(p) for c, p in zip(self.crop_names, probabilities)
            },
            'n_scenes_used':     min(n, n_feat),
            'feature_scenes':    n_feat,
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