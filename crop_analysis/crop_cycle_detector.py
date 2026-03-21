"""
Crop Cycle Detector - Dynamic Detection from Continuous Time Series
====================================================================
VERSION 1.0

Detects crop cultivation cycles from continuous NDVI data without
predefined seasonal boundaries. Uses temporal pattern analysis to
identify sowing, growth, and harvest phases.

Key Features:
- Continuous time series analysis (no seasonal windows)
- Automatic greenup/senescence detection
- Duration-based crop type classification
- Confidence scoring for each detected cycle
- Handles multiple crops per year (double/triple cropping)
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class CropCycle:
    """Represents a detected crop cultivation cycle"""
    sowing_date: datetime
    harvest_date: datetime
    peak_date: datetime
    duration_days: int
    crop_type: Optional[str]
    peak_ndvi: float
    baseline_ndvi: float
    ndvi_rise: float
    integral_ndvi: float  # Cumulative NDVI (yield proxy)
    confidence: float
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization"""
        return {
            'sowing_date': self.sowing_date.isoformat(),
            'harvest_date': self.harvest_date.isoformat(),
            'peak_date': self.peak_date.isoformat(),
            'duration_days': self.duration_days,
            'crop_type': self.crop_type,
            'peak_ndvi': round(self.peak_ndvi, 3),
            'baseline_ndvi': round(self.baseline_ndvi, 3),
            'ndvi_rise': round(self.ndvi_rise, 3),
            'integral_ndvi': round(self.integral_ndvi, 2),
            'confidence': round(self.confidence, 2)
        }


