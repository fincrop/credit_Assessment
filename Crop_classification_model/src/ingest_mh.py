"""
Phase 1c — Marathwada cotton and soybean (accuracy plan, Track A1–A2).

  Cottondata_1500_gpkg.gpkg, SoyabeanData_1000.gpkg
      -> data/00_parcels_mh_new.parquet     parcels to extract (new geometry only)
      -> data/00_parcels_mh_full.parquet    augmented set + new, for cycles/features
      -> data/splits/mh2023_split.json      frozen spatial test blocks

What is different about these two files, and handled here:

  * `Area` is ACRES (median m2/Area = 4046.9). Converted, never used raw.
  * Cotton carries its survey date as `G_Date` (2023-07-10 for every row).
    Soybean carries `Date` (2023-08-10) and a later `GDate` (Sep/Oct 2023).
    `Date` keeps the training convention: a season marker on the 10th of a
    month, used only to anchor the extraction window and the season attribution.
  * Soybean rows come from a "Checked" and an "Unchecked" source layer. Only
    Checked rows may enter the test set; Unchecked rows train at half weight.
  * Some polygons are already in `00_parcels_augmented.parquet` (IoU > 0.5:
    46 cotton, 300 soybean on 2 Oct 2026). They are not extracted again and are
    never allowed into the test set, because the shipped model trained on them.

Spatial split (the only accuracy evidence we have without field visits):
whole 0.1 degree blocks are held out until the test set holds at least
MIN_TEST cotton, MIN_TEST soybean and MIN_TEST_TUR local tur. Cotton blocks
nearest Dhaswadi are taken first, because that is the area we report on.
At n = 200 and recall ~0.85 the 95% interval is about +-5 points.

    python -m src.ingest_mh --dry-run
    python -m src.ingest_mh
    python -m src.extract --parcels 00_parcels_mh_new.parquet \\
        --shard-dir shards_mh --out 01_scenes_mh.parquet \\
        --poly-fraction 1.0 --bin-anchor 2020-12-06
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from math import cos, radians

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

log = setup_logging("ingest_mh")

SOURCES = (
    # path, crop, survey-date column, season-marker column
    ("Cottondata_1500_gpkg.gpkg", "Cotton", "G_Date", "G_Date"),
    ("SoyabeanData_1000.gpkg", "Soyabean", "GDate", "Date"),
)
BASE = DATA / "00_parcels_augmented.parquet"
OUT_NEW = DATA / "00_parcels_mh_new.parquet"
OUT_FULL = DATA / "00_parcels_mh_full.parquet"
SPLIT = DATA / "splits" / "mh2023_split.json"

PIXEL_M = 10.0
CORE_INSET_M = 10.0
MIN_CORE_PIXELS = 5            # same gate as ingest.py
DUP_IOU = 0.5                  # same polygon digitised twice

# Marathwada test region and the reporting village.
MH_BOX = (18.0, 20.5, 75.0, 77.5)          # lat0, lat1, lon0, lon1
DHASWADI = (18.80, 76.85)
SPLIT_BLOCK_DEG = 0.10
MIN_TEST = 200
MIN_TEST_TUR = 100

KEEP = ["sample_id", "geom_hash", "Crop_Name", "Date", "win_start", "win_end",
        "area_ha", "area_ha_true", "px10", "core_px10", "px_gate_ok",
        "lat", "lon", "block_id", "ecoregion", "source_file", "geometry"]


def _geom_hash(geom) -> str:
    return hashlib.sha1(geom.wkb).hexdigest()[:16]


def _ecoregions(lat, lon):
    from utils.india_geo_context import infer_agro_ecoregion
    return [infer_agro_ecoregion(float(a), float(o))[0] for a, o in zip(lat, lon)]


def read_sources() -> gpd.GeoDataFrame:
    frames = []
    for path, crop, survey_col, marker_col in SOURCES:
        g = gpd.read_file(PROJECT / path).to_crs(4326)
        g["Crop_Name"] = crop
        g["Date"] = pd.to_datetime(g[marker_col])
        g["survey_date"] = pd.to_datetime(g[survey_col])
        g["source_file"] = path
        if "layer" in g.columns:
            g["qa_checked"] = g["layer"].astype(str).str.contains("_Checked")
        else:
            g["qa_checked"] = pd.NA           # cotton: no review flag in the file
        g["Area_acres"] = g["Area"].astype(float)
        frames.append(g[["Crop_Name", "Date", "survey_date", "source_file",
                         "qa_checked", "Area_acres", "geometry"]])
        log.info("%-28s %5d rows  %s", path, len(g), crop)
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=4326)


def measure(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    ga = g.to_crs(EQUAL_AREA_CRS)
    true_m2 = ga.geometry.area.values
    ratio = float(np.median(true_m2 / g["Area_acres"].values))
    log.info("unit check: median m2/Area = %.1f (acre = 4046.9)", ratio)
    if not 3900 < ratio < 4200:
        raise SystemExit(f"Area column is not acres (ratio {ratio:.1f}); refusing to convert")
    g = g.copy()
    # The cotton file stores a Z of 0.0 on every vertex; Earth Engine rejects
    # 3-D GeoJSON ("Invalid GeoJSON geometry"), so keep x/y only.
    import shapely
    g["geometry"] = shapely.force_2d(g.geometry.values)
    g["area_ha"] = g["Area_acres"].values * ACRES_TO_HA
    g["area_ha_true"] = true_m2 / 1e4
    g["px10"] = true_m2 / PIXEL_M ** 2
    g["core_px10"] = np.maximum(ga.geometry.buffer(-CORE_INSET_M).area.values, 0.0) / PIXEL_M ** 2
    g["px_gate_ok"] = g["core_px10"] >= MIN_CORE_PIXELS
    cent = ga.geometry.centroid.to_crs(4326)
    g["lat"], g["lon"] = cent.y.values, cent.x.values
    g["block_id"] = block_id(g["lat"].values, g["lon"].values, BLOCK_DEG)
    g["ecoregion"] = _ecoregions(g["lat"].values, g["lon"].values)
    g["win_start"] = (g["Date"] - pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")
    g["win_end"] = (g["Date"] + pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")
    g["geom_hash"] = [_geom_hash(x) for x in g.geometry]
    return g


def match_existing(new: gpd.GeoDataFrame, base: gpd.GeoDataFrame) -> pd.Series:
    """geom_hash of the base parcel each new polygon duplicates (IoU > 0.5), else None."""
    a = new.to_crs(EQUAL_AREA_CRS)[["geometry"]]
    b = base.to_crs(EQUAL_AREA_CRS)[["geometry", "geom_hash"]]
    j = gpd.sjoin(a, b, predicate="intersects")
    out = pd.Series([None] * len(new), index=new.index, dtype=object)
    for i, r in zip(j.index, j["index_right"]):
        ga, gb = a.geometry.loc[i], b.geometry.loc[r]
        iou = ga.intersection(gb).area / max(ga.union(gb).area, 1e-9)
        if iou > DUP_IOU and out.loc[i] is None:
            out.loc[i] = b.loc[r, "geom_hash"]
    return out


def _in_box(lat, lon) -> np.ndarray:
    a, b, c, d = MH_BOX
    return (lat >= a) & (lat <= b) & (lon >= c) & (lon <= d)


def _km(lat, lon, ref) -> np.ndarray:
    dy = (np.asarray(lat) - ref[0]) * 111.32
    dx = (np.asarray(lon) - ref[1]) * 111.32 * cos(radians(ref[0]))
    return np.hypot(dx, dy)


QUOTAS = {"Cotton": MIN_TEST, "Soyabean": MIN_TEST, "Tur": MIN_TEST_TUR}


def choose_blocks(df: pd.DataFrame, eligible: pd.Series) -> tuple[list, dict]:
    """Greedy block selection: most quota progress per unit of training lost.

    The three crops share blocks (soybean and tur are interplanted across the
    same belt; cotton is dense). Filling one crop's quota in isolation drained
    another's training pool: cotton-first held out 820 cotton, low-cotton-first
    held out 410 of ~500 local tur. Each step here scores a block by the quota
    it fills divided by the share of each crop's Marathwada pool it removes from
    training, with a mild preference for blocks near Dhaswadi.
    """
    focus = df["in_mh"] & df["Crop_Name"].isin(list(QUOTAS))
    pool = df[focus].Crop_Name.value_counts().to_dict()
    by_block = {}
    for blk, grp in df[focus].groupby("split_block"):
        el = grp[eligible.loc[grp.index]]
        by_block[blk] = {
            "gain": {c: int((el.Crop_Name == c).sum()) for c in QUOTAS},
            "loss": {c: int((grp.Crop_Name == c).sum()) for c in QUOTAS},
            "dist": float(grp["dist_km"].min()),
        }

    chosen, got = [], {c: 0 for c in QUOTAS}
    while any(got[c] < q for c, q in QUOTAS.items()):
        best, best_score = None, 0.0
        for blk, b in by_block.items():
            if blk in chosen:
                continue
            progress = sum(min(b["gain"][c], max(QUOTAS[c] - got[c], 0)) / QUOTAS[c]
                           for c in QUOTAS)
            if progress <= 0:
                continue
            cost = sum(b["loss"][c] / max(pool.get(c, 1), 1) for c in QUOTAS) + 1e-3
            score = progress / cost / (1.0 + b["dist"] / 150.0)
            if score > best_score:
                best, best_score = blk, score
        if best is None:
            break
        chosen.append(best)
        for c in QUOTAS:
            got[c] += by_block[best]["gain"][c]
    return chosen, got


def build_split(full: pd.DataFrame) -> dict:
    """Hold out whole 0.1 degree blocks until each test quota is met.

    Eligible test rows: Marathwada, px gate passed, and never seen by the shipped
    model (new geometry only for cotton and soybean; local tur is existing data
    and is flagged as such). A block is taken whole or not at all.
    """
    df = full.copy()
    df["split_block"] = block_id(df["lat"].values, df["lon"].values, SPLIT_BLOCK_DEG)
    df["in_mh"] = _in_box(df["lat"].values, df["lon"].values)
    df["dist_km"] = _km(df["lat"], df["lon"], DHASWADI)

    new = df["mh_origin"] == "new"
    checked = df["qa_checked"].astype("boolean").fillna(True).astype(bool)
    # Scored rows: Marathwada, enough core pixels, and never seen by the shipped
    # model. Duplicates of shipped training parcels sit in held-out blocks but are
    # not scored. Every row of a held-out block, scored or not, leaves training.
    eligible = df["in_mh"] & df["px_gate_ok"] & (
        (new & ((df["Crop_Name"] == "Cotton") | ((df["Crop_Name"] == "Soyabean") & checked)))
        | ((df["Crop_Name"] == "Tur") & (df["mh_origin"] == "existing"))
    )

    chosen, got = choose_blocks(df, eligible)
    test = df["split_block"].isin(chosen) & eligible
    short = {c: q for c, q in QUOTAS.items() if got[c] < q}
    if short:
        log.warning("test quota not met: %s (got %s)", short, got)
    held = df["split_block"].isin(chosen)
    return {
        "version": "mh2023_v1",
        "block_deg": SPLIT_BLOCK_DEG,
        "test_blocks": sorted(chosen),
        "test_geom_hashes": sorted(df.loc[test, "geom_hash"]),
        # Every parcel in a held-out block, scored or not: none may train.
        "excluded_from_training": sorted(df.loc[held, "geom_hash"]),
        "counts": {k: int(v) for k, v in df[test].Crop_Name.value_counts().items()},
        "note": ("Whole blocks held out. Never train or tune on these. Tur test rows "
                 "are existing training data: a comparison against the SHIPPED model "
                 "on tur is in-sample."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    base = gpd.read_parquet(BASE)
    src = measure(read_sources())

    dup = match_existing(src, base)
    src["dup_of"] = dup
    log.info("already in the augmented set (IoU > %.1f): %s",
             DUP_IOU, src[dup.notna()].Crop_Name.value_counts().to_dict())

    exact = src["geom_hash"].duplicated()
    if exact.any():
        log.info("dropping %d exact duplicate geometries within the new files", int(exact.sum()))
        src = src[~exact].copy()

    new = src[src["dup_of"].isna()].copy()
    new["sample_id"] = np.arange(len(new)) + 2_000_000       # disjoint from base / augment
    new["mh_origin"] = "new"

    # Base rows carry the review flag and origin columns too, so one table holds both.
    full = base.copy()
    full["qa_checked"] = pd.NA
    full["survey_date"] = pd.NaT
    full["mh_origin"] = "existing"
    dup_hashes = set(src.loc[src["dup_of"].notna(), "dup_of"])
    full.loc[full["geom_hash"].isin(dup_hashes), "mh_origin"] = "existing_dup_of_mh"
    # Unchecked soybean duplicates keep their review flag for weighting.
    flag = src[src["dup_of"].notna()].set_index("dup_of")["qa_checked"]
    full.loc[full["geom_hash"].isin(flag.index), "qa_checked"] = (
        full.loc[full["geom_hash"].isin(flag.index), "geom_hash"].map(flag))

    cols = KEEP + ["qa_checked", "survey_date", "mh_origin"]
    full = gpd.GeoDataFrame(pd.concat([full[cols].astype({"qa_checked": object}), new[cols].astype({"qa_checked": object})], ignore_index=True),
                            geometry="geometry", crs=4326)

    split = build_split(full)
    log.info("test set: %s in %d blocks", split["counts"], len(split["test_blocks"]))
    log.info("new parcels to extract: %d  (%s)", len(new), new.Crop_Name.value_counts().to_dict())
    log.info("px gate failures among new: %d", int((~new["px_gate_ok"]).sum()))

    if a.dry_run:
        log.info("--dry-run: nothing written")
        return 0

    gpd.GeoDataFrame(new[cols], geometry="geometry", crs=4326).to_parquet(OUT_NEW, index=False)
    full.to_parquet(OUT_FULL, index=False)
    SPLIT.parent.mkdir(parents=True, exist_ok=True)
    SPLIT.write_text(json.dumps(split, indent=1))
    log.info("wrote %s, %s, %s", OUT_NEW.name, OUT_FULL.name, SPLIT.relative_to(PROJECT))
    log.info("next: python -m src.extract --parcels %s --shard-dir shards_mh "
             "--out 01_scenes_mh.parquet --poly-fraction 1.0 --bin-anchor 2020-12-06",
             OUT_NEW.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
