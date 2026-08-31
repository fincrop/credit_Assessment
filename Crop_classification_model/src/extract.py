"""
Phase 2 — Satellite extraction.

data/00_parcels_clean.parquet -> data/01_scenes_raw.parquet

THE EXPENSIVE ARTIFACT. Runs once; every later stage iterates against the cache.

PARITY IS THE WHOLE POINT
─────────────────────────
Index formulas, cloud masking, the collection, the cloud cap, the scale and the
reducer are all mirrored from SatelliteDataCollector._fetch_gee_scene_stats.
Three things there are easy to get wrong:

  1. Production reduces over `ee.Geometry.Rectangle(bbox)` — the polygon's
     ENVELOPE, not the polygon. In this dataset the envelope is a median 1.49x
     the parcel area, so only ~67% of the pixels production averages sit inside
     the field (40% at p5). `bbox` is therefore what the shipped model trains
     on. A `--poly-fraction` sample also extracts the true polygon so the
     dilution can be quantified and a Stage-2 fix argued from evidence.

  2. Production reduces PER SCENE, then keeps the lowest-cloud scene per 10-day
     bin. We reduce ONE cloud-sorted mosaic per bin: images are sorted
     cloudiest-first and mosaicked, so the clearest scene's pixels sit on top
     and the next-clearest fills only its masked holes. Where the clearest scene
     covers the parcel this is identical to production; where it does not,
     production yields NaN (or falls back) and we recover the pixel. A
     deliberate, documented, strictly-better deviation — and the only reason the
     job is hours rather than days.

  3. Production applies `.sort(CLOUD).limit(120)` per YEAR as a scene budget.
     We do not: the per-bin structure already bounds work, and a multi-year
     sort/limit would starve whole seasons — the exact truncation bug
     production's own comments describe.

ARCHITECTURE — bin-centric, measured not guessed
────────────────────────────────────────────────
Benchmarked at ~50 feature-reductions/second regardless of how they are
grouped, so the only thing that matters is minimising REQUESTS. One request per
(bin, spatially-coherent parcel chunk) reduces thousands of parcels at once
instead of one parcel over many bins.

Run:
  python -m src.extract --limit-per-crop 100      # the spike (~1.6 h)
  python -m src.extract                           # everything (~5 h)
  python -m src.extract --validate 30             # parity vs the collector
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, PROJECT, init_ee, setup_logging

log = setup_logging("extract")

IN = DATA / "00_parcels_clean.parquet"
OUT = DATA / "01_scenes_raw.parquet"
SHARD_DIR = DATA / "shards"

# ── production constants (mirrored, not guessed) ─────────────────────────────
S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CS_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_BAND = "cs_cdf"
CS_THRESHOLD = 0.60           # PipelineConfig.CLOUD_SCORE_PLUS_THRESHOLD
CLOUD_CAP = 70.0              # PipelineConfig.MAX_CLOUD_COVER_CONTINUOUS
INTERVAL_DAYS = 10            # PipelineConfig.CONTINUOUS_SCENE_INTERVAL_DAYS
SCALE_M = 10                  # PipelineConfig.TARGET_RESOLUTION_M

INDEX_BANDS = [
    "NDVI", "EVI", "NDMI", "NDWI", "NDRE", "PSRI", "LSWI",
    "MSAVI2", "NIRv", "NDBI", "MNDWI", "BSI", "GCVI", "kNDVI",
]
# Production keeps mean for every index plus stdDev/p90 for NDVI.
STAT_COLS = (
    [f"{b}_mean" for b in INDEX_BANDS] + ["NDVI_stdDev", "NDVI_p90"]
)

# ── batching (benchmark-derived) ─────────────────────────────────────────────
FEATURES_PER_REQUEST = 3000   # ~55 s/request; getInfo stays well inside limits
REQUEST_RETRIES = 4
RETRY_BASE_SLEEP = 5.0


# =============================================================================
# server-side index computation — mirrors _scene_to_feature exactly
# =============================================================================
def _indices_image(img):
    import ee

    qa = img.select("QA60")
    cloud_mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    scl = img.select("SCL")
    scl_mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    mask = cloud_mask.And(scl_mask).And(img.select(CS_BAND).gte(CS_THRESHOLD))

    masked = img.updateMask(mask).divide(10000)

    NIR = masked.select("B8")
    RED = masked.select("B4")
    BLUE = masked.select("B2")
    GREEN = masked.select("B3")
    SWIR1 = masked.select("B11")
    RE2 = masked.select("B6")

    ndvi = masked.normalizedDifference(["B8", "B4"]).rename("NDVI")
    evi = masked.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": NIR, "RED": RED, "BLUE": BLUE},
    ).rename("EVI")
    ndmi = masked.normalizedDifference(["B8", "B11"]).rename("NDMI")
    ndwi = masked.normalizedDifference(["B3", "B8"]).rename("NDWI")
    ndre = masked.normalizedDifference(["B8", "B5"]).rename("NDRE")
    psri = masked.expression(
        "(RED - BLUE) / (RE2 + 1e-6)",
        {"RED": RED, "BLUE": BLUE, "RE2": RE2},
    ).rename("PSRI")
    # LSWI shares NDMI's formula but is carried with water semantics.
    lswi = masked.normalizedDifference(["B8", "B11"]).rename("LSWI")
    msavi2 = masked.expression(
        "(2 * NIR + 1 - sqrt((2 * NIR + 1) ** 2 - 8 * (NIR - RED))) / 2",
        {"NIR": NIR, "RED": RED},
    ).rename("MSAVI2")
    nirv = ndvi.multiply(NIR).rename("NIRv")
    ndbi = masked.normalizedDifference(["B11", "B8"]).rename("NDBI")
    mndwi = masked.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    bsi = masked.expression(
        "((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE) + 1e-6)",
        {"SWIR1": SWIR1, "RED": RED, "NIR": NIR, "BLUE": BLUE},
    ).rename("BSI")
    gcvi = NIR.divide(GREEN.add(1e-6)).subtract(1).rename("GCVI")
    kndvi = ndvi.pow(2).tanh().rename("kNDVI")

    return ee.Image.cat([
        ndvi, evi, ndmi, ndwi, ndre, psri, lswi, msavi2, nirv,
        ndbi, mndwi, bsi, gcvi, kndvi,
    ])


def _reducer():
    import ee

    return (
        ee.Reducer.mean()
        .combine(ee.Reducer.stdDev(), None, True)
        .combine(ee.Reducer.percentile([90]), None, True)
    )


def utm_epsg_for_lon(lon: float) -> str:
    """
    Northern-hemisphere UTM zone for a longitude. India spans zones 42-47.

    Used to pin the reduction grid — see `_mosaic_between`.
    """
    zone = int((float(lon) + 180) // 6) + 1
    return f"EPSG:326{zone:02d}"


def bin_mosaic_ee(aoi, b0, epsg: str):
    """
    Cloud-sorted mosaic for the 10-day bin starting at `b0` (an ee.Date).

    Server-side variant so it can be called from inside an ee.List.map; the
    client-side `_bin_mosaic` wraps it. Keeping ONE implementation matters — a
    second copy of the mosaic logic is how a validator ends up certifying code
    the extractor does not actually run.
    """
    import ee

    b0 = ee.Date(b0)
    return _mosaic_between(aoi, b0, b0.advance(INTERVAL_DAYS, "day"), epsg)


def _bin_mosaic(aoi, b0: date, epsg: str):
    """Cloud-sorted mosaic for one 10-day bin. Clearest scene lands on top."""
    start = b0.strftime("%Y-%m-%d")
    end = (b0 + timedelta(days=INTERVAL_DAYS)).strftime("%Y-%m-%d")
    return _mosaic_between(aoi, start, end, epsg)


def _mosaic_between(aoi, start, end, epsg: str):
    """
    THE PROJECTION IS NOT OPTIONAL.

    ee.ImageCollection.mosaic() returns an image whose default projection is
    EPSG:4326 at a 111 km nominal scale, NOT the Sentinel-2 tile's native UTM.
    Reducing that at scale=10 rasterises on a lat/lon grid instead of the
    sensor grid, which shifted EVERY bin by ~0.001-0.2 NDVI against production
    (measured: 0/331 bins matched, p95 0.0165). Pinning the grid with
    setDefaultProjection brings the difference to exactly 0.000000 — verified
    against SatelliteDataCollector on both single- and multi-scene bins.

    Callers must pass the UTM zone the parcels actually sit in, which is why
    requests are chunked by zone.
    """
    import ee

    coll = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(aoi)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_CAP))
        .linkCollection(ee.ImageCollection(CS_COLLECTION), [CS_BAND])
    )
    # A bin with no qualifying scene yields an EMPTY collection, whose mosaic()
    # is a band-less image — reduceRegions then raises "Image has no bands" and
    # kills the whole server-side bin loop, not just that bin. Anchoring on a
    # fully masked constant image keeps the band schema present so an empty bin
    # returns nulls (a real "no observation") instead of an exception.
    blank = (
        ee.Image.constant([0] * len(INDEX_BANDS))
        .rename(INDEX_BANDS)
        .float()
        .updateMask(ee.Image.constant(0))
    )
    # Descending cloud order: ee.ImageCollection.mosaic() puts the LAST image on
    # top, so the clearest scene wins and cloudier ones only fill its holes.
    indexed = coll.sort("CLOUDY_PIXEL_PERCENTAGE", False).map(_indices_image)
    mosaic = ee.ImageCollection([blank]).merge(indexed).mosaic()
    return mosaic.setDefaultProjection(ee.Projection(epsg).atScale(SCALE_M))


def _feature_collection(batch: gpd.GeoDataFrame, poly_hashes: set):
    """One ee.Feature per (parcel, geometry kind)."""
    import ee

    feats = []
    for row in batch.itertuples():
        feats.append(ee.Feature(
            ee.Geometry(row.geometry.envelope.__geo_interface__,
                        proj="EPSG:4326", geodesic=False),
            {"geom_hash": row.geom_hash, "geom_kind": "bbox"},
        ))
        if row.geom_hash in poly_hashes:
            feats.append(ee.Feature(
                ee.Geometry(row.geometry.__geo_interface__,
                            proj="EPSG:4326", geodesic=False),
                {"geom_hash": row.geom_hash, "geom_kind": "poly"},
            ))
    return ee.FeatureCollection(feats)


def _request(mosaic, fc, reducer) -> List[Dict[str, Any]]:
    out = mosaic.reduceRegions(collection=fc, reducer=reducer, scale=SCALE_M)

    last_err: Optional[Exception] = None
    for attempt in range(REQUEST_RETRIES):
        try:
            info = out.getInfo()
            return [f.get("properties", {}) for f in info.get("features", [])]
        except Exception as exc:                     # noqa: BLE001 — EE raises broadly
            last_err = exc
            msg = str(exc).lower()
            transient = any(t in msg for t in (
                "concurrent", "rate limit", "quota", "timed out", "timeout",
                "deadline", "internal error", "backend error", "503", "429",
                "temporarily", "unavailable",
            ))
            if attempt == REQUEST_RETRIES - 1 or not transient:
                break
            sleep = RETRY_BASE_SLEEP * (2 ** attempt)
            log.warning("    request failed (%s); retry %d/%d in %.0fs",
                        str(exc)[:90], attempt + 1, REQUEST_RETRIES, sleep)
            time.sleep(sleep)

    raise RuntimeError(str(last_err))


# =============================================================================
# planning
# =============================================================================
def global_bin_anchor(parcels: gpd.GeoDataFrame) -> date:
    """
    The single origin of the 10-day bin grid: the earliest window start.

    EVERY consumer must derive bins from this same anchor. A parcel-local grid
    starting at its own win_start lands on the shared grid only when the offset
    happens to be a multiple of INTERVAL_DAYS, so lookups silently miss and the
    parcel looks like it has no observations at all.
    """
    return min(datetime.strptime(s, "%Y-%m-%d").date() for s in parcels["win_start"])


def bins_for_window(anchor: date, win_start: str, win_end: str) -> List[date]:
    """Grid-aligned bin starts covering [win_start, win_end]."""
    d0 = datetime.strptime(win_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(win_end, "%Y-%m-%d").date()
    # Snap DOWN to the shared grid so the first bin contains d0.
    k = (d0 - anchor).days // INTERVAL_DAYS
    cur = anchor + timedelta(days=k * INTERVAL_DAYS)
    out = []
    while cur <= d1:
        if cur + timedelta(days=INTERVAL_DAYS) > d0:
            out.append(cur)
        cur = cur + timedelta(days=INTERVAL_DAYS)
    return out


def _global_bins(parcels: gpd.GeoDataFrame) -> List[date]:
    """10-day bin grid spanning every parcel's window, on the shared anchor."""
    d0 = global_bin_anchor(parcels)
    d1 = max(datetime.strptime(s, "%Y-%m-%d").date() for s in parcels["win_end"])
    out, cur = [], d0
    while cur <= d1:
        out.append(cur)
        cur = cur + timedelta(days=INTERVAL_DAYS)
    return out


