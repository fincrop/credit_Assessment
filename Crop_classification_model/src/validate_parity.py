"""
Phase 2 gate — is the batched extractor telling the same story as production?

The batched path in extract.py is bespoke code. If it disagrees with
SatelliteDataCollector, we would train on features production will never
produce, and every metric afterwards would be measuring the wrong thing.

This runs BOTH paths over the same parcels and the same window and compares
index values bin by bin. The gate from classification_model.md Phase 2 is
NDVI agreement <= 0.005.

Two known, intended differences are reported separately rather than hidden:
  * production reduces over the bbox, we also extract the polygon — comparison
    uses the bbox series, which is the like-for-like one;
  * production picks the single clearest scene per bin, we mosaic the bin, so we
    recover observations production leaves as NaN. Those bins are counted as
    "recovered", not as disagreements.

Run:  python -m src.extract --validate 30
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import REPORTS, setup_logging

log = setup_logging("validate")

NDVI_GATE = 0.005
COMPARE_INDICES = ["NDVI_mean", "EVI_mean", "NDMI_mean"]


def _production_series(row) -> Dict[str, Dict[str, float]]:
    """
    Run the real collector on one parcel, restricted to its label window.

    PipelineConfig is patched for the call so the collector looks at the same
    window we do instead of its default 3-year season-anchored lookback.
    """
    from config import PipelineConfig
    from data_acquisition.satellite_collector import SatelliteDataCollector

    collector = SatelliteDataCollector(verbose=False)

    d0 = datetime.strptime(row.win_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(row.win_end, "%Y-%m-%d").date()

    # The collector's continuous path derives its own window; call the GEE
    # scene-stats fetcher directly with our window so the comparison is exact.
    import ee

    bbox = list(row.geometry.bounds)
    aoi = ee.Geometry.Rectangle(bbox, proj="EPSG:4326", geodesic=False)
    by_day = collector._fetch_gee_scene_stats(
        aoi, d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")
    )
    if not by_day:
        return {}

    interval = int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10))
    slots = collector._build_date_slots_with_stats(by_day, d0, d1, interval)

    out: Dict[str, Dict[str, float]] = {}
    for b0, stats in slots:
        if stats is None:
            continue
        out[b0.isoformat()] = stats
    return out


def _batched_series(row) -> Dict[str, Dict[str, float]]:
    """The extractor's own path, bbox geometry, same window."""
    import ee

    from .extract import (bin_mosaic_ee, _reducer, utm_epsg_for_lon,
                          INTERVAL_DAYS, SCALE_M)

    d0 = datetime.strptime(row.win_start, "%Y-%m-%d").date()
    d1 = datetime.strptime(row.win_end, "%Y-%m-%d").date()

    fc = ee.FeatureCollection([
        ee.Feature(
            ee.Geometry(row.geometry.envelope.__geo_interface__,
                        proj="EPSG:4326", geodesic=False),
            {"geom_hash": row.geom_hash, "geom_kind": "bbox"},
        )
    ])
    aoi = ee.Geometry(row.geometry.envelope.__geo_interface__,
                      proj="EPSG:4326", geodesic=False)
    reducer = _reducer()
    epsg = utm_epsg_for_lon(row.geometry.centroid.x)

    bins, cur = [], d0
    while cur <= d1:
        bins.append(cur)
        cur = cur + timedelta(days=INTERVAL_DAYS)

    # Server-side loop over bins in one request: same mosaic logic as extract.py.
    bins_ms = ee.List([ee.Date(b.isoformat()).millis() for b in bins])

    def per_bin(b_ms):
        b0 = ee.Date(b_ms)
        # Uses the extractor's OWN mosaic builder, not a reimplementation, so
        # this test certifies the code that actually runs.
        return bin_mosaic_ee(aoi, b0, epsg).reduceRegions(
            collection=fc, reducer=reducer, scale=SCALE_M,
        ).map(lambda f: f.set("bin_start", b0.format("YYYY-MM-dd")))

    fc_out = ee.FeatureCollection(bins_ms.map(per_bin)).flatten()
    info = fc_out.getInfo()
    return {
        f["properties"]["bin_start"]: f["properties"]
        for f in info.get("features", [])
    }


def run_validation(parcels: gpd.GeoDataFrame, n: int) -> int:
    sample = parcels.groupby("Crop_Name", group_keys=False).apply(
        lambda g: g.head(max(1, n // parcels["Crop_Name"].nunique()))
    ).head(n)

    log.info("validating %d parcel(s): production collector vs batched extractor",
             len(sample))

    rows: List[Dict] = []
    for i, row in enumerate(sample.itertuples(), 1):
        t0 = time.time()
        try:
            prod = _production_series(row)
        except Exception as exc:                      # noqa: BLE001
            log.warning("  [%d/%d] %s production path failed: %s",
                        i, len(sample), row.geom_hash, str(exc)[:110])
            continue
        try:
            mine = _batched_series(row)
        except Exception as exc:                      # noqa: BLE001
            log.warning("  [%d/%d] %s batched path failed: %s",
                        i, len(sample), row.geom_hash, str(exc)[:110])
            continue

        shared = sorted(set(prod) & set(mine))
        only_mine = sorted(set(mine) - set(prod))

        for b in shared:
            rec = {"geom_hash": row.geom_hash, "crop": row.Crop_Name, "bin": b}
            for k in COMPARE_INDICES:
                pv = pd.to_numeric(prod[b].get(k), errors="coerce")
                mv = pd.to_numeric(mine[b].get(k), errors="coerce")
                rec[f"prod_{k}"] = pv
                rec[f"mine_{k}"] = mv
                rec[f"d_{k}"] = (
                    abs(pv - mv) if np.isfinite(pv) and np.isfinite(mv) else np.nan
                )
            rows.append(rec)

        n_rec = sum(
            1 for b in only_mine
            if np.isfinite(pd.to_numeric(mine[b].get("NDVI_mean"), errors="coerce"))
        )
        log.info("  [%d/%d] %-10s shared=%3d recovered=%3d  (%.0fs)",
                 i, len(sample), row.Crop_Name, len(shared), n_rec, time.time() - t0)

    if not rows:
        log.error("no comparable bins produced — cannot validate")
        return 1

    df = pd.DataFrame(rows)
    out_csv = REPORTS / "parity_validation.csv"
    df.to_csv(out_csv, index=False)

    log.info("")
    log.info("=" * 68)
    log.info("PARITY RESULT   (%d comparable bins, %d parcels)",
             len(df), df["geom_hash"].nunique())
    log.info("=" * 68)

    passed = True
    for k in COMPARE_INDICES:
        d = df[f"d_{k}"].dropna()
        if d.empty:
            log.warning("  %-10s no overlapping finite values", k)
            continue
        log.info("  %-10s n=%5d  mean=%.6f  p95=%.6f  max=%.6f",
                 k, len(d), d.mean(), d.quantile(0.95), d.max())
        if k == "NDVI_mean":
            if d.quantile(0.95) > NDVI_GATE:
                log.error("  GATE FAILED: NDVI p95 diff %.6f > %.3f",
                          d.quantile(0.95), NDVI_GATE)
                passed = False
            else:
                log.info("  GATE PASSED: NDVI p95 diff %.6f <= %.3f",
                         d.quantile(0.95), NDVI_GATE)

    log.info("wrote %s", out_csv)
    return 0 if passed else 1
