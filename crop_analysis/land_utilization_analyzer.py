"""
Land Utilization Analyzer
==========================
VERSION 1.0

Analyzes land use intensity and patterns from detected crop cycles.
Provides crop-agnostic metrics that work without knowing specific crop types.

Key Metrics:
- Crops per year (cropping intensity)
- Land utilization index (% time under cultivation)
- Cycle duration breakdown
- Cropping pattern classification
- Fallow period analysis
"""

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List
from collections import Counter
import logging

logger = logging.getLogger(__name__)


class LandUtilizationAnalyzer:
    """
    Analyzes land utilization patterns from detected crop cycles
    """
    
    def analyze(
        self,
        cycles: List,  # List[CropCycle]
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """
        Comprehensive land utilization analysis
        
        Args:
            cycles: List of detected CropCycle objects
            start_date: Analysis period start
            end_date: Analysis period end
            
        Returns:
            Dict with utilization metrics and patterns
        """
        if not cycles:
            logger.warning("No cycles provided for utilization analysis")
            return self._empty_result()
        
        total_days = (end_date - start_date).days
        if total_days <= 0:
            logger.error("Invalid date range")
            return self._empty_result()
        
        logger.info(f"Analyzing land utilization from {len(cycles)} cycles over {total_days} days")
        
        # Calculate basic metrics
        crop_intensity = self._calculate_crop_intensity(cycles, total_days)
        utilization_index = self._calculate_utilization_index(cycles, start_date, end_date)
        cycle_duration_breakdown = self._analyze_cycle_durations(cycles)
        cropping_pattern = self._classify_cropping_pattern(cycles, total_days)
        fallow_analysis = self._analyze_fallow_periods(cycles, start_date, end_date)
        crop_diversity = self._analyze_crop_diversity(cycles)
        
        window_years = total_days / 365.0
        result = {
            'crop_intensity': round(crop_intensity, 2),
            # Backward/compat alias used by main + task descriptions
            'crops_per_year': round(crop_intensity, 2),
            'analysis_window_years': round(window_years, 3),
            'land_utilization_index': round(utilization_index, 3),
            'cycle_duration_breakdown': cycle_duration_breakdown,
            'cropping_pattern': cropping_pattern,
            'cycles_in_analysis_window': len(cycles),
            'fallow_analysis': fallow_analysis,
            'crop_diversity': crop_diversity,
            'total_cycles': len(cycles),
            'analysis_period_days': total_days
        }
        
        logger.info(f"Crop intensity: {crop_intensity:.2f} crops/year")
        logger.info(f"Land utilization: {utilization_index:.1%}")
        logger.info(f"Cropping pattern: {cropping_pattern}")
        
        return result
    
    def _calculate_crop_intensity(
        self,
        cycles: List,
        total_days: int
    ) -> float:
        """
        Calculate crops per year (annualized)
        
        Example:
        - 3 cycles in 365 days = 3.0 crops/year
        - 6 cycles in 730 days (2 years) = 3.0 crops/year
        - 2 cycles in 365 days = 2.0 crops/year (double cropping)
        """
        years = total_days / 365.0
        if years == 0:
            return 0.0
        
        crop_intensity = len(cycles) / years
        return crop_intensity
    
    def _calculate_utilization_index(
        self,
        cycles: List,
        start_date: datetime,
        end_date: datetime
    ) -> float:
        """
        Calculate land utilization index (0-1)
        
        = (Total days under cultivation) / (Total days in period)
        
        Handles overlapping cycles correctly
        """
        total_days = (end_date - start_date).days
        if total_days == 0:
            return 0.0
        
        # Create array of days
        occupied_days = set()
        
        for cycle in cycles:
            # Add each day from sowing to harvest
            current = cycle.sowing_date
            while current <= cycle.harvest_date and current <= end_date:
                if current >= start_date:
                    occupied_days.add(current.date())
                current += timedelta(days=1)
        
        cultivation_days = len(occupied_days)
        utilization_index = cultivation_days / total_days
        
        return utilization_index
    
    def _analyze_cycle_durations(self, cycles: List) -> Dict:
        """
        Breakdown of cycle durations into categories
        
        Short: <120 days (vegetables, pulses)
        Medium: 120-240 days (cereals, oilseeds)
        Long: >240 days (sugarcane, perennials)
        """
        short_cycles = [c for c in cycles if c.duration_days < 120]
        medium_cycles = [c for c in cycles if 120 <= c.duration_days < 240]
        long_cycles = [c for c in cycles if c.duration_days >= 240]
        
        avg_duration = np.mean([c.duration_days for c in cycles]) if cycles else 0
        
        return {
            'short_duration_count': len(short_cycles),
            'medium_duration_count': len(medium_cycles),
            'long_duration_count': len(long_cycles),
            'average_duration_days': round(avg_duration, 1),
            'short_duration_fraction': round(len(short_cycles) / len(cycles), 2) if cycles else 0,
            'medium_duration_fraction': round(len(medium_cycles) / len(cycles), 2) if cycles else 0,
            'long_duration_fraction': round(len(long_cycles) / len(cycles), 2) if cycles else 0
        }
    
    def _classify_cropping_pattern(
        self,
        cycles: List,
        total_days: int
    ) -> str:
        """
        Classify the overall cropping pattern
        
        Patterns:
        - INTENSIVE_MULTIPLE (3+ crops/year)
        - DOUBLE_CROPPING (2-3 crops/year)
        - LONG_DURATION_DOMINANT (mostly long cycles)
        - MULTI_YEAR_SINGLE_CROP (several cycles, but spread over multi-year window
          so annualized intensity is low — avoids confusion with SINGLE_SEASON)
        - SINGLE_SEASON (few cycles relative to span)
        """
        years = total_days / 365.0
        crops_per_year = len(cycles) / years if years > 0 else 0
        
        # Check duration distribution
        long_cycles = sum(1 for c in cycles if c.duration_days >= 240)
        long_fraction = long_cycles / len(cycles) if cycles else 0
        
        if crops_per_year >= 3.0:
            return "INTENSIVE_MULTIPLE"
        elif crops_per_year >= 2.0:
            if long_fraction > 0.5:
                return "DOUBLE_WITH_LONG_DURATION"
            else:
                return "DOUBLE_CROPPING"
        elif long_fraction > 0.5:
            return "LONG_DURATION_DOMINANT"
        elif len(cycles) >= 2 and crops_per_year < 2.0:
            # e.g. 2 cycles across ~3.6 years ⇒ ~0.56 crops/year: not "single season" in plain language
            return "MULTI_YEAR_SINGLE_CROP"
        else:
            return "SINGLE_SEASON"
    
    def _analyze_fallow_periods(
        self,
        cycles: List,
        start_date: datetime,
        end_date: datetime
    ) -> Dict:
        """
        Analyze fallow (uncultivated) periods between crops
        """
        if len(cycles) < 2:
            return {
                'fallow_periods': 0,
                'average_fallow_days': 0,
                'longest_fallow_days': 0,
                'fallow_fraction': 1.0 if len(cycles) == 0 else 0.0
            }
        
        # Sort cycles by sowing date
        sorted_cycles = sorted(cycles, key=lambda c: c.sowing_date)
        
        fallow_periods = []
        
        # Gaps between consecutive cycles
        for i in range(len(sorted_cycles) - 1):
            current_harvest = sorted_cycles[i].harvest_date
            next_sowing = sorted_cycles[i + 1].sowing_date
            
            if next_sowing > current_harvest:
                fallow_days = (next_sowing - current_harvest).days
                fallow_periods.append(fallow_days)
        
        # Calculate fallow fraction
        total_days = (end_date - start_date).days
        cultivation_days = sum(c.duration_days for c in cycles)
        fallow_days = max(0, total_days - cultivation_days)
        fallow_fraction = fallow_days / total_days if total_days > 0 else 0
        
        return {
            'fallow_periods': len(fallow_periods),
            'average_fallow_days': round(np.mean(fallow_periods), 1) if fallow_periods else 0,
            'longest_fallow_days': max(fallow_periods) if fallow_periods else 0,
            'fallow_fraction': round(fallow_fraction, 3)
        }
    
    def _analyze_crop_diversity(self, cycles: List) -> Dict:
        """
        Analyze crop type diversity (based on classified types)
        """
        if not cycles:
            return {
                'unique_crop_types': 0,
                'crop_type_distribution': {},
                'shannon_index': 0.0
            }
        
        # Count crop types
        crop_types = [c.crop_type for c in cycles if c.crop_type]
        type_counts = Counter(crop_types)
        
        # Shannon diversity index
        shannon_index = 0.0
        total = len(crop_types)
        if total > 0:
            for count in type_counts.values():
                proportion = count / total
                if proportion > 0:
                    shannon_index -= proportion * np.log(proportion)
        
        return {
            'unique_crop_types': len(type_counts),
            'crop_type_distribution': dict(type_counts),
            'shannon_index': round(shannon_index, 2)
        }
    
    def _empty_result(self) -> Dict:
        """Return empty result structure"""
        return {
            'crop_intensity': 0.0,
            'crops_per_year': 0.0,
            'analysis_window_years': 0.0,
            'land_utilization_index': 0.0,
            'cycle_duration_breakdown': {
                'short_duration_count': 0,
                'medium_duration_count': 0,
                'long_duration_count': 0,
                'average_duration_days': 0,
                'short_duration_fraction': 0,
                'medium_duration_fraction': 0,
                'long_duration_fraction': 0
            },
            'cropping_pattern': 'NO_DATA',
            'fallow_analysis': {
                'fallow_periods': 0,
                'average_fallow_days': 0,
                'longest_fallow_days': 0,
                'fallow_fraction': 1.0
            },
            'crop_diversity': {
                'unique_crop_types': 0,
                'crop_type_distribution': {},
                'shannon_index': 0.0
            },
            'cycles_in_analysis_window': 0,
            'total_cycles': 0,
            'analysis_period_days': 0
        }


# Export
__all__ = ['LandUtilizationAnalyzer']
