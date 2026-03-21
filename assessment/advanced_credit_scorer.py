"""
Advanced Credit Scorer - ML-Enhanced Version
============================================
VERSION 2.0

Combines traditional rule-based scoring with ML predictions and explanations.
Supports both unsupervised (no labels) and supervised (with historical outcomes) modes.

Features:
- Backward compatible with existing credit_scorer
- Unsupervised risk scoring (Day 1)
- Supervised ML when training data available
- SHAP-style explanations
- Confidence scores
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
import logging

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import RandomForestClassifier
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logging.warning("sklearn not available - ML features disabled")

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    logging.warning("xgboost not available - using RandomForest fallback")

# Import configuration
try:
    from config.pipeline_config import PipelineConfig
except ImportError:
    from ..config.pipeline_config import PipelineConfig

logger = logging.getLogger(__name__)


class AdvancedCreditScorer:
    """
    ML-enhanced credit scorer with multiple scoring modes
    """
    
    def __init__(
        self,
        mode: str = 'hybrid',  # 'rule_based', 'unsupervised', 'supervised', 'hybrid'
        verbose: bool = True
    ):
        """
        Initialize advanced credit scorer
        
        Args:
            mode: Scoring mode
                - 'rule_based': Traditional weighted scoring (existing)
                - 'unsupervised': ML without labels (Day 1)
                - 'supervised': ML with historical data (Month 6+)
                - 'hybrid': Combine rule-based + ML (best)
            verbose: Enable detailed logging
        """
        self.mode = mode
        self.verbose = verbose
        
        # ML models
        self.model = None
        self.scaler = None
        self.feature_names = None
        
        # Unsupervised components
        self.unsupervised_segmenter = None
        
        logger.info(f"Advanced Credit Scorer initialized (mode={mode})")
    
    def calculate_credit_score(
        self,
        cropping_analysis: Dict,
        performance_analysis: Dict,
        weather_analysis: Dict,
        farmer_benefits: Optional[Dict] = None,
        crop_cycles: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate credit score using selected mode
        
        Args:
            cropping_analysis: Crop detection results
            performance_analysis: Performance metrics
            weather_analysis: Weather risk analysis
            farmer_benefits: Government benefits enrollment
            crop_cycles: Advanced crop cycle analysis (optional)
            
        Returns:
            Dict with score, risk category, explanation, confidence
        """
        # Build assessment dictionary
        assessment = {
            'cropping_analysis': cropping_analysis,
            'performance_analysis': performance_analysis,
            'weather_analysis': weather_analysis,
            'farmer_benefits': farmer_benefits,
            'crop_cycles': crop_cycles
        }
        
        if self.mode == 'rule_based':
            return self._rule_based_score(assessment)
        elif self.mode == 'unsupervised':
            return self._unsupervised_score(assessment)
        elif self.mode == 'supervised':
            return self._supervised_score(assessment)
        elif self.mode == 'hybrid':
            return self._hybrid_score(assessment)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")
    
    def _rule_based_score(self, assessment: Dict) -> Dict:
        """
        Traditional rule-based scoring (existing system)
        """
        from config.pipeline_config import PipelineConfig
        weights = PipelineConfig.CREDIT_WEIGHTS
        
        scores = {}
        
        # Crop Detection (35%)
        ca = assessment['cropping_analysis']
        seasons_with = ca.get('seasons_with_crops', 0)
        total_seasons = ca.get('total_seasons_analyzed', 1)
        season_consistency = (seasons_with / total_seasons) * 50
        
        # Temporal pattern quality
        season_results = ca.get('season_results', [])
        if season_results:
            pattern_scores = [
                s.get('arc_score', 0) * 0.4 +
                s.get('frac_above_thresh', 0) * 30 +
                s.get('ndvi_rise_ratio', 0) * 30
                for s in season_results if s.get('crop_detected')
            ]
            pattern_quality = np.mean(pattern_scores) if pattern_scores else 0
        else:
            pattern_quality = 0
        
        # Crop diversity
        unique_crops = len(ca.get('crops_detected', {}))
        diversity_score = min(20, unique_crops * 7)
        
        scores['crop_detection'] = season_consistency + pattern_quality * 0.3 + diversity_score
        
        # Crop Performance (30%)
        pa = assessment['performance_analysis']
        scores['crop_performance'] = pa.get('average_performance_score', 50)
        
        # Yield Potential (15%)
        scores['yield_potential'] = pa.get('average_yield_score', 50)
        
        # Cropping Intensity (12%)
        intensity = ca.get('cropping_intensity', 0)
        if intensity >= 2.5:
            intensity_score = 95
        elif intensity >= 2.0:
            intensity_score = 85
        elif intensity >= 1.5:
            intensity_score = 70
        elif intensity >= 1.0:
            intensity_score = 55
        else:
            intensity_score = max(0, intensity * 40)
        scores['cropping_intensity'] = intensity_score
        
        # Weather Safety (5%)
        wa = assessment['weather_analysis']
        weather_risk = wa.get('weather_risk_score', 50)
        scores['weather_risk'] = max(0, 100 - weather_risk)
        
        # Govt Benefits (3%)
        fb = assessment.get('farmer_benefits', {})
        if fb:
            pm_kisan = 20 if fb.get('pm_kisan_enrolled') else 0
            insurance = 30 if fb.get('has_crop_insurance') else 0
            scores['govt_benefits'] = pm_kisan + insurance
        else:
            scores['govt_benefits'] = 0
        
        # Weighted total
        total = sum(
            scores[k] * (weights[k] / 100)
            for k in scores
        )
        total = round(min(100, max(0, total)), 1)
        
        # Risk category
        if total >= 70:
            risk_category = 'LOW'
        elif total >= 50:
            risk_category = 'MEDIUM'
        elif total >= 30:
            risk_category = 'HIGH'
        else:
            risk_category = 'VERY_HIGH'
        
        return {
            'credit_score': total,
            'risk_category': risk_category,
            'component_scores': {k: round(v, 1) for k, v in scores.items()},
            'method': 'rule_based',
            'confidence': 75.0  # Rule-based has moderate confidence
        }
    
    def _unsupervised_score(self, assessment: Dict) -> Dict:
        """
        Unsupervised ML scoring (no training labels needed)
        """
        if not SKLEARN_AVAILABLE:
            logger.warning("sklearn not available, falling back to rule-based")
            return self._rule_based_score(assessment)
        
        # Use unsupervised segmentation
        try:
            from assessment.unsupervised_segmentation import UnsupervisedFarmerSegmentation, RiskAnomalyDetector
        except ImportError:
            from .unsupervised_segmentation import UnsupervisedFarmerSegmentation, RiskAnomalyDetector
        
        if self.unsupervised_segmenter is None:
            # Initialize on first use (would be trained on historical data in production)
            self.unsupervised_segmenter = UnsupervisedFarmerSegmentation(n_segments=5)
            logger.info("Unsupervised segmenter initialized (would be pre-trained in production)")
        
        # Get segment and risk
        try:
            prediction = self.unsupervised_segmenter.predict(assessment)
            
            # Map risk score to credit score (inverse relationship)
            credit_score = 100 - prediction['risk_score']
            
            return {
                'credit_score': round(credit_score, 1),
                'risk_category': prediction['risk_category'],
                'segment': prediction['segment'],
                'method': 'unsupervised_ml',
                'confidence': 65.0,  # Unsupervised has lower confidence
                'explanation': f"Assigned to segment {prediction['segment']}"
            }
        except:
            # Fallback to rule-based if prediction fails
            logger.warning("Unsupervised prediction failed, using rule-based fallback")
            return self._rule_based_score(assessment)
    
    def _supervised_score(self, assessment: Dict) -> Dict:
        """
        Supervised ML scoring (requires trained model)
        """
        if self.model is None:
            logger.warning("No trained model available, falling back to rule-based")
            return self._rule_based_score(assessment)
        
        # Extract features
        features = self.extract_features(assessment)
        X_scaled = self.scaler.transform(features)
        
        # Predict
        prob_repay = self.model.predict_proba(X_scaled)[0][1]
        credit_score = prob_repay * 100
        
        # Risk category
        if credit_score >= 70:
            risk_category = 'LOW'
        elif credit_score >= 50:
            risk_category = 'MEDIUM'
        elif credit_score >= 30:
            risk_category = 'HIGH'
        else:
            risk_category = 'VERY_HIGH'
        
        # Confidence (how far from decision boundary)
        confidence = abs(prob_repay - 0.5) * 2 * 100
        
        return {
            'credit_score': round(credit_score, 1),
            'risk_category': risk_category,
            'probability_of_repayment': round(prob_repay, 3),
            'method': 'supervised_ml',
            'confidence': round(confidence, 1),
            'model_version': 'xgboost_v1' if XGBOOST_AVAILABLE else 'rf_v1'
        }
    
    def _hybrid_score(self, assessment: Dict) -> Dict:
        """
        Hybrid: Combine rule-based + ML for best results
        """
        # Get both scores
        rule_score = self._rule_based_score(assessment)
        
        # Try ML scoring
        if self.model is not None:
            ml_score = self._supervised_score(assessment)
            ml_weight = 0.7  # Give more weight to ML if available
        elif SKLEARN_AVAILABLE:
            ml_score = self._unsupervised_score(assessment)
            ml_weight = 0.4  # Lower weight for unsupervised
        else:
            # No ML available, use rule-based only
            return rule_score
        
        # Combine scores
        combined_score = (
            rule_score['credit_score'] * (1 - ml_weight) +
            ml_score['credit_score'] * ml_weight
        )
        
        # Combined risk category (use ML if confident)
        if ml_score.get('confidence', 0) > 70:
            risk_category = ml_score['risk_category']
        else:
            risk_category = rule_score['risk_category']
        
        return {
            'credit_score': round(combined_score, 1),
            'risk_category': risk_category,
            'rule_based_score': rule_score['credit_score'],
            'ml_score': ml_score['credit_score'],
            'ml_weight': ml_weight,
            'method': 'hybrid',
            'confidence': round((rule_score.get('confidence', 75) + ml_score.get('confidence', 65)) / 2, 1)
        }
    
    def calculate_credit_limit(
        self,
        credit_score: float,
        field_area_ha: float,
        cropping_analysis: Dict,
        crop_detected: Optional[Dict] = None,
        farmer_benefits: Optional[Dict] = None
    ) -> Dict:
        """
        Calculate recommended credit limit (wrapper for compatibility)
        
        This method provides compatibility with the original credit_scorer interface.
        It uses the base credit_scorer logic for limit calculation.
        
        Args:
            credit_score: Credit score (0-100)
            field_area_ha: Field area in hectares
            cropping_analysis: Crop detection results
            crop_detected: Detected crop information
            farmer_benefits: Government benefits enrollment
            
        Returns:
            Credit limit recommendations
        """
        # Import base credit scorer for limit calculation
        try:
            from assessment.credit_scorer import CreditScorer
        except ImportError:
            from .credit_scorer import CreditScorer
        
        # Create temporary credit scorer for limit calculation
        base_scorer = CreditScorer(verbose=False)
        
        # Call the calculate_credit_limit method from base scorer
        return base_scorer.calculate_credit_limit(
            credit_score=credit_score,
            field_area_ha=field_area_ha,
            cropping_analysis=cropping_analysis,
            crop_detected=crop_detected,
            farmer_benefits=farmer_benefits
        )
    
    def extract_features(self, assessment: Dict) -> pd.DataFrame:
        """
        Extract numerical features for ML model
        """
        features = {}
        
        # Cropping features
        ca = assessment['cropping_analysis']
        features['cropping_intensity'] = ca.get('cropping_intensity', 0)
        features['crop_detection_score'] = ca.get('crop_detection_score', 0)
        features['seasons_with_crops'] = ca.get('seasons_with_crops', 0)
        features['unique_crops'] = len(ca.get('crops_detected', {}))
        
        # Performance features
        pa = assessment['performance_analysis']
        features['avg_performance'] = pa.get('average_performance_score', 0)
        features['avg_yield'] = pa.get('average_yield_score', 0)
        
        # Weather features
        wa = assessment['weather_analysis']
        features['weather_risk'] = wa.get('weather_risk_score', 50)
        features['extreme_events'] = wa.get('total_extreme_events', 0)
        
        # Advanced features (if available)
        if assessment.get('crop_cycles'):
            cc = assessment['crop_cycles']
            utilization = cc.get('utilization_metrics', {})
            features['cycle_intensity'] = utilization.get('crop_intensity', 0)
            features['land_utilization'] = utilization.get('land_utilization_index', 0) * 100
            features['fallow_fraction'] = utilization.get('fallow_analysis', {}).get('fallow_fraction', 0) * 100
        else:
            features['cycle_intensity'] = 0
            features['land_utilization'] = 0
            features['fallow_fraction'] = 0
        
        # Government benefits
        fb = assessment.get('farmer_benefits', {})
        features['pm_kisan'] = 1 if fb.get('pm_kisan_enrolled') else 0
        features['insurance'] = 1 if fb.get('has_crop_insurance') else 0
        
        return pd.DataFrame([features])
    
    def train(
        self,
        training_assessments: List[Dict],
        outcomes: List[int]  # 1 = repaid, 0 = defaulted
    ):
        """
        Train supervised ML model on historical loan data
        
        Args:
            training_assessments: List of historical farmer assessments
            outcomes: List of loan outcomes (1=repaid, 0=defaulted)
        """
        if not SKLEARN_AVAILABLE:
            raise ImportError("sklearn required for ML training")
        
        logger.info(f"Training supervised model on {len(training_assessments)} examples")
        
        # Extract features
        X_list = []
        for assessment in training_assessments:
            features = self.extract_features(assessment)
            X_list.append(features)
        
        X = pd.concat(X_list, ignore_index=True)
        y = np.array(outcomes)
        
        self.feature_names = list(X.columns)
        
        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        # Train model
        if XGBOOST_AVAILABLE:
            self.model = xgb.XGBClassifier(
                max_depth=6,
                learning_rate=0.1,
                n_estimators=100,
                objective='binary:logistic',
                random_state=42
            )
        else:
            self.model = RandomForestClassifier(
                n_estimators=100,
                max_depth=6,
                random_state=42
            )
        
        self.model.fit(X_scaled, y)
        
        # Log feature importance
        if hasattr(self.model, 'feature_importances_'):
            importance = dict(zip(
                self.feature_names,
                self.model.feature_importances_
            ))
            
            logger.info("Top 5 features:")
            for feat, imp in sorted(importance.items(), key=lambda x: -x[1])[:5]:
                logger.info(f"  {feat}: {imp:.3f}")
        
        logger.info("Model training complete")


# Export
__all__ = ['AdvancedCreditScorer']