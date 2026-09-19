"""
Area-wide crop classification — segment first, then classify.

The credit pipeline classifies a crop cycle on a *known parcel*. This module
answers a different question: given only an outline (a village, a block, a drawn
region), find the fields inside it and name each one's crop.

WHY SEGMENT-THEN-CLASSIFY, and not per-pixel-then-vectorise
-----------------------------------------------------------
The obvious route is to run the model on every pixel and polygonise the result.
It half-works, and the halves matter.

Measured on the training set (reports/pixel_vs_parcel.json), degrading the
model's input from a parcel mean to a single pixel costs 12.9 points:

    inference unit                 blocked balanced accuracy
    parcel mean (49 px, trained)   0.7614
    16-pixel object                0.7015
    4-pixel object                 0.6733
    single pixel                   0.6323

So per-pixel classification is usable -- 0.63 against 0.056 chance -- but the
curve is steeply concave: averaging over as few as 16 pixels recovers half the
loss. The model was trained on parcel means over a median of 49 core pixels
(within-parcel NDVI sigma 0.0761, so a single pixel is ~7x noisier than
anything it saw in training), and an object of a few dozen pixels puts the
input back in that distribution for free.

The decisive problem is elsewhere, and no amount of accuracy fixes it:
**polygonising a classified raster does not produce field boundaries.** It
produces regions of contiguous same-class pixels. Two adjacent farms growing
rice are one rice region by definition, so in a rice-dominant village the output
is a handful of giant blobs rather than fields. Class identity and land tenure
are different things. Boundaries need a *discontinuity* signal -- a bund, a
road, a difference in sowing date -- which classification does not carry.

Hence: SNIC objects are classified by the existing model. To *look like
fields* they have to split on a discontinuity — a bund, a road, a sowing-date
change — not on a regular seed lattice. Compactness-0 SNIC on true-colour +
NIR + phenology + spatial edges is that signal; compactness 0.5 on NDVI
percentiles alone produced chessboard blocks of a fixed size.

Stages, matching the UI's progress steps one-for-one:

    validating   cropland fraction and size checks, before any imagery is paid for
    extracting   Sentinel-2 composites across the season window
    segmenting   SNIC superpixels -> candidate field objects
    classifying  cycle detection + the tier-1 model, per object
    vectorizing  merge adjacent same-crop objects, filter, emit polygons
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# 10-day compositing, matching CONTINUOUS_SCENE_INTERVAL_DAYS and the grid the
# model was trained on. Changing it changes the feature vector's meaning.
INTERVAL_DAYS = 10
TARGET_SCALE_M = 10

# SNIC seed spacing in 10 m pixels. ~5 → one seed per ~0.25 ha, near the
# default minimum field size. Compactness 0 makes the seed lattice only a
# starting guess: clusters grow along spectral/edge discontinuities (bunds,
# roads, different sowing dates) instead of staying square around each seed.
SNIC_SEED_SPACING = 5
SNIC_COMPACTNESS = 0.1
SNIC_CONNECTIVITY = 8
# Must stay 2× seed size. A large neighbourhood is how GEE SNIC avoids tile
# edges — *too* large and neighbouring tiles emit overlapping cluster ids.
SNIC_NEIGHBORHOOD = 2 * SNIC_SEED_SPACING

MIN_OBS_FOR_DETECTOR = 5
# Objects below this many pixels are dropped before classification: their mean
# is back in the noisy regime the table above measures.
MIN_OBJECT_PIXELS = 12

# ESA WorldCover class 40 = cropland. Used only as a *gate and a statistic*,
# never as a model feature. v100/v200 are ImageCollections (one global mosaic
# per year) — loading the collection id with ee.Image() is accepted client-side
# and only fails later at getInfo with "Asset ... is not an Image".
WORLDCOVER_CROPLAND = 40
WORLDCOVER_ASSET = "ESA/WorldCover/v200"

MAX_AOI_HA = 50_000


class ClassificationError(RuntimeError):
    """Raised when a stage cannot proceed. Message reaches the user verbatim."""


@dataclass
class ValidationCheck:
    id: str
    label: str
    status: str                       # pass | warn | fail
    detail: Optional[str] = None
    value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "label": self.label, "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        if self.value is not None:
            out["value"] = round(float(self.value), 4)
        return out


@dataclass
class ClassifyInputs:
    season: str = "kharif"
    year: int = 2024
    target_crops: List[str] = field(default_factory=list)
    min_field_area_ha: float = 0.2
    confidence_threshold: float = 0.25
    apply_region_guard: bool = True
    apply_season_mask: bool = True
    # Optional label for the run (history, result header, download filenames).
    region_name: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ClassifyInputs":
        d = d or {}
        return cls(
            season=str(d.get("season") or "kharif"),
            year=int(d.get("year") or datetime.now().year),
            target_crops=[str(c) for c in (d.get("target_crops") or [])],
            min_field_area_ha=float(d.get("min_field_area_ha") or 0.2),
            confidence_threshold=float(d.get("confidence_threshold") or 0.25),
            apply_region_guard=bool(d.get("apply_region_guard", True)),
            apply_season_mask=bool(d.get("apply_season_mask", True)),
            region_name=str(d.get("region_name") or "").strip(),
        )


ProgressFn = Callable[[str, Optional[float], Optional[str], Optional[List[Dict[str, Any]]]], None]


def _noop_progress(stage: str, percent: Optional[float] = None,
                   message: Optional[str] = None,
                   checks: Optional[List[Dict[str, Any]]] = None) -> None:
    logger.info("[area_classifier] %s %s %s", stage, percent, message or "")


# =============================================================================
# season windows
# =============================================================================
def season_window(season: str, year: int) -> Tuple[date, date]:
    """
    Observation window for a season.

    Deliberately wider than the agronomic season on both sides: the cycle
    detector needs to see the rise before sowing and the fall after harvest to
    place a cycle at all, and a window clipped to the season itself truncates
    exactly the shoulders it fits against.
    """
    s = (season or "kharif").lower()
    if s == "kharif":
        return date(year, 5, 1), date(year, 12, 15)
    if s == "rabi":
        return date(year, 9, 15), date(year + 1, 5, 15)
    if s == "zaid":
        return date(year, 2, 1), date(year, 8, 15)
    return date(year, 4, 1), date(year + 1, 6, 30)   # whole_year


def _bins_for(d0: date, d1: date) -> List[date]:
    out, cur = [], d0
    while cur <= d1:
        out.append(cur)
        cur = cur + timedelta(days=INTERVAL_DAYS)
    return out


# =============================================================================
# geometry helpers
# =============================================================================
def _ring_area_ha(ring: Sequence[Sequence[float]]) -> float:
    if len(ring) < 4:
        return 0.0
    R = 6378137.0
    total = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = ring[i][0], ring[i][1]
        lon2, lat2 = ring[i + 1][0], ring[i + 1][1]
        total += math.radians(lon2 - lon1) * (
            2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2))
        )
    return abs(total * R * R / 2.0) / 10_000.0


def geometry_area_ha(geom: Dict[str, Any]) -> float:
    t = geom.get("type")
    if t == "Polygon":
        rings = geom.get("coordinates") or []
        return sum(_ring_area_ha(r) * (1 if i == 0 else -1) for i, r in enumerate(rings))
    if t == "MultiPolygon":
        return sum(
            sum(_ring_area_ha(r) * (1 if i == 0 else -1) for i, r in enumerate(poly))
            for poly in (geom.get("coordinates") or [])
        )
    return 0.0


def geometry_centroid(geom: Dict[str, Any]) -> Dict[str, float]:
    t = geom.get("type")
    rings: List[Any] = []
    if t == "Polygon":
        rings = [(geom.get("coordinates") or [[]])[0]]
    elif t == "MultiPolygon":
        rings = [p[0] for p in (geom.get("coordinates") or []) if p]
    lat = lon = 0.0
    n = 0
    for ring in rings:
        for pt in ring[:-1] if len(ring) > 1 else ring:
            lon += pt[0]
            lat += pt[1]
            n += 1
    return {"lat": lat / n, "lng": lon / n} if n else {"lat": 0.0, "lng": 0.0}


# =============================================================================
# Earth Engine
# =============================================================================
def _ee():
    try:
        import ee  # noqa: PLC0415
    except ImportError as exc:                                # pragma: no cover
        raise ClassificationError(
            "earthengine-api is not installed on the service."
        ) from exc
    return ee


def _worldcover_image(ee):
    """WorldCover `Map` band as an Image.

    The catalog id `ESA/WorldCover/v200` is an ImageCollection, not an Image.
    `.mosaic()` is correct for the single global mosaic and still works if ESA
    later splits the product into tiles.
    """
    return ee.ImageCollection(WORLDCOVER_ASSET).mosaic().select("Map")


def _apply_cropland_mask(ee, clusters):
    """Mask SNIC clusters to WorldCover cropland. Returns (image, used_mask)."""
    try:
        wc = _worldcover_image(ee)
        return clusters.updateMask(wc.eq(WORLDCOVER_CROPLAND)), True
    except Exception as exc:                                  # noqa: BLE001
        logger.info("WorldCover mask unavailable (%s); classifying the full AOI",
                    str(exc)[:120])
        return clusters, False


def _absorb_specks(clusters, min_pixels: int):
    """Fold tiny SNIC fragments into the neighbouring cluster.

    Deleting them (or WorldCover-masking before vectorising) punches holes in
    the tessellation. Independent polygon simplify then overlaps the survivors.
    Absorbing specks on the raster keeps a partition: no gaps, no overlays.
    """
    cap = int(min(1023, max(int(min_pixels) + 1, 64)))
    count = clusters.connectedPixelCount(cap, True)
    small = count.lt(int(min_pixels))
    filled = clusters.focalMode(radius=3, kernelType="square", units="pixels", iterations=4)
    return clusters.where(small, filled)


def _vectorise_clusters(ee, clusters, aoi, min_area_m2: float):
    # min_area_m2 is kept for call-site compatibility; specks are already
    # absorbed on the raster so the vectors are a gap-free partition. A second
    # area filter here would reopen holes.
    _ = min_area_m2
    vectors = clusters.reduceToVectors(
        geometry=aoi,
        crs="EPSG:3857",
        scale=TARGET_SCALE_M,
        geometryType="polygon",
        eightConnected=True,
        labelProperty="cluster",
        maxPixels=1e9,
        bestEffort=True,
    )
    return vectors.map(
        lambda f: f.set("area_m2", f.geometry().area(maxError=1))
    ).getInfo()


def _gradient_magnitude(band):
    """Spatial edge strength of a single-band image (bunds, roads, ditches)."""
    g = band.gradient()
    return g.select("x").pow(2).add(g.select("y").pow(2)).sqrt()


def _nonempty_collection(ee, images):
    return (
        ee.ImageCollection(images)
        .map(lambda im: im.set("n_bands", im.bandNames().size()))
        .filter(ee.Filter.gt("n_bands", 0))
    )


def _segmentation_image(ee, images, aoi):
    """True-colour + false-colour NIR + phenology + bund edges, scaled for SNIC.

    Compactness-0 SNIC follows discontinuities in this stack. RGB/NIR show soil
    bunds that NDVI percentiles miss when two neighbours grow the same crop;
    NDVI/EVI/NDMI percentiles split fields sown on different dates.
    """
    stack = _nonempty_collection(ee, images)
    rgb = (
        stack.select(["RED", "GREEN", "BLUE", "NIR"])
        .median()
        .multiply(2.5)
        .clamp(0, 1)
    )
    pheno = (
        stack.select(["NDVI", "EVI", "NDMI"])
        .reduce(ee.Reducer.percentile([15, 50, 85]))
        .clamp(-0.2, 1.0)
    )
    ndvi_med = stack.select("NDVI").median()
    edge = (
        _gradient_magnitude(ndvi_med)
        .max(_gradient_magnitude(rgb.select("NIR")))
        .max(_gradient_magnitude(rgb.select("RED")))
        .multiply(8)
        .clamp(0, 1)
        .rename("EDGE")
    )
    try:
        canny = ee.Algorithms.CannyEdgeDetector(ndvi_med, 0.2, 1.0).rename("CANNY")
        edge = edge.max(canny)
    except Exception:                                        # noqa: BLE001
        logger.info("Canny edges unavailable; SNIC will use gradient magnitude only")
    return (
        ee.Image.cat([rgb, pheno, edge])
        .clip(aoi)
        .reproject(crs="EPSG:3857", scale=TARGET_SCALE_M)
    )


def _aoi_geometry(areas: List[Dict[str, Any]]):
    ee = _ee()
    geoms = []
    for a in areas:
        g = a.get("boundary")
        if not g:
            continue
        geoms.append(ee.Geometry(g, None, False))
    if not geoms:
        raise ClassificationError("No usable boundary geometry in the request.")
    out = geoms[0]
    for g in geoms[1:]:
        out = out.union(g, maxError=1)
    return out


def _s2_index_image(ee, img, cs_band: str, cs_threshold: float):
    """Cloud-masked index stack for one Sentinel-2 scene.

    Formulas are transcribed from satellite_collector._scene_to_feature so a
    segment's trajectory is the same quantity the model was trained on. They
    are duplicated rather than imported because that function builds a
    per-parcel FeatureCollection reduction inline and cannot be called for a
    per-object one.
    """
    qa = img.select("QA60")
    cloud = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    scl = img.select("SCL")
    scl_ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    mask = cloud.And(scl_ok)
    try:
        mask = mask.And(img.select(cs_band).gte(cs_threshold))
    except Exception:                                        # noqa: BLE001
        pass
    m = img.updateMask(mask).divide(10000)

    NIR, RED, BLUE = m.select("B8"), m.select("B4"), m.select("B2")
    GREEN, SWIR1, RE2 = m.select("B3"), m.select("B11"), m.select("B6")

    ndvi = m.normalizedDifference(["B8", "B4"]).rename("NDVI")
    evi = m.expression(
        "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
        {"NIR": NIR, "RED": RED, "BLUE": BLUE},
    ).rename("EVI")
    ndmi = m.normalizedDifference(["B8", "B11"]).rename("NDMI")
    ndwi = m.normalizedDifference(["B3", "B8"]).rename("NDWI")
    ndre = m.normalizedDifference(["B8", "B5"]).rename("NDRE")
    psri = m.expression(
        "(RED - BLUE) / (RE2 + 1e-6)", {"RED": RED, "BLUE": BLUE, "RE2": RE2}
    ).rename("PSRI")
    lswi = m.normalizedDifference(["B8", "B11"]).rename("LSWI")
    msavi2 = m.expression(
        "(2 * NIR + 1 - sqrt((2 * NIR + 1) ** 2 - 8 * (NIR - RED))) / 2",
        {"NIR": NIR, "RED": RED},
    ).rename("MSAVI2")
    nirv = ndvi.multiply(NIR).rename("NIRv")
    ndbi = m.normalizedDifference(["B11", "B8"]).rename("NDBI")
    mndwi = m.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    bsi = m.expression(
        "((SWIR1 + RED) - (NIR + BLUE)) / ((SWIR1 + RED) + (NIR + BLUE) + 1e-6)",
        {"SWIR1": SWIR1, "RED": RED, "NIR": NIR, "BLUE": BLUE},
    ).rename("BSI")
    gcvi = NIR.divide(GREEN.add(1e-6)).subtract(1).rename("GCVI")
    kndvi = ndvi.pow(2).tanh().rename("kNDVI")

    return _ee().Image.cat([
        ndvi, evi, ndmi, ndwi, ndre, psri, lswi, msavi2, nirv,
        ndbi, mndwi, bsi, gcvi, kndvi,
        BLUE.rename("BLUE"), GREEN.rename("GREEN"),
        RED.rename("RED"), NIR.rename("NIR"),
    ])


def _composite_collection(ee, aoi, d0: date, d1: date, cloud_cap: float):
    """One cloud-masked index image per 10-day bin, median-composited."""
    from config import PipelineConfig

    cs_band = str(getattr(PipelineConfig, "CLOUD_SCORE_PLUS_BAND", "cs_cdf"))
    cs_thresh = float(getattr(PipelineConfig, "CLOUD_SCORE_PLUS_THRESHOLD", 0.60))

    base = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(aoi)
        .filterDate(d0.isoformat(), (d1 + timedelta(days=1)).isoformat())
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_cap))
    )
    try:
        cs = ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED")
        base = base.linkCollection(cs, [cs_band])
    except Exception:                                        # noqa: BLE001
        logger.info("Cloud Score+ unavailable; falling back to QA60 + SCL only")

    bins = _bins_for(d0, d1)
    images = []
    kept_bins: List[date] = []
    for b0 in bins:
        b1 = b0 + timedelta(days=INTERVAL_DAYS)
        sub = base.filterDate(b0.isoformat(), b1.isoformat())
        img = (
            ee.ImageCollection(sub.map(lambda i: _s2_index_image(ee, i, cs_band, cs_thresh)))
            .median()
            .set("bin_start", b0.isoformat())
        )
        images.append(img)
        kept_bins.append(b0)
    return images, kept_bins


# =============================================================================
# stage 1 — validation
# =============================================================================
def validate_aoi(areas: List[Dict[str, Any]], inputs: ClassifyInputs) -> List[ValidationCheck]:
    ee = _ee()
    checks: List[ValidationCheck] = []

    total_ha = sum(geometry_area_ha(a.get("boundary") or {}) for a in areas)
    if total_ha <= 0:
        checks.append(ValidationCheck("area_size", "Area has zero extent", "fail"))
        return checks
    if total_ha > MAX_AOI_HA:
        checks.append(ValidationCheck(
            "area_size", "Area within processing limit", "fail",
            f"{total_ha:,.0f} ha exceeds the {MAX_AOI_HA:,} ha ceiling for one job.",
        ))
        return checks
    checks.append(ValidationCheck(
        "area_size", "Area within processing limit", "pass", f"{total_ha:,.0f} ha"
    ))

    aoi = _aoi_geometry(areas)

    # Cropland fraction from ESA WorldCover. A gate, not a feature: it decides
    # whether classifying is worth doing, and later masks non-farmland out of
    # the statistics so a village's built-up core is not reported as fallow.
    try:
        wc = _worldcover_image(ee)
        frac = (
            wc.eq(WORLDCOVER_CROPLAND)
            .reduceRegion(reducer=ee.Reducer.mean(), geometry=aoi,
                          scale=30, maxPixels=1e9, bestEffort=True)
            .get("Map")
        )
        cropland = float(ee.Number(frac).getInfo() or 0.0)
        if cropland < 0.05:
            checks.append(ValidationCheck(
                "cropland", "Agricultural land present", "fail", value=cropland,
                detail="Under 5% of this area is cropland in ESA WorldCover. "
                       "Classification would mostly return non-agricultural.",
            ))
        elif cropland < 0.30:
            checks.append(ValidationCheck(
                "cropland", "Agricultural land present", "warn", value=cropland,
                detail="Mostly non-cropland; expect a large unclassified share.",
            ))
        else:
            checks.append(ValidationCheck(
                "cropland", "Agricultural land present", "pass", value=cropland
            ))
    except Exception as exc:                                  # noqa: BLE001
        checks.append(ValidationCheck(
            "cropland", "Agricultural land present", "warn",
            f"Land-cover check unavailable ({str(exc)[:80]}); proceeding.",
        ))

    # Scene availability. Below MIN_OBS_FOR_DETECTOR usable composites the
    # cycle detector cannot place a cycle and every object would abstain, so it
    # is worth failing here rather than after the extraction bill.
    d0, d1 = season_window(inputs.season, inputs.year)
    try:
        n_scenes = int(
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(d0.isoformat(), d1.isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 80))
            .size()
            .getInfo()
        )
        if n_scenes < MIN_OBS_FOR_DETECTOR:
            checks.append(ValidationCheck(
                "imagery", "Enough usable imagery", "fail",
                f"Only {n_scenes} Sentinel-2 scenes in {d0}–{d1}. "
                f"Need at least {MIN_OBS_FOR_DETECTOR}.",
            ))
        else:
            checks.append(ValidationCheck(
                "imagery", "Enough usable imagery", "pass",
                f"{n_scenes} scenes in {d0}–{d1}",
            ))
    except Exception as exc:                                  # noqa: BLE001
        checks.append(ValidationCheck(
            "imagery", "Enough usable imagery", "warn", str(exc)[:100]
        ))

    return checks


# =============================================================================
# stage 2/3 — segment, then per-object time series
# =============================================================================
def segment_and_extract(
    areas: List[Dict[str, Any]],
    inputs: ClassifyInputs,
    progress: ProgressFn,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """SNIC superpixels + their index trajectories.

    Returns (objects, bin_dates) where each object carries `geometry`,
    `area_ha`, `n_pixels` and `series` -- a per-bin dict of index means in the
    exact `SatelliteDataCollector.INDEX_KEYS` naming the model expects.
    """
    ee = _ee()
    aoi = _aoi_geometry(areas)
    d0, d1 = season_window(inputs.season, inputs.year)

    from config import PipelineConfig
    cloud_cap = float(getattr(PipelineConfig, "MAX_CLOUD_COVER_CONTINUOUS", 70.0))

    progress("extracting", 10.0, f"Building composites for {d0}–{d1}", None)
    images, bins = _composite_collection(ee, aoi, d0, d1, cloud_cap)
    if not images:
        raise ClassificationError("No Sentinel-2 composites could be built for that window.")

    # Segmentation runs on a season summary, not on one date: a single scene
    # splits on transient cloud shadow, while a median true-colour/NIR view
    # plus NDVI percentiles over the window respond to bunds and to how the
    # surface behaves — which is what a field boundary actually is.
    progress("segmenting", 30.0, "Finding field boundaries from imagery", None)
    seg_input = _segmentation_image(ee, images, aoi)
    snic = ee.Algorithms.Image.Segmentation.SNIC(
        image=seg_input,
        size=SNIC_SEED_SPACING,
        compactness=SNIC_COMPACTNESS,
        connectivity=SNIC_CONNECTIVITY,
        neighborhoodSize=SNIC_NEIGHBORHOOD,
    )
    # SeedGrid in a different projection than the 3857 input produced overlapping
    # cluster ids at tile edges. `size=` places seeds in the image's own grid.
    clusters = snic.select("clusters").clip(aoi)

    min_px = max(MIN_OBJECT_PIXELS, int(inputs.min_field_area_ha * 10_000 / (TARGET_SCALE_M ** 2)))
    min_area_m2 = min_px * TARGET_SCALE_M ** 2
    try:
        clusters = _absorb_specks(clusters, min_px)
    except Exception as exc:                                  # noqa: BLE001
        logger.info("Could not absorb SNIC specks (%s); vectorising as-is", str(exc)[:120])

    # Do not WorldCover-mask the raster before vectorising: that punches holes
    # (roads, bunds, mixed pixels) which then look like missing fields. The
    # cropland check already ran at validation; leftover non-farm objects
    # classify as Unclassified / Fallow.

    progress("segmenting", 45.0, "Reading object geometry", None)
    try:
        fc = _vectorise_clusters(ee, clusters, aoi, min_area_m2)
    except Exception as exc:                                  # noqa: BLE001
        raise ClassificationError(
            f"Segmentation failed while vectorising: {str(exc)[:160]}"
        ) from exc

    feats = fc.get("features") or []
    if not feats:
        raise ClassificationError(
            "No field objects survived segmentation. The area may hold no cropland, "
            "or the minimum field size may be too large."
        )

    objects: List[Dict[str, Any]] = []
    for i, f in enumerate(feats):
        geom = f.get("geometry") or {}
        area_ha = geometry_area_ha(geom)
        if area_ha <= 0:
            continue
        objects.append({
            "field_id": i + 1,
            "geometry": geom,
            "area_ha": round(area_ha, 4),
            "n_pixels": int(area_ha * 10_000 / (TARGET_SCALE_M ** 2)),
            "centroid": geometry_centroid(geom),
            "series": {},
        })

    logger.info("[area_classifier] %d objects after segmentation", len(objects))

    # Per-object index means, one reduceRegions per bin. Same shape as the
    # training-side extraction, which is why the resulting trajectories are
    # directly comparable to what the model learned.
    obj_fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry(o["geometry"], None, False), {"field_id": o["field_id"]})
        for o in objects
    ])
    by_id = {o["field_id"]: o for o in objects}
    bin_dates = [b.isoformat() for b in bins]

    for n, (img, b0) in enumerate(zip(images, bins), 1):
        pct = 45.0 + 35.0 * (n / max(len(images), 1))
        progress("extracting", pct, f"Composite {n}/{len(images)} · {b0.isoformat()}", None)
        try:
            reduced = img.reduceRegions(
                collection=obj_fc,
                reducer=ee.Reducer.mean(),
                scale=TARGET_SCALE_M,
                tileScale=4,
            ).getInfo()
        except Exception as exc:                              # noqa: BLE001
            logger.warning("bin %s failed: %s", b0, str(exc)[:120])
            continue

        for f in reduced.get("features") or []:
            props = f.get("properties") or {}
            fid = props.get("field_id")
            obj = by_id.get(fid)
            if obj is None:
                continue
            ndvi = props.get("NDVI")
            if ndvi is None:
                continue
            obj["series"][b0.isoformat()] = {
                f"{k}_mean": props.get(k)
                for k in ("NDVI", "EVI", "NDMI", "NDWI", "NDRE", "PSRI", "LSWI",
                          "MSAVI2", "NIRv", "GCVI", "kNDVI", "NDBI", "MNDWI", "BSI")
            }

    return objects, bin_dates


# =============================================================================
# stage 4 — classify each object
# =============================================================================
def _scenes_from_series(series: Dict[str, Dict[str, Any]],
                        bin_dates: List[str]) -> Tuple[List[Dict[str, Any]], int]:
    """Object series -> the scene list shape the extractor expects.

    A bin with no finite NDVI becomes an explicit `missing` placeholder with a
    full NaN bundle, exactly as satellite_collector's GEE branch does. Zero-
    filling instead would read as bare soil and drag the trajectory down.
    """
    from data_acquisition.satellite_collector import SatelliteDataCollector

    scenes: List[Dict[str, Any]] = []
    n_real = 0
    for b in bin_dates:
        rec = series.get(b)
        ndvi = None if rec is None else rec.get("NDVI_mean")
        ok = False
        if rec is not None and ndvi is not None:
            try:
                ok = bool(np.isfinite(float(ndvi)))
            except (TypeError, ValueError):
                ok = False
        if not ok:
            scenes.append({
                "date": b, "missing": True, "cloud_cover": None,
                "indices": SatelliteDataCollector._nan_index_bundle(),
                "bands_available": [],
            })
            continue
        indices: Dict[str, float] = {}
        for k, v in rec.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                fv = float("nan")
            indices[k] = fv if np.isfinite(fv) else float("nan")
        scenes.append({
            "date": b, "missing": False, "cloud_cover": None,
            "indices": indices,
            "bands_available": ["B02", "B03", "B04", "B05", "B06", "B08", "B11"],
        })
        n_real += 1
    return scenes, n_real


def _cycle_date(cycle: Any, key: str) -> Optional[date]:
    aliases = {
        "start_date": ("start_date", "sowing_date"),
        "end_date": ("end_date", "harvest_date"),
    }
    for k in aliases.get(key, (key,)):
        v = cycle.get(k) if isinstance(cycle, dict) else getattr(cycle, k, None)
        if not v:
            continue
        try:
            return datetime.fromisoformat(str(v)[:10]).date()
        except (TypeError, ValueError):
            continue
    return None


def _pick_cycle(cycles: List[Any], d0: date, d1: date) -> Optional[Any]:
    """The cycle that best fills the requested season.

    Training attributed a cycle by nearness to a survey date. There is no survey
    date here, so the rule is overlap with the season window -- the cycle the
    user actually asked about, rather than the longest one, which for a
    perennial would swallow the season and answer a different question.
    """
    best, best_overlap = None, 0
    for c in cycles or []:
        s = _cycle_date(c, "start_date")
        e = _cycle_date(c, "end_date")
        if s is None or e is None:
            continue
        overlap = (min(e, d1) - max(s, d0)).days
        if overlap > best_overlap:
            best, best_overlap = c, overlap
    return best


def _representative_latlon(objects: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    lats, lons = [], []
    for o in objects:
        c = o.get("centroid") or {}
        if c.get("lat") is None or c.get("lng") is None:
            continue
        try:
            lats.append(float(c["lat"]))
            lons.append(float(c["lng"]))
        except (TypeError, ValueError):
            continue
    if not lats:
        return None, None
    return float(np.median(lats)), float(np.median(lons))


def classify_objects(
    objects: List[Dict[str, Any]],
    bin_dates: List[str],
    inputs: ClassifyInputs,
    progress: ProgressFn,
) -> Dict[str, Any]:
    """Run cycle detection and the tier-1 model over every object."""
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.crop_detector import EXTRACTOR_VERSION, CropDetector
    from config import DEFAULT_CROP_MODEL_PATH, resolve_package_path

    d0, d1 = season_window(inputs.season, inputs.year)
    detector = CropCycleDetector()

    model_path = resolve_package_path(
        os.environ.get("CROP_MODEL_PATH", DEFAULT_CROP_MODEL_PATH)
    )
    lat, lon = _representative_latlon(objects)
    try:
        crop_model = CropDetector(
            crop_model_path=str(model_path),
            latitude=lat,
            longitude=lon,
            verbose=False,
        )
    except Exception as exc:                                  # noqa: BLE001
        raise ClassificationError(
            "Could not load the crop model at %s: %s" % (model_path, str(exc)[:160])
        ) from exc

    allow = {c.strip() for c in inputs.target_crops if c and c.strip()}
    n_total = len(objects)

    # Double-logistic phenology refinement calls scipy.optimize.curve_fit, which
    # on Windows can abort the process inside LAPACK with no Python exception
    # (see CropCycleDetector._fit_double_logistic). Area classification only
    # needs the walked sowing/harvest dates, so skip the fit rather than risk
    # killing the worker — which leaves the job stuck at "classifying" forever.
    os.environ["PHENO_FIT_DISABLE"] = "1"
    cycle_log = logging.getLogger("crop_analysis.crop_cycle_detector")
    prev_cycle_level = cycle_log.level
    cycle_log.setLevel(logging.WARNING)

    try:
        for i, obj in enumerate(objects, 1):
            progress("classifying", 80.0 + 15.0 * ((i - 1) / max(n_total, 1)),
                     "Object %d/%d" % (i, n_total), None)

            scenes, n_real = _scenes_from_series(obj["series"], bin_dates)
            if n_real < MIN_OBS_FOR_DETECTOR:
                obj["crop"] = "Unclassified"
                obj["confidence"] = 0.0
                obj["note"] = "only %d usable observations" % n_real
                continue

            ndvi = np.array([s["indices"].get("NDVI_mean", np.nan) for s in scenes], float)
            evi = np.array([s["indices"].get("EVI_mean", np.nan) for s in scenes], float)
            ndmi = np.array([s["indices"].get("NDMI_mean", np.nan) for s in scenes], float)

            try:
                cycles = detector.detect_cycles(
                    dates=list(bin_dates), ndvi_values=ndvi, evi_values=evi,
                    ndmi_values=ndmi, scenes=scenes, grid_step_days=INTERVAL_DAYS,
                )
            except Exception as exc:                              # noqa: BLE001
                obj["crop"] = "Unclassified"
                obj["confidence"] = 0.0
                obj["note"] = "cycle detection failed: %s" % str(exc)[:80]
                continue

            cycles = [c.to_dict() if hasattr(c, "to_dict") else c for c in (cycles or [])]
            cycle = _pick_cycle(cycles, d0, d1)
            if cycle is None:
                obj["crop"] = "Fallow"
                obj["confidence"] = 0.0
                obj["note"] = "no crop cycle detected in the season window"
                continue

            try:
                pred = crop_model._classify_crop_chronological(scenes, cycle)
            except Exception as exc:                              # noqa: BLE001
                obj["crop"] = "Unclassified"
                obj["confidence"] = 0.0
                obj["note"] = "classifier error: %s" % str(exc)[:80]
                continue

            probs: Dict[str, float] = dict(pred.get("all_probabilities") or {})
            # Restricting to target crops renormalises rather than re-running the
            # model: its opinion is unchanged, the user has only said which answers
            # they are willing to accept.
            if allow:
                probs = {k: v for k, v in probs.items() if k in allow}
                tot = sum(probs.values())
                if tot > 0:
                    probs = {k: v / tot for k, v in probs.items()}

            if probs:
                crop, conf = max(probs.items(), key=lambda kv: kv[1])
            else:
                crop, conf = None, 0.0

            abstained = bool(pred.get("abstained")) or conf < inputs.confidence_threshold
            if abstained or not crop:
                obj["crop"] = "Abstained"
                obj["confidence"] = round(float(conf), 4)
                obj["top_crop_unreliable"] = crop
                obj["note"] = pred.get("abstain_reason") or "below confidence threshold"
            else:
                obj["crop"] = crop
                obj["confidence"] = round(float(conf), 4)

            dur = cycle.get("duration_days") if isinstance(cycle, dict) else getattr(cycle, "duration_days", None)
            if dur:
                obj["cycle_duration_days"] = float(dur)
    finally:
        cycle_log.setLevel(prev_cycle_level)

    progress("classifying", 95.0, "Classified %d objects" % n_total, None)
    return {"objects": objects, "model_version": EXTRACTOR_VERSION}


# =============================================================================
# stage 5 — clean field polygons (absorb specks, dissolve same-crop neighbours)
# =============================================================================
# SNIC over-segments a farm into many small pieces. Independent simplify then
# makes those pieces overlap. Absorbing specks into the neighbour they share
# the longest border with, then dissolving adjacent same-crop parts, keeps a
# partition: one polygon per contiguous crop patch, no double edges, no slivers.

# Fragments below this are too small to be a field even when the user left
# min_field_area at its default 0.2 ha (which would otherwise swallow typical
# 0.1 ha plots). 0.05 ha ≈ 5 Sentinel-2 pixels.
_SPECK_HA = 0.05


def _polygon_parts(geom) -> List[Any]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    t = geom.geom_type
    if t == "Polygon":
        return [geom]
    if t == "MultiPolygon":
        return [g for g in geom.geoms if not g.is_empty]
    if t == "GeometryCollection":
        out: List[Any] = []
        for g in geom.geoms:
            out.extend(_polygon_parts(g))
        return out
    return []


def _as_shapely(geom: Optional[Dict[str, Any]]):
    if not geom:
        return None
    from shapely.geometry import shape
    from shapely.validation import make_valid

    try:
        g = shape(geom)
    except (ValueError, TypeError, AttributeError):
        return None
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = make_valid(g)
    parts = _polygon_parts(g)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    from shapely.ops import unary_union
    return unary_union(parts)


def _to_geojson(geom) -> Optional[Dict[str, Any]]:
    from shapely.geometry import mapping

    if geom is None or geom.is_empty:
        return None
    gj = mapping(geom)
    if gj.get("type") in ("Polygon", "MultiPolygon"):
        return gj
    return None


def _shared_border_length(a, b) -> float:
    try:
        inter = a.boundary.intersection(b.boundary)
        if inter.is_empty:
            return 0.0
        return float(inter.length)
    except Exception:                                         # noqa: BLE001
        return 0.0


def _absorb_speck_into(host: Dict[str, Any], speck: Dict[str, Any]) -> None:
    from shapely.ops import unary_union

    host["_g"] = unary_union([host["_g"], speck["_g"]])
    ha_h = float(host.get("area_ha") or 0.0)
    ha_s = float(speck.get("area_ha") or 0.0)
    if host.get("crop") == speck.get("crop") and (ha_h + ha_s) > 0:
        host["confidence"] = (
            ha_h * float(host.get("confidence") or 0.0)
            + ha_s * float(speck.get("confidence") or 0.0)
        ) / (ha_h + ha_s)
    host["area_ha"] = ha_h + ha_s


def merge_field_objects(
    objects: List[Dict[str, Any]],
    min_area_ha: float = 0.2,
) -> List[Dict[str, Any]]:
    """Turn SNIC pieces into farm-like polygons.

    Tiny slivers join the neighbour they share the longest border with (even
    if that neighbour is a different crop). Remaining adjacent same-crop
    pieces dissolve into one field. Disconnected patches of the same crop
    stay separate.
    """
    from shapely.ops import unary_union

    items: List[Dict[str, Any]] = []
    for o in objects:
        g = _as_shapely(o.get("geometry"))
        if g is None:
            continue
        rec = dict(o)
        rec["_g"] = g
        rec["area_ha"] = float(o.get("area_ha") or 0.0) or geometry_area_ha(
            _to_geojson(g) or {}
        )
        items.append(rec)

    speck_ha = min(_SPECK_HA, max(0.015, float(min_area_ha) * 0.25))

    for _ in range(8):
        if len(items) < 2:
            break
        areas = [
            geometry_area_ha(_to_geojson(it["_g"]) or {}) or float(it.get("area_ha") or 0.0)
            for it in items
        ]
        tiny_idxs = [i for i, a in enumerate(areas) if 0 < a < speck_ha]
        if not tiny_idxs:
            break
        tiny_idxs.sort(key=lambda i: areas[i])
        absorbed: set[int] = set()
        for ti in tiny_idxs:
            if ti in absorbed:
                continue
            best_j: Optional[int] = None
            best_len = 0.0
            best_dist = float("inf")
            for j, other in enumerate(items):
                if j == ti or j in absorbed:
                    continue
                sl = _shared_border_length(items[ti]["_g"], other["_g"])
                if sl > best_len:
                    best_len, best_j = sl, j
                    continue
                if best_len == 0.0:
                    try:
                        d = float(items[ti]["_g"].distance(other["_g"]))
                    except Exception:                         # noqa: BLE001
                        d = float("inf")
                    if d < best_dist:
                        best_dist, best_j = d, j
            if best_j is None:
                continue
            _absorb_speck_into(items[best_j], items[ti])
            absorbed.add(ti)
        if not absorbed:
            break
        items = [it for i, it in enumerate(items) if i not in absorbed]

    by_crop: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by_crop.setdefault(it.get("crop") or "Unclassified", []).append(it)

    merged: List[Dict[str, Any]] = []
    fid = 1
    for crop, group in by_crop.items():
        union = unary_union([it["_g"] for it in group])
        for part in _polygon_parts(union):
            gj = _to_geojson(part)
            if not gj:
                continue
            area = geometry_area_ha(gj)
            if area <= 0:
                continue
            conf_num = conf_den = 0.0
            note = None
            duration = None
            c = part.centroid
            for it in group:
                try:
                    if it["_g"].intersects(part) and not it["_g"].touches(part):
                        w = float(it.get("area_ha") or 0.0)
                    else:
                        w = 0.0
                except Exception:                             # noqa: BLE001
                    w = 0.0
                if w <= 0:
                    continue
                conf_num += w * float(it.get("confidence") or 0.0)
                conf_den += w
                if note is None and it.get("note"):
                    note = it["note"]
                if duration is None and it.get("cycle_duration_days"):
                    duration = it["cycle_duration_days"]
            rec = {
                "field_id": fid,
                "geometry": gj,
                "area_ha": round(area, 4),
                "centroid": {"lat": round(float(c.y), 6), "lng": round(float(c.x), 6)},
                "crop": crop,
                "confidence": round(conf_num / conf_den, 4) if conf_den else 0.0,
            }
            if note:
                rec["note"] = note
            if duration:
                rec["cycle_duration_days"] = duration
            merged.append(rec)
            fid += 1
    return merged


# =============================================================================
# stage 6 — assemble the output layer and the statistics
# =============================================================================
# Kept in step with the frontend's types.ts. Hue carries crop identity, so the
# assignment is fixed rather than index-based: the same crop must be the same
# colour across two runs, or two maps cannot be compared.
CROP_COLORS: Dict[str, str] = {
    "Rice": "#C9A227", "Wheat": "#E0B94A", "Maize": "#F0CE6D",
    "Bajra": "#A8862B", "Jowar": "#8C6F22",
    "Gram": "#B4553A", "Tur": "#8E3F2C",
    "Groundnut": "#D97A34", "Mustard": "#E8A03C", "Soyabean": "#B5652A",
    "Cotton": "#7C5FA8", "Tobacco": "#5D477E",
    "Sugarcane": "#2E7D4F", "Banana": "#3F9E68", "Grapes": "#276145",
    "Onion": "#3E7FA8", "Potato": "#5FA3C4", "Chilli": "#2A5F80",
}
NON_CROP_COLORS: Dict[str, str] = {
    "Non-agricultural": "#9A9287", "Water": "#4A7FA5", "Fallow": "#C4B99F",
    "Unclassified": "#B0A89C", "Abstained": "#8F8779",
}
# Classes that are an answer of "no crop named here", so they are excluded from
# classified area but still reported -- an abstention the user cannot see is
# indistinguishable from a confident call.
NON_CROP_CLASSES = frozenset(
    {"Unclassified", "Abstained", "Fallow", "Non-agricultural", "Water"}
)


def class_color(name: str) -> str:
    return CROP_COLORS.get(name) or NON_CROP_COLORS.get(name) or "#B0A89C"


def build_result(
    objects: List[Dict[str, Any]],
    areas: List[Dict[str, Any]],
    inputs: ClassifyInputs,
    bin_dates: List[str],
    model_version: Optional[str],
) -> Dict[str, Any]:
    total_ha = sum(geometry_area_ha(a.get("boundary") or {}) for a in areas)

    features: List[Dict[str, Any]] = []
    by_crop: Dict[str, Dict[str, float]] = {}

    for o in objects:
        crop = o.get("crop") or "Unclassified"
        conf = float(o.get("confidence") or 0.0)
        area = float(o.get("area_ha") or 0.0)

        agg = by_crop.setdefault(crop, {"field_count": 0.0, "area_ha": 0.0, "conf_sum": 0.0})
        agg["field_count"] += 1
        agg["area_ha"] += area
        agg["conf_sum"] += conf

        props: Dict[str, Any] = {
            "field_id": o["field_id"],
            "crop": crop,
            "confidence": round(conf, 4),
            "area_ha": round(area, 4),
            "centroid": o.get("centroid"),
            "color": class_color(crop),
        }
        if o.get("note"):
            props["note"] = o["note"]
        if o.get("cycle_duration_days"):
            props["cycle_duration_days"] = o["cycle_duration_days"]
        features.append({"type": "Feature", "geometry": o["geometry"], "properties": props})

    classified_ha = sum(v["area_ha"] for k, v in by_crop.items() if k not in NON_CROP_CLASSES)
    unclassified_ha = sum(v["area_ha"] for k, v in by_crop.items() if k in NON_CROP_CLASSES)
    mapped_ha = classified_ha + unclassified_ha

    stats = [
        {
            "crop": crop,
            "field_count": int(v["field_count"]),
            "area_ha": round(v["area_ha"], 3),
            "area_share": round(v["area_ha"] / mapped_ha, 4) if mapped_ha else 0.0,
            "mean_confidence": (
                round(v["conf_sum"] / v["field_count"], 4) if v["field_count"] else 0.0
            ),
        }
        for crop, v in sorted(by_crop.items(), key=lambda kv: -kv[1]["area_ha"])
    ]

    conf_vals = [
        float(o.get("confidence") or 0.0)
        for o in objects
        if (o.get("crop") or "") not in NON_CROP_CLASSES
    ]

    return {
        "aoi_name": (
            (inputs.region_name or "").strip()
            or (areas[0].get("name") if areas else None)
            or "Area of interest"
        ),
        "season": inputs.season,
        "year": inputs.year,
        "total_area_ha": round(total_ha, 2),
        "classified_area_ha": round(classified_ha, 2),
        "unclassified_area_ha": round(unclassified_ha, 2),
        "field_count": len(objects),
        "mean_confidence": round(float(np.mean(conf_vals)), 4) if conf_vals else 0.0,
        "stats": stats,
        "fields": {"type": "FeatureCollection", "features": features},
        "scenes_used": bin_dates,
        "model_version": model_version,
    }


# =============================================================================
# orchestration
# =============================================================================
def run_classification(
    areas: List[Dict[str, Any]],
    inputs_raw: Dict[str, Any],
    progress: Optional[ProgressFn] = None,
) -> Dict[str, Any]:
    """Full pipeline. Raises ClassificationError with a user-facing message."""
    prog = progress or _noop_progress
    inputs = ClassifyInputs.from_dict(inputs_raw)

    prog("validating", 5.0, "Checking the area", None)
    checks = validate_aoi(areas, inputs)
    prog("validating", 15.0, None, [c.to_dict() for c in checks])
    failed = [c for c in checks if c.status == "fail"]
    if failed:
        raise ClassificationError(failed[0].detail or failed[0].label)

    objects, bin_dates = segment_and_extract(areas, inputs, prog)

    prog("classifying", 80.0, "Classifying %d objects" % len(objects), None)
    out = classify_objects(objects, bin_dates, inputs, prog)

    prog("vectorizing", 96.0, "Cleaning field boundaries", None)
    cleaned = merge_field_objects(out["objects"], inputs.min_field_area_ha)
    result = build_result(cleaned, areas, inputs, bin_dates, out.get("model_version"))
    result["validation_checks"] = [c.to_dict() for c in checks]
    prog("complete", 100.0, None, [c.to_dict() for c in checks])
    return result
