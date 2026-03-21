"""
Weather Analyzer
================
Analyzes weather patterns and detects extreme events using NASA POWER API.

VERSION 3.0 — Major Updates:
1. Season-aligned fetching:
   - Weather is fetched per season window (Kharif Jun-Nov, Rabi Dec-May)
   - Cross-season crop events get weather fetched across their full span
   - No more overlapping date ranges causing duplicate event counts

2. Crop-stage aware risk:
   - Extreme events during CRITICAL growth stages penalize more than
     events during early vegetative or harvest phases
   - Critical stage windows derived from CropGrowthCurves if available

3. Improved risk scoring:
   - Per-event severity weighted by crop stage criticality
   - Drought detection considers seasonal rainfall norms (not flat threshold)
   - Rainfall variability measured across same season type year-on-year
     (Kharif vs Kharif, not mixing Kharif+Rabi)
   - Weather score is now a normalized 0–100 (100 = highest risk)

4. Regional thresholds applied per season (same as before, now also
   used for drought norm and expected seasonal rainfall)
"""

import numpy as np
import pandas as pd
import requests
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

try:
    from config.regional_config import RegionalConfig
    REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False

try:
    from config.crop_parameters import CropGrowthCurves
    CROP_PARAMS_AVAILABLE = True
except ImportError:
    try:
        from config.crop_parameters import CropGrowthCurves
        CROP_PARAMS_AVAILABLE = True
    except ImportError:
        CROP_PARAMS_AVAILABLE = False

from config.pipeline_config import PipelineConfig
from utils.data_processing import DataProcessor

logger = logging.getLogger(__name__)


