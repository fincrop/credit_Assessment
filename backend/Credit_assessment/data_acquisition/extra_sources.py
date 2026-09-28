"""
Inputs for the tier-2 feature blocks (crop_analysis/extra_features.py).

One implementation, imported by the offline extractor
(Crop_classification_model/src/extract_extra.py) and by the live classifier,
so the pixels a model was trained on are reduced exactly the way they are
served.

  Sentinel-1 GRD     VV / VH backscatter (dB), 10-day median composites
  Sentinel-2 L2A     10 surface-reflectance bands, cloud-masked 10-day medians
  AlphaEarth         GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL, 64-d, per year
  NASA POWER         daily T2M / T2M_MAX / T2M_MIN / PRECTOTCORR / RH2M

EFFICIENCY: bins are stacked as bands of ONE image and reduced with a single
reduceRegions over many polygons, instead of one request per bin. For a season
of ~25 bins x 12 bands that is ~300 bands in one call, which Earth Engine
handles comfortably and which turns ~9,000 training cycles into a few dozen
requests.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# The shared 10-day grid origin of the training extraction (see
# Crop_classification_model/src/extract.py::global_bin_anchor). Serving uses the
# same phase so a bin means the same ten days on both sides.
BIN_ANCHOR = date(2020, 12, 6)
INTERVAL_DAYS = 10
SCALE_M = 10

S1_COLLECTION = "COPERNICUS/S1_GRD"
S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CS_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_BAND = "cs_cdf"
CS_THRESHOLD = 0.60
S2_CLOUD_CAP = 80
EMB_COLLECTION = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"

S1_BANDS = ("VV", "VH")
REFL_BANDS = ("B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12")
EMB_BANDS = tuple(f"A{i:02d}" for i in range(64))

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = ("T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M")
# Years of history fetched before a cycle to form the day-of-year climatology
# its anomalies are measured against. Training's per-cell frames span ~5 years.
CLIMATOLOGY_YEARS = 5

RETRIES = 4


# =============================================================================
# bin grid
# =============================================================================
def bins_covering(lo: date, hi: date, anchor: date = BIN_ANCHOR) -> List[date]:
    """Grid-aligned 10-day bin starts whose bins intersect [lo, hi]."""
    k = (lo - anchor).days // INTERVAL_DAYS
    cur = anchor + timedelta(days=k * INTERVAL_DAYS)
    out = []
    while cur <= hi:
        out.append(cur)
        cur += timedelta(days=INTERVAL_DAYS)
    return out


def bin_center(b0: date) -> date:
    return b0 + timedelta(days=INTERVAL_DAYS // 2)


# =============================================================================
# per-bin images
# =============================================================================
def _blank(ee, bands: Sequence[str]):
    return (ee.Image.constant([0] * len(bands)).rename(list(bands)).float()
            .updateMask(ee.Image.constant(0)))


def s1_bin_image(ee, aoi, start: str, end: str):
    """Median VV/VH (dB) of IW dual-pol GRD scenes in [start, end)."""
    coll = (
        ee.ImageCollection(S1_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(list(S1_BANDS))
    )
    return ee.ImageCollection([_blank(ee, S1_BANDS)]).merge(coll).median()


def _s2_mask(ee, img):
    qa = img.select("QA60")
    ok = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    scl = img.select("SCL")
    ok = ok.And(scl.neq(3)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    ok = ok.And(img.select(CS_BAND).gte(CS_THRESHOLD))
    return img.updateMask(ok).select(list(REFL_BANDS)).divide(10000).float()


def s2_reflectance_bin_image(ee, aoi, start: str, end: str):
    """Cloud-masked (QA60 + SCL + Cloud Score+) median reflectance, 0-1."""
    coll = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_CLOUD_CAP))
        .linkCollection(ee.ImageCollection(CS_COLLECTION), [CS_BAND])
        .map(lambda i: _s2_mask(ee, i))
    )
    return ee.ImageCollection([_blank(ee, REFL_BANDS)]).merge(coll).median()


def embedding_image(ee, year: int):
    """AlphaEarth embedding for `year`. The product is published about a year
    behind, so a current-year request has no images; the blank keeps the band
    names so it comes back fully masked instead of failing on select."""
    coll = (ee.ImageCollection(EMB_COLLECTION)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .select(list(EMB_BANDS)))
    return ee.ImageCollection([_blank(ee, EMB_BANDS)]).merge(coll).mosaic()


def time_stack(ee, aoi, bins: Sequence[date], blocks: Iterable[str]):
    """All requested bins x bands as one multi-band image.

    Band names are `<band>__<k>` where k indexes `bins`.
    """
    blocks = set(blocks)
    parts = []
    for k, b0 in enumerate(bins):
        s, e = b0.isoformat(), (b0 + timedelta(days=INTERVAL_DAYS)).isoformat()
        if "s1" in blocks:
            parts.append(s1_bin_image(ee, aoi, s, e)
                         .rename([f"{b}__{k}" for b in S1_BANDS]))
        if "refl" in blocks:
            parts.append(s2_reflectance_bin_image(ee, aoi, s, e)
                         .rename([f"{b}__{k}" for b in REFL_BANDS]))
    if not parts:
        raise ValueError("time_stack needs 's1' and/or 'refl'")
    return ee.Image.cat(parts)


# =============================================================================
# reduction
# =============================================================================
def _getinfo_retry(obj):
    last = None
    for attempt in range(RETRIES):
        try:
            return obj.getInfo()
        except Exception as exc:                          # noqa: BLE001
            last = exc
            msg = str(exc).lower()
            transient = any(t in msg for t in (
                "concurrent", "rate", "quota", "timed out", "timeout", "deadline",
                "internal", "backend", "503", "429", "temporar", "unavailable",
            ))
            if not transient or attempt == RETRIES - 1:
                break
            time.sleep(5 * 2 ** attempt)
    raise RuntimeError(str(last)[:300])


def _ee_geojson(g: Mapping[str, Any]) -> Dict[str, Any]:
    """2-D, list-based GeoJSON. Earth Engine rejects geometries carrying Z
    coordinates ("Invalid GeoJSON geometry"), and field polygons digitised in
    some tools arrive 3-D — 641 of the augmented training parcels did."""
    import shapely
    from shapely.geometry import mapping, shape
    geom = shapely.force_2d(shape(g))
    return json.loads(json.dumps(mapping(geom)))


def reduce_features(ee, image, features: Sequence[Tuple[str, Mapping[str, Any]]],
                    scale: int = SCALE_M, tile_scale: int = 4) -> Dict[str, Dict[str, Any]]:
    """Mean of every band of `image` over each (id, GeoJSON geometry)."""
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(_ee_geojson(g), None, False), {"_id": str(fid)})
        for fid, g in features
    ])
    out = image.reduceRegions(collection=fc, reducer=ee.Reducer.mean(),
                              scale=scale, tileScale=tile_scale)
    info = _getinfo_retry(out)
    res: Dict[str, Dict[str, Any]] = {}
    for f in info.get("features", []):
        p = f.get("properties") or {}
        fid = p.pop("_id", None)
        if fid is not None:
            res[str(fid)] = p
    return res


def unstack(props: Mapping[str, Any], bins: Sequence[date], blocks: Iterable[str]
            ) -> Dict[str, List[Dict[str, Any]]]:
    """reduceRegions properties -> {"s1": [...], "refl": [...]} series lists
    in the shape extra_features expects (dated at bin centres)."""
    blocks = set(blocks)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for blk, bands in (("s1", S1_BANDS), ("refl", REFL_BANDS)):
        if blk not in blocks:
            continue
        series = []
        for k, b0 in enumerate(bins):
            rec: Dict[str, Any] = {"date": bin_center(b0).isoformat()}
            have = False
            for b in bands:
                v = props.get(f"{b}__{k}")
                rec[b] = v
                have = have or v is not None
            if have:
                series.append(rec)
        out[blk] = series
    return out


def fetch_time_series(ee, geoms: Sequence[Tuple[str, Mapping[str, Any]]],
                      lo: date, hi: date, blocks: Iterable[str]
                      ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """S1 / reflectance series for several polygons over one window."""
    blocks = [b for b in blocks if b in ("s1", "refl")]
    if not blocks or not geoms:
        return {}
    bins = bins_covering(lo, hi)
    aoi = ee.FeatureCollection([ee.Feature(ee.Geometry(_ee_geojson(g), None, False))
                                for _, g in geoms]).geometry().bounds()
    img = time_stack(ee, aoi, bins, blocks)
    raw = reduce_features(ee, img, geoms)
    return {fid: unstack(p, bins, blocks) for fid, p in raw.items()}


def fetch_embeddings(ee, geoms: Sequence[Tuple[str, Mapping[str, Any]]], year: int
                     ) -> Dict[str, Optional[List[float]]]:
    raw = reduce_features(ee, embedding_image(ee, year), geoms)
    out: Dict[str, Optional[List[float]]] = {}
    for fid, p in raw.items():
        vec = [p.get(b) for b in EMB_BANDS]
        out[fid] = None if any(v is None for v in vec) else [float(v) for v in vec]
    return out


# =============================================================================
# NASA POWER
# =============================================================================
def fetch_power_daily(lat: float, lon: float, start: date, end: date,
                      timeout: int = 120):
    """Daily POWER frame for one point, -999 -> NaN. Raises on failure."""
    import numpy as np
    import pandas as pd
    import requests

    params = {
        "parameters": ",".join(POWER_PARAMS), "community": "AG",
        "latitude": round(float(lat), 4), "longitude": round(float(lon), 4),
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }
    last = None
    for attempt in range(3):
        try:
            r = requests.get(POWER_URL, params=params, timeout=timeout)
            r.raise_for_status()
            props = r.json().get("properties", {}).get("parameter", {})
            if not props:
                raise ValueError("empty POWER parameter block")
            df = pd.DataFrame(props)
            df.index = pd.to_datetime(df.index, format="%Y%m%d")
            df = df.replace(-999.0, np.nan).reset_index(names="date")
            return df
        except Exception as exc:                          # noqa: BLE001
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"NASA POWER request failed: {str(last)[:200]}")


def fetch_weather_for_cycles(lat: float, lon: float, lo: date, hi: date):
    """Daily weather with day-of-year climatology attached, ready for
    extra_features.build_weather_features. The climatology spans
    CLIMATOLOGY_YEARS before `lo`, so anomalies are relative to the place's
    own recent normal — the same construction the training cells used."""
    from crop_analysis.extra_features import add_climatology

    # Snap to the 0.25-degree cell centre the training frames were fetched on.
    # Measured on live parcels: with the snap every absolute weather feature
    # matches training exactly; without it gdd_total drifts ~1%.
    #
    # Residual, documented: anomaly columns differ by ~0.02 because training's
    # climatology used each cell's full 2020-2025 record while serving can only
    # use the CLIMATOLOGY_YEARS before the cycle (no future data at inference).
    lat, lon = power_cell(lat, lon)
    start = date(lo.year - CLIMATOLOGY_YEARS, 1, 1)
    end = min(hi, date.today() - timedelta(days=3))
    return add_climatology(fetch_power_daily(lat, lon, start, end))


POWER_GRID_DEG = 0.25


def power_cell(lat: float, lon: float) -> Tuple[float, float]:
    """Centre of the POWER grid cell — identical to src/weather.py::_cell."""
    g = POWER_GRID_DEG
    return round(round(lat / g) * g, 2), round(round(lon / g) * g, 2)


# =============================================================================
# serving orchestration — everything one farm's cycles need, in few requests
# =============================================================================
def _cycle_dates(c: Any) -> Tuple[Optional[date], Optional[date], Optional[date]]:
    from crop_analysis.extra_features import _as_date

    def g(k1, k2=None):
        v = c.get(k1) if isinstance(c, dict) else getattr(c, k1, None)
        if v is None and k2:
            v = c.get(k2) if isinstance(c, dict) else getattr(c, k2, None)
        return _as_date(v)
    return g("sowing_date", "start_date"), g("harvest_date", "end_date"), g("peak_date")


def fetch_inputs_for_cycles(geometry: Mapping[str, Any], lat: float, lon: float,
                            cycles: Sequence[Any], blocks: Iterable[str],
                            ee_module=None) -> Dict[int, Dict[str, Any]]:
    """
    Tier-2 inputs for every cycle of one farm.

    One reduceRegions over the union window for S1 + reflectance, one POWER
    call, one embedding reduction per distinct year. A failing source is
    logged and left out; the feature builders turn a missing input into NaN
    columns, and the classifier records which blocks were unavailable.

    Returns {cycle_position: {"s1_series", "refl_series", "weather_daily",
    "embedding", "missing": [...]}}.
    """
    from crop_analysis.extra_features import PAD_DAYS, embedding_year

    blocks = set(blocks)
    spans = {}
    for i, c in enumerate(cycles):
        s, h, p = _cycle_dates(c)
        if s and h:
            spans[i] = (s, h, p)
    out: Dict[int, Dict[str, Any]] = {i: {"missing": []} for i in spans}
    if not spans:
        return out
    lo = min(s for s, _, _ in spans.values()) - timedelta(days=PAD_DAYS)
    hi = max(h for _, h, _ in spans.values()) + timedelta(days=PAD_DAYS)
    geoms = [("farm", dict(geometry))]

    ee = ee_module
    if blocks & {"s1", "refl", "emb"} and ee is None:
        import ee  # noqa: PLC0415

    if blocks & {"s1", "refl"}:
        try:
            ser = fetch_time_series(ee, geoms, lo, hi, blocks).get("farm", {})
        except Exception as exc:                          # noqa: BLE001
            logger.warning("tier-2 S1/reflectance fetch failed: %s", str(exc)[:160])
            ser = {}
        for i in out:
            out[i]["s1_series"] = ser.get("s1")
            out[i]["refl_series"] = ser.get("refl")
            out[i]["missing"] += [b for b in ("s1", "refl") if b in blocks and not ser.get(b)]

    if "weather" in blocks:
        try:
            daily = fetch_weather_for_cycles(lat, lon, lo, hi)
        except Exception as exc:                          # noqa: BLE001
            logger.warning("tier-2 weather fetch failed: %s", str(exc)[:160])
            daily = None
        for i in out:
            out[i]["weather_daily"] = daily
            if daily is None:
                out[i]["missing"].append("weather")

    if "emb" in blocks:
        years = {i: embedding_year(s, h, p) for i, (s, h, p) in spans.items()}
        vecs: Dict[int, Optional[List[float]]] = {}
        for y in sorted(set(years.values())):
            try:
                vecs[y] = fetch_embeddings(ee, geoms, y).get("farm")
            except Exception as exc:                      # noqa: BLE001
                logger.warning("embedding fetch failed for %s: %s", y, str(exc)[:160])
                vecs[y] = None
        for i in out:
            out[i]["embedding"] = vecs.get(years[i])
            if out[i]["embedding"] is None:
                out[i]["missing"].append("emb")
    return out
