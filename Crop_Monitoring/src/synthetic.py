"""A synthetic two-cohort soybean season for the demo and the tests.

No Earth Engine. The dates are chosen so the framework's rules are observable:
a June rain pulse, a July cloud gap with radar, an August second sowing, one
tree pixel, and an irrigation wet-up on a dry week.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.models import MonitorRequest, ObservationStack, PixelRecord, PixelTrack, WeatherDay


def _ndvi(day: date, emerge: date, peak: date) -> float:
    if day < emerge:
        return 0.15
    if day < peak:
        span = max((peak - emerge).days, 1)
        frac = (day - emerge).days / span
        return 0.32 + (0.78 - 0.32) * frac
    decline = (day - peak).days / 50.0
    return max(0.28, 0.78 - 0.35 * decline)


def _rvi(day: date, emerge: date) -> float:
    # Radar leads optical green-up by a few days.
    rise = emerge - timedelta(days=1)
    if day < rise:
        return 0.18
    if day < rise + timedelta(days=12):
        return 0.36
    return min(0.62, 0.40 + (day - rise).days / 80.0)


def soybean_mixed(year: int = 2024) -> tuple[MonitorRequest, ObservationStack]:
    emerge_a = date(year, 7, 5)
    peak_a = date(year, 9, 5)
    emerge_b = date(year, 8, 10)
    peak_b = date(year, 10, 1)
    start = date(year, 6, 1)
    end = date(year, 9, 20)
    # These are the 5-day optical dates that fall in the monsoon gap.
    gap = {
        date(year, 7, 6), date(year, 7, 11), date(year, 7, 16),
        date(year, 7, 21), date(year, 7, 26),
    }

    pixels: list[PixelTrack] = []
    # 16 + 12 crop pixels so both cohorts clear the 12-pixel and 15% rules.
    groups = [(f"a{i:02d}", emerge_a, peak_a, 74.50, 18.50) for i in range(16)]
    groups += [(f"b{i:02d}", emerge_b, peak_b, 74.51, 18.51) for i in range(12)]

    optical_days = []
    day = start
    while day <= end:
        if day not in gap and (day - start).days % 5 == 0:
            optical_days.append(day)
        day += timedelta(days=1)
    # Force the emergence dates themselves onto the optical calendar.
    for extra in (emerge_a, emerge_b, peak_a, date(year, 8, 2), date(year, 7, 30)):
        if extra not in optical_days and extra <= end:
            optical_days.append(extra)
    optical_days = sorted(set(optical_days))

    radar_days_asc = []
    radar_days_desc = []
    day = start
    while day <= end:
        if (day - start).days % 12 == 0:
            radar_days_asc.append(day)
        if (day - start).days % 12 == 6:
            radar_days_desc.append(day)
        day += timedelta(days=1)

    for idx, (pid, emerge, peak, lon, lat) in enumerate(groups):
        records: list[PixelRecord] = []
        for day in optical_days:
            ndvi = _ndvi(day, emerge, peak)
            # Four early-cohort pixels are the water-stressed patch at the peak.
            stressed = pid in {"a00", "a01", "a02", "a03"} and day == peak_a
            if stressed:
                ndvi = 0.40
            if day < date(year, 8, 2):
                ndmi = 0.05
            elif day == date(year, 8, 2):
                ndmi = 0.28
            else:
                ndmi = 0.30
            if stressed:
                ndmi = 0.02
            records.append(PixelRecord(
                date=day, sensor="s2", ndvi=ndvi, ndre=0.12 if stressed else ndvi * 0.46,
                cire=ndvi * 0.8, ndmi=ndmi, lswi=ndmi, mndwi=-0.05,
                bsi=0.05, psri=0.02, nbr=ndvi * 0.5,
            ))
        for day in radar_days_asc:
            records.append(PixelRecord(
                date=day, sensor="s1", rvi=_rvi(day, emerge), vv=-9.0, orbit="ASCENDING",
            ))
        for day in radar_days_desc:
            records.append(PixelRecord(
                date=day, sensor="s1", rvi=_rvi(day, emerge), vv=-10.0, orbit="DESCENDING",
            ))
        pixels.append(PixelTrack(pid, lon + idx * 0.0001, lat, records))

    # Bund tree: green all season, masked out before the split.
    tree_records = [
        PixelRecord(date=day, sensor="s2", ndvi=0.72, ndre=0.30, ndmi=0.25, bsi=0.0, psri=0.01)
        for day in optical_days
    ]
    pixels.append(PixelTrack("tree", 74.49, 18.49, tree_records))

    weather: list[WeatherDay] = []
    day = start
    while day <= end:
        rain = 0.0
        if date(year, 6, 18) <= day <= date(year, 6, 20):
            rain = 14.0
        weather.append(WeatherDay(
            date=day, rain_p10=rain * 0.8, rain_p50=rain, rain_p90=rain,
            tmean=28.0, tmax=33.0, tmin=24.0, vpd=1.2, par=9.0, et0=4.5,
        ))
        day += timedelta(days=1)

    neighborhood = {day: 0.06 for day in optical_days}
    request = MonitorRequest(
        crop="Soyabean",
        confidence=0.86,
        season="kharif",
        as_of=end,
        district_yield_t_ha=1.2,
        ecoregion="DECCAN",
    )
    stack = ObservationStack(pixels, weather, neighborhood, latitude=18.5)
    return request, stack
