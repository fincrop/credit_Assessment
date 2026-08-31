"""
End-to-end smoke test on SYNTHETIC data.

Purpose: exercise every downstream stage — continuous-grid assembly, cycle
detection, label attribution, feature construction, blocked CV, calibration and
export — without waiting on the multi-hour satellite extraction. Catches
plumbing bugs while the real data is still downloading.

This proves the CODE runs and the contracts hold. It says nothing about model
quality; that only comes from real imagery.

Run:  python -m src.smoke_test
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ._bootstrap import setup_logging

log = setup_logging("smoke")

SEED = 7
INTERVAL = 10

# (crop, duration_days, peak_ndvi, sowing month) — coarse but phenologically
# plausible shapes, enough to give the detector something real to find.
SYNTH_CROPS = [
    ("Wheat", 130, 0.82, 11),
    ("Rice", 120, 0.85, 6),
    ("Cotton", 180, 0.78, 5),
    ("Bajra", 85, 0.62, 6),
    ("Sugarcane", 330, 0.88, 2),
    ("Mustard", 130, 0.75, 11),
]


def _double_logistic(t: np.ndarray, dur: float, peak: float, base: float = 0.16):
    """Green-up / senescence curve — the shape phenology detectors look for."""
    up = 1.0 / (1.0 + np.exp(-(t - 0.25 * dur) / (0.07 * dur)))
    down = 1.0 / (1.0 + np.exp((t - 0.78 * dur) / (0.08 * dur)))
    return base + (peak - base) * up * down


def _synth_series(sow: date, dur: int, peak: float, win0: date, win1: date, rng):
    """A 10-day-binned NDVI/EVI/NDMI series with a realistic cloud-gap pattern."""
    rows = []
    cur = win0
    while cur <= win1:
        dt = (cur - sow).days
        if 0 <= dt <= dur:
            ndvi = float(_double_logistic(np.array([dt]), dur, peak)[0])
        else:
            ndvi = 0.15 + 0.03 * rng.standard_normal()
        ndvi = float(np.clip(ndvi + 0.015 * rng.standard_normal(), 0.02, 0.95))

        # Monsoon months lose most observations, exactly as the real data does.
        p_missing = 0.55 if cur.month in (6, 7, 8, 9) else 0.12
        if rng.random() < p_missing:
            rows.append({"bin_start": cur.isoformat(), "NDVI_mean": np.nan})
        else:
            rows.append({
                "bin_start": cur.isoformat(),
                "NDVI_mean": ndvi,
                "EVI_mean": float(np.clip(ndvi * 0.92, 0.0, 1.0)),
                "NDMI_mean": float(np.clip(ndvi * 0.55 - 0.05, -0.3, 0.8)),
                "NDRE_mean": float(np.clip(ndvi * 0.65, 0.0, 1.0)),
                "PSRI_mean": float(np.clip(0.05 - ndvi * 0.04, -0.2, 0.3)),
                "kNDVI_mean": float(np.tanh(ndvi ** 2)),
                "LSWI_mean": float(np.clip(ndvi * 0.5 - 0.05, -0.3, 0.8)),
                "GCVI_mean": float(max(0.0, ndvi * 6.0 - 0.5)),
                "NDWI_mean": float(-ndvi * 0.6),
                "MSAVI2_mean": float(ndvi * 0.9),
                "NIRv_mean": float(ndvi * 0.3),
                "NDBI_mean": float(-ndvi * 0.3),
                "MNDWI_mean": float(-ndvi * 0.4),
                "BSI_mean": float(0.1 - ndvi * 0.3),
                "NDVI_stdDev": 0.04,
                "NDVI_p90": min(0.98, ndvi + 0.05),
            })
        cur = cur + timedelta(days=INTERVAL)
    return rows


def _check(name: str, ok: bool, detail: str = "") -> bool:
    log.info("  [%s] %s%s", "PASS" if ok else "FAIL", name,
             f"  — {detail}" if detail else "")
    return ok


def main() -> int:
    from config import PipelineConfig
    from crop_analysis.crop_cycle_detector import CropCycleDetector
    from crop_analysis.crop_detector import (
        DEFAULT_ABSTAIN_RULE, EXTRACTOR_VERSION, TIER1_EXTRA_INDICES,
        TIER1_SCALAR_NAMES, build_feature_dict, build_features,
        extractor_feature_names,
    )
    from .cycles import _attribute, _build_continuous
    from data_acquisition.satellite_collector import SatelliteDataCollector

    rng = np.random.default_rng(SEED)
    index_keys = SatelliteDataCollector.INDEX_KEYS
    detector = CropCycleDetector()
    results = []

    log.info("=" * 68)
    log.info("STAGE A — shared extractor contract")
    log.info("=" * 68)
    names = extractor_feature_names()
    n_grid = PipelineConfig.ML_FEATURE_SCENES
    n_t0 = n_grid * len(PipelineConfig.ML_FEATURE_INDICES)
    expected = n_t0 + n_grid * len(TIER1_EXTRA_INDICES) + len(TIER1_SCALAR_NAMES)
    results.append(_check(
        "extractor advertises tier-0 grids + tier-1 grids + tier-1 scalars",
        len(names) == expected,
        f"{len(names)} names (= {n_t0} + {n_grid*len(TIER1_EXTRA_INDICES)} + "
        f"{len(TIER1_SCALAR_NAMES)}), version={EXTRACTOR_VERSION}"))
    results.append(_check("feature names are 1-based zero-padded",
                          names[0] == "NDVI_t01" and names[14] == "NDVI_t15"))
    results.append(_check(
        "tier-0 subset is unchanged by the tier-1 addition",
        extractor_feature_names()[:n_t0]
        == [f"{i.replace('_mean','')}_t{k+1:02d}"
            for i in PipelineConfig.ML_FEATURE_INDICES for k in range(n_grid)]))
    results.append(_check("abstain rule stricter than the 0.25 pipeline gate",
                          DEFAULT_ABSTAIN_RULE["p_min"] > 0.25,
                          str(DEFAULT_ABSTAIN_RULE)))

    log.info("")
    log.info("=" * 68)
    log.info("STAGE B — continuous grid, cycle detection, attribution")
    log.info("=" * 68)

    feature_rows = []
    tier1_rows = []
    n_attributed = 0
    for ci, (crop, dur, peak, sow_month) in enumerate(SYNTH_CROPS):
        # 40 synthetic parcels per crop over 5 blocks. Raised from 12 after the
        # estimator's regularisation was tightened (min_child_weight=10): at 12
        # per crop the trees could not split at all and blocked CV returned
        # exactly chance. The point of this check is to exercise the REAL
        # configuration, so grow the fixture rather than relax the assertion.
        for rep in range(40):
            year = 2022 + (rep % 2)
            sow = date(year, sow_month, 5 + (rep % 3) * 5)
            survey = sow + timedelta(days=int(dur * 0.6))
            win0, win1 = survey - timedelta(days=400), survey + timedelta(days=400)

            bins = pd.DataFrame(_synth_series(sow, dur, peak, win0, win1, rng))
            parcel = pd.Series({
                "win_start": win0.isoformat(), "win_end": win1.isoformat(),
                "area_ha": 1.0, "geom_hash": f"{crop[:3].lower()}{rep:03d}",
            })

            continuous = _build_continuous(parcel, bins, index_keys)
            if continuous is None:
                continue

            cycles = detector.detect_cycles(
                dates=continuous["dates"],
                ndvi_values=continuous["ndvi_values"],
                evi_values=continuous["evi_values"],
                ndmi_values=continuous["ndmi_values"],
                scenes=continuous["scenes"],
                grid_step_days=INTERVAL,
            )
            idx, tag = _attribute(cycles, survey, crop)
            if idx is None:
                continue
            n_attributed += 1

            cyc = cycles[idx]
            s, h = cyc.sowing_date.date(), cyc.harvest_date.date()
            lo = (s - timedelta(days=5)).isoformat()
            hi = (h + timedelta(days=5)).isoformat()
            sl = sorted(
                [x for x in continuous["scenes"]
                 if not x.get("missing") and lo <= x["date"] <= hi],
                key=lambda x: x["date"])
            if len(sl) < 5:
                continue

            fd = build_feature_dict(sl)
            fd_t1 = build_features(sl, cyc)
            tier1_rows.append(fd_t1)
            feature_rows.append({
                "Crop_Name": crop,
                "block_id": f"b{ci}_{rep % 5}",
                "ecoregion": f"eco{ci % 3}",
                "geom_hash": parcel["geom_hash"] + f"_{ci}",
                "sample_id": len(feature_rows),
                "lat": 20.0, "lon": 76.0, "area_ha": 1.0,
                "attribution": tag, "duration_outlier": False,
                "cycle_kind": cyc.cycle_kind, "season_type": cyc.season_type,
                "sowing_date": s.isoformat(), "harvest_date": h.isoformat(),
                "survey_date": survey.isoformat(), "n_cycles_detected": len(cycles),
                "land_cover_class": "CROPLAND",
                **fd,
            })

    results.append(_check("cycles detected and attributed",
                          n_attributed >= 120,
                          f"{n_attributed}/{len(SYNTH_CROPS)*12} synthetic parcels"))
    t0_names = names[:n_t0]
    results.append(_check("tier-0 rows carry exactly the 45-column contract",
                          len(feature_rows) >= 100
                          and all(n in feature_rows[0] for n in t0_names),
                          f"{len(feature_rows)} rows"))
    results.append(_check("tier-1 rows carry every advertised feature",
                          bool(tier1_rows)
                          and all(n in tier1_rows[0] for n in names),
                          f"{len(tier1_rows)} rows, "
                          f"duration_days={tier1_rows[0]['duration_days']:.0f}"
                          if tier1_rows else "no rows"))
    results.append(_check("tier-1 duration is a real value, not a zero-fill",
                          bool(tier1_rows)
                          and tier1_rows[0]["duration_days"] > 30
                          and tier1_rows[0]["log_duration"] > 0))

    if len(feature_rows) < 100:
        log.error("too few synthetic rows to exercise training — stopping")
        return 1

    log.info("")
    log.info("=" * 68)
    log.info("STAGE C — blocked CV, calibration, gates, export")
    log.info("=" * 68)

    from sklearn.preprocessing import LabelEncoder
    from .features import META_COLS
    from crop_analysis.model_bundle import CalibratedBundle, TemperatureScaler
    from .train import (
        _cv, _expand, _make_model, _pipeline_confidence, _sample_weights,
    )

    df = pd.DataFrame(feature_rows)
    feat_cols = [c for c in df.columns if c not in META_COLS]
    X = np.nan_to_num(df[feat_cols].to_numpy(dtype=float))
    le = LabelEncoder().fit(sorted(df["Crop_Name"].unique()))
    y = le.transform(df["Crop_Name"])
    blocks = df["block_id"].to_numpy()
    classes = list(le.classes_)

    results.append(_check("no banned column leaked into the matrix",
                          not ({"lat", "lon", "area_ha", "survey_date"}
                               & set(feat_cols))))

    blocked = _cv(X, y, blocks, blocks, classes, "blocked", run_baselines=True)
    s = blocked["calibrated"]
    log.info("  synthetic blocked bal_acc=%.3f  ECE=%.3f  temp=%.3f",
             s["balanced_accuracy"], s["ece"], blocked["mean_temperature"])
    log.info("  baselines: %s", blocked["baselines"])
    results.append(_check("blocked CV ran and beats chance",
                          s["balanced_accuracy"] > 1.5 / len(classes),
                          f"bal_acc={s['balanced_accuracy']:.3f} vs chance "
                          f"{1/len(classes):.3f}"))
    results.append(_check("calibration produced a finite temperature",
                          np.isfinite(blocked["mean_temperature"])))

    # pipeline confidence transform must reproduce CropDetector's formula
    p = np.array([[0.6, 0.3, 0.1], [0.4, 0.38, 0.22]])
    conf = _pipeline_confidence(p)
    expect = [min(0.6, 0.6 * min(1.0, 0.30 / 0.10 + 0.5)),
              min(0.4, 0.4 * min(1.0, 0.02 / 0.10 + 0.5))]
    results.append(_check("pipeline confidence matches CropDetector's formula",
                          np.allclose(conf, expect),
                          f"{np.round(conf,4).tolist()} vs {np.round(expect,4).tolist()}"))

    # weights must both rebalance classes and de-cluster blocks
    w = _sample_weights(y, blocks)
    results.append(_check("sample weights are positive and finite",
                          np.all(w > 0) and np.all(np.isfinite(w))))

    # ── the fail-fast guard: a tier-1-shaped bundle must be REFUSED ────────
    log.info("")
    log.info("=" * 68)
    log.info("STAGE D — fail-fast guard against the silent zero-fill trap")
    log.info("=" * 68)

    import tempfile
    from pathlib import Path

    import joblib
    from crop_analysis.crop_detector import CropDetector

    tmp = Path(tempfile.mkdtemp())
    scaler = TemperatureScaler().fit(
        np.clip(np.random.default_rng(0).random((len(y), len(classes))), 1e-6, 1),
        y)
    m = _make_model(len(classes)).fit(X, y)
    good = {
        "model": CalibratedBundle(m, scaler, np.arange(len(classes))),
        "label_encoder": le,
        "feature_names": list(feat_cols),
        "crop_names": list(le.classes_),
        "extractor_version": EXTRACTOR_VERSION,
    }
    good_path = tmp / "good.joblib"
    joblib.dump(good, good_path)

    try:
        CropDetector(crop_model_path=str(good_path), latitude=20.0, longitude=76.0,
                     verbose=False)
        loaded_ok = True
        err = ""
    except Exception as exc:                              # noqa: BLE001
        loaded_ok, err = False, str(exc)[:120]
    results.append(_check("a contract-faithful bundle LOADS", loaded_ok, err))

    bad = dict(good)
    # Must be names the extractor genuinely CANNOT produce. `duration_days` used
    # to serve here and is now a real tier-1 feature, which quietly turned this
    # into a no-op test.
    bad["feature_names"] = list(feat_cols) + ["sar_rvi_t07", "soil_ph_topsoil"]
    bad_path = tmp / "bad.joblib"
    joblib.dump(bad, bad_path)
    try:
        CropDetector(crop_model_path=str(bad_path), latitude=20.0, longitude=76.0,
                     verbose=False)
        refused = False
        msg = "loaded anyway — GUARD IS BROKEN"
    except ValueError as exc:
        refused = "cannot produce" in str(exc)
        msg = str(exc)[:90]
    except Exception as exc:                              # noqa: BLE001
        refused, msg = False, f"wrong exception: {type(exc).__name__}"
    results.append(_check("a bundle needing unavailable features is REFUSED",
                          refused, msg))

    stale = dict(good)
    stale["extractor_version"] = "tier9_bogus"
    stale_path = tmp / "stale.joblib"
    joblib.dump(stale, stale_path)
    try:
        CropDetector(crop_model_path=str(stale_path), latitude=20.0,
                     longitude=76.0, verbose=False)
        v_refused, msg = False, "loaded anyway"
    except ValueError as exc:
        v_refused, msg = "extractor" in str(exc), str(exc)[:90]
    except Exception as exc:                              # noqa: BLE001
        v_refused, msg = False, f"wrong exception: {type(exc).__name__}"
    results.append(_check("an extractor-version mismatch is REFUSED",
                          v_refused, msg))

    mis = dict(good)
    mis["crop_names"] = list(le.classes_)[::-1]
    mis_path = tmp / "mis.joblib"
    joblib.dump(mis, mis_path)
    try:
        CropDetector(crop_model_path=str(mis_path), latitude=20.0, longitude=76.0,
                     verbose=False)
        c_refused, msg = False, "loaded anyway"
    except ValueError as exc:
        c_refused, msg = "crop_names" in str(exc), str(exc)[:90]
    except Exception as exc:                              # noqa: BLE001
        c_refused, msg = False, f"wrong exception: {type(exc).__name__}"
    results.append(_check("mis-ordered crop_names is REFUSED", c_refused, msg))

    # ── abstention must flow through to a null crop, not a wrong one ───────
    log.info("")
    log.info("=" * 68)
    log.info("STAGE E — abstention reaches the pipeline as a null crop")
    log.info("=" * 68)

    det = CropDetector(crop_model_path=str(good_path), latitude=20.0,
                       longitude=76.0, verbose=False)
    thin_scenes = [
        {"date": f"2023-01-{d:02d}",
         "indices": {"NDVI_mean": 0.4, "EVI_mean": 0.35, "NDMI_mean": 0.2}}
        for d in (1, 11, 21)
    ]
    out = det._classify_crop_chronological(thin_scenes)
    results.append(_check("3-scene cycle abstains (n_scenes_min=8)",
                          out["abstained"] and out["crop"] is None,
                          f"reason={out['abstain_reason']}"))
    results.append(_check("abstention still returns a full probability vector",
                          len(out["all_probabilities"]) == len(classes)
                          and out["confidence"] == 0.0))
    results.append(_check("argmax retained for audit but not as a prediction",
                          out["top_crop_unreliable"] in classes))

    log.info("")
    log.info("=" * 68)
    log.info("STAGE F — a tier-1 model must abstain when no cycle is supplied")
    log.info("=" * 68)

    # The bundle's model must actually accept 147 columns — reusing the tier-0
    # estimator with tier-1 feature_names made the test fail on a shape
    # mismatch, which was the FIXTURE being inconsistent, not the code.
    t1_feats = list(tier1_rows[0])
    X1 = np.nan_to_num(
        np.array([[r.get(k, 0.0) for k in t1_feats] for r in tier1_rows]),
        nan=0.0, posinf=0.0, neginf=0.0,
    )
    y1 = y[:len(X1)]
    m1 = _make_model(len(classes)).fit(X1, y1)
    scaler1 = TemperatureScaler().fit(
        _expand(m1.predict_proba(X1), m1.classes_, len(classes)), y1)

    t1 = dict(good)
    t1["model"] = CalibratedBundle(m1, scaler1, np.arange(len(classes)))
    t1["feature_names"] = t1_feats
    t1_path = tmp / "tier1.joblib"
    joblib.dump(t1, t1_path)
    det1 = CropDetector(crop_model_path=str(t1_path), latitude=20.0,
                        longitude=76.0, verbose=False)
    results.append(_check("tier-1 bundle is recognised as cycle-dependent",
                          getattr(det1, "requires_cycle", False)))

    wide = [
        {"date": f"2023-{m:02d}-{d:02d}",
         "indices": {k: 0.4 for k in
                     ("NDVI_mean", "EVI_mean", "NDMI_mean", "NDRE_mean",
                      "PSRI_mean", "kNDVI_mean", "LSWI_mean", "GCVI_mean",
                      "NDVI_std")}}
        for m, d in [(1, 5), (1, 15), (1, 25), (2, 5), (2, 15), (2, 25),
                     (3, 5), (3, 15), (3, 25), (4, 5), (4, 15), (4, 25)]
    ]
    no_cycle = det1._classify_crop_chronological(wide)
    results.append(_check(
        "no cycle -> abstains instead of zero-filling duration",
        no_cycle["abstained"] and no_cycle["crop"] is None
        and no_cycle["abstain_reason"] == "cycle_metadata_unavailable",
        f"reason={no_cycle['abstain_reason']}"))

    with_cycle = det1._classify_crop_chronological(wide, cycle=cyc)
    results.append(_check(
        "with a cycle -> scores normally (no spurious abstention)",
        with_cycle["abstain_reason"] != "cycle_metadata_unavailable",
        f"reason={with_cycle['abstain_reason']}"))

    log.info("")
    log.info("=" * 68)
    n_pass, n_tot = sum(results), len(results)
    log.info("SMOKE TEST: %d/%d checks passed", n_pass, n_tot)
    log.info("=" * 68)
    return 0 if n_pass == n_tot else 1


if __name__ == "__main__":
    sys.exit(main())
