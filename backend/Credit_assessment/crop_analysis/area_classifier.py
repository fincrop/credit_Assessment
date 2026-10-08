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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from pathlib import Path

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
    # Field-boundary source: auto | alu | ftw | watershed | snic. See
    # crop_analysis/field_delineation.py for the chain and its benchmark.
    delineation_method: str = "auto"
    # Date the run is made. The observation window never extends past it: a
    # future bin is not an observation, and filling it with the last value
    # invents a flat green tail that reads as a long-season crop.
    as_of: Optional[date] = None
    # First day of imagery to download. The fused feature grid stays anchored
    # on 1 May, so an empty fortnight at the start is a missing step, not a
    # shifted season. None keeps the 1 May download.
    window_start: Optional[date] = None
    # Survey-number plots (WGS84 FeatureCollection). Aligned to the image's own
    # field edges before use (crop_analysis.cadastral_align, plan C1.3).
    cadastral_plots: Optional[Dict[str, Any]] = None
    # Very-high-resolution GeoTIFF the user is licensed to analyse (sharper
    # bunds). Basemap tiles are never mined: their terms do not allow it.
    vhr_imagery_path: Optional[str] = None

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
            delineation_method=str(d.get("delineation_method") or
                                   os.environ.get("DELINEATION_METHOD") or "auto").lower(),
            as_of=_parse_day(d.get("as_of")),
            window_start=_parse_day(d.get("window_start")),
            cadastral_plots=d.get("cadastral_plots") or None,
            vhr_imagery_path=d.get("vhr_imagery_path") or os.environ.get("VHR_IMAGERY_PATH") or None,
        )


