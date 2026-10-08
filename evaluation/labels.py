"""Shared label normalisation for interpreter sheets, client records and maps.

Only spelling / synonym normalisation happens here. Nothing is re-labelled
on the basis of model output.
"""

from __future__ import annotations

import math
from typing import Optional

# canonical name -> accepted spellings (lower-case). Canonical names are the
# pipeline's own class names (classifier, crop calendar, reports), so a bank
# report never shows two spellings of one crop.
_SYNONYMS = {
    "Cotton": ["cotton", "kapas", "kapus", "seed cotton", "seed-cotton"],
    "Soyabean": ["soybean", "soyabean", "soya bean", "soy bean", "soya", "soy"],
    "Soyabean+Tur": ["soyabean+tur", "soybean+tur", "soyabean + tur", "soybean + tur",
                     "soybean tur intercrop", "soyabean tur intercrop"],
    "Not requested": ["not requested"],
    "Insufficient data": ["insufficient data", "no data"],
    "Tur": ["tur", "toor", "arhar", "pigeon pea", "pigeonpea", "red gram", "redgram"],
    "Jowar": ["jowar", "sorghum", "jawar"],
    "Bajra": ["bajra", "pearl millet", "pearlmillet"],
    "Sugarcane": ["sugarcane", "sugar cane", "ganna"],
    "Maize": ["maize", "corn", "makka"],
    "Fallow": ["fallow", "bare", "bare soil", "not sown", "unsown"],
    "Other": ["other", "other crop", "other/unknown", "other / unknown", "unknown crop"],
    "Abstained": ["abstained", "abstain"],
    "Non-crop": ["non-crop", "noncrop", "non crop", "built-up", "built up", "water",
                 "tree", "trees", "orchard boundary", "road"],
    "Unclear": ["unclear", "cannot tell", "can't tell", "cant tell", "?"],
}
_LOOKUP = {s: canon for canon, alts in _SYNONYMS.items() for s in alts}
_LOOKUP.update({canon.lower(): canon for canon in _SYNONYMS})

CONFIDENCE_LEVELS = ("high", "medium", "low")


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == "" or str(v).strip().lower() in ("nan", "none", "null")


def normalise_label(v) -> Optional[str]:
    """Return the canonical class name, the title-cased input when unknown,
    or None when blank."""
    if is_blank(v):
        return None
    s = " ".join(str(v).strip().split())
    return _LOOKUP.get(s.lower(), s[:1].upper() + s[1:])


def normalise_confidence(v) -> Optional[str]:
    if is_blank(v):
        return None
    s = str(v).strip().lower()
    alias = {"h": "high", "m": "medium", "med": "medium", "l": "low"}
    s = alias.get(s, s)
    return s if s in CONFIDENCE_LEVELS else None


def min_confidence(*levels) -> Optional[str]:
    vals = [l for l in levels if l in CONFIDENCE_LEVELS]
    if not vals:
        return None
    return max(vals, key=CONFIDENCE_LEVELS.index)
