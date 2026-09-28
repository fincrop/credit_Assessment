"""
Phase 4a' — tier-2 feature table.

  03_features_tier1<suffix>.parquet      (built by src.features on the same cycles)
  05_extra_series.parquet                (src.extract_extra: S1 + reflectance)
  05_extra_embeddings.parquet            (src.extract_extra: AlphaEarth)
  04_weather_daily.parquet               (src.weather --fetch: NASA POWER cells)
        -> 03_features_tier2<suffix>.parquet

Every tier-2 column is computed by backend `crop_analysis.extra_features` — the
module the live classifier calls — so train/serve parity is structural.

A block whose input is missing for a cycle is NaN, never zero (0 dB VH and 0
reflectance are real values). XGBoost learns a missing-value branch for them,
which is also what happens in production when, say, POWER is unreachable.

    python -m src.features_extra --cycles 02_cycles_season.parquet --suffix _season
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from ._bootstrap import DATA, setup_logging

log = setup_logging("features_extra")


def _weather_block(cyc: pd.DataFrame) -> pd.DataFrame:
    from crop_analysis.extra_features import add_climatology, build_weather_features
    from .weather import DAILY_PATH, _cell

    daily = pd.read_parquet(DAILY_PATH)
    by_cell = {k: add_climatology(g) for k, g in daily.groupby(["cell_lat", "cell_lon"])}
    rows = []
    for r in cyc.itertuples():
        d = by_cell.get(_cell(r.lat, r.lon))
        f = build_weather_features(d, r.sowing_date, r.harvest_date, r.peak_date)
        rows.append({"geom_hash": r.geom_hash, "cycle_index": r.cycle_index, **f})
    out = pd.DataFrame(rows)
    log.info("weather: %.1f%% of cycles have a thermal-time block",
             100 * out["gdd_total"].notna().mean())
    return out


def _series_blocks(cyc: pd.DataFrame) -> pd.DataFrame:
    from crop_analysis.extra_features import (
        build_reflectance_features, build_s1_features, refl_feature_names, s1_feature_names,
    )
    p = DATA / "05_extra_series.parquet"
    if not p.exists():
        log.warning("no %s — S1/reflectance blocks will be NaN", p.name)
        return pd.DataFrame(columns=["geom_hash", "cycle_index"])
    ser = pd.read_parquet(p).set_index(["geom_hash", "cycle_index"])
    rows = []
    nan_s1 = {k: np.nan for k in s1_feature_names()}
    nan_rf = {k: np.nan for k in refl_feature_names()}
    for r in cyc.itertuples():
        key = (r.geom_hash, int(r.cycle_index))
        rec = {"geom_hash": r.geom_hash, "cycle_index": r.cycle_index}
        if key in ser.index:
            s = ser.loc[key]
            rec.update(build_s1_features(json.loads(s["s1_series"]), r.sowing_date, r.harvest_date))
            rec.update(build_reflectance_features(json.loads(s["refl_series"]),
                                                  r.sowing_date, r.harvest_date, r.peak_date))
        else:
            rec.update(nan_s1)
            rec.update(nan_rf)
        rows.append(rec)
    out = pd.DataFrame(rows)
    log.info("S1: median %.0f obs/cycle; reflectance: median %.0f obs/cycle",
             out["S1_n_obs"].median(), out["R_n_obs"].median())
    return out


def _embedding_block(cyc: pd.DataFrame) -> pd.DataFrame:
    from crop_analysis.extra_features import build_embedding_features, embedding_feature_names
    p = DATA / "05_extra_embeddings.parquet"
    if not p.exists():
        log.warning("no %s — embedding block will be NaN", p.name)
        return pd.DataFrame(columns=["geom_hash", "cycle_index"])
    emb = pd.read_parquet(p).set_index(["geom_hash", "cycle_index"])
    rows = []
    for r in cyc.itertuples():
        key = (r.geom_hash, int(r.cycle_index))
        vec = json.loads(emb.loc[key, "embedding"]) if key in emb.index else None
        rows.append({"geom_hash": r.geom_hash, "cycle_index": r.cycle_index,
                     **build_embedding_features(vec)})
    out = pd.DataFrame(rows)
    log.info("embeddings: %.1f%% present", 100 * out["EMB00"].notna().mean())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", default="02_cycles_season.parquet")
    ap.add_argument("--suffix", default="_season")
    a = ap.parse_args()

    cyc = pd.read_parquet(DATA / a.cycles)
    t1 = pd.read_parquet(DATA / f"03_features_tier1{a.suffix}.parquet")
    keys = ["geom_hash", "sowing_date"]
    # tier-1 already carries some cycle bookkeeping as META columns; only
    # bring over what it lacks, or the merge would suffix cycle_index_x/_y.
    want = [c for c in ("cycle_index", "peak_date", "label_season",
                        "season_consistent", "attribution_mode") if c not in t1.columns]
    base = t1.merge(cyc[keys + want], on=keys, how="left") if want else t1
    log.info("tier-1 rows: %d  (cycle_index matched %.1f%%)", len(base),
             100 * base["cycle_index"].notna().mean())
    cyc = cyc[cyc["geom_hash"].isin(base["geom_hash"])]

    out = base
    for blk in (_weather_block(cyc), _series_blocks(cyc), _embedding_block(cyc)):
        if len(blk.columns) > 2:
            out = out.merge(blk, on=["geom_hash", "cycle_index"], how="left")
    dst = DATA / f"03_features_tier2{a.suffix}.parquet"
    out.to_parquet(dst, index=False)
    log.info("wrote %s  (%d rows, %d cols)", dst.name, len(out), len(out.columns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