class CropCycleDetector:
    """
    Detects crop cultivation cycles from continuous NDVI time series
    without predefined seasonal boundaries
    """
    
    def __init__(
        self,
        min_ndvi_rise: float = 0.15,
        min_baseline: float = 0.25,
        min_peak: float = 0.40,
        min_duration_days: int = 50,
        max_duration_days: int = 400
    ):
        """
        Initialize detector with thresholds
        
        Args:
            min_ndvi_rise: Minimum NDVI increase for valid greenup (0.15 = 15%)
            min_baseline: Maximum baseline NDVI for bare soil (0.25)
            min_peak: Minimum peak NDVI for valid crop (0.40)
            min_duration_days: Minimum cycle length (50 days)
            max_duration_days: Maximum cycle length (400 days)
        """
        self.min_ndvi_rise = min_ndvi_rise
        self.min_baseline = min_baseline
        self.min_peak = min_peak
        self.min_duration_days = min_duration_days
        self.max_duration_days = max_duration_days
    
    def detect_cycles(
        self,
        dates: List[datetime],
        ndvi_values: List[float]
    ) -> List[CropCycle]:
        """
        Main method: Detect all crop cycles in a continuous time series
        
        Args:
            dates: List of observation dates
            ndvi_values: Corresponding NDVI values
            
        Returns:
            List of detected crop cycles
        """
        if len(dates) < 10:
            logger.warning(f"Insufficient data for cycle detection: {len(dates)} observations")
            return []
        
        logger.info(f"Detecting crop cycles from {len(dates)} observations")
        logger.info(f"Date range: {dates[0].strftime('%Y-%m-%d')} to {dates[-1].strftime('%Y-%m-%d')}")
        
        # Convert to numpy arrays for easier processing
        dates_arr = np.array(dates)
        ndvi = np.array(ndvi_values)
        
        # Smooth NDVI to reduce noise
        ndvi_smooth = self._smooth_timeseries(ndvi)
        
        # Detect greenup events (potential sowing dates)
        greenup_indices = self._detect_greenup_events(ndvi_smooth)
        logger.info(f"Detected {len(greenup_indices)} potential greenup events")
        
        # Trace each greenup to find full crop cycle
        cycles = []
        for greenup_idx in greenup_indices:
            cycle = self._trace_crop_cycle(
                dates_arr, ndvi_smooth, greenup_idx
            )
            
            if cycle and self._validate_cycle(cycle):
                cycles.append(cycle)
        
        logger.info(f"Validated {len(cycles)} complete crop cycles")
        
        # Remove overlapping/duplicate cycles
        cycles = self._merge_overlapping_cycles(cycles)
        logger.info(f"Final count after merging: {len(cycles)} cycles")
        
        return cycles
    
    def _smooth_timeseries(
        self,
        ndvi: np.ndarray,
        window_size: int = 3
    ) -> np.ndarray:
        """Apply moving average to reduce noise"""
        try:
            from scipy.ndimage import uniform_filter1d
            return uniform_filter1d(ndvi, size=window_size, mode='nearest')
        except ImportError:
            # Fallback to simple moving average
            smoothed = np.copy(ndvi)
            for i in range(len(ndvi)):
                start = max(0, i - window_size // 2)
                end = min(len(ndvi), i + window_size // 2 + 1)
                smoothed[i] = np.mean(ndvi[start:end])
            return smoothed
    
    def _detect_greenup_events(self, ndvi: np.ndarray) -> List[int]:
        """
        Detect sowing events via rapid NDVI increase
        
        Criteria:
        1. Baseline NDVI < threshold (bare soil)
        2. Rapid rise > threshold
        3. Peak reaches minimum vegetation level
        4. Sustained growth (not just rain flush)
        """
        greenup_indices = []
        
        # Use sliding window to detect rapid rises
        window = 4  # ~4 weeks if scenes are weekly
        
        for i in range(len(ndvi) - window * 2):
            # Baseline: average of previous window
            baseline = np.mean(ndvi[max(0, i-window):i+1])
            
            # Post-greenup: average of next window
            post_greenup = np.mean(ndvi[i+window:i+2*window])
            
            # Check criteria
            ndvi_rise = post_greenup - baseline
            
            if (baseline < self.min_baseline and 
                ndvi_rise > self.min_ndvi_rise and
                post_greenup > self.min_peak):
                
                # Verify sustained growth
                if self._is_sustained_growth(ndvi, i, i + 2*window):
                    # Avoid detecting same event multiple times
                    if not greenup_indices or i - greenup_indices[-1] > window:
                        greenup_indices.append(i)
        
        return greenup_indices
    
    def _is_sustained_growth(
        self,
        ndvi: np.ndarray,
        start_idx: int,
        end_idx: int
    ) -> bool:
        """
        Check if NDVI growth is sustained (not just temporary rain flush)
        """
        if end_idx >= len(ndvi):
            return False
        
        growth_segment = ndvi[start_idx:end_idx]
        
        # At least 60% of points should show increasing trend
        increasing_points = np.sum(np.diff(growth_segment) > 0)
        total_transitions = len(growth_segment) - 1
        
        if total_transitions == 0:
            return False
        
        return (increasing_points / total_transitions) > 0.6
    
    def _trace_crop_cycle(
        self,
        dates: np.ndarray,
        ndvi: np.ndarray,
        greenup_idx: int
    ) -> Optional[CropCycle]:
        """
        From a greenup event, trace the complete crop cycle
        """
        # Find peak (reproductive stage)
        search_window = min(30, len(ndvi) - greenup_idx - 1)  # ~30 weeks max
        if search_window < 5:
            return None
        
        peak_idx = greenup_idx + np.argmax(
            ndvi[greenup_idx:greenup_idx + search_window]
        )
        peak_ndvi = ndvi[peak_idx]
        
        # Find harvest (senescence)
        harvest_idx = self._detect_senescence(ndvi, peak_idx)
        
        if harvest_idx is None:
            return None
        
        # Calculate cycle metrics
        sowing_date = dates[greenup_idx]
        peak_date = dates[peak_idx]
        harvest_date = dates[harvest_idx]
        
        duration_days = (harvest_date - sowing_date).days
        
        # Validate duration
        if not (self.min_duration_days <= duration_days <= self.max_duration_days):
            return None
        
        # Calculate integral NDVI (yield proxy)
        ndvi_segment = ndvi[greenup_idx:harvest_idx+1]
        integral_ndvi = np.trapz(ndvi_segment)
        
        # Create cycle object
        cycle = CropCycle(
            sowing_date=sowing_date,
            harvest_date=harvest_date,
            peak_date=peak_date,
            duration_days=duration_days,
            crop_type=self._classify_crop_by_duration(duration_days, peak_ndvi),
            peak_ndvi=float(peak_ndvi),
            baseline_ndvi=float(np.mean(ndvi[max(0, greenup_idx-2):greenup_idx+1])),
            ndvi_rise=float(peak_ndvi - ndvi[greenup_idx]),
            integral_ndvi=float(integral_ndvi),
            confidence=self._calculate_confidence(ndvi_segment)
        )
        
        return cycle
    
    def _detect_senescence(
        self,
        ndvi: np.ndarray,
        peak_idx: int
    ) -> Optional[int]:
        """
        Detect harvest point via NDVI decline
        """
        search_window = min(25, len(ndvi) - peak_idx - 1)
        
        if search_window < 3:
            return None
        
        for i in range(peak_idx, peak_idx + search_window):
            # Check for sustained drop
            if i + 2 >= len(ndvi):
                break
            
            # Senescence = NDVI drops below threshold AND stays low
            if (ndvi[i] < 0.35 and 
                np.mean(ndvi[i:min(i+3, len(ndvi))]) < 0.30):
                return i
        
        # If no clear senescence, use the point of maximum decline
        post_peak = ndvi[peak_idx:peak_idx + search_window]
        if len(post_peak) > 0:
            return peak_idx + len(post_peak) - 1
        
        return None
    
    def _classify_crop_by_duration(
        self,
        duration_days: int,
        peak_ndvi: float
    ) -> str:
        """
        Classify crop type based on cycle duration and peak NDVI
        
        Short-duration (<120 days): Vegetables, Pulses
        Medium-duration (120-240 days): Cereals, Oilseeds
        Long-duration (>240 days): Sugarcane, Perennials
        """
        if duration_days < 120:
            if peak_ndvi > 0.60:
                return "SHORT_HIGH_VIGOR"  # e.g., Tomato, Potato
            else:
                return "SHORT_MODERATE"    # e.g., Pulses, Leafy Greens
        elif duration_days < 240:
            if peak_ndvi > 0.70:
                return "MEDIUM_HIGH_VIGOR"  # e.g., Rice, Maize
            else:
                return "MEDIUM_MODERATE"    # e.g., Wheat, Millets
        else:
            return "LONG_DURATION"          # e.g., Sugarcane, Banana
    
    def _calculate_confidence(self, ndvi_segment: np.ndarray) -> float:
        """
        Calculate confidence score (0-100) for detected cycle
        
        Based on:
        - NDVI variation (higher = more confident)
        - Segment length (longer = more data = more confident)
        - Peak magnitude (higher = clearer signal)
        """
        if len(ndvi_segment) == 0:
            return 0.0
        
        # Component 1: NDVI variation (0-40 points)
        ndvi_std = np.std(ndvi_segment)
        variation_score = min(40, ndvi_std * 200)  # Scale to 0-40
        
        # Component 2: Segment length (0-30 points)
        length_score = min(30, len(ndvi_segment) / 20 * 30)
        
        # Component 3: Peak magnitude (0-30 points)
        peak_score = min(30, np.max(ndvi_segment) * 50)
        
        total_confidence = variation_score + length_score + peak_score
        return float(np.clip(total_confidence, 0, 100))
    
    def _validate_cycle(self, cycle: CropCycle) -> bool:
        """
        Validate detected cycle meets all quality criteria
        """
        # Check duration
        if not (self.min_duration_days <= cycle.duration_days <= self.max_duration_days):
            return False
        
        # Check peak NDVI
        if cycle.peak_ndvi < self.min_peak:
            return False
        
        # Check NDVI rise
        if cycle.ndvi_rise < self.min_ndvi_rise:
            return False
        
        # Check confidence
        if cycle.confidence < 30:
            return False
        
        return True
    
    def _merge_overlapping_cycles(
        self,
        cycles: List[CropCycle]
    ) -> List[CropCycle]:
        """
        Remove overlapping cycles, keeping the one with higher confidence
        """
        if len(cycles) <= 1:
            return cycles
        
        # Sort by sowing date
        sorted_cycles = sorted(cycles, key=lambda c: c.sowing_date)
        
        merged = []
        i = 0
        
        while i < len(sorted_cycles):
            current = sorted_cycles[i]
            
            # Check for overlap with next cycle
            if i + 1 < len(sorted_cycles):
                next_cycle = sorted_cycles[i + 1]
                
                # Overlap if next sowing is before current harvest
                if next_cycle.sowing_date < current.harvest_date:
                    # Keep the one with higher confidence
                    if next_cycle.confidence > current.confidence:
                        current = next_cycle
                    i += 2  # Skip both, we kept one
                else:
                    merged.append(current)
                    i += 1
            else:
                merged.append(current)
                i += 1
        
        return merged


# Export
__all__ = ['CropCycle', 'CropCycleDetector']
