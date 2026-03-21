"""
Data Processing Utilities
==========================
Common data processing functions used across the pipeline.

Functions for:
- Array statistics
- Time-series processing
- Data validation
- Index calculations
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DataProcessor:
    """Common data processing utilities"""
    
    @staticmethod
    def calculate_array_stats(arr: np.ndarray) -> Dict[str, float]:
        """
        Calculate basic statistics for an array.
        
        Args:
            arr: Input numpy array
        
        Returns:
            Dictionary with mean, std, min, max, percentiles
        """
        valid = arr[~np.isnan(arr)]
        
        if len(valid) == 0:
            return {
                'mean': 0.0,
                'std': 0.0,
                'min': 0.0,
                'max': 0.0,
                'p10': 0.0,
                'p50': 0.0,
                'p90': 0.0,
                'count': 0
            }
        
        return {
            'mean': float(np.mean(valid)),
            'std': float(np.std(valid)),
            'min': float(np.min(valid)),
            'max': float(np.max(valid)),
            'p10': float(np.percentile(valid, 10)),
            'p50': float(np.percentile(valid, 50)),
            'p90': float(np.percentile(valid, 90)),
            'count': int(len(valid))
        }
    
    @staticmethod
    def align_arrays(arrays: List[np.ndarray]) -> List[np.ndarray]:
        """
        Align multiple arrays to the same shape (minimum dimensions).
        
        Args:
            arrays: List of 2D numpy arrays
        
        Returns:
            List of aligned arrays
        """
        if not arrays:
            return arrays
        
        min_height = min(arr.shape[0] for arr in arrays)
        min_width = min(arr.shape[1] for arr in arrays)
        
        aligned = [arr[:min_height, :min_width] for arr in arrays]
        
        return aligned
    
    @staticmethod
    def calculate_ndvi(
        nir: np.ndarray,
        red: np.ndarray
    ) -> np.ndarray:
        """
        Calculate NDVI (Normalized Difference Vegetation Index).
        
        NDVI = (NIR - RED) / (NIR + RED)
        
        Args:
            nir: Near-infrared band
            red: Red band
        
        Returns:
            NDVI array, clipped to [-1, 1]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            ndvi = (nir - red) / (nir + red + 1e-10)
            ndvi = np.where(np.isfinite(ndvi), np.clip(ndvi, -1, 1), np.nan)
        
        return ndvi
    
    @staticmethod
    def calculate_evi(
        nir: np.ndarray,
        red: np.ndarray,
        blue: np.ndarray,
        G: float = 2.5,
        C1: float = 6.0,
        C2: float = 7.5,
        L: float = 1.0
    ) -> np.ndarray:
        """
        Calculate EVI (Enhanced Vegetation Index).
        
        EVI = G * ((NIR - RED) / (NIR + C1*RED - C2*BLUE + L))
        
        Args:
            nir: Near-infrared band
            red: Red band
            blue: Blue band
            G, C1, C2, L: EVI coefficients
        
        Returns:
            EVI array, clipped to [-1, 3]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            evi = G * (nir - red) / (nir + C1 * red - C2 * blue + L + 1e-10)
            evi = np.where(np.isfinite(evi), np.clip(evi, -1, 3), np.nan)
        
        return evi
    
    @staticmethod
    def calculate_ndmi(
        nir: np.ndarray,
        swir: np.ndarray
    ) -> np.ndarray:
        """
        Calculate NDMI (Normalized Difference Moisture Index).
        
        NDMI = (NIR - SWIR) / (NIR + SWIR)
        
        Args:
            nir: Near-infrared band
            swir: Short-wave infrared band
        
        Returns:
            NDMI array, clipped to [-1, 1]
        """
        with np.errstate(divide='ignore', invalid='ignore'):
            ndmi = (nir - swir) / (nir + swir + 1e-10)
            ndmi = np.where(np.isfinite(ndmi), np.clip(ndmi, -1, 1), np.nan)
        
        return ndmi
    
    @staticmethod
    def validate_band_data(
        data: np.ndarray,
        min_valid_ratio: float = 0.15
    ) -> bool:
        """
        Validate satellite band data quality.
        
        Args:
            data: Band data array
            min_valid_ratio: Minimum ratio of valid (non-NaN) pixels
        
        Returns:
            True if data is valid, False otherwise
        """
        if data is None or data.size == 0:
            return False
        
        valid_ratio = np.sum(~np.isnan(data)) / data.size
        
        return valid_ratio >= min_valid_ratio
    
    @staticmethod
    def clean_satellite_data(
        data: np.ndarray,
        nodata_value: Optional[float] = None,
        min_value: float = 0.0,
        max_value: float = 10000.0
    ) -> np.ndarray:
        """
        Clean satellite band data.
        
        Args:
            data: Raw band data
            nodata_value: No-data sentinel value
            min_value: Minimum valid value
            max_value: Maximum valid value
        
        Returns:
            Cleaned array with invalid values set to NaN
        """
        cleaned = data.copy()
        
        # Replace nodata
        if nodata_value is not None:
            cleaned[cleaned == nodata_value] = np.nan
        
        # Remove outliers
        cleaned[cleaned < min_value] = np.nan
        cleaned[cleaned > max_value] = np.nan
        
        return cleaned
    
    @staticmethod
    def find_consecutive_periods(
        boolean_series: pd.Series,
        min_length: int = 3
    ) -> List[Tuple[int, int]]:
        """
        Find consecutive True periods in a boolean series.
        
        Args:
            boolean_series: Pandas Series of boolean values
            min_length: Minimum length of consecutive period
        
        Returns:
            List of (start_idx, end_idx) tuples
        """
        periods = []
        start_idx = None
        
        for i, val in enumerate(boolean_series):
            if val and start_idx is None:
                start_idx = i
            elif not val and start_idx is not None:
                if i - start_idx >= min_length:
                    periods.append((start_idx, i - 1))
                start_idx = None
        
        # Check last period
        if start_idx is not None and len(boolean_series) - start_idx >= min_length:
            periods.append((start_idx, len(boolean_series) - 1))
        
        return periods
    
    @staticmethod
    def parse_date_flexible(
        date_input: Any
    ) -> Optional[datetime]:
        """
        Parse date from various formats.
        
        Args:
            date_input: Date as string, datetime, or timestamp
        
        Returns:
            datetime object or None if parsing fails
        """
        if isinstance(date_input, datetime):
            return date_input
        
        if isinstance(date_input, pd.Timestamp):
            return date_input.to_pydatetime()
        
        if isinstance(date_input, str):
            try:
                return pd.to_datetime(date_input)
            except:
                pass
        
        return None
    
    @staticmethod
    def interpolate_time_series(
        dates: List[datetime],
        values: List[float],
        target_dates: List[datetime]
    ) -> np.ndarray:
        """
        Interpolate time-series values to target dates.
        
        Args:
            dates: Original observation dates
            values: Original values
            target_dates: Target dates for interpolation
        
        Returns:
            Interpolated values
        """
        # Convert dates to ordinal numbers
        x = np.array([d.toordinal() for d in dates])
        y = np.array(values)
        x_target = np.array([d.toordinal() for d in target_dates])
        
        # Interpolate
        y_interp = np.interp(x_target, x, y)
        
        return y_interp
    
    @staticmethod
    def calculate_cumulative_integral(
        x: np.ndarray,
        y: np.ndarray
    ) -> float:
        """
        Calculate area under curve using trapezoidal integration.
        
        Args:
            x: X values (e.g., days)
            y: Y values (e.g., NDVI)
        
        Returns:
            Area under curve
        """
        return float(np.trapz(y, x))
    
    @staticmethod
    def normalize_to_range(
        values: np.ndarray,
        target_min: float = 0.0,
        target_max: float = 1.0
    ) -> np.ndarray:
        """
        Normalize values to target range.
        
        Args:
            values: Input values
            target_min: Target minimum
            target_max: Target maximum
        
        Returns:
            Normalized values
        """
        v_min = np.nanmin(values)
        v_max = np.nanmax(values)
        
        if v_max == v_min:
            return np.full_like(values, target_min)
        
        normalized = (values - v_min) / (v_max - v_min)
        normalized = normalized * (target_max - target_min) + target_min
        
        return normalized
    
    @staticmethod
    def calculate_coefficient_of_variation(
        values: np.ndarray
    ) -> float:
        """
        Calculate coefficient of variation (CV = std / mean).
        
        Args:
            values: Input values
        
        Returns:
            Coefficient of variation
        """
        valid = values[~np.isnan(values)]
        
        if len(valid) == 0 or np.mean(valid) == 0:
            return 0.0
        
        return float(np.std(valid) / np.mean(valid))
    
    @staticmethod
    def resample_to_resolution(
        data: np.ndarray,
        current_resolution: int,
        target_resolution: int
    ) -> Tuple[int, int]:
        """
        Calculate output shape for resampling to target resolution.
        
        Args:
            data: Input array
            current_resolution: Current pixel resolution (meters)
            target_resolution: Target pixel resolution (meters)
        
        Returns:
            Tuple of (output_height, output_width)
        """
        scale = current_resolution / target_resolution
        
        out_height = max(10, int(data.shape[0] * scale))
        out_width = max(10, int(data.shape[1] * scale))
        
        return out_height, out_width
