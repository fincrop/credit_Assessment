"""
Fused-season extraction for the cloud-robust kharif classifier.

  parcels (labelled) -> data/06_fused_series.parquet

For every labelled parcel whose crop has a kharif (or perennial) season
instance near its survey date: Sentinel-1 VV/VH and cloud-masked Sentinel-2
reflectance on the shared 10-day grid over the WHOLE kharif window
(1 May - 31 Dec of the season year), reduced over the true field polygon.

Two differences from the tier-1 pipeline, both deliberate:

  * The season comes from the crop calendar and the survey date, not from the
    optical cycle detector. Cycle detection is exactly what fails under
    monsoon cloud (132 Marathwada soybean parcels were dropped as
    "cycle_too_few_scenes"); the fused model must learn from those parcels.
  * The full season window is stored, so training can truncate it at any
    as-of date (in-season mode) and the model learns from partial seasons.

The Earth Engine logic is the backend's `data_acquisition.extra_sources`
(the live classifier calls the same function), so training and serving reduce
pixels identically.

    python -m src.extract_fused --dry-run
    python -m src.extract_fused --threads 6
    python -m src.extract_fused --consolidate-only
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import List, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, init_ee, setup_logging

log = setup_logging("extract_fused")

SHARDS = DATA / "shards_fused"
OUT = DATA / "06_fused_series.parquet"
PARCELS = DATA / "00_parcels_mh_full.parquet"
CHUNK = 100
LOC_CELL_DEG = 2.0
PERENNIAL = {"Sugarcane", "Banana", "Grapes"}
KHARIF_NAMES = {"kharif", "late_kharif"}


def season_year(crop: str, survey: date) -> int | None:
    """Kharif season year the label refers to, or None when the crop has no
    kharif instance near the survey (rabi-only labels are not kharif data)."""
    from crop_analysis.crop_calendar import season_instances

    if crop in PERENNIAL:
        return survey.year if survey.month >= 5 else survey.year - 1
    insts = season_instances(crop, survey)
    # Only the season the survey actually refers to (the nearest instance).
    # Taking any kharif instance near the date turned rabi/spring maize labels
    # (Punjab, surveyed in March) into kharif training rows.
    if insts and insts[0].season in KHARIF_NAMES:
        return insts[0].sow_start.year
    return None


def plan(parcels: gpd.GeoDataFrame) -> pd.DataFrame:
    p = parcels[parcels["px_gate_ok"]].copy()
    p["survey"] = pd.to_datetime(p["Date"]).dt.date
    p["season_year"] = [season_year(c, s) for c, s in zip(p["Crop_Name"], p["survey"])]
    p = p[p["season_year"].notna()].copy()
    p["season_year"] = p["season_year"].astype(int)
    p["cell"] = ((p["lat"] // LOC_CELL_DEG).astype(int).astype(str) + "_"
                 + (p["lon"] // LOC_CELL_DEG).astype(int).astype(str))
    return p


def chunks(p: pd.DataFrame) -> List[pd.DataFrame]:
    out = []
    for _, g in p.sort_values(["season_year", "cell", "lat"]).groupby(["season_year", "cell"]):
        for i in range(0, len(g), CHUNK):
            out.append(g.iloc[i:i + CHUNK])
    return out


def _job(i: int, ch: pd.DataFrame) -> int:
    import ee
    from data_acquisition.extra_sources import fetch_time_series

    shard = SHARDS / f"fused_{i:05d}.parquet"
    if shard.exists():
        return 0
    y = int(ch["season_year"].iloc[0])
    geoms: List[Tuple[str, dict]] = [(r.geom_hash, r.geometry.__geo_interface__) for r in ch.itertuples()]
    res = fetch_time_series(ee, geoms, date(y, 5, 1), date(y, 12, 31), ["s1", "refl"])
    rows = []
    meta = ch.set_index("geom_hash")
    for gh, blk in res.items():
        m = meta.loc[gh]
        rows.append({"geom_hash": gh, "Crop_Name": m["Crop_Name"], "season_year": y,
                     "block_id": m["block_id"], "ecoregion": m["ecoregion"],
                     "lat": float(m["lat"]), "lon": float(m["lon"]),
                     "mh_origin": m.get("mh_origin"), "qa_checked": m.get("qa_checked"),
                     "s1_series": json.dumps(blk.get("s1", [])),
                     "refl_series": json.dumps(blk.get("refl", []))})
    pd.DataFrame(rows).to_parquet(shard, index=False)
    return len(rows)


def consolidate() -> None:
    files = sorted(SHARDS.glob("fused_*.parquet"))
    if not files:
        return
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df = df.drop_duplicates(["geom_hash", "season_year"])
    df.to_parquet(OUT, index=False)
    log.info("wrote %s (%d parcel-seasons from %d shards)", OUT.name, len(df), len(files))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--consolidate-only", action="store_true")
    a = ap.parse_args()
    SHARDS.mkdir(exist_ok=True)
    if a.consolidate_only:
        consolidate()
        return 0
    parcels = gpd.read_parquet(PARCELS)
    p = plan(parcels)
    log.info("parcel-seasons: %d  by crop %s", len(p), p.Crop_Name.value_counts().to_dict())
    log.info("season years: %s", p.season_year.value_counts().sort_index().to_dict())
    cs = chunks(p)
    if a.limit:
        cs = cs[: a.limit]
    log.info("chunks: %d", len(cs))
    if a.dry_run:
        return 0
    init_ee()
    done, fails = 0, []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=a.threads) as pool:
        futs = {pool.submit(_job, i, ch): i for i, ch in enumerate(cs)}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as exc:                      # noqa: BLE001
                fails.append((futs[f], str(exc)[:160]))
                log.warning("chunk %d failed: %s", futs[f], str(exc)[:160])
            with lock:
                done += 1
                if done % 10 == 0 or done == len(cs):
                    log.info("  %d/%d chunks (%d failed)", done, len(cs), len(fails))
    consolidate()
    if fails:
        log.warning("%d chunks failed; re-run to retry (finished shards are skipped)", len(fails))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