def _parse_day(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


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


def observation_window(inputs: "ClassifyInputs") -> Tuple[date, date]:
    """Season window clipped to the run date.

    Imagery after `as_of` does not exist yet. The season window is a request;
    this is what can actually be observed.
    """
    d0, d1 = season_window(inputs.season, inputs.year)
    as_of = inputs.as_of or date.today()
    return d0, min(d1, as_of)


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

    d0, d1 = observation_window(inputs)
    if d1 <= d0:
        checks.append(ValidationCheck(
            "window", "Season has started", "fail",
            f"The {inputs.season} {inputs.year} window starts {d0}; nothing can be "
            f"observed before then.",
        ))
        return checks

    # Mid-season run (plan B4). Measured on Dhaswadi, 2 Oct 2026: with only real
    # observations inside each cycle, 1,155 fields had 2-4 clear looks in a cycle
    # still in progress, below the 5 the model was trained on. Say so up front.
    _, season_end = season_window(inputs.season, inputs.year)
    if d1 < season_end:
        checks.append(ValidationCheck(
            "season_progress", "Season complete", "warn",
            f"Observations end {d1} but {inputs.season} cycles run to {season_end}. A crop named "
            f"from a cycle that is still open is 'provisional'. Cloudy gaps are filled for "
            f"cycle detection; a field is left 'Insufficient data' only when it was barely seen.",
        ))

    # A crop list narrows which names are printed. Other crops stay visible as
    # Others, with the model's own name kept for the hover, and are never
    # relabelled as the crop the user asked for.
    allow = sorted({c.strip() for c in inputs.target_crops if c and c.strip()})
    if len(allow) == 1:
        checks.append(ValidationCheck(
            "crop_filter", "Crop list covers the area's crops", "warn",
            f"Only {allow[0]} was selected. Every other crop the model names is "
            f"reported as 'Others', with the model's crop shown on hover, "
            f"not relabelled {allow[0]}.",
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
def _snic_objects(ee, images, aoi, inputs: "ClassifyInputs", progress) -> List[Dict[str, Any]]:
    """Legacy SNIC segmentation — the last-resort boundary source."""
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

    for o in objects:
        o["boundary_source"] = "snic"
    return objects


def _areas_geojson(areas: List[Dict[str, Any]]) -> Dict[str, Any]:
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union
    geoms = [shape(a["boundary"]) for a in areas if a.get("boundary")]
    if not geoms:
        raise ClassificationError("No usable boundary geometry in the request.")
    return mapping(unary_union(geoms))


def _delineated_objects(areas, inputs: "ClassifyInputs", progress) -> Optional[List[Dict[str, Any]]]:
    """Field polygons from the delineation chain; None -> caller falls back to SNIC."""
    from crop_analysis.field_delineation import DelineationError, delineate

    def _p(method: str) -> None:
        progress("segmenting", 30.0, "Delineating field boundaries (%s)" % method, None)

    try:
        from crop_analysis.field_delineation import kharif_months
        months = (kharif_months(inputs.year, inputs.as_of or date.today())
                  if (inputs.season or "kharif").lower() == "kharif" else None)
        res = delineate(_areas_geojson(areas), year=inputs.year,
                        method=inputs.delineation_method,
                        # Season-profile merging is off by default: on the
                        # Marathwada parcels it lowered median IoU 0.177 -> 0.132
                        # (fields already ~5x the surveyed parcel area).
                        season_months=months,
                        use_profiles=bool(months) and os.environ.get(
                            "DELINEATION_PROFILE_MERGE", "0") == "1",
                        vhr_path=inputs.vhr_imagery_path,
                        min_field_ha=max(0.03, min(inputs.min_field_area_ha, 0.1)),
                        progress=_p)
    except DelineationError as exc:
        logger.warning("[area_classifier] delineation unavailable (%s); using SNIC",
                       str(exc)[:200])
        return None
    objects = []
    for i, f in enumerate(res.fields, 1):
        geom = f["geometry"]
        area_ha = geometry_area_ha(geom)
        if area_ha <= 0:
            continue
        objects.append({
            "field_id": i,
            "geometry": geom,
            "area_ha": round(area_ha, 4),
            "n_pixels": int(area_ha * 10_000 / (TARGET_SCALE_M ** 2)),
            "centroid": geometry_centroid(geom),
            "series": {},
            "boundary_source": res.method,
            "boundary_confidence": (f.get("properties") or {}).get("confidence"),
        })
    logger.info("[area_classifier] %d fields from %s delineation", len(objects), res.method)
    if objects and inputs.cadastral_plots:
        objects = _apply_cadastral(areas, inputs, objects, progress)
    return objects or None


# Report of the last cadastral alignment, attached to the result by run_classification.
_ALIGNMENT_REPORT: Dict[str, Any] = {}


def _apply_cadastral(areas, inputs: "ClassifyInputs", objects, progress):
    """Align survey plots to this AOI's edge map, then split segments on them.

    A village whose alignment fails the gate keeps image-delineated boundaries;
    the report says why (plan C1.3).
    """
    from crop_analysis.cadastral_align import EdgeGrid, align_plots, constrain_segments
    from crop_analysis.field_delineation import combined_edge, fetch_boundary_arrays

    _ALIGNMENT_REPORT.clear()
    progress("segmenting", 40.0, "Aligning survey-number plots to field edges", None)
    try:
        ba = fetch_boundary_arrays(_areas_geojson(areas), year=inputs.year)
        grid = EdgeGrid(combined_edge(ba), ba.x0, ba.y1, ba.epsg)
        aligned, report = align_plots(inputs.cadastral_plots, grid)
    except Exception as exc:                                  # noqa: BLE001
        _ALIGNMENT_REPORT.update({"passed": False, "reason": f"alignment failed: {str(exc)[:160]}"})
        return objects
    _ALIGNMENT_REPORT.update(report)
    if not report.get("passed"):
        return objects
    feats = [{"type": "Feature", "geometry": o["geometry"],
              "properties": {k: v for k, v in o.items() if k != "geometry"}} for o in objects]
    split = constrain_segments(feats, aligned, min_area_ha=max(0.03, inputs.min_field_area_ha * 0.25),
                               epsg=ba.epsg)
    out = []
    for i, f in enumerate(split, 1):
        props = dict(f.get("properties") or {})
        props.update({
            "field_id": i, "geometry": f["geometry"],
            "area_ha": round(geometry_area_ha(f["geometry"]), 4),
            "centroid": geometry_centroid(f["geometry"]), "series": {},
        })
        props["n_pixels"] = int(props["area_ha"] * 10_000 / (TARGET_SCALE_M ** 2))
        out.append(props)
    logger.info("[area_classifier] cadastral constraint: %d -> %d fields", len(objects), len(out))
    return out


def segment_and_extract(
    areas: List[Dict[str, Any]],
    inputs: ClassifyInputs,
    progress: ProgressFn,
    extract_series: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """SNIC superpixels + their index trajectories.

    Returns (objects, bin_dates) where each object carries `geometry`,
    `area_ha`, `n_pixels` and `series` -- a per-bin dict of index means in the
    exact `SatelliteDataCollector.INDEX_KEYS` naming the model expects.
    """
    ee = _ee()
    aoi = _aoi_geometry(areas)
    d0, d1 = observation_window(inputs)

    from config import PipelineConfig
    cloud_cap = float(getattr(PipelineConfig, "MAX_CLOUD_COVER_CONTINUOUS", 70.0))

    images, bins = None, []

    def _images():
        nonlocal images, bins
        if images is None:
            progress("extracting", 10.0, f"Building composites for {d0}–{d1}", None)
            images, bins = _composite_collection(ee, aoi, d0, d1, cloud_cap)
            if not images:
                raise ClassificationError("No Sentinel-2 composites could be built for that window.")
        return images

    # Segmentation runs on a season summary, not on one date: a single scene
    # splits on transient cloud shadow, while a median true-colour/NIR view
    # plus NDVI percentiles over the window respond to bunds and to how the
    # surface behaves — which is what a field boundary actually is.
    objects = None
    if inputs.delineation_method != "snic":
        objects = _delineated_objects(areas, inputs, progress)
    if objects is None:
        objects = _snic_objects(ee, _images(), aoi, inputs, progress)

    logger.info("[area_classifier] %d objects after segmentation", len(objects))
    if not extract_series:
        # The fused classifier reads Sentinel-1 + reflectance itself.
        return objects, []
    _images()

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


# Classes that answer "no crop named here". "Others" is a crop the model named
# that is not printed as that crop: it was not requested, or the call was too
# weak to stand as the class. The model's own name stays on `model_top_crop`
# for the hover. "Insufficient data" is a field that was barely seen, which
# must never be shown as Fallow.
OTHERS = "Others"
NOT_REQUESTED = OTHERS
NO_DATA = "Insufficient data"

# Cycle scenes the trainer requires before it builds features
# (Crop_classification_model/src/features.py). Fewer and the row never existed
# in training, so the model has no basis for it.
MIN_CYCLE_SCENES = 5

# A bare field stays below this NDVI on every clear look of the season.
BARE_MAX_NDVI = 0.30
# Below this many clear looks in the window, "no cycle" means "not seen".
MIN_OBS_FOR_ABSENCE = 8
# Longest gap between clear looks that still lets us call a field bare.
MAX_GAP_FOR_ABSENCE_DAYS = 45

# Soybean and tur are sown together in strips across Marathwada. At 10 m the
# two mix inside one parcel, so a close soybean/tur split is reported as the
# intercrop rather than forced onto one of them.
INTERCROPS = {frozenset({"Soyabean", "Tur"}): "Soyabean+Tur"}
INTERCROP_MIN_JOINT = 0.60
INTERCROP_MAX_MARGIN = 0.10

# Deccan kharif (Latur, Beed, the frozen Marathwada test) grows Cotton,
# Soyabean and Tur. Rice and Onion are printed only when they lead those
# three by this margin; a narrower lead is shown as Others and the model's
# lean stays on model_top_crop. On fused v3, margin 0.25 cleared every
# cotton field called Onion on the frozen test (3 at 1 Oct, 1 full season)
# and hid 1 of 565 Onion training rows.
DECCAN_KHARIF_LOCAL = ("Cotton", "Soyabean", "Tur")
DECCAN_KHARIF_HOLD = ("Onion", "Rice")
DECCAN_KHARIF_HOLD_MARGIN = 0.25

# Export keys carried from the classifier to every field polygon (B2).
FIELD_EXPORT_KEYS = (
    "status", "model_top_crop", "top2_crop", "p_top1", "p_top2", "margin",
    "abstain_reason", "n_obs_cycle", "cycle_complete", "region_support",
    "season_consistent", "cycle_sowing", "cycle_peak", "cycle_harvest",
    "ecoregion", "survey_no", "alignment_residual_m", "boundary_confidence",
    "possible_crop", "n_obs_optical", "n_obs_radar", "frac_imputed",
)


def _cycle_value(cycle: Any, key: str) -> Any:
    return cycle.get(key) if isinstance(cycle, dict) else getattr(cycle, key, None)


def cycle_scenes(scenes: List[Dict[str, Any]], cycle: Any) -> List[Dict[str, Any]]:
    """Real observations inside the cycle, padded exactly as training pads.

    Training (`features._slice_scenes`) and the credit path
    (`CropDetector._collect_scenes_between`) both build features from real
    scenes inside [sowing - pad, harvest + pad]. Passing the whole season with
    missing placeholders stretched the 15-step grid over months the model never
    saw and counted empty bins as observations.
    """
    from crop_analysis.crop_detector import collect_scenes_between, cycle_scene_date_bounds

    s = _cycle_date(cycle, "start_date")
    e = _cycle_date(cycle, "end_date")
    if s is None or e is None:
        return []
    lo, hi = cycle_scene_date_bounds(s.isoformat(), e.isoformat())
    return collect_scenes_between(scenes, lo, hi)


def cycle_is_complete(cycle: Any, scenes: List[Dict[str, Any]], as_of: date) -> bool:
    """True when the harvest drop was actually observed before the run date.

    A cycle whose end the detector extrapolated (no clear look on or after the
    harvest date) is still in progress. Its length, and every feature derived
    from it, is provisional.
    """
    harvest = _cycle_date(cycle, "end_date")
    if harvest is None or harvest > as_of:
        return False
    for sc in scenes:
        if sc.get("missing"):
            continue
        d = str(sc.get("date") or "")[:10]
        if d and d >= harvest.isoformat():
            return True
    return False


def fill_short_gaps(values: np.ndarray, max_gap: int = 2) -> np.ndarray:
    """Linearly fill NaN runs of at most `max_gap` samples.

    Used for cycle detection only. A one- or two-composite monsoon hole should
    not erase a crop curve. Longer holes stay empty so a missing month is not
    invented, and the classifier still sees only real clear scenes.
    """
    v = np.array(values, dtype=float).copy()
    n = len(v)
    i = 0
    while i < n:
        if np.isfinite(v[i]):
            i += 1
            continue
        j = i
        while j < n and not np.isfinite(v[j]):
            j += 1
        left = v[i - 1] if i > 0 and np.isfinite(v[i - 1]) else None
        right = v[j] if j < n and np.isfinite(v[j]) else None
        if (j - i) <= max_gap and left is not None and right is not None:
            v[i:j] = np.linspace(left, right, (j - i) + 2)[1:-1]
        i = j
    return v


def classify_without_cycle(scenes: List[Dict[str, Any]]) -> Tuple[str, str]:
    """Name a field the cycle detector found no crop on (B7).

    "No cycle" used to mean Fallow. It mixes three different answers: bare all
    season, green but not shaped like an annual crop, and simply not seen
    through the monsoon. Only the first is Fallow. Green that we could see,
    even through a cloudy gap, is Others — a crop was there and is not named.
    """
    real = [sc for sc in scenes if not sc.get("missing")]
    dates = sorted(str(sc.get("date"))[:10] for sc in real)
    gap = 0
    for a, b in zip(dates, dates[1:]):
        gap = max(gap, (date.fromisoformat(b) - date.fromisoformat(a)).days)
    ndvi = [float(sc["indices"].get("NDVI_mean", np.nan)) for sc in real]
    ndvi = [v for v in ndvi if np.isfinite(v)]
    peak = max(ndvi) if ndvi else 0.0
    if len(real) < MIN_OBS_FOR_ABSENCE or gap > MAX_GAP_FOR_ABSENCE_DAYS:
        if peak >= BARE_MAX_NDVI:
            return OTHERS, (
                f"canopy was visible (peak NDVI {peak:.2f}) over {len(real)} clear looks, "
                f"longest gap {gap} days — not named"
            )
        return NO_DATA, (
            f"{len(real)} clear observations, longest gap {gap} days: too few to "
            f"say the field was not cropped"
        )
    if peak < BARE_MAX_NDVI:
        return "Fallow", f"bare all season: peak NDVI {peak:.2f} over {len(real)} clear observations"
    return OTHERS, (
        f"green (peak NDVI {peak:.2f}) but no annual crop cycle: possible perennial, "
        f"trees, grass, or a cycle outside the season"
    )


def apply_reference_crop(obj: Dict[str, Any], scenes: List[Dict[str, Any]], as_of: date,
                          season: Optional[str]) -> None:
    """Name Cotton or Soyabean from the field's NDVI shape when the model did not.

    Runs only on a printed Others. Fallow and a field that was barely seen stay
    as they are. The model's own lean is left on model_top_crop. The confidence
    is the curve separation, not the probability of Onion or Banana.
    """
    if (season or "").lower() != "kharif" or obj.get("crop") != OTHERS:
        return
    try:
        import sys
        from pathlib import Path

        package = Path(__file__).resolve().parents[3] / "Crop_Monitoring"
        if str(package) not in sys.path:
            sys.path.insert(0, str(package))
        from src.raster.reference import name_from_looks
    except Exception:  # noqa: BLE001
        return
    dates, values = [], []
    for sc in scenes:
        if sc.get("missing"):
            continue
        raw = str(sc.get("date") or "")[:10]
        ndvi = (sc.get("indices") or {}).get("NDVI_mean")
        if len(raw) < 10 or ndvi is None:
            continue
        try:
            dates.append(date.fromisoformat(raw))
            values.append(float(ndvi))
        except (TypeError, ValueError):
            continue
    named = name_from_looks(dates, values, as_of)
    if not named:
        return
    obj["crop"] = named["crop"]
    obj["confidence"] = named["confidence"]
    obj["status"] = "reference"
    obj["note"] = named["note"]


def _deccan_kharif_hold(
    top1: Optional[str],
    probs: Mapping[str, float],
    season: Optional[str],
    ecoregion: Optional[str],
) -> Optional[str]:
    """Hold Rice and Onion on the Deccan in kharif unless they lead the local crops."""
    if (season or "").lower() != "kharif" or ecoregion != "DECCAN_PLATEAU":
        return None
    if top1 not in DECCAN_KHARIF_HOLD:
        return None
    local = max(float(probs.get(c) or 0.0) for c in DECCAN_KHARIF_LOCAL)
    gap = float(probs.get(top1) or 0.0) - local
    if gap >= DECCAN_KHARIF_HOLD_MARGIN:
        return None
    return (
        f"{top1} leads Cotton, Soyabean and Tur by {gap:.2f}, "
        f"under {DECCAN_KHARIF_HOLD_MARGIN:.2f}; shown as Others"
    )


def decide_crop(
    pred: Dict[str, Any],
    cycle: Any,
    *,
    allow: Sequence[str] = (),
    confidence_threshold: float = 0.25,
    season: Optional[str] = None,
    ecoregion: Optional[str] = None,
    support: Optional[Dict] = None,
    apply_region_guard: bool = True,
    apply_season_mask: bool = True,
    cycle_complete: bool = True,
) -> Dict[str, Any]:
    """One field's crop decision from the model's full 18-class answer.

    The model's probabilities are never renormalised over a user's crop list:
    that turned "80% soybean" into "Cotton 1.0" when only cotton was requested.
    The argmax is never moved either. The region guard and the season check
    only lower the trust in it, and the abstain rule acts on that trust.
    """
    from crop_analysis.crop_calendar import season_consistent
    from crop_analysis.region_guard import tier

    probs = {k: float(v) for k, v in (pred.get("all_probabilities") or {}).items()}
    ranked = sorted(probs.items(), key=lambda kv: -kv[1])
    top1, p1 = ranked[0] if ranked else (None, 0.0)
    top2, p2 = ranked[1] if len(ranked) > 1 else (None, 0.0)
    margin = p1 - p2

    out: Dict[str, Any] = {
        "model_top_crop": top1,
        "top2_crop": top2,
        "p_top1": round(p1, 4),
        "p_top2": round(p2, 4),
        "margin": round(margin, 4),
        "cycle_complete": bool(cycle_complete),
    }

    trust = p1
    reasons: List[str] = []
    if top1 and apply_region_guard and ecoregion:
        t, scale = tier(support or {}, top1, ecoregion)
        out["region_support"] = t
        if scale < 1.0:
            trust *= scale
            reasons.append(f"{top1} has {t} training support in {ecoregion}")
    peak = _cycle_date(cycle, "peak_date") if cycle is not None else None
    if top1 and apply_season_mask and peak is not None:
        ok = season_consistent(top1, peak, season)
        out["season_consistent"] = ok
        if ok is False:
            trust *= 0.15
            reasons.append(
                f"a cycle peaking {peak.isoformat()} is outside {top1}'s calendar"
                + (f" for {season}" if season else "")
            )

    intercrop = INTERCROPS.get(frozenset({top1, top2})) if top1 and top2 else None
    model_abstain = pred.get("abstain_reason") if pred.get("abstained") else None
    # "n_scenes N < 8" means the optical record is short. The probabilities are
    # still the model's answer; they are shown, and marked provisional.
    scene_limited = bool(model_abstain and str(model_abstain).startswith("n_scenes"))

    if (intercrop and margin < INTERCROP_MAX_MARGIN and p1 + p2 >= INTERCROP_MIN_JOINT
            and not reasons and not model_abstain):
        crop, conf, status = intercrop, p1 + p2, "intercrop"
    elif not top1:
        crop, conf, status = OTHERS, trust, "abstained"
        reasons.insert(0, model_abstain or "no class probability")
    elif reasons or (model_abstain and not scene_limited) or trust < confidence_threshold:
        # A name exists (model_top_crop) but it is not reliable enough, or not
        # the crop that will be printed. The map class is Others; hover shows
        # the name. Region and season guards never become the printed class.
        crop, conf, status = OTHERS, trust, "abstained"
        reasons.insert(0, model_abstain or f"confidence {trust:.2f} < {confidence_threshold:.2f}")
    else:
        crop, conf, status = top1, trust, "confirmed" if cycle_complete else "provisional"
        if scene_limited:
            status = "provisional"
            reasons.append(str(model_abstain))

    hold = _deccan_kharif_hold(top1, probs, season, ecoregion)
    if hold and status in ("confirmed", "provisional"):
        crop, status = OTHERS, "not_requested"
        reasons.append(hold)

    allowed = {c for c in allow if c}
    if allowed and status in ("confirmed", "provisional", "intercrop"):
        named = set(crop.split("+")) if status == "intercrop" else {crop}
        if not named & allowed:
            crop, status = OTHERS, "not_requested"
            reasons.append(f"model names {out['model_top_crop']}, which was not requested")

    out.update({
        "crop": crop,
        "confidence": round(float(conf), 4),
        "status": status,
    })
    if reasons:
        out["abstain_reason" if status == "abstained" else "note"] = "; ".join(reasons)
    if status == "abstained":
        out["note"] = out["abstain_reason"]
    return out


def model_provenance(model_path: Any, crop_model: Any) -> Dict[str, Any]:
    """Which model actually ran (B6). The extractor version alone is not that."""
    import hashlib
    from pathlib import Path

    from crop_analysis.crop_detector import EXTRACTOR_VERSION

    p = Path(str(model_path))
    digest = None
    try:
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        pass
    return {
        "name": p.stem,
        "path": p.name,
        "sha256": digest,
        "extractor_version": EXTRACTOR_VERSION,
        "classes": list(getattr(crop_model, "crop_names", []) or []),
        "extra_blocks": list(getattr(crop_model, "extra_blocks", []) or []),
    }


TIER2_CHUNK = 150


def _prefetch_tier2(objects, crop_model, d0: date, d1: date, lat, lon, progress):
    """S1 / reflectance for every object (one reduceRegions per chunk over the
    season window) and one weather series for the AOI. Embeddings are fetched
    lazily per year in `_with_embedding`, since the year depends on the cycle.
    Returns {field_id: extra-inputs dict}; empty when the bundle is tier-0/1."""
    blocks = list(getattr(crop_model, "extra_blocks", []) or [])
    if not blocks:
        return {}
    from crop_analysis.extra_features import PAD_DAYS
    from data_acquisition import extra_sources as xs

    ee = _ee()
    lo, hi = d0 - timedelta(days=PAD_DAYS), d1 + timedelta(days=PAD_DAYS)
    out: Dict[int, Dict[str, Any]] = {o["field_id"]: {"missing": []} for o in objects}
    if set(blocks) & {"s1", "refl"}:
        for k in range(0, len(objects), TIER2_CHUNK):
            chunk = objects[k:k + TIER2_CHUNK]
            progress("classifying", 80.0, "Radar & reflectance %d/%d" % (k + len(chunk), len(objects)), None)
            try:
                res = xs.fetch_time_series(
                    ee, [(str(o["field_id"]), o["geometry"]) for o in chunk], lo, hi, blocks)
            except Exception as exc:                          # noqa: BLE001
                logger.warning("tier-2 series chunk failed: %s", str(exc)[:160])
                res = {}
            for o in chunk:
                r = res.get(str(o["field_id"]), {})
                e = out[o["field_id"]]
                e["s1_series"], e["refl_series"] = r.get("s1"), r.get("refl")
                e["missing"] += [b for b in ("s1", "refl") if b in blocks and not r.get(b)]
    if "emb" in blocks:
        # A season window spans at most two calendar years; batch both so the
        # per-object lookup in _with_embedding is a cache hit.
        for y in sorted({d0.year, d1.year}):
            for k in range(0, len(objects), TIER2_CHUNK * 3):
                chunk = objects[k:k + TIER2_CHUNK * 3]
                try:
                    res = xs.fetch_embeddings(
                        ee, [(str(o["field_id"]), o["geometry"]) for o in chunk], y)
                except Exception as exc:                      # noqa: BLE001
                    logger.warning("embedding prefetch %s failed: %s", y, str(exc)[:120])
                    continue
                for o in chunk:
                    _EMB_CACHE[(o["field_id"], y)] = res.get(str(o["field_id"]))
    if "weather" in blocks and lat is not None and lon is not None:
        try:
            daily = xs.fetch_weather_for_cycles(lat, lon, lo, hi)
        except Exception as exc:                              # noqa: BLE001
            logger.warning("tier-2 weather failed: %s", str(exc)[:160])
            daily = None
        for e in out.values():
            e["weather_daily"] = daily
            if daily is None:
                e["missing"].append("weather")
    return out


_EMB_CACHE: Dict[Tuple[int, int], Optional[List[float]]] = {}


def _with_embedding(extra, obj, cycle, crop_model):
    from crop_analysis.extra_features import embedding_year
    from data_acquisition.extra_sources import fetch_embeddings

    g = cycle.get if isinstance(cycle, dict) else (lambda k: getattr(cycle, k, None))
    y = embedding_year(g("sowing_date") or g("start_date"),
                       g("harvest_date") or g("end_date"), g("peak_date"))
    key = (obj["field_id"], y)
    if key not in _EMB_CACHE:
        try:
            _EMB_CACHE[key] = fetch_embeddings(_ee(), [(str(obj["field_id"]), obj["geometry"])], y).get(
                str(obj["field_id"]))
        except Exception:                                     # noqa: BLE001
            _EMB_CACHE[key] = None
    e = dict(extra)
    e["embedding"] = _EMB_CACHE[key]
    if e["embedding"] is None:
        e["missing"] = list(e.get("missing", [])) + ["emb"]
    return e


def area_model_path():
    """Model for area classification.

    AREA_CROP_MODEL_PATH if set. Otherwise fused v3, the strongest bundle on
    the frozen Marathwada test (full-season cotton 0.962, soybean 0.958).
    The optical tier-1 model remains the fallback when that file is absent.
    """
    from config import DEFAULT_CROP_MODEL_PATH, REPO_ROOT, resolve_package_path

    env = os.environ.get("AREA_CROP_MODEL_PATH")
    if env:
        return resolve_package_path(env)
    fused = REPO_ROOT / "Crop_classification_model" / "models" / "crop_classifier_fused_v3.joblib"
    if fused.is_file():
        return fused
    return resolve_package_path(os.environ.get("CROP_MODEL_PATH", DEFAULT_CROP_MODEL_PATH))


def classify_objects_fused(
    objects: List[Dict[str, Any]],
    inputs: ClassifyInputs,
    progress: ProgressFn,
    model_path: Any,
) -> Dict[str, Any]:
    """Cloud-robust path: Sentinel-1 + optical fused per field, no cycle detection.

    A field is only "Insufficient data" when neither radar nor optical saw it
    at all. Thin evidence lowers the model's confidence instead (the decision
    rules then route it to Others with the model's lean on hover).
    """
    from crop_analysis.fused_classifier import FusedClassifier, fetch_series, is_fallow
    from crop_analysis.fused_features import has_any_data, peak_date
    from crop_analysis.region_guard import ecoregion_for, load_support

    d0, d1 = observation_window(inputs)
    as_of = inputs.as_of or date.today()
    clf = FusedClassifier(model_path)
    allow = {c.strip() for c in inputs.target_crops if c and c.strip()}
    own = Path(str(model_path)).with_suffix(".region_support.json")
    support = load_support(str(own) if own.exists() else None) if inputs.apply_region_guard else {}
    year = inputs.year
    lo = inputs.window_start or date(year, 5, 1)
    hi = min(date(year, 12, 31), as_of)
    if lo > hi:
        lo = hi

    def _p(done, total):
        progress("classifying", 80.0 + 8.0 * done / max(total, 1),
                 "Radar + optical series %d/%d" % (done, total), None)

    series = fetch_series(_ee(), objects, lo, hi, _p)
    n_total = len(objects)
    season_complete = as_of >= date(year, 12, 15)
    for i, obj in enumerate(objects, 1):
        if i % 200 == 0:
            progress("classifying", 88.0 + 7.0 * i / max(n_total, 1), "Object %d/%d" % (i, n_total), None)
        blk = series.get(str(obj["field_id"])) or {}
        feats, cur = clf.features(blk.get("s1") or [], blk.get("refl") or [], year, hi)
        obj["n_obs_optical"] = int(feats.get("n_opt") or 0)
        obj["n_obs_radar"] = int(feats.get("n_sar") or 0)
        obj["frac_imputed"] = round(float(feats.get("frac_imputed") or 0.0), 3)
        if not has_any_data(feats):
            obj.update(crop=NO_DATA, confidence=0.0, status="no_data",
                       note="no Sentinel-1 or Sentinel-2 observation of this field")
            continue
        if is_fallow(feats):
            obj.update(crop="Fallow", confidence=0.0, status="no_cycle",
                       note=f"bare all season: peak NDVI {feats['ndvi_max']:.2f} over "
                            f"{obj['n_obs_optical']} clear optical looks")
            continue
        pred = clf.predict(feats)
        pk = peak_date(feats, year)
        c = obj.get("centroid") or {}
        eco = ecoregion_for(c.get("lat"), c.get("lng")) if c else None
        obj["ecoregion"] = eco
        obj["cycle_peak"] = pk.isoformat() if pk else None
        obj.update(decide_crop(
            pred, {"peak_date": pk.isoformat()} if pk else None,
            allow=sorted(allow), confidence_threshold=inputs.confidence_threshold,
            season=inputs.season, ecoregion=eco, support=support,
            apply_region_guard=inputs.apply_region_guard,
            apply_season_mask=inputs.apply_season_mask,
            cycle_complete=season_complete,
        ))
        _possible_requested(obj, allow)
    progress("classifying", 95.0, "Classified %d objects" % n_total, None)
    prov = clf.provenance()
    return {
        "objects": objects,
        "model_version": prov["name"],
        "model": prov,
        "window": {"start": lo.isoformat(), "end": min(d1, hi).isoformat(), "as_of": as_of.isoformat()},
    }


def _possible_requested(obj: Dict[str, Any], allow: set) -> None:
    """An uncertain field whose best guess IS a requested crop stays Others on
    the map, but is flagged so the summary can report it separately instead
    of silently dropping it from that crop's area."""
    if obj.get("crop") == OTHERS and obj.get("status") == "abstained" and allow \
            and obj.get("model_top_crop") in allow:
        obj["possible_crop"] = obj["model_top_crop"]
        obj["note"] = (f"possible {obj['model_top_crop']} (uncertain: p {obj.get('p_top1')}, "
                       f"next {obj.get('top2_crop')} {obj.get('p_top2')})")


def classify_objects(
    objects: List[Dict[str, Any]],
    bin_dates: List[str],
    inputs: ClassifyInputs,
    progress: ProgressFn,
) -> Dict[str, Any]:
    """Run cycle detection and the tier-1 model over every object."""
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.crop_detector import CropDetector
    from config import DEFAULT_CROP_MODEL_PATH, resolve_package_path

    from crop_analysis.region_guard import ecoregion_for, load_support

    d0, d1 = observation_window(inputs)
    as_of = inputs.as_of or date.today()
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
    # A model may ship its own training-support table next to it
    # (<model>.region_support.json); otherwise the shared table is used.
    from pathlib import Path

    own_support = Path(str(model_path)).with_suffix(".region_support.json")
    support = (load_support(str(own_support) if own_support.exists() else None)
               if inputs.apply_region_guard else {})
    provenance = model_provenance(model_path, crop_model)

    _EMB_CACHE.clear()
    extra_by_obj = _prefetch_tier2(objects, crop_model, d0, d1, lat, lon, progress)

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
            # Three clear looks is enough to see whether a canopy existed.
            # Fewer than that, the field was barely seen.
            if n_real < 3:
                obj["crop"] = NO_DATA
                obj["confidence"] = 0.0
                obj["status"] = "no_data"
                obj["note"] = "only %d clear looks in the season" % n_real
                continue

            ndvi = np.array([s["indices"].get("NDVI_mean", np.nan) for s in scenes], float)
            evi = np.array([s["indices"].get("EVI_mean", np.nan) for s in scenes], float)
            ndmi = np.array([s["indices"].get("NDMI_mean", np.nan) for s in scenes], float)
            # Short cloudy holes are filled for cycle placement only. The
            # scenes handed to the model stay the real clear ones.
            ndvi_f = fill_short_gaps(ndvi)
            evi_f = fill_short_gaps(evi)
            ndmi_f = fill_short_gaps(ndmi)

            try:
                cycles = detector.detect_cycles(
                    dates=list(bin_dates), ndvi_values=ndvi_f, evi_values=evi_f,
                    ndmi_values=ndmi_f, scenes=scenes, grid_step_days=INTERVAL_DAYS,
                )
            except Exception as exc:                              # noqa: BLE001
                obj["crop"] = OTHERS
                obj["confidence"] = 0.0
                obj["status"] = "no_cycle"
                obj["note"] = "cycle detection failed: %s" % str(exc)[:80]
                continue

            cycles = [c.to_dict() if hasattr(c, "to_dict") else c for c in (cycles or [])]
            cycle = _pick_cycle(cycles, d0, d1)
            if cycle is None:
                label, why = classify_without_cycle(scenes)
                obj["crop"] = label
                obj["confidence"] = 0.0
                obj["status"] = "no_cycle"
                obj["note"] = why
                apply_reference_crop(obj, scenes, as_of, inputs.season)
                continue

            in_cycle = cycle_scenes(scenes, cycle)
            obj["n_obs_cycle"] = len(in_cycle)
            obj["cycle_sowing"] = str(_cycle_value(cycle, "sowing_date") or "")[:10] or None
            obj["cycle_peak"] = str(_cycle_value(cycle, "peak_date") or "")[:10] or None
            obj["cycle_harvest"] = str(_cycle_value(cycle, "harvest_date") or "")[:10] or None
            # Two real looks is enough to build a feature grid. Below the
            # training minimum the printed class is Others and the model's
            # lean stays on hover — the field is not dropped.
            if len(in_cycle) < 2:
                label, why = classify_without_cycle(scenes)
                obj["crop"] = label
                obj["confidence"] = 0.0
                obj["status"] = "no_data" if label == NO_DATA else "no_cycle"
                obj["note"] = why
                continue

            try:
                blocks = getattr(crop_model, "extra_blocks", None) or []
                if blocks:
                    extra = extra_by_obj.get(obj["field_id"])
                    if extra is not None and "emb" in blocks:
                        extra = _with_embedding(extra, obj, cycle, crop_model)
                    pred = crop_model._classify_crop_chronological(in_cycle, cycle, extra=extra)
                else:
                    pred = crop_model._classify_crop_chronological(in_cycle, cycle)
            except Exception as exc:                              # noqa: BLE001
                obj["crop"] = OTHERS
                obj["confidence"] = 0.0
                obj["status"] = "no_cycle"
                obj["note"] = "classifier error: %s" % str(exc)[:80]
                continue

            c = obj.get("centroid") or {}
            eco = ecoregion_for(c.get("lat"), c.get("lng")) if c else None
            obj["ecoregion"] = eco
            obj.update(decide_crop(
                pred, cycle,
                allow=sorted(allow),
                confidence_threshold=inputs.confidence_threshold,
                season=inputs.season,
                ecoregion=eco,
                support=support,
                apply_region_guard=inputs.apply_region_guard,
                apply_season_mask=inputs.apply_season_mask,
                cycle_complete=cycle_is_complete(cycle, scenes, as_of),
            ))
            # A short optical record still gets the model's lean, but that lean
            # is not printed as the crop. Hover shows model_top_crop.
            if len(in_cycle) < MIN_CYCLE_SCENES and obj.get("crop") not in (OTHERS, "Fallow", NO_DATA):
                leaned = obj.get("model_top_crop") or obj.get("crop")
                obj["model_top_crop"] = leaned
                obj["crop"] = OTHERS
                obj["status"] = "provisional"
                obj["note"] = (
                    f"only {len(in_cycle)} clear looks inside the cycle; "
                    f"model leans {leaned}"
                )

            dur = cycle.get("duration_days") if isinstance(cycle, dict) else getattr(cycle, "duration_days", None)
            if dur:
                obj["cycle_duration_days"] = float(dur)
            apply_reference_crop(obj, scenes, as_of, inputs.season)
    finally:
        cycle_log.setLevel(prev_cycle_level)

    progress("classifying", 95.0, "Classified %d objects" % n_total, None)
    return {
        "objects": objects,
        "model_version": provenance["name"],
        "model": provenance,
        "window": {"start": d0.isoformat(), "end": d1.isoformat(), "as_of": as_of.isoformat()},
    }


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
    dissolve_same_crop: bool = True,
) -> List[Dict[str, Any]]:
    """Turn SNIC pieces into farm-like polygons.

    Tiny slivers join the neighbour they share the longest border with (even
    if that neighbour is a different crop). Remaining adjacent same-crop
    pieces dissolve into one field. Disconnected patches of the same crop
    stay separate.

    `dissolve_same_crop=False` is for objects that already ARE field
    boundaries (ALU / FTW / watershed): two neighbouring wheat farms are two
    fields, and dissolving them would throw away exactly what delineation
    produced. Specks are still absorbed.
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

    # "Not requested" pieces only dissolve with pieces the model gave the same
    # crop, or a soybean patch and a maize patch would merge under one label.
    def _key(it: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        crop = it.get("crop") or "Unclassified"
        # Others keeps the model's crop in the key, so a soybean patch and a
        # maize patch do not dissolve into one field.
        return crop, (it.get("model_top_crop") if crop == OTHERS else None)

    by_crop: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}
    for it in items:
        by_crop.setdefault(_key(it), []).append(it)

    merged: List[Dict[str, Any]] = []
    fid = 1
    groups = ([(k[0], g) for k, g in by_crop.items()] if dissolve_same_crop
              else [(_key(it)[0], [it]) for it in items])
    for crop, group in groups:
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
            dominant, dominant_w = None, 0.0
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
                if w > dominant_w:
                    dominant, dominant_w = it, w
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
            # The model's own answer travels with the polygon. A dissolved patch
            # reports the piece covering most of it, never a blend.
            if dominant is not None:
                for k in FIELD_EXPORT_KEYS:
                    if dominant.get(k) is not None:
                        rec[k] = dominant[k]
            src = group[0].get("boundary_source")
            if src:
                rec["boundary_source"] = src
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
    "Soyabean+Tur": "#A2512B",
}
NON_CROP_COLORS: Dict[str, str] = {
    "Non-agricultural": "#9A9287", "Water": "#4A7FA5", "Fallow": "#C4B99F",
    "Unclassified": "#B0A89C", "Abstained": "#8F8779",
    "Not requested": "#D6CFC2", OTHERS: "#C4BBAE", NO_DATA: "#E4DED3",
}
# Classes that are an answer of "no crop named here", so they are excluded from
# classified area but still reported. Older runs used Unclassified, Abstained
# and Not requested; those labels still draw.
NON_CROP_CLASSES = frozenset(
    {"Unclassified", "Abstained", "Fallow", "Non-agricultural", "Water",
     "Not requested", OTHERS, NO_DATA}
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
        if o.get("boundary_source"):
            props["boundary_source"] = o["boundary_source"]
        for k in FIELD_EXPORT_KEYS:
            if o.get(k) is not None:
                props[k] = o[k]
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

    possible: Dict[str, Dict[str, float]] = {}
    for o in objects:
        pc = o.get("possible_crop")
        if pc:
            agg = possible.setdefault(pc, {"field_count": 0, "area_ha": 0.0})
            agg["field_count"] += 1
            agg["area_ha"] = round(agg["area_ha"] + float(o.get("area_ha") or 0.0), 3)

    return {
        "possible_requested": possible,
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

    from crop_analysis.fused_classifier import is_fused_bundle

    model_path = area_model_path()
    fused = is_fused_bundle(model_path)
    objects, bin_dates = segment_and_extract(areas, inputs, prog, extract_series=not fused)

    prog("classifying", 80.0, "Classifying %d objects" % len(objects), None)
    if fused:
        out = classify_objects_fused(objects, inputs, prog, model_path)
    else:
        out = classify_objects(objects, bin_dates, inputs, prog)

    prog("vectorizing", 96.0, "Cleaning field boundaries", None)
    sources = sorted({o.get("boundary_source", "snic") for o in out["objects"]})
    cleaned = merge_field_objects(out["objects"], inputs.min_field_area_ha,
                                  dissolve_same_crop=(sources == ["snic"]))
    result = build_result(cleaned, areas, inputs, bin_dates, out.get("model_version"))
    if inputs.cadastral_plots:
        result["cadastral_alignment"] = dict(_ALIGNMENT_REPORT)
    result["model"] = out.get("model")
    result["window"] = out.get("window")
    _, season_end = season_window(inputs.season, inputs.year)
    result["season_complete"] = bool(out.get("window") and out["window"]["end"] >= season_end.isoformat())
    result["target_crops"] = sorted({c for c in inputs.target_crops if c})
    result["delineation"] = {"requested": inputs.delineation_method, "sources": sources}
    result["validation_checks"] = [c.to_dict() for c in checks]
    prog("complete", 100.0, None, [c.to_dict() for c in checks])
    return result
