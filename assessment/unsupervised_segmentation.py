"""
Unsupervised Farmer Segmentation
=================================
VERSION 1.0

ML-based farmer segmentation WITHOUT requiring pre-labeled training data.
Uses unsupervised learning to discover natural farmer segments and risk patterns.

Techniques:
- K-Means clustering for farmer segmentation
- Isolation Forest for risk/anomaly detection
- DBSCAN for pattern discovery
- PCA for feature reduction

Key Advantage: Works from Day 1 with NO labeled data!
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from sklearn.cluster import KMeans, DBSCAN
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
import logging

logger = logging.getLogger(__name__)


class UnsupervisedFarmerSegmentation:
    """
    Discover farmer segments using unsupervised ML
    NO labeled data required!
    """
    
    def __init__(
        self,
        n_segments: int = 5,
        contamination: float = 0.15  # Expected fraction of outliers
    ):
        """
        Initialize segmentation model
        
        Args:
            n_segments: Number of farmer segments to discover (default 5)
            contamination: Expected fraction of risky/outlier farmers (default 15%)
        """
        self.n_segments = n_segments
        self.contamination = contamination
        
        self.scaler = None
        self.kmeans = None
        self.isolation_forest = None
        self.feature_names = None
        
        logger.info(f"Unsupervised Segmentation initialized (k={n_segments}, contamination={contamination})")
    
    def fit(
        self,
        assessments: List[Dict]
    ) -> Tuple[List[int], List[Dict]]:
        """
        Fit segmentation model on farmer assessments
        
        Args:
            assessments: List of farmer assessment dictionaries
            
        Returns:
            Tuple of (segment_labels, segment_profiles)
        """
        logger.info(f"Fitting unsupervised model on {len(assessments)} farmers")
        
        # Extract features
        X, feature_names = self._extract_features(assessments)
        self.feature_names = feature_names
        
        # Normalize features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        # K-Means clustering
        self.kmeans = KMeans(n_clusters=self.n_segments, random_state=42, n_init=10)
        segments = self.kmeans.fit_predict(X_scaled)
        
        # Isolation Forest for anomaly detection
        self.isolation_forest = IsolationForest(
            contamination=self.contamination,
            random_state=42
        )
        self.isolation_forest.fit(X_scaled)
        
        # Create segment profiles
        segment_profiles = self._create_segment_profiles(X, segments, feature_names)
        
        logger.info(f"Discovered {self.n_segments} farmer segments:")
        for i, profile in enumerate(segment_profiles):
            logger.info(f"  Segment {i}: {profile['name']} ({profile['count']} farmers)")
        
        return segments.tolist(), segment_profiles
    
    def predict(self, assessment: Dict) -> Dict:
        """
        Predict segment and risk for a new farmer
        
        Args:
            assessment: Farmer assessment dictionary
            
        Returns:
            Dict with segment, risk_score, and explanation
        """
        if self.kmeans is None:
            raise ValueError("Model not fitted. Call fit() first.")
        
        # Extract features
        X, _ = self._extract_features([assessment])
        X_scaled = self.scaler.transform(X)
        
        # Predict segment
        segment = int(self.kmeans.predict(X_scaled)[0])
        
        # Predict risk (anomaly score)
        anomaly_score = self.isolation_forest.score_samples(X_scaled)[0]
        # Convert to 0-100 scale (lower anomaly score = higher risk)
        risk_score = max(0, min(100, (1 - anomaly_score) * 50 + 50))
        
        # Determine risk category
        if risk_score < 30:
            risk_category = "LOW"
        elif risk_score < 50:
            risk_category = "MEDIUM"
        elif risk_score < 70:
            risk_category = "HIGH"
        else:
            risk_category = "VERY_HIGH"
        
        return {
            'segment': segment,
            'risk_score': round(risk_score, 1),
            'risk_category': risk_category,
            'is_outlier': risk_score > 70,
            'method': 'unsupervised_ml'
        }
    
    def _extract_features(
        self,
        assessments: List[Dict]
    ) -> Tuple[np.ndarray, List[str]]:
        """
        Extract numerical features from assessments
        """
        features = []
        feature_names = []
        
        for assessment in assessments:
            farmer_features = []
            
            # Cropping features
            cropping = assessment.get('cropping_analysis', {})
            farmer_features.append(cropping.get('cropping_intensity', 0))
            farmer_features.append(cropping.get('crop_detection_score', 0))
            farmer_features.append(len(cropping.get('crops_detected', {})))
            
            # Performance features
            performance = assessment.get('performance_analysis', {})
            farmer_features.append(performance.get('average_performance_score', 0))
            farmer_features.append(performance.get('average_yield_score', 0))
            
            # Weather risk
            weather = assessment.get('weather_analysis', {})
            farmer_features.append(weather.get('weather_risk_score', 50))
            farmer_features.append(weather.get('total_extreme_events', 0))
            
            # Advanced features (if available)
            if 'crop_cycles' in assessment:
                cycle_data = assessment['crop_cycles']
                utilization = cycle_data.get('utilization_metrics', {})
                farmer_features.append(utilization.get('crop_intensity', 0))
                farmer_features.append(utilization.get('land_utilization_index', 0) * 100)
            else:
                farmer_features.extend([0, 0])
            
            features.append(farmer_features)
        
        # Feature names
        if not feature_names:
            feature_names = [
                'cropping_intensity',
                'crop_detection_score',
                'crop_diversity',
                'performance_score',
                'yield_score',
                'weather_risk',
                'extreme_events',
                'cycle_intensity',
                'land_utilization'
            ]
        
        return np.array(features), feature_names
    
    def _create_segment_profiles(
        self,
        X: np.ndarray,
        segments: np.ndarray,
        feature_names: List[str]
    ) -> List[Dict]:
        """
        Create interpretable profiles for each segment
        """
        profiles = []
        
        for segment_id in range(self.n_segments):
            segment_mask = segments == segment_id
            segment_data = X[segment_mask]
            
            if len(segment_data) == 0:
                continue
            
            # Calculate average feature values
            avg_features = np.mean(segment_data, axis=0)
            feature_dict = {
                name: round(float(val), 2)
                for name, val in zip(feature_names, avg_features)
            }
            
            # Classify segment based on characteristics
            segment_name = self._classify_segment(feature_dict)
            
            profile = {
                'segment_id': segment_id,
                'name': segment_name,
                'count': int(np.sum(segment_mask)),
                'average_features': feature_dict,
                'description': self._generate_segment_description(segment_name, feature_dict)
            }
            
            profiles.append(profile)
        
        return profiles
    
    def _classify_segment(self, features: Dict) -> str:
        """
        Classify segment into interpretable categories
        """
        intensity = features.get('cropping_intensity', 0)
        performance = features.get('performance_score', 0)
        
        if intensity >= 2.5 and performance >= 75:
            return "HIGH_INTENSITY_COMMERCIAL"
        elif intensity >= 2.0 and performance >= 65:
            return "PROGRESSIVE_DOUBLE_CROPPING"
        elif intensity >= 1.5 and performance >= 55:
            return "MODERATE_TRADITIONAL"
        elif intensity < 1.5 and performance >= 55:
            return "LARGE_SCALE_SINGLE_CROP"
        else:
            return "LOW_INTENSITY_SMALLHOLDERS"
    
    def _generate_segment_description(self, name: str, features: Dict) -> str:
        """
        Generate human-readable description of segment
        """
        descriptions = {
            "HIGH_INTENSITY_COMMERCIAL": (
                f"High-intensity commercial farmers with {features.get('cropping_intensity', 0):.1f} crops/year "
                f"and {features.get('performance_score', 0):.0f}% average performance. "
                "These are the most productive and lowest-risk farmers."
            ),
            "PROGRESSIVE_DOUBLE_CROPPING": (
                f"Progressive farmers practicing double cropping ({features.get('cropping_intensity', 0):.1f} crops/year) "
                f"with good performance ({features.get('performance_score', 0):.0f}%). "
                "Moderate risk, good potential."
            ),
            "MODERATE_TRADITIONAL": (
                f"Traditional farmers with moderate intensity ({features.get('cropping_intensity', 0):.1f} crops/year) "
                f"and average performance ({features.get('performance_score', 0):.0f}%). "
                "Medium risk category."
            ),
            "LARGE_SCALE_SINGLE_CROP": (
                f"Large-scale farmers focusing on single long-duration crops "
                f"with {features.get('performance_score', 0):.0f}% performance. "
                "Lower intensity but potentially lower risk due to scale."
            ),
            "LOW_INTENSITY_SMALLHOLDERS": (
                f"Small-scale farmers with low intensity ({features.get('cropping_intensity', 0):.1f} crops/year) "
                f"and lower performance ({features.get('performance_score', 0):.0f}%). "
                "Higher risk category, may need additional support."
            )
        }
        
        return descriptions.get(name, "Unclassified segment")


class RiskAnomalyDetector:
    """
    Detect risky/anomalous farmers using Isolation Forest
    Can work standalone or alongside segmentation
    """
    
    def __init__(self, contamination: float = 0.15):
        """
        Args:
            contamination: Expected fraction of anomalies (default 15%)
        """
        self.contamination = contamination
        self.model = None
        self.scaler = None
    
    def fit(self, assessments: List[Dict]):
        """Train anomaly detector"""
        segmenter = UnsupervisedFarmerSegmentation()
        X, _ = segmenter._extract_features(assessments)
        
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        self.model = IsolationForest(
            contamination=self.contamination,
            random_state=42
        )
        self.model.fit(X_scaled)
        
        logger.info(f"Risk anomaly detector trained on {len(assessments)} farmers")
    
    def predict(self, assessment: Dict) -> Dict:
        """
        Predict if farmer is anomalous/risky
        
        Returns:
            Dict with is_anomaly, anomaly_score, risk_level
        """
        if self.model is None:
            raise ValueError("Model not trained. Call fit() first.")
        
        segmenter = UnsupervisedFarmerSegmentation()
        X, _ = segmenter._extract_features([assessment])
        X_scaled = self.scaler.transform(X)
        
        # Predict
        is_anomaly = self.model.predict(X_scaled)[0] == -1
        anomaly_score = float(self.model.score_samples(X_scaled)[0])
        
        # Convert to risk score (0-100)
        risk_score = max(0, min(100, (1 - anomaly_score) * 50 + 50))
        
        if risk_score < 30:
            risk_level = "LOW"
        elif risk_score < 50:
            risk_level = "MEDIUM"
        elif risk_score < 70:
            risk_level = "HIGH"
        else:
            risk_level = "VERY_HIGH"
        
        return {
            'is_anomaly': bool(is_anomaly),
            'anomaly_score': round(anomaly_score, 3),
            'risk_score': round(risk_score, 1),
            'risk_level': risk_level
        }


# Export
__all__ = ['UnsupervisedFarmerSegmentation', 'RiskAnomalyDetector']
