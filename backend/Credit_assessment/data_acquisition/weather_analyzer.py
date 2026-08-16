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

import json
import numpy as np
import pandas as pd
import requests
import time
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
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

# Optional backends for additional weather sources (all guarded — the analyzer
# always works on NASA POWER without them).
try:
    import ee  # ERA5-Land + CHIRPS via Google Earth Engine
    _EE_OK = True
except Exception:  # pragma: no cover
    ee = None
    _EE_OK = False

try:
    import imdlib as _imd  # canonical reader for IMD gridded (rain/tmax/tmin)
    _IMDLIB_OK = True
except Exception:  # pragma: no cover
    _imd = None
    _IMDLIB_OK = False

logger = logging.getLogger(__name__)

# Module-level in-memory fallback when Mongo weather_power_cache is unavailable
_POWER_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}

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
        interval_days: Optional[int] = None,
        mongo_helper: Optional[Any] = None,
    ):
        self.base_url  = PipelineConfig.NASA_POWER_BASE_URL
        self.params    = PipelineConfig.WEATHER_PARAMETERS
        self.verbose   = verbose
        self.latitude  = latitude
        self.longitude = longitude
        self.mongo_helper = mongo_helper
        self.interval_days = int(
            interval_days
            if interval_days is not None
            else getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)
        )

        self.thresholds = self._default_thresholds()
        self.region     = 'DEFAULT'
        # Per-analyze fetch accounting for weather_degraded / weather_data_status
        self._fetch_attempted = 0
        self._fetch_ok = 0
        self._fetch_short = 0
        self._fetch_failed = 0
        self._last_weather_source = None

        if self.verbose:
            logger.info("WeatherAnalyzer v4.0 initialized (Region: %s)", self.region)
            logger.info(
                "  Static fallback heatwave: >%s C for %s+ d | satellite interval=%s d",
                self.thresholds["heatwave_temp"],
                self.thresholds["heatwave_min_days"],
                self.interval_days,
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
        if self.verbose:
            logger.info(f"\n{'='*70}")
            logger.info("ANALYZING WEATHER PATTERNS")
            logger.info(f"  Region: {self.region}")
            logger.info(f"{'='*70}")

        # Decide which season list drives the date windows
        analysis_windows = merged_seasons if merged_seasons else seasonal_data

        self._reset_fetch_counters()
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
                self._fetch_attempted += 1
                df = self._fetch_weather_data(latitude, longitude, start_date, end_date)
                if df.empty:
                    self._fetch_failed += 1
                    logger.warning(f"  {label}: No weather data returned")
                    continue

                if self._series_too_short(df, start_date, end_date):
                    self._fetch_short += 1
                else:
                    self._fetch_ok += 1

                # Basic seasonal statistics
                stats = self._compute_seasonal_stats(df, season, year, is_cross)

                events, t_used, thr_mode = self._detect_extreme_events(df, season, year, crop)
                stats['extreme_events'] = events
                stats['weather_thresholds_used'] = t_used
                stats['weather_threshold_mode'] = thr_mode
                all_extreme_events.extend(events)

                seasonal_weather.append(stats)

                if self.verbose:
                    logger.info(
                        "  %s: rain=%.0fmm  avg_temp=%.1f degC  max=%.1f degC  events=%d",
                        label,
                        stats["total_rainfall_mm"],
                        stats["avg_temp_c"],
                        stats["max_temp_c"],
                        len(events),
                    )

            except Exception as e:
                self._fetch_failed += 1
                logger.warning("  %s: Weather fetch failed - %s", label, str(e)[:60])

        # Compute risk score
        weather_risk_score = self._calculate_weather_risk(
            seasonal_weather, all_extreme_events
        )

        if self.verbose:
            logger.info("\nWeather summary:")
            logger.info("  Total extreme events: %d", len(all_extreme_events))
            logger.info("  Weather risk score:   %.1f/100", weather_risk_score)

        status_flags = self._weather_status_flags(len(seasonal_weather))
        return {
            'seasonal_weather':    seasonal_weather,
            'extreme_events':      all_extreme_events,
            'weather_risk_score':  weather_risk_score,
            'total_extreme_events': len(all_extreme_events),
            'region':              self.region,
            'thresholds_used':     self.thresholds,
            'interval_days':       self.interval_days,
            **status_flags,
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
        if self.verbose:
            logger.info(f"\n{'='*70}")
            logger.info("CYCLE-ALIGNED WEATHER ANALYSIS  (v4.0)")
            logger.info(f"  Region: {self.region}")
            logger.info(f"{'='*70}")

        season_results_list = crop_cycles_analysis.get('season_results', [])
        seasonal_weather: List[Dict] = []
        all_extreme_events: List[Dict] = []
        self._reset_fetch_counters()

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
                self._fetch_attempted += 1
                df = self._fetch_weather_data(latitude, longitude, start_date, end_date)
                if df.empty:
                    self._fetch_failed += 1
                    logger.warning(f"  {label}: No weather data returned")
                    continue

                if self._series_too_short(df, start_date, end_date):
                    self._fetch_short += 1
                else:
                    self._fetch_ok += 1

                actual_duration = len(df)

                # â”€â”€ Stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                stats = self._compute_cycle_stats(
                    df, cycle_label, result.get('year', 0), actual_duration
                )
                stats.update({
                    'cycle_id':           cycle_label,
                    # Carried from the detected cycle so downstream consumers can
                    # attribute weather to kharif/rabi/zaid. 'season' remains the
                    # positional cycle id ('cycle_1', ...), which is NOT a season
                    # name — anything grouping by season must read season_type.
                    'season_type':        result.get('season_type'),
                    'season_label':       result.get('season_label'),
                    'crop':               crop or 'Unclassified',
                    'start_date':         start_date,
                    'end_date':           end_date,
                    'duration_days':      actual_duration,
                    'cultivation_signal': cult_signal,
                })

                # â”€â”€ Extreme events with real-day stage mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                events, t_used, thr_mode = self._detect_cycle_extreme_events(
                    df             = df,
                    cycle_label    = cycle_label,
                    year           = result.get('year', 0),
                    crop           = crop,
                    cycle_duration = actual_duration,
                )

                stats['extreme_events'] = events
                stats['weather_thresholds_used'] = t_used
                stats['weather_threshold_mode'] = thr_mode

                # Pillar 4: agronomic indicators + backward resilience per cycle.
                indicators = self._weather_indicators(df, latitude, actual_duration, cycle_label)
                stats['weather_indicators'] = indicators
                stats['weather_source'] = self._last_weather_source or 'power'
                veg_series = [
                    (sc.get('indices', {}) or {}).get('VS_mean',
                        (sc.get('indices', {}) or {}).get('NDVI_mean'))
                    for sc in sorted(result.get('scenes') or [], key=lambda x: x.get('date', ''))
                ]
                res = self._cycle_resilience(veg_series, indicators, events)
                if res is not None:
                    stats['resilience'] = res

                all_extreme_events.extend(events)
                seasonal_weather.append(stats)

                critical_ct = sum(1 for e in events if e.get('crop_stage_critical'))
                if self.verbose:
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
                self._fetch_failed += 1
                logger.warning("  %s: Weather fetch failed - %s", label, str(e)[:60])

        # Risk score — cycle-safe (no kharif/rabi string dependency)
        weather_risk_score = self._calculate_cycle_risk(
            seasonal_weather, all_extreme_events
        )

        critical_count = sum(1 for ev in all_extreme_events if ev.get('crop_stage_critical'))
        if self.verbose:
            logger.info("\nCycle weather summary:")
            logger.info("  Total extreme events:    %d", len(all_extreme_events))
            logger.info("  Critical-stage events:   %d", critical_count)
            logger.info("  Weather risk score:      %.1f/100", weather_risk_score)

        cycle_risk_scores = [
            {
                'cycle_id':   s.get('cycle_id'),
                'risk_score': WeatherAnalyzer._per_cycle_weather_risk(s),
                'n_events':   len(s.get('extreme_events') or []),
            }
            for s in seasonal_weather
        ]

        # Pillar 4: parcel-relative SPI/SPEI + two-directional summary.
        self._standardize_spi_spei(seasonal_weather)
        forward_exposure = self._forward_exposure(seasonal_weather)
        _res = [
            s['resilience']['score'] for s in seasonal_weather
            if isinstance(s.get('resilience'), dict)
            and s['resilience'].get('tested') and s['resilience'].get('score') is not None
        ]
        backward_resilience = {
            'tested_cycles': len(_res),
            'mean_resilience_score': round(float(np.mean(_res)), 1) if _res else None,
            'note': 'vigor retention under adverse weather; None = never stress-tested in window',
        }
        weather_sources_used = sorted(
            {s.get('weather_source') for s in seasonal_weather if s.get('weather_source')}
        )

        status_flags = self._weather_status_flags(len(seasonal_weather))
        return {
            'seasonal_weather':      seasonal_weather,
            'extreme_events':        all_extreme_events,
            'weather_risk_score':    weather_risk_score,
            'total_extreme_events':  len(all_extreme_events),
            'critical_stage_events': critical_count,
            'cycle_risk_scores':     cycle_risk_scores,
            # Pillar 4 additions (two-directional + richer indicators)
            'weather_indicators_present': True,
            'forward_exposure':      forward_exposure,
            'backward_resilience':   backward_resilience,
            'weather_sources_used':  weather_sources_used,
            'region':                self.region,
            'thresholds_used':       self.thresholds,
            'interval_days':         self.interval_days,
            'analysis_mode':         'cycle_aligned_v5',
            **status_flags,
        }



    def _fetch_weather_data(
        self,
        latitude:   float,
        longitude:  float,
        start_date: str,
        end_date:   str,
    ) -> pd.DataFrame:
        """
        Multi-source daily weather fetch (Pillar 4). Tries sources in
        WEATHER_SOURCE_PRIORITY order; the first returning a usable frame wins
        and ``self._last_weather_source`` records it. NASA POWER is the default,
        always-available fallback. IMD/ERA5/CHIRPS are guarded hooks that return
        empty until a loader/credentials are configured, so the pipeline
        degrades gracefully to POWER.
        """
        self._last_weather_source = None
        priority = list(getattr(PipelineConfig, "WEATHER_SOURCE_PRIORITY", ["power"]) or ["power"])
        if "power" not in priority:
            priority.append("power")  # guarantee a fallback
        for src in priority:
            try:
                df = self._fetch_from_source(src, latitude, longitude, start_date, end_date)
            except Exception as e:
                logger.debug("weather source %s failed: %s", src, e)
                df = pd.DataFrame()
            if df is not None and not df.empty:
                self._last_weather_source = src
                return df
        self._last_weather_source = None
        return pd.DataFrame()

    def _fetch_from_source(
        self, source: str, latitude: float, longitude: float,
        start_date: str, end_date: str,
    ) -> pd.DataFrame:
        s = (source or "power").strip().lower()
        if s == "power":
            return self._fetch_power(latitude, longitude, start_date, end_date)
        if s == "imd":
            return self._fetch_imd(latitude, longitude, start_date, end_date)
        if s == "era5":
            return self._fetch_era5(latitude, longitude, start_date, end_date)
        if s == "chirps":
            return self._fetch_chirps(latitude, longitude, start_date, end_date)
        logger.debug("unknown weather source '%s'", source)
        return pd.DataFrame()

    def _fetch_imd(self, latitude, longitude, start_date, end_date) -> pd.DataFrame:
        """
        IMD 0.25deg rainfall + 1deg temperature (authoritative for India) via the
        canonical `imdlib` reader. Requires pre-downloaded IMD yearly files in
        WEATHER_IMD_DATA_DIR. Returns the POWER-schema df, or empty (-> POWER) if
        imdlib / data / config is unavailable.
        """
        if not getattr(PipelineConfig, "WEATHER_IMD_ENABLE", False):
            return pd.DataFrame()
        data_dir = getattr(PipelineConfig, "WEATHER_IMD_DATA_DIR", None)
        if not (_IMDLIB_OK and data_dir):
            logger.info("IMD enabled but imdlib/WEATHER_IMD_DATA_DIR unavailable; falling back")
            return pd.DataFrame()
        try:
            y0 = int(start_date[:4])
            y1 = int(end_date[:4])
            frames = {}
            for var, col in (("rain", "PRECTOTCORR"), ("tmax", "T2M_MAX"), ("tmin", "T2M_MIN")):
                try:
                    ds = _imd.open_data(var, y0, y1, "yearwise", data_dir).get_xarray()
                    pt = ds.sel(lat=float(latitude), lon=float(longitude), method="nearest")
                    ser = pt.to_series()
                    frames[col] = ser
                except Exception as ve:
                    logger.debug("IMD var %s unavailable: %s", var, ve)
            if "PRECTOTCORR" not in frames and "T2M_MAX" not in frames:
                return pd.DataFrame()
            df = pd.DataFrame(frames)
            df.index = pd.to_datetime(df.index)
            df.index.name = "date"
            df = df.loc[start_date:end_date]
            # Fill/derive the POWER-schema companions.
            if "T2M_MAX" in df and "T2M_MIN" in df:
                df["T2M"] = (df["T2M_MAX"] + df["T2M_MIN"]) / 2.0
            df = df.replace([-999.0, 99.9], np.nan)
            return df.dropna(how="all")
        except Exception as e:
            logger.warning("IMD fetch failed (%s); falling back", str(e)[:120])
            return pd.DataFrame()

    def _fetch_era5(self, latitude, longitude, start_date, end_date) -> pd.DataFrame:
        """
        ERA5-Land daily via GEE (ECMWF/ERA5_LAND/DAILY_AGGR), mapped to the POWER
        schema (T2M/T2M_MAX/T2M_MIN in C, PRECTOTCORR in mm, RH2M from dewpoint,
        WS2M from 10m wind). Guarded: empty -> POWER fallback.
        """
        if not getattr(PipelineConfig, "WEATHER_ERA5_ENABLE", False):
            return pd.DataFrame()
        if not (_EE_OK and ee is not None):
            return pd.DataFrame()
        try:
            pt = ee.Geometry.Point([float(longitude), float(latitude)])
            coll = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
                    .filterDate(start_date, end_date)
                    .select(["temperature_2m", "temperature_2m_max", "temperature_2m_min",
                             "dewpoint_temperature_2m", "total_precipitation_sum",
                             "u_component_of_wind_10m", "v_component_of_wind_10m"]))

            def _feat(img):
                stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=pt,
                                         scale=11132, bestEffort=True, maxPixels=1e8)
                return ee.Feature(None, ee.Dictionary(stats).combine(
                    ee.Dictionary({"date": img.date().format("YYYY-MM-dd")})))

            feats = coll.map(_feat).getInfo().get("features", [])
            rows = []
            for f in feats:
                p = f.get("properties", {})
                t = p.get("temperature_2m")
                tmax = p.get("temperature_2m_max")
                tmin = p.get("temperature_2m_min")
                td = p.get("dewpoint_temperature_2m")
                pr = p.get("total_precipitation_sum")
                u = p.get("u_component_of_wind_10m")
                v = p.get("v_component_of_wind_10m")
                if t is None:
                    continue
                t_c = t - 273.15
                row = {
                    "date": p.get("date"),
                    "T2M": t_c,
                    "T2M_MAX": (tmax - 273.15) if tmax is not None else np.nan,
                    "T2M_MIN": (tmin - 273.15) if tmin is not None else np.nan,
                    "PRECTOTCORR": (pr * 1000.0) if pr is not None else np.nan,  # m -> mm
                }
                if td is not None:
                    td_c = td - 273.15
                    # Magnus RH from T and dewpoint.
                    row["RH2M"] = float(np.clip(
                        100.0 * np.exp((17.625 * td_c) / (243.04 + td_c))
                        / np.exp((17.625 * t_c) / (243.04 + t_c)), 0, 100))
                if u is not None and v is not None:
                    row["WS2M"] = float(np.sqrt(u ** 2 + v ** 2) * 0.75)  # 10m -> ~2m
                rows.append(row)
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df.pop("date"))
            df.index.name = "date"
            return df.sort_index()
        except Exception as e:
            logger.warning("ERA5 fetch failed (%s); falling back", str(e)[:120])
            return pd.DataFrame()

    def _fetch_chirps(self, latitude, longitude, start_date, end_date) -> pd.DataFrame:
        """
        CHIRPS daily rainfall via GEE (UCSB-CHG/CHIRPS/DAILY) -> PRECTOTCORR (mm).
        Rainfall-only; the indicator/stat code degrades gracefully (skips GDD/PET)
        when temperature columns are absent, so CHIRPS is usable as a rainfall
        source or cross-check. Guarded: empty -> POWER fallback.
        """
        if not getattr(PipelineConfig, "WEATHER_CHIRPS_ENABLE", False):
            return pd.DataFrame()
        if not (_EE_OK and ee is not None):
            return pd.DataFrame()
        try:
            pt = ee.Geometry.Point([float(longitude), float(latitude)])
            coll = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                    .filterDate(start_date, end_date).select("precipitation"))

            def _feat(img):
                stats = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=pt,
                                         scale=5566, bestEffort=True, maxPixels=1e8)
                return ee.Feature(None, ee.Dictionary(stats).combine(
                    ee.Dictionary({"date": img.date().format("YYYY-MM-dd")})))

            feats = coll.map(_feat).getInfo().get("features", [])
            rows = [{"date": f["properties"].get("date"),
                     "PRECTOTCORR": f["properties"].get("precipitation")}
                    for f in feats if f.get("properties", {}).get("precipitation") is not None]
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows)
            df.index = pd.to_datetime(df.pop("date"))
            df.index.name = "date"
            return df.sort_index()
        except Exception as e:
            logger.warning("CHIRPS fetch failed (%s); falling back", str(e)[:120])
            return pd.DataFrame()

    def _fetch_power(
        self,
        latitude:   float,
        longitude:  float,
        start_date: str,
        end_date:   str,
    ) -> pd.DataFrame:
        """Fetch daily weather data from NASA POWER API (with Mongo/memory cache)."""
        cache_key = self._power_cache_key(latitude, longitude, start_date, end_date)
        cached = self._cache_get(cache_key)
        if cached is not None:
            try:
                return self._payload_to_df(cached)
            except Exception as e:
                logger.debug("weather cache decode failed (%s); refetching", e)

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
                self._cache_put(
                    cache_key,
                    self._df_to_payload(df),
                    metadata={
                        'lat': round(float(latitude), 3),
                        'lon': round(float(longitude), 3),
                        'start': start_date,
                        'end': end_date,
                    },
                )
                return df

            except Exception as e:
                if attempt == PipelineConfig.WEATHER_API_MAX_RETRIES - 1:
                    logger.error(f"  NASA POWER failed after {attempt+1} attempts: {e}")
                    return pd.DataFrame()
                time.sleep(2 ** attempt)

        return pd.DataFrame()

    def _reset_fetch_counters(self) -> None:
        self._fetch_attempted = 0
        self._fetch_ok = 0
        self._fetch_short = 0
        self._fetch_failed = 0

    @staticmethod
    def _power_cache_key(
        latitude: float, longitude: float, start_date: str, end_date: str
    ) -> str:
        return (
            f"{round(float(latitude), 3):.3f}|"
            f"{round(float(longitude), 3):.3f}|"
            f"{start_date}|{end_date}"
        )

    def _cache_get(self, cache_key: str) -> Optional[Dict]:
        helper = self.mongo_helper
        if helper is not None and hasattr(helper, "get_weather_power_cache"):
            try:
                hit = helper.get_weather_power_cache(cache_key)
                if hit is not None:
                    return hit
            except Exception as e:
                logger.debug("weather mongo cache get failed: %s", e)
        return _POWER_MEMORY_CACHE.get(cache_key)

    def _cache_put(
        self,
        cache_key: str,
        payload: Dict,
        metadata: Optional[Dict] = None,
    ) -> None:
        helper = self.mongo_helper
        wrote = False
        if helper is not None and hasattr(helper, "upsert_weather_power_cache"):
            try:
                wrote = bool(
                    helper.upsert_weather_power_cache(
                        cache_key, payload, metadata=metadata, ttl_days=30
                    )
                )
            except Exception as e:
                logger.debug("weather mongo cache put failed: %s", e)
        if not wrote:
            _POWER_MEMORY_CACHE[cache_key] = payload

    @staticmethod
    def _df_to_payload(df: pd.DataFrame) -> Dict:
        # orient=split is compact and reconstructs cleanly (NaN → null in JSON)
        return json.loads(df.to_json(orient="split", date_format="iso"))

    @staticmethod
    def _payload_to_df(payload: Dict) -> pd.DataFrame:
        df = pd.DataFrame(
            payload.get("data") or [],
            columns=payload.get("columns") or [],
            index=pd.to_datetime(payload.get("index") or []),
        )
        df.index.name = "date"
        return df

    @staticmethod
    def _series_too_short(
        df: pd.DataFrame, start_date: str, end_date: str
    ) -> bool:
        min_obs = int(getattr(PipelineConfig, "WEATHER_DYNAMIC_MIN_OBS", 21))
        n = len(df)
        if n < min_obs:
            return True
        try:
            start = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
            end = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
            expected = max(1, (end - start).days + 1)
            # Treat as short if we got under half the window (partial POWER fill)
            if n < max(min_obs, int(expected * 0.5)):
                return True
        except (TypeError, ValueError):
            pass
        return False

    def _weather_status_flags(self, n_series: int) -> Dict[str, Any]:
        """
        Derive weather_degraded + weather_data_status from this analyze pass.
        ok | degraded | unavailable
        """
        attempted = self._fetch_attempted
        if attempted == 0 or n_series == 0:
            status = "unavailable"
            degraded = True
        elif self._fetch_failed > 0 or self._fetch_short > 0:
            status = "degraded"
            degraded = True
        else:
            status = "ok"
            degraded = False
        return {
            "weather_degraded": degraded,
            "weather_data_status": status,
            "weather_fetch_stats": {
                "attempted": attempted,
                "ok": self._fetch_ok,
                "short_series": self._fetch_short,
                "failed": self._fetch_failed,
            },
        }

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

    # =========================================================================
    # PILLAR 4 — weather indicators (SPI/SPEI, spells, GDD, onset) +
    #            two-directional risk (backward resilience / forward exposure)
    # =========================================================================

    @staticmethod
    def _max_run(mask) -> int:
        """Longest run of consecutive True values."""
        best = cur = 0
        for v in mask:
            if bool(v):
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return int(best)

    @staticmethod
    def _hargreaves_pet(df: pd.DataFrame, latitude: Optional[float]) -> Optional[np.ndarray]:
        """
        Daily Hargreaves reference ET0 (mm/day) from Tmax/Tmin + top-of-atmosphere
        radiation (needs latitude + day-of-year). Used for SPEI / water balance.
        """
        if not all(c in df.columns for c in ("T2M_MAX", "T2M_MIN")):
            return None
        try:
            lat = math.radians(float(latitude or 0.0))
            doy = df.index.dayofyear.to_numpy()
            dr = 1.0 + 0.033 * np.cos(2.0 * np.pi / 365.0 * doy)
            dec = 0.409 * np.sin(2.0 * np.pi / 365.0 * doy - 1.39)
            ws = np.arccos(np.clip(-np.tan(lat) * np.tan(dec), -1.0, 1.0))
            Ra = (24.0 * 60.0 / np.pi) * 0.0820 * dr * (
                ws * np.sin(lat) * np.sin(dec)
                + np.cos(lat) * np.cos(dec) * np.sin(ws)
            )
            Ra_mm = 0.408 * Ra  # MJ/m2/day -> equivalent mm/day
            tmax = df["T2M_MAX"].to_numpy(dtype=float)
            tmin = df["T2M_MIN"].to_numpy(dtype=float)
            tmean = (tmax + tmin) / 2.0
            tr = np.clip(tmax - tmin, 0.0, None)
            pet = 0.0023 * Ra_mm * (tmean + 17.8) * np.sqrt(tr)
            return np.clip(pet, 0.0, None)
        except Exception:
            return None

    def _monsoon_onset_offset(self, df: pd.DataFrame) -> Optional[int]:
        """
        Monsoon onset timing anomaly (days late[+]/early[-]) for cycles spanning
        the onset window: first day whose 3-day rain >= threshold in Jun/Jul,
        compared to a configurable normal onset day-of-year.
        """
        if "PRECTOTCORR" not in df.columns or df.empty:
            return None
        months = df.index.month.to_numpy()
        if not np.any((months >= 6) & (months <= 7)):
            return None
        roll = df["PRECTOTCORR"].fillna(0).rolling(3, min_periods=1).sum()
        thr = float(getattr(PipelineConfig, "MONSOON_ONSET_3DAY_MM", 15.0))
        onset_dt = None
        for dt, v in roll.items():
            if dt.month in (6, 7) and v >= thr:
                onset_dt = dt
                break
        if onset_dt is None:
            return None
        normal_doy = int(getattr(PipelineConfig, "MONSOON_NORMAL_ONSET_DOY", 160))
        return int(onset_dt.dayofyear - normal_doy)

    def _weather_indicators(
        self,
        df: pd.DataFrame,
        latitude: Optional[float],
        duration_days: int,
        cycle_label: str,
    ) -> Dict:
        """
        Per-cycle agronomic weather indicators. SPI/SPEI-like standardization is
        added AFTER the cycle loop (parcel-relative). Returns a dict; 'available'
        is False when the weather series is missing.
        """
        P = PipelineConfig
        ind: Dict[str, Any] = {"cycle_id": cycle_label}
        if df is None or df.empty:
            ind["available"] = False
            return ind
        ind["available"] = True

        if "PRECTOTCORR" in df.columns:
            rain = df["PRECTOTCORR"].fillna(0).to_numpy(dtype=float)
            wet_thr = float(getattr(P, "WEATHER_WET_DAY_MM", 2.5))
            heavy_thr = float(getattr(P, "WEATHER_HEAVY_RAIN_FLOOR_MM", 42.0))
            ind["total_rainfall_mm"] = round(float(rain.sum()), 1)
            ind["rain_total_raw"] = float(rain.sum())
            ind["max_dry_spell_days"] = self._max_run(rain < wet_thr)
            ind["max_wet_spell_days"] = self._max_run(rain >= wet_thr)
            ind["n_heavy_rain_days"] = int(np.sum(rain >= heavy_thr))

        if all(c in df.columns for c in ("T2M_MAX", "T2M_MIN")):
            tmax = df["T2M_MAX"].to_numpy(dtype=float)
            tmin = df["T2M_MIN"].to_numpy(dtype=float)
            tbase = float(getattr(P, "GDD_BASE_C", 10.0))
            tcap = float(getattr(P, "GDD_CAP_C", 35.0))
            tmean = np.clip((tmax + tmin) / 2.0, None, tcap)
            gdd = np.clip(tmean - tbase, 0.0, None)
            ind["gdd_total"] = round(float(np.nansum(gdd)), 1)
            ind["heat_stress_days"] = int(np.nansum(tmax > float(getattr(P, "HEAT_STRESS_TMAX_C", 38.0))))
            ind["cold_stress_days"] = int(np.nansum(tmin < float(getattr(P, "COLD_STRESS_TMIN_C", 8.0))))

        pet = self._hargreaves_pet(df, latitude)
        if pet is not None:
            ind["pet_total_mm"] = round(float(np.nansum(pet)), 1)
            if "PRECTOTCORR" in df.columns:
                rain_sum = float(df["PRECTOTCORR"].fillna(0).sum())
                ind["water_balance_mm"] = round(rain_sum - float(np.nansum(pet)), 1)
                ind["water_balance_raw"] = rain_sum - float(np.nansum(pet))
                ind["aridity_ratio"] = round(rain_sum / max(float(np.nansum(pet)), 1e-6), 3)

        onset = self._monsoon_onset_offset(df)
        if onset is not None:
            ind["monsoon_onset_offset_days"] = onset

        return ind

    def _cycle_resilience(
        self,
        veg_series: List[Optional[float]],
        indicators: Dict,
        events: List[Dict],
    ) -> Optional[Dict]:
        """
        Backward-looking resilience: did the parcel's vegetation hold up UNDER
        adverse weather? Only 'tested' when stress signals are present. Score is
        the achieved canopy vigor despite stress (high peak vigor under stress =
        resilient; poor vigor under stress = not).
        """
        vals = [float(x) for x in (veg_series or []) if x is not None and np.isfinite(x)]
        if len(vals) < 4:
            return None
        peak = float(np.max(vals))

        P = PipelineConfig
        stress_signals = 0
        if indicators.get("max_dry_spell_days", 0) >= int(getattr(P, "RESILIENCE_DRY_SPELL_DAYS", 21)):
            stress_signals += 1
        if indicators.get("heat_stress_days", 0) >= int(getattr(P, "RESILIENCE_HEAT_DAYS", 7)):
            stress_signals += 1
        if any(e.get("crop_stage_critical") for e in (events or [])):
            stress_signals += 1

        if stress_signals == 0:
            return {"tested": False, "score": None, "peak_vigor": round(peak, 3)}

        lo = float(getattr(P, "RESILIENCE_VIGOR_LO", 0.30))
        hi = float(getattr(P, "RESILIENCE_VIGOR_HI", 0.70))
        score = float(np.clip((peak - lo) / max(hi - lo, 1e-6) * 100.0, 0.0, 100.0))
        return {
            "tested": True,
            "score": round(score, 1),
            "peak_vigor": round(peak, 3),
            "stress_signals": stress_signals,
        }

    @staticmethod
    def _forward_exposure(seasonal_weather: List[Dict]) -> Dict:
        """
        Forward-looking regional exposure derived from the parcel's OWN multi-year
        adverse-event frequency (drought / heat / flood). Historical-derived now;
        the same field is the hook for a real regional-climatology blend later.
        """
        n = max(1, len(seasonal_weather))
        def _freq(pred) -> float:
            return sum(1 for s in seasonal_weather if pred((s.get("weather_indicators") or {}))) / n
        drought = _freq(lambda wi: wi.get("max_dry_spell_days", 0) >= 21)
        heat = _freq(lambda wi: wi.get("heat_stress_days", 0) >= 7)
        flood = _freq(lambda wi: wi.get("n_heavy_rain_days", 0) >= 3)
        exposure = min(100.0, 100.0 * (0.5 * drought + 0.3 * heat + 0.2 * flood))
        return {
            "drought_freq": round(drought, 2),
            "heat_freq": round(heat, 2),
            "flood_freq": round(flood, 2),
            "exposure_score": round(exposure, 1),
            "basis": "historical_cycles",
            "note": "historical-derived; hook for regional climatology blend",
        }

    @staticmethod
    def _standardize_spi_spei(seasonal_weather: List[Dict]) -> None:
        """
        Add parcel-relative SPI-like (rainfall) and SPEI-like (P-PET) z-scores to
        each cycle's indicators, standardized across the parcel's own cycles.
        Real long-record SPI/SPEI is a documented future upgrade.
        """
        def _z(key_raw: str, key_out: str):
            vals = [
                (s.get("weather_indicators") or {}).get(key_raw)
                for s in seasonal_weather
                if (s.get("weather_indicators") or {}).get("available")
            ]
            vals = [float(v) for v in vals if v is not None and np.isfinite(v)]
            if len(vals) < 3:
                return
            mu = float(np.mean(vals))
            sd = float(np.std(vals)) or 1e-6
            for s in seasonal_weather:
                wi = s.get("weather_indicators") or {}
                raw = wi.get(key_raw)
                if wi.get("available") and raw is not None and np.isfinite(raw):
                    wi[key_out] = round(float((float(raw) - mu) / sd), 2)
                    wi[f"{key_out}_basis"] = "parcel_cycle_relative"
        _z("rain_total_raw", "spi_like")
        _z("water_balance_raw", "spei_like")

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
    # DYNAMIC THRESHOLDS (per cycle, aligned with satellite sampling interval)
    # =========================================================================

    def _cycle_extreme_thresholds(
        self,
        df: pd.DataFrame,
        cycle_duration: int,
    ) -> Tuple[Dict, str]:
        """
        Build temperature/rain/drought cutoffs from this cycle's NASA POWER series.
        Spell lengths scale with ``self.interval_days`` (same as satellite grid step).
        Falls back to ``self.thresholds`` when disabled or data are too short.
        """
        base = dict(self.thresholds)
        P = PipelineConfig
        if not getattr(P, "WEATHER_USE_DYNAMIC_THRESHOLDS", True):
            return base, "fixed_config"

        n = len(df)
        min_obs = int(getattr(P, "WEATHER_DYNAMIC_MIN_OBS", 21))
        if n < min_obs:
            return base, "fixed_short_series"

        interval = max(3, int(self.interval_days or 10))
        spell = max(
            int(base["heatwave_min_days"]),
            int(np.ceil(interval / 3.0)),
        )

        t = dict(base)
        t["heatwave_min_days"] = spell
        t["cold_wave_min_days"] = spell

        try:
            if "T2M_MAX" in df.columns:
                tmax = df["T2M_MAX"].dropna().astype(float)
                if len(tmax) >= 14:
                    qh = float(np.nanpercentile(tmax.values, P.WEATHER_DYNAMIC_HEAT_PERCENTILE))
                    t["heatwave_temp"] = float(
                        np.clip(
                            qh + float(P.WEATHER_DYNAMIC_HEAT_DELTA_C),
                            float(P.WEATHER_DYNAMIC_HEAT_FLOOR_C),
                            float(P.WEATHER_DYNAMIC_HEAT_CAP_C),
                        )
                    )

            if "T2M_MIN" in df.columns:
                tmin = df["T2M_MIN"].dropna().astype(float)
                if len(tmin) >= 14:
                    qc = float(np.nanpercentile(tmin.values, P.WEATHER_DYNAMIC_COLD_PERCENTILE))
                    t["cold_wave_temp"] = float(
                        min(
                            float(P.WEATHER_DYNAMIC_COLD_CAP_C),
                            max(
                                float(P.WEATHER_DYNAMIC_COLD_FLOOR_C),
                                qc - float(P.WEATHER_DYNAMIC_COLD_DELTA_C),
                            ),
                        )
                    )

            if "PRECTOTCORR" in df.columns:
                prect = df["PRECTOTCORR"].fillna(0).astype(float)
                wet = prect[prect > 0.5]
                if len(wet) >= 7:
                    t["heavy_rain_single_mm"] = float(
                        max(
                            float(P.WEATHER_HEAVY_RAIN_FLOOR_MM),
                            float(np.nanpercentile(wet.values, P.WEATHER_HEAVY_RAIN_WET_PERCENTILE)),
                        )
                    )
                roll3 = prect.rolling(3, min_periods=3).sum().dropna()
                if len(roll3) >= 14:
                    t["heavy_rain_3day_mm"] = float(
                        max(
                            float(P.WEATHER_HEAVY_3DAY_FLOOR_MM),
                            float(np.nanpercentile(roll3.values, P.WEATHER_HEAVY_3DAY_PERCENTILE)),
                        )
                    )

                dry_guess = float(
                    np.nanpercentile(prect.values, float(P.WEATHER_DROUGHT_DRY_PERCENTILE))
                )
                t["drought_daily_mm"] = float(
                    np.clip(
                        dry_guess,
                        float(P.WEATHER_DROUGHT_DAILY_FLOOR_MM),
                        float(P.WEATHER_DROUGHT_DAILY_CEILING_MM),
                    )
                )

                dspell = max(
                    int(P.WEATHER_DROUGHT_MIN_DAYS_FLOOR),
                    int(np.ceil(cycle_duration * float(P.WEATHER_DROUGHT_MIN_DAY_FRAC_OF_CYCLE))),
                    int(np.ceil(interval * float(P.WEATHER_DROUGHT_INTERVAL_FACTOR))),
                )
                t["drought_days"] = int(min(dspell, int(P.WEATHER_DROUGHT_MIN_DAYS_CAP)))

            return t, "dynamic_percentile"
        except Exception as ex:
            logger.debug("Dynamic weather thresholds failed (%s); using static.", ex)
            return base, "fixed_error"

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
    ) -> Tuple[List[Dict], Dict, str]:
        """
        Detect extreme weather events anchored to ACTUAL CROP GROWING DAYS.

        Returns (events, thresholds_used, threshold_mode).
        """
        events: List[Dict] = []
        t, thr_mode = self._cycle_extreme_thresholds(df, cycle_duration)

        if thr_mode == "dynamic_percentile":
            drought_thresh = int(t["drought_days"])
        else:
            base_dt = t["drought_days"]
            if cycle_duration <= 90:
                drought_thresh = max(15, base_dt - 5)
            elif cycle_duration >= 150:
                drought_thresh = base_dt + 5
            else:
                drought_thresh = base_dt

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

        return events, self._plain_threshold_dict(t), thr_mode

    # â”€â”€ Legacy detection (BASIC mode / analyze_seasonal_weather) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _detect_extreme_events(
        self,
        df:     pd.DataFrame,
        season: str,
        year:   int,
        crop:   Optional[str] = None,
    ) -> Tuple[List[Dict], Dict, str]:
        """Season-window extreme detection; thresholds from dynamic percentiles when enabled."""
        events: List[Dict] = []
        n_days = len(df)
        t, thr_mode = self._cycle_extreme_thresholds(df, max(n_days, 1))
        critical_fracs = self._get_critical_stage_fracs(crop)

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
            dspell = int(t['drought_days'])
            spans = DataProcessor.find_consecutive_periods(dry, dspell)
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
                    'threshold_days': dspell,
                    'crop_stage_critical': critical,
                })

        return events, self._plain_threshold_dict(t), thr_mode

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
            med_ht = self._median_heatwave_threshold_from_stats(seasonal_weather)
            extreme_thresh   = med_ht + 3
            frac_extreme_hot = sum(tt > extreme_thresh for tt in max_temps) / len(max_temps)
            temp_risk        = min(100.0, frac_extreme_hot * 150)
        else:
            temp_risk = 30.0
        risk += temp_risk * 0.05

        return round(min(100.0, max(0.0, risk)), 1)

    @staticmethod
    def _per_cycle_weather_risk(stats: Dict) -> float:
        """
        Per-cycle 0–100 risk from that cycle's extreme_events (for credit blending).
        Calibrated mild when no events; scales with severity + critical-stage flags.
        """
        events = stats.get('extreme_events') or []
        if not events:
            return 8.0
        sev_w = {'medium': 1.0, 'high': 2.0, 'extreme': 3.5}
        wsum = 0.0
        for e in events:
            sev = str(e.get('severity', 'medium') or 'medium').lower()
            wsum += float(sev_w.get(sev, 1.0))
        crit = sum(1 for e in events if e.get('crop_stage_critical'))
        score = 18.0 + min(52.0, wsum * 4.0) + min(22.0, crit * 5.0)
        return round(min(100.0, score), 1)

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
            med_ht = self._median_heatwave_threshold_from_stats(seasonal_weather)
            extreme_thresh   = med_ht + 3
            frac_extreme_hot = sum(tt > extreme_thresh for tt in max_temps) / len(max_temps)
            temp_risk        = min(100.0, frac_extreme_hot * 150)
        else:
            temp_risk = 30.0
        risk += temp_risk * 0.05

        return round(min(100.0, max(0.0, risk)), 1)

    @staticmethod
    def _plain_threshold_dict(t: Dict) -> Dict:
        out: Dict = {}
        for k, v in t.items():
            if isinstance(v, (np.floating, float)):
                out[k] = float(v)
            elif isinstance(v, (np.integer, int)):
                out[k] = int(v)
            else:
                out[k] = v
        return out

    def _median_heatwave_threshold_from_stats(self, seasonal_weather: List[Dict]) -> float:
        hts: List[float] = []
        for s in seasonal_weather:
            w = s.get('weather_thresholds_used')
            if isinstance(w, dict) and 'heatwave_temp' in w:
                try:
                    hts.append(float(w['heatwave_temp']))
                except (TypeError, ValueError):
                    continue
        if hts:
            return float(np.median(hts))
        return float(self.thresholds['heatwave_temp'])

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

