"""
Phase 1 — Ingest & audit.

crop_classification_train_500.gpkg -> data/00_parcels_clean.parquet

Does four things and asserts the result of each:
  1. De-duplicate on geometry (109 WKB-identical rows in the source).
  2. Fix the unit bug: `Area` is ACRES, not hectares. Everything downstream
     carries `area_ha`. See classification_model.md section A.4 — feeding raw
     `Area` as field_area_ha makes validate_farm_geometry reject 100% of
     polygons (ratio 0.405 < GEOMETRY_AREA_RATIO_MIN 0.5) and silently fall
     back to point+buffer, which would break train/serve parity with no error.
  3. Assign spatial CV blocks (0.25 deg) and agro-ecoregion, using the
     pipeline's own infer_agro_ecoregion so labels match production.
  4. Reproduce the Part A audit numbers, so a change in the source file is
     caught here rather than surfacing as a strange model later.

Run:  python -m src.ingest
"""
from __future__ import annotations

import hashlib
import sys

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import (
    ACRES_TO_HA,
    BLOCK_DEG,
    DATA,
    EQUAL_AREA_CRS,
    PROJECT,
    block_id,
    setup_logging,
)

log = setup_logging("ingest")

SRC_GPKG = PROJECT / "crop_classification_train_500.gpkg"
OUT = DATA / "00_parcels_clean.parquet"

# Expected source shape — a mismatch means the file changed and the audit
# numbers in classification_model.md no longer describe it.
EXPECT_ROWS = 9000
EXPECT_CLASSES = 18
EXPECT_DUPES = 109

# Sentinel-2 native optical resolution; used for the pixel-count gate.
PIXEL_M = 10.0
# One-pixel inset to suppress boundary mixing. Parcels here have a median of
# ~6 vertices (cadastral approximations), so edge pixels are unreliable.
CORE_INSET_M = 10.0
# Below this many core pixels a parcel mean is too noisy to train on.
MIN_CORE_PIXELS = 5


def _ecoregion_labels(lats: np.ndarray, lons: np.ndarray) -> list[str]:
    """Agro-ecoregion via the pipeline's own inference (not a local copy)."""
    from utils.india_geo_context import infer_agro_ecoregion

    return [infer_agro_ecoregion(float(la), float(lo))[0] for la, lo in zip(lats, lons)]


