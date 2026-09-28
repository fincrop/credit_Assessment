"""
Phase 2b — tier-2 inputs: Sentinel-1, S2 reflectance, AlphaEarth embeddings.

  data/02_cycles_*.parquet -> data/05_extra_series.parquet   (S1 + reflectance)
                              data/05_extra_embeddings.parquet

Extraction is per ATTRIBUTED CYCLE, over [sowing - 15 d, harvest + 15 d] — the
only window a tier-2 feature ever reads — rather than per parcel over the
±400-day survey window stage 2 needed. That is ~6x fewer pixels.

All Earth Engine logic lives in backend `data_acquisition.extra_sources`, the
same module the live classifier calls. Nothing here builds an image.

Cycles are chunked by coarse location then date so each request covers a
compact area and a short span (<= MAX_SPAN_DAYS). Shards are written per chunk
and skipped on restart, so an interrupted run resumes where it stopped.

    python -m src.extract_extra --cycles 02_cycles_season.parquet
    python -m src.extract_extra --cycles 02_cycles_season.parquet --only emb
    python -m src.extract_extra --consolidate-only
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, init_ee, setup_logging

log = setup_logging("extract_extra")

SHARDS = DATA / "shards_extra"
OUT_SERIES = DATA / "05_extra_series.parquet"
OUT_EMB = DATA / "05_extra_embeddings.parquet"

PAD_DAYS = 15
CHUNK_CYCLES = 120
MAX_SPAN_DAYS = 330
EMB_CHUNK = 400
LOC_CELL_DEG = 3.0


def _plan_chunks(cyc: pd.DataFrame) -> List[pd.DataFrame]:
    c = cyc.copy()
    c["lo"] = pd.to_datetime(c["sowing_date"]) - pd.Timedelta(days=PAD_DAYS)
    c["hi"] = pd.to_datetime(c["harvest_date"]) + pd.Timedelta(days=PAD_DAYS)
    c["cell"] = ((c["lat"] // LOC_CELL_DEG).astype(int).astype(str) + "_"
                 + (c["lon"] // LOC_CELL_DEG).astype(int).astype(str))
    chunks: List[pd.DataFrame] = []
    for _, g in c.sort_values(["cell", "lo"]).groupby("cell", sort=True):
        cur: List[int] = []
        lo = hi = None
        for idx, r in g.iterrows():
            nlo = r["lo"] if lo is None else min(lo, r["lo"])
            nhi = r["hi"] if hi is None else max(hi, r["hi"])
            if cur and (len(cur) >= CHUNK_CYCLES or (nhi - nlo).days > MAX_SPAN_DAYS):
                chunks.append(g.loc[cur])
                cur, nlo, nhi = [], r["lo"], r["hi"]
            cur.append(idx)
            lo, hi = nlo, nhi
        if cur:
            chunks.append(g.loc[cur])
    return chunks


def _geoms(chunk: pd.DataFrame, parcels: gpd.GeoDataFrame) -> List[Tuple[str, dict]]:
    out = []
    for r in chunk.itertuples():
        geom = parcels.loc[r.geom_hash, "geometry"]
        out.append((f"{r.geom_hash}|{int(r.cycle_index)}", geom.__geo_interface__))
    return out


def _series_job(i: int, chunk: pd.DataFrame, parcels, blocks: List[str]) -> int:
    import ee
    from data_acquisition.extra_sources import fetch_time_series

    shard = SHARDS / f"series_{i:04d}.parquet"
    if shard.exists():
        return 0
    lo = chunk["lo"].min().date()
    hi = chunk["hi"].max().date()
    res = fetch_time_series(ee, _geoms(chunk, parcels), lo, hi, blocks)
    rows = []
    for key, blk in res.items():
        gh, ci = key.split("|")
        rows.append({"geom_hash": gh, "cycle_index": int(ci),
                     "s1_series": json.dumps(blk.get("s1", [])),
                     "refl_series": json.dumps(blk.get("refl", []))})
    pd.DataFrame(rows).to_parquet(shard, index=False)
    return len(rows)


def _emb_job(i: int, year: int, chunk: pd.DataFrame, parcels) -> int:
    import ee
    from data_acquisition.extra_sources import fetch_embeddings

    shard = SHARDS / f"emb_{year}_{i:04d}.parquet"
    if shard.exists():
        return 0
    res = fetch_embeddings(ee, _geoms(chunk, parcels), year)
    rows = []
    for key, vec in res.items():
        gh, ci = key.split("|")
        rows.append({"geom_hash": gh, "cycle_index": int(ci), "emb_year": year,
                     "embedding": json.dumps(vec)})
    pd.DataFrame(rows).to_parquet(shard, index=False)
    return len(rows)


def consolidate() -> None:
    s = sorted(SHARDS.glob("series_*.parquet"))
    if s:
        df = pd.concat([pd.read_parquet(f) for f in s], ignore_index=True)
        df = df.drop_duplicates(["geom_hash", "cycle_index"])
        df.to_parquet(OUT_SERIES, index=False)
        log.info("wrote %s (%d cycles from %d shards)", OUT_SERIES.name, len(df), len(s))
    e = sorted(SHARDS.glob("emb_*.parquet"))
    if e:
        df = pd.concat([pd.read_parquet(f) for f in e], ignore_index=True)
        df = df.drop_duplicates(["geom_hash", "cycle_index"])
        df.to_parquet(OUT_EMB, index=False)
        log.info("wrote %s (%d cycles from %d shards)", OUT_EMB.name, len(df), len(e))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", default="02_cycles_season.parquet")
    ap.add_argument("--parcels", default="00_parcels_augmented.parquet")
    ap.add_argument("--only", choices=["series", "emb"], default=None)
    ap.add_argument("--blocks", default="s1,refl")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="max chunks (smoke test)")
    ap.add_argument("--consolidate-only", action="store_true")
    a = ap.parse_args()

    SHARDS.mkdir(exist_ok=True)
    if a.consolidate_only:
        consolidate()
        return 0

    init_ee()
    from crop_analysis.extra_features import embedding_year

    cyc = pd.read_parquet(DATA / a.cycles)
    parcels = gpd.read_parquet(DATA / a.parcels).set_index("geom_hash")
    cyc = cyc[cyc["geom_hash"].isin(parcels.index)].reset_index(drop=True)
    log.info("cycles: %d", len(cyc))

    jobs = []
    if a.only in (None, "series"):
        chunks = _plan_chunks(cyc)
        if a.limit:
            chunks = chunks[: a.limit]
        spans = [(c["hi"].max() - c["lo"].min()).days for c in chunks]
        log.info("series chunks: %d  (median span %d d, max %d d)",
                 len(chunks), int(np.median(spans)), max(spans))
        blocks = [b.strip() for b in a.blocks.split(",") if b.strip()]
        jobs += [("series", i, (i, ch, parcels, blocks)) for i, ch in enumerate(chunks)]

    if a.only in (None, "emb"):
        cyc["emb_year"] = [embedding_year(r.sowing_date, r.harvest_date, r.peak_date)
                           for r in cyc.itertuples()]
        n = 0
        for year, g in cyc.groupby("emb_year"):
            for j in range(0, len(g), EMB_CHUNK):
                jobs.append(("emb", n, (j // EMB_CHUNK, int(year), g.iloc[j:j + EMB_CHUNK], parcels)))
                n += 1
        log.info("embedding chunks: %d  years %s", n,
                 dict(cyc["emb_year"].value_counts().sort_index()))

    done = 0
    lock = threading.Lock()
    fails = []
    with ThreadPoolExecutor(max_workers=a.threads) as pool:
        futs = {pool.submit(_series_job if k == "series" else _emb_job, *args): (k, i)
                for k, i, args in jobs}
        for f in as_completed(futs):
            k, i = futs[f]
            try:
                f.result()
            except Exception as exc:                     # noqa: BLE001
                fails.append((k, i, str(exc)[:160]))
                log.warning("%s chunk %d failed: %s", k, i, str(exc)[:160])
            with lock:
                done += 1
                if done % 10 == 0 or done == len(jobs):
                    log.info("  %d/%d chunks  (%d failed)", done, len(jobs), len(fails))

    consolidate()
    if fails:
        log.warning("%d chunks failed — re-run to retry them (completed shards are skipped)",
                    len(fails))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
