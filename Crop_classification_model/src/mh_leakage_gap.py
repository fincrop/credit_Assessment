"""
Leakage gap for crop_classifier_tier1_mh_v1: random-split minus spatially-blocked
balanced accuracy, same recipe (train._cv, kind="xgboost"), same training rows
(03_features_tier1_mh.parquet minus the frozen mh2023 held-out blocks).

The shipped bundle recorded 0.1333 (random 0.8825 vs blocked 0.7492). The
production bundle test requires every bundle to carry it so nobody quotes the
random-split figure. Writes the value into both copies of the bundle and into
reports/mh_eval.json.

    python -m src.mh_leakage_gap
"""
from __future__ import annotations

import json
import sys

import joblib
import numpy as np
import pandas as pd

from ._bootstrap import DATA, MODELS, PROJECT, REPORTS, setup_logging
from . import mh_metrics as M
from .train import _cv

log = setup_logging("mh_leakage_gap")

BUNDLE = "crop_classifier_tier1_mh_v1.joblib"
BACKEND_BUNDLE = PROJECT.parent / "backend" / "Credit_assessment" / "models" / BUNDLE


def main() -> int:
    split = json.loads((DATA / "splits" / "mh2023_split.json").read_text())
    bundle = joblib.load(MODELS / BUNDLE)
    names = list(bundle["feature_names"])
    F = pd.read_parquet(DATA / "03_features_tier1_mh.parquet")
    F = F[M.training_split_mask(F.geom_hash, split)].reset_index(drop=True)
    classes = sorted(F.Crop_Name.unique())
    cidx = {c: i for i, c in enumerate(classes)}
    X = F[names].to_numpy(float)
    X[~np.isfinite(X)] = 0.0
    y = np.array([cidx[c] for c in F.Crop_Name])
    blocks = F.block_id.to_numpy()
    log.info("rows %d, classes %d", len(y), len(classes))

    blocked = _cv(X, y, blocks, blocks, classes, "blocked", kind="xgboost")["calibrated"]
    rnd = _cv(X, y, blocks, blocks, classes, "random", kind="xgboost")["calibrated"]
    gap = float(rnd["balanced_accuracy"]) - float(blocked["balanced_accuracy"])
    log.info("blocked %.4f  random %.4f  LEAKAGE GAP %+.4f",
             blocked["balanced_accuracy"], rnd["balanced_accuracy"], gap)

    detail = {"leakage_gap": round(gap, 4),
              "random_balanced_accuracy": round(float(rnd["balanced_accuracy"]), 4),
              "blocked_balanced_accuracy_same_recipe": round(float(blocked["balanced_accuracy"]), 4),
              "recipe": "train._cv kind=xgboost, nested temperature calibration",
              "rows": int(len(y))}
    for path in (MODELS / BUNDLE, BACKEND_BUNDLE):
        if not path.exists():
            continue
        b = joblib.load(path)
        b["leakage_gap"] = detail["leakage_gap"]
        b["leakage_gap_detail"] = detail
        joblib.dump(b, path, compress=3)
        log.info("updated %s", path)
    rep = REPORTS / "mh_eval.json"
    if rep.exists():
        r = json.loads(rep.read_text())
        r["leakage_gap"] = detail
        rep.write_text(json.dumps(r, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