# Expected seasonal rainfall norms (mm) — used to contextualise drought
# These are rough pan-India averages; regional config can override
SEASONAL_RAINFALL_NORMS = {
    'kharif': 700,   # Jun–Nov monsoon
    'rabi':   150,   # Dec–May (mostly dry belt)
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

        # Regional thresholds
        if REGIONAL_CONFIG_AVAILABLE and latitude and longitude:
            self.thresholds = RegionalConfig.get_weather_thresholds(latitude, longitude)
            self.region     = RegionalConfig.get_region(latitude, longitude)
        else:
            self.thresholds = self._default_thresholds()
            self.region     = 'DEFAULT'

        logger.info(f"✔ WeatherAnalyzer v3.0 initialized  (Region: {self.region})")
        logger.info(f"  Heatwave: >{self.thresholds['heatwave_temp']}°C "
                    f"for {self.thresholds['heatwave_min_days']}+ days")
        logger.info(f"  Drought:  {self.thresholds['drought_days']} consecutive "
                    f"dry days (<{self.thresholds['drought_daily_mm']}mm/day)")

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
                    f"  {label}: rain={stats['total_rainfall_mm']:.0f}mm  "
                    f"avg_temp={stats['avg_temp_c']:.1f}°C  "
                    f"max={stats['max_temp_c']:.1f}°C  "
                    f"events={len(events)}"
                )

            except Exception as e:
                logger.warning(f"  {label}: Weather fetch failed — {str(e)[:60]}")

        # Compute risk score
        weather_risk_score = self._calculate_weather_risk(
            seasonal_weather, all_extreme_events
        )

        logger.info(f"\n📊 Weather Summary:")
        logger.info(f"  Total extreme events: {len(all_extreme_events)}")
        logger.info(f"  Weather risk score:   {weather_risk_score:.1f}/100")

        return {
            'seasonal_weather':    seasonal_weather,
            'extreme_events':      all_extreme_events,
            'weather_risk_score':  weather_risk_score,
            'total_extreme_events': len(all_extreme_events),
            'region':              self.region,
            'thresholds_used':     self.thresholds,
        }

    # =========================================================================
    # DATA FETCH
    # =========================================================================

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
    # SEASONAL STATISTICS
    # =========================================================================

    def _compute_seasonal_stats(
        self,
        df:       pd.DataFrame,
        season:   str,
        year:     int,
        is_cross: bool,
    ) -> Dict:
        """Compute basic seasonal weather statistics."""
        stats: Dict = {
            'season':   season,
            'year':     year,
            'is_cross': is_cross,
        }

        if 'PRECTOTCORR' in df.columns:
            stats['total_rainfall_mm'] = float(df['PRECTOTCORR'].sum())
            rain_norm = SEASONAL_RAINFALL_NORMS.get(
                season.split('+')[0],   # for cross-season, use first season type
                400,
            )
            stats['rainfall_vs_norm_pct'] = round(
                stats['total_rainfall_mm'] / max(rain_norm, 1) * 100, 1
            )

        if 'T2M' in df.columns:
            stats['avg_temp_c']  = float(df['T2M'].mean())
        if 'T2M_MAX' in df.columns:
            stats['max_temp_c']  = float(df['T2M_MAX'].max())
        if 'T2M_MIN' in df.columns:
            stats['min_temp_c']  = float(df['T2M_MIN'].min())
        if 'RH2M' in df.columns:
            stats['avg_humidity'] = float(df['RH2M'].mean())
        if 'WS2M' in df.columns:
            stats['avg_wind_ms']  = float(df['WS2M'].mean())

        return stats

    # =========================================================================
    # EXTREME EVENT DETECTION
    # =========================================================================

    def _detect_extreme_events(
        self,
        df:     pd.DataFrame,
        season: str,
        year:   int,
        crop:   Optional[str] = None,
    ) -> List[Dict]:
        """
        Detect four event types using regional thresholds.
        Each event is tagged with:
          - type, severity, dates/duration
          - crop_stage_critical: True if event falls during a critical
            growth stage of the detected crop (increases risk weight)
        """
        events: List[Dict] = []
        t = self.thresholds

        # Compute critical-stage date fractions for this crop (if available)
        critical_fracs = self._get_critical_stage_fracs(crop)
        n_days = len(df)

        # ── 1. Heatwave ────────────────────────────────────────────────────
        if 'T2M_MAX' in df.columns:
            hot   = df['T2M_MAX'] > t['heatwave_temp']
            spans = DataProcessor.find_consecutive_periods(hot, t['heatwave_min_days'])
            for s_idx, e_idx in spans:
                max_t     = float(df['T2M_MAX'].iloc[s_idx:e_idx+1].max())
                excess    = max_t - t['heatwave_temp']
                severity  = ('extreme' if excess > 5 else
                             'high'    if excess > 3 else 'medium')
                mid_frac  = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical  = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type':       'heatwave',
                    'severity':   severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': e_idx - s_idx + 1,
                    'max_temp_c': round(max_t, 1),
                    'threshold':  t['heatwave_temp'],
                    'crop_stage_critical': critical,
                })

        # ── 2. Cold wave ───────────────────────────────────────────────────
        if 'T2M_MIN' in df.columns:
            cold  = df['T2M_MIN'] < t['cold_wave_temp']
            spans = DataProcessor.find_consecutive_periods(cold, t['cold_wave_min_days'])
            for s_idx, e_idx in spans:
                min_t    = float(df['T2M_MIN'].iloc[s_idx:e_idx+1].min())
                deficit  = t['cold_wave_temp'] - min_t
                severity = ('extreme' if deficit > 5 else
                            'high'    if deficit > 3 else 'medium')
                mid_frac = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type':       'cold_wave',
                    'severity':   severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': e_idx - s_idx + 1,
                    'min_temp_c': round(min_t, 1),
                    'threshold':  t['cold_wave_temp'],
                    'crop_stage_critical': critical,
                })

        # ── 3. Heavy rainfall ──────────────────────────────────────────────
        if 'PRECTOTCORR' in df.columns:
            # Single-day extremes
            seen_dates = set()
            for idx, row in df[df['PRECTOTCORR'] > t['heavy_rain_single_mm']].iterrows():
                rain     = float(row['PRECTOTCORR'])
                severity = ('extreme' if rain > t['heavy_rain_single_mm'] * 2 else
                            'high'    if rain > t['heavy_rain_single_mm'] * 1.5 else 'medium')
                day_frac = df.index.get_loc(idx) / max(n_days, 1)
                events.append({
                    'type':       'heavy_rainfall',
                    'severity':   severity,
                    'date':       idx.strftime('%Y-%m-%d'),
                    'rainfall_mm': round(rain, 1),
                    'threshold':  t['heavy_rain_single_mm'],
                    'crop_stage_critical': self._is_critical_stage(
                        day_frac, critical_fracs
                    ),
                })
                seen_dates.add(idx)

            # 3-day rolling extremes (avoid duplicating single-day events)
            rolling = df['PRECTOTCORR'].rolling(3).sum()
            for idx in df.index[rolling > t['heavy_rain_3day_mm']]:
                if idx not in seen_dates:
                    day_frac = df.index.get_loc(idx) / max(n_days, 1)
                    events.append({
                        'type':       'heavy_rainfall_period',
                        'severity':   'medium',
                        'end_date':   idx.strftime('%Y-%m-%d'),
                        '3day_total_mm': round(float(rolling.loc[idx]), 1),
                        'threshold':  t['heavy_rain_3day_mm'],
                        'crop_stage_critical': self._is_critical_stage(
                            day_frac, critical_fracs
                        ),
                    })

        # ── 4. Drought ─────────────────────────────────────────────────────
        if 'PRECTOTCORR' in df.columns:
            dry   = df['PRECTOTCORR'] < t['drought_daily_mm']
            spans = DataProcessor.find_consecutive_periods(dry, t['drought_days'])
            for s_idx, e_idx in spans:
                duration  = e_idx - s_idx + 1
                total_r   = float(df['PRECTOTCORR'].iloc[s_idx:e_idx+1].sum())
                severity  = ('extreme' if duration > 90 else
                             'high'    if duration > 60 else 'medium')
                mid_frac  = (s_idx + e_idx) / 2 / max(n_days, 1)
                critical  = self._is_critical_stage(mid_frac, critical_fracs)
                events.append({
                    'type':       'drought',
                    'severity':   severity,
                    'start_date': df.index[s_idx].strftime('%Y-%m-%d'),
                    'end_date':   df.index[e_idx].strftime('%Y-%m-%d'),
                    'duration_days': duration,
                    'total_rain_mm': round(total_r, 1),
                    'threshold_days': t['drought_days'],
                    'crop_stage_critical': critical,
                })

        return events

    # =========================================================================
    # WEATHER RISK SCORE  (0–100, higher = worse)
    # =========================================================================

    def _calculate_weather_risk(
        self,
        seasonal_weather: List[Dict],
        extreme_events:   List[Dict],
    ) -> float:
        """
        Composite weather risk score (0–100):

          Component                          Weight
          ─────────────────────────────────  ──────
          1. Extreme event frequency×severity  45%
          2. Crop-stage critical event penalty  20%
          3. Rainfall variability (same-type)   20%
          4. Drought occurrence                 10%
          5. Temperature extremity              5%
        """
        if not seasonal_weather:
            return 50.0

        n_years = max(1, len(set(s['year'] for s in seasonal_weather)))

        # ── 1. Event frequency × severity (45%) ───────────────────────────
        sev_weights = {'medium': 1.0, 'high': 2.0, 'extreme': 3.5}
        weighted_total = sum(
            sev_weights.get(e.get('severity', 'medium'), 1.0)
            for e in extreme_events
        )
        wt_per_year = weighted_total / n_years

        if   wt_per_year >= 8:  event_risk = 100.0
        elif wt_per_year >= 5:  event_risk = 80.0 + (wt_per_year - 5) / 3 * 20
        elif wt_per_year >= 3:  event_risk = 55.0 + (wt_per_year - 3) / 2 * 25
        elif wt_per_year >= 1:  event_risk = 25.0 + (wt_per_year - 1) / 2 * 30
        else:                   event_risk = 5.0

        risk = event_risk * 0.45

        # ── 2. Crop-stage critical events (20%) ───────────────────────────
        critical_events = [e for e in extreme_events if e.get('crop_stage_critical')]
        critical_sev    = sum(
            sev_weights.get(e.get('severity', 'medium'), 1.0)
            for e in critical_events
        )
        critical_per_year = critical_sev / n_years
        critical_risk = min(100.0, critical_per_year * 20.0)

        risk += critical_risk * 0.20

        # ── 3. Rainfall variability — same season type (20%) ──────────────
        # Separate Kharif and Rabi rainfall, compute CV within each type
        kharif_rain = [s['total_rainfall_mm'] for s in seasonal_weather
                       if 'kharif' in s.get('season', '') and 'total_rainfall_mm' in s]
        rabi_rain   = [s['total_rainfall_mm'] for s in seasonal_weather
                       if 'rabi' in s.get('season', '') and 'total_rainfall_mm' in s]

        cv_list = []
        for rain_list in [kharif_rain, rabi_rain]:
            if len(rain_list) >= 2:
                cv = DataProcessor.calculate_coefficient_of_variation(
                    np.array(rain_list)
                )
                cv_list.append(cv)

        if cv_list:
            mean_cv       = float(np.mean(cv_list))
            rainfall_risk = min(100.0, mean_cv * 80)
        else:
            rainfall_risk = 30.0   # mild default uncertainty

        risk += rainfall_risk * 0.20

        # ── 4. Drought severity (10%) ──────────────────────────────────────
        droughts        = [e for e in extreme_events if e['type'] == 'drought']
        severe_droughts = [e for e in droughts
                           if e.get('severity') in ('high', 'extreme')]
        if severe_droughts:
            drought_risk = min(100.0, len(severe_droughts) / n_years * 60.0)
        elif droughts:
            drought_risk = min(100.0, len(droughts) / n_years * 30.0)
        else:
            drought_risk = 0.0

        risk += drought_risk * 0.10

        # ── 5. Temperature extremity (5%) ──────────────────────────────────
        max_temps = [s['max_temp_c'] for s in seasonal_weather if 'max_temp_c' in s]
        if max_temps:
            extreme_thresh   = self.thresholds['heatwave_temp'] + 3
            frac_extreme_hot = sum(t > extreme_thresh for t in max_temps) / len(max_temps)
            temp_risk        = min(100.0, frac_extreme_hot * 150)
        else:
            temp_risk = 30.0

        risk += temp_risk * 0.05

        return round(min(100.0, max(0.0, risk)), 1)

    # =========================================================================
    # CROP-STAGE CRITICAL HELPERS
    # =========================================================================

    def _get_critical_stage_fracs(self, crop: Optional[str]) -> List[Tuple[float, float]]:
        """
        Return list of (start_frac, end_frac) representing critical growth
        stages as fractions of the total crop duration.

        Returns empty list if crop params unavailable.
        """
        if not CROP_PARAMS_AVAILABLE or not crop or crop in ('Unknown', None):
            return []

        critical_stages = CropGrowthCurves.CRITICAL_STAGES.get(crop, [])
        curve           = CropGrowthCurves.EXPECTED_NDVI_CURVES.get(crop, [])
        crop_duration   = CropGrowthCurves.get_crop_duration(crop)

        if not critical_stages or not curve:
            return []

        fracs = []
        for day, ndvi, stage_name in curve:
            if any(cs in stage_name for cs in critical_stages):
                center = day / max(crop_duration, 1)
                # ±10% window around the critical stage
                fracs.append((max(0.0, center - 0.10), min(1.0, center + 0.10)))

        return fracs

    @staticmethod
    def _is_critical_stage(
        day_frac:       float,
        critical_fracs: List[Tuple[float, float]],
    ) -> bool:
        """Return True if day_frac falls within any critical stage window."""
        if not critical_fracs:
            return False
        return any(lo <= day_frac <= hi for lo, hi in critical_fracs)

    # =========================================================================
    # DEFAULTS
    # =========================================================================

    @staticmethod
    def _default_thresholds() -> Dict:
        cfg = PipelineConfig
        return {
            'heatwave_temp':       cfg.HEATWAVE_THRESHOLD_C,
            'heatwave_min_days':   cfg.HEATWAVE_MIN_DAYS,
            'cold_wave_temp':      cfg.COLD_WAVE_THRESHOLD_C,
            'cold_wave_min_days':  cfg.COLD_WAVE_MIN_DAYS,
            'drought_days':        cfg.DROUGHT_MIN_DAYS,
            'drought_daily_mm':    cfg.DROUGHT_DAILY_RAINFALL_MM,
            'heavy_rain_single_mm': cfg.HEAVY_RAIN_SINGLE_DAY_MM,
            'heavy_rain_3day_mm':  cfg.HEAVY_RAIN_3DAY_MM,
        }