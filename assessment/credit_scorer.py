# """
# Credit Risk Scorer
# ==================
# Calculates credit score from satellite-derived agricultural metrics.

# VERSION 3.0 — Major Updates:
# 1. Crop Detection score (35%):
#    - Uses temporal pattern quality metrics (arc_score, frac_above_thresh,
#      ndvi_rise) in addition to season consistency and intensity
#    - Cross-season crops scored correctly (not double-penalised)

# 2. Cropping Intensity score (12%):
#    - Tiered scoring updated for 2-season system (max intensity = 1.0)
#    - Cross-season long-duration crops correctly counted

# 3. Weather Risk (5%):
#    - Now uses crop-stage-critical event penalty from weather_analyzer
#    - Inverted as before: weather_safety = 100 - weather_risk

# 4. Credit limit calculation:
#    - Intensity multiplier recalibrated for 2-season max
#    - High-value crop list expanded to 12 crops

# 5. Conditions:
#    - More specific conditions based on which components are weak
# """

# from typing import Dict, List, Optional
# import logging

# from config import PipelineConfig

# logger = logging.getLogger(__name__)


# class CreditScorer:
#     """Calculates credit risk score using weighted satellite-derived components."""

#     def __init__(self, verbose: bool = True):
#         self.verbose = verbose
#         self.weights = PipelineConfig.CREDIT_WEIGHTS
#         logger.info("✔ CreditScorer v3.0 initialized")
#         logger.info(f"  Weights: {self.weights}")

#     # =========================================================================
#     # MAIN SCORING
#     # =========================================================================

#     def calculate_credit_score(
#         self,
#         cropping_analysis:   Dict,
#         performance_analysis: Dict,
#         weather_analysis:    Dict,
#         farmer_benefits:     Optional[Dict] = None,
#     ) -> Dict:
#         """
#         Compute final credit score from all pipeline components.

#         Args:
#             cropping_analysis:   from CropDetector.analyze_cropping_pattern()
#             performance_analysis: from CropPerformanceAnalyzer.analyze_performance()
#             weather_analysis:    from WeatherAnalyzer.analyze_seasonal_weather()
#             farmer_benefits:     dict with pm_kisan_enrolled, has_crop_insurance

#         Returns:
#             Dict: credit_score (0–100), risk_category, component_scores,
#                   weak_components, calculation_method, assessment_date
#         """
#         logger.info(f"\n{'='*70}")
#         logger.info("CALCULATING CREDIT SCORE")
#         logger.info(f"{'='*70}")

#         scores: Dict[str, float] = {}

#         # ── 1. Crop Detection (35%) ────────────────────────────────────────
#         scores['crop_detection'] = self._score_crop_detection(cropping_analysis)
#         logger.info(f"  Crop Detection:    {scores['crop_detection']:.1f}/100")

#         # ── 2. Crop Performance (30%) ──────────────────────────────────────
#         scores['crop_performance'] = performance_analysis.get(
#             'average_performance_score', 50.0
#         )
#         logger.info(f"  Crop Performance:  {scores['crop_performance']:.1f}/100")

#         # ── 3. Yield Potential (15%) ───────────────────────────────────────
#         scores['yield_potential'] = performance_analysis.get(
#             'average_yield_score', 50.0
#         )
#         logger.info(f"  Yield Potential:   {scores['yield_potential']:.1f}/100")

#         # ── 4. Cropping Intensity (12%) ────────────────────────────────────
#         scores['cropping_intensity'] = self._score_cropping_intensity(cropping_analysis)
#         logger.info(f"  Cropping Intensity:{scores['cropping_intensity']:.1f}/100")

#         # ── 5. Weather Safety (5%) ─────────────────────────────────────────
#         weather_risk = weather_analysis.get('weather_risk_score', 50.0)
#         scores['weather_risk'] = max(0.0, 100.0 - weather_risk)
#         logger.info(f"  Weather Safety:    {scores['weather_risk']:.1f}/100"
#                     f"  (risk={weather_risk:.1f})")

#         # ── 6. Govt Benefits (3%) ──────────────────────────────────────────
#         scores['govt_benefits'] = self._score_govt_benefits(farmer_benefits)
#         logger.info(f"  Govt Benefits:     {scores['govt_benefits']:.1f}/100")

