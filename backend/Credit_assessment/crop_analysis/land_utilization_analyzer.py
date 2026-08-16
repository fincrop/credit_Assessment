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
from typing import Any, Dict, List
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
            # Schema alias (C2): cultivated-day fraction, distinct from crops_per_year
            'land_utilization_fraction': round(utilization_index, 3),
            'cycle_duration_breakdown': cycle_duration_breakdown,
            'cropping_pattern': cropping_pattern,
            'cycles_in_analysis_window': len(cycles),
            'fallow_analysis': fallow_analysis,
            'crop_diversity': crop_diversity,
            # Kharif / rabi / zaid breakdown. New — nothing in the pipeline
            # previously read season_type, so no per-season analysis existed.
            'by_season': self._analyze_by_season(cycles, start_date, end_date),
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

        # Shares one occupancy definition with _analyze_fallow_periods, so
        # utilization + fallow always sum to 1. (Also replaces a day-by-day
        # loop that built a set of every calendar day in a 3-year window.)
        return self._occupied_days(cycles, start_date, end_date) / total_days
    
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
        # Inter-cycle gaps need at least two cycles, but the fallow FRACTION does
        # not — and the early return here used to skip it entirely, reporting
        # fallow_fraction 0.0 for a single cycle. A lone four-month crop in a
        # three-year window is overwhelmingly fallow land, not fully utilised.
        sorted_cycles = sorted(cycles, key=lambda c: c.sowing_date)

        fallow_periods = []
        for i in range(len(sorted_cycles) - 1):
            current_harvest = sorted_cycles[i].harvest_date
            next_sowing = sorted_cycles[i + 1].sowing_date

            if next_sowing > current_harvest:
                fallow_days = (next_sowing - current_harvest).days
                fallow_periods.append(fallow_days)

        # Fallow fraction, from the SAME occupancy definition the utilization
        # index uses.
        #
        # This previously summed c.duration_days, which double-counts wherever
        # cycles overlap — and phenology refinement can push adjacent cycles
        # into overlap. The result was that land_utilization_index (built from a
        # union of occupied days) and fallow_fraction (built from a sum) did not
        # add to 1, while fallow_fraction feeds the risk index at 15% weight.
        total_days = (end_date - start_date).days
        cultivation_days = self._occupied_days(cycles, start_date, end_date)
        fallow_days = max(0, total_days - cultivation_days)
        fallow_fraction = fallow_days / total_days if total_days > 0 else 0

        return {
            'fallow_periods': len(fallow_periods),
            'average_fallow_days': round(np.mean(fallow_periods), 1) if fallow_periods else 0,
            'longest_fallow_days': max(fallow_periods) if fallow_periods else 0,
            'fallow_fraction': round(fallow_fraction, 3),
            'cultivated_days': cultivation_days,
            'total_days': total_days,
            # Both figures now derive from one occupancy set, so this holds.
            'occupancy_basis': 'union_of_cultivated_days',
        }

    @staticmethod
    def _occupied_days(cycles: List, start_date: datetime, end_date: datetime) -> int:
        """
        Distinct calendar days covered by at least one cycle.

        Overlap-safe by construction: the single source of truth for "how much
        of the window was under cultivation", used by both the utilization index
        and the fallow fraction so the two cannot disagree.
        """
        if not cycles:
            return 0
        spans = []
        for c in cycles:
            lo = max(c.sowing_date, start_date)
            hi = min(c.harvest_date, end_date)
            if hi > lo:
                spans.append((lo, hi))
        if not spans:
            return 0

        spans.sort(key=lambda s: s[0])
        merged = [list(spans[0])]
        for lo, hi in spans[1:]:
            if lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        return sum((hi - lo).days for lo, hi in merged)

    def _analyze_by_season(self, cycles: List, start_date: datetime,
                           end_date: datetime) -> Dict:
        """
        Per-season breakdown: kharif / rabi / zaid.

        This did not exist. LandUtilizationAnalyzer never read season_type at
        all, and neither did the risk engine — so despite every cycle carrying a
        season label, there was no Kharif/Rabi/Zaid analysis anywhere in the
        pipeline. This is what makes "cropped in 3 of 3 kharif seasons but only
        1 of 3 rabi" reportable.
        """
        years = max(1, round((end_date - start_date).days / 365.0))
        seasons = ('kharif', 'rabi', 'zaid')
        out: Dict[str, Any] = {}

        for season in seasons:
            in_season = [
                c for c in cycles
                if str(getattr(c, 'season_type', '') or '').lower() == season
            ]
            durations = [c.duration_days for c in in_season]
            peaks = [
                float(getattr(c, 'peak_ndvi', 0.0) or 0.0)
                for c in in_season
                if getattr(c, 'peak_ndvi', None) is not None
            ]
            out[season] = {
                'n_cycles': len(in_season),
                # Fraction of available years in which this season was cropped.
                'years_cropped': len({c.sowing_date.year for c in in_season}),
                'years_available': years,
                'utilisation': round(
                    min(1.0, len({c.sowing_date.year for c in in_season}) / years), 3
                ),
                'mean_duration_days': round(float(np.mean(durations)), 1) if durations else None,
                'mean_peak_ndvi': round(float(np.mean(peaks)), 3) if peaks else None,
            }

        cross = [
            c for c in cycles
            if str(getattr(c, 'season_type', '') or '').lower() == 'cross_season'
        ]
        perennial = [
            c for c in cycles
            if str(getattr(c, 'season_type', '') or '').lower() == 'perennial'
        ]
        out['cross_season'] = {'n_cycles': len(cross)}
        out['perennial'] = {'n_production_years': len(perennial)}
        out['seasons_cropped'] = sum(
            1 for s in seasons if out[s]['n_cycles'] > 0
        )
        return out
    
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
            'land_utilization_fraction': 0.0,
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