def main() -> int:
    if not SRC_GPKG.exists():
        log.error("source not found: %s", SRC_GPKG)
        return 1

    log.info("reading %s", SRC_GPKG.name)
    g = gpd.read_file(SRC_GPKG)
    log.info("  %d rows, %d classes, crs=%s", len(g), g["Crop_Name"].nunique(), g.crs)

    if len(g) != EXPECT_ROWS or g["Crop_Name"].nunique() != EXPECT_CLASSES:
        log.warning(
            "source shape differs from the audited file (%d rows / %d classes); "
            "Part A numbers may no longer hold",
            len(g), g["Crop_Name"].nunique(),
        )

    g["Date"] = pd.to_datetime(g["Date"])

    # ── geometry hash + de-duplication ────────────────────────────────────
    wkb = g.geometry.to_wkb()
    g["geom_hash"] = [hashlib.sha1(b).hexdigest()[:16] for b in wkb]
    n_dupes = int(wkb.duplicated().sum())
    log.info("duplicate geometries: %d (expected %d)", n_dupes, EXPECT_DUPES)

    before = len(g)
    g = g.loc[~wkb.duplicated().values].copy()
    log.info("de-duplicated: %d -> %d rows", before, len(g))

    # ── unit fix + true geometric area ────────────────────────────────────
    ga = g.to_crs(EQUAL_AREA_CRS)
    true_m2 = ga.geometry.area.values

    ratio = np.median(true_m2 / g["Area"].values)
    log.info("unit check: median m2/Area = %.1f  (1 acre = 4046.86, 1 ha = 10000)", ratio)
    if not (3900 < ratio < 4200):
        log.error(
            "Area column is NOT acres (median m2/Area = %.1f). The acres->ha "
            "conversion below would be wrong. Aborting rather than silently "
            "poisoning every downstream area value.", ratio,
        )
        return 1

    g["area_ha"] = g["Area"].values * ACRES_TO_HA
    g["area_ha_true"] = true_m2 / 1e4
    g["px10"] = true_m2 / (PIXEL_M ** 2)

    core_m2 = ga.geometry.buffer(-CORE_INSET_M).area.values
    g["core_px10"] = np.maximum(core_m2, 0.0) / (PIXEL_M ** 2)

    # Sanity: after the fix, the ratio the pipeline's QA computes must land
    # inside [0.5, 2.0] or the collector will reject the polygon.
    qa_ratio = g["area_ha_true"] / g["area_ha"]
    log.info(
        "post-fix geometry QA ratio: min=%.3f median=%.3f max=%.3f  "
        "(pipeline accepts 0.5-2.0)",
        qa_ratio.min(), qa_ratio.median(), qa_ratio.max(),
    )
    n_qa_fail = int(((qa_ratio < 0.5) | (qa_ratio > 2.0)).sum())
    if n_qa_fail:
        log.warning("%d parcel(s) would still fail geometry QA", n_qa_fail)

    # ── centroid, spatial blocks, ecoregion ───────────────────────────────
    cent = ga.geometry.centroid.to_crs("EPSG:4326")
    g["lat"] = cent.y.values
    g["lon"] = cent.x.values
    g["block_id"] = block_id(g["lat"].values, g["lon"].values, BLOCK_DEG)
    g["ecoregion"] = _ecoregion_labels(g["lat"].values, g["lon"].values)

    # ── pixel-count gate ──────────────────────────────────────────────────
    thin = g["core_px10"] < MIN_CORE_PIXELS
    g["px_gate_ok"] = ~thin
    log.info(
        "core pixels (10m, after -%.0fm inset): p5=%.1f median=%.1f | "
        "%d parcel(s) below %d px flagged",
        CORE_INSET_M, g["core_px10"].quantile(0.05), g["core_px10"].median(),
        int(thin.sum()), MIN_CORE_PIXELS,
    )

    # ── window anchor for extraction (see D.3) ────────────────────────────
    # `Date` is a survey snapshot, not a sowing date, and is near-constant
    # within a class. Its ONLY legitimate role is anchoring the observation
    # window wide enough to contain whichever cycle the label refers to.
    g["win_start"] = (g["Date"] - pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")
    g["win_end"] = (g["Date"] + pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")

    # ── audit (reproduces Part A) ─────────────────────────────────────────
    log.info("")
    log.info("=" * 68)
    log.info("AUDIT")
    log.info("=" * 68)
    log.info("spatial blocks (%.2f deg): %d", BLOCK_DEG, g["block_id"].nunique())
    single = int((g.groupby("block_id")["Crop_Name"].nunique() == 1).sum())
    log.info("  single-crop blocks: %d / %d (%.0f%%)",
             single, g["block_id"].nunique(), 100 * single / g["block_id"].nunique())
    blocks_per_class = g.groupby("Crop_Name")["block_id"].nunique().sort_values()
    log.info("  blocks per class: min=%d (%s)  max=%d (%s)",
             blocks_per_class.iloc[0], blocks_per_class.index[0],
             blocks_per_class.iloc[-1], blocks_per_class.index[-1])
    log.info("unique Date values per class: min=%d max=%d  <- leakage channel",
             g.groupby("Crop_Name")["Date"].nunique().min(),
             g.groupby("Crop_Name")["Date"].nunique().max())
    log.info("ecoregions: %s", dict(g["ecoregion"].value_counts()))
    log.info("")
    log.info("per-class summary:")
    summary = (
        g.groupby("Crop_Name")
        .agg(n=("geom_hash", "size"),
             blocks=("block_id", "nunique"),
             dates=("Date", "nunique"),
             area_ha_med=("area_ha", "median"),
             core_px_med=("core_px10", "median"))
        .round(2)
    )
    for line in summary.to_string().splitlines():
        log.info("  %s", line)

    # ── write ─────────────────────────────────────────────────────────────
    keep = [
        "sample_id", "geom_hash", "Crop_Name", "Date", "win_start", "win_end",
        "area_ha", "area_ha_true", "px10", "core_px10", "px_gate_ok",
        "lat", "lon", "block_id", "ecoregion", "source_file", "geometry",
    ]
    out = gpd.GeoDataFrame(g[keep], geometry="geometry", crs=g.crs)
    out.to_parquet(OUT, index=False)
    log.info("")
    log.info("wrote %s  (%d rows, %d cols)", OUT.relative_to(PROJECT), len(out), len(keep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
