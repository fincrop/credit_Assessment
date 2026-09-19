"""
Phase 1b — targeted geographic augmentation.

  local source GeoPackages -> data/00_parcels_augmented.parquet

`classification_model.md` L.7 calls re-sampling from the full source files the
highest-leverage change available and proposes doing it *randomly*. Measurement
says random is the wrong rule and the stated reason is the wrong one.

What the audit actually shows: six crops -- Chilli, Maize, Mustard, Potato,
Rice, Tobacco -- occupy exactly ONE agro-ecoregion in the training set. Hold
that region out and they have zero training rows, so leave-one-ecoregion-out
recall is 0.000 by construction. No estimator, feature or amount of extra data
*from the same region* can change that, which is why the shipped model maps
rice and maize acceptably at home and not at all in a new district. The crops
that do appear in several regions transfer in proportion: Banana sits in 3
regions and holds 0.80 recall under LOEO, Cotton in 4 holds 0.56.

So the objective is not "more parcels". It is **ecoregion coverage per crop**,
and a random draw from source files that are themselves regionally concentrated
would mostly deepen the regions we already have. Measured on the local files,
the two rules differ sharply:

    Elai_3000_Rice.gpkg   3000 rows, 32 blocks -- but 2829 Gangetic / 171 S.Pen.

A random 500 takes ~28 Southern Peninsula parcels. This module takes the 171.

Selection rules, in order:
  1. Prefer parcels in ecoregions the crop does not yet have.
  2. Then top up under-represented regions toward a quota.
  3. Cap per (crop, block) so we widen coverage instead of deepening clusters.
  4. Sample *randomly* inside every stratum -- never by `Area`. Ranking by area
     is what created defect A.4, where Cotton's p25 sat above most crops' max
     and the model could read the label off the field size.

Not everything is fixable from local data. Mustard has 6 out-of-region parcels
available, Chilli none, and Tobacco has no source file at all. Those three stay
single-region and the honest response is a deployment guard, not a model.

    python -m src.augment --dry-run     # report what it would add
    python -m src.augment               # write 00_parcels_augmented.parquet
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Dict, List

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

log = setup_logging("augment")

BASE = DATA / "00_parcels_clean.parquet"
OUT = DATA / "00_parcels_augmented.parquet"

PIXEL_M = 10.0
CORE_INSET_M = 10.0
MIN_CORE_PIXELS = 5

SEED = 42
# Ceiling per (crop, 0.25 deg block). The source files hold up to 1,415 parcels
# in a single block; lifting that wholesale would re-create the clustering that
# makes blocked CV meaningless in the first place.
MAX_PER_CROP_BLOCK = 25
# ...but a region the crop does not yet have is worth accepting clustering for.
# Rice's 164 Southern-Peninsula candidates sit in a single block: under the
# normal cap only 27 get through, which is not enough to teach the crop a second
# climate. The two caps encode a real trade-off -- the tight one protects
# blocked CV from deepened clusters, the loose one buys ecoregion coverage,
# which is the axis LOEO folds on and the one village mapping actually needs.
# Clustered coverage of a second region beats none.
MAX_PER_CROP_BLOCK_NEW_REGION = 150
# Target parcels per (crop, ecoregion) once a region is represented at all.
REGION_QUOTA = 300
# Below this, a (crop, region) pair is treated as ABSENT rather than covered.
# It matters: Rice has exactly 1 Southern-Peninsula parcel, Maize 2 in Central
# Highland, Mustard 4 in North-West. Counting those as "the crop has a second
# region" is how a dataset looks geographically replicated while every model
# trained on it collapses out-of-region -- one parcel teaches nothing, but it
# does suppress the alarm.
MIN_VIABLE_REGION = 30

SOURCE_ROOT = Path("C:/Users/gopik/Desktop/All files")

# Only files verified as EPSG:4326 with a readable Crop_Name or a known single
# crop. UP_Maize_feb_July_2023.gpkg is deliberately absent: it is in a projected
# CRS (srs_id 100000) and would need reprojection, and Uttar Pradesh is Gangetic
# anyway -- it adds rows to the region maize already has, which is the one thing
# this module exists not to do.
SOURCES: List[Dict] = [
    # (path relative to SOURCE_ROOT, crop or None to read Crop_Name)
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Rice.gpkg", "crop": "Rice"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Maize.gpkg", "crop": "Maize"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Wheat.gpkg", "crop": "Wheat"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Cotton.gpkg", "crop": "Cotton"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Potato.gpkg", "crop": "Potato"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Onion.gpkg", "crop": "Onion"},
    {"path": "2000 Crop data/Crop_gpkg_files/3000_crop_data/All Crops/3000/Elai_3000_Mustard.gpkg", "crop": "Mustard"},
    {"path": "2000 Crop data/Crop_gpkg_files/soyabean_4000.gpkg", "crop": "Soyabean"},
    {"path": "2000 Crop data/Crop_gpkg_files/Bihar_maize_december_June.gpkg", "crop": "Maize"},
    {"path": "2000 Crop data/Crop_gpkg_files/Punjab_maize_Feb_July.gpkg", "crop": "Maize"},
    # Mixed-crop files. All_Crops_Data is the only local source of Maize outside
    # the Gangetic plain (200 parcels in CENTRAL_HIGHLAND_MIXED).
    {"path": "Elai_files/All_Crops_Data.gpkg", "crop": None},
    {"path": "Elai_files/all_farms_200.gpkg", "crop": None},
]

# Crops the classifier knows. Anything else in a mixed file is dropped -- E.6
# decided against training an `Others` attractor, and non-crop labels belong to
# the land-cover gate, not here.
KNOWN = {
    "Bajra", "Banana", "Chilli", "Cotton", "Gram", "Grapes", "Groundnut",
    "Jowar", "Maize", "Mustard", "Onion", "Potato", "Rice", "Soyabean",
    "Sugarcane", "Tobacco", "Tur", "Wheat",
}


def _ecoregions(lat: np.ndarray, lon: np.ndarray) -> List[str]:
    from utils.india_geo_context import infer_agro_ecoregion
    return [infer_agro_ecoregion(float(a), float(o))[0] for a, o in zip(lat, lon)]


def _geom_hash(geom) -> str:
    return hashlib.sha1(geom.wkb).hexdigest()[:16]


def _read_source(spec: Dict) -> gpd.GeoDataFrame:
    path = SOURCE_ROOT / spec["path"]
    if not path.exists():
        log.warning("missing source: %s", spec["path"])
        return gpd.GeoDataFrame()
    g = gpd.read_file(path)
    if g.crs is None or g.crs.to_epsg() != 4326:
        try:
            g = g.to_crs(4326)
        except Exception as e:                              # noqa: BLE001
            log.warning("cannot reproject %s (%s); skipping", spec["path"], e)
            return gpd.GeoDataFrame()

    g["Crop_Name"] = spec["crop"] if spec["crop"] else g.get("Crop_Name")
    g = g[g["Crop_Name"].isin(KNOWN)].copy()
    if g.empty:
        return gpd.GeoDataFrame()

    # `Date` anchors the extraction window (D.3). Sources that lack it cannot be
    # attributed to a cycle, so they are dropped rather than given a guessed one.
    if "Date" not in g.columns:
        log.warning("%s has no Date column; skipping", spec["path"])
        return gpd.GeoDataFrame()
    g["Date"] = pd.to_datetime(g["Date"], errors="coerce")
    g = g[g["Date"].notna()].copy()
    g["source_file"] = Path(spec["path"]).name
    return g[["Crop_Name", "Date", "source_file", "geometry"]]


def _measure(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach the geometry/pixel/region columns the ingest contract requires."""
    ga = g.to_crs(EQUAL_AREA_CRS)
    true_m2 = ga.geometry.area.values
    g = g.copy()
    g["area_ha_true"] = true_m2 / 1e4
    g["area_ha"] = g["area_ha_true"]          # no acres column on these sources
    g["px10"] = true_m2 / (PIXEL_M ** 2)
    g["core_px10"] = np.maximum(
        ga.geometry.buffer(-CORE_INSET_M).area.values, 0.0) / (PIXEL_M ** 2)
    g["px_gate_ok"] = g["core_px10"] >= MIN_CORE_PIXELS

    cent = ga.geometry.centroid.to_crs("EPSG:4326")
    g["lat"] = cent.y.values
    g["lon"] = cent.x.values
    g["block_id"] = block_id(g["lat"].values, g["lon"].values, BLOCK_DEG)
    g["ecoregion"] = _ecoregions(g["lat"].values, g["lon"].values)
    g["win_start"] = (g["Date"] - pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")
    g["win_end"] = (g["Date"] + pd.Timedelta(days=400)).dt.strftime("%Y-%m-%d")
    g["geom_hash"] = [_geom_hash(x) for x in g.geometry]
    return g


def select(base: pd.DataFrame, pool: gpd.GeoDataFrame,
           rng: np.random.Generator) -> gpd.GeoDataFrame:
    """Choose the parcels that most improve per-crop ecoregion coverage."""
    have = (base.groupby(["Crop_Name", "ecoregion"]).size()
                .rename("have").reset_index())
    have_map = {(r.Crop_Name, r.ecoregion): r.have for r in have.itertuples()}

    picks = []
    for (crop, region), grp in pool.groupby(["Crop_Name", "ecoregion"]):
        existing = have_map.get((crop, region), 0)
        if existing >= REGION_QUOTA:
            continue                     # region already well covered
        want = REGION_QUOTA - existing
        opens = existing < MIN_VIABLE_REGION
        cap = MAX_PER_CROP_BLOCK_NEW_REGION if opens else MAX_PER_CROP_BLOCK
        # Cap per block first, so a single dense block cannot fill the quota.
        capped = (grp.groupby("block_id", group_keys=False)[grp.columns.tolist()]
                     .apply(lambda d: d.sample(min(len(d), cap),
                                               random_state=SEED)))
        if len(capped) > want:
            capped = capped.sample(want, random_state=SEED)
        capped = capped.copy()
        capped["existing_in_region"] = existing
        capped["opens_region"] = opens
        picks.append(capped)

    if not picks:
        return gpd.GeoDataFrame()
    return pd.concat(picks, ignore_index=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report the coverage change without writing")
    a = ap.parse_args()
    rng = np.random.default_rng(SEED)

    # gpd, not pd: read through pandas the geometry column comes back as raw
    # WKB bytes, which then cannot be concatenated with the shapely geometries
    # on the new rows.
    base = gpd.read_parquet(BASE)
    log.info("base: %d parcels, %d crops", len(base), base.Crop_Name.nunique())

    frames = [f for s in SOURCES if not (f := _read_source(s)).empty]
    if not frames:
        raise SystemExit("no readable sources")
    pool = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True),
                            geometry="geometry", crs="EPSG:4326")
    log.info("pool before filtering: %d parcels", len(pool))

    pool = _measure(pool)
    # Never re-add a parcel the base already holds, and never keep one too small
    # to yield a trustworthy parcel mean.
    pool = pool[~pool.geom_hash.isin(set(base.geom_hash))]
    pool = pool[~pool.geom_hash.duplicated()]
    pool = pool[pool.px_gate_ok]
    log.info("pool after dedupe + pixel gate: %d parcels", len(pool))
    avail = pool.groupby(["Crop_Name", "ecoregion"]).size()
    log.info("candidates available per crop/region:")
    for (crop, reg), n in avail.sort_values(ascending=False).items():
        log.info("    %-11s %-28s %5d", crop, reg, n)

    picked = select(base, pool, rng)
    if picked.empty:
        log.info("nothing to add -- every crop/region already meets quota")
        return 0

    log.info("")
    log.info("=" * 70)
    log.info("SELECTED %d parcels", len(picked))
    log.info("=" * 70)
    opens = picked[picked.opens_region]
    log.info("%d of them lift a crop/region pair from below the %d-parcel "
             "viability floor:", len(opens), MIN_VIABLE_REGION)
    for (crop, reg), grp in opens.groupby(["Crop_Name", "ecoregion"]):
        log.info("    %-11s %-28s %3d -> %4d", crop, reg,
                 int(grp.existing_in_region.iloc[0]),
                 int(grp.existing_in_region.iloc[0]) + len(grp))

    def viable(df: pd.DataFrame) -> pd.Series:
        n = df.groupby(["Crop_Name", "ecoregion"]).size()
        return (n[n >= MIN_VIABLE_REGION].reset_index()
                 .groupby("Crop_Name").ecoregion.nunique())

    both = pd.concat([base[["Crop_Name", "ecoregion"]],
                      picked[["Crop_Name", "ecoregion"]]])
    cmp = pd.DataFrame({"viable_before": viable(base),
                        "viable_after": viable(both)}).fillna(0).astype(int)
    cmp["added"] = picked.groupby("Crop_Name").size().reindex(cmp.index).fillna(0).astype(int)
    log.info("")
    log.info("ecoregions with >= %d parcels, per crop:", MIN_VIABLE_REGION)
    for line in cmp.to_string().splitlines():
        log.info("  %s", line)
    log.info("")
    log.info("crops stuck in a single viable region: %d -> %d",
             int((cmp.viable_before <= 1).sum()), int((cmp.viable_after <= 1).sum()))
    fixed = cmp[(cmp.viable_before <= 1) & (cmp.viable_after >= 2)].index.tolist()
    still = cmp[cmp.viable_after <= 1].index.tolist()
    if fixed:
        log.info("FIXED (now learnable under LOEO): %s", ", ".join(fixed))
    if still:
        log.info("STILL single-region (not fixable from local data): %s",
                 ", ".join(still))

    if a.dry_run:
        log.info("\n--dry-run: nothing written")
        return 0

    keep = ["geom_hash", "Crop_Name", "Date", "win_start", "win_end",
            "area_ha", "area_ha_true", "px10", "core_px10", "px_gate_ok",
            "lat", "lon", "block_id", "ecoregion", "source_file", "geometry"]
    add = picked[keep].copy()
    add["sample_id"] = np.arange(len(add)) + 1_000_000     # disjoint from base
    out = gpd.GeoDataFrame(
        pd.concat([base[["sample_id"] + keep], add[["sample_id"] + keep]],
                  ignore_index=True),
        geometry="geometry", crs="EPSG:4326")
    out.to_parquet(OUT, index=False)
    log.info("\nwrote %s  (%d rows: %d base + %d new)",
             OUT.relative_to(PROJECT), len(out), len(base), len(add))
    log.info("next: python -m src.extract --parcels %s", OUT.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
