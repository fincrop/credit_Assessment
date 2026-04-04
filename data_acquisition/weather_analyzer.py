"""
Weather Analyzer â€” VERSION 4.0
================================
Context-aware weather risk analysis aligned to ACTIVE CROP INTERVALS.

KEY FIXES IN v4.0 (Stage 4 of pipeline refactoring)
------------------------------------------------------
1. REAL-DAY STAGE POSITIONING
   Event timing expressed as days since sowing, not df-index position.
   This ensures a heatwave at day 30 of a 120-day crop correctly maps
   to the vegetative stage, not mid-season.

2. GROWTH STAGE NAME TAGGING
   Every extreme event carries a `stage_name` field (e.g. 'FLOWERING',
   'GRAIN_FILL') so lenders and AI reports can explain impact
   in agronomic terms without knowing crop biology.

3. ADAPTIVE DROUGHT THRESHOLD
   Drought dry-day threshold now scaled by cycle length:
   short cycle (<=90d) â†’ lower bar; long cycle (>=150d) â†’ higher bar.
   Eliminates false droughts in short rain-fed cycles and missed droughts
   in long sugarcane-type cycles.

4. RAINFALL NORM BY CYCLE LENGTH
   Expected rainfall benchmarked against duration-proportional monthly norms
   (not fixed kharif=700mm / rabi=150mm regardless of actual cycle length).

5. CYCLE-SAFE RISK SCORING
   All risk components work with 'cycle_1', 'cycle_2' season labels
   (not just 'kharif'/'rabi' string lookups which broke in ENHANCED mode).

6. HUMAN-READABLE IMPACT NARRATIVE
   `crop_impact_narrative` field on every event explains the agricultural
   consequence in plain language for inclusion in lender/AI reports.
"""

import numpy as np
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

try:
        REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False

try:
    from config import CropGrowthCurves
    CROP_PARAMS_AVAILABLE = True
except ImportError:
    try:
        from config import CropGrowthCurves
        CROP_PARAMS_AVAILABLE = True
    except ImportError:
        CROP_PARAMS_AVAILABLE = False

from config import PipelineConfig
from utils.data_processing import DataProcessor

logger = logging.getLogger(__name__)


# Expected seasonal rainfall norms (mm) â€” used to contextualise drought
# These are rough pan-India averages; regional config can override
SEASONAL_RAINFALL_NORMS = {
    'kharif': 700,   # Junâ€“Nov monsoon
    'rabi':   150,   # Decâ€“May (mostly dry belt)
}


