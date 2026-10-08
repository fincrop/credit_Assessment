"""Build crop reference curves from labelled Marathwada parcels (plan §5.2).

Replaces the parametric defaults in src/raster/reference.py for every crop
with >= MIN_FIELDS labelled parcels in the region:

    labelled parcel scenes (10-day field means, true polygon footprint)
      -> Whittaker smoothing on a daily axis
      -> sowing from a fit to the parametric default (shift + stretch)
      -> per-crop double logistic fitted on DAS-aligned medians

Only parcels OUTSIDE the frozen mh2023 test blocks are used, so the held-out
evaluation stays independent. Writes Crop_Monitoring/reference/crop_reference_curves.json.

    python build_reference_curves.py
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import src._bootstrap  # noqa: F401,E402
from src.raster import reference as rf  # noqa: E402
from src.raster import smooth as sm  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "Crop_classification_model" / "data"
CROPS = ("Cotton", "Soyabean", "Tur")
MIN_FIELDS = 30
SEASON = 2023
# Fit each crop only over its own season. Measured on these parcels (monthly
# median NDVI, 2023): soybean falls to 0.32 in October and a rabi crop greens
# the same fields from December (0.65 in January), so a Mar-Feb fit treats two
# crops as one season. Cotton stays green to January (0.61) and falls in
# February; tur parcels dip in October (soybean-tur intercrop rows harvested)
# and carry on to January.
SEASON_END = {"Soyabean": date(SEASON, 11, 30), "Cotton": date(SEASON + 1, 2, 28),
              "Tur": date(SEASON + 1, 2, 28)}
BOX = (18.0, 20.5, 75.0, 77.5)


def main() -> int:
    parcels = pd.read_parquet(DATA / "00_parcels_mh_full.parquet",
                              columns=["geom_hash", "Crop_Name", "lat", "lon", "px_gate_ok"])
    split = json.loads((DATA / "splits" / "mh2023_split.json").read_text())
    held = set(split["excluded_from_training"])
    a, b, c, d = BOX
    p = parcels[parcels.Crop_Name.isin(CROPS) & parcels.px_gate_ok
                & parcels.lat.between(a, b) & parcels.lon.between(c, d)
                & ~parcels.geom_hash.isin(held)]
    want = set(p.geom_hash)
    frames = []
    for name in ("01_scenes_mh.parquet", "01_scenes_augmented.parquet"):
        f = DATA / name
        if f.exists():
            s = pd.read_parquet(f, columns=["geom_hash", "geom_kind", "bin_start", "NDVI_mean"])
            frames.append(s[s.geom_hash.isin(want)])
    scenes = pd.concat(frames, ignore_index=True)
    # True-polygon means where extracted; bounding box otherwise.
    scenes["rank"] = (scenes.geom_kind != "poly").astype(int)
    scenes = scenes.sort_values("rank").drop_duplicates(["geom_hash", "bin_start"])
    start, end = date(SEASON, 3, 1), date(SEASON + 1, 2, 28)
    crop_of = dict(zip(p.geom_hash, p.Crop_Name))
    series = []
    fitted = {c: 0 for c in CROPS}
    for gh, grp in scenes.groupby("geom_hash"):
        crop = crop_of.get(gh)
        g = grp.dropna(subset=["NDVI_mean"])
        dts = [date.fromisoformat(str(x)[:10]) + timedelta(days=5) for x in g.bin_start]
        end_c = SEASON_END.get(crop, end)
        keep = [i for i, x in enumerate(dts) if start <= x <= end_c]
        if len(keep) < 10:
            continue
        od = [dts[i] for i in keep]
        vals = g.NDVI_mean.to_numpy(float)[keep][:, None]
        z = sm.smooth_series(od, vals, np.ones_like(vals), start, end_c)[:, 0]
        days = sm.daily_axis(start, end_c)
        support = np.zeros(len(days))
        for x in od:
            k = (x - start).days
            support[max(0, k - 5):k + 6] = 1
        fit = rf.fit_curve(days, z, support, rf.DEFAULT_CURVES[crop],
                           date(SEASON, 5, 1), date(SEASON, 8, 15))
        if fit is None or fit.rmse > 0.08:
            continue
        series.append((crop, days, z, fit.sowing, fit.stretch))
        fitted[crop] += 1
    print("parcels used per crop:", fitted)
    curves = rf.build_reference(series, min_fields=MIN_FIELDS)
    built = {k: v for k, v in curves.items() if v.source == "labelled_mh2023"}
    for k, v in built.items():
        d0 = rf.DEFAULT_CURVES[k]
        print(f"{k:9s} n={v.n_fields:4d}  peak DAS {v.peak_das:5.0f} (default {d0.peak_das:5.0f})  "
              f"season {v.season_days:5.0f} d (default {d0.season_days:5.0f})  peak {v.peak:.2f}")
    rf.save_curves(curves, meta={
        "built_from": "Marathwada labelled parcels, Kharif 2023, outside mh2023 test blocks",
        "season": SEASON, "min_fields": MIN_FIELDS, "parcels_used": fitted,
        "note": "Single season (2023, El Nino, late monsoon). Timing is DAS-relative, so it transfers; "
                "peak NDVI levels may not.",
    })
    print("wrote", rf.REFERENCE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
