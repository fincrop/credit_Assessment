"""Earth Engine pulls for one village tile: per-scene pixel arrays.

Every acquisition date becomes one (H, W) array per band on the village's 10 m
UTM grid, fetched with `ee.data.computePixels`. Cached per sensor per date, and
the date list is cached with them, so a rerun of the same window reads disk and
does not call Earth Engine. A later as-of date downloads only the new scenes.
Village weather is saved in the same folder.

    Sentinel-2 L2A    B2 B3 B4 B5 B6 B7 B8 B11 B12, Cloud Score+ cs_cdf >= 0.6,
                      SCL shadow/cloud/cirrus/snow/saturated removed.
                      20 m bands resampled bilinearly to the 10 m grid.
    Landsat 8/9 L2    renamed to their Sentinel-2 equivalents (B2 B3 B4 B8 B11
                      B12), QA_PIXEL cloud/shadow/cirrus/snow removed; 30 m
                      resampled bilinearly. Cross-calibrated in indices.py.
    Sentinel-1 GRD    VV, VH in linear power, per acquisition, tagged with pass
                      and relative orbit (incidence geometry differs per orbit).
    Dynamic World     season mode label, to drop trees/built/water pixels.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from src.raster.grid import Grid
from src.raster.stack import SceneStack

logger = logging.getLogger(__name__)

S2 = "COPERNICUS/S2_SR_HARMONIZED"
CS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_MIN = 0.60
L8, L9 = "LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2"
S1 = "COPERNICUS/S1_GRD"
DW = "GOOGLE/DYNAMICWORLD/V1"

S2_BANDS = ["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B11", "B12"]
LS_BANDS = {"SR_B2": "B2", "SR_B3": "B3", "SR_B4": "B4", "SR_B5": "B8", "SR_B6": "B11", "SR_B7": "B12"}
S1_BANDS = ["VV", "VH"]
NODATA = -9999.0
MIN_CLEAR_FRACTION = 0.01
WORKERS = 4
# Bump when the pixels written to disk change (mask, bands, scaling). A new
# phenology or stress formula reads these files and does not bump them.
S2_RECIPE = "s2_scene_v1"
LS_RECIPE = "landsat_scene_v1"
S1_RECIPE = "s1_scene_v1"
WEATHER_RECIPE = "village_weather_v1"


def _grid_key(grid: Grid) -> str:
    return hashlib.sha1(json.dumps(grid.__dict__, sort_keys=True).encode()).hexdigest()[:12]


def _ee():
    import ee
    from src._bootstrap import init_ee
    init_ee()
    return ee


def _region(ee, grid: Grid):
    xmin, ymin, xmax, ymax = grid.bounds()
    return ee.Geometry.Rectangle([xmin, ymin, xmax, ymax], f"EPSG:{grid.epsg}", False)


def _compute(ee, image, grid: Grid, bands: list[str], retries: int = 3) -> dict[str, np.ndarray]:
    last = None
    for attempt in range(retries):
        try:
            arr = ee.data.computePixels({
                "expression": image.select(bands).unmask(NODATA).toFloat(),
                "fileFormat": "NUMPY_NDARRAY",
                "grid": grid.ee_grid(),
            })
            out = {}
            for b in bands:
                v = np.asarray(arr[b], np.float32)
                v[v <= NODATA + 1] = np.nan
                out[b] = v
            return out
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc).lower()
            if not any(t in msg for t in ("429", "rate", "quota", "timeout", "deadline",
                                          "internal", "unavailable", "concurrent")):
                break
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"computePixels failed: {str(last)[:300]}")


def _compute_tiled(ee, image, grid: Grid, bands: list[str]) -> dict[str, np.ndarray]:
    """Large tiles split to stay inside the request size limit."""
    if grid.width * grid.height * len(bands) * 4 <= 32 * 1024 * 1024:
        return _compute(ee, image, grid, bands)
    out = {b: np.full(grid.shape, np.nan, np.float32) for b in bands}
    for sub in grid.tiles(512):
        part = _compute(ee, image, sub, bands)
        r, c = grid.offset_of(sub)
        for b in bands:
            out[b][r:r + sub.height, c:c + sub.width] = part[b]
    return out


def _dates_of(ee, col) -> list[date]:
    stamps = col.aggregate_array("system:time_start").getInfo() or []
    return sorted({datetime.utcfromtimestamp(s / 1000).date() for s in stamps})


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def _index_items(cache: Path, sensor: str, recipe: str, start: date, end: date):
    """Scene rows when this folder already lists every acquisition in [start, end]."""
    path = cache / f"{sensor}_index.json"
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        stored_start = date.fromisoformat(doc["start"])
        stored_end = date.fromisoformat(doc["end"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if doc.get("recipe") != recipe or stored_start > start or stored_end < end:
        return None
    rows = []
    for item in doc.get("items") or []:
        try:
            day = date.fromisoformat(item["date"])
        except (KeyError, TypeError, ValueError):
            return None
        if start <= day <= end:
            rows.append(item)
    return rows


def _write_index(cache: Path, sensor: str, recipe: str, start: date, end: date, items: list) -> None:
    _atomic_json(cache / f"{sensor}_index.json", {
        "recipe": recipe, "start": start.isoformat(), "end": end.isoformat(), "items": items,
    })


def _load_npz(path: Path, bands: list[str]) -> dict[str, np.ndarray]:
    with np.load(path) as z:
        return {b: np.array(z[b], dtype=np.float32, copy=True) for b in bands}


def _split_cached(items, file_of, bands: list[str]):
    """(loaded (item, arrays), items still to download). An unreadable file is re-fetched."""
    loaded, missing = [], []
    for item in items:
        path = file_of(item)
        if path.is_file():
            try:
                loaded.append((item, _load_npz(path, bands)))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("re-downloading unreadable %s: %s", path.name, str(exc)[:120])
        missing.append(item)
    return loaded, missing


def _s2_file(cache: Path, day: date) -> Path:
    return cache / f"s2_{day.isoformat()}.npz"


def _ls_file(cache: Path, day: date) -> Path:
    return cache / f"landsat_{day.isoformat()}.npz"


def _s1_file(cache: Path, day: date, pas: str, orb: int) -> Path:
    return cache / f"s1_{day.isoformat()}_{pas[:3]}{orb}.npz"


# ── Sentinel-2 ──────────────────────────────────────────────────────────────
def _s2_day_image(ee, region, day: date):
    col = (ee.ImageCollection(S2).filterBounds(region)
           .filterDate(day.isoformat(), (day + timedelta(days=1)).isoformat())
           .linkCollection(ee.ImageCollection(CS), ["cs_cdf"]))

    def prep(img):
        scl = img.select("SCL")
        ok = (img.select("cs_cdf").gte(CS_MIN)
              .And(scl.neq(1)).And(scl.neq(3)).And(scl.neq(8))
              .And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11)))
        refl = img.select(S2_BANDS).divide(10000).resample("bilinear")
        return refl.addBands(img.select("cs_cdf")).updateMask(ok)

    return col.map(prep).mosaic()


def fetch_s2(grid: Grid, start: date, end: date, cache: Path) -> SceneStack:
    bands = S2_BANDS + ["cs_cdf"]
    items = _index_items(cache, "s2", S2_RECIPE, start, end)
    ee = region = None
    if items is None:
        ee = _ee()
        region = _region(ee, grid)
        days = _dates_of(ee, ee.ImageCollection(S2).filterBounds(region)
                         .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
                         .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 95)))
        items = [{"date": d.isoformat()} for d in days]
        _write_index(cache, "s2", S2_RECIPE, start, end, items)
        logger.info("Sentinel-2: %d acquisition dates %s..%s", len(items), start, end)
    else:
        logger.info("Sentinel-2: %d dates listed on disk %s..%s", len(items), start, end)

    loaded, missing = _split_cached(items, lambda item: _s2_file(cache, date.fromisoformat(item["date"])), bands)
    got = [(date.fromisoformat(item["date"]), data) for item, data in loaded]
    if not missing:
        logger.info("Sentinel-2: %d dates from disk", len(got))
        return _assemble("s2", grid, got, bands)
    if ee is None:
        ee = _ee()
        region = _region(ee, grid)

    def download(item):
        day = date.fromisoformat(item["date"])
        data = _compute_tiled(ee, _s2_day_image(ee, region, day), grid, bands)
        np.savez_compressed(_s2_file(cache, day), **data)
        return day, data

    got.extend(_parallel(download, missing))
    return _assemble("s2", grid, got, bands)


# ── Landsat 8/9 ─────────────────────────────────────────────────────────────
def _ls_day_image(ee, region, day: date):
    col = (ee.ImageCollection(L8).merge(ee.ImageCollection(L9)).filterBounds(region)
           .filterDate(day.isoformat(), (day + timedelta(days=1)).isoformat()))

    def prep(img):
        qa = img.select("QA_PIXEL")
        bits = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
        ok = qa.bitwiseAnd(bits).eq(0).And(img.select("QA_RADSAT").eq(0))
        sr = (img.select(list(LS_BANDS)).multiply(0.0000275).add(-0.2)
              .rename(list(LS_BANDS.values())).resample("bilinear"))
        return sr.updateMask(ok)

    return col.map(prep).mosaic()


def fetch_landsat(grid: Grid, start: date, end: date, cache: Path) -> SceneStack:
    bands = list(LS_BANDS.values())
    items = _index_items(cache, "landsat", LS_RECIPE, start, end)
    ee = region = None
    if items is None:
        ee = _ee()
        region = _region(ee, grid)
        days = _dates_of(ee, ee.ImageCollection(L8).merge(ee.ImageCollection(L9)).filterBounds(region)
                         .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
                         .filter(ee.Filter.lt("CLOUD_COVER", 90)))
        items = [{"date": d.isoformat()} for d in days]
        _write_index(cache, "landsat", LS_RECIPE, start, end, items)
        logger.info("Landsat 8/9: %d acquisition dates", len(items))
    else:
        logger.info("Landsat 8/9: %d dates listed on disk", len(items))

    loaded, missing = _split_cached(
        items, lambda item: _ls_file(cache, date.fromisoformat(item["date"])), bands)
    got = [(date.fromisoformat(item["date"]), data) for item, data in loaded]
    if not missing:
        logger.info("Landsat 8/9: %d dates from disk", len(got))
        return _assemble("landsat", grid, got, bands)
    if ee is None:
        ee = _ee()
        region = _region(ee, grid)

    def download(item):
        day = date.fromisoformat(item["date"])
        data = _compute_tiled(ee, _ls_day_image(ee, region, day), grid, bands)
        np.savez_compressed(_ls_file(cache, day), **data)
        return day, data

    got.extend(_parallel(download, missing))
    return _assemble("landsat", grid, got, bands)


# ── Sentinel-1 ──────────────────────────────────────────────────────────────
def _s1_collection(ee, region, start: date, end: date):
    return (ee.ImageCollection(S1).filterBounds(region)
            .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))


def fetch_s1(grid: Grid, start: date, end: date, cache: Path) -> SceneStack:
    items = _index_items(cache, "s1", S1_RECIPE, start, end)
    ee = region = col = None
    if items is None:
        ee = _ee()
        region = _region(ee, grid)
        col = _s1_collection(ee, region, start, end)
        info = col.reduceColumns(ee.Reducer.toList(3), ["system:time_start", "orbitProperties_pass",
                                                        "relativeOrbitNumber_start"]).get("list").getInfo() or []
        keys = sorted({(datetime.utcfromtimestamp(t / 1000).date(), p, int(o)) for t, p, o in info})
        items = [{"date": d.isoformat(), "pass": p, "orbit": o} for d, p, o in keys]
        _write_index(cache, "s1", S1_RECIPE, start, end, items)
        logger.info("Sentinel-1: %d acquisitions", len(items))
    else:
        logger.info("Sentinel-1: %d acquisitions listed on disk", len(items))

    def file_of(item):
        return _s1_file(cache, date.fromisoformat(item["date"]), item["pass"], int(item["orbit"]))

    loaded, missing = _split_cached(items, file_of, S1_BANDS)
    got = [((date.fromisoformat(item["date"]), item["pass"], int(item["orbit"])), data)
           for item, data in loaded]
    if missing:
        if col is None:
            ee = _ee()
            region = _region(ee, grid)
            col = _s1_collection(ee, region, start, end)

        def download(item):
            day = date.fromisoformat(item["date"])
            pas, orb = item["pass"], int(item["orbit"])
            img = (col.filterDate(day.isoformat(), (day + timedelta(days=1)).isoformat())
                   .filter(ee.Filter.eq("orbitProperties_pass", pas))
                   .filter(ee.Filter.eq("relativeOrbitNumber_start", orb))
                   .mosaic())
            # GRD is stored in dB; linear power is what averages correctly.
            lin = ee.Image(10).pow(img.select(S1_BANDS).divide(10))
            lin = lin.updateMask(img.select("VV").gt(-30))            # border noise
            data = _compute_tiled(ee, lin, grid, S1_BANDS)
            np.savez_compressed(_s1_file(cache, day, pas, orb), **data)
            return (day, pas, orb), data

        got.extend(_parallel(download, missing))
    else:
        logger.info("Sentinel-1: %d acquisitions from disk", len(got))
    got = [(k, v) for k, v in got if v is not None]
    dates = [k[0] for k, _ in got]
    meta = [{"pass": k[1], "relative_orbit": k[2]} for k, _ in got]
    bands = {b: np.stack([v[b] for _, v in got]) if got else np.zeros((0, *grid.shape), np.float32)
             for b in S1_BANDS}
    return SceneStack("s1", grid, dates, bands, meta)


def fetch_dynamic_world(grid: Grid, start: date, end: date, cache: Path) -> Optional[np.ndarray]:
    """Season mode of the Dynamic World label (0 water ... 4 crops ... 6 built)."""
    f = cache / f"dw_{start.isoformat()}_{end.isoformat()}.npz"
    if f.exists():
        return np.load(f)["label"]
    ee = _ee()
    region = _region(ee, grid)
    img = (ee.ImageCollection(DW).filterBounds(region)
           .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
           .select("label").mode().rename("label"))
    try:
        data = _compute_tiled(ee, img, grid, ["label"])
    except RuntimeError as exc:
        logger.warning("Dynamic World unavailable: %s", exc)
        return None
    np.savez_compressed(f, label=data["label"])
    return data["label"]


# ── helpers ─────────────────────────────────────────────────────────────────
def _parallel(fn, items):
    out = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for res in pool.map(_safe(fn), items):
            if res is not None:
                out.append(res)
    return out


def _safe(fn):
    def run(item):
        try:
            return fn(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scene %s skipped: %s", item, str(exc)[:200])
            return None
    return run


def _assemble(sensor: str, grid: Grid, got: list, bands: list[str]) -> SceneStack:
    got = sorted(got, key=lambda kv: kv[0])
    keep = []
    for day, data in got:
        clear = np.isfinite(data[bands[0]]).mean()
        if clear >= MIN_CLEAR_FRACTION:
            keep.append((day, data, float(clear)))
    stack = {b: np.stack([d[b] for _, d, _ in keep]) if keep else np.zeros((0, *grid.shape), np.float32)
             for b in bands}
    return SceneStack(sensor, grid, [k[0] for k in keep], stack,
                      [{"clear_fraction": round(k[2], 4)} for k in keep])


def cache_dir(root: Path, grid: Grid) -> Path:
    p = root / _grid_key(grid)
    p.mkdir(parents=True, exist_ok=True)
    (p / "grid.json").write_text(json.dumps(grid.__dict__), encoding="utf-8")
    return p


def _pull_weather(grid: Grid, start: date, end: date):
    from src.observe import _weather

    ee = _ee()
    lon0, lat0, lon1, lat1 = grid.lonlat_bounds()
    geom = ee.Geometry.Rectangle([lon0, lat0, lon1, lat1])
    return _weather(ee, geom, start, end, (lat0 + lat1) / 2, storms=True, longitude=(lon0 + lon1) / 2)


def _weather_rows(days) -> list[dict]:
    from dataclasses import asdict

    rows = []
    for day in days:
        row = asdict(day)
        row["date"] = day.date.isoformat()
        for key, value in list(row.items()):
            if hasattr(value, "item"):
                row[key] = value.item()
        rows.append(row)
    return rows


def _weather_days(rows: list[dict]):
    from src.models import WeatherDay

    out = []
    for row in rows:
        row = dict(row)
        row["date"] = date.fromisoformat(row["date"])
        out.append(WeatherDay(**row))
    return out


def _read_weather(path: Path):
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        date.fromisoformat(doc["start"])
        date.fromisoformat(doc["end"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if doc.get("recipe") != WEATHER_RECIPE or not isinstance(doc.get("days"), list):
        return None
    return doc


def _slice_weather(rows: list[dict], start: date, end: date):
    return [d for d in _weather_days(rows) if start <= d.date <= end]


def fetch_weather(grid: Grid, start: date, end: date, cache: Optional[Path] = None):
    """Village weather (CHIRPS 5 km, ERA5-Land 9 km, IMERG, NASA POWER fill).

    Saved beside the scene grids. The same window is read back from disk. A
    later as-of date fetches only the days after the stored end, and only
    extends the file when that tail actually returns.
    """
    if cache is None:
        cache = cache_dir(Path(__file__).resolve().parents[2] / "cache", grid)
    path = cache / "weather.json"
    doc = _read_weather(path)
    if doc is not None:
        stored_start = date.fromisoformat(doc["start"])
        stored_end = date.fromisoformat(doc["end"])
        if stored_start <= start and stored_end >= end:
            logger.info("village weather from disk %s..%s", start, end)
            return _slice_weather(doc["days"], start, end)
        if stored_start == start and stored_end < end:
            gap_start = stored_end + timedelta(days=1)
            extra = _pull_weather(grid, gap_start, end)
            if not extra:
                logger.warning("weather %s..%s returned nothing; cache left at %s",
                               gap_start, end, stored_end)
                return _slice_weather(doc["days"], start, stored_end)
            days = _slice_weather(doc["days"], start, stored_end)
            days.extend(d for d in extra if d.date > stored_end)
            _atomic_json(path, {"recipe": WEATHER_RECIPE, "start": start.isoformat(),
                                "end": end.isoformat(), "days": _weather_rows(days)})
            logger.info("village weather extended %s..%s", gap_start, end)
            return days
    days = _pull_weather(grid, start, end)
    if days:
        _atomic_json(path, {"recipe": WEATHER_RECIPE, "start": start.isoformat(),
                            "end": end.isoformat(), "days": _weather_rows(days)})
        logger.info("village weather saved %s..%s (%d days)", start, end, len(days))
    return days
