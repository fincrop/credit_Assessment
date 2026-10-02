"""Split a boundary into sowing cohorts before any stress score."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from src.library import MIN_SOWING_GAP_DAYS, MIN_ZONE_PIXELS, MIN_ZONE_SHARE, CropPrior
from src.models import PixelTrack


@dataclass
class Zone:
    zone_id: str
    kind: str
    pixels: list[PixelTrack] = field(default_factory=list)
    greenup: date | None = None
    # boundary: the classified polygon was kept. sowing / harvest: it was split.
    split_reason: str = "boundary"

    @property
    def pixel_count(self) -> int:
        return len(self.pixels)


def _optical(track: PixelTrack):
    rows = [r for r in track.records if r.sensor in ("s2", "landsat") and r.ndvi is not None]
    rows.sort(key=lambda r: r.date)
    return rows


def _is_noncrop(track: PixelTrack) -> bool:
    """Year-round canopy (trees, bunds) or permanently bare ground."""
    ndvi = [r.ndvi for r in _optical(track) if r.ndvi is not None]
    if len(ndvi) < 3:
        return False
    if min(ndvi) > 0.45 and median(ndvi) > 0.60:
        return True
    if max(ndvi) < 0.18:
        return True
    return False


def _no_canopy(track: PixelTrack, emergence_ndvi: float) -> bool:
    """Never reaches this crop's emergence greenness, so it is not that crop."""
    ndvi = [r.ndvi for r in _optical(track) if r.ndvi is not None]
    if len(ndvi) < 2:
        return False
    return max(ndvi) < emergence_ndvi


def greenup_date(track: PixelTrack, emergence_ndvi: float) -> date | None:
    """First sustained green-up. Optical replaces radar when they agree."""
    optical = _optical_greenup(track, emergence_ndvi)
    radar = _radar_greenup(track)
    if optical and radar:
        if abs((optical - radar).days) <= 21:
            return optical
        return min(optical, radar)
    return optical or radar


def _optical_greenup(track: PixelTrack, threshold: float) -> date | None:
    clear = _optical(track)
    for i, row in enumerate(clear):
        if row.ndvi is None or row.ndvi < threshold:
            continue
        prev = clear[i - 1] if i else None
        nxt = clear[i + 1] if i + 1 < len(clear) else None
        rose = prev is not None and prev.ndvi is not None and prev.ndvi < threshold - 0.05
        if not rose and prev is not None:
            continue
        if nxt is None:
            return row.date
        held = (nxt.date - row.date).days <= 40 and nxt.ndvi is not None and nxt.ndvi >= threshold - 0.08
        if held or (nxt.date - row.date).days > 40:
            return row.date
    return None


def _radar_greenup(track: PixelTrack) -> date | None:
    by_orbit: dict[str, list] = {}
    for row in track.records:
        if row.sensor == "s1" and row.rvi is not None and row.orbit:
            by_orbit.setdefault(row.orbit, []).append(row)
    found: list[date] = []
    for rows in by_orbit.values():
        rows.sort(key=lambda r: r.date)
        if len(rows) < 3:
            continue
        baseline = median(r.rvi for r in rows[:2])
        for i in range(2, len(rows)):
            if rows[i].rvi is None or rows[i].rvi < baseline + 0.08:
                continue
            if i + 1 < len(rows) and rows[i + 1].rvi is not None and rows[i + 1].rvi >= baseline + 0.05:
                found.append(rows[i].date)
                break
    return min(found) if found else None


def _cluster(dated: list[tuple[PixelTrack, date]], original_n: int) -> list[list[tuple[PixelTrack, date]]]:
    if not dated:
        return []
    dated = sorted(dated, key=lambda item: item[1])
    best = None
    for i in range(1, len(dated)):
        gap = (dated[i][1] - dated[i - 1][1]).days
        if gap < MIN_SOWING_GAP_DAYS:
            continue
        left, right = i, len(dated) - i
        if left < MIN_ZONE_PIXELS or right < MIN_ZONE_PIXELS:
            continue
        if left / original_n < MIN_ZONE_SHARE or right / original_n < MIN_ZONE_SHARE:
            continue
        if best is None or gap > best[0]:
            best = (gap, i)
    if best is None:
        return [dated]
    cut = best[1]
    return _cluster(dated[:cut], original_n) + _cluster(dated[cut:], original_n)


