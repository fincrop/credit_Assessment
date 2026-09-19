"""
Rebuild the full pipeline on the augmented parcel set and measure the change.

Runs the stages that come after extraction, against `00_parcels_augmented`
instead of `00_parcels_clean`, writing to `*_aug` outputs so the shipped
artifacts stay untouched and the two can be compared directly.

Why a separate driver rather than flags on each stage: the augmented run needs
the scene tables merged first. Shard tags key on a parcel's POSITIONAL index
(see `extract._run_extraction`), so the 1,163 new parcels had to be extracted as
their own batch into `shards_aug/`; appending them to the base parcel file would
have re-chunked every bin and invalidated all 752 cached shards. The merge here
is what makes that split invisible downstream.

    python -m src.run_augmented            # merge -> cycles -> features -> weather
    python -m src.run_augmented --measure  # ...then LOEO, augmented vs shipped
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

from . import _bootstrap  # noqa: F401

log = logging.getLogger("run_augmented")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PY = sys.executable

SCENES_BASE = DATA / "01_scenes_raw.parquet"
SCENES_NEW = DATA / "01_scenes_new.parquet"
SCENES_AUG = DATA / "01_scenes_augmented.parquet"


def _run(args: list[str]) -> None:
    log.info("$ %s", " ".join(args[2:]))
    r = subprocess.run([PY, "-u", *args[1:]], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"stage failed ({r.returncode}): {' '.join(args[1:])}")


def merge_scenes() -> None:
    for f in (SCENES_BASE, SCENES_NEW):
        if not f.exists():
            raise SystemExit(f"missing {f}")
    base = pd.read_parquet(SCENES_BASE)
    new = pd.read_parquet(SCENES_NEW)
    # The base run extracted a 25% polygon-comparison subset; the augmentation
    # batch ran with --poly-fraction 0, so only bbox rows exist for it. Both are
    # kept -- cycles.py filters to one geom_kind anyway.
    out = pd.concat([base, new], ignore_index=True)
    before = len(out)
    out = out.drop_duplicates(subset=["geom_hash", "geom_kind", "bin_start"])
    out.to_parquet(SCENES_AUG, index=False)
    log.info("merged scenes: %d base + %d new = %d rows (%d dupes dropped), "
             "%d parcels", len(base), len(new), len(out), before - len(out),
             out.geom_hash.nunique())


def measure() -> None:
    """LOEO on the augmented features, against the shipped set."""
    import numpy as np
    from .crossregion import RECIPES, loeo, replication

    rows = []
    for tag, path in (("shipped", "03_features_tier1.parquet"),
                      ("augmented", "03_features_tier1_aug.parquet")):
        f = DATA / path
        if not f.exists():
            log.warning("skipping %s -- %s missing", tag, path)
            continue
        df = pd.read_parquet(f)
        classes = sorted(df.Crop_Name.unique())
        y = np.array([classes.index(c) for c in df.Crop_Name])
        eco = df.ecoregion.to_numpy()

        rep = replication(y, eco, classes)
        single = [c for c, v in rep.items() if v["n_regions"] == 1]
        log.info("\n=== %s: %d cycles, %d single-region crops %s",
                 tag, len(df), len(single), single)

        for recipe in ("baseline", "weather_thermal"):
            if recipe == "weather_thermal" and tag == "augmented" and not (
                    DATA / "04_weather_cycle_aug.parquet").exists():
                log.warning("  skipping weather_thermal -- no augmented weather")
                continue
            X, _ = RECIPES[recipe](df)
            r = loeo(X, y, eco, classes, season=df.season_type.to_numpy())
            rows.append({
                "set": tag, "recipe": recipe, "n": len(df),
                "loeo": r["balanced_accuracy"],
                "learnable": r["learnable_subset"]["balanced_accuracy"],
                "season": r["season_masked"]["balanced_accuracy"],
                "coarse": r["coarse_functional"]["balanced_accuracy"],
                "single_region_classes": r["n_single_region_classes"],
            })
            log.info("  %-16s LOEO %.4f  learnable %.4f  +season %.4f",
                     recipe, r["balanced_accuracy"],
                     r["learnable_subset"]["balanced_accuracy"],
                     r["season_masked"]["balanced_accuracy"])

    if rows:
        t = pd.DataFrame(rows)
        log.info("\n%s", t.to_string(index=False))
        t.to_csv(ROOT / "reports" / "augmented_comparison.csv", index=False)
        log.info("wrote reports/augmented_comparison.csv")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measure", action="store_true",
                    help="also run the LOEO comparison at the end")
    ap.add_argument("--skip-build", action="store_true",
                    help="only measure; assume the stages already ran")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    if not a.skip_build:
        merge_scenes()
        _run(["py", "-m", "src.cycles",
              "--parcels", "00_parcels_augmented.parquet",
              "--scenes", "01_scenes_augmented.parquet",
              "--out", "02_cycles_aug.parquet"])
        _run(["py", "-m", "src.features",
              "--parcels", "00_parcels_augmented.parquet",
              "--scenes", "01_scenes_augmented.parquet",
              "--cycles", "02_cycles_aug.parquet",
              "--suffix", "_aug"])
        # New parcels sit in regions the POWER grid has never been fetched for,
        # so this pulls only the missing cells -- fetch_daily resumes from the
        # existing 253-cell cache.
        _run(["py", "-m", "src.weather", "--fetch", "--build",
              "--cycles", "02_cycles_aug.parquet",
              "--out", "04_weather_cycle_aug.parquet"])

    if a.measure:
        measure()
    return 0


if __name__ == "__main__":
    sys.exit(main())
