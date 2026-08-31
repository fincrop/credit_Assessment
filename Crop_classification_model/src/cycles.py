"""
Phase 3 — Cycle detection & label attribution.

data/01_scenes_raw.parquet -> data/02_cycles.parquet + data/rejections.csv

THE BRIDGE. Our labels are attached to POLYGONS; the pipeline classifies
DETECTED CYCLES. This is where the two are joined, and it is the highest-risk
stage in the project: a wrong attribution puts a wrong label on a trajectory,
which is worse than having no model at all.

The whole stage runs production code — the same `_build_composite_signal`,
`classify_land_cover` and `CropCycleDetector` the pipeline runs — so a cycle
here is the same object Stage 4 would classify in production.

REJECTIONS ARE A DELIVERABLE, NOT A LOSS
────────────────────────────────────────
Every dropped parcel is written to rejections.csv with a reason. If a class is
mostly rejected for `no_cycle_detected`, that is not a data problem: it is a
finding about CropCycleDetector on that crop, and it belongs in front of
Stage 3's owners rather than silently reducing our sample.

Run:  python -m src.cycles
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, PROJECT, REPORTS, setup_logging

log = setup_logging("cycles")

PARCELS = DATA / "00_parcels_clean.parquet"
SCENES = DATA / "01_scenes_raw.parquet"
OUT = DATA / "02_cycles.parquet"
REJECTS = DATA / "rejections.csv"

INTERVAL_DAYS = 10
MIN_OBS_FOR_DETECTOR = 10        # CropCycleDetector's own floor
MIN_SCENES_PER_CYCLE = 5         # PipelineConfig.MIN_OBSERVATIONS_PER_SEASON
CYCLE_PADDING_DAYS = 5           # CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS

# Attribution tolerances (classification_model.md D.3)
NEAREST_TOLERANCE_DAYS = 90
DURATION_OUTLIER_SLACK = 0.50

PERENNIAL_CROPS = {"Banana", "Sugarcane", "Grapes"}


# =============================================================================
# rebuild `continuous_data` exactly as the GEE collector path produces it
# =============================================================================
def _build_continuous(
    parcel: pd.Series,
    bins: pd.DataFrame,
    index_keys: Tuple[str, ...],
    anchor: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """
    Assemble the scene grid the pipeline would see.

    Mirrors satellite_collector's GEE branch: one entry per 10-day bin, and a
    bin whose NDVI is not finite becomes a `missing: True` placeholder with a
    full NaN index bundle (which is what downstream imputation expects).
    """
    from data_acquisition.satellite_collector import SatelliteDataCollector

    d0 = datetime.strptime(parcel["win_start"], "%Y-%m-%d").date()
    d1 = datetime.strptime(parcel["win_end"], "%Y-%m-%d").date()

    by_bin = {r["bin_start"]: r for _, r in bins.iterrows()}

    # Bins MUST come off the shared extraction grid, not this parcel's own
    # win_start — see extract.global_bin_anchor. Falling back to a local grid
    # is only safe when there is no extracted data to align with.
    if anchor is not None:
        from .extract import bins_for_window
        grid = bins_for_window(anchor, parcel["win_start"], parcel["win_end"])
    else:
        grid, cur = [], d0
        while cur <= d1:
            grid.append(cur)
            cur = cur + timedelta(days=INTERVAL_DAYS)

    scenes: List[Dict[str, Any]] = []
    for cur in grid:
        key = cur.isoformat()
        rec = by_bin.get(key)
        ndvi = np.nan if rec is None else pd.to_numeric(rec.get("NDVI_mean"),
                                                        errors="coerce")
        if rec is None or not np.isfinite(ndvi):
            scenes.append({
                "date": key,
                "missing": True,
                "cloud_cover": None,
                "indices": SatelliteDataCollector._nan_index_bundle(),
                "bands_available": [],
            })
        else:
            indices = {}
            for k in index_keys:
                v = pd.to_numeric(rec.get(k), errors="coerce")
                indices[k] = float(v) if np.isfinite(v) else np.nan
            scenes.append({
                "date": key,
                "missing": False,
                "cloud_cover": None,
                "indices": indices,
                "bands_available": ["B02", "B03", "B04", "B05", "B06", "B08", "B11"],
            })

    n_ok = sum(1 for s in scenes if not s["missing"])
    if n_ok == 0:
        # Grid misalignment presents exactly like "no data": every lookup misses
        # and the parcel is rejected. Distinguish the two, loudly, because the
        # silent version cost a whole dry run to notice.
        n_usable_rows = int(
            np.isfinite(pd.to_numeric(bins["NDVI_mean"], errors="coerce")).sum()
        ) if "NDVI_mean" in bins else 0
        if n_usable_rows > 0:
            raise RuntimeError(
                f"bin-grid misalignment for {parcel['geom_hash']}: shard holds "
                f"{n_usable_rows} usable observation(s) but none landed on the "
                f"grid built for [{parcel['win_start']}..{parcel['win_end']}]. "
                f"Shard bins e.g. {sorted(by_bin)[:3]}; grid e.g. "
                f"{[g.isoformat() for g in grid[:3]]}."
            )
        return None

    continuous = {
        "scenes": scenes,
        "dates": [s["date"] for s in scenes],
        "ndvi_values": [s["indices"].get("NDVI_mean", np.nan) for s in scenes],
        "evi_values": [s["indices"].get("EVI_mean", np.nan) for s in scenes],
        "ndmi_values": [s["indices"].get("NDMI_mean", np.nan) for s in scenes],
        "interval_days": INTERVAL_DAYS,
        "start_date": d0.isoformat(),
        "end_date": d1.isoformat(),
        "total_days": (d1 - d0).days,
        "valid_observations": n_ok,
    }

    # Pillar 1: build the composite detection signal (VS_mean) with production's
    # own classmethod. Without it CropCycleDetector silently falls back to its
    # internal CVI blend and would detect DIFFERENT cycles than production.
    try:
        continuous = SatelliteDataCollector._build_composite_signal(
            continuous, field_area_ha=float(parcel["area_ha"])
        )
    except Exception as exc:                            # noqa: BLE001
        log.debug("composite signal failed for %s: %s", parcel["geom_hash"], exc)

    return continuous


# =============================================================================
# label attribution (classification_model.md D.3)
# =============================================================================
def _cycle_window(cyc) -> Tuple[date, date]:
    return cyc.sowing_date.date(), cyc.harvest_date.date()


def _attribute(cycles: List, survey: date, crop: str) -> Tuple[Optional[int], str]:
    """
    Which detected cycle does the label refer to?

    `Date` is a survey snapshot, not a season boundary, and is near-constant
    within a class — so it is a loose anchor, never a sowing date.

    Returns (index into cycles, attribution tag) or (None, rejection reason).
    The tag is prefixed 'kindmismatch_' when the chosen cycle's cycle_kind
    disagrees with the label's expected kind. That USED to be a hard rejection;
    on real data it cost Banana every one of its usable parcels, because a
    330-day stand the detector calls 'annual' is still the right cycle. The
    disagreement is now recorded rather than acted on.
    """
    from config import CropGrowthCurves

    if not cycles:
        return None, "no_cycle_detected"

    is_perennial_label = crop in PERENNIAL_CROPS
    ref = CropGrowthCurves.CROP_DURATIONS.get(crop, {})
    typical = float(ref.get("typical_days", 120))

    def _pick(idxs: List[int]) -> Tuple[Optional[int], str]:
        """Choose among cycles that all contain / all neighbour the survey."""
        if not idxs:
            return None, ""
        if len(idxs) == 1:
            return idxs[0], "contains"
        # Break ties on agronomic duration rather than arbitrarily.
        best = min(idxs, key=lambda i: abs(cycles[i].duration_days - typical))
        return best, "duration_matched"

    # Two preference tiers: kind-matching cycles first, everything else second.
    for tier, want_match in enumerate((True, False)):
        containing, nearest = [], []
        for i, c in enumerate(cycles):
            kind = str(getattr(c, "cycle_kind", "annual")).lower()
            matches = (kind == "perennial") == is_perennial_label
            if matches != want_match:
                continue
            s, h = _cycle_window(c)
            if s <= survey <= h:
                containing.append(i)
            else:
                gap = min(abs((survey - s).days), abs((survey - h).days))
                if gap <= NEAREST_TOLERANCE_DAYS:
                    nearest.append((gap, i))

        idx, tag = _pick(containing)
        if idx is None and nearest:
            nearest.sort()
            # Only accept a neighbouring cycle if it is unambiguously closest.
            if len(nearest) == 1 or nearest[0][0] < nearest[1][0]:
                idx, tag = nearest[0][1], "nearest"

        if idx is not None:
            return idx, tag if want_match else f"kindmismatch_{tag}"

    return None, "no_cycle_near_survey_date"


def _duration_outlier(cyc, crop: str) -> bool:
    from config import CropGrowthCurves

    ref = CropGrowthCurves.CROP_DURATIONS.get(crop)
    if not ref:
        return False
    lo = float(ref["min_days"]) * (1 - DURATION_OUTLIER_SLACK)
    hi = float(ref["max_days"]) * (1 + DURATION_OUTLIER_SLACK)
    return not (lo <= float(cyc.duration_days) <= hi)


# =============================================================================
# driver
# =============================================================================
def _cycle_scene_slice(continuous: Dict, cyc) -> List[Dict]:
    """
    The scenes Stage 4 would classify: real observations inside
    [sowing - pad, harvest + pad]. Placeholders are excluded, exactly as
    CropDetector._collect_scenes_between does.
    """
    s, h = _cycle_window(cyc)
    lo = (s - timedelta(days=CYCLE_PADDING_DAYS)).isoformat()
    hi = (h + timedelta(days=CYCLE_PADDING_DAYS)).isoformat()
    return [
        sc for sc in continuous["scenes"]
        if not sc.get("missing") and lo <= sc["date"] <= hi
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geom-kind", default="bbox",
                    choices=["bbox", "poly"],
                    help="bbox = production parity (default)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    for f in (PARCELS, SCENES):
        if not f.exists():
            log.error("missing %s", f)
            return 1

    from data_acquisition.satellite_collector import SatelliteDataCollector
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.land_cover_gate import classify_land_cover
    from utils.india_geo_context import infer_agro_ecoregion

    index_keys = SatelliteDataCollector.INDEX_KEYS

    parcels = gpd.read_parquet(PARCELS)
    scenes = pd.read_parquet(SCENES)
    scenes = scenes[scenes["geom_kind"] == args.geom_kind]
    log.info("parcels: %d  |  scene rows (%s): %d",
             len(parcels), args.geom_kind, len(scenes))

    have = set(scenes["geom_hash"].unique())
    parcels = parcels[parcels["geom_hash"].isin(have)]
    if args.limit:
        parcels = parcels.head(args.limit)
    log.info("parcels with extracted data: %d", len(parcels))

    scene_groups = dict(tuple(scenes.groupby("geom_hash")))

    # Anchor comes from the FULL parcel set, exactly as extraction computed it —
    # deriving it from a filtered subset would shift the grid.
    from .extract import global_bin_anchor
    anchor = global_bin_anchor(gpd.read_parquet(PARCELS))
    log.info("shared bin-grid anchor: %s", anchor.isoformat())

    detector = CropCycleDetector()

    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    reasons: Counter = Counter()

    for i, (_, p) in enumerate(parcels.iterrows(), 1):
        gh = p["geom_hash"]
        base = {
            "geom_hash": gh, "sample_id": int(p["sample_id"]),
            "Crop_Name": p["Crop_Name"], "block_id": p["block_id"],
            "ecoregion": p["ecoregion"], "lat": p["lat"], "lon": p["lon"],
            "area_ha": p["area_ha"], "core_px10": p["core_px10"],
        }

        if not bool(p["px_gate_ok"]):
            rejected.append({**base, "reason": "too_few_core_pixels"})
            reasons["too_few_core_pixels"] += 1
            continue

        continuous = _build_continuous(p, scene_groups[gh], index_keys, anchor)
        if continuous is None:
            rejected.append({**base, "reason": "no_valid_observations"})
            reasons["no_valid_observations"] += 1
            continue

        n_obs = int(continuous["valid_observations"])
        if n_obs < MIN_OBS_FOR_DETECTOR:
            rejected.append({**base, "reason": "insufficient_observations",
                             "n_obs": n_obs})
            reasons["insufficient_observations"] += 1
            continue

        _, agro_profile = infer_agro_ecoregion(float(p["lat"]), float(p["lon"]))

        # ── land-cover gate: is this farmland at all? ──────────────────────
        try:
            lc = classify_land_cover(
                continuous,
                field_area_ha=float(p["area_ha"]),
                location={"latitude": float(p["lat"]), "longitude": float(p["lon"])},
                agro_profile=agro_profile,
                registry_crop=p["Crop_Name"],
            )
        except Exception as exc:                        # noqa: BLE001
            lc = {"outcome": "error", "reason": str(exc)[:120], "class": None}

        if lc.get("outcome") == "reject":
            rejected.append({**base, "reason": "land_cover_fail",
                             "detail": f"{lc.get('class')}: {lc.get('reason','')[:90]}"})
            reasons["land_cover_fail"] += 1
            continue

        # ── cycle detection ───────────────────────────────────────────────
        try:
            cycles = detector.detect_cycles(
                dates=continuous["dates"],
                ndvi_values=continuous["ndvi_values"],
                evi_values=continuous["evi_values"],
                ndmi_values=continuous["ndmi_values"],
                scenes=continuous["scenes"],
                grid_step_days=INTERVAL_DAYS,
                agro_profile=agro_profile,
            )
        except Exception as exc:                        # noqa: BLE001
            rejected.append({**base, "reason": "cycle_detector_error",
                             "detail": str(exc)[:120]})
            reasons["cycle_detector_error"] += 1
            continue

        survey = pd.to_datetime(p["Date"]).date()
        idx, tag = _attribute(cycles, survey, p["Crop_Name"])
        if idx is None:
            rejected.append({**base, "reason": tag, "n_cycles": len(cycles)})
            reasons[tag] += 1
            continue

        cyc = cycles[idx]
        cyc_scenes = _cycle_scene_slice(continuous, cyc)
        if len(cyc_scenes) < MIN_SCENES_PER_CYCLE:
            rejected.append({**base, "reason": "cycle_too_few_scenes",
                             "n_scenes": len(cyc_scenes)})
            reasons["cycle_too_few_scenes"] += 1
            continue

        s, h = _cycle_window(cyc)
        kept.append({
            **base,
            "attribution": tag,
            "kind_mismatch": tag.startswith("kindmismatch_"),
            "n_cycles_detected": len(cycles),
            "cycle_index": idx,
            "sowing_date": s.isoformat(),
            "harvest_date": h.isoformat(),
            "peak_date": cyc.peak_date.date().isoformat(),
            "duration_days": int(cyc.duration_days),
            "cycle_kind": str(getattr(cyc, "cycle_kind", "annual")),
            "season_type": str(getattr(cyc, "season_type", "") or ""),
            "season_label": str(getattr(cyc, "season_label", "") or ""),
            "peak_ndvi": float(cyc.peak_ndvi),
            "baseline_ndvi": float(cyc.baseline_ndvi),
            "ndvi_rise": float(cyc.ndvi_rise),
            "integral_ndvi_days": float(cyc.integral_ndvi_days),
            "peak_evi": float(getattr(cyc, "peak_evi", 0.0) or 0.0),
            "peak_ndmi": float(getattr(cyc, "peak_ndmi", 0.0) or 0.0),
            "cycle_confidence": float(getattr(cyc, "confidence", 0.0) or 0.0),
            "peak_observed": bool(getattr(cyc, "peak_observed", True)),
            "observed_fraction": float(getattr(cyc, "observed_fraction", 1.0) or 1.0),
            "n_scenes_real": len(cyc_scenes),
            "n_obs_window": n_obs,
            "duration_outlier": _duration_outlier(cyc, p["Crop_Name"]),
            "land_cover_class": lc.get("class"),
            "land_cover_outcome": lc.get("outcome"),
            "survey_date": survey.isoformat(),
        })

        if i % 250 == 0 or i == len(parcels):
            log.info("  %d/%d  kept=%d rejected=%d", i, len(parcels),
                     len(kept), len(rejected))

    if not kept:
        log.error("no cycles attributed — nothing to train on")
        pd.DataFrame(rejected).to_csv(REJECTS, index=False)
        return 1

    df = pd.DataFrame(kept)
    df.to_parquet(OUT, index=False)
    pd.DataFrame(rejected).to_csv(REJECTS, index=False)

    # ── report ────────────────────────────────────────────────────────────
    n_tot = len(parcels)
    log.info("")
    log.info("=" * 72)
    log.info("ATTRIBUTION RESULT")
    log.info("=" * 72)
    log.info("attributed: %d / %d  (%.1f%%)", len(df), n_tot, 100 * len(df) / n_tot)
    log.info("")
    log.info("rejections by reason:")
    for r, n in reasons.most_common():
        log.info("  %-28s %5d  (%.1f%%)", r, n, 100 * n / n_tot)
    log.info("")
    log.info("attribution modes: %s", dict(df["attribution"].value_counts()))
    log.info("duration outliers kept (flagged): %d (%.1f%%)",
             int(df["duration_outlier"].sum()),
             100 * df["duration_outlier"].mean())
    log.info("cycle_kind mismatches kept (flagged): %d (%.1f%%)",
             int(df["kind_mismatch"].sum()), 100 * df["kind_mismatch"].mean())
    log.info("")
    log.info("per-class survival:")
    surv = (
        pd.DataFrame({"total": parcels.groupby("Crop_Name").size(),
                      "kept": df.groupby("Crop_Name").size()})
        .fillna(0).astype(int)
    )
    surv["pct"] = (100 * surv["kept"] / surv["total"]).round(1)
    surv["blocks"] = df.groupby("Crop_Name")["block_id"].nunique()
    surv["dur_med"] = df.groupby("Crop_Name")["duration_days"].median()
    surv["scenes_med"] = df.groupby("Crop_Name")["n_scenes_real"].median()
    for line in surv.fillna(0).to_string().splitlines():
        log.info("  %s", line)

    thin = surv[surv["kept"] < 40]
    if len(thin):
        log.warning("")
        log.warning("classes with <40 samples — blocked CV folds will be unstable: %s",
                    list(thin.index))

    log.info("")
    log.info("wrote %s and %s", OUT.relative_to(PROJECT), REJECTS.relative_to(PROJECT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