def split_zones(pixels: list[PixelTrack], prior: CropPrior) -> list[Zone]:
    """Mask non-crop pixels, then split the rest on green-up date."""
    noncrop: list[PixelTrack] = []
    crop_pixels: list[PixelTrack] = []
    for track in pixels:
        if _is_noncrop(track) or _no_canopy(track, prior.emergence_ndvi):
            noncrop.append(track)
        else:
            crop_pixels.append(track)

    zones: list[Zone] = []
    if noncrop:
        zones.append(Zone("noncrop", "non_crop", noncrop, None))

    dated: list[tuple[PixelTrack, date]] = []
    undated: list[PixelTrack] = []
    for track in crop_pixels:
        when = greenup_date(track, prior.emergence_ndvi)
        if when is None:
            undated.append(track)
        else:
            dated.append((track, when))

    original_n = max(len(crop_pixels), 1)
    clusters = _cluster(dated, original_n)
    if not clusters and (dated or undated):
        clusters = [dated] if dated else [[]]

    # Pixels with no green-up join the largest cohort. They still belong to the field.
    if undated and clusters:
        clusters.sort(key=len, reverse=True)
        for track in undated:
            clusters[0].append((track, clusters[0][0][1] if clusters[0] else date.today()))
        if not clusters[0] and undated:
            clusters = [[(track, date.today()) for track in undated]]
    elif undated and not clusters:
        clusters = [[(track, date.today()) for track in undated]]

    crop_zones = []
    for bunch in clusters:
        if not bunch:
            continue
        dates = [item[1] for item in bunch]
        crop_zones.append(Zone(
            zone_id="",
            kind="crop",
            pixels=[item[0] for item in bunch],
            greenup=sorted(dates)[len(dates) // 2],
        ))
    crop_zones.sort(key=lambda z: (z.greenup or date.max, -z.pixel_count))
    reason = "sowing" if len(crop_zones) > 1 else "boundary"
    for i, zone in enumerate(crop_zones, start=1):
        zone.zone_id = f"z{i}"
        zone.split_reason = reason
        zones.append(zone)
    if not any(z.kind == "crop" for z in zones) and crop_pixels:
        zones.append(Zone("z1", "crop", crop_pixels, None))
    return zones


def crash_date(track: PixelTrack, after: date | None, residue: float) -> date | None:
    """First date the canopy falls to residue after its own peak. None while it is still up."""
    clear = _optical(track)
    if after is not None:
        clear = [row for row in clear if row.date >= after - timedelta(days=5)]
    if len(clear) < 3:
        return None
    peak_i = max(range(len(clear)), key=lambda i: clear[i].ndvi or 0.0)
    peak_val = clear[peak_i].ndvi or 0.0
    if peak_val < 0.35:
        return None
    for i in range(peak_i + 1, len(clear)):
        value = clear[i].ndvi
        if value is None or (clear[i].date - clear[peak_i].date).days < 10:
            continue
        if value <= residue or value <= peak_val * 0.45:
            return clear[i].date
    return None


def split_harvest(zone: Zone, prior: CropPrior) -> list[Zone]:
    """Split one sowing cohort when its pixels are harvested on different dates.

    A cohort that is still green stays one parcel. Two crash dates at least 21
    days apart, each large enough to map, become two parcels with the same
    sowing date and their own harvest.
    """
    if zone.kind != "crop":
        return [zone]
    dated: list[tuple[PixelTrack, date]] = []
    standing: list[PixelTrack] = []
    for track in zone.pixels:
        when = crash_date(track, zone.greenup, prior.residue_cover)
        if when is None:
            standing.append(track)
        else:
            dated.append((track, when))
    original_n = max(zone.pixel_count, 1)
    harvested = _cluster(dated, original_n) if dated else []
    parts: list[Zone] = []
    for bunch in harvested:
        if len(bunch) < MIN_ZONE_PIXELS or len(bunch) / original_n < MIN_ZONE_SHARE:
            continue
        parts.append(Zone(
            zone_id=zone.zone_id,
            kind="crop",
            pixels=[item[0] for item in bunch],
            greenup=zone.greenup,
            split_reason="harvest",
        ))
    standing_large = (
        len(standing) >= MIN_ZONE_PIXELS and len(standing) / original_n >= MIN_ZONE_SHARE
    )
    if standing_large and parts:
        parts.append(Zone(
            zone_id=zone.zone_id,
            kind="crop",
            pixels=standing,
            greenup=zone.greenup,
            split_reason="harvest",
        ))
    if len(parts) < 2:
        return [zone]
    used = {id(pixel) for part in parts for pixel in part.pixels}
    leftover = [track for track in zone.pixels if id(track) not in used]
    if leftover:
        parts.sort(key=lambda part: part.pixel_count, reverse=True)
        parts[0].pixels.extend(leftover)
    parts.sort(key=lambda part: part.pixel_count, reverse=True)
    return parts


def zone_index_series(zone: Zone) -> list[dict]:
    """Zone-mean indices on every satellite date. This is the series a cluster comparison reads."""
    names = ("ndvi", "ndre", "cire", "ndmi", "lswi", "mndwi", "psri", "bsi", "nbr", "rvi")
    buckets: dict[tuple[date, str], dict[str, list[float]]] = {}
    for track in zone.pixels:
        for record in track.records:
            slot = buckets.setdefault((record.date, record.sensor), {})
            for name in names:
                value = getattr(record, name, None)
                if value is not None:
                    slot.setdefault(name, []).append(float(value))
    rows = []
    for (day, sensor), cols in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1])):
        if not cols:
            continue
        row: dict = {"date": day.isoformat(), "sensor": sensor}
        count = 0
        for name, values in cols.items():
            row[name] = round(sum(values) / len(values), 4)
            count = max(count, len(values))
        row["pixels"] = count
        rows.append(row)
    return rows


def zone_ndmi_series(zone: Zone) -> dict[date, float]:
    buckets: dict[date, list[float]] = {}
    for track in zone.pixels:
        for row in track.records:
            if row.sensor in ("s2", "landsat") and row.ndmi is not None:
                buckets.setdefault(row.date, []).append(row.ndmi)
    return {day: sum(vals) / len(vals) for day, vals in buckets.items()}
