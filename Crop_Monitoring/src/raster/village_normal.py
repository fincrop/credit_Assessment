"""Village multi-year normal (plan §5.5): is the whole village below its usual season?

Cohort stress compares a field with its neighbours, so a drought that hits
every field equally is invisible to it. This compares the village's current
cropland NDVI curve with the 2019-2025 curves of the same cropland, each year
aligned on its own monsoon onset so an early or late monsoon is not mistaken
for a good or bad crop.

Cropland: ESA WorldCover v200 class 40. Dynamic World is not used: on the
Dhaswadi tile its monsoon label called 63% of cropland "trees".
Per year: Sentinel-2 L2A with Cloud Score+ >= 0.6, 10-day median NDVI, median
over cropland pixels in the village box. Onset from CHIRPS with the same rule
as sowing.monsoon_onset. All reductions run server-side; only the series come
back.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np

from src.raster.grid import Grid
from src.raster.sowing import monsoon_onset

logger = logging.getLogger(__name__)

BIN_DAYS = 10
OFFSETS = list(range(-30, 151, BIN_DAYS))      # days relative to onset
BELOW_RUN = 2                                  # consecutive bins below P25


def _year_series(ee, region, year: int) -> tuple[dict[date, float], dict[date, float]]:
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
          .filterDate(f"{year}-05-01", f"{year}-12-01")
          .linkCollection(ee.ImageCollection("GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"), ["cs_cdf"]))
    crop = ee.ImageCollection("ESA/WorldCover/v200").first().eq(40)

    def ndvi(img):
        return (img.normalizedDifference(["B8", "B4"]).rename("ndvi")
                .updateMask(img.select("cs_cdf").gte(0.6)).updateMask(crop))

    starts = [date(year, 5, 1) + timedelta(days=BIN_DAYS * k) for k in range(21)]

    def one(d0):
        d0 = ee.Date(d0)
        comp = s2.filterDate(d0, d0.advance(BIN_DAYS, "day")).map(ndvi).median()
        v = comp.reduceRegion(ee.Reducer.median(), region, 20, maxPixels=1e8, bestEffort=True).get("ndvi")
        return ee.Feature(None, {"d": d0.format("YYYY-MM-dd"), "v": v})

    fc = ee.FeatureCollection([one(d.isoformat()) for d in starts]).getInfo()
    ndvi_by = {}
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        if p.get("v") is not None:
            ndvi_by[date.fromisoformat(p["d"])] = float(p["v"])

    rain = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(f"{year}-05-15", f"{year}-09-01")
            .map(lambda img: ee.Feature(None, {
                "d": img.date().format("YYYY-MM-dd"),
                "r": img.reduceRegion(ee.Reducer.mean(), region, 5000).get("precipitation")})))
    rain_by = {}
    for f in ee.FeatureCollection(rain).getInfo().get("features", []):
        p = f.get("properties") or {}
        if p.get("r") is not None:
            rain_by[date.fromisoformat(p["d"])] = float(p["r"])
    return ndvi_by, rain_by


def _aligned(ndvi_by: dict[date, float], onset: Optional[date]) -> np.ndarray:
    out = np.full(len(OFFSETS), np.nan)
    if onset is None or not ndvi_by:
        return out
    days = sorted(ndvi_by)
    x = np.array([(d - onset).days + BIN_DAYS / 2 for d in days], float)
    y = np.array([ndvi_by[d] for d in days], float)
    for i, off in enumerate(OFFSETS):
        near = np.abs(x - off) <= BIN_DAYS
        if near.any():
            out[i] = float(np.median(y[near]))
    return out


def compute(grid: Grid, current_curve: dict[date, float], current_onset: Optional[date],
            years=range(2019, 2026)) -> dict:
    """Normal across past years vs the current village curve, on onset offsets."""
    from src.raster.fetch import _ee

    ee = _ee()
    lon0, lat0, lon1, lat1 = grid.lonlat_bounds()
    region = ee.Geometry.Rectangle([lon0, lat0, lon1, lat1])
    past = []
    onsets = {}
    for y in years:
        try:
            nd, rain = _year_series(ee, region, y)
        except Exception as exc:  # noqa: BLE001
            logger.warning("village normal %s skipped: %s", y, str(exc)[:200])
            continue
        on = monsoon_onset(rain, y).date
        onsets[y] = on.isoformat() if on else None
        past.append(_aligned(nd, on))
    if len(past) < 3:
        return {"status": "unavailable", "reason": f"only {len(past)} past seasons", "onsets": onsets}
    mat = np.vstack(past)
    with np.errstate(all="ignore"):
        med = np.nanmedian(mat, axis=0)
        p25 = np.nanpercentile(mat, 25, axis=0)
        p75 = np.nanpercentile(mat, 75, axis=0)
    cur = _aligned(current_curve, current_onset)
    below = np.isfinite(cur) & np.isfinite(p25) & (cur < p25)
    run = longest = 0
    for b in below:
        run = run + 1 if b else 0
        longest = max(longest, run)
    seen = np.isfinite(cur)
    status = "below_normal" if longest >= BELOW_RUN else ("near_normal" if seen.any() else "unavailable")
    r = lambda a: [None if not np.isfinite(v) else round(float(v), 3) for v in a]  # noqa: E731
    return {
        "status": status, "offsets_days": OFFSETS, "current": r(cur), "normal_median": r(med),
        "normal_p25": r(p25), "normal_p75": r(p75), "years": sorted(onsets), "onsets": onsets,
        "longest_below_p25_bins": int(longest),
        "note": "Village cropland NDVI vs the same cropland in past seasons, aligned on each year's "
                "monsoon onset. Village-level only.",
    }