#         # ── Weighted total ─────────────────────────────────────────────────
#         total = sum(
#             scores[k] * (self.weights[k] / 100)
#             for k in scores
#         )
#         total = round(min(100.0, max(0.0, total)), 1)

#         risk_category  = self._classify_risk(total)
#         weak_components = [k for k, v in scores.items() if v < 50.0]

#         logger.info(f"\n  ► Total Credit Score: {total}/100")
#         logger.info(f"  ► Risk Category:      {risk_category}")
#         if weak_components:
#             logger.info(f"  ► Weak components:    {', '.join(weak_components)}")

#         return {
#             'credit_score':      total,
#             'risk_category':     risk_category,
#             'component_scores':  {k: round(v, 1) for k, v in scores.items()},
#             'weak_components':   weak_components,
#             'calculation_method': 'satellite_temporal_v3',
#             'assessment_date':   __import__('datetime').datetime.now().isoformat(),
#         }



#     def calculate_credit_limit(
#         self,
#         credit_score: float,
#         field_area_ha: float,
#         cropping_analysis: Dict,
#         crop_detected: Dict = None,
#         farmer_benefits: Dict = None
#     ) -> Dict:
#         """
#         Calculate recommended credit limit based on farming quality score.
        
#         This is for FARMING QUALITY assessment, not financial creditworthiness.
#         The limit reflects farming capacity and efficiency.
        
#         Args:
#             credit_score: Farming quality score (0-100)
#             field_area_ha: Field size in hectares
#             cropping_analysis: Cropping patterns and intensity data
#             crop_detected: Detected crops information
#             farmer_benefits: Government benefits (PM-KISAN, insurance)
            
#         Returns:
#             Dictionary with credit limit and components
#         """
#         # Extract cropping intensity
#         cropping_intensity = cropping_analysis.get('cropping_intensity', 1.0)
        
#         # Base limit per hectare based on farming quality score
#         if credit_score >= 80:
#             base_per_ha = 15000  # Excellent farmer
#             risk_level = 'LOW'
#         elif credit_score >= 70:
#             base_per_ha = 12000  # Good farmer
#             risk_level = 'MEDIUM_LOW'
#         elif credit_score >= 60:
#             base_per_ha = 10000  # Average farmer
#             risk_level = 'MEDIUM'
#         elif credit_score >= 50:
#             base_per_ha = 7500   # Below average
#             risk_level = 'MEDIUM_HIGH'
#         elif credit_score >= 40:
#             base_per_ha = 5000   # Poor farmer
#             risk_level = 'HIGH'
#         else:
#             base_per_ha = 3000   # Very poor
#             risk_level = 'VERY_HIGH'
        
#         # Cropping intensity multiplier
#         # More crops per year = better land utilization = higher capacity
#         if cropping_intensity >= 2.5:
#             intensity_mult = 1.30  # Intensive farming
#         elif cropping_intensity >= 2.0:
#             intensity_mult = 1.20  # Double cropping
#         elif cropping_intensity >= 1.5:
#             intensity_mult = 1.10  # Moderate intensity
#         elif cropping_intensity >= 1.0:
#             intensity_mult = 1.00  # Single cropping
#         else:
#             intensity_mult = 0.85  # Low intensity
        
#         # Area multiplier (economies of scale)
#         if field_area_ha >= 10:
#             area_mult = 1.15  # Large farm
#         elif field_area_ha >= 5:
#             area_mult = 1.10  # Medium farm
#         elif field_area_ha >= 2:
#             area_mult = 1.05  # Small-medium farm
#         elif field_area_ha >= 1:
#             area_mult = 1.00  # Small farm
#         else:
#             area_mult = 0.90  # Very small farm
        
#         # Crop value multiplier
#         # High-value crops can support higher credit
#         high_value_crops = [
#             'Sugarcane', 'Cotton', 'Banana', 'Potato', 'Tomato',
#             'Onion', 'Garlic', 'Grapes', 'Pomegranate', 'Mango'
#         ]
        
#         crop_mult = 1.00
#         if crop_detected and 'dominant_crop' in crop_detected:
#             dominant_crop = crop_detected['dominant_crop']
#             if dominant_crop in high_value_crops:
#                 crop_mult = 1.15
#                 logger.debug(f"High-value crop detected: {dominant_crop}, multiplier: 1.15")
        
