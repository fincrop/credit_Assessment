"""
Region support guard — refuse to name a crop where the model has never seen it.

The measured fact this exists for: crops present in exactly one agro-ecoregion
score **0.000 recall** under leave-one-ecoregion-out. Not low -- zero. Rice
appears only in the Gangetic plains, Chilli and Tobacco only in the Southern
Peninsula, and when their region is held out the model has no training row for
them at all. The optimistic reading of a 0.75 blocked-CV score is that the model
is good everywhere; the honest one is that it is good where it has seen
neighbours and arbitrary elsewhere.

Without a guard that failure is silent and confident. The classifier still emits
a softmax over 18 crops for a parcel in a region where 6 of them have no support,
the argmax still clears the 0.25 gate a good fraction of the time, and a wrong
crop name swings up to 45% of the downstream ICAR risk index. Confidently wrong
is worse here than abstaining, because `analyze_cycles` already handles a null
crop by taking the crop-agnostic path -- the pipeline was built for this.

What the guard does NOT do is change the argmax. Rewriting the prediction would
hide the problem and make the model agree with a prior instead of reporting what
it saw. It scales confidence by how much training support the predicted crop has
in *this* parcel's region, and attaches a readable reason. The abstain rule that
already exists then does the rest.

Support tiers, from the LOEO evidence:

    >= 30 parcels in-region   supported    -- keep confidence
    1..29                     thin         -- 0.5x, flag
    0                         unsupported  -- 0.15x, flag

The 30-parcel floor is the same one `src.augment` uses, and for the same reason:
Rice has exactly 1 Southern-Peninsula parcel and Maize 2 in Central Highland.
A handful of parcels looks like coverage in a `nunique()` count and teaches the
model nothing.

Measured on blocked OOF predictions:

    tier          n      accuracy
    supported   6994     0.836
    thin         188     0.128
    unsupported  725     0.000

    precision @ gate 0.25:  0.758 -> 0.837   (coverage 0.973 -> 0.880)

READ THAT 0.000 CAREFULLY -- on this dataset it is partly tautological, and the
precision gain is therefore an overstatement. Every OOF row is itself training
data, so if the true crop in region R were X, then X would by definition have
support in R. A prediction flagged `unsupported` consequently *cannot* be right
here. The number confirms the guard fires on the intended rows; it does not
prove that magnitude of gain on unseen data.

The genuine, non-circular argument is about evidence rather than accuracy: the
model has no basis for naming a crop in a region where it never saw one, so a
confident answer there is unjustified regardless of whether it happens to land.
The real cost is the mirror case -- a farmer genuinely growing rice outside the
Gangetic plain gets suppressed. That trade is deliberate and asymmetric: a
false abstention routes to the crop-agnostic path, while a confident wrong crop
name moves up to 45% of the ICAR-curve risk index. The guard also shrinks as
coverage grows -- `src.augment` already lifts Rice and Maize out of the
single-region set, and each such fix retires a rule rather than entrenching it.

    python -m src.region_guard --build    # -> models/region_support.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from . import _bootstrap  # noqa: F401

log = logging.getLogger("region_guard")

DATA = Path(__file__).resolve().parent.parent / "data"
MODELS = Path(__file__).resolve().parent.parent / "models"
SUPPORT_PATH = MODELS / "region_support.json"

# The tier rule is shared with inference (crop_analysis.region_guard) so the
# two cannot drift. Only the table builder lives here.
from crop_analysis.region_guard import (  # noqa: E402
    THIN_SCALE,
    UNSUPPORTED_SCALE,
    VIABLE,
)
from crop_analysis.region_guard import tier as _shared_tier  # noqa: E402


def build(features: pd.DataFrame) -> Dict:
    """Count training cycles per (crop, ecoregion) from the training features."""
    counts = (features.groupby(["Crop_Name", "ecoregion"]).size()
                      .rename("n").reset_index())
    table: Dict[str, Dict[str, int]] = {}
    for r in counts.itertuples():
        table.setdefault(r.Crop_Name, {})[r.ecoregion] = int(r.n)

    summary = {}
    for crop, regions in table.items():
        viable = [r for r, n in regions.items() if n >= VIABLE]
        summary[crop] = {
            "supported_regions": sorted(viable),
            "n_supported_regions": len(viable),
            "single_region": len(viable) <= 1,
        }
    return {"viable_threshold": VIABLE, "counts": table, "summary": summary,
            "n_train_cycles": int(len(features))}


def tier(support: Dict, crop: str, ecoregion: str) -> Tuple[str, float]:
    """(tier name, confidence multiplier) for one crop in one region."""
    return _shared_tier(support, crop, ecoregion)


def apply(proba: np.ndarray, classes, ecoregions, support: Dict):
    """Scale per-row confidence by the predicted crop's regional support.

    Returns (adjusted_confidence, tiers, notes). `proba` is left untouched and
    the argmax never moves: the model's opinion is reported as-is, and only the
    trust attached to it changes. A caller that wants a different crop should
    get there through the existing abstain path, not through a silent rewrite.
    """
    classes = list(classes)
    top = proba.argmax(1)
    conf = proba.max(1)

    adj = np.empty(len(top))
    tiers, notes = [], []
    for i, (j, c) in enumerate(zip(top, conf)):
        crop, reg = classes[j], ecoregions[i]
        t, scale = tier(support, crop, reg)
        adj[i] = c * scale
        tiers.append(t)
        notes.append(
            "" if t == "supported" else
            f"{crop} has {'no' if t == 'unsupported' else 'thin'} training "
            f"support in {reg}; confidence scaled x{scale}"
        )
    return adj, np.array(tiers), notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--features", default="03_features_tier1.parquet")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    f = DATA / a.features
    if not f.exists():
        raise SystemExit(f"missing {f}")
    sup = build(pd.read_parquet(f))

    log.info("crop                supported regions")
    for crop in sorted(sup["summary"]):
        s = sup["summary"][crop]
        mark = "  <-- SINGLE REGION" if s["single_region"] else ""
        log.info("  %-12s %d  %s%s", crop, s["n_supported_regions"],
                 ", ".join(r[:22] for r in s["supported_regions"]), mark)
    single = [c for c, s in sup["summary"].items() if s["single_region"]]
    log.info("\n%d of %d crops are confined to one region: %s",
             len(single), len(sup["summary"]), ", ".join(single))

    if a.build:
        MODELS.mkdir(parents=True, exist_ok=True)
        SUPPORT_PATH.write_text(json.dumps(sup, indent=1))
        log.info("\nwrote %s", SUPPORT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
