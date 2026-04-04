"""
SHAP Explainer — VERSION 2.0
==============================
Feature-level attribution for the credit scoring model.

Stage 7 update — consumes all Stage 2-6 outputs:
  - 16-feature extraction aligned with AdvancedCreditScorer.extract_features()
  - cultivation_signal, n_anomalies, n_high_impact, cycle_weather_risk
  - n_complete_cycles, n_active_cycles, avg_cycle_duration
  - Rule-based attribution maps Stage 6 component_scores with weights 35/25/15/8/7/5/5
  - Per-cycle anomaly narrative included in explanation output
  - Active-cycle note added when cycles are still growing

Supports:
    TreeExplainer   — for RF / XGBoost (fast, exact)
    KernelExplainer — fallback for any model type
    Rule-based attr — when no model available (always works)
"""

import logging
import numpy as np
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Stage 6 component weights (must match AdvancedCreditScorer._WEIGHTS)
_WEIGHTS = {
    'crop_detection':    35,
    'crop_performance':  25,
    'yield_potential':   15,
    'weather_safety':     8,
    'anomaly_penalty':    7,
    'cropping_intensity': 5,
    'govt_benefits':      5,
}


class SHAPExplainer:
    """
    SHAP-based explainability for the credit scoring model.
    Falls back to deterministic rule-based attribution when
    the shap package or a trained ML model is unavailable.
    """

    def __init__(
        self,
        model=None,
        feature_names: Optional[List[str]] = None,
    ):
        self.model           = model
        self.feature_names   = feature_names or []
        self._shap_explainer = None
        self._shap_available = self._check_shap()

    @staticmethod
    def _check_shap() -> bool:
        try:
            import shap  # noqa: F401
            return True
        except ImportError:
            logger.warning(
                "SHAP package not installed. "
                "Run: pip install shap  — rule-based attribution will be used."
            )
            return False

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def explain_assessment(self, assessment: Dict) -> Dict:
        """
        Generate SHAP (or rule-based) explanations from the full pipeline dict.

        Returns:
            Dict with shap_values, feature_contributions, top_positive,
            top_negative, force_plot_data, anomaly_narrative, and
            cycle_summary.
        """
        if not self._shap_available or self.model is None:
            result = self._rule_based_shap(assessment)
        else:
            features = self._extract_features(assessment)
            result   = self._compute_shap(features, assessment)

        # Always enrich with cycle-level narrative (Stage 5 data)
        result['anomaly_narrative'] = self._build_anomaly_narrative(assessment)
        result['cycle_summary']     = self._build_cycle_summary(assessment)
        result['active_cycle_note'] = self._active_cycle_note(assessment)

        return result

    # =========================================================================
    # FEATURE EXTRACTION  (16 features, aligned with AdvancedCreditScorer)
    # =========================================================================

    @staticmethod
    def _extract_features(assessment: Dict) -> Dict[str, float]:
        """
        Extract 16 normalized (0–100) features for ML-SHAP explanation.
        Aligned with AdvancedCreditScorer.extract_features().
        """
        ca = assessment.get('cropping_analysis', {})
        pa = assessment.get('performance_analysis', {})
        wa = assessment.get('weather_analysis', {})
        fb = assessment.get('farmer_benefits', {}) or {}
        cc = assessment.get('crop_cycles', {})    or {}

        sp = pa.get('seasonal_performance', [])
        n_anom   = sum(len(p.get('anomaly_events', [])) for p in sp)
        n_high   = sum(
            len([e for e in p.get('anomaly_events', []) if e.get('impact') == 'HIGH'])
            for p in sp
        )

        cycle_risks = wa.get('cycle_risk_scores', [])
        avg_cr = float(np.mean([c.get('risk_score', 50) for c in cycle_risks])) \
            if cycle_risks else float(wa.get('weather_risk_score', 50))

        util = cc.get('utilization_metrics', {}) or {}

        ci = float(ca.get('cropping_intensity', 0.0))

        return {
            'cropping_intensity_pct':    min(100.0, ci * 50.0),
            'cultivation_signal':        float(ca.get('cultivation_signal', 50.0)),
            'n_complete_cycles':         min(100.0, float(pa.get('n_complete_cycles', 0)) / 6.0 * 100),
            'n_active_cycles':           float(pa.get('n_active_cycles', 0)) * 20,
            'avg_performance_score':     float(pa.get('average_performance_score', 50.0)),
            'avg_yield_score':           float(pa.get('average_yield_score', 50.0)),
            'n_anomalies_total':         min(100.0, float(n_anom) * 5),
            'n_high_impact_anomalies':   min(100.0, float(n_high) * 10),
            'weather_risk_score':        float(wa.get('weather_risk_score', 50.0)),
            'total_extreme_events':      min(100.0, float(wa.get('total_extreme_events', 0)) * 5),
            'avg_cycle_weather_risk':    avg_cr,
            'land_utilization_pct':      float(util.get('land_utilization_index', 0.0)) * 100,
            'seasons_with_crops_pct':    min(100.0,
                float(ca.get('seasons_with_crops', 0))
                / max(float(ca.get('total_seasons_analyzed', 1)), 1) * 100),
            'unique_crops':              min(100.0, float(len(ca.get('crops_detected', {}))) * 25),
            'pm_kisan':                  100.0 if fb.get('pm_kisan_enrolled') else 0.0,
            'has_crop_insurance':        100.0 if fb.get('has_crop_insurance') else 0.0,
        }

    # =========================================================================
    # SHAP COMPUTATION
    # =========================================================================

    def _compute_shap(self, features: Dict[str, float], assessment: Dict) -> Dict:
        """Compute SHAP values using TreeExplainer or KernelExplainer fallback."""
        import shap

        feat_names = list(features.keys())
        X = np.array([[features[k] for k in feat_names]], dtype=float)

        try:
            if self._shap_explainer is None:
                try:
                    self._shap_explainer = shap.TreeExplainer(self.model)
                    logger.info("SHAP: using TreeExplainer")
                except Exception:
                    bg = np.zeros((1, len(feat_names)))
                    self._shap_explainer = shap.KernelExplainer(
                        self.model.predict, bg
                    )
                    logger.info("SHAP: TreeExplainer failed → KernelExplainer")

            shap_vals = self._shap_explainer.shap_values(X)
            if isinstance(shap_vals, list):
                shap_vals = shap_vals[-1]
            sv = shap_vals[0]

        except Exception as exc:
            logger.error(f"SHAP computation failed: {exc}")
            return self._rule_based_shap(assessment)

        contribs     = {k: round(float(v), 3) for k, v in zip(feat_names, sv)}
        sorted_c     = sorted(contribs.items(), key=lambda x: abs(x[1]), reverse=True)

        top_positive = [
            {'feature': k, 'contribution': v, 'value': round(features[k], 1),
             'label': _feature_label(k)}
            for k, v in sorted_c if v > 0
        ][:5]
        top_negative = [
            {'feature': k, 'contribution': v, 'value': round(features[k], 1),
             'label': _feature_label(k)}
            for k, v in sorted_c if v < 0
        ][:5]

        try:
            base_value = float(self._shap_explainer.expected_value)
            if isinstance(base_value, np.ndarray):
                base_value = float(base_value[-1])
        except Exception:
            base_value = 50.0

        return {
            'shap_available':        True,
            'method':                'tree_shap' if hasattr(self._shap_explainer, 'trees') else 'kernel_shap',
            'base_value':            round(base_value, 2),
            'feature_contributions': contribs,
            'top_positive_drivers':  top_positive,
            'top_negative_drivers':  top_negative,
            'feature_values':        {k: round(v, 1) for k, v in features.items()},
            'force_plot_data': {
                'base_value':    round(base_value, 2),
                'shap_values':   [round(float(v), 3) for v in sv],
                'feature_names': feat_names,
                'features':      [round(features[k], 1) for k in feat_names],
            },
        }

    # =========================================================================
    # RULE-BASED ATTRIBUTION  (no model needed)
    # =========================================================================

    @staticmethod
    def _rule_based_shap(assessment: Dict) -> Dict:
        """
        Deterministic attribution from Stage 6 component_scores.
        Uses the 7-component weights to derive per-component contributions.
        """
        cr = assessment.get('credit_assessment', {})
        component_scores = cr.get('component_scores', {})

        if not component_scores:
            return {
                'shap_available': False,
                'method': 'none',
                'reason': 'No model or component_scores found in assessment',
            }

        # Attribution: contribution = weight × (score - 50) / 100
        # Positive = component is HELPING the score
        # Negative = component is HURTING the score
        contribs = {}
        for comp, score in component_scores.items():
            w = _WEIGHTS.get(comp, 5)
            contribs[comp] = round((score - 50) / 100 * (w / 10), 3)

        sorted_c     = sorted(contribs.items(), key=lambda x: abs(x[1]), reverse=True)
        top_positive = [
            {'feature': k, 'contribution': v,
             'value': round(component_scores.get(k, 50), 1),
             'label': _feature_label(k)}
            for k, v in sorted_c if v > 0
        ][:5]
        top_negative = [
            {'feature': k, 'contribution': v,
             'value': round(component_scores.get(k, 50), 1),
             'label': _feature_label(k)}
            for k, v in sorted_c if v < 0
        ][:5]

        return {
            'shap_available':        True,
            'method':                'rule_based_attribution_v2',
            'base_value':            50.0,
            'feature_contributions': contribs,
            'top_positive_drivers':  top_positive,
            'top_negative_drivers':  top_negative,
            'feature_values':        {k: round(v, 1) for k, v in component_scores.items()},
        }

    # =========================================================================
    # ENRICHMENT: ANOMALY NARRATIVE & CYCLE SUMMARY
    # =========================================================================

    @staticmethod
    def _build_anomaly_narrative(assessment: Dict) -> List[Dict]:
        """
        Return a flat list of all HIGH/MEDIUM anomaly events across all
        cycles, with stage, date, and interpretation. Used by AI reports.
        """
        pa = assessment.get('performance_analysis', {})
        sp = pa.get('seasonal_performance', [])
        events = []
        for perf in sp:
            cycle_label = (
                f"{perf.get('season','?').upper()} {perf.get('year','?')}"
            )
            for ev in perf.get('anomaly_events', []):
                if ev.get('impact') in ('HIGH', 'MEDIUM'):
                    events.append({
                        'cycle':       cycle_label,
                        'type':        ev.get('type'),
                        'stage':       ev.get('stage'),
                        'impact':      ev.get('impact'),
                        'date':        ev.get('date', ''),
                        'description': ev.get('description', ''),
                    })
        return events

    @staticmethod
    def _build_cycle_summary(assessment: Dict) -> List[Dict]:
        """
        Return a compact per-cycle summary for AI report generation.
        Includes scoring_method, health, yield_pct, anomaly count.
        """
        pa = assessment.get('performance_analysis', {})
        sp = pa.get('seasonal_performance', [])
        summary = []
        for p in sp:
            summary.append({
                'label':          f"{p.get('season','?').upper()} {p.get('year','?')}",
                'crop':           p.get('crop', 'Unknown'),
                'is_active':      p.get('is_active_cycle', False),
                'health_score':   p.get('health_score',   0),
                'yield_pct':      p.get('yield_potential_pct', '?'),
                'scoring_method': p.get('scoring_method', '?'),
                'n_anomalies':    len(p.get('anomaly_events', [])),
                'n_high':         sum(1 for e in p.get('anomaly_events', [])
                                     if e.get('impact') == 'HIGH'),
                'narrative':      p.get('performance_narrative', ''),
            })
        return summary

    @staticmethod
    def _active_cycle_note(assessment: Dict) -> str:
        pa = assessment.get('performance_analysis', {})
        n  = pa.get('n_active_cycles', 0)
        return (
            f"{n} crop cycle(s) currently active — yield income expected at harvest. "
            "Current scores reflect in-progress growth; final performance will be higher."
        ) if n else ""


