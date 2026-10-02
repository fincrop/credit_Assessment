"""Daily canopy, water, and biomass. Satellites correct the weather step."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.library import CropPrior, temperature_scalar
from src.models import WeatherDay
from src.sowing import SowingEstimate
from src.zones import Zone


@dataclass
class StateDay:
    date: date
    cover: float
    water: float
    biomass: float
    gain: float
    uncertainty: float
    kind: str
    ndvi: float | None = None
    ndre: float | None = None
    cire: float | None = None
    ndmi: float | None = None
    tau: float = 0.0
    stage: str = "pre-emergence"


def _weather_by_day(weather: list[WeatherDay]) -> dict[date, WeatherDay]:
    return {w.date: w for w in weather}


def _zone_obs(zone: Zone) -> dict[date, dict]:
    """Median optical/radar values per date. Optical wins over radar for kind."""
    buckets: dict[date, dict[str, list]] = {}
    sensors: dict[date, set[str]] = {}
    for track in zone.pixels:
        for row in track.records:
            slot = buckets.setdefault(row.date, {})
            sensors.setdefault(row.date, set()).add(row.sensor)
            for name in ("ndvi", "ndre", "cire", "ndmi", "lswi", "mndwi", "bsi", "psri", "nbr", "rvi"):
                value = getattr(row, name)
                if value is None:
                    continue
                if name == "rvi" and row.sensor != "s1":
                    continue
                if name != "rvi" and row.sensor == "s1":
                    continue
                slot.setdefault(name, []).append(value)
    out = {}
    for day, slot in buckets.items():
        med = {k: sum(v) / len(v) for k, v in slot.items() if v}
        kinds = sensors.get(day, set())
        if "s2" in kinds or "landsat" in kinds:
            med["kind"] = "optical"
        elif "s1" in kinds:
            med["kind"] = "radar"
        else:
            med["kind"] = "inferred"
        out[day] = med
    return out


def _cover_from_ndvi(ndvi: float) -> float:
    return max(0.0, min(1.0, (ndvi - 0.12) / 0.72))


def _cover_from_rvi(rvi: float) -> float:
    return max(0.0, min(1.0, (rvi - 0.12) / 0.55))


def _water_from_ndmi(ndmi: float) -> float:
    return max(0.0, min(1.0, (ndmi + 0.15) / 0.55))


def _coarse_on(coarse: dict[date, float] | None, day: date) -> float | None:
    """Nearest 250 m NDVI within 8 days. Absent when a 10 m view exists."""
    if not coarse:
        return None
    if day in coarse:
        return coarse[day]
    best: tuple[int, float] | None = None
    for seen, value in coarse.items():
        gap = abs((seen - day).days)
        if gap <= 8 and (best is None or gap < best[0]):
            best = (gap, value)
    return None if best is None else best[1]


def simulate(
    zone: Zone,
    weather: list[WeatherDay],
    sowing: SowingEstimate,
    prior: CropPrior,
    monitor_start: date,
    monitor_end: date,
    coarse_ndvi: dict[date, float] | None = None,
) -> list[StateDay]:
    """Advance one day at a time. Uncertainty rises until a sensor corrects it."""
    by_day = _weather_by_day(weather)
    obs = _zone_obs(zone)
    sow = sowing.date or monitor_start
    cover = 0.05
    water = 0.55
    biomass = 0.0
    uncertainty = 0.40
    rows: list[StateDay] = []
    day = monitor_start
    while day <= monitor_end:
        wx = by_day.get(day)
        tmean = wx.tmean if wx else prior.opt_temp
        par = wx.par if wx else 8.0
        et0 = wx.et0 if wx else 4.0
        rain = wx.rain_p50 if wx else 0.0
        growing = sowing.known and day >= sow
        t_scalar = temperature_scalar(tmean, prior) if growing else 0.0
        w_scalar = max(0.15, min(1.0, water))
        fapar = max(0.0, min(0.95, 1.15 * cover - 0.05))
        gain = prior.rue * par * fapar * t_scalar * w_scalar * 10.0 if growing else 0.0
        if growing:
            biomass += gain
            room = max(0.0, prior.cover_max - cover)
            cover = min(prior.cover_max, cover + 0.018 * room * t_scalar * w_scalar)
        water = water - 0.025 * (et0 / 5.0) + min(rain, 40.0) / 25.0
        if wx and wx.irrigation:
            water = max(water, 0.75)
        water = max(0.0, min(1.0, water))
        uncertainty = min(1.0, uncertainty + 0.035)

        seen = obs.get(day)
        kind = "inferred"
        ndvi = ndre = cire = ndmi = None
        if seen and seen.get("kind") == "optical" and "ndvi" in seen:
            kind = "optical"
            ndvi = seen.get("ndvi")
            ndre = seen.get("ndre")
            cire = seen.get("cire")
            ndmi = seen.get("ndmi")
            if ndvi is not None:
                cover = 0.35 * cover + 0.65 * _cover_from_ndvi(ndvi)
            if ndmi is not None:
                water = 0.40 * water + 0.60 * _water_from_ndmi(ndmi)
            uncertainty *= 0.35
        elif seen and seen.get("kind") == "radar" and "rvi" in seen:
            kind = "radar"
            cover = 0.55 * cover + 0.45 * _cover_from_rvi(seen["rvi"])
            uncertainty *= 0.62
        else:
            coarse = _coarse_on(coarse_ndvi, day)
            if coarse is not None:
                kind = "coarse"
                ndvi = coarse
                cover = 0.70 * cover + 0.30 * _cover_from_ndvi(coarse)
                uncertainty *= 0.80

        rows.append(StateDay(
            date=day, cover=cover, water=water, biomass=biomass, gain=gain,
            uncertainty=uncertainty, kind=kind, ndvi=ndvi, ndre=ndre, cire=cire, ndmi=ndmi,
        ))
        day += timedelta(days=1)
    return rows