def _spatial_sort(df: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Order parcels so that consecutive chunks are geographically coherent — a
    chunk spanning one region loads far fewer Sentinel-2 tiles than a chunk
    scattered across India.
    """
    d = df.copy()
    d["_la"] = np.floor(d["lat"] * 2).astype(int)
    d["_lo"] = np.floor(d["lon"] * 2).astype(int)
    # Serpentine order: alternate lon direction per lat band to keep successive
    # chunks adjacent instead of jumping back across the subcontinent.
    d = d.sort_values(["_la", "_lo"], kind="stable")
    out = []
    for i, (_, band) in enumerate(d.groupby("_la", sort=True)):
        out.append(band.iloc[::-1] if i % 2 else band)
    return gpd.GeoDataFrame(pd.concat(out), crs=df.crs).drop(columns=["_la", "_lo"])


def _subset(parcels: gpd.GeoDataFrame, limit_per_crop: int, seed: int):
    """Spike subset: N per crop, spread across spatial blocks rather than
    clustered, so spike metrics are not measured on one neighbourhood."""
    if not limit_per_crop:
        return parcels
    out = []
    for _, grp in parcels.groupby("Crop_Name"):
        g = grp.sample(frac=1.0, random_state=seed).sort_values("block_id", kind="stable")
        step = max(1, len(g) // limit_per_crop)
        out.append(g.iloc[::step].head(limit_per_crop))
    return gpd.GeoDataFrame(pd.concat(out), crs=parcels.crs)


# =============================================================================
# driver
# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-per-crop", type=int, default=0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--poly-fraction", type=float, default=0.25,
                    help="share of parcels ALSO reduced over the true polygon, "
                         "to quantify production's bbox dilution")
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", dest="resume", action="store_false")
    ap.add_argument("--validate", type=int, default=0)
    ap.add_argument("--consolidate-only", action="store_true")
    ap.add_argument("--workers", type=int, default=1,
                    help="total number of parallel extractor processes")
    ap.add_argument("--worker", type=int, default=0,
                    help="this process's index in [0, workers)")
    args = ap.parse_args()

    if not IN.exists():
        log.error("missing %s — run `python -m src.ingest` first", IN)
        return 1

    parcels = gpd.read_parquet(IN)
    parcels = _subset(parcels, args.limit_per_crop, args.seed)
    parcels = _spatial_sort(parcels)
    parcels["utm_epsg"] = [utm_epsg_for_lon(v) for v in parcels["lon"]]

    rng = np.random.default_rng(args.seed)
    poly_mask = rng.random(len(parcels)) < args.poly_fraction
    poly_hashes = set(parcels.loc[poly_mask, "geom_hash"])

    log.info("parcels: %d  |  polygon-comparison subset: %d (%.0f%%)",
             len(parcels), len(poly_hashes), 100 * args.poly_fraction)

    if not args.consolidate_only:
        project = init_ee()
        log.info("earth engine ready (project=%s)", project)

    if args.validate:
        from .validate_parity import run_validation
        return run_validation(parcels, args.validate)

    SHARD_DIR.mkdir(parents=True, exist_ok=True)

    if not args.consolidate_only:
        rc = _run_extraction(parcels, poly_hashes, args.resume,
                             args.worker, args.workers)
        if rc not in (0, 2):
            return rc

    return _consolidate(parcels)


def _run_extraction(parcels: gpd.GeoDataFrame, poly_hashes: set, resume: bool,
                    worker: int = 0, workers: int = 1) -> int:
    import ee

    bins = _global_bins(parcels)
    reducer = _reducer()

    # Precompute each parcel's window as dates once.
    ws = pd.to_datetime(parcels["win_start"]).dt.date.values
    we = pd.to_datetime(parcels["win_end"]).dt.date.values

    # Plan: (bin, chunk) units, sized so each request holds ~FEATURES_PER_REQUEST
    # features. Feature count per parcel is 2 for the polygon subset, else 1.
    per_parcel_feats = np.where(parcels["geom_hash"].isin(poly_hashes), 2, 1)

    # Chunk by UTM zone: the reduction grid is pinned per request, so every
    # parcel in a request must belong to the zone being pinned.
    zones = parcels["utm_epsg"].values

    units: List[Tuple[date, str, np.ndarray]] = []
    for b0 in bins:
        need = np.where((ws <= b0) & (b0 <= we))[0]
        if need.size == 0:
            continue
        for epsg in np.unique(zones[need]):
            zpart = need[zones[need] == epsg]
            cum = np.cumsum(per_parcel_feats[zpart])
            n_chunks = max(1, int(np.ceil(cum[-1] / FEATURES_PER_REQUEST)))
            for part in np.array_split(zpart, n_chunks):
                if part.size:
                    units.append((b0, str(epsg), part))

    total_feats = sum(int(per_parcel_feats[p].sum()) for _, _, p in units)
    log.info("bins spanning all windows: %d", len(bins))
    log.info("requests planned: %d  |  feature-reductions: %d", len(units), total_feats)
    log.info("estimated wall time at ~45 feat/s, 1 worker: %.1f h",
             total_feats / 45 / 3600)

    if workers > 1:
        # Interleave rather than block-partition: units are ordered by bin and
        # early/late bins have far fewer parcels in window, so contiguous slices
        # would hand one worker most of the work.
        units = units[worker::workers]
        log.info("worker %d/%d taking %d unit(s)", worker, workers, len(units))
        total_feats = sum(int(per_parcel_feats[p].sum()) for _, _, p in units)

    t0 = time.time()
    n_done = n_skip = n_fail = 0
    feats_done = 0

    for ui, (b0, epsg, idx) in enumerate(units, 1):
        tag = f"{b0.isoformat()}__{epsg.replace(':', '')}__{idx[0]:06d}_{idx[-1]:06d}"
        shard = SHARD_DIR / f"{tag}.parquet"
        unit_feats = int(per_parcel_feats[idx].sum())

        if resume and shard.exists():
            n_skip += 1
            feats_done += unit_feats
            continue

        batch = parcels.iloc[idx]
        # Peers write shards while we work; re-check right before spending a
        # request so parallel workers do not duplicate each other's units.
        if resume and shard.exists():
            n_skip += 1
            feats_done += unit_feats
            continue
        try:
            aoi = ee.Geometry(
                batch.geometry.unary_union.envelope.__geo_interface__,
                proj="EPSG:4326", geodesic=False,
            )
            fc = _feature_collection(batch, poly_hashes)
            rows = _request(_bin_mosaic(aoi, b0, epsg), fc, reducer)
        except RuntimeError as exc:
            n_fail += 1
            log.error("  unit %s failed: %s", tag, str(exc)[:140])
            continue

        df = pd.DataFrame(rows)
        if df.empty:
            df = pd.DataFrame(columns=["geom_hash", "geom_kind"])
        for c in STAT_COLS:
            if c not in df.columns:
                df[c] = np.nan
        df["bin_start"] = b0.isoformat()
        tmp = shard.with_suffix(".parquet.tmp")
        df[["geom_hash", "geom_kind", "bin_start"] + STAT_COLS].to_parquet(
            tmp, index=False)
        tmp.replace(shard)          # atomic: readers never see a partial file

        n_done += 1
        feats_done += unit_feats

        if ui % 20 == 0 or ui == len(units):
            el = time.time() - t0
            rate = (feats_done / el) if el > 0 else 0
            eta = (total_feats - feats_done) / rate if rate > 0 else float("nan")
            log.info(
                "unit %d/%d  done=%d skip=%d fail=%d  %.0f feat/s  eta %.1f h",
                ui, len(units), n_done, n_skip, n_fail, rate, eta / 3600,
            )

    log.info("extraction finished: done=%d skip=%d fail=%d", n_done, n_skip, n_fail)
    return 0 if n_fail == 0 else 2


# Shard names encode the architecture that wrote them. An earlier per-cell
# design binned on each parcel's OWN win_start rather than the shared anchor, so
# mixing its files in produced bins on six different grids and every lookup in
# cycles.py missed. Consolidation now reads ONLY current-format shards.
SHARD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}__EPSG\d{5}__\d{6}_\d{6}\.parquet$")


def _consolidate(parcels: gpd.GeoDataFrame) -> int:
    all_files = sorted(SHARD_DIR.glob("*.parquet"))
    shards = [f for f in all_files if SHARD_RE.match(f.name)]
    foreign = len(all_files) - len(shards)
    if foreign:
        log.warning("ignoring %d shard(s) not matching the current format "
                    "(likely from an earlier extraction architecture)", foreign)
    if not shards:
        log.error("no shards to consolidate")
        return 1

    log.info("consolidating %d shard(s)", len(shards))
    parts = []
    for i, s in enumerate(shards, 1):
        d = pd.read_parquet(s)
        if not d.empty:
            parts.append(d)
        if i % 500 == 0:
            log.info("  read %d/%d", i, len(shards))

    if not parts:
        log.error("all shards empty")
        return 1

    allrows = pd.concat(parts, ignore_index=True)
    # A (parcel, kind, bin) can only appear once; duplicates would mean a unit
    # was sharded twice under different index ranges.
    before = len(allrows)
    allrows = allrows.drop_duplicates(subset=["geom_hash", "geom_kind", "bin_start"])
    if len(allrows) != before:
        log.warning("dropped %d duplicate (parcel, kind, bin) row(s)", before - len(allrows))

    allrows = allrows.sort_values(["geom_hash", "geom_kind", "bin_start"])

    # Every bin must sit on the single shared grid. If it does not, downstream
    # lookups miss and parcels are rejected as "no observations" — a failure
    # that is invisible unless checked here.
    anchor = global_bin_anchor(parcels)
    offsets = {
        (datetime.strptime(b, "%Y-%m-%d").date() - anchor).days % INTERVAL_DAYS
        for b in allrows["bin_start"].unique()
    }
    if offsets != {0}:
        log.error("bin grid is NOT shared: offsets mod %d = %s (expected {0}). "
                  "Delete data/shards and re-extract.", INTERVAL_DAYS,
                  sorted(offsets))
        return 1
    log.info("bin-grid check: all bins aligned to anchor %s", anchor.isoformat())

    allrows.to_parquet(OUT, index=False)

    log.info("")
    log.info("=" * 68)
    log.info("wrote %s", OUT.relative_to(PROJECT))
    log.info("  rows: %d", len(allrows))
    for kind, sub in allrows.groupby("geom_kind"):
        finite = np.isfinite(pd.to_numeric(sub["NDVI_mean"], errors="coerce"))
        log.info("  %-5s parcels=%d  bins=%d  valid NDVI=%d (%.0f%%)",
                 kind, sub["geom_hash"].nunique(), len(sub),
                 int(finite.sum()), 100 * finite.mean())
    bbox = allrows[allrows["geom_kind"] == "bbox"]
    per_parcel = bbox.assign(
        ok=np.isfinite(pd.to_numeric(bbox["NDVI_mean"], errors="coerce"))
    ).groupby("geom_hash")["ok"].sum()
    log.info("  valid observations per parcel: p5=%.0f median=%.0f p95=%.0f",
             per_parcel.quantile(0.05), per_parcel.median(), per_parcel.quantile(0.95))
    log.info("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
