"""Sowing date as a posterior over rain, radar, optical, and land surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import exp
from statistics import median

from src.library import CropPrior, ResolvedWindow
from src.models import Cue, WeatherDay
from src.zones import Zone


@dataclass
class SowingEstimate:
    known: bool
    date: date | None
    early: date | None
    late: date | None
    confidence: float
    sources: list[str]
    note: str


def _add(day: date, n: int) -> date:
    return day + timedelta(days=n)


def rain_trigger(weather: list[WeatherDay], start: date, end: date) -> date | None:
    """First meaningful wet spell inside the sowing window after a drier run."""
    days = sorted(
        (w for w in weather if start - timedelta(days=20) <= w.date <= end),
        key=lambda w: w.date,
    )
    for i, row in enumerate(days):
        if row.date < start or row.date > end:
            continue
        ahead = days[i:i + 3]
        if len(ahead) < 3:
            continue
        wet = sum(d.rain_p50 for d in ahead)
        prev = days[max(0, i - 7):i]
        dry = sum(d.rain_p50 for d in prev) if prev else 0.0
        if wet >= 25.0 and dry < 15.0:
            return row.date
    return None


def _series_median(zone: Zone, sensor: str, attr: str, orbit: str | None = None) -> dict[date, float]:
    buckets: dict[date, list[float]] = {}
    for track in zone.pixels:
        for row in track.records:
            if row.sensor != sensor:
                continue
            if orbit and row.orbit != orbit:
                continue
            value = getattr(row, attr)
            if value is None:
                continue
            buckets.setdefault(row.date, []).append(value)
    return {day: median(vals) for day, vals in buckets.items() if vals}


def _radar_emergence(zone: Zone) -> date | None:
    orbits = sorted({
        row.orbit
        for track in zone.pixels
        for row in track.records
        if row.sensor == "s1" and row.orbit and row.rvi is not None
    })
    found: list[date] = []
    for orbit in orbits:
        series = sorted(_series_median(zone, "s1", "rvi", orbit).items())
        if len(series) < 3:
            continue
        baseline = median(v for _, v in series[:2])
        for i in range(2, len(series)):
            day, value = series[i]
            if value < baseline + 0.08:
                continue
            if i + 1 < len(series) and series[i + 1][1] >= baseline + 0.05:
                found.append(day)
                break
    return min(found) if found else None


def _optical_emergence(zone: Zone, threshold: float) -> date | None:
    # Zone median NDVI, same confirmation rule as a pixel.
    series = sorted(_series_median(zone, "s2", "ndvi").items())
    landsat = sorted(_series_median(zone, "landsat", "ndvi").items())
    merged: dict[date, float] = {}
    for day, value in landsat + series:
        # Sentinel-2 wins when both sensors see the same day.
        if day not in merged or True:
            merged[day] = value
    for day, value in series:
        merged[day] = value
    clear = sorted(merged.items())
    for i, (day, value) in enumerate(clear):
        if value < threshold:
            continue
        prev = clear[i - 1] if i else None
        nxt = clear[i + 1] if i + 1 < len(clear) else None
        rose = prev is not None and prev[1] < threshold - 0.05
        if prev is not None and not rose:
            continue
        if nxt is None:
            return day
        held = (nxt[0] - day).days <= 40 and nxt[1] >= threshold - 0.08
        if held or (nxt[0] - day).days > 40:
            return day
    return None


def _lst_cooling(weather: list[WeatherDay], start: date, end: date) -> date | None:
    baseline_vals = [
        w.lst_delta for w in weather
        if w.lst_delta is not None and start - timedelta(days=25) <= w.date < start
    ]
    if len(baseline_vals) < 2:
        baseline_vals = [w.lst_delta for w in weather if w.lst_delta is not None and w.date < start]
    if not baseline_vals:
        return None
    baseline = median(baseline_vals)
    for row in sorted(weather, key=lambda w: w.date):
        if row.date < start or row.date > end or row.lst_delta is None:
            continue
        if row.lst_delta < baseline - 3.0:
            return row.date
    return None


def extract_cues(
    zone: Zone,
    weather: list[WeatherDay],
    prior: CropPrior,
    window: ResolvedWindow,
) -> list[Cue]:
    cues: list[Cue] = []
    search_end = window.sow_end + timedelta(days=prior.emergence_lag + 21)
    rain = rain_trigger(weather, window.sow_start, window.sow_end)
    if rain is not None:
        cues.append(Cue("rain", rain, 7.0, 1.0))

    radar = _radar_emergence(zone)
    if radar is not None:
        cues.append(Cue("radar", radar - timedelta(days=prior.emergence_lag), 8.0, 1.1))

    # Per-pixel green-up median is a radar/optical mix; the zone optical series
    # is the precise cue. Fall back to the pixel green-up when scene medians
    # are too thin.
    optical = _optical_emergence(zone, prior.emergence_ndvi)
    if optical is None and zone.greenup is not None:
        optical = zone.greenup
    if optical is not None and optical <= search_end:
        cues.append(Cue("optical", optical - timedelta(days=prior.emergence_lag), 5.0, 1.2))

    cooling = _lst_cooling(weather, window.sow_start, window.sow_end + timedelta(days=10))
    if cooling is not None:
        cues.append(Cue("land_surface", cooling, 8.0, 0.8))
    return cues


def _prior_weight(day: date, cal_start: date, cal_end: date, extra_start: date, extra_end: date) -> float:
    if cal_start <= day <= cal_end:
        return 1.0
    if extra_start <= day <= extra_end:
        return 0.55
    return 0.02


def fuse_sowing(
    cues: list[Cue],
    cal_start: date,
    cal_end: date,
    extra_start: date | None = None,
    extra_end: date | None = None,
    hint: Cue | None = None,
) -> SowingEstimate:
    """Calendar prior times each cue. A provided sowing date dominates."""
    if hint is not None and hint.family == "provided":
        return SowingEstimate(
            True, hint.date, _add(hint.date, -2), _add(hint.date, 2),
            0.95, ["provided"], "Sowing date taken from the farm record.",
        )

    extra_start = extra_start or cal_start
    extra_end = extra_end or cal_end
    lo = min(cal_start, extra_start) - timedelta(days=15)
    hi = max(cal_end, extra_end) + timedelta(days=15)
    use = list(cues)
    if hint is not None:
        use.append(hint)
    if not use:
        return SowingEstimate(
            False, None, cal_start, cal_end, 0.0, [],
            "No rain, radar, optical, or land-surface cue inside the season. Sowing was not invented.",
        )

    days = []
    cursor = lo
    while cursor <= hi:
        days.append(cursor)
        cursor += timedelta(days=1)

    weights = []
    for day in days:
        prior = _prior_weight(day, cal_start, cal_end, extra_start, extra_end)
        like = 1.0
        for cue in use:
            sig = max(cue.sigma_days, 1.0)
            z = (day - cue.date).days / sig
            like *= cue.weight * exp(-0.5 * z * z) + 1e-6
        weights.append(prior * like)

    total = sum(weights)
    if total <= 0:
        return SowingEstimate(False, None, cal_start, cal_end, 0.0, [], "Sowing posterior was empty.")

    def _quantile(q: float) -> date:
        acc = 0.0
        for day, w in zip(days, weights):
            acc += w / total
            if acc >= q:
                return day
        return days[-1]

    early, mid, late = _quantile(0.10), _quantile(0.50), _quantile(0.90)
    sources = []
    for cue in use:
        if abs((cue.date - mid).days) <= max(cue.sigma_days, 10):
            if cue.family not in sources:
                sources.append(cue.family)
    span = (late - early).days
    families = len(sources)
    confidence = 0.30 + 0.18 * max(families - 1, 0)
    if span <= 12:
        confidence += 0.22
    elif span <= 20:
        confidence += 0.10
    else:
        confidence -= 0.08
    if "provided" in sources or (hint and hint.family == "provided"):
        confidence = max(confidence, 0.9)
    confidence = max(0.15, min(0.93, confidence))
    note = (
        "Most likely sowing is the median of the multi-sensor posterior. "
        "Early and late are the 10th and 90th percentiles."
    )
    return SowingEstimate(True, mid, early, late, round(confidence, 3), sources, note)


def estimate_sowing(
    zone: Zone,
    weather: list[WeatherDay],
    prior: CropPrior,
    window: ResolvedWindow,
    hint: Cue | None = None,
) -> SowingEstimate:
    cues = extract_cues(zone, weather, prior, window)
    extra_start, extra_end = window.sow_start, window.sow_end
    anchor = zone.greenup or (cues[0].date if cues else None)
    if anchor is not None:
        implied = anchor - timedelta(days=prior.emergence_lag)
        extra_start = min(window.sow_start, implied - timedelta(days=20))
        extra_end = max(window.sow_end, implied + timedelta(days=10))
    # A pixel green-up that the zone median missed still counts.
    if zone.greenup is not None and not any(c.family == "optical" for c in cues):
        cues.append(Cue("optical", zone.greenup - timedelta(days=prior.emergence_lag), 6.0, 1.0))
    return fuse_sowing(cues, window.sow_start, window.sow_end, extra_start, extra_end, hint)
