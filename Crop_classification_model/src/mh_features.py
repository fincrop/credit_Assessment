"""
Cycle-scene level feature variants for the Marathwada retrain (Track A5/A6).

Base features always come from `src.features` (the production builder). This
module adds two things that stage cannot do, using the SAME building blocks
(`cycles._build_continuous`, `features._slice_scenes`, `build_feature_dict`,
`TIER1_EXTRA_INDICES`, `TIER1_SCALARS`), so a variant with zero perturbation is
byte-identical to the stage output (asserted in `parity_check`):

1. Year-robustness augmentation (training rows only).
   `build_feature_dict` resamples on SCENE ORDER inside the cycle window, so a
   whole-season calendar shift is a no-op in feature space. What a different
   year actually changes is (a) where the detector puts the cycle boundaries
   relative to the curve and (b) how long the crop takes. So:
     shift   : window [sowing+d, harvest+d], d ~ U[-20, +20] days, scenes re-sliced
     stretch : duration_days, log_duration, integral_ndvi_days and n_scenes_real
               scaled by (1+e), e ~ U[-0.10, +0.10]; the curve shape is kept
   Each augmented row carries its parent's geom_hash/block so CV folds keep
   parent and copies on the same side, and copies are never built for test or
   excluded parcels (the caller passes training cycles only).

2. In-season truncation (plan B4).
   The parcel's series is cut at `sowing + D` days (sowing from the full-season
   detection), the composite signal is rebuilt on the truncated series, the
   production CropCycleDetector is re-run, and the cycle matching the
   full-season one (nearest sowing within 45 days) is featurised from its own
   scalars. The land-cover gate is not re-run (the parcel passed it on the full
   season). No cycle -> the field is unclassified at that date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .cycles import CYCLE_PADDING_DAYS, INTERVAL_DAYS, MIN_SCENES_PER_CYCLE, _build_continuous
from .features import META_COLS, TIER1_EXTRA_INDICES, TIER1_SCALARS, _slice_scenes

SHIFT_DAYS = 20
STRETCH = 0.10
MATCH_SOWING_DAYS = 45


def _index_keys():
    from data_acquisition.satellite_collector import SatelliteDataCollector
    return SatelliteDataCollector.INDEX_KEYS


def _t0_names() -> List[str]:
    from config import PipelineConfig
    from crop_analysis.crop_detector import extractor_feature_names
    pre = tuple(f"{i.replace('_mean', '')}_t" for i in PipelineConfig.ML_FEATURE_INDICES)
    return [n for n in extractor_feature_names() if n.startswith(pre)]


def feature_vector(cyc_scenes: List[Dict], scalars: Dict[str, float]) -> Dict[str, float]:
    """Tier-1 features exactly as src.features builds them."""
    from crop_analysis.crop_detector import build_feature_dict

    cyc_scenes = sorted(cyc_scenes, key=lambda s: s.get("date", ""))
    fd0 = build_feature_dict(cyc_scenes)
    fd1 = {k: fd0[k] for k in fd0}
    fd1.update(build_feature_dict(cyc_scenes, indices=TIER1_EXTRA_INDICES))
    for k in TIER1_SCALARS:
        fd1[k] = float(scalars.get(k, 0.0))
    fd1["log_duration"] = float(np.log1p(max(float(scalars["duration_days"]), 0.0)))
    return fd1


def _shift_iso(d: str, days: int) -> str:
    return (datetime.strptime(d, "%Y-%m-%d").date() + timedelta(days=int(days))).isoformat()


def _continuous_for(parcel: pd.Series, gh: str, bins: pd.DataFrame, anchor: date,
                    win_end: Optional[str] = None):
    return _build_continuous(
        pd.Series({"win_start": parcel["win_start"],
                   "win_end": win_end or parcel["win_end"],
                   "area_ha": parcel["area_ha"], "geom_hash": gh}),
        bins, _index_keys(), anchor)


# =============================================================================
# 1. augmentation
# =============================================================================
def augment_cycles(parcels: pd.DataFrame, scenes: pd.DataFrame, cycles: pd.DataFrame,
                   anchor: date, n_aug: int = 1, seed: int = 42,
                   shift_days: int = SHIFT_DAYS, stretch: float = STRETCH,
                   zero: bool = False) -> pd.DataFrame:
    """
    `n_aug` perturbed copies of every cycle in `cycles` (TRAINING rows only —
    the caller is responsible, and run_mh asserts it). `zero=True` builds the
    unperturbed row instead, for the parity check.
    """
    rng = np.random.default_rng(seed)
    pidx = parcels.set_index("geom_hash")
    groups = dict(tuple(scenes.groupby("geom_hash")))
    rows: List[Dict[str, Any]] = []
    for _, c in cycles.iterrows():
        gh = c["geom_hash"]
        if gh not in groups or gh not in pidx.index:
            continue
        cont = _continuous_for(pidx.loc[gh], gh, groups[gh], anchor)
        if cont is None:
            continue
        meta = {k: c[k] for k in META_COLS if k in c}
        for j in range(1 if zero else n_aug):
            d = 0 if zero else int(rng.integers(-shift_days, shift_days + 1))
            e = 0.0 if zero else float(rng.uniform(-stretch, stretch))
            sc = _slice_scenes(cont, _shift_iso(c["sowing_date"], d),
                               _shift_iso(c["harvest_date"], d))
            if len(sc) < MIN_SCENES_PER_CYCLE:
                continue
            scal = {k: float(c[k]) for k in TIER1_SCALARS if k in c}
            if zero:
                scal["n_scenes_real"] = float(c["n_scenes_real"])
            else:
                f = 1.0 + e
                scal["duration_days"] = float(c["duration_days"]) * f
                scal["integral_ndvi_days"] = float(c["integral_ndvi_days"]) * f
                scal["n_scenes_real"] = float(max(MIN_SCENES_PER_CYCLE,
                                                  round(len(sc) * f)))
            rows.append({**meta, **feature_vector(sc, scal),
                         "aug_shift_days": d, "aug_stretch": round(e, 4),
                         "aug_copy": j})
    return pd.DataFrame(rows)


# =============================================================================
# 2. in-season truncation
# =============================================================================
def truncated_features(parcels: pd.DataFrame, scenes: pd.DataFrame, cycles: pd.DataFrame,
                       anchor: date, days: int) -> pd.DataFrame:
    """Features as an in-season run `days` after detected sowing would build them."""
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from utils.india_geo_context import infer_agro_ecoregion

    detector = CropCycleDetector()
    pidx = parcels.set_index("geom_hash")
    groups = dict(tuple(scenes.groupby("geom_hash")))
    rows: List[Dict[str, Any]] = []
    for _, c in cycles.iterrows():
        gh = c["geom_hash"]
        meta = {k: c[k] for k in META_COLS if k in c}
        base = {**meta, "trunc_days": days}
        if gh not in groups or gh not in pidx.index:
            rows.append({**base, "trunc_status": "no_scenes"})
            continue
        p = pidx.loc[gh]
        full_sow = datetime.strptime(c["sowing_date"], "%Y-%m-%d").date()
        cutoff = full_sow + timedelta(days=int(days))
        win_end = min(cutoff, datetime.strptime(p["win_end"], "%Y-%m-%d").date()).isoformat()
        cont = _continuous_for(p, gh, groups[gh], anchor, win_end=win_end)
        if cont is None or cont["valid_observations"] < 10:
            rows.append({**base, "trunc_status": "insufficient_observations"})
            continue
        _, agro = infer_agro_ecoregion(float(p["lat"]), float(p["lon"]))
        try:
            cyc_list = detector.detect_cycles(
                dates=cont["dates"], ndvi_values=cont["ndvi_values"],
                evi_values=cont["evi_values"], ndmi_values=cont["ndmi_values"],
                scenes=cont["scenes"], grid_step_days=INTERVAL_DAYS, agro_profile=agro)
        except Exception as exc:                              # noqa: BLE001
            rows.append({**base, "trunc_status": f"detector_error:{str(exc)[:60]}"})
            continue
        best, best_gap = None, None
        for cy in cyc_list:
            gap = abs((cy.sowing_date.date() - full_sow).days)
            if gap <= MATCH_SOWING_DAYS and (best_gap is None or gap < best_gap):
                best, best_gap = cy, gap
        if best is None:
            rows.append({**base, "trunc_status": "no_matching_cycle",
                         "n_cycles_trunc": len(cyc_list)})
            continue
        s = best.sowing_date.date().isoformat()
        h = min(best.harvest_date.date(), cutoff).isoformat()
        sc = [x for x in _slice_scenes(cont, s, h) if x["date"] <= cutoff.isoformat()]
        if len(sc) < MIN_SCENES_PER_CYCLE:
            rows.append({**base, "trunc_status": "too_few_scenes", "n_scenes": len(sc)})
            continue
        scal = {
            "duration_days": float(best.duration_days),
            "n_scenes_real": float(len(sc)),
            "observed_fraction": float(getattr(best, "observed_fraction", 1.0) or 1.0),
            "peak_observed": float(bool(getattr(best, "peak_observed", True))),
            "peak_ndvi": float(best.peak_ndvi), "baseline_ndvi": float(best.baseline_ndvi),
            "ndvi_rise": float(best.ndvi_rise),
            "integral_ndvi_days": float(best.integral_ndvi_days),
            "peak_evi": float(getattr(best, "peak_evi", 0.0) or 0.0),
            "peak_ndmi": float(getattr(best, "peak_ndmi", 0.0) or 0.0),
            "cycle_confidence": float(getattr(best, "confidence", 0.0) or 0.0),
        }
        rows.append({**base, **feature_vector(sc, scal), "trunc_status": "ok",
                     "trunc_sowing": s, "trunc_harvest": best.harvest_date.date().isoformat(),
                     "trunc_sowing_gap_days": best_gap})
    return pd.DataFrame(rows)


def parity_check(parcels: pd.DataFrame, scenes: pd.DataFrame, cycles: pd.DataFrame,
                 stage_features: pd.DataFrame, anchor: date,
                 feature_names: Sequence[str], n: int = 25) -> float:
    """Max |difference| between an unperturbed rebuild and the stage output."""
    sub = cycles.head(n)
    z = augment_cycles(parcels, scenes, sub, anchor, zero=True)
    if z.empty:
        return float("nan")
    a = z.set_index("geom_hash")[list(feature_names)]
    b = stage_features.set_index("geom_hash").loc[a.index, list(feature_names)]
    return float(np.nanmax(np.abs(a.to_numpy(float) - b.to_numpy(float))))