#         # Government benefits multiplier (safety net)
#         benefits_mult = 1.00
#         if farmer_benefits:
#             if farmer_benefits.get('pm_kisan_enrolled'):
#                 benefits_mult += 0.05  # PM-KISAN provides safety net
#             if farmer_benefits.get('has_insurance'):
#                 benefits_mult += 0.05  # Crop insurance reduces risk
        
#         # Calculate base limit
#         base_limit = base_per_ha * field_area_ha
        
#         # Apply all multipliers
#         total_multiplier = intensity_mult * area_mult * crop_mult * benefits_mult
#         final_limit = base_limit * total_multiplier
        
#         # Round to nearest 1000
#         final_limit = round(final_limit / 1000) * 1000
        
#         # Suggested interest rate (based on farming quality)
#         interest_rate = self._suggest_interest_rate(credit_score)
        
#         # Suggested tenure (based on cropping intensity)
#         if cropping_intensity >= 2.0:
#             tenure_months = 6  # Short-term for intensive farmers
#         else:
#             tenure_months = 12  # Annual for traditional farmers
        
#         return {
#             'recommended_limit': final_limit,
#             'base_limit_per_ha': base_per_ha,
#             'total_base_limit': base_limit,
#             'field_area_ha': field_area_ha,
#             'risk_level': risk_level,
#             'multipliers': {
#                 'intensity': intensity_mult,
#                 'area': area_mult,
#                 'crop_value': crop_mult,
#                 'benefits': benefits_mult,
#                 'total': total_multiplier
#             },
#             'components': {
#                 'base': base_limit,
#                 'after_intensity': base_limit * intensity_mult,
#                 'after_area': base_limit * intensity_mult * area_mult,
#                 'after_crop': base_limit * intensity_mult * area_mult * crop_mult,
#                 'final': final_limit
#             },
#             'recommendations': {
#                 'interest_rate': interest_rate,
#                 'tenure_months': tenure_months,
#                 'repayment_type': 'lump_sum' if tenure_months <= 6 else 'installments'
#             },
#             'reasoning': self._explain_limit(
#                 credit_score,
#                 cropping_intensity,
#                 field_area_ha,
#                 total_multiplier
#             )
#         }
    
#     def _suggest_interest_rate(self, credit_score: float) -> float:
#         """
#         Suggest interest rate based on farming quality score.
        
#         Better farmers get better rates (they're more reliable).
        
#         Args:
#             credit_score: Farming quality score (0-100)
            
#         Returns:
#             Suggested annual interest rate (%)
#         """
#         if credit_score >= 80:
#             return 8.5   # Excellent farmer rate
#         elif credit_score >= 70:
#             return 9.5   # Good farmer rate
#         elif credit_score >= 60:
#             return 10.5  # Average farmer rate
#         elif credit_score >= 50:
#             return 12.0  # Below average rate
#         elif credit_score >= 40:
#             return 14.0  # Poor farmer rate
#         else:
#             return 16.0  # Very high risk rate
    
#     def _explain_limit(
#         self,
#         credit_score: float,
#         intensity: float,
#         area: float,
#         multiplier: float
#     ) -> str:
#         """
#         Generate human-readable explanation of credit limit.
        
#         Args:
#             credit_score: Farming quality score
#             intensity: Cropping intensity
#             area: Field area
#             multiplier: Total multiplier applied
            
#         Returns:
#             Explanation string
#         """
#         parts = []
        
#         # Score-based reasoning
#         if credit_score >= 80:
#             parts.append("Excellent farming practices")
#         elif credit_score >= 70:
#             parts.append("Good farming performance")
#         elif credit_score >= 60:
#             parts.append("Average farming quality")
#         else:
#             parts.append("Below-average farming practices")
        
#         # Intensity-based reasoning
#         if intensity >= 2.5:
#             parts.append("intensive land use")
#         elif intensity >= 2.0:
#             parts.append("efficient double cropping")
#         elif intensity >= 1.5:
#             parts.append("moderate cropping intensity")
#         else:
#             parts.append("low land utilization")
        
#         # Area-based reasoning
#         if area >= 10:
#             parts.append("large-scale operations")
#         elif area >= 5:
#             parts.append("medium-scale farming")
#         else:
#             parts.append("small-scale farming")
        
