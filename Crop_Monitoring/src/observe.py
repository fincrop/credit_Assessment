"""Earth Engine observations for one parcel.

Optical pixels, Sentinel-1 by orbit, Landsat, and buffer weather are reduced
to the same records the rest of the pipeline scores. A missing collection is
skipped. A missing credential is an error.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

S2_BANDS = ["NDVI", "NDMI", "NDRE", "LSWI", "MNDWI", "CIRE", "BSI", "PSRI", "NBR"]
S1_BANDS = ["RVI", "VV"]
LS_BANDS = ["NDVI", "NDMI", "BSI", "LST"]
_DATED_BAND = re.compile(r"d(\d{8})_([A-Za-z][A-Za-z0-9]*)")

from src.models import ObservationStack, PixelRecord, PixelTrack, WeatherDay
from src.weather import finalize_day, vpd_kpa

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CS_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_BAND = "cs_cdf"
CS_THRESHOLD = 0.60
S1_COLLECTION = "COPERNICUS/S1_GRD"
ERA5_COLLECTION = "ECMWF/ERA5_LAND/DAILY_AGGR"
CHIRPS_COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
IMERG_COLLECTION = "NASA/GPM_L3/IMERG_V07"
LST_COLLECTION = "MODIS/061/MOD11A1"
NDVI_COLLECTION = "MODIS/061/MOD13Q1"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
DW_COLLECTION = "GOOGLE/DYNAMICWORLD/V1"
L8_COLLECTION = "LANDSAT/LC08/C02/T1_L2"
L9_COLLECTION = "LANDSAT/LC09/C02/T1_L2"

ERA5_BANDS = [
    "temperature_2m",
    "temperature_2m_max",
    "temperature_2m_min",
    "dewpoint_temperature_2m",
    "total_precipitation_sum",
    "surface_solar_radiation_downwards_sum",
    "potential_evaporation_sum",
    "volumetric_soil_water_layer_1",
    "soil_temperature_level_1",
    "skin_temperature",
]


class MonitorFetchError(RuntimeError):
    pass


def fetch_stack(geometry: dict, start: date, end: date, crop: str) -> ObservationStack:
    """Download one season for a GeoJSON geometry."""
    try:
        import ee
    except ImportError as exc:
        raise MonitorFetchError("earthengine-api is not installed.") from exc
    from src._bootstrap import init_ee
    try:
        init_ee()
    except Exception as exc:
        raise MonitorFetchError(str(exc)) from exc

    geom = ee.Geometry(geometry)
    core = geom.buffer(-10)
    use = ee.Geometry(ee.Algorithms.If(core.area(1).gt(100), core, geom))
    points = _sample_points(geometry)
    if len(points) < 8:
        points = _sample_points(geometry, inset_m=0)
    if not points:
        raise MonitorFetchError("No sample points fell inside the field polygon.")

    start_s, end_s = start.isoformat(), (end + timedelta(days=1)).isoformat()
    ee_points = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point(xy), {"pixel_id": pid})
        for pid, xy in points
    ])
    labels = _dynamic_world_labels(ee, use, ee_points, start_s, end_s)
    crop_n, labeled_n = _cropland_counts(labels)
    keep = _keep_ids(labels, crop)
    points = [(pid, xy) for pid, xy in points if pid in keep]
    if len(points) < 8:
        points = [(pid, xy) for pid, xy in _sample_points(geometry, inset_m=0)]
    ee_points = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point(xy), {"pixel_id": pid})
        for pid, xy in points
    ])

    tracks: dict[str, PixelTrack] = {
        pid: PixelTrack(pid, xy[0], xy[1], []) for pid, xy in points
    }
    neighborhood: dict[date, float] = {}

    _ingest_s2(ee, use, ee_points, start_s, end_s, tracks, None)
    _ingest_s1(ee, use, ee_points, start_s, end_s, tracks, "ASCENDING")
    _ingest_s1(ee, use, ee_points, start_s, end_s, tracks, "DESCENDING")
    _ingest_landsat(ee, use, ee_points, start_s, end_s, tracks)

    if not _has_optical(tracks):
        raise MonitorFetchError(
            "Earth Engine did not return canopy observations for this field. "
            "It was not scored and was not removed from the crop."
        )
    try:
        _fill_neighborhood(ee, use, start_s, end_s, neighborhood)
    except Exception as exc:
        logger.warning("Neighborhood moisture skipped: %s", exc)
    lat = sum(xy[1] for _, xy in points) / len(points)
    lon = sum(xy[0] for _, xy in points) / len(points)
    coarse = _coarse_ndvi(ee, [(pid, xy[0], xy[1]) for pid, xy in points], start_s, end_s)
    weather = _weather(ee, geom, start, end, lat, longitude=lon)
    pixels = [tracks[pid] for pid, _ in points if tracks[pid].records]
    fraction = round(crop_n / labeled_n, 3) if labeled_n else None
    return ObservationStack(
        pixels, weather, neighborhood, latitude=lat,
        coarse_ndvi=coarse, cropland_fraction=fraction,
    )


def points_for_field(geometry: dict) -> list[tuple[str, tuple[float, float]]]:
    """A few sample points per farm. Small fields do not get a 7 by 7 grid."""
    from shapely.geometry import shape

    poly = shape(geometry)
    if poly.is_empty:
        return []
    minx, miny, maxx, maxy = poly.bounds
    lat = (miny + maxy) / 2.0
    metres = 111_320.0 * max(math.cos(math.radians(lat)), 0.2)
    ha = abs(poly.area) * metres * 110_540.0 / 10_000.0
    if ha <= 0.5:
        side, cap = 2, 4
    elif ha <= 3:
        side, cap = 3, 9
    else:
        side, cap = 4, 12
    return _sample_points(geometry, inset_m=0, side=side)[:cap]


def fetch_field_stacks(
    fields: list[dict],
    start: date,
    end: date,
    crop: str,
) -> dict[str, ObservationStack]:
    """One Earth Engine pass per point batch for every field of this crop.

    Weather is read once for the shared bounds. A village of a thousand farms
    does not repeat the satellite collection a thousand times.
    """
    try:
        import ee
    except ImportError as exc:
        raise MonitorFetchError("earthengine-api is not installed.") from exc
    from src._bootstrap import init_ee
    try:
        init_ee()
    except Exception as exc:
        raise MonitorFetchError(str(exc)) from exc

    packed: list[tuple[str, str, float, float]] = []
    for field in fields:
        fid = str(field["field_id"])
        for pid, xy in points_for_field(field["geometry"]):
            packed.append((fid, f"{fid}::{pid}", xy[0], xy[1]))
    if not packed:
        raise MonitorFetchError("No sample points fell inside the selected fields.")

    tracks = {
        pid: PixelTrack(pid, lon, lat, [])
        for _, pid, lon, lat in packed
    }
    neighborhood: dict[date, float] = {}
    start_s, end_s = start.isoformat(), (end + timedelta(days=1)).isoformat()
    crop_n = labeled_n = 0
    logger.info("Reducing sample points for %s farms, not clipping each boundary", len(fields))
    for batch in _chunks(packed, 500):
        ee_points = ee.FeatureCollection([
            ee.Feature(ee.Geometry.Point([lon, lat]), {"pixel_id": pid})
            for _, pid, lon, lat in batch
        ])
        bounds = _bounds_geometry([[lon, lat] for _, _, lon, lat in batch], pad=0.02)
        use = ee.Geometry(bounds)
        labels = _dynamic_world_labels(ee, use, ee_points, start_s, end_s)
        got_crop, got_labeled = _cropland_counts(labels)
        crop_n += got_crop
        labeled_n += got_labeled
        keep = _keep_ids(labels, crop)
        kept = [row for row in batch if row[1] in keep] or batch
        if len(kept) != len(batch):
            ee_points = ee.FeatureCollection([
                ee.Feature(ee.Geometry.Point([lon, lat]), {"pixel_id": pid})
                for _, pid, lon, lat in kept
            ])
        _ingest_s2(ee, use, ee_points, start_s, end_s, tracks, neighborhood)
        _ingest_s1(ee, use, ee_points, start_s, end_s, tracks, "ASCENDING")
        _ingest_s1(ee, use, ee_points, start_s, end_s, tracks, "DESCENDING")
        _ingest_landsat(ee, use, ee_points, start_s, end_s, tracks)

    if not _has_optical(tracks):
        raise MonitorFetchError(
            "Earth Engine did not return canopy values for these fields. "
            "The satellite scenes are still there; the read was refused or came back empty. "
            "The farms were not removed from the crop. Run the same selection again."
        )

    bounds = _bounds_geometry(
        [[lon, lat] for _, _, lon, lat in packed],
        pad=0.0,
    )
    lats = [lat for _, _, _, lat in packed]
    lons = [lon for _, _, lon, _ in packed]
    coarse = _coarse_ndvi(ee, [(pid, lon, lat) for _, pid, lon, lat in packed], start_s, end_s)
    fraction = round(crop_n / labeled_n, 3) if labeled_n else None
    try:
        _fill_neighborhood(ee, ee.Geometry(bounds), start_s, end_s, neighborhood)
    except Exception as exc:
        logger.warning("Neighborhood moisture skipped: %s", exc)
    # CHIRPS and ERA5 cover the village. The half-hourly rain product is skipped
    # here so a large farm set does not add another heavy compute.
    weather = _weather(
        ee, ee.Geometry(bounds), start, end, sum(lats) / len(lats),
        storms=False, longitude=sum(lons) / len(lons),
    )

    stacks: dict[str, ObservationStack] = {}
    for field in fields:
        fid = str(field["field_id"])
        pixels = [
            track for pid, track in tracks.items()
            if pid.startswith(f"{fid}::") and track.records
        ]
        stacks[fid] = ObservationStack(
            pixels, weather, dict(neighborhood), latitude=sum(lats) / len(lats),
            coarse_ndvi=dict(coarse), cropland_fraction=fraction,
        )
    return stacks


def _chunks(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _bounds_geometry(coords: list[list[float]], pad: float) -> dict:
    lons = [xy[0] for xy in coords]
    lats = [xy[1] for xy in coords]
    minx, maxx = min(lons) - pad, max(lons) + pad
    miny, maxy = min(lats) - pad, max(lats) + pad
    return {
        "type": "Polygon",
        "coordinates": [[
            [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
        ]],
    }


def _sample_points(geometry: dict, inset_m: float = 10.0, side: int = 7) -> list[tuple[str, tuple[float, float]]]:
    from shapely.geometry import Point, shape

    poly = shape(geometry)
    inset = poly.buffer(-inset_m / 111_320.0) if inset_m else poly
    if inset.is_empty:
        inset = poly
    minx, miny, maxx, maxy = inset.bounds
    if maxx <= minx or maxy <= miny:
        return []
    found = []
    for i in range(side):
        for j in range(side):
            x = minx + (i + 0.5) / side * (maxx - minx)
            y = miny + (j + 0.5) / side * (maxy - miny)
            if inset.covers(Point(x, y)):
                found.append((f"p{len(found):02d}", (x, y)))
    return found[:48]


def _keep_ids(labels: dict[str, float], crop: str) -> set[str]:
    # Dynamic World: 0 water, 1 trees, 2 grass, 3 flooded, 4 crops, 5 shrub, 6 built.
    drop = {1, 6} if crop == "Rice" else {0, 1, 6}
    keep = set()
    for pid, label in labels.items():
        if label is None or int(round(label)) not in drop:
            keep.add(pid)
    return keep or set(labels)


def _dynamic_world_labels(ee, geom, points, start: str, end: str) -> dict[str, float]:
    try:
        mode = (ee.ImageCollection(DW_COLLECTION)
                .filterBounds(geom)
                .filterDate(start, end)
                .select("label")
                .mode())
        info = _getinfo(mode.reduceRegions(points, ee.Reducer.mode(), 10))
    except Exception:
        return {}
    out = {}
    for feat in info.get("features", []):
        props = feat.get("properties") or {}
        pid = props.get("pixel_id")
        if pid is not None:
            # Reducer.mode() keeps the band name (`label`). `mode` is a fallback.
            out[str(pid)] = props.get("label", props.get("mode"))
    return out


def _s2_image(ee, img):
    qa = img.select("QA60")
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    scl = img.select("SCL")
    mask = mask.And(scl.neq(3)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    mask = mask.And(img.select(CS_BAND).gte(CS_THRESHOLD))
    scaled = img.updateMask(mask).divide(10000)
    nir = scaled.select("B8")
    red = scaled.select("B4")
    blue = scaled.select("B2")
    green = scaled.select("B3")
    swir = scaled.select("B11")
    re1 = scaled.select("B5")
    re2 = scaled.select("B6")
    re3 = scaled.select("B7")
    b12 = scaled.select("B12")
    ndvi = scaled.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ndmi = scaled.normalizedDifference(["B8", "B11"]).rename("NDMI")
    ndre = scaled.normalizedDifference(["B8", "B5"]).rename("NDRE")
    lswi = scaled.normalizedDifference(["B8", "B11"]).rename("LSWI")
    mndwi = scaled.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    cire = re3.divide(re1.add(1e-6)).subtract(1).rename("CIRE")
    bsi = scaled.expression(
        "((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE) + 1e-6)",
        {"SWIR": swir, "RED": red, "NIR": nir, "BLUE": blue},
    ).rename("BSI")
    psri = scaled.expression(
        "(RED - BLUE) / (RE2 + 1e-6)",
        {"RED": red, "BLUE": blue, "RE2": re2},
    ).rename("PSRI")
    nbr = scaled.normalizedDifference(["B8", "B12"]).rename("NBR")
    return ee.Image.cat([ndvi, ndmi, ndre, lswi, mndwi, cire, bsi, psri, nbr]).float()


def _s2_collection(ee, geom, start, end):
    # A mostly cloudy scene is kept. The cloud mask still drops cloudy pixels,
    # so a clear patch inside it can enter the 10-day composite.
    return (ee.ImageCollection(S2_COLLECTION)
            .filterBounds(geom)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 95))
            .linkCollection(ee.ImageCollection(CS_COLLECTION), [CS_BAND]))


def _ingest_s2(ee, geom, points, start, end, tracks, neighborhood) -> None:
    try:
        logger.info("Sentinel-2 10-day composite")
        col = _s2_collection(ee, geom, start, end)
        info = _composite_sample(
            ee, col, lambda img: _s2_image(ee, img), start, end, points, 10, S2_BANDS,
        )
    except Exception as exc:
        logger.warning("Sentinel-2 sample failed: %s", exc)
        return
    _fill_optical(info, tracks, "s2", neighborhood)


def _ingest_s1(ee, geom, points, start, end, tracks, orbit: str) -> None:
    try:
        def _rvi(img):
            lin = ee.Image(10).pow(img.select(["VV", "VH"]).divide(10))
            vv = lin.select("VV")
            vh = lin.select("VH")
            rvi = vh.multiply(4).divide(vv.add(vh).add(1e-6)).rename("RVI")
            return rvi.addBands(img.select("VV")).set("system:time_start", img.get("system:time_start"))

        logger.info("Sentinel-1 %s 10-day composite", orbit)
        col = (ee.ImageCollection(S1_COLLECTION)
               .filterBounds(geom)
               .filterDate(start, end)
               .filter(ee.Filter.eq("instrumentMode", "IW"))
               .filter(ee.Filter.eq("orbitProperties_pass", orbit))
               .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
               .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
               .select(["VV", "VH"]))
        info = _composite_sample(ee, col, _rvi, start, end, points, 10, S1_BANDS)
    except Exception as exc:
        logger.warning("Sentinel-1 %s sample failed: %s", orbit, exc)
        return
    for feat in info.get("features", []):
        props = feat.get("properties") or {}
        pid = str(props.get("pixel_id") or "")
        day = _parse_day(props.get("date"))
        if pid not in tracks or day is None or props.get("RVI") is None:
            continue
        tracks[pid].records.append(PixelRecord(
            date=day, sensor="s1", rvi=_f(props.get("RVI")), vv=_f(props.get("VV")), orbit=orbit,
        ))


def _ingest_landsat(ee, geom, points, start, end, tracks) -> None:
    try:
        def _prep(img):
            qa = img.select("QA_PIXEL")
            clear = qa.bitwiseAnd(1 << 3).eq(0).And(qa.bitwiseAnd(1 << 4).eq(0))
            optical = (img.select(["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6"])
                       .multiply(0.0000275).add(-0.2)
                       .updateMask(clear))
            ndvi = optical.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
            ndmi = optical.normalizedDifference(["SR_B5", "SR_B6"]).rename("NDMI")
            bsi = optical.expression(
                "((SWIR + RED) - (NIR + BLUE)) / ((SWIR + RED) + (NIR + BLUE) + 1e-6)",
                {"SWIR": optical.select("SR_B6"), "RED": optical.select("SR_B4"),
                 "NIR": optical.select("SR_B5"), "BLUE": optical.select("SR_B2")},
            ).rename("BSI")
            lst = (img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
                   .rename("LST").updateMask(clear))
            return (ee.Image.cat([ndvi, ndmi, bsi, lst])
                    .set("system:time_start", img.get("system:time_start")))

        logger.info("Landsat 10-day composite")
        col = (ee.ImageCollection(L8_COLLECTION).merge(ee.ImageCollection(L9_COLLECTION))
               .filterBounds(geom)
               .filterDate(start, end))
        info = _composite_sample(ee, col, _prep, start, end, points, 30, LS_BANDS)
    except Exception as exc:
        logger.warning("Landsat sample failed: %s", exc)
        return
    _fill_optical(info, tracks, "landsat", None)


def _has_optical(tracks: dict) -> bool:
    for track in tracks.values():
        for row in track.records:
            if row.sensor in ("s2", "landsat") and row.ndvi is not None:
                return True
    return False


def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def period_length(start: date, end: date) -> int:
    """About sixteen steps across the season, and never finer than 10 days."""
    span = max((end - start).days, 1)
    return max(10, math.ceil(span / 16))


def _dated_band(key: str) -> Optional[tuple[str, str]]:
    match = _DATED_BAND.search(str(key))
    if not match:
        return None
    return match.group(1), match.group(2)


def unstack_features(info: dict) -> dict:
    """Turn one row per point, with a band per date, back into one row per date."""
    features = []
    for feat in (info or {}).get("features") or []:
        props = feat.get("properties") or {}
        pid = props.get("pixel_id")
        by_day: dict[str, dict] = {}
        for key, value in props.items():
            parsed = _dated_band(str(key))
            if parsed is None or value is None:
                continue
            day, band = parsed
            by_day.setdefault(day, {})[band] = value
        for day, bands in by_day.items():
            row = {"pixel_id": pid, "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}"}
            row.update(bands)
            features.append({"type": "Feature", "properties": row})
    return {"features": features}


def _periods(ee, source, prepare, start, end, band_names: list[str]):
    start_d = _as_date(start)
    end_d = _as_date(end)
    step = period_length(start_d, end_d)
    count = max(math.ceil(max((end_d - start_d).days, 1) / step), 1)
    origin = ee.Date(start_d.isoformat())

    def one(index):
        index = ee.Number(index)
        opened = origin.advance(index.multiply(step), "day")
        closed = opened.advance(step, "day")
        window = source.filterDate(opened, closed)
        mid = opened.advance(step / 2.0, "day")
        stamp = ee.String("d").cat(mid.format("YYYYMMdd"))
        empty = (ee.Image.constant([0] * len(band_names))
                 .rename(band_names)
                 .updateMask(ee.Image.constant(0)))
        filled = window.map(prepare).median()
        image = ee.Image(ee.Algorithms.If(window.size().gt(0), filled, empty))
        names = image.bandNames().map(lambda name: stamp.cat("_").cat(ee.String(name)))
        return image.rename(names).set({
            "system:time_start": mid.millis(),
            "n": window.size(),
        })

    return ee.ImageCollection(ee.List.sequence(0, count - 1).map(one)).filter(ee.Filter.gt("n", 0))


def _composite_sample(ee, source, prepare, start, end, points, scale: int, band_names: list[str]) -> dict:
    """One reduce for every point. Dates are 10-day medians, not one request per scene."""
    periods = _periods(ee, source, prepare, start, end, band_names)
    stacked = periods.toBands()
    reduced = stacked.reduceRegions(collection=points, reducer=ee.Reducer.mean(), scale=scale)
    info = _getinfo(reduced) or {"features": []}
    return unstack_features(info)


def _fill_neighborhood(ee, geom, start, end, neighborhood: dict) -> None:
    """One moisture series for the buffer around the farms, not one per satellite scene."""
    ring = geom.buffer(2000).difference(geom.buffer(500), 1)
    periods = _periods(
        ee, _s2_collection(ee, geom, start, end),
        lambda img: _s2_image(ee, img), start, end, S2_BANDS,
    )
    stacked = periods.toBands()
    names = stacked.bandNames().filter(ee.Filter.stringContains("item", "_NDMI"))
    stats = stacked.select(names).reduceRegion(
        reducer=ee.Reducer.mean(), geometry=ring, scale=20, bestEffort=True, maxPixels=1e8,
    )
    raw = _getinfo(stats) or {}
    for key, value in raw.items():
        parsed = _dated_band(str(key))
        number = _f(value)
        if parsed is None or number is None:
            continue
        day = _parse_day(f"{parsed[0][:4]}-{parsed[0][4:6]}-{parsed[0][6:8]}")
        if day is not None:
            neighborhood[day] = number


def _fill_optical(info: dict, tracks: dict, sensor: str, neighborhood: Optional[dict]) -> None:
    for feat in info.get("features", []):
        props = feat.get("properties") or {}
        pid = str(props.get("pixel_id") or "")
        day = _parse_day(props.get("date"))
        if pid not in tracks or day is None or props.get("NDVI") is None:
            continue
        tracks[pid].records.append(PixelRecord(
            date=day,
            sensor=sensor,
            ndvi=_f(props.get("NDVI")),
            ndre=_f(props.get("NDRE")),
            cire=_f(props.get("CIRE")),
            ndmi=_f(props.get("NDMI")),
            lswi=_f(props.get("LSWI")),
            mndwi=_f(props.get("MNDWI")),
            bsi=_f(props.get("BSI")),
            psri=_f(props.get("PSRI")),
            nbr=_f(props.get("NBR")),
            lst_c=_f(props.get("LST")),
        ))
        if neighborhood is not None and props.get("neigh") is not None:
            neighborhood[day] = float(props["neigh"])


def _cropland_counts(labels: dict) -> tuple[int, int]:
    """Dynamic World class 4 is crops. The count is the cropland prior."""
    labeled = crops = 0
    for value in labels.values():
        if value is None:
            continue
        try:
            label = int(round(float(value)))
        except (TypeError, ValueError):
            continue
        labeled += 1
        if label == 4:
            crops += 1
    return crops, labeled


def _coarse_ndvi(ee, points: list[tuple], start: str, end: str) -> dict[date, float]:
    """One 250 m NDVI reduction for the sample points. Not one clip per farm."""
    if not points:
        return {}
    buckets: dict[date, list[float]] = {}
    try:
        for batch in _chunks(points, 1500):
            ee_points = ee.FeatureCollection([
                ee.Feature(ee.Geometry.Point([lon, lat]), {"pixel_id": pid})
                for pid, lon, lat in batch
            ])
            col = (ee.ImageCollection(NDVI_COLLECTION)
                   .filterDate(start, end)
                   .select(["NDVI"])
                   .map(lambda img: img.multiply(0.0001).rename("NDVI")
                        .copyProperties(img, ["system:time_start"])))
            info = _composite_sample(
                ee, col, lambda img: ee.Image(img).select(["NDVI"]),
                start, end, ee_points, 250, ["NDVI"],
            )
            for feat in info.get("features") or []:
                props = feat.get("properties") or {}
                day = _parse_day(props.get("date"))
                value = _f(props.get("NDVI"))
                if day is None or value is None:
                    continue
                buckets.setdefault(day, []).append(value)
    except Exception as exc:
        logger.warning("MODIS NDVI gap-fill skipped: %s", exc)
        return {}
    return {day: sum(vals) / len(vals) for day, vals in buckets.items() if vals}


def _merge_power(
    days: dict[date, WeatherDay],
    frame: dict[date, dict],
    tempered: set[date],
    gauged: set[date],
) -> None:
    """Fill days Earth Engine left empty. Existing CHIRPS and ERA5 values stay."""
    for day, row in frame.items():
        slot = days.get(day)
        if slot is None:
            slot = WeatherDay(date=day)
            days[day] = slot
        if day not in tempered and row.get("tmean") is not None:
            slot.tmean = float(row["tmean"])
            if row.get("tmax") is not None:
                slot.tmax = float(row["tmax"])
            if row.get("tmin") is not None:
                slot.tmin = float(row["tmin"])
        if day not in gauged and row.get("rain") is not None:
            rain = float(row["rain"])
            slot.rain_p10 = rain
            slot.rain_p50 = rain
            slot.rain_p90 = rain


def _power_frame(lat: float, lon: float, start: date, end: date) -> dict[date, dict]:
    import json
    from urllib.parse import urlencode
    from urllib.request import urlopen

    end = min(end, date.today() - timedelta(days=3))
    if end <= start:
        return {}
    query = urlencode({
        "parameters": "T2M,T2M_MAX,T2M_MIN,PRECTOTCORR",
        "community": "AG",
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    })
    try:
        with urlopen(POWER_URL + "?" + query, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning("NASA POWER skipped: %s", exc)
        return {}
    params = (payload.get("properties") or {}).get("parameter") or {}
    frame: dict[date, dict] = {}
    for key, attr in (
        ("T2M", "tmean"),
        ("T2M_MAX", "tmax"),
        ("T2M_MIN", "tmin"),
        ("PRECTOTCORR", "rain"),
    ):
        for stamp, value in (params.get(key) or {}).items():
            day = _parse_day(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}")
            number = _f(value)
            if day is None or number is None or number <= -900:
                continue
            frame.setdefault(day, {})[attr] = number
    return frame


def _weather(
    ee, geom, start: date, end: date, latitude: float,
    storms: bool = True, longitude: float | None = None,
) -> list[WeatherDay]:
    start_s, end_s = start.isoformat(), (end + timedelta(days=1)).isoformat()
    rain_buf = geom.buffer(10_000)
    era_buf = geom.buffer(15_000)
    chirps = _daily_reduce(
        ee,
        ee.ImageCollection(CHIRPS_COLLECTION).filterDate(start_s, end_s).select("precipitation"),
        rain_buf,
        5000,
        ee.Reducer.percentile([10, 50, 90]),
    )
    era = _daily_reduce(
        ee,
        ee.ImageCollection(ERA5_COLLECTION).filterDate(start_s, end_s).select(ERA5_BANDS),
        era_buf,
        11000,
        ee.Reducer.mean(),
    )
    imerg = _imerg_daily(ee, rain_buf, start_s, end_s) if storms else []
    lst = _modis_lst(ee, era_buf, start_s, end_s)

    days: dict[date, WeatherDay] = {}
    gauged: set[date] = set()
    tempered: set[date] = set()
    for row in chirps:
        day = _parse_day(row.get("date"))
        if day is None:
            continue
        days[day] = WeatherDay(
            date=day,
            rain_p10=_f(row.get("precipitation_p10")) or 0.0,
            rain_p50=_f(row.get("precipitation_p50")) or 0.0,
            rain_p90=_f(row.get("precipitation_p90")) or 0.0,
        )
        gauged.add(day)
    for row in imerg:
        day = _parse_day(row.get("date"))
        if day is None:
            continue
        slot = days.setdefault(day, WeatherDay(date=day))
        # IMERG is the storm-spread check. Keep CHIRPS as the seasonal amount
        # when it exists; fill from IMERG when the gauge product is empty.
        p10 = _f(row.get("rain_p10"))
        p50 = _f(row.get("rain_p50"))
        p90 = _f(row.get("rain_p90"))
        if slot.rain_p50 == 0 and p50:
            slot.rain_p10, slot.rain_p50, slot.rain_p90 = p10 or 0, p50, p90 or p50
        elif p90 is not None:
            slot.rain_p90 = max(slot.rain_p90, p90)
            if p10 is not None:
                slot.rain_p10 = min(slot.rain_p10, p10) if slot.rain_p10 else p10

    for row in era:
        day = _parse_day(row.get("date"))
        if day is None:
            continue
        slot = days.setdefault(day, WeatherDay(date=day))
        t = _kelvin(row.get("temperature_2m"))
        tmax = _kelvin(row.get("temperature_2m_max"))
        tmin = _kelvin(row.get("temperature_2m_min"))
        td = _kelvin(row.get("dewpoint_temperature_2m"))
        if t is not None:
            slot.tmean = t
            tempered.add(day)
        if tmax is not None:
            slot.tmax = tmax
        if tmin is not None:
            slot.tmin = tmin
        if t is not None and td is not None:
            slot.vpd = vpd_kpa(t, td)
        rs = _f(row.get("surface_solar_radiation_downwards_sum"))
        if rs is not None:
            slot.par = 0.48 * rs / 1e6
        et = _f(row.get("potential_evaporation_sum"))
        if et is not None:
            slot.et0 = abs(et) * 1000.0
        slot.soil_moisture = _f(row.get("volumetric_soil_water_layer_1"))
        slot.soil_temp = _kelvin(row.get("soil_temperature_level_1"))
        slot.skin_temp = _kelvin(row.get("skin_temperature"))

    for row in lst:
        day = _parse_day(row.get("date"))
        if day is None or day not in days:
            continue
        lst_c = _f(row.get("LST_Day_1km"))
        if lst_c is None:
            continue
        # MODIS scale 0.02 K.
        if lst_c > 150:
            lst_c = lst_c * 0.02 - 273.15
        days[day].lst_delta = lst_c - days[day].tmax

    if longitude is not None:
        _merge_power(days, _power_frame(latitude, longitude, start, end), tempered, gauged)

    out = [finalize_day(days[k], latitude) for k in sorted(days)]
    return out


def _daily_reduce(ee, collection, region, scale: int, reducer) -> list[dict]:
    try:
        def _annotate(img):
            stats = img.reduceRegion(
                reducer=reducer, geometry=region, scale=scale,
                bestEffort=True, maxPixels=1e8,
            )
            return img.set(stats).set("date", img.date().format("YYYY-MM-dd"))

        annotated = collection.map(_annotate)
        # Property names are unknown until the first image exists. Pull a
        # dictionary of every property via aggregate_array of the known ones
        # plus date, then zip client-side.
        probe = annotated.limit(1).first()
        names = _getinfo(probe.propertyNames()) or []
        names = [n for n in names if n not in ("system:index", "system:time_start", "system:footprint")]
        if "date" not in names:
            names.append("date")
        payload = {n: annotated.aggregate_array(n) for n in names}
        raw = _getinfo(ee.Dictionary(payload)) or {}
    except Exception:
        return []
    dates = raw.get("date") or []
    rows = []
    for i, day in enumerate(dates):
        row = {"date": day}
        for key, values in raw.items():
            if key == "date" or not isinstance(values, list) or i >= len(values):
                continue
            row[key] = values[i]
        rows.append(row)
    return rows


def _imerg_daily(ee, region, start: str, end: str) -> list[dict]:
    """Half-hourly IMERG summed to a daily spread. Failure leaves CHIRPS in charge."""
    try:
        col = ee.ImageCollection(IMERG_COLLECTION).filterDate(start, end).select("precipitation")
        n = int(_getinfo(ee.Date(end).difference(ee.Date(start), "day")) or 0)
        if n <= 0 or n > 400:
            return []

        def one(offset):
            day = ee.Date(start).advance(offset, "day")
            # mm/hr at 30-minute steps.
            total = col.filterDate(day, day.advance(1, "day")).map(lambda i: i.multiply(0.5)).sum()
            stats = total.rename("rain").reduceRegion(
                reducer=ee.Reducer.percentile([10, 50, 90]),
                geometry=region, scale=10000, bestEffort=True, maxPixels=1e8,
            )
            return ee.Feature(None, stats).set("date", day.format("YYYY-MM-dd"))

        fc = ee.FeatureCollection(ee.List.sequence(0, n - 1).map(one))
        info = _getinfo(fc) or {}
    except Exception:
        return []
    rows = []
    for feat in info.get("features", []):
        props = feat.get("properties") or {}
        rows.append({
            "date": props.get("date"),
            "rain_p10": props.get("rain_p10"),
            "rain_p50": props.get("rain_p50"),
            "rain_p90": props.get("rain_p90"),
        })
    return rows


def _modis_lst(ee, region, start: str, end: str) -> list[dict]:
    try:
        col = (ee.ImageCollection(LST_COLLECTION)
               .filterDate(start, end)
               .select("LST_Day_1km")
               .map(lambda img: img.multiply(0.02).subtract(273.15).copyProperties(img, ["system:time_start"])))
        return _daily_reduce(ee, col, region, 1000, ee.Reducer.mean())
    except Exception:
        return []


def _getinfo(obj) -> Any:
    """One quiet retry. The Earth Engine client has already backed off on HTTP 429."""
    last = None
    for attempt in range(2):
        try:
            return obj.getInfo()
        except Exception as exc:  # noqa: BLE001
            last = exc
            message = str(exc).lower()
            transient = any(token in message for token in (
                "concurrent", "rate", "quota", "timed out", "timeout", "deadline",
                "internal", "backend", "503", "429", "temporar", "unavailable",
            ))
            if not transient or attempt == 1:
                break
            time.sleep(20)
    raise RuntimeError(str(last)[:300])


def _parse_day(value) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _kelvin(value) -> Optional[float]:
    number = _f(value)
    if number is None:
        return None
    if number > 150:
        return number - 273.15
    return number
