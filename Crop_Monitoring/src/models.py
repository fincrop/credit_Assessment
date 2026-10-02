"""Shared records for one monitoring run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass
class PixelRecord:
    date: date
    sensor: str
    ndvi: Optional[float] = None
    ndre: Optional[float] = None
    cire: Optional[float] = None
    ndmi: Optional[float] = None
    lswi: Optional[float] = None
    mndwi: Optional[float] = None
    bsi: Optional[float] = None
    psri: Optional[float] = None
    nbr: Optional[float] = None
    rvi: Optional[float] = None
    vv: Optional[float] = None
    orbit: Optional[str] = None
    lst_c: Optional[float] = None


@dataclass
class PixelTrack:
    pixel_id: str
    lon: float
    lat: float
    records: list[PixelRecord] = field(default_factory=list)


@dataclass
class WeatherDay:
    date: date
    rain_p10: float = 0.0
    rain_p50: float = 0.0
    rain_p90: float = 0.0
    tmean: float = 27.0
    tmax: float = 32.0
    tmin: float = 22.0
    vpd: float = 1.0
    par: float = 8.0
    et0: float = 4.0
    soil_moisture: Optional[float] = None
    skin_temp: Optional[float] = None
    soil_temp: Optional[float] = None
    lst_delta: Optional[float] = None
    uneven_rain: bool = False
    irrigation: bool = False


@dataclass
class ObservationStack:
    pixels: list[PixelTrack]
    weather: list[WeatherDay]
    neighborhood_ndmi: dict[date, float] = field(default_factory=dict)
    latitude: Optional[float] = None
    # 250 m MODIS NDVI. Fills a cloudy day. It does not redraw the boundary.
    coarse_ndvi: dict[date, float] = field(default_factory=dict)
    # Share of sample points Dynamic World calls cropland.
    cropland_fraction: Optional[float] = None


@dataclass
class MonitorRequest:
    crop: str
    geometry: Optional[dict] = None
    confidence: float = 1.0
    season: Optional[str] = None
    ecoregion: Optional[str] = None
    as_of: Optional[date] = None
    start: Optional[date] = None
    end: Optional[date] = None
    sowing_hint: Optional[date] = None
    sowing_hint_source: str = "provided"
    district_yield_t_ha: Optional[float] = None
    peer_integrals: Optional[list[float]] = None
    cluster_id: Optional[str] = None
    village: Optional[str] = None
    classification_job_id: Optional[str] = None
    source_field_id: Optional[str] = None


@dataclass
class Cue:
    family: str
    date: date
    sigma_days: float
    weight: float = 1.0
