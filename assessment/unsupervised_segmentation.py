"""
Unsupervised Farmer Segmentation — VERSION 2.0
===============================================
ML-based farmer segmentation WITHOUT requiring pre-labeled training data.

VERSION 2.0 — Stage 6 update:
- _extract_features() now consumes Stage 2-5 outputs:
    cultivation_signal, n_anomalies, n_high_impact_events,
    cycle_weather_risk, yield_potential_pct, n_active_cycles,
    avg_cycle_duration_days
- Single-farmer inference fixed: synthetic population bootstrap perturbs
  ALL 14 features with realistic agricultural variance
- Segment profiles reclassified with new intensity vocabulary
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# FEATURE EXTRACTION  (shared by fit + predict + bootstrap)
# ============================================================================

def _extract_single(assessment: Dict) -> List[float]:
    """
    Extract 14 numerical features from one assessment dict.
    All features normalised to consistent scales before ML.

    Feature list:
      0  cropping_intensity      (cycles per year, 0-3)
      1  cultivation_signal      (0-100, Stage 2 — peak/AUC signal)
      2  n_complete_cycles       (from n_complete_cycles, 0-8)
      3  n_active_cycles         (1 if currently growing, else 0)
      4  avg_cycle_duration_days (avg length of complete cycles, 0-240)
      5  avg_performance_score   (0-100)
      6  avg_yield_score         (0-100)
      7  n_anomalies_total       (sum across all cycles, 0-20)
      8  n_high_impact_anomalies (sum HIGH-impact events, 0-10)
      9  weather_risk_score      (0-100 risk; higher = worse)
     10  n_extreme_weather_events (count of extreme events, 0-15)
     11  cycle_weather_risk      (avg cycle-aligned risk 0-100, Stage 4)
     12  land_utilization_index  (0-1 → ×100)
     13  n_seasons_detected      (seasons with crops detected, 0-6)
    """
    f: List[float] = []

    # ── Cropping analysis ────────────────────────────────────────────────────
    ca = assessment.get('cropping_analysis', {})
    f.append(float(ca.get('cropping_intensity',   0.0)))
    f.append(float(ca.get('cultivation_signal',   50.0)))  # Stage-2 signal

    # ── Cycle metadata (from cycle-based analysis) ───────────────────────────
    cc = assessment.get('crop_cycles', {}) or {}
    f.append(float(cc.get('n_complete_cycles',  ca.get('seasons_with_crops', 0))))
    f.append(float(cc.get('n_active_cycles',    0)))

    # Average cycle duration from seasonal_performance
    pa = assessment.get('performance_analysis', {})
    sp = pa.get('seasonal_performance', [])
    complete_sp = [p for p in sp if not p.get('is_active_cycle')]
    if complete_sp:
        durations = []
        for p in complete_sp:
            s = p.get('start_date', '')
            e = p.get('end_date', '')
            try:
                from datetime import datetime
                ds = (datetime.strptime(e, '%Y-%m-%d')
                      - datetime.strptime(s, '%Y-%m-%d')).days
                if ds > 0:
                    durations.append(ds)
            except Exception:
                pass
        f.append(float(np.mean(durations)) if durations else 100.0)
    else:
        f.append(100.0)

    # ── Performance analysis (Stage 5) ──────────────────────────────────────
    f.append(float(pa.get('average_performance_score', 50.0)))
    f.append(float(pa.get('average_yield_score',       50.0)))

    # Anomaly counts from seasonal_performance entries
    n_anom_total = 0
    n_high_total = 0
    for p in sp:
        evs = p.get('anomaly_events', [])
        n_anom_total += len(evs)
        n_high_total += sum(1 for e in evs if e.get('impact') == 'HIGH')
    f.append(float(n_anom_total))
    f.append(float(n_high_total))

    # ── Weather analysis (Stage 4) ───────────────────────────────────────────
    wa = assessment.get('weather_analysis', {})
    f.append(float(wa.get('weather_risk_score',      50.0)))
    f.append(float(wa.get('total_extreme_events',    0)))

    # Cycle-aligned weather risk (Stage 4 new field)
    cycle_risks = wa.get('cycle_risk_scores', [])
    if cycle_risks:
        f.append(float(np.mean([cr.get('risk_score', 50) for cr in cycle_risks])))
    else:
        f.append(float(wa.get('weather_risk_score', 50.0)))

    # ── Land utilisation (from crop_cycles if available) ─────────────────────
    utilization = cc.get('utilization_metrics', {}) or {}
    f.append(float(utilization.get('land_utilization_index', 0.0)) * 100)

    # Seasons detected ────────────────────────────────────────────────────────
    f.append(float(ca.get('seasons_with_crops', len([p for p in sp]))))

    return f


FEATURE_NAMES = [
    'cropping_intensity',
    'cultivation_signal',
    'n_complete_cycles',
    'n_active_cycles',
    'avg_cycle_duration_days',
    'avg_performance_score',
    'avg_yield_score',
    'n_anomalies_total',
    'n_high_impact_anomalies',
    'weather_risk_score',
    'n_extreme_weather_events',
    'cycle_weather_risk',
    'land_utilization_index',
    'n_seasons_detected',
]


def build_synthetic_population(assessment: Dict, n: int = 50) -> List[Dict]:
    """
    Build a synthetic population by perturbing the single-farmer assessment
    with realistic Gaussian noise on all 14 features.
    Used for single-farmer unsupervised bootstrap fitting.

    Noise scales are chosen agronomically:
      - cultivation_signal: ±15 (within-field variance)
      - performance / yield: ±12
      - cropping_intensity: ±0.30
      - n_anomalies: ±3
      - weather_risk: ±10
    """
    import copy, random

    population = []
    for _ in range(n):
        syn = copy.deepcopy(assessment)

        # Perturb cropping_analysis
        ca = syn.setdefault('cropping_analysis', {})
        ca['cropping_intensity'] = max(0.0, ca.get('cropping_intensity', 0.5)
                                       + random.gauss(0, 0.30))
        ca['cultivation_signal'] = max(0.0, min(100.0,
            ca.get('cultivation_signal', 50.0) + random.gauss(0, 15)))
        ca['seasons_with_crops'] = max(0, int(
            ca.get('seasons_with_crops', 3) + random.gauss(0, 1)))

        # Perturb performance_analysis
        pa = syn.setdefault('performance_analysis', {})
        pa['average_performance_score'] = max(0, min(100,
            pa.get('average_performance_score', 50) + random.gauss(0, 12)))
        pa['average_yield_score'] = max(0, min(100,
            pa.get('average_yield_score', 50) + random.gauss(0, 12)))

        # Perturb anomaly counts inside seasonal_performance
        sp = pa.get('seasonal_performance', [])
        for p in sp:
            evs = p.get('anomaly_events', [])
            # Randomly add or remove events
            delta = int(random.gauss(0, 1.5))
            if delta > 0:
                for _ in range(delta):
                    evs.append({'impact': random.choice(['LOW', 'MEDIUM', 'HIGH'])})
            elif delta < 0:
                for _ in range(abs(delta)):
                    if evs:
                        evs.pop()
            p['anomaly_events'] = evs

        # Perturb weather_analysis
        wa = syn.setdefault('weather_analysis', {})
        wa['weather_risk_score'] = max(0, min(100,
            wa.get('weather_risk_score', 50) + random.gauss(0, 10)))
        wa['total_extreme_events'] = max(0, int(
            wa.get('total_extreme_events', 3) + random.gauss(0, 2)))

        population.append(syn)

    return population


class UnsupervisedFarmerSegmentation:
    """
    Discover farmer segments using unsupervised ML.
    NO labeled data required. Supports single-farmer inference via
    synthetic-population bootstrap.
    """

    def __init__(
        self,
        n_segments: int = 5,
        contamination: float = 0.15,
    ):
        self.n_segments    = n_segments
        self.contamination = contamination

        self.scaler           = None
        self.kmeans           = None
        self.isolation_forest = None
        self.feature_names    = FEATURE_NAMES[:]

        logger.info(
            f"UnsupervisedFarmerSegmentation v2.0 initialized "
            f"(k={n_segments}, contamination={contamination}, "
            f"features={len(self.feature_names)})"
        )

    def fit(self, assessments: List[Dict]) -> Tuple[List[int], List[Dict]]:
        """
        Fit segmentation model on a population of farmer assessments.

        Returns:
            (segment_labels, segment_profiles)
        """
        logger.info(f"Fitting unsupervised model on {len(assessments)} assessments "
                    f"with {len(self.feature_names)} features")

        X = np.array([_extract_single(a) for a in assessments])

        # Replace NaN/inf with median
        X = np.where(np.isfinite(X), X, np.nanmedian(X, axis=0))

        self.scaler   = StandardScaler()
        X_scaled      = self.scaler.fit_transform(X)

        self.kmeans   = KMeans(
            n_clusters=min(self.n_segments, len(assessments)),
            random_state=42, n_init=10,
        )
        segments = self.kmeans.fit_predict(X_scaled)

        self.isolation_forest = IsolationForest(
            contamination=self.contamination,
            random_state=42,
        )
        self.isolation_forest.fit(X_scaled)

        profiles = self._create_segment_profiles(X, segments)
        logger.info(f"Discovered {self.n_segments} segments:")
        for p in profiles:
            logger.info(f"  Segment {p['segment_id']}: {p['name']} ({p['count']} farmers)")

        return segments.tolist(), profiles

    def predict(self, assessment: Dict) -> Dict:
        """
        Predict segment and risk for a single farmer assessment.
        Raises ValueError if model not fitted; call fit() or bootstrap first.
        """
        if self.kmeans is None:
            raise ValueError("Model not fitted. Call fit() first.")

        x  = np.array([_extract_single(assessment)])
        x  = np.where(np.isfinite(x), x, 0.0)
        xs = self.scaler.transform(x)

        segment       = int(self.kmeans.predict(xs)[0])
        anomaly_score = float(self.isolation_forest.score_samples(xs)[0])

        # Convert IsolationForest score_samples to 0–100 risk
        # score_samples is roughly in [-0.5, 0.1] (lower = more anomalous)
        # Map: 0.10 → 0 risk (normal), -0.50 → 100 risk (very anomalous)
        risk_score = float(np.clip((0.10 - anomaly_score) / 0.60 * 100, 0, 100))

        if   risk_score < 30: risk_category = "LOW"
        elif risk_score < 50: risk_category = "MEDIUM"
        elif risk_score < 70: risk_category = "HIGH"
        else:                 risk_category = "VERY_HIGH"

        # Feature vector for interpretation
        feat_vec = dict(zip(self.feature_names, x[0]))

        return {
            'segment':          segment,
            'risk_score':       round(risk_score, 1),
            'risk_category':    risk_category,
            'is_outlier':       risk_score >= 70,
            'anomaly_score_raw': round(anomaly_score, 4),
            'method':           'unsupervised_ml_v2',
            'feature_snapshot': {k: round(float(v), 2) for k, v in feat_vec.items()},
        }

    # =========================================================================
    # PRIVATE METHODS
    # =========================================================================

    def _create_segment_profiles(
        self,
        X:        np.ndarray,
        segments: np.ndarray,
    ) -> List[Dict]:
        profiles = []
        for seg_id in range(self.n_segments):
            mask = segments == seg_id
            seg_data = X[mask]
            if len(seg_data) == 0:
                continue
            avg = np.mean(seg_data, axis=0)
            feat_dict = {
                name: round(float(val), 2)
                for name, val in zip(self.feature_names, avg)
            }
            name = self._classify_segment(feat_dict)
            profiles.append({
                'segment_id':       seg_id,
                'name':             name,
                'count':            int(np.sum(mask)),
                'average_features': feat_dict,
                'description':      self._describe_segment(name, feat_dict),
            })
        return profiles

    @staticmethod
    def _classify_segment(f: Dict) -> str:
        intensity   = f.get('cropping_intensity', 0)
        signal      = f.get('cultivation_signal', 50)
        performance = f.get('avg_performance_score', 0)
        yield_score = f.get('avg_yield_score', 0)
        high_impact = f.get('n_high_impact_anomalies', 0)

        # Tier 1: commercial/high intensity
        if intensity >= 2.0 and signal >= 70 and performance >= 70:
            return 'HIGH_INTENSITY_COMMERCIAL'
        # Tier 2: progressive double cropping
        if intensity >= 1.5 and performance >= 60 and high_impact <= 2:
            return 'PROGRESSIVE_DOUBLE_CROPPING'
        # Tier 3: moderate traditional
        if intensity >= 1.0 and performance >= 50:
            return 'MODERATE_TRADITIONAL'
        # Tier 4: high yield but low frequency
        if yield_score >= 65 and intensity < 1.5:
            return 'LARGE_SCALE_SINGLE_CROP'
        # Tier 5: stressed or low intensity
        return 'LOW_INTENSITY_OR_STRESSED'

    @staticmethod
    def _describe_segment(name: str, f: Dict) -> str:
        descs = {
            'HIGH_INTENSITY_COMMERCIAL': (
                f"High-intensity commercial farming: {f.get('cropping_intensity', 0):.1f} "
                f"cycles/year, cultivation signal {f.get('cultivation_signal', 0):.0f}/100, "
                f"performance {f.get('avg_performance_score', 0):.0f}%. Lowest risk tier."
            ),
            'PROGRESSIVE_DOUBLE_CROPPING': (
                f"Progressive double-cropping with {f.get('cropping_intensity', 0):.1f} "
                f"cycles/year, {f.get('avg_performance_score', 0):.0f}% performance. "
                f"Low-medium risk with good credit capacity."
            ),
            'MODERATE_TRADITIONAL': (
                f"Traditional farming, {f.get('cropping_intensity', 0):.1f} cycles/year, "
                f"{f.get('avg_performance_score', 0):.0f}% performance. Medium risk."
            ),
            'LARGE_SCALE_SINGLE_CROP': (
                f"Single long-duration crop, yield {f.get('avg_yield_score', 0):.0f}%. "
                "Lower intensity but good per-cycle performance. Medium-low risk."
            ),
            'LOW_INTENSITY_OR_STRESSED': (
                f"Low intensity ({f.get('cropping_intensity', 0):.1f} cycles/year) "
                f"or stressed ({f.get('n_high_impact_anomalies', 0):.0f} high-impact events). "
                "Higher risk; may need additional lender support."
            ),
        }
        return descs.get(name, 'Unclassified segment')


# ============================================================================
# STANDALONE RISK ANOMALY DETECTOR
# ============================================================================

class RiskAnomalyDetector:
    """
    Detect risky/anomalous farmers using Isolation Forest.
    Can work standalone or alongside segmentation.
    """

    def __init__(self, contamination: float = 0.15):
        self.contamination = contamination
        self.model  = None
        self.scaler = None

    def fit(self, assessments: List[Dict]):
        X = np.array([_extract_single(a) for a in assessments])
        X = np.where(np.isfinite(X), X, np.nanmedian(X, axis=0))
        self.scaler = StandardScaler()
        X_scaled    = self.scaler.fit_transform(X)
        self.model  = IsolationForest(
            contamination=self.contamination, random_state=42
        )
        self.model.fit(X_scaled)
        logger.info(f"RiskAnomalyDetector trained on {len(assessments)} farmers")

    def predict(self, assessment: Dict) -> Dict:
        if self.model is None:
            raise ValueError("Model not trained. Call fit() first.")
        x       = np.array([_extract_single(assessment)])
        x       = np.where(np.isfinite(x), x, 0.0)
        xs      = self.scaler.transform(x)
        is_anom = self.model.predict(xs)[0] == -1
        raw     = float(self.model.score_samples(xs)[0])
        risk    = float(np.clip((0.10 - raw) / 0.60 * 100, 0, 100))
        if   risk < 30: level = "LOW"
        elif risk < 50: level = "MEDIUM"
        elif risk < 70: level = "HIGH"
        else:           level = "VERY_HIGH"
        return {
            'is_anomaly':   bool(is_anom),
            'anomaly_score': round(raw,  3),
            'risk_score':    round(risk, 1),
            'risk_level':    level,
        }


__all__ = ['UnsupervisedFarmerSegmentation', 'RiskAnomalyDetector',
           'build_synthetic_population', '_extract_single', 'FEATURE_NAMES']