class WeatherAnalyzer:
    """
    Season-aligned, crop-stage-aware weather risk analyzer.
    Uses NASA POWER daily data for historical weather analysis.
    """

    def __init__(
        self,
        latitude:  Optional[float] = None,
        longitude: Optional[float] = None,
        verbose:   bool = True,
    ):
        self.base_url  = PipelineConfig.NASA_POWER_BASE_URL
        self.params    = PipelineConfig.WEATHER_PARAMETERS
        self.verbose   = verbose
        self.latitude  = latitude
        self.longitude = longitude

        # Unified thresholds
        self.thresholds = self._default_thresholds()
        self.region     = 'DEFAULT'

        logger.info("WeatherAnalyzer v4.0 initialized  (Region: %s)", self.region)
        logger.info(
            "  Heatwave: >%s degC for %s+ days",
            self.thresholds["heatwave_temp"],
            self.thresholds["heatwave_min_days"],
        )
        logger.info(
            "  Drought:  %s consecutive dry days (<%s mm/day)",
            self.thresholds["drought_days"],
            self.thresholds["drought_daily_mm"],
        )

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def analyze_seasonal_weather(
        self,
        latitude:      float,
        longitude:     float,
        seasonal_data: List[Dict],
        merged_seasons: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Fetch and analyze weather for all season windows.

        Uses merged_seasons (cross-season events) if available to fetch
        weather across the correct date span for long-duration crops.

        Args:
            latitude, longitude: field location
            seasonal_data:  raw season windows from SatelliteDataCollector
            merged_seasons: cross-season resolved windows (preferred)

        Returns:
            Dict: seasonal_weather, extreme_events, weather_risk_score,
                  total_extreme_events, region, thresholds_used
        """
        logger.info(f"\n{'='*70}")
        logger.info("ANALYZING WEATHER PATTERNS")
        logger.info(f"  Region: {self.region}")
        logger.info(f"{'='*70}")

        # Decide which season list drives the date windows
        analysis_windows = merged_seasons if merged_seasons else seasonal_data

        seasonal_weather = []
        all_extreme_events: List[Dict] = []

        for window in analysis_windows:
            start_date = window.get('start_date')
            end_date   = window.get('end_date')
            season     = window.get('season', 'unknown')
            year       = window.get('year', 0)
            crop       = window.get('predicted_crop')   # may be None at this stage
            is_cross   = window.get('is_cross_season', False)

            if not start_date or not end_date:
                continue

            label = f"{season.upper()} {year}" + (" [X-SEASON]" if is_cross else "")

            try:
                df = self._fetch_weather_data(latitude, longitude, start_date, end_date)
                if df.empty:
                    logger.warning(f"  {label}: No weather data returned")
                    continue

                # Basic seasonal statistics
                stats = self._compute_seasonal_stats(df, season, year, is_cross)

                # Detect extreme events (regional thresholds)
                events = self._detect_extreme_events(df, season, year, crop)
                stats['extreme_events'] = events
                all_extreme_events.extend(events)

                seasonal_weather.append(stats)

                logger.info(
                    "  %s: rain=%.0fmm  avg_temp=%.1f degC  max=%.1f degC  events=%d",
                    label,
                    stats["total_rainfall_mm"],
                    stats["avg_temp_c"],
                    stats["max_temp_c"],
                    len(events),
                )

            except Exception as e:
                logger.warning("  %s: Weather fetch failed - %s", label, str(e)[:60])

        # Compute risk score
        weather_risk_score = self._calculate_weather_risk(
            seasonal_weather, all_extreme_events
        )

        logger.info("\nWeather summary:")
        logger.info("  Total extreme events: %d", len(all_extreme_events))
        logger.info("  Weather risk score:   %.1f/100", weather_risk_score)

        return {
            'seasonal_weather':    seasonal_weather,
            'extreme_events':      all_extreme_events,
            'weather_risk_score':  weather_risk_score,
            'total_extreme_events': len(all_extreme_events),
            'region':              self.region,
            'thresholds_used':     self.thresholds,
        }

    def analyze_cycle_weather(
        self,
        latitude:   float,
        longitude:  float,
        crop_cycles_analysis: Dict,
    ) -> Dict:
        """
        Cycle-aligned, context-aware weather risk analysis  [v4.0]

        For each detected crop cycle:
          1. Fetches NASA POWER daily data across the EXACT growing window.
          2. Maps each extreme event to real DAYS-SINCE-SOWING using the
             cycle start_date as anchor (not df-index position).
          3. Tags every event with the crop growth stage it hit.
          4. Attaches a human-readable crop_impact_narrative for lenders/AI.
          5. Computes risk via _calculate_cycle_risk() â€” safe for any labels.

        Args:
            latitude, longitude: field location
            crop_cycles_analysis: result from CropDetector.analyze_cycles()

        Returns:
            Same schema as analyze_seasonal_weather() for drop-in compat.
        """
        logger.info(f"\n{'='*70}")
        logger.info("CYCLE-ALIGNED WEATHER ANALYSIS  (v4.0)")
        logger.info(f"  Region: {self.region}")
        logger.info(f"{'='*70}")

        season_results_list = crop_cycles_analysis.get('season_results', [])
        seasonal_weather: List[Dict] = []
        all_extreme_events: List[Dict] = []

        for result in season_results_list:
            if not result.get('crop_detected'):
                continue

            start_date    = result.get('start_date')
            end_date      = result.get('end_date')
            crop          = result.get('predicted_crop')          # may be None
            cycle_label   = result.get('season', 'cycle')
            duration_days = result.get('duration_days', 0)
            cult_signal   = result.get('cultivation_signal', 50.0)

            if not start_date or not end_date:
                continue

            crop_info = f" ({crop})" if crop else " (Unclassified)"
            label = f"{cycle_label.upper()} [{start_date} -> {end_date}]{crop_info}"

            try:
                df = self._fetch_weather_data(latitude, longitude, start_date, end_date)
                if df.empty:
                    logger.warning(f"  {label}: No weather data returned")
                    continue

                actual_duration = len(df)

                # â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                stats = self._compute_cycle_stats(
                    df, cycle_label, result.get('year', 0), actual_duration
                )
                stats.update({
                    'cycle_id':           cycle_label,
                    'crop':               crop or 'Unclassified',
                    'start_date':         start_date,
                    'end_date':           end_date,
                    'duration_days':      actual_duration,
                    'cultivation_signal': cult_signal,
                })

                # â”€â”€ Extreme events with real-day stage mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                events = self._detect_cycle_extreme_events(
                    df             = df,
                    cycle_label    = cycle_label,
                    year           = result.get('year', 0),
                    crop           = crop,
                    cycle_duration = actual_duration,
                )

                stats['extreme_events'] = events
                all_extreme_events.extend(events)
                seasonal_weather.append(stats)

                critical_ct = sum(1 for e in events if e.get('crop_stage_critical'))
                logger.info(
                    "  %s:  rain=%.0fmm  avg_temp=%.1f degC  events=%d  critical=%d",
                    label,
                    stats.get("total_rainfall_mm", 0),
                    stats.get("avg_temp_c", 0),
                    len(events),
                    critical_ct,
                )
                for ev in events:
                    crit_tag = " [!] CRITICAL" if ev.get("crop_stage_critical") else ""
                    logger.info(
                        "    - [%s] %s | stage=%s%s | %s",
                        ev["type"],
                        ev.get("severity", "?"),
                        ev.get("stage_name", "?"),
                        crit_tag,
                        ev.get("crop_impact_narrative", "")[:70],
                    )

            except Exception as e:
                logger.warning("  %s: Weather fetch failed - %s", label, str(e)[:60])

        # Risk score â€” cycle-safe (no kharif/rabi string dependency)
        weather_risk_score = self._calculate_cycle_risk(
            seasonal_weather, all_extreme_events
        )

        critical_count = sum(1 for ev in all_extreme_events if ev.get('crop_stage_critical'))
        logger.info("\nCycle weather summary:")
        logger.info("  Total extreme events:    %d", len(all_extreme_events))
        logger.info("  Critical-stage events:   %d", critical_count)
        logger.info("  Weather risk score:      %.1f/100", weather_risk_score)

        return {
            'seasonal_weather':      seasonal_weather,
            'extreme_events':        all_extreme_events,
            'weather_risk_score':    weather_risk_score,
            'total_extreme_events':  len(all_extreme_events),
            'critical_stage_events': critical_count,
            'region':                self.region,
            'thresholds_used':       self.thresholds,
            'analysis_mode':         'cycle_aligned_v4',
        }



    def _fetch_weather_data(
        self,
        latitude:   float,
        longitude:  float,
        start_date: str,
        end_date:   str,
    ) -> pd.DataFrame:
        """Fetch daily weather data from NASA POWER API."""
        params = {
            'parameters':  ','.join(self.params),
            'community':   PipelineConfig.NASA_POWER_COMMUNITY,
            'longitude':   longitude,
            'latitude':    latitude,
            'start':       start_date.replace('-', ''),
            'end':         end_date.replace('-', ''),
            'format':      'JSON',
        }
        for attempt in range(PipelineConfig.WEATHER_API_MAX_RETRIES):
            try:
                resp = requests.get(
                    self.base_url, params=params,
                    timeout=PipelineConfig.WEATHER_API_TIMEOUT,
                )
                resp.raise_for_status()
                data  = resp.json()
                props = data.get('properties', {}).get('parameter', {})
                if not props:
                    raise ValueError("Empty parameter block in response")

                df = pd.DataFrame(props)
                df.index = pd.to_datetime(df.index, format='%Y%m%d')
                df.index.name = 'date'
                df = df.replace(-999, np.nan)
                return df

            except Exception as e:
                if attempt == PipelineConfig.WEATHER_API_MAX_RETRIES - 1:
                    logger.error(f"  NASA POWER failed after {attempt+1} attempts: {e}")
                    return pd.DataFrame()
                time.sleep(2 ** attempt)

        return pd.DataFrame()

    # =========================================================================
    # SEASONAL / CYCLE STATISTICS
    # =========================================================================

    def _compute_cycle_stats(
        self,
        df:             pd.DataFrame,
        cycle_label:    str,
        year:           int,
        duration_days:  int,
    ) -> Dict:
        """
        Compute per-cycle weather statistics with duration-proportional norms.
        Expected rainfall = duration_days Ã— 3.5 mm/day (active growing season avg).
        This avoids over-penalising short Rabi cycles (â‰ˆ90d) vs long Kharif (â‰ˆ180d).
        """
        stats: Dict = {'season': cycle_label, 'year': year}

        if 'PRECTOTCORR' in df.columns:
            total_rain = float(df['PRECTOTCORR'].sum())
            stats['total_rainfall_mm']    = total_rain
            expected_rain                 = max(duration_days * 3.5, 1)
            stats['rainfall_vs_norm_pct'] = round(total_rain / expected_rain * 100, 1)

        if 'T2M'     in df.columns: stats['avg_temp_c']   = float(df['T2M'].mean())
        if 'T2M_MAX' in df.columns: stats['max_temp_c']   = float(df['T2M_MAX'].max())
        if 'T2M_MIN' in df.columns: stats['min_temp_c']   = float(df['T2M_MIN'].min())
        if 'RH2M'    in df.columns: stats['avg_humidity'] = float(df['RH2M'].mean())
        if 'WS2M'    in df.columns: stats['avg_wind_ms']  = float(df['WS2M'].mean())

        return stats

    def _compute_seasonal_stats(
        self,
        df:       pd.DataFrame,
        season:   str,
        year:     int,
        is_cross: bool,
    ) -> Dict:
        """Legacy seasonal stats â€” kept for BASIC mode / analyze_seasonal_weather."""
        stats: Dict = {'season': season, 'year': year, 'is_cross': is_cross}

        if 'PRECTOTCORR' in df.columns:
            stats['total_rainfall_mm'] = float(df['PRECTOTCORR'].sum())
            rain_norm = SEASONAL_RAINFALL_NORMS.get(season.split('+')[0], 400)
            stats['rainfall_vs_norm_pct'] = round(
                stats['total_rainfall_mm'] / max(rain_norm, 1) * 100, 1
            )

        if 'T2M'     in df.columns: stats['avg_temp_c']   = float(df['T2M'].mean())
        if 'T2M_MAX' in df.columns: stats['max_temp_c']   = float(df['T2M_MAX'].max())
        if 'T2M_MIN' in df.columns: stats['min_temp_c']   = float(df['T2M_MIN'].min())
        if 'RH2M'    in df.columns: stats['avg_humidity'] = float(df['RH2M'].mean())
        if 'WS2M'    in df.columns: stats['avg_wind_ms']  = float(df['WS2M'].mean())

        return stats

    # =========================================================================
    # EXTREME EVENT DETECTION  â€”  v4.0 Cycle-aligned (primary)
    # =========================================================================

    def _detect_cycle_extreme_events(
        self,
        df:             pd.DataFrame,
        cycle_label:    str,
        year:           int,
        crop:           Optional[str],
        cycle_duration: int,
    ) -> List[Dict]:
        """
        Detect extreme weather events anchored to ACTUAL CROP GROWING DAYS.

        Improvements vs v3:
          A. Event timing = real days-since-sowing (df-index date delta, not
             index-fraction). Eliminates mis-staging when weather data has gaps.
          B. stage_name on every event (VEGETATIVE / FLOWERING / GRAIN_FILL /
             RIPENING or named stages from CropGrowthCurves).
          C. Adaptive drought threshold scaled by cycle length:
             â‰¤90d â†’ drought_daysâˆ’5;  90â€“150d â†’ config value;  â‰¥150d â†’ +5.
          D. Drought severity expressed as % of cycle duration, not flat days.
          E. crop_impact_narrative: plain-English agronomic consequence.
        """
        events: List[Dict] = []
        t = self.thresholds

        # Adaptive drought threshold
        base_dt = t['drought_days']
        if   cycle_duration <= 90:  drought_thresh = max(15, base_dt - 5)
        elif cycle_duration >= 150: drought_thresh = base_dt + 5
        else:                       drought_thresh = base_dt

        # Critical stage fractions + named stage map from crop config
        critical_fracs, stage_map = self._get_critical_stage_fracs_v4(crop)
        n_days = len(df)

        def _days_since_sowing(idx_pos: int) -> int:
            if n_days == 0:
                return 0
            return int((df.index[idx_pos] - df.index[0]).days)

        def _stage_at_day(day: int) -> Tuple[bool, str]:
            frac = day / max(cycle_duration, 1)
            critical = any(lo <= frac <= hi for lo, hi in critical_fracs)
            for (lo, hi), name in stage_map.items():
                if lo <= frac < hi:
                    return critical, name
            if   frac < 0.25: return critical, 'VEGETATIVE'
            elif frac < 0.50: return critical, 'FLOWERING'
            elif frac < 0.75: return critical, 'GRAIN_FILL'
            else:             return critical, 'RIPENING'

        def _narrative(etype: str, stage: str, sev: str, **kw) -> str:
            consequences = {
                'VEGETATIVE': 'may slow early growth but crop can recover',
                'FLOWERING':  'high risk of flower abortion and yield loss',
                'GRAIN_FILL': 'severely reduces grain weight and quality',
                'RIPENING':   'may cause premature ripening or lodging',
            }
            impact = consequences.get(stage, 'affects crop at an active growth stage')
            if etype == 'heatwave':
                return (f"{sev.capitalize()} heatwave ({kw.get('max_t',0):.0f}Â°C) "
                        f"during {stage} â€” {impact}.")
            if etype == 'cold_wave':
                return (f"{sev.capitalize()} cold wave ({kw.get('min_t',0):.0f}Â°C) "
                        f"during {stage} â€” {impact}.")
            if etype == 'drought':
                return (f"{kw.get('dur',0)}-day drought ({kw.get('pct',0):.0f}% of cycle) "
                        f"during {stage} â€” {impact}. Deficit: {kw.get('tot',0):.0f}mm.")
            if etype in ('heavy_rainfall', 'heavy_rainfall_period'):
                return (f"Excess rainfall ({kw.get('rain',0):.0f}mm) during {stage} "
                        f"â€” risk of waterlogging and root disease.")
            return f"{etype} during {stage}."

        # â”€â”€ 1. Heatwave â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if 'T2M_MAX' in df.columns:
            hot   = df['T2M_MAX'] > t['heatwave_temp']
            spans = DataProcessor.find_consecutive_periods(hot, t['heatwave_min_days'])
            for s_idx, e_idx in spans:
                max_t   = float(df['T2M_MAX'].iloc[s_idx:e_idx+1].max())
                excess  = max_t - t['heatwave_temp']
                sev     = 'extreme' if excess > 5 else 'high' if excess > 3 else 'medium'
                mid_day = _days_since_sowing((s_idx + e_idx) // 2)
                crit, sname = _stage_at_day(mid_day)
                events.append({
                    'type':                  'heatwave',
                    'severity':              sev,
                    'start_date':            df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':              df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days':         e_idx - s_idx + 1,
                    'days_since_sowing':     mid_day,
                    'max_temp_c':            round(max_t, 1),
                    'threshold':             t['heatwave_temp'],
                    'stage_name':            sname,
                    'crop_stage_critical':   crit,
                    'cycle_id':              cycle_label,
                    'crop':                  crop or 'Unclassified',
                    'impact_severity':       'HIGH' if crit else ('MEDIUM' if sev == 'extreme' else 'LOW'),
                    'crop_impact_narrative': _narrative('heatwave', sname, sev, max_t=max_t),
                })

        # â”€â”€ 2. Cold wave â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if 'T2M_MIN' in df.columns:
            cold  = df['T2M_MIN'] < t['cold_wave_temp']
            spans = DataProcessor.find_consecutive_periods(cold, t['cold_wave_min_days'])
            for s_idx, e_idx in spans:
                min_t   = float(df['T2M_MIN'].iloc[s_idx:e_idx+1].min())
                deficit = t['cold_wave_temp'] - min_t
                sev     = 'extreme' if deficit > 5 else 'high' if deficit > 3 else 'medium'
                mid_day = _days_since_sowing((s_idx + e_idx) // 2)
                crit, sname = _stage_at_day(mid_day)
                events.append({
                    'type':                  'cold_wave',
                    'severity':              sev,
                    'start_date':            df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':              df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days':         e_idx - s_idx + 1,
                    'days_since_sowing':     mid_day,
                    'min_temp_c':            round(min_t, 1),
                    'threshold':             t['cold_wave_temp'],
                    'stage_name':            sname,
                    'crop_stage_critical':   crit,
                    'cycle_id':              cycle_label,
                    'crop':                  crop or 'Unclassified',
                    'impact_severity':       'HIGH' if crit else ('MEDIUM' if sev == 'extreme' else 'LOW'),
                    'crop_impact_narrative': _narrative('cold_wave', sname, sev, min_t=min_t),
                })

        # â”€â”€ 3. Heavy rainfall (single day + 3-day rolling) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if 'PRECTOTCORR' in df.columns:
            seen_dates = set()
            heavy_mask = df['PRECTOTCORR'] > t['heavy_rain_single_mm']
            for idx, row in df[heavy_mask].iterrows():
                rain    = float(row['PRECTOTCORR'])
                day_pos = df.index.get_loc(idx)
                days_ss = _days_since_sowing(day_pos)
                sev     = ('extreme' if rain > t['heavy_rain_single_mm'] * 2
                           else 'high' if rain > t['heavy_rain_single_mm'] * 1.5
                           else 'medium')
                crit, sname = _stage_at_day(days_ss)
                events.append({
                    'type':                  'heavy_rainfall',
                    'severity':              sev,
                    'date':                  idx.strftime('%Y-%m-%d'),
                    'days_since_sowing':     days_ss,
                    'rainfall_mm':           round(rain, 1),
                    'threshold':             t['heavy_rain_single_mm'],
                    'stage_name':            sname,
                    'crop_stage_critical':   crit,
                    'cycle_id':              cycle_label,
                    'crop':                  crop or 'Unclassified',
                    'impact_severity':       'HIGH' if crit else ('MEDIUM' if sev == 'extreme' else 'LOW'),
                    'crop_impact_narrative': _narrative('heavy_rainfall', sname, sev, rain=rain),
                })
                seen_dates.add(idx)

            rolling = df['PRECTOTCORR'].rolling(3).sum()
            for idx in df.index[rolling > t['heavy_rain_3day_mm']]:
                if idx not in seen_dates:
                    day_pos = df.index.get_loc(idx)
                    days_ss = _days_since_sowing(day_pos)
                    r3      = float(rolling.loc[idx])
                    crit, sname = _stage_at_day(days_ss)
                    events.append({
                        'type':                  'heavy_rainfall_period',
                        'severity':              'medium',
                        'end_date':              idx.strftime('%Y-%m-%d'),
                        'days_since_sowing':     days_ss,
                        '3day_total_mm':         round(r3, 1),
                        'threshold':             t['heavy_rain_3day_mm'],
                        'stage_name':            sname,
                        'crop_stage_critical':   crit,
                        'cycle_id':              cycle_label,
                        'crop':                  crop or 'Unclassified',
                        'impact_severity':       'HIGH' if crit else 'LOW',
                        'crop_impact_narrative': _narrative('heavy_rainfall_period', sname, 'medium', rain=r3),
                    })

        # â”€â”€ 4. Drought (adaptive threshold, %-of-cycle severity) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if 'PRECTOTCORR' in df.columns:
            dry   = df['PRECTOTCORR'] < t['drought_daily_mm']
            spans = DataProcessor.find_consecutive_periods(dry, drought_thresh)
            for s_idx, e_idx in spans:
                duration = e_idx - s_idx + 1
                total_r  = float(df['PRECTOTCORR'].iloc[s_idx:e_idx+1].sum())
                pct      = round(duration / max(cycle_duration, 1) * 100, 1)
                sev      = ('extreme' if pct > 70 else 'high' if pct > 40 else 'medium')
                mid_day  = _days_since_sowing((s_idx + e_idx) // 2)
                crit, sname = _stage_at_day(mid_day)
                events.append({
                    'type':                  'drought',
                    'severity':              sev,
                    'start_date':            df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':              df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days':         duration,
                    'days_since_sowing':     mid_day,
                    'total_rain_mm':         round(total_r, 1),
                    'threshold_days':        drought_thresh,
                    'pct_of_cycle':          pct,
                    'stage_name':            sname,
                    'crop_stage_critical':   crit,
                    'cycle_id':              cycle_label,
                    'crop':                  crop or 'Unclassified',
                    'impact_severity':       'HIGH' if crit else ('MEDIUM' if sev in ('high','extreme') else 'LOW'),
                    'crop_impact_narrative': _narrative('drought', sname, sev,
                                                        dur=duration, pct=pct, tot=total_r),
                })

        return events

    # â”€â”€ Legacy detection (BASIC mode / analyze_seasonal_weather) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _detect_extreme_events(
        self,
        df:     pd.DataFrame,
        season: str,
        year:   int,
        crop:   Optional[str] = None,
    ) -> List[Dict]:
        """Legacy extreme event detection (BASIC mode, fixed seasonal windows)."""
        events: List[Dict] = []
        t = self.thresholds
        critical_fracs = self._get_critical_stage_fracs(crop)
        n_days = len(df)

        if 'T2M_MAX' in df.columns:
            hot   = df['T2M_MAX'] > t['heatwave_temp']
            spans = DataProcessor.find_consecutive_periods(hot, t['heatwave_min_days'])
            for s_idx, e_idx in spans:
                max_t    = float(df['T2M_MAX'].iloc[s_idx:e_idx+1].max())
                excess   = max_t - t['heatwave_temp']
                severity = 'extreme' if excess > 5 else 'high' if excess > 3 else 'medium'
                mid_frac = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type': 'heatwave', 'severity': severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': e_idx - s_idx + 1,
                    'max_temp_c': round(max_t, 1),
                    'threshold':  t['heatwave_temp'],
                    'crop_stage_critical': critical,
                })

        if 'T2M_MIN' in df.columns:
            cold  = df['T2M_MIN'] < t['cold_wave_temp']
            spans = DataProcessor.find_consecutive_periods(cold, t['cold_wave_min_days'])
            for s_idx, e_idx in spans:
                min_t    = float(df['T2M_MIN'].iloc[s_idx:e_idx+1].min())
                deficit  = t['cold_wave_temp'] - min_t
                severity = 'extreme' if deficit > 5 else 'high' if deficit > 3 else 'medium'
                mid_frac = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type': 'cold_wave', 'severity': severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': e_idx - s_idx + 1,
                    'min_temp_c': round(min_t, 1),
                    'threshold':  t['cold_wave_temp'],
                    'crop_stage_critical': critical,
                })

        if 'PRECTOTCORR' in df.columns:
            seen_dates = set()
            for idx, row in df[df['PRECTOTCORR'] > t['heavy_rain_single_mm']].iterrows():
                rain     = float(row['PRECTOTCORR'])
                severity = ('extreme' if rain > t['heavy_rain_single_mm'] * 2
                            else 'high' if rain > t['heavy_rain_single_mm'] * 1.5
                            else 'medium')
                day_frac = df.index.get_loc(idx) / max(n_days, 1)
                events.append({
                    'type': 'heavy_rainfall', 'severity': severity,
                    'date': idx.strftime('%Y-%m-%d'),
                    'rainfall_mm': round(rain, 1),
                    'threshold':   t['heavy_rain_single_mm'],
                    'crop_stage_critical': self._is_critical_stage(day_frac, critical_fracs),
                })
                seen_dates.add(idx)

            rolling = df['PRECTOTCORR'].rolling(3).sum()
            for idx in df.index[rolling > t['heavy_rain_3day_mm']]:
                if idx not in seen_dates:
                    day_frac = df.index.get_loc(idx) / max(n_days, 1)
                    events.append({
                        'type': 'heavy_rainfall_period', 'severity': 'medium',
                        'end_date': idx.strftime('%Y-%m-%d'),
                        '3day_total_mm': round(float(rolling.loc[idx]), 1),
                        'threshold':     t['heavy_rain_3day_mm'],
                        'crop_stage_critical': self._is_critical_stage(day_frac, critical_fracs),
                    })

            dry   = df['PRECTOTCORR'] < t['drought_daily_mm']
            spans = DataProcessor.find_consecutive_periods(dry, t['drought_days'])
            for s_idx, e_idx in spans:
                duration  = e_idx - s_idx + 1
                total_r   = float(df['PRECTOTCORR'].iloc[s_idx:e_idx+1].sum())
                severity  = 'extreme' if duration > 90 else 'high' if duration > 60 else 'medium'
                mid_frac  = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical  = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type': 'drought', 'severity': severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': duration,
                    'total_rain_mm': round(total_r, 1),
                    'threshold_days': t['drought_days'],
                    'crop_stage_critical': critical,
                })

        return events

    # =========================================================================
    # WEATHER RISK SCORE â€” v4.0 cycle-aligned (primary)
    # =========================================================================

    def _calculate_cycle_risk(
        self,
        seasonal_weather: List[Dict],
        extreme_events:   List[Dict],
    ) -> float:
        """
        Cycle-aligned weather risk score (0â€“100, higher = worse)  [v4.0]

        Works with any cycle labels (cycle_1, cycle_2, â€¦), not just 'kharif'/'rabi'.

          Component                              Weight
          â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€  â”€â”€â”€â”€â”€â”€
          1. Extreme event frequency Ã— severity    40%
          2. Critical-stage events (raised)        30%
          3. Rainfall deficit or excess vs norm    15%
          4. Drought fraction of cycle             10%
          5. Temperature extremity                  5%
        """
        if not seasonal_weather:
            return 50.0

        n_cycles    = max(1, len(seasonal_weather))
        sev_weights = {'medium': 1.0, 'high': 2.0, 'extreme': 3.5}

        # Deduplicate events (some detectors can emit near-identical events).
        deduped: List[Dict] = []
        seen = set()
        for e in extreme_events:
            k = (
                e.get('cycle_id'),
                e.get('type'),
                e.get('severity'),
                e.get('date') or e.get('start_date'),
                e.get('end_date'),
            )
            if k in seen:
                continue
            seen.add(k)
            deduped.append(e)
        extreme_events = deduped

        # 1. Event frequency Ã— severity (40%)
        weighted_total = sum(
            sev_weights.get(e.get('severity', 'medium'), 1.0) for e in extreme_events
        )
        wt_per = weighted_total / n_cycles
        # Calibrated: avoid saturating risk from a handful of clustered cold-wave spans.
        # 0 -> 0, 4 -> 50, 8 -> 100
        event_risk = float(np.clip((wt_per / 8.0) * 100.0, 0.0, 100.0))
        risk = event_risk * 0.40

        # 2. Critical-stage events (30%)
        crit_sev = sum(
            sev_weights.get(e.get('severity', 'medium'), 1.0)
            for e in extreme_events if e.get('crop_stage_critical')
        )
        # Calibrated: keep critical-stage contribution meaningful but not dominating.
        risk += min(100.0, (crit_sev / n_cycles) * 18.0) * 0.30

        # 3. Rainfall as % of cycle-length norm (15%)
        rain_pcts = [
            s.get('rainfall_vs_norm_pct', 100)
            for s in seasonal_weather if 'rainfall_vs_norm_pct' in s
        ]
        if rain_pcts:
            deficit_ct = sum(1 for p in rain_pcts if p < 50)
            excess_ct  = sum(1 for p in rain_pcts if p > 200)
            rain_risk  = min(100.0, (deficit_ct + excess_ct) / len(rain_pcts) * 100)
        else:
            rain_risk  = 30.0
        risk += rain_risk * 0.15

        # 4. Drought fraction of cycle (10%)
        droughts = [e for e in extreme_events if e.get('type') == 'drought']
        if droughts:
            avg_pct      = float(np.mean([e.get('pct_of_cycle', 30) for e in droughts]))
            # avg_pct already represents fraction of the crop window; keep linear.
            drought_risk = min(100.0, avg_pct * 1.0)
        else:
            drought_risk = 0.0
        risk += drought_risk * 0.10

        # 5. Temperature extremity (5%)
        max_temps = [s['max_temp_c'] for s in seasonal_weather if 'max_temp_c' in s]
        if max_temps:
            extreme_thresh   = self.thresholds['heatwave_temp'] + 3
            frac_extreme_hot = sum(tt > extreme_thresh for tt in max_temps) / len(max_temps)
            temp_risk        = min(100.0, frac_extreme_hot * 150)
        else:
            temp_risk = 30.0
        risk += temp_risk * 0.05

        return round(min(100.0, max(0.0, risk)), 1)

    # â”€â”€ Legacy risk scorer (BASIC mode) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _calculate_weather_risk(
        self,
        seasonal_weather: List[Dict],
        extreme_events:   List[Dict],
    ) -> float:
        """
        Legacy composite weather risk score (0â€“100).
        Kept for analyze_seasonal_weather() (BASIC mode).
        """
        if not seasonal_weather:
            return 50.0

        n_years = max(1, len(set(s['year'] for s in seasonal_weather)))
        sev_weights = {'medium': 1.0, 'high': 2.0, 'extreme': 3.5}

        weighted_total = sum(
            sev_weights.get(e.get('severity', 'medium'), 1.0) for e in extreme_events
        )
        wt_per_year = weighted_total / n_years

        if   wt_per_year >= 8: event_risk = 100.0
        elif wt_per_year >= 5: event_risk = 80.0 + (wt_per_year - 5) / 3 * 20
        elif wt_per_year >= 3: event_risk = 55.0 + (wt_per_year - 3) / 2 * 25
        elif wt_per_year >= 1: event_risk = 25.0 + (wt_per_year - 1) / 2 * 30
        else:                  event_risk = 5.0

        risk = event_risk * 0.45

        critical_events = [e for e in extreme_events if e.get('crop_stage_critical')]
        critical_sev    = sum(sev_weights.get(e.get('severity', 'medium'), 1.0)
                              for e in critical_events)
        risk += min(100.0, (critical_sev / n_years) * 20.0) * 0.20

        kharif_rain = [s['total_rainfall_mm'] for s in seasonal_weather
                       if 'kharif' in s.get('season', '') and 'total_rainfall_mm' in s]
        rabi_rain   = [s['total_rainfall_mm'] for s in seasonal_weather
                       if 'rabi' in s.get('season', '') and 'total_rainfall_mm' in s]
        cv_list = []
        for rain_list in [kharif_rain, rabi_rain]:
            if len(rain_list) >= 2:
                cv_list.append(DataProcessor.calculate_coefficient_of_variation(
                    np.array(rain_list)
                ))
        rainfall_risk = float(np.mean(cv_list)) * 80 if cv_list else 30.0
        risk += min(100.0, rainfall_risk) * 0.20

        droughts        = [e for e in extreme_events if e['type'] == 'drought']
        severe_droughts = [e for e in droughts if e.get('severity') in ('high', 'extreme')]
        drought_risk    = (min(100.0, len(severe_droughts) / n_years * 60.0) if severe_droughts
                          else min(100.0, len(droughts) / n_years * 30.0) if droughts
                          else 0.0)
        risk += drought_risk * 0.10

        max_temps = [s['max_temp_c'] for s in seasonal_weather if 'max_temp_c' in s]
        if max_temps:
            extreme_thresh   = self.thresholds['heatwave_temp'] + 3
            frac_extreme_hot = sum(tt > extreme_thresh for tt in max_temps) / len(max_temps)
            temp_risk        = min(100.0, frac_extreme_hot * 150)
        else:
            temp_risk = 30.0
        risk += temp_risk * 0.05

        return round(min(100.0, max(0.0, risk)), 1)

    # =========================================================================
    # CROP-STAGE HELPERS  â€” v4.0
    # =========================================================================

    def _get_critical_stage_fracs_v4(
        self,
        crop: Optional[str],
    ) -> Tuple[List[Tuple[float, float]], Dict]:
        """
        Returns:
          fracs    : [(lo, hi), â€¦] for critical growth stages as cycle fractions
          stage_map: {(lo, hi): stage_name} for ALL stages (used by _stage_at_day)

        Falls back to a generic 4-quarter map when crop is unavailable.
        """
        generic_map = {
            (0.00, 0.25): 'VEGETATIVE',
            (0.25, 0.50): 'FLOWERING',
            (0.50, 0.75): 'GRAIN_FILL',
            (0.75, 1.00): 'RIPENING',
        }

        if not CROP_PARAMS_AVAILABLE or not crop or crop in ('Unknown', None, ''):
            return [], generic_map

        critical_stages = CropGrowthCurves.CRITICAL_STAGES.get(crop, [])
        curve           = CropGrowthCurves.EXPECTED_NDVI_CURVES.get(crop, [])
        crop_duration   = CropGrowthCurves.get_crop_duration(crop)

        if not critical_stages or not curve:
            return [], generic_map

        fracs, stage_map = [], {}
        for day, ndvi, stage_name in curve:
            frac = day / max(crop_duration, 1)
            lo   = round(max(0.0, frac - 0.05), 3)
            hi   = round(min(1.0, frac + 0.05), 3)
            stage_map[(lo, hi)] = stage_name
            if any(cs in stage_name for cs in critical_stages):
                fracs.append((max(0.0, frac - 0.10), min(1.0, frac + 0.10)))

        return fracs, (stage_map if stage_map else generic_map)

    def _get_critical_stage_fracs(
        self,
        crop: Optional[str],
    ) -> List[Tuple[float, float]]:
        """Legacy helper (BASIC mode only). Returns critical fracs list."""
        fracs, _ = self._get_critical_stage_fracs_v4(crop)
        return fracs

    @staticmethod
    def _is_critical_stage(
        day_frac:       float,
        critical_fracs: List[Tuple[float, float]],
    ) -> bool:
        """Return True if day_frac falls within any critical stage window."""
        return any(lo <= day_frac <= hi for lo, hi in critical_fracs)

    # =========================================================================
    # DEFAULTS
    # =========================================================================

    @staticmethod
    def _default_thresholds() -> Dict:
        cfg = PipelineConfig
        return {
            'heatwave_temp':        cfg.HEATWAVE_THRESHOLD_C,
            'heatwave_min_days':    cfg.HEATWAVE_MIN_DAYS,
            'cold_wave_temp':       cfg.COLD_WAVE_THRESHOLD_C,
            'cold_wave_min_days':   cfg.COLD_WAVE_MIN_DAYS,
            'drought_days':         cfg.DROUGHT_MIN_DAYS,
            'drought_daily_mm':     cfg.DROUGHT_DAILY_RAINFALL_MM,
            'heavy_rain_single_mm': cfg.HEAVY_RAIN_SINGLE_DAY_MM,
            'heavy_rain_3day_mm':   cfg.HEAVY_RAIN_3DAY_MM,
        }