#         # Overall
#         if multiplier > 1.2:
#             parts.append("Strong capacity for credit")
#         elif multiplier > 1.0:
#             parts.append("Good credit capacity")
#         else:
#             parts.append("Limited credit capacity")
        
#         return "; ".join(parts) + "."
 


#     # =========================================================================
#     # COMPONENT SCORERS
#     # =========================================================================

#     def _score_crop_detection(self, ca: Dict) -> float:
#         """
#         Crop detection score (0–100) — 3 sub-components:

#         A. Season consistency (50 pts):
#            Fraction of seasons where crops were detected × 50.
#            Cross-season crops counted as 1 not 2.

#         B. Temporal pattern quality (30 pts):
#            Average of arc_score, frac_above_thresh, ndvi_rise_ratio
#            across all detected seasons.

#         C. Crop diversity (20 pts):
#            Up to 20 pts for detecting multiple distinct crop types
#            across the analysis window (shows active, planned farming).
#         """
#         seasons_with = ca.get('seasons_with_crops',       0)
#         total_seasons = ca.get('total_seasons_analyzed',  1)
#         season_results = ca.get('season_results',          [])
#         crops_detected = ca.get('crops_detected',          {})

#         # A. Season consistency
#         consistency = (seasons_with / max(total_seasons, 1)) * 50.0

#         # B. Temporal pattern quality from detected seasons
#         detected_results = [r for r in season_results if r.get('crop_detected')]
#         if detected_results:
#             arc_scores   = [r.get('arc_score',          0.5) for r in detected_results]
#             frac_scores  = [r.get('frac_above_thresh',  0.5) for r in detected_results]
#             rise_scores  = [min(1.0, r.get('ndvi_rise', 0.0) / 0.30)
#                             for r in detected_results]   # normalize rise to 0-1
#             pattern_q    = (
#                 (sum(arc_scores)  / len(arc_scores))  * 0.40 +
#                 (sum(frac_scores) / len(frac_scores)) * 0.35 +
#                 (sum(rise_scores) / len(rise_scores)) * 0.25
#             )
#             pattern_score = pattern_q * 30.0
#         else:
#             pattern_score = 0.0

#         # C. Crop diversity
#         n_crops       = len(crops_detected)
#         diversity     = min(20.0, n_crops * 7.0)   # 7 pts per distinct crop type, max 20

#         total = round(min(100.0, consistency + pattern_score + diversity), 1)
#         return total

#     def _score_cropping_intensity(self, ca: Dict) -> float:
#         """
#         Score cropping intensity for the 2-season system.

#         Intensity range in this system:
#           0.0  — no crops detected at all
#           0.5  — only one season cultivated each year (kharif OR rabi)
#           1.0  — both seasons cultivated every year (maximum for annual crops)

#         Scoring curve:
#           ≥1.0 → 100 pts   (both seasons every year)
#           0.75 → 75 pts
#           0.5  → 50 pts    (one season per year)
#           <0.5 → linear, 0–50 pts
#         """
#         intensity = ca.get('cropping_intensity', 0.0)

#         if intensity >= 1.0:
#             score = 100.0
#         elif intensity >= 0.5:
#             # Linear interpolation 50–100 over range 0.5–1.0
#             score = 50.0 + (intensity - 0.5) / 0.5 * 50.0
#         else:
#             score = intensity / 0.5 * 50.0

#         return round(min(100.0, max(0.0, score)), 1)

#     def _score_govt_benefits(self, benefits: Optional[Dict]) -> float:
#         """PM-KISAN enrollment + crop insurance = 100 pts."""
#         if not benefits:
#             return 50.0   # neutral when data not provided
#         score = 0.0
#         if benefits.get('pm_kisan_enrolled'):
#             score += 50.0
#         if benefits.get('has_crop_insurance'):
#             score += 50.0
#         return round(score, 1)

#     def _classify_risk(self, score: float) -> str:
#         for category, (lo, hi) in PipelineConfig.RISK_THRESHOLDS.items():
#             if lo <= score < hi:
#                 return category
#         return 'VERY_HIGH'

#     # =========================================================================
#     # CREDIT LIMIT CALCULATION
#     # =========================================================================

#     def calculate_credit_limit(
#         self,
#         credit_score:      float,
#         risk_category:     str,
#         field_area_ha:     float,
#         cropping_analysis: Dict,
#     ) -> Dict:
#         """
#         Compute recommended credit limit, interest rate, and conditions.

