"""
Counterfactual Engine — VERSION 2.0
=====================================
Generates "what-if" credit score projections aligned with Stage 6 scoring.

Stage 7 update:
  - Weight table updated to match Stage 6 AdvancedCreditScorer (35/25/15/8/7/5/5)
  - Scenarios now target Stage 2-5 root causes:
    cultivation_signal, anomaly events, cycle completion rate, cycle-aligned weather risk
  - Seasonal language (Kharif/Rabi) replaced with intensity-based language
  - Active cycle awareness: generates a "complete active cycles" scenario
  - Each scenario includes actionable agronomy guidance

Usage:
    engine = CounterfactualEngine()
    counterfactuals = engine.generate(assessment)
"""

import logging
from typing import Dict, List

from config import PipelineConfig

logger = logging.getLogger(__name__)

# Must match AdvancedCreditScorer._WEIGHTS  (Stage 6)
_WEIGHTS = {
    'crop_detection':    35,
    'crop_performance':  25,
    'yield_potential':   15,
    'weather_safety':     8,
    'anomaly_penalty':    7,
    'cropping_intensity': 5,
    'govt_benefits':      5,
}


class CounterfactualEngine:
    """
    Generates actionable "what-if" score improvement scenarios.
    Works entirely without additional models or API calls.
    """

    def generate(self, assessment: Dict) -> Dict:
        """
        Generate top-3 ranked counterfactual scenarios.

        Args:
            assessment: Full pipeline assessment dict

        Returns:
            Dict with current_score, target_score, scenarios (list),
            projected_score_with_all_improvements, and improvement_roadmap.
        """
        cr              = assessment.get('credit_assessment', {})
        current_score   = float(cr.get('credit_score', 50))
        component_scores = cr.get('component_scores', {})
        weak            = cr.get('weak_components', [])

        scenarios = self._generate_scenarios(assessment, component_scores, weak)
        scenarios.sort(key=lambda s: s['score_gain'], reverse=True)
        top3 = scenarios[:3]

        cumulative = current_score
        for sc in top3:
            cumulative += sc['score_gain']
        cumulative = round(min(100.0, cumulative), 1)

        logger.info(
            f"Counterfactuals: current={current_score}  "
            f"top_gain={top3[0]['score_gain'] if top3 else 0}  "
            f"cumulative={cumulative}"
        )

        return {
            'current_score':                    current_score,
            'current_risk_category':            cr.get('risk_category', 'UNKNOWN'),
            'scenarios':                        top3,
            'projected_score_all_improvements': cumulative,
            'projected_risk_all_improvements':  self._risk_from_score(cumulative),
            'improvement_roadmap':              self._build_roadmap(top3),
        }

    # =========================================================================
    # SCENARIO GENERATORS
    # =========================================================================

    def _generate_scenarios(
        self,
        assessment: Dict,
        component_scores: Dict,
        weak: List[str],
    ) -> List[Dict]:

        scenarios = []
        ca = assessment.get('cropping_analysis', {})
        pa = assessment.get('performance_analysis', {})
        wa = assessment.get('weather_analysis',    {})
        fb = assessment.get('farmer_benefits', {}) or {}

        ci          = float(ca.get('cropping_intensity', 0.5))
        signal      = float(ca.get('cultivation_signal', 50.0))
        crop_perf   = float(pa.get('average_performance_score', 50))
        yield_score = float(pa.get('average_yield_score',       40))
        n_active    = int(pa.get('n_active_cycles', 0))
        n_complete  = int(pa.get('n_complete_cycles', 0))
        weather_risk = float(wa.get('weather_risk_score', 50))

        sp = pa.get('seasonal_performance', [])
        n_high = sum(
            len([e for e in p.get('anomaly_events', []) if e.get('impact') == 'HIGH'])
            for p in sp
        )
        cycle_risks = wa.get('cycle_risk_scores', [])

        # ── S1: Increase Cropping Intensity ───────────────────────────────────
        if ci < 2.0:
            target_ci = min(2.0, ci + 0.75)
            cur_score = component_scores.get('cropping_intensity',
                        min(100, ci * 50))
            # From _score_cropping_intensity_v4
            if   target_ci >= 3.0: new_score = 100.0
            elif target_ci >= 2.0: new_score = 75.0 + (target_ci - 2.0) * 25.0
            elif target_ci >= 1.0: new_score = 50.0 + (target_ci - 1.0) * 25.0
            else:                  new_score = target_ci * 50.0
            gain = ((new_score - cur_score) / 100) * _WEIGHTS['cropping_intensity']
            scenarios.append({
                'id':            'increase_cropping_intensity',
                'title':         'Increase Crop Cycles per Year',
                'description':   (
                    f"Your field is currently cultivated at {ci:.1f} cycles/year. "
                    f"Adding one more crop cycle per year (target: {target_ci:.1f}) "
                    "improves your land utilization score significantly."
                ),
                'actions': [
                    "Plan a short-duration crop (45–75 days) in the gap between main crops.",
                    "Consider inter-cropping or relay cropping to utilize fallow periods.",
                    "Use shorter-duration, high-yield variety seeds to fit an extra cycle.",
                ],
                'change_needed':             f"Crop cycles/year: {ci:.1f} → {target_ci:.1f}",
                'score_gain':                round(gain, 1),
                'projected_component_score': round(new_score, 1),
                'component':                 'cropping_intensity',
                'feasibility':               'HIGH',
                'timeframe':                 '1 agricultural season (3–5 months)',
            })

        # ── S2: Improve Cultivation Signal / Crop Health ──────────────────────
        if signal < 70 or crop_perf < 65:
            target_signal = min(85.0, signal + 20)
            target_perf   = min(100.0, crop_perf + 20)
            # Crop detection uses signal × 0.50 for up to 50 pts
            cur_det_score  = component_scores.get('crop_detection',  signal * 0.5)
            new_det_score  = min(50, target_signal * 0.5)
            gain_det       = ((new_det_score - cur_det_score) / 50) * _WEIGHTS['crop_detection'] * 0.6
            gain_perf      = ((target_perf - crop_perf) / 100) * _WEIGHTS['crop_performance']
            total_gain     = gain_det + gain_perf
            scenarios.append({
                'id':            'improve_cultivation_signal',
                'title':         'Improve Crop Health and Vegetation Density',
                'description':   (
                    f"Your cultivation signal is {signal:.0f}/100 and crop health is "
                    f"{crop_perf:.1f}/100. Stronger, healthier vegetation improves both "
                    "the crop detection score and performance score."
                ),
                'actions': [
                    "Schedule irrigation based on crop growth stage (vegetative/flowering/grain fill).",
                    "Apply balanced NPK fertilizer aligned with soil test recommendations.",
                    "Use drip/sprinkler irrigation to avoid water stress during peak growth.",
                    "Monitor for pest/disease with weekly field scouting during vegetative stage.",
                ],
                'change_needed':             f"Cultivation signal: {signal:.0f} → {target_signal:.0f}, "
                                             f"crop health: {crop_perf:.1f} → {target_perf:.1f}",
                'score_gain':                round(total_gain, 1),
                'projected_component_score': round(target_perf, 1),
                'component':                 'crop_detection + crop_performance',
                'feasibility':               'MEDIUM',
                'timeframe':                 '1–2 growing seasons',
            })

        # ── S3: Reduce Anomaly Events (HIGH-impact stress) ────────────────────
        if n_high >= 2:
            target_high = max(0, n_high - 2)
            ph = float(getattr(PipelineConfig, "CREDIT_ANOMALY_PENALTY_HIGH", 2.5))
            cur_ap     = component_scores.get('anomaly_penalty',
                         max(0.0, 100.0 - n_high * ph))
            new_ap     = min(100.0, max(0.0, 100.0 - target_high * ph))
            gain       = ((new_ap - cur_ap) / 100) * _WEIGHTS['anomaly_penalty']
            scenarios.append({
                'id':            'reduce_stress_events',
                'title':         'Reduce High-Impact Crop Stress Events',
                'description':   (
                    f"Satellite data detected {n_high} high-impact stress events across "
                    "your growing seasons (likely drought, flooding, or disease pressure). "
                    "Reducing these improves both anomaly penalty and yield scores."
                ),
                'actions': [
                    "Install soil-moisture sensors or tensiometers to prevent drought stress.",
                    "Build bunds/field borders for flood protection during heavy rain.",
                    "Apply fungicide/pesticide preventatively at crop-stage-critical periods.",
                    "Use weather advisory services (e.g., DAMINI, Meghdoot IMD apps) for timely action.",
                ],
                'change_needed':             f"High-impact events: {n_high} → {target_high}",
                'score_gain':                round(gain, 1),
                'projected_component_score': round(new_ap, 1),
                'component':                 'anomaly_penalty',
                'feasibility':               'MEDIUM',
                'timeframe':                 '1 season with infrastructure/practices change',
            })

        # ── S4: Improve Yield Potential ───────────────────────────────────────
        if yield_score < 60:
            target_yield = min(100.0, yield_score + 25)
            gain = ((target_yield - yield_score) / 100) * _WEIGHTS['yield_potential']
            scenarios.append({
                'id':            'improve_yield_potential',
                'title':         'Increase Crop Yield Potential',
                'description':   (
                    f"Your average yield potential score is {yield_score:.1f}/100. "
                    f"Adopting better agronomic inputs and precision practices can "
                    f"target {target_yield:.1f}/100 in the next 2–3 seasons."
                ),
                'actions': [
                    "Use certified high-yielding variety (HYV) seeds from authorised dealers.",
                    "Conduct soil testing every 2 years and apply required micro-nutrients.",
                    "Time sowing within the optimal window for your crop variety.",
                    "Apply zinc, boron micro-nutrients if soil test indicates deficiency.",
                ],
                'change_needed':             f"Yield score: {yield_score:.1f} → {target_yield:.1f}",
                'score_gain':                round(gain, 1),
                'projected_component_score': round(target_yield, 1),
                'component':                 'yield_potential',
                'feasibility':               'MEDIUM',
                'timeframe':                 '2–3 seasons',
            })

        # ── S5: Weather Risk Mitigation ───────────────────────────────────────
        if weather_risk > 50 or (cycle_risks and
           any(cr.get('risk_score', 0) > 60 for cr in cycle_risks)):
            new_risk = max(30.0, weather_risk - 20)
            cur_ws   = component_scores.get('weather_safety', max(0, 100 - weather_risk))
            new_ws   = max(0.0, 100.0 - new_risk)
            gain     = ((new_ws - cur_ws) / 100) * _WEIGHTS['weather_safety']
            scenarios.append({
                'id':            'reduce_weather_risk',
                'title':         'Adopt Weather-Adaptive Farming Practices',
                'description':   (
                    f"Your weather risk score is {weather_risk:.1f}/100. "
                    "Extreme weather events at critical crop stages (flowering, grain-fill) "
                    "significantly hurt your credit score."
                ),
                'actions': [
                    "Enroll in PMFBY crop insurance before sowing — mandatory for risk coverage.",
                    "Install drip/micro-sprinkler irrigation for drought resilience.",
                    "Adopt zero-tillage or minimum-tillage to preserve soil moisture.",
                    "Choose weather-tolerant or climate-smart crop varieties.",
                ],
                'change_needed':             f"Weather risk: {weather_risk:.1f} → {new_risk:.1f}",
                'score_gain':                round(gain, 1),
                'projected_component_score': round(new_ws, 1),
                'component':                 'weather_safety',
                'feasibility':               'MEDIUM',
                'timeframe':                 '1–3 seasons with infrastructure investment',
            })

        # ── S6: Government Scheme Enrollment ─────────────────────────────────
        pm_kisan  = bool(fb.get('pm_kisan_enrolled'))
        insurance = bool(fb.get('has_crop_insurance'))
        if not pm_kisan or not insurance:
            missing = []
            if not pm_kisan:  missing.append('PM-KISAN')
            if not insurance: missing.append('PMFBY Crop Insurance')
            cur_gb  = component_scores.get('govt_benefits',
                      (50 if pm_kisan else 0) + (50 if insurance else 0))
            gain    = ((100 - cur_gb) / 100) * _WEIGHTS['govt_benefits']
            scenarios.append({
                'id':            'govt_scheme_enrollment',
                'title':         f"Enroll in Government Schemes: {', '.join(missing)}",
                'description':   (
                    f"You are not enrolled in: {', '.join(missing)}. "
                    "Government scheme participation reduces lender risk perception "
                    "and directly improves your credit profile."
                ),
                'actions': [
                    "Visit your nearest bank or CSC center to register for PM-KISAN.",
                    "Enroll in PMFBY through your bank or PM-FASAL BIMA app before sowing.",
                    "Ensure Aadhaar-linked bank account for scheme benefits.",
                ],
                'change_needed':             f"Enroll in: {', '.join(missing)}",
                'score_gain':                round(gain, 1),
                'projected_component_score': 100.0,
                'component':                 'govt_benefits',
                'feasibility':               'HIGH',
                'timeframe':                 'Immediate (2–4 weeks)',
            })

        # ── S7: Complete Active Cycles ────────────────────────────────────────
        if n_active > 0:
            # Score gain = score would rise as active cycles complete successfully
            estimated_gain = round(n_active * 1.5, 1)
            scenarios.append({
                'id':            'complete_active_cycles',
                'title':         'Successfully Complete Current Growing Cycles',
                'description':   (
                    f"You currently have {n_active} active crop cycle(s) that are "
                    "still growing. Successfully completing these will improve your "
                    "historical performance record and credit score."
                ),
                'actions': [
                    "Maintain scheduled irrigation through the grain-fill/maturation phase.",
                    "Apply post-flowering fungicide protection to secure yield.",
                    "Plan timely harvest to prevent post-maturity losses.",
                ],
                'change_needed':             f"Complete {n_active} active cycle(s) with good performance",
                'score_gain':                estimated_gain,
                'projected_component_score': crop_perf + 5,
                'component':                 'crop_performance',
                'feasibility':               'HIGH',
                'timeframe':                 'Current season (in-progress)',
            })

        return scenarios

    # =========================================================================
    # ROADMAP BUILDER
    # =========================================================================

    @staticmethod
    def _build_roadmap(scenarios: List[Dict]) -> List[Dict]:
        """Convert top-3 scenarios into a time-ordered improvement roadmap."""
        order = {'Immediate': 0, '1': 1, '2': 2, '3': 3}
        def _order(s: Dict) -> int:
            tf = s.get('timeframe', '')
            for k, v in order.items():
                if k in tf:
                    return v
            return 99

        sorted_sc = sorted(scenarios, key=_order)
        roadmap   = []
        for i, sc in enumerate(sorted_sc, 1):
            roadmap.append({
                'step':        i,
                'action':      sc['title'],
                'timeframe':   sc.get('timeframe', ''),
                'score_gain':  sc['score_gain'],
                'feasibility': sc.get('feasibility', ''),
            })
        return roadmap

    @staticmethod
    def _risk_from_score(score: float) -> str:
        if score >= 70: return 'LOW'
        if score >= 50: return 'MEDIUM'
        if score >= 30: return 'HIGH'
        return 'VERY_HIGH'
