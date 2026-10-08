"""
Region support guard at inference: trust a crop name only where training saw it.

The evidence and the tier thresholds are documented in
`Crop_classification_model/src/region_guard.py`, which builds
`models/region_support.json` from the training features and imports the tier
rule from here, so training and inference use one rule.

The guard never moves the argmax. It scales the confidence attached to the
model's top crop by how much training support that crop has in the parcel's
agro-ecoregion, and the abstain rule does the rest.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

VIABLE = 30                # in-region training cycles for "supported"
THIN_SCALE = 0.5           # confidence multiplier for 1..VIABLE-1
UNSUPPORTED_SCALE = 0.15   # ...and for zero

SUPPORT_PATH = Path(__file__).resolve().parent.parent / "models" / "region_support.json"


@lru_cache(maxsize=4)
def load_support(path: Optional[str] = None) -> Dict:
    p = Path(path) if path else SUPPORT_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("region support table unavailable (%s): guard disabled", exc)
        return {}


def tier(support: Dict, crop: str, ecoregion: str) -> Tuple[str, float]:
    """(tier name, confidence multiplier) for one crop in one region."""
    if not support:
        return "unknown", 1.0
    n = support.get("counts", {}).get(crop, {}).get(ecoregion, 0)
    if n >= support.get("viable_threshold", VIABLE):
        return "supported", 1.0
    if n > 0:
        return "thin", THIN_SCALE
    return "unsupported", UNSUPPORTED_SCALE


def ecoregion_for(lat: Optional[float], lon: Optional[float]) -> str:
    from utils.india_geo_context import infer_agro_ecoregion

    return infer_agro_ecoregion(lat, lon)[0]
