"""
Advanced Credit Scorer — VERSION 3.0
=====================================
Combines traditional rule-based scoring with ML predictions.
Stage 6 update: fully consumes Stage 2-5 pipeline outputs.

Modes:
  rule_based    — Weighted scoring using all pipeline component outputs
  unsupervised  — Isolation Forest / KMeans on 14 features, single-farmer safe
  supervised    — XGBoost/RandomForest when historical labels available
  hybrid        — rule_based × 0.65 + unsupervised × 0.35 (single-farmer default)

Scoring component map (rule_based):
  crop_detection   35%  — cycle count, cultivation_signal, consistency
  crop_performance 25%  — avg_performance_score (Stage 5)
  yield_potential  15%  — avg_yield_score (Stage 5)
  weather_safety    8%  — cycle_weather_risk (Stage 4), extreme event count
  anomaly_penalty   7%  — n_high_impact_anomalies (Stage 5)
  cropping_intensity 5% — cycles/year
  govt_benefits     5%  — PM-KISAN, crop insurance
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime
import logging

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import RandomForestClassifier
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False

try:
    from config import PipelineConfig
except ImportError:
    from config import PipelineConfig

logger = logging.getLogger(__name__)


# ============================================================================
# WEIGHT TABLE  (rule_based scoring)
# ============================================================================
_WEIGHTS = {
    'crop_detection':    35,
    'crop_performance':  25,
    'yield_potential':   15,
    'weather_safety':     8,
    'anomaly_penalty':    7,
    'cropping_intensity': 5,
    'govt_benefits':      5,
}
assert sum(_WEIGHTS.values()) == 100, "Weights must sum to 100"


class AdvancedCreditScorer:
    """
    ML-enhanced credit scorer — multi-mode, single-farmer safe.
    """

    def __init__(
        self,
        mode: str = 'hybrid',
        verbose: bool = True,
    ):
        """
        Args:
            mode: 'rule_based' | 'unsupervised' | 'supervised' | 'hybrid'
            verbose: Enable detailed logging
        """
        self.mode    = mode
        self.verbose = verbose

        self.model                = None
        self.scaler               = None
        self.feature_names        = None
        self.unsupervised_segmenter = None

        logger.info(f"AdvancedCreditScorer v3.0 initialized (mode={mode})")

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def calculate_credit_score(
        self,
        cropping_analysis:    Dict,
        performance_analysis: Dict,
        weather_analysis:     Dict,
        farmer_benefits:      Optional[Dict] = None,
        crop_cycles:          Optional[Dict] = None,
    ) -> Dict:
        """
        Calculate credit score using the configured mode.

        Args:
            cropping_analysis:    From CropDetector.analyze_cycles()
            performance_analysis: From CropPerformanceAnalyzer.analyze_performance()
            weather_analysis:     From WeatherAnalyzer.analyze_cycle_weather()
            farmer_benefits:      {pm_kisan_enrolled, has_crop_insurance}
            crop_cycles:          Optional raw cycle metadata dict

        Returns:
            Dict with credit_score, risk_category, component_scores,
                  method, confidence, weak_components, scoring_narrative
        """
        assessment = {
            'cropping_analysis':  cropping_analysis,
            'performance_analysis': performance_analysis,
            'weather_analysis':   weather_analysis,
            'farmer_benefits':    farmer_benefits or {},
            'crop_cycles':        crop_cycles or {},
        }

        if   self.mode == 'rule_based':    return self._rule_based_score(assessment)
        elif self.mode == 'unsupervised':  return self._unsupervised_score(assessment)
        elif self.mode == 'supervised':    return self._supervised_score(assessment)
        elif self.mode == 'hybrid':        return self._hybrid_score(assessment)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

    def calculate_credit_limit(
        self,
        credit_score:      float,
        field_area_ha:     float,
        cropping_analysis: Dict,
        performance_analysis: Optional[Dict] = None,
        farmer_benefits:   Optional[Dict] = None,
    ) -> Dict:
        """
        Compute recommended credit limit, interest rate, tenure, and conditions.

        Integrates Stage 5 data: if active cycle present, flag as pending harvest.
        """
        # Map score to base limit per hectare
        if   credit_score >= 80: base_per_ha, risk_level = 15000, 'LOW'
        elif credit_score >= 70: base_per_ha, risk_level = 12000, 'MEDIUM_LOW'
        elif credit_score >= 60: base_per_ha, risk_level = 10000, 'MEDIUM'
        elif credit_score >= 50: base_per_ha, risk_level =  7500, 'MEDIUM_HIGH'
        elif credit_score >= 40: base_per_ha, risk_level =  5000, 'HIGH'
        else:                    base_per_ha, risk_level =  3000, 'VERY_HIGH'

        # Intensity multiplier (cycles/year scale)
        ci = cropping_analysis.get('cropping_intensity', 1.0)
        intensity_mult = round(0.80 + min(ci / 2.0, 1.0) * 0.40, 3)  # 0.80–1.20

        # Area multiplier
        if   field_area_ha >= 10: area_mult = 1.15
        elif field_area_ha >= 5:  area_mult = 1.10
        elif field_area_ha >= 2:  area_mult = 1.05
        elif field_area_ha >= 1:  area_mult = 1.00
        else:                     area_mult = 0.90

        # High-value crop multiplier
        HIGH_VALUE = {'Sugarcane', 'Cotton', 'Banana', 'Potato', 'Tomato',
                      'Onion', 'Garlic', 'Grapes', 'Pomegranate', 'Mango',
                      'Chilli', 'Tobacco'}
        dominant = cropping_analysis.get('dominant_crop', '')
        crop_mult = 1.15 if dominant in HIGH_VALUE else 1.00

        # Benefits multiplier
        fb = farmer_benefits or {}
        ben_mult = 1.00
        if fb.get('pm_kisan_enrolled'): ben_mult += 0.05
        if fb.get('has_crop_insurance'): ben_mult += 0.05

        # Active cycle adjustment — note it but don't reduce limit
        pa = performance_analysis or {}
        n_active = pa.get('n_active_cycles', 0)
        active_note = ''
        if n_active > 0:
            active_note = (
                f"{n_active} cycle(s) currently growing — harvest income expected; "
                "final repayment capacity higher than current assessment."
            )

        total_mult  = intensity_mult * area_mult * crop_mult * ben_mult
        base_total  = base_per_ha * max(field_area_ha, 0.1)
        final_limit = round((base_total * total_mult) / 1000) * 1000

        return {
            'recommended_limit': final_limit,
            'base_limit_per_ha': base_per_ha,
            'risk_level':        risk_level,
            'field_area_ha':     field_area_ha,
            'multipliers': {
                'intensity':   intensity_mult,
                'area':        area_mult,
                'crop_value':  crop_mult,
                'benefits':    ben_mult,
                'total':       round(total_mult, 3),
            },
            'recommendations': {
                'interest_rate':    self._suggest_interest_rate(credit_score),
                'tenure_months':    6 if ci >= 2.0 else 12,
                'repayment_type':   'lump_sum' if ci >= 2.0 else 'installments',
            },
            'reasoning':    self._explain_limit(credit_score, ci, field_area_ha, total_mult),
            'active_cycle_note': active_note,
        }

    # =========================================================================
    # SCORING MODES
    # =========================================================================

    def _rule_based_score(self, assessment: Dict) -> Dict:
        """
        Rule-based scoring consuming all Stage 1–5 outputs.

        Components:
          crop_detection   (35%): cycle count + cultivation_signal + season consistency
          crop_performance (25%): avg_performance_score from Stage 5
          yield_potential  (15%): avg_yield_score from Stage 5
          weather_safety    (8%): 100 - weighted weather risk (stage-4 cycle risk)
          anomaly_penalty   (7%): 100 - penalty for HIGH-impact anomaly events
          cropping_intensity(5%): cycles per year score
          govt_benefits     (5%): PM-KISAN + insurance
        """
        scores: Dict[str, float] = {}
        ca = assessment.get('cropping_analysis', {})
        pa = assessment.get('performance_analysis', {})
        wa = assessment.get('weather_analysis', {})
        fb = assessment.get('farmer_benefits', {})

        # ── 1. Crop Detection (35%) ──────────────────────────────────────────
        scores['crop_detection'] = self._score_crop_detection_v4(ca, pa)

        # ── 2. Crop Performance (25%) ────────────────────────────────────────
        scores['crop_performance'] = float(pa.get('average_performance_score', 50.0))

        # ── 3. Yield Potential (15%) ─────────────────────────────────────────
        scores['yield_potential'] = float(pa.get('average_yield_score', 50.0))

        # ── 4. Weather Safety (8%): Stage-4 cycle-aligned risk ───────────────
        scores['weather_safety'] = self._score_weather_safety_v4(wa)

        # ── 5. Anomaly Penalty (7%): Stage-5 per-cycle anomaly events ────────
        scores['anomaly_penalty'] = self._score_anomaly_penalty(pa)

        # ── 6. Cropping Intensity (5%) ───────────────────────────────────────
        scores['cropping_intensity'] = self._score_cropping_intensity_v4(ca)

        # ── 7. Govt Benefits (5%) ────────────────────────────────────────────
        scores['govt_benefits'] = self._score_govt_benefits(fb)

        # ── Weighted total ────────────────────────────────────────────────────
        total = sum(scores[k] * (_WEIGHTS[k] / 100) for k in scores)
        total = round(min(100.0, max(0.0, total)), 1)

        risk_category   = self._classify_risk(total)
        weak_components = [k for k, v in scores.items() if v < 50.0]

        logger.info(f"\n{'='*60}")
        logger.info("CREDIT SCORE  (rule_based v4.0)")
        logger.info(f"{'='*60}")
        for k, v in scores.items():
            wt = _WEIGHTS[k]
            logger.info(f"  {k:22s}: {v:5.1f}/100  ×{wt}% = {v*wt/100:.1f}")
        logger.info(f"\n  ► Total Credit Score: {total}/100")
        logger.info(f"  ► Risk Category:      {risk_category}")
        if weak_components:
            logger.info(f"  ► Weak components:    {', '.join(weak_components)}")

        narrative = self._build_score_narrative(
            total, risk_category, scores, weak_components, pa
        )

        return {
            'credit_score':      total,
            'risk_category':     risk_category,
            'component_scores':  {k: round(v, 1) for k, v in scores.items()},
            'component_weights': _WEIGHTS.copy(),
            'weak_components':   weak_components,
            'method':            'rule_based_v4',
            'confidence':        80.0,
            'assessment_date':   datetime.now().isoformat(),
            'scoring_narrative': narrative,
        }

    def _unsupervised_score(self, assessment: Dict) -> Dict:
        """
        Unsupervised ML scoring — single-farmer safe via synthetic bootstrap.
        Fits KMeans + IsolationForest on synthetic population if not pre-fitted.
        """
        if not SKLEARN_AVAILABLE:
            logger.warning("sklearn not available — falling back to rule_based")
            return self._rule_based_score(assessment)

        try:
            from assessment.unsupervised_segmentation import (
                UnsupervisedFarmerSegmentation, build_synthetic_population
            )
        except ImportError:
            from .unsupervised_segmentation import (
                UnsupervisedFarmerSegmentation, build_synthetic_population
            )

        if self.unsupervised_segmenter is None:
            self.unsupervised_segmenter = UnsupervisedFarmerSegmentation(n_segments=5)

        if self.unsupervised_segmenter.kmeans is None:
            try:
                pop = build_synthetic_population(assessment, n=60)
                self.unsupervised_segmenter.fit(pop)
                logger.info("Unsupervised segmenter bootstrap-fitted on 60-farm synthetic population")
            except Exception as e:
                logger.warning(f"Segmenter bootstrap failed ({e}) — using rule_based")
                return self._rule_based_score(assessment)

        try:
            seg_input = {
                'cropping_analysis':  assessment['cropping_analysis'],
                'performance_analysis': assessment['performance_analysis'],
                'weather_analysis':   assessment['weather_analysis'],
                'farmer_benefits':    assessment.get('farmer_benefits', {}),
                'crop_cycles':        assessment.get('crop_cycles', {}),
            }
            pred = self.unsupervised_segmenter.predict(seg_input)

            # Convert risk_score (0-100 risk) to credit_score (0-100 creditworthiness)
            credit_score = round(100.0 - pred['risk_score'], 1)
            risk_cat     = self._classify_risk(credit_score)

            return {
                'credit_score':    credit_score,
                'risk_category':   risk_cat,
                'segment':         pred['segment'],
                'anomaly_score_raw': pred.get('anomaly_score_raw'),
                'feature_snapshot': pred.get('feature_snapshot', {}),
                'method':          'unsupervised_v2',
                'confidence':      60.0,  # lower; single-farmer bootstrap
                'assessment_date': datetime.now().isoformat(),
            }
        except Exception as e:
            logger.warning(f"Unsupervised prediction failed ({e}) — rule_based fallback")
            return self._rule_based_score(assessment)

    def _supervised_score(self, assessment: Dict) -> Dict:
        """
        Supervised ML scoring (requires pre-trained model via train()).
        Falls back to rule_based if no model available.
        """
        if self.model is None:
            logger.warning("No trained model available — falling back to rule_based")
            return self._rule_based_score(assessment)

        features = self.extract_features(assessment)
        X_scaled = self.scaler.transform(features)
        prob     = self.model.predict_proba(X_scaled)[0][1]
        score    = round(prob * 100, 1)

        return {
            'credit_score':              score,
            'risk_category':             self._classify_risk(score),
            'probability_of_repayment':  round(prob, 3),
            'method':                    'supervised_ml',
            'confidence':                round(abs(prob - 0.5) * 200, 1),
            'model_version':             'xgboost_v1' if XGBOOST_AVAILABLE else 'rf_v1',
            'assessment_date':           datetime.now().isoformat(),
        }

    def _hybrid_score(self, assessment: Dict) -> Dict:
        """
        Hybrid: rule_based × 0.65 + ML × 0.35.
        Weights: rule_based is always reliable for single-farmer scenarios;
        unsupervised contribution capped at 35% because bootstrap has noise.
        When supervised model available, ML weight rises to 0.50.
        """
        rule = self._rule_based_score(assessment)

        if self.model is not None:
            ml   = self._supervised_score(assessment)
            ml_w = 0.50
        elif SKLEARN_AVAILABLE:
            ml   = self._unsupervised_score(assessment)
            ml_w = 0.35   # conservative: bootstrap-fitted single-farmer
        else:
            return rule

        rb_w    = 1.0 - ml_w
        combined = round(rule['credit_score'] * rb_w + ml['credit_score'] * ml_w, 1)
        combined  = min(100.0, max(0.0, combined))
        risk_cat  = self._classify_risk(combined)

        # Narrative from rule component (has full decomposition)
        narrative = rule.get('scoring_narrative', '')

        return {
            'credit_score':       combined,
            'risk_category':      risk_cat,
            'rule_based_score':   rule['credit_score'],
            'ml_score':           ml['credit_score'],
            'ml_weight':          ml_w,
            'rule_weight':        round(rb_w, 2),
            'component_scores':   rule.get('component_scores', {}),
            'component_weights':  _WEIGHTS.copy(),
            'weak_components':    rule.get('weak_components', []),
            'method':             'hybrid_v3',
            'ml_method':          ml.get('method', 'unknown'),
            'confidence':         round(rule.get('confidence', 80) * rb_w
                                        + ml.get('confidence', 60) * ml_w, 1),
            'assessment_date':    datetime.now().isoformat(),
            'scoring_narrative':  narrative,
        }

    # =========================================================================
    # COMPONENT SCORERS  (v4 — consume Stage 2-5 outputs)
    # =========================================================================

    @staticmethod
    def _score_crop_detection_v4(
        ca: Dict,
        pa: Dict,
    ) -> float:
        """
        Crop detection score (0–100):
          50 pts — cultivation_signal (Stage 2: captures peak, arc, AUC quality)
          30 pts — season consistency (fraction of seasons with crops)
          20 pts — n_complete_cycles normalised to expected 3-year window
        """
        # Cultivation signal (0-100) → up to 50 pts
        signal = float(ca.get('cultivation_signal', 0.0))
        if signal == 0.0:
            # Fallback: derive from performance
            signal = pa.get('average_performance_score', 50.0)
        signal_score = min(50.0, signal * 0.50)

        # Season consistency → up to 30 pts
        total_seasons  = max(float(ca.get('total_seasons_analyzed', 1)), 1)
        seasons_with   = float(ca.get('seasons_with_crops', 0))
        # Also count from seasonal_performance
        sp = pa.get('seasonal_performance', [])
        if sp:
            seasons_with = max(seasons_with, float(len(sp)))
        consistency_score = min(30.0, (seasons_with / total_seasons) * 30.0)

        # n_complete_cycles → up to 20 pts (normalised to 6 expected in 3 years)
        n_complete = pa.get('n_complete_cycles', 0) or len(
            [p for p in sp if not p.get('is_active_cycle')]
        )
        cycle_score = min(20.0, (n_complete / 6.0) * 20.0)

        return round(min(100.0, signal_score + consistency_score + cycle_score), 1)

    @staticmethod
    def _score_weather_safety_v4(wa: Dict) -> float:
        """
        Weather safety score (0–100).
        Stage-4 enrichment: uses cycle_risk_scores (list of per-cycle risks).
        If available, weight cycle-aligned risk more heavily than seasonal.
        """
        cycle_risks = wa.get('cycle_risk_scores', [])
        if cycle_risks:
            # Average of cycle-aligned risk scores (0-100 risk scale)
            avg_cycle_risk = float(np.mean([cr.get('risk_score', 50) for cr in cycle_risks]))
            # Include seasonal as secondary signal
            seasonal_risk  = float(wa.get('weather_risk_score', 50.0))
            combined_risk  = 0.70 * avg_cycle_risk + 0.30 * seasonal_risk
        else:
            combined_risk = float(wa.get('weather_risk_score', 50.0))

        # Penalty for many extreme events
        extreme_events = int(wa.get('total_extreme_events', 0))
        event_penalty  = min(15.0, extreme_events * 2.5)

        safety_score = max(0.0, 100.0 - combined_risk - event_penalty)
        return round(min(100.0, safety_score), 1)

    @staticmethod
    def _score_anomaly_penalty(pa: Dict) -> float:
        """
        Anomaly penalty score (0–100 where 100 = no anomalies).
        Stage-5 enrichment: uses per-cycle anomaly_events with impact ratings.
        """
        sp = pa.get('seasonal_performance', [])
        if not sp:
            return 75.0   # Neutral when no data

        n_high   = sum(len([e for e in p.get('anomaly_events', [])
                            if e.get('impact') == 'HIGH'])   for p in sp)
        n_medium = sum(len([e for e in p.get('anomaly_events', [])
                            if e.get('impact') == 'MEDIUM']) for p in sp)
        n_low    = sum(len([e for e in p.get('anomaly_events', [])
                            if e.get('impact') == 'LOW'])    for p in sp)

        # Penalty: HIGH=8pts, MEDIUM=3pts, LOW=1pt
        penalty = min(100.0, n_high * 8 + n_medium * 3 + n_low * 1)
        return round(max(0.0, 100.0 - penalty), 1)

    @staticmethod
    def _score_cropping_intensity_v4(ca: Dict) -> float:
        """
        Cropping intensity score (0–100).
        Calibrated to cycles-per-year scale (0–3+).
        """
        ci = float(ca.get('cropping_intensity', 0.0))
        if   ci >= 3.0: return 100.0
        elif ci >= 2.0: return 75.0 + (ci - 2.0) * 25.0
        elif ci >= 1.0: return 50.0 + (ci - 1.0) * 25.0
        else:           return ci * 50.0

    @staticmethod
    def _score_govt_benefits(fb: Dict) -> float:
        if not fb:
            return 50.0
        score = 0.0
        if fb.get('pm_kisan_enrolled'):  score += 50.0
        if fb.get('has_crop_insurance'): score += 50.0
        return round(score, 1)

    @staticmethod
    def _classify_risk(score: float) -> str:
        try:
            for category, (lo, hi) in PipelineConfig.RISK_THRESHOLDS.items():
                if lo <= score < hi:
                    return category
        except Exception:
            pass
        if   score >= 70: return 'LOW'
        elif score >= 50: return 'MEDIUM'
        elif score >= 30: return 'HIGH'
        return 'VERY_HIGH'

    @staticmethod
    def _suggest_interest_rate(score: float) -> float:
        if   score >= 80: return 8.5
        elif score >= 70: return 9.5
        elif score >= 60: return 10.5
        elif score >= 50: return 12.0
        elif score >= 40: return 14.0
        else:             return 16.0

    @staticmethod
    def _explain_limit(score: float, intensity: float, area: float, mult: float) -> str:
        parts = []
        if   score >= 80: parts.append("Excellent farming practices")
        elif score >= 70: parts.append("Good farming performance")
        elif score >= 60: parts.append("Average farming quality")
        else:             parts.append("Below-average farming practices")
        if   intensity >= 2.5: parts.append("intensive land use")
        elif intensity >= 2.0: parts.append("efficient double cropping")
        elif intensity >= 1.5: parts.append("moderate cropping intensity")
        else:                  parts.append("low land utilization")
        if   area >= 10: parts.append("large-scale operations")
        elif area >= 5:  parts.append("medium-scale farming")
        else:            parts.append("small-scale farming")
        if   mult > 1.2: parts.append("Strong capacity for credit")
        elif mult > 1.0: parts.append("Good credit capacity")
        else:            parts.append("Limited credit capacity")
        return "; ".join(parts) + "."

    @staticmethod
    def _build_score_narrative(
        total: float,
        risk_category: str,
        scores: Dict,
        weak_components: List[str],
        pa: Dict,
    ) -> str:
        """Plain-English summary of credit score for reports."""
        if   total >= 80: quality = "excellent"
        elif total >= 65: quality = "good"
        elif total >= 50: quality = "moderate"
        elif total >= 35: quality = "below average"
        else:             quality = "poor"

        n_active = pa.get('n_active_cycles', 0)
        active_note = (f" Note: {n_active} crop cycle(s) currently active "
                       f"(income expected at harvest)." if n_active else "")

        sentences = [
            f"Credit assessment: {quality} (score {total:.0f}/100, risk: {risk_category}).{active_note}"
        ]
        if weak_components:
            sentences.append(
                f"Weak areas requiring attention: {', '.join(weak_components)}."
            )
        else:
            sentences.append("All components are performing adequately.")

        # Weather callout if very risky
        ws = scores.get('weather_safety', 100)
        if ws < 40:
            sentences.append(
                "Weather risk is elevated — recent extreme events have significantly impacted "
                "crop health. Consider mandatory crop insurance."
            )

        # Anomaly callout
        ap = scores.get('anomaly_penalty', 100)
        if ap < 50:
            sentences.append(
                "Multiple high-impact vegetation stress events detected across growing seasons "
                "— lender should note increased yield uncertainty."
            )

        return " ".join(sentences)

    # =========================================================================
    # FEATURE EXTRACTION  (ML models)
    # =========================================================================

    def extract_features(self, assessment: Dict) -> pd.DataFrame:
        """
        Extract 16 numerical features for supervised ML.
        Aligned with unsupervised_segmentation._extract_single() feature set
        plus 2 additional supervised-mode features.
        """
        ca = assessment.get('cropping_analysis', {})
        pa = assessment.get('performance_analysis', {})
        wa = assessment.get('weather_analysis', {})
        fb = assessment.get('farmer_benefits', {}) or {}
        cc = assessment.get('crop_cycles', {}) or {}

        sp = pa.get('seasonal_performance', [])
        n_anom_total = sum(len(p.get('anomaly_events', [])) for p in sp)
        n_high_total = sum(
            len([e for e in p.get('anomaly_events', []) if e.get('impact') == 'HIGH'])
            for p in sp
        )

        cycle_risks = wa.get('cycle_risk_scores', [])
        avg_cycle_risk = float(np.mean([cr.get('risk_score', 50) for cr in cycle_risks])) \
            if cycle_risks else float(wa.get('weather_risk_score', 50))

        util = cc.get('utilization_metrics', {}) or {}

        feats = {
            'cropping_intensity':       float(ca.get('cropping_intensity', 0)),
            'cultivation_signal':       float(ca.get('cultivation_signal', 50)),
            'n_complete_cycles':        float(pa.get('n_complete_cycles', 0)),
            'n_active_cycles':          float(pa.get('n_active_cycles', 0)),
            'avg_performance_score':    float(pa.get('average_performance_score', 50)),
            'avg_yield_score':          float(pa.get('average_yield_score', 50)),
            'n_anomalies_total':        float(n_anom_total),
            'n_high_impact_anomalies':  float(n_high_total),
            'weather_risk_score':       float(wa.get('weather_risk_score', 50)),
            'total_extreme_events':     float(wa.get('total_extreme_events', 0)),
            'avg_cycle_weather_risk':   avg_cycle_risk,
            'land_utilization_index':   float(util.get('land_utilization_index', 0)) * 100,
            'seasons_with_crops':       float(ca.get('seasons_with_crops', 0)),
            'unique_crops':             float(len(ca.get('crops_detected', {}))),
            'pm_kisan':                 1.0 if fb.get('pm_kisan_enrolled') else 0.0,
            'insurance':                1.0 if fb.get('has_crop_insurance') else 0.0,
        }

        return pd.DataFrame([feats])

    # =========================================================================
    # SUPERVISED TRAINING
    # =========================================================================

    def train(
        self,
        training_assessments: List[Dict],
        outcomes: List[int],  # 1=repaid, 0=defaulted
    ):
        """
        Train supervised ML model on historical loan data.
        Args:
            training_assessments: list of assessment dicts
            outcomes: loan outcome labels
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError("sklearn required for ML training")

        logger.info(f"Training supervised model on {len(training_assessments)} examples")
        X   = pd.concat([self.extract_features(a) for a in training_assessments],
                        ignore_index=True)
        y   = np.array(outcomes)

        self.feature_names = list(X.columns)
        self.scaler        = StandardScaler()
        X_scaled           = self.scaler.fit_transform(X)

        if XGBOOST_AVAILABLE:
            self.model = xgb.XGBClassifier(
                max_depth=6, learning_rate=0.1,
                n_estimators=100, objective='binary:logistic', random_state=42,
            )
        else:
            self.model = RandomForestClassifier(
                n_estimators=100, max_depth=6, random_state=42,
            )

        self.model.fit(X_scaled, y)

        if hasattr(self.model, 'feature_importances_'):
            importance = sorted(
                zip(self.feature_names, self.model.feature_importances_),
                key=lambda x: -x[1]
            )
            logger.info("Top 5 features:")
            for name, imp in importance[:5]:
                logger.info(f"  {name}: {imp:.3f}")

        logger.info("Supervised model training complete")


__all__ = ['AdvancedCreditScorer']