#         Credit limit = base_per_ha × intensity_multiplier × crop_multiplier × area

#         Intensity multiplier (for 2-season system):
#           intensity 1.0 → ×1.20  (both seasons, max multiplier)
#           intensity 0.5 → ×1.00  (one season, neutral)
#           intensity 0.0 → ×0.80  (no crops, penalised)
#         """
#         base = PipelineConfig.CREDIT_LIMITS_PER_HA[risk_category]

#         # Intensity multiplier recalibrated for 2-season max (was 0.5 baseline)
#         intensity = cropping_analysis.get('cropping_intensity', 0.5)
#         intensity_mult = 0.80 + (intensity / 1.0) * 0.40   # 0.80 – 1.20 range

#         # High-value crop multiplier
#         crops_det  = cropping_analysis.get('crops_detected', {})
#         hv_mult    = (PipelineConfig.HIGH_VALUE_MULTIPLIER
#                       if any(c in crops_det for c in PipelineConfig.HIGH_VALUE_CROPS)
#                       else 1.0)

#         limit_per_ha = base * intensity_mult * hv_mult
#         total_limit  = limit_per_ha * max(field_area_ha, 0.1)

#         conditions = self._generate_conditions(
#             risk_category, cropping_analysis
#         )

#         return {
#             'recommended_credit_limit': round(total_limit, 2),
#             'limit_per_hectare':        round(limit_per_ha, 2),
#             'interest_rate':            PipelineConfig.INTEREST_RATES[risk_category],
#             'repayment_period_months':  PipelineConfig.REPAYMENT_PERIODS[risk_category],
#             'collateral_required':      risk_category in ('HIGH', 'VERY_HIGH'),
#             'intensity_multiplier':     round(intensity_mult, 3),
#             'high_value_crop_bonus':    hv_mult > 1.0,
#             'conditions':               conditions,
#         }

#     def _generate_conditions(
#         self,
#         risk_category:     str,
#         cropping_analysis: Dict,
#     ) -> List[str]:
#         conditions = []
#         intensity  = cropping_analysis.get('cropping_intensity', 0.0)
#         seasons_with = cropping_analysis.get('seasons_with_crops', 0)
#         total        = cropping_analysis.get('total_seasons_analyzed', 1)

#         if risk_category == 'LOW':
#             conditions.append("Standard loan terms apply")
#             conditions.append("Quarterly satellite monitoring")
#             if intensity >= 1.0:
#                 conditions.append("Eligible for enhanced credit limit review after 2 seasons")

#         elif risk_category == 'MEDIUM':
#             conditions.append("Crop insurance strongly recommended")
#             conditions.append("Bi-monthly satellite monitoring")
#             if intensity < 0.7:
#                 conditions.append(
#                     f"Cropping intensity {intensity:.2f} — improving land utilisation "
#                     f"in both Kharif and Rabi could enhance credit limit"
#                 )
#             if seasons_with / max(total, 1) < 0.6:
#                 conditions.append(
#                     "Cultivate consistently across more seasons to improve assessment"
#                 )

#         elif risk_category == 'HIGH':
#             conditions.append("Crop insurance mandatory before disbursement")
#             conditions.append("Monthly satellite monitoring")
#             conditions.append("Collateral required")
#             conditions.append(
#                 "Submit crop plan for next season to loan officer"
#             )

#         else:  # VERY_HIGH
#             conditions.append("Crop insurance mandatory")
#             conditions.append("Bi-weekly satellite monitoring")
#             conditions.append("Collateral required (min 125% of loan value)")
#             conditions.append("Co-borrower or guarantor required")
#             conditions.append("Agricultural credit counselling mandatory")
#             conditions.append(
#                 "Re-assessment after 2 full seasons of consistent cultivation"
#             )

#         return conditions


"""
Credit Risk Scorer - FIXED VERSION
===================================
FIXES:
1. calculate_credit_limit() signature corrected - no more crop_detected parameter
2. Proper handling of cropping_analysis for dominant crop extraction
3. Better error handling and logging

Calculates credit score from satellite-derived agricultural metrics.
"""

from typing import Dict, List, Optional
import logging

from config import PipelineConfig

logger = logging.getLogger(__name__)