# ============================================================================
# HELPERS
# ============================================================================

_FEATURE_LABELS = {
    'crop_detection':          'Crop Detection Consistency',
    'crop_performance':        'Crop Health Performance',
    'yield_potential':         'Yield Potential',
    'weather_safety':          'Weather Safety',
    'anomaly_penalty':         'Vegetation Anomaly Risk',
    'cropping_intensity':      'Cropping Intensity',
    'govt_benefits':           'Government Scheme Enrollment',
    'cultivation_signal':      'Cultivation Signal Strength',
    'n_complete_cycles':       'Completed Crop Cycles',
    'n_anomalies_total':       'Total Anomaly Events',
    'n_high_impact_anomalies': 'High-Impact Stress Events',
    'weather_risk_score':      'Weather Risk Score',
    'avg_cycle_weather_risk':  'Cycle-Aligned Weather Risk',
    'avg_performance_score':   'Average Crop Performance',
    'avg_yield_score':         'Average Yield Score',
    'land_utilization_pct':    'Land Utilization',
    'seasons_with_crops_pct':  'Season Consistency',
    'pm_kisan':                'PM-KISAN Enrollment',
    'has_crop_insurance':      'Crop Insurance Coverage',
}


def _feature_label(key: str) -> str:
    return _FEATURE_LABELS.get(key, key.replace('_', ' ').title())
