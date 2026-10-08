"""
Small end-to-end proof inputs for `python -m src.run_mh --dev`.

Builds, in a scratch directory (never data/):
  * a parcel subset: up to N training rows per class + every held-out row that
    already has scenes (existing tur and the existing duplicates of new
    Marathwada polygons) + the parcels in the 2-parcel extraction smoke file;
  * the matching subset of 01_scenes_augmented and the smoke scenes as the
    "new" batch;
  * a dev split: the real split restricted to the subset, with the existing
    duplicates of new cotton/soy polygons promoted to test rows so the test
    report has cotton and soybean in it. Training exclusions are the real ones.

The numbers a dev run prints mean nothing; it proves the driver runs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ._bootstrap import DATA

DEFAULT_SCRATCH = Path(os.environ.get(
    "RUN_MH_DEV_DIR",
    "C:/Users/gopik/AppData/Local/Temp/claude/C--Users-gopik-Downloads-agri-credit-pipeline/"
    "b660b693-0bb7-4011-a1b7-0facdfa3d8bc/scratchpad/dev_mh"))
SMOKE = DEFAULT_SCRATCH.parent / "scenes_smoke.parquet"


def prepare_dev(a) -> None:
    root = Path(a.workdir) if a.workdir else DEFAULT_SCRATCH
    root.mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    split = json.loads((DATA / "splits" / "mh2023_split.json").read_text(encoding="utf-8"))
    excl, test = set(split["excluded_from_training"]), set(split["test_geom_hashes"])

    full = gpd.read_parquet(DATA / "00_parcels_mh_full.parquet")
    smoke = pd.read_parquet(SMOKE)
    train = full[~full.geom_hash.isin(excl | test) & (full.mh_origin != "new")]
    pick = (train.groupby("Crop_Name", group_keys=False)
                 .apply(lambda d: d.sample(min(len(d), a.dev_per_class), random_state=0)))
    held = full[full.geom_hash.isin(excl) & (full.mh_origin != "new")]
    new = full[full.geom_hash.isin(set(smoke.geom_hash))]
    sub = gpd.GeoDataFrame(pd.concat([pick, held, new]), geometry="geometry", crs=full.crs)
    sub = sub[~sub.geom_hash.duplicated()]
    sub.to_parquet(root / "00_parcels_dev.parquet", index=False)

    keep = set(sub.geom_hash)
    base = pd.read_parquet(DATA / "01_scenes_augmented.parquet")
    base[base.geom_hash.isin(keep)].to_parquet(root / "01_scenes_base_dev.parquet", index=False)

    dev_test = (test & keep) | set(held[held.mh_origin == "existing_dup_of_mh"].geom_hash) \
        | set(new.geom_hash)
    dsplit = {
        "version": "dev_" + split["version"],
        "block_deg": split["block_deg"],
        "test_blocks": split["test_blocks"],
        "test_geom_hashes": sorted(dev_test),
        "excluded_from_training": sorted((excl & keep) | dev_test),
        "counts": sub[sub.geom_hash.isin(dev_test)].Crop_Name.value_counts().to_dict(),
        "note": "DEV split for pipeline proof only",
    }
    (root / "split_dev.json").write_text(json.dumps(dsplit, indent=1), encoding="utf-8")

    a.workdir = str(root)
    a.reports = a.reports or str(root / "reports")
    a.parcels = str(root / "00_parcels_dev.parquet")
    a.scenes_base = str(root / "01_scenes_base_dev.parquet")
    a.scenes_new = str(SMOKE)
    a.split = str(root / "split_dev.json")
    a.model_out = a.model_out or str(root / "crop_classifier_tier1_mh_dev.joblib")
    print(f"dev inputs in {root}: {len(sub)} parcels, test {dsplit['counts']}")
