"""
Phase 4a — Feature construction.

data/02_cycles.parquet -> data/03_features_tier0.parquet
                          data/03_features_tier1.parquet

TRAIN/SERVE PARITY IS STRUCTURAL HERE
─────────────────────────────────────
The 45 tier-0 features are built by importing `build_feature_dict` from
crop_analysis.crop_detector — the exact function the live pipeline calls. There
is no second implementation to drift from. `extractor_feature_names()` supplies
the column order, and EXTRACTOR_VERSION is stamped into the output so the model
bundle can refuse to load against a different extractor.

TIER 0 vs TIER 1
────────────────
tier0: exactly ML_FEATURE_INDICES x ML_FEATURE_SCENES = 45 columns. Deployable
       with zero pipeline change.
tier1: adds cycle duration, observation-quality scalars, six more index grids
       and the phenology summaries. Requires the matching extractor change in
       crop_detector.py before it can ever be served — hence the fail-fast
       guard added in CropDetector.__init__.

Run:  python -m src.features
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, PROJECT, setup_logging
from .cycles import (
    CYCLE_PADDING_DAYS,
    INTERVAL_DAYS,
    _build_continuous,
)

log = setup_logging("features")

PARCELS = DATA / "00_parcels_clean.parquet"
SCENES = DATA / "01_scenes_raw.parquet"
CYCLES = DATA / "02_cycles.parquet"
OUT_T0 = DATA / "03_features_tier0.parquet"
OUT_T1 = DATA / "03_features_tier1.parquet"

# Extra index grids for tier 1. Chosen for what they discriminate, not for
# completeness — see classification_model.md E.3(b).
TIER1_EXTRA_INDICES = (
    "NDRE_mean",    # chlorophyll / N status — the Wheat<->Mustard hope
    "PSRI_mean",    # senescence timing — sharp-harvest crops vs gradual
    "kNDVI_mean",   # saturation-resistant at dense canopy (Sugarcane, Banana)
    "LSWI_mean",    # surface water — rice flooding signature
    "GCVI_mean",    # green chlorophyll, tracks LAI / biomass
    "NDVI_std",     # within-parcel heterogeneity — orchard rows vs broadcast
)

# Cycle scalars carried straight through from CropCycle.
TIER1_SCALARS = [
    "duration_days", "n_scenes_real", "observed_fraction", "peak_observed",
    "peak_ndvi", "baseline_ndvi", "ndvi_rise", "integral_ndvi_days",
    "peak_evi", "peak_ndmi", "cycle_confidence",
]

# Columns that must NEVER become features. Kept alongside the features for
# grouping, weighting and auditing only. See classification_model.md G.1/G.3.
META_COLS = [
    "geom_hash", "sample_id", "Crop_Name", "block_id", "ecoregion",
    "lat", "lon", "area_ha", "attribution", "duration_outlier", "kind_mismatch",
    "cycle_kind", "season_type", "sowing_date", "harvest_date", "survey_date",
    "n_cycles_detected", "land_cover_class",
]


def _slice_scenes(continuous: Dict, sowing: str, harvest: str) -> List[Dict]:
    """Real observations in [sowing - pad, harvest + pad] — the exact window
    CropDetector._collect_scenes_between uses at inference time."""
    s = datetime.strptime(sowing, "%Y-%m-%d").date()
    h = datetime.strptime(harvest, "%Y-%m-%d").date()
    lo = (s - timedelta(days=CYCLE_PADDING_DAYS)).isoformat()
    hi = (h + timedelta(days=CYCLE_PADDING_DAYS)).isoformat()
    return [
        sc for sc in continuous["scenes"]
        if not sc.get("missing") and lo <= sc["date"] <= hi
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geom-kind", default="bbox", choices=["bbox", "poly"])
    args = ap.parse_args()

    for f in (PARCELS, SCENES, CYCLES):
        if not f.exists():
            log.error("missing %s — run the earlier stages first", f)
            return 1

    from data_acquisition.satellite_collector import SatelliteDataCollector
    from crop_analysis.crop_detector import (
        EXTRACTOR_VERSION,
        build_feature_dict,
        extractor_feature_names,
    )
    from config import PipelineConfig

    index_keys = SatelliteDataCollector.INDEX_KEYS
    t0_names = extractor_feature_names()
    log.info("extractor: %s  |  tier-0 features: %d", EXTRACTOR_VERSION, len(t0_names))
    log.info("  ML_FEATURE_SCENES=%d  ML_FEATURE_INDICES=%s",
             PipelineConfig.ML_FEATURE_SCENES,
             list(PipelineConfig.ML_FEATURE_INDICES))

    parcels = gpd.read_parquet(PARCELS).set_index("geom_hash")
    scenes = pd.read_parquet(SCENES)
    scenes = scenes[scenes["geom_kind"] == args.geom_kind]
    cycles = pd.read_parquet(CYCLES)
    log.info("cycles to featurise: %d", len(cycles))

    scene_groups = dict(tuple(scenes.groupby("geom_hash")))

    from .extract import global_bin_anchor
    anchor = global_bin_anchor(gpd.read_parquet(PARCELS))

    rows_t0: List[Dict[str, Any]] = []
    rows_t1: List[Dict[str, Any]] = []
    n_skip = 0

    for i, (_, c) in enumerate(cycles.iterrows(), 1):
        gh = c["geom_hash"]
        if gh not in scene_groups:
            n_skip += 1
            continue

        p = parcels.loc[gh]
        continuous = _build_continuous(
            pd.Series({
                "win_start": p["win_start"], "win_end": p["win_end"],
                "area_ha": p["area_ha"], "geom_hash": gh,
            }),
            scene_groups[gh], index_keys, anchor,
        )
        if continuous is None:
            n_skip += 1
            continue

        cyc_scenes = _slice_scenes(continuous, c["sowing_date"], c["harvest_date"])
        if len(cyc_scenes) < 5:
            n_skip += 1
            continue

        # CropDetector sorts by date before building features; do the same.
        cyc_scenes = sorted(cyc_scenes, key=lambda s: s.get("date", ""))

        meta = {k: c[k] for k in META_COLS if k in c}

        # ── tier 0: the production contract, byte for byte ─────────────────
        fd0 = build_feature_dict(cyc_scenes)
        rows_t0.append({**meta, **{k: fd0[k] for k in t0_names}})

        # ── tier 1: + duration, quality, extra grids, phenology ───────────
        fd1 = dict(fd0)
        fd1.update(build_feature_dict(cyc_scenes, indices=TIER1_EXTRA_INDICES))
        for k in TIER1_SCALARS:
            fd1[k] = float(c[k]) if k in c else 0.0
        # Duration spans 65->365 days; the log makes that range linear for a
        # tree's split points and keeps the raw value available too.
        fd1["log_duration"] = float(np.log1p(max(float(c["duration_days"]), 0.0)))
        rows_t1.append({**meta, **fd1})

        if i % 500 == 0 or i == len(cycles):
            log.info("  %d/%d  built=%d skipped=%d", i, len(cycles),
                     len(rows_t0), n_skip)

    if not rows_t0:
        log.error("no feature rows built")
        return 1

    df0 = pd.DataFrame(rows_t0)
    df1 = pd.DataFrame(rows_t1)

    # Fail loudly if the tier-0 column set ever drifts from the live extractor:
    # a silently different column set is precisely the zero-fill trap.
    feat0 = [c for c in df0.columns if c not in META_COLS]
    assert feat0 == t0_names, (
        f"tier-0 columns drifted from extractor_feature_names(): "
        f"{set(feat0) ^ set(t0_names)}"
    )

    df0.to_parquet(OUT_T0, index=False)
    df1.to_parquet(OUT_T1, index=False)

    log.info("")
    log.info("=" * 68)
    log.info("wrote %s  (%d rows, %d features)",
             OUT_T0.relative_to(PROJECT), len(df0), len(feat0))
    log.info("wrote %s  (%d rows, %d features)",
             OUT_T1.relative_to(PROJECT), len(df1),
             len([c for c in df1.columns if c not in META_COLS]))
    log.info("  skipped: %d", n_skip)
    log.info("")
    log.info("class balance after attribution:")
    vc = df0["Crop_Name"].value_counts().sort_index()
    for k, v in vc.items():
        log.info("  %-12s %4d", k, v)
    log.info("  imbalance ratio max/min: %.2f", vc.max() / max(vc.min(), 1))
    log.info("  blocks: %d  |  ecoregions: %d",
             df0["block_id"].nunique(), df0["ecoregion"].nunique())
    log.info("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
