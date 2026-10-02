"""Buffer-weather helpers: spread, irrigation, vapour-pressure deficit, PAR."""

from __future__ import annotations

from datetime import date, timedelta
from math import acos, cos, exp, pi, radians, sin, sqrt

from src.models import WeatherDay


def vpd_kpa(t_c: float, td_c: float) -> float:
    """Saturation deficit from air temperature and dewpoint, kPa."""
    def _es(t: float) -> float:
        return 0.6108 * exp(17.27 * t / (t + 237.3))
    return max(0.0, _es(t_c) - _es(td_c))


def extraterrestrial_mj(lat_deg: float, doy: int) -> float:
    """FAO-56 extraterrestrial radiation, MJ/m2/day."""
    phi = radians(lat_deg)
    dr = 1 + 0.033 * cos(2 * pi / 365.0 * doy)
    delta = 0.409 * sin(2 * pi / 365.0 * doy - 1.39)
    # Sunset hour angle. Guard polar day/night.
    ws_arg = max(-1.0, min(1.0, -tan_phi_delta(phi, delta)))
    ws = acos(ws_arg)
    ra = (24 * 60 / pi) * 0.0820 * dr * (
        ws * sin(phi) * sin(delta) + cos(phi) * cos(delta) * sin(ws)
    )
    return max(ra, 0.0)


def tan_phi_delta(phi: float, delta: float) -> float:
    c = cos(phi) * cos(delta)
    if abs(c) < 1e-6:
        return 0.0
    return sin(phi) * sin(delta) / c


def par_from_temperature(lat_deg: float, day: date, tmax: float, tmin: float) -> float:
    """PAR (MJ/m2) from the Hargreaves radiation estimate when ERA5 shortwave is missing."""
    ra = extraterrestrial_mj(lat_deg, day.timetuple().tm_yday)
    rs = 0.16 * sqrt(max(tmax - tmin, 0.0)) * ra
    return max(0.5, 0.48 * rs)


def finalize_day(row: WeatherDay, latitude: float | None) -> WeatherDay:
    if row.rain_p90 > row.rain_p50 + 8.0:
        row.uneven_rain = True
    if row.par <= 0 and latitude is not None:
        row.par = par_from_temperature(latitude, row.date, row.tmax, row.tmin)
    elif row.par > 40:
        # ERA5 shortwave arrives as J/m2. PAR is about half, in MJ/m2.
        row.par = 0.5 * row.par / 1e6
    return row


def apply_irrigation(
    weather: list[WeatherDay],
    ndmi_by_date: dict[date, float],
    neighborhood: dict[date, float],
) -> None:
    """Flag a wet-up that the rain grid did not see and the neighbouring cropland did not share."""
    days = sorted(ndmi_by_date)
    by_day = {w.date: w for w in weather}
    for day in days:
        prev_days = [d for d in days if timedelta(0) < day - d <= timedelta(days=6)]
        if not prev_days:
            continue
        prev = max(prev_days)
        jump = ndmi_by_date[day] - ndmi_by_date[prev]
        if jump < 0.08:
            continue
        rain = 0.0
        cursor = day - timedelta(days=4)
        while cursor <= day:
            wx = by_day.get(cursor)
            if wx is not None:
                rain += wx.rain_p90
            cursor += timedelta(days=1)
        if rain >= 5.0:
            continue
        neigh_now = neighborhood.get(day)
        neigh_prev = neighborhood.get(prev)
        if neigh_now is not None and neigh_prev is not None and (neigh_now - neigh_prev) >= 0.06:
            continue
        wx = by_day.get(day)
        if wx is not None:
            wx.irrigation = True