class CreditScorer:
    """Calculates credit risk score using weighted satellite-derived components."""

    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.weights = PipelineConfig.CREDIT_WEIGHTS
        logger.info("✔ CreditScorer v3.1 initialized (FIXED)")
        logger.info(f"  Weights: {self.weights}")

    # =========================================================================
    # MAIN SCORING
    # =========================================================================

    def calculate_credit_score(
        self,
        cropping_analysis: Dict,
        performance_analysis: Dict,
        weather_analysis: Dict,
        farmer_benefits: Optional[Dict] = None,
        crop_cycles: Optional[Dict] = None,  # NEW: Optional crop cycles data
    ) -> Dict:
        """
        Compute final credit score from all pipeline components.
        """
        logger.info(f"\n{'='*70}")
        logger.info("CALCULATING CREDIT SCORE")
        logger.info(f"{'='*70}")

        scores: Dict[str, float] = {}

        # ── 1. Crop Detection (35%) ────────────────────────────────────────
        scores['crop_detection'] = self._score_crop_detection(cropping_analysis, crop_cycles)
        logger.info(f"  Crop Detection:    {scores['crop_detection']:.1f}/100")

        # ── 2. Crop Performance (30%) ──────────────────────────────────────
        scores['crop_performance'] = performance_analysis.get(
            'average_performance_score', 50.0
        )
        logger.info(f"  Crop Performance:  {scores['crop_performance']:.1f}/100")

        # ── 3. Yield Potential (15%) ───────────────────────────────────────
        scores['yield_potential'] = performance_analysis.get(
            'average_yield_score', 50.0
        )
        logger.info(f"  Yield Potential:   {scores['yield_potential']:.1f}/100")

        # ── 4. Cropping Intensity (12%) ────────────────────────────────────
        scores['cropping_intensity'] = self._score_cropping_intensity(
            cropping_analysis, crop_cycles
        )
        logger.info(f"  Cropping Intensity:{scores['cropping_intensity']:.1f}/100")

        # ── 5. Weather Safety (5%) ─────────────────────────────────────────
        weather_risk = weather_analysis.get('weather_risk_score', 50.0)
        scores['weather_risk'] = max(0.0, 100.0 - weather_risk)
        logger.info(f"  Weather Safety:    {scores['weather_risk']:.1f}/100")

        # ── 6. Govt Benefits (3%) ──────────────────────────────────────────
        scores['govt_benefits'] = self._score_govt_benefits(farmer_benefits)
        logger.info(f"  Govt Benefits:     {scores['govt_benefits']:.1f}/100")

        # ── Weighted total ─────────────────────────────────────────────────
        total = sum(
            scores[k] * (self.weights[k] / 100)
            for k in scores
        )
        total = round(min(100.0, max(0.0, total)), 1)

        risk_category = self._classify_risk(total)
        weak_components = [k for k, v in scores.items() if v < 50.0]

        logger.info(f"\n  ► Total Credit Score: {total}/100")
        logger.info(f"  ► Risk Category:      {risk_category}")
        if weak_components:
            logger.info(f"  ► Weak components:    {', '.join(weak_components)}")

        return {
            'credit_score': total,
            'risk_category': risk_category,
            'component_scores': {k: round(v, 1) for k, v in scores.items()},
            'weak_components': weak_components,
            'calculation_method': 'satellite_temporal_v3',
            'method': 'rule_based',  # For compatibility with ML scorer
            'assessment_date': __import__('datetime').datetime.now().isoformat(),
        }

    # =========================================================================
    # COMPONENT SCORERS
    # =========================================================================

    def _score_crop_detection(
        self,
        ca: Dict,
        crop_cycles: Optional[Dict] = None
    ) -> float:
        """
        Score crop detection from either traditional analysis or cycle detection.
        """
        # If we have crop cycles, use those
        if crop_cycles and crop_cycles.get('cycles_count', 0) > 0:
            cycles_count = crop_cycles['cycles_count']
            utilization_index = crop_cycles.get('utilization_metrics', {}).get(
                'land_utilization_index', 0
            )
            
            # Scoring based on cycles
            cycle_score = min(50, cycles_count * 10)  # Up to 5 cycles = 50 points
            utilization_score = utilization_index * 50  # 0-1 range → 0-50 points
            
            return round(cycle_score + utilization_score, 1)
        
        # Traditional seasonal analysis
        seasons_with = ca.get('seasons_with_crops', 0)
        total_seasons = ca.get('total_seasons_analyzed', 1)
        
        consistency = (seasons_with / max(total_seasons, 1)) * 50.0
        
        # Crop detection score if available
        detection_score = ca.get('crop_detection_score', 0)
        pattern_score = detection_score * 0.3
        
        # Diversity
        n_crops = len(ca.get('crops_detected', {}))
        diversity = min(20.0, n_crops * 7.0)
        
        total = round(min(100.0, consistency + pattern_score + diversity), 1)
        return total

    def _score_cropping_intensity(
        self,
        ca: Dict,
        crop_cycles: Optional[Dict] = None
    ) -> float:
        """
        Score cropping intensity from cycles or traditional analysis.
        """
        # If we have crop cycles, use crop intensity directly
        if crop_cycles and crop_cycles.get('utilization_metrics'):
            crop_intensity = crop_cycles['utilization_metrics'].get('crop_intensity', 0)
            # crops_per_year: 0-3+ range
            # 3+ crops/year = 100 points
            # 2 crops/year = 75 points
            # 1 crop/year = 50 points
            if crop_intensity >= 3.0:
                score = 100.0
            elif crop_intensity >= 2.0:
                score = 75.0 + (crop_intensity - 2.0) * 25.0
            elif crop_intensity >= 1.0:
                score = 50.0 + (crop_intensity - 1.0) * 25.0
            else:
                score = crop_intensity * 50.0
            
            return round(min(100.0, max(0.0, score)), 1)
        
        # Traditional intensity calculation
        intensity = ca.get('cropping_intensity', 0.0)
        
        if intensity >= 1.0:
            score = 100.0
        elif intensity >= 0.5:
            score = 50.0 + (intensity - 0.5) / 0.5 * 50.0
        else:
            score = intensity / 0.5 * 50.0
        
        return round(min(100.0, max(0.0, score)), 1)

    def _score_govt_benefits(self, benefits: Optional[Dict]) -> float:
        """PM-KISAN enrollment + crop insurance."""
        if not benefits:
            return 50.0
        score = 0.0
        if benefits.get('pm_kisan_enrolled'):
            score += 50.0
        if benefits.get('has_crop_insurance'):
            score += 50.0
        return round(score, 1)

    def _classify_risk(self, score: float) -> str:
        for category, (lo, hi) in PipelineConfig.RISK_THRESHOLDS.items():
            if lo <= score < hi:
                return category
        return 'VERY_HIGH'

    # =========================================================================
    # CREDIT LIMIT CALCULATION - FIXED SIGNATURE
    # =========================================================================

    def calculate_credit_limit(
        self,
        credit_score: float,
        field_area_ha: float,
        cropping_analysis: Dict,
        crop_detected: Optional[Dict] = None,  # DEPRECATED but kept for compatibility
        farmer_benefits: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate recommended credit limit based on farming quality score.
        
        FIXED: Removed reliance on crop_detected parameter.
        Now extracts dominant crop from cropping_analysis directly.
        """
        # Extract cropping intensity
        cropping_intensity = cropping_analysis.get('cropping_intensity', 1.0)
        
        # Base limit per hectare based on farming quality score
        if credit_score >= 80:
            base_per_ha = 15000
            risk_level = 'LOW'
        elif credit_score >= 70:
            base_per_ha = 12000
            risk_level = 'MEDIUM_LOW'
        elif credit_score >= 60:
            base_per_ha = 10000
            risk_level = 'MEDIUM'
        elif credit_score >= 50:
            base_per_ha = 7500
            risk_level = 'MEDIUM_HIGH'
        elif credit_score >= 40:
            base_per_ha = 5000
            risk_level = 'HIGH'
        else:
            base_per_ha = 3000
            risk_level = 'VERY_HIGH'
        
        # Cropping intensity multiplier
        if cropping_intensity >= 2.5:
            intensity_mult = 1.30
        elif cropping_intensity >= 2.0:
            intensity_mult = 1.20
        elif cropping_intensity >= 1.5:
            intensity_mult = 1.10
        elif cropping_intensity >= 1.0:
            intensity_mult = 1.00
        else:
            intensity_mult = 0.85
        
        # Area multiplier
        if field_area_ha >= 10:
            area_mult = 1.15
        elif field_area_ha >= 5:
            area_mult = 1.10
        elif field_area_ha >= 2:
            area_mult = 1.05
        elif field_area_ha >= 1:
            area_mult = 1.00
        else:
            area_mult = 0.90
        
        # Crop value multiplier - FIXED: Extract from cropping_analysis
        high_value_crops = [
            'Sugarcane', 'Cotton', 'Banana', 'Potato', 'Tomato',
            'Onion', 'Garlic', 'Grapes', 'Pomegranate', 'Mango',
            'Chilli', 'Tobacco'
        ]
        
        crop_mult = 1.00
        dominant_crop = cropping_analysis.get('dominant_crop')
        if dominant_crop and dominant_crop in high_value_crops:
            crop_mult = 1.15
            logger.debug(f"High-value crop detected: {dominant_crop}, multiplier: 1.15")
        
        # Government benefits multiplier
        benefits_mult = 1.00
        if farmer_benefits:
            if farmer_benefits.get('pm_kisan_enrolled'):
                benefits_mult += 0.05
            if farmer_benefits.get('has_crop_insurance'):
                benefits_mult += 0.05
        
        # Calculate base limit
        base_limit = base_per_ha * field_area_ha
        
        # Apply all multipliers
        total_multiplier = intensity_mult * area_mult * crop_mult * benefits_mult
        final_limit = base_limit * total_multiplier
        
        # Round to nearest 1000
        final_limit = round(final_limit / 1000) * 1000
        
        # Suggested interest rate
        interest_rate = self._suggest_interest_rate(credit_score)
        
        # Suggested tenure
        if cropping_intensity >= 2.0:
            tenure_months = 6
        else:
            tenure_months = 12
        
        return {
            'recommended_limit': final_limit,
            'base_limit_per_ha': base_per_ha,
            'total_base_limit': base_limit,
            'field_area_ha': field_area_ha,
            'risk_level': risk_level,
            'multipliers': {
                'intensity': intensity_mult,
                'area': area_mult,
                'crop_value': crop_mult,
                'benefits': benefits_mult,
                'total': total_multiplier
            },
            'components': {
                'base': base_limit,
                'after_intensity': base_limit * intensity_mult,
                'after_area': base_limit * intensity_mult * area_mult,
                'after_crop': base_limit * intensity_mult * area_mult * crop_mult,
                'final': final_limit
            },
            'recommendations': {
                'interest_rate': interest_rate,
                'tenure_months': tenure_months,
                'repayment_type': 'lump_sum' if tenure_months <= 6 else 'installments'
            },
            'reasoning': self._explain_limit(
                credit_score,
                cropping_intensity,
                field_area_ha,
                total_multiplier
            )
        }
    
    def _suggest_interest_rate(self, credit_score: float) -> float:
        """Suggest interest rate based on farming quality score."""
        if credit_score >= 80:
            return 8.5
        elif credit_score >= 70:
            return 9.5
        elif credit_score >= 60:
            return 10.5
        elif credit_score >= 50:
            return 12.0
        elif credit_score >= 40:
            return 14.0
        else:
            return 16.0
    
    def _explain_limit(
        self,
        credit_score: float,
        intensity: float,
        area: float,
        multiplier: float
    ) -> str:
        """Generate human-readable explanation of credit limit."""
        parts = []
        
        if credit_score >= 80:
            parts.append("Excellent farming practices")
        elif credit_score >= 70:
            parts.append("Good farming performance")
        elif credit_score >= 60:
            parts.append("Average farming quality")
        else:
            parts.append("Below-average farming practices")
        
        if intensity >= 2.5:
            parts.append("intensive land use")
        elif intensity >= 2.0:
            parts.append("efficient double cropping")
        elif intensity >= 1.5:
            parts.append("moderate cropping intensity")
        else:
            parts.append("low land utilization")
        
        if area >= 10:
            parts.append("large-scale operations")
        elif area >= 5:
            parts.append("medium-scale farming")
        else:
            parts.append("small-scale farming")
        
        if multiplier > 1.2:
            parts.append("Strong capacity for credit")
        elif multiplier > 1.0:
            parts.append("Good credit capacity")
        else:
            parts.append("Limited credit capacity")
        
        return "; ".join(parts) + "."