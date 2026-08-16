"""
Declared-crop verification
==========================
Checks a farmer-declared crop against the phenology we actually observed.

WHY THIS EXISTS
───────────────
The registry gives us a crop name, but it is self-reported and unverified. Two
bad options were on the table:

  * TRUST IT. Route the parcel down the crop-specific scoring path, where
    CropGrowthCurves benchmarks it against ICAR reference curves. A wrong
    declaration then swings up to 45% of the index by weight, on nothing.
  * IGNORE IT. Which is what the pipeline did — crop_confidence was hardcoded
    0.0 against a >= 0.25 gate, so a declared crop set the label and nothing
    else. That left ~1150 lines of ICAR reference data dead and the per-stage
    weather analysis permanently inert, because it needs a crop name.

There is a third option, and it needs no ground truth: CHECK THE DECLARATION
AGAINST WHAT WE SAW. Cotton is documented at 165-180 days with a characteristic
trajectory. If the observed cycle ran 90 days and peaked low, the declaration
is inconsistent with the observation — and we can establish that from data we
already have.

    consistent      -> unlock crop-specific scoring at a BOUNDED confidence
    inconsistent    -> crop-agnostic scoring, and record the mismatch
    indeterminate   -> crop-agnostic scoring, no claim either way

WHY THE CONFIDENCE IS CAPPED
────────────────────────────
A match is corroboration, not measurement. Many crops share a duration band and
a peak greenness — wheat and mustard both run ~130 days in rabi and both peak
around 0.8. Agreement raises our belief that the declaration is plausible; it
never establishes that the crop IS what was declared. So the ceiling sits well
below anything that would read as a detection, while still clearing the 0.25
gate that unlocks crop-specific analysis.

A SIDE EFFECT WORTH HAVING
──────────────────────────
Every mismatch is a labelled example: a declared crop that demonstrably did not
grow. That is the first crop-label signal this system has ever collected, and
it is exactly what a future classifier retraining (F-1) needs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from config import CropGrowthCurves, PipelineConfig

logger = logging.getLogger(__name__)

VERIFICATION_VERSION = "crop_verification_v1"

CONSISTENT = "consistent"
INCONSISTENT = "inconsistent"
INDETERMINATE = "indeterminate"

__all__ = [
    "verify_declared_crop",
    "VERIFICATION_VERSION",
    "CONSISTENT",
    "INCONSISTENT",
    "INDETERMINATE",
]


def _cycle_field(cycle: Any, key: str) -> Any:
    if isinstance(cycle, dict):
        return cycle.get(key)
    return getattr(cycle, key, None)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _resolve_crop(declared: Optional[str]) -> Optional[str]:
    """
    Canonical crop name known to CropGrowthCurves, or None.

    Normalisation is done ONCE here and the canonical name is what callers pass
    downstream. Elsewhere in the codebase the same lookup has been done against
    a raw string in one place and a normalised one in another, which silently
    disabled stage detection for 'paddy' or 'RICE'.
    """
    if not declared:
        return None
    raw = str(declared).strip()
    if not raw:
        return None
    for candidate in (raw, raw.title(), CropGrowthCurves._normalize_crop_name(raw)):
        if candidate in CropGrowthCurves.CROP_DURATIONS:
            return candidate
    return None


def verify_declared_crop(
    cycles: List[Any],
    declared_crop: Optional[str],
    *,
    cycle_kind: str = "annual",
) -> Dict:
    """
    Is the declared crop consistent with the phenology we observed?

    Returns a verdict dict, always stamped onto the assessment so a lender can
    see why a crop label was or was not used.
    """
    P = PipelineConfig
    verdict: Dict[str, Any] = {
        "version": VERIFICATION_VERSION,
        "declared_crop": declared_crop,
        "canonical_crop": None,
        "outcome": INDETERMINATE,
        "confidence": 0.0,
        "reason": "",
        "evidence": {},
    }

    crop = _resolve_crop(declared_crop)
    verdict["canonical_crop"] = crop

    if crop is None:
        verdict["reason"] = (
            f"No crop declared." if not declared_crop else
            f"Declared crop {declared_crop!r} is not in the reference set, so "
            f"there is nothing to check it against."
        )
        return verdict

    # A perennial has no annual duration to compare; the reference curves
    # describe sown crops. Claiming a verdict here would be a category error.
    if str(cycle_kind).lower() == "perennial":
        verdict["reason"] = (
            "Parcel is perennial; annual crop curves do not apply."
        )
        return verdict

    ref = CropGrowthCurves.CROP_DURATIONS[crop]
    expected_peak = _num(CropGrowthCurves.get_peak_ndvi(crop))
    dur_tol = float(getattr(P, "CROP_VERIFY_DURATION_TOLERANCE", 0.30))
    peak_tol = float(getattr(P, "CROP_VERIFY_PEAK_NDVI_TOLERANCE", 0.25))
    min_cycles = int(getattr(P, "CROP_VERIFY_MIN_CYCLES", 1))

    lo_days = float(ref["min_days"]) * (1.0 - dur_tol)
    hi_days = float(ref["max_days"]) * (1.0 + dur_tol)

    checks: List[Dict] = []
    for c in cycles or []:
        duration = _num(_cycle_field(c, "duration_days"))
        peak = _num(_cycle_field(c, "peak_ndvi"))
        season = str(_cycle_field(c, "season_type") or "").lower()
        if duration is None:
            continue

        duration_ok = lo_days <= duration <= hi_days
        # Peak is checked only as a FLOOR. A crop that outperforms its reference
        # curve is not evidence against the declaration; one that never gets
        # near it is.
        peak_ok = (
            True if (peak is None or expected_peak is None)
            else peak >= expected_peak * (1.0 - peak_tol)
        )
        season_ok = (
            True if not season or ref.get("season") in (None, "both", "perennial")
            else season == str(ref.get("season", "")).lower()
        )

        checks.append({
            "duration_days": duration,
            "expected_days": [ref["min_days"], ref["max_days"]],
            "duration_ok": duration_ok,
            "peak_ndvi": peak,
            "expected_peak_ndvi": expected_peak,
            "peak_ok": peak_ok,
            "season_type": season or None,
            "expected_season": ref.get("season"),
            "season_ok": season_ok,
        })

    verdict["evidence"] = {
        "n_cycles_checked": len(checks),
        "reference": {
            "min_days": ref["min_days"], "typical_days": ref["typical_days"],
            "max_days": ref["max_days"], "season": ref.get("season"),
            "expected_peak_ndvi": expected_peak,
        },
        "tolerances": {"duration": dur_tol, "peak_ndvi": peak_tol},
        "cycles": checks,
    }

    if len(checks) < min_cycles:
        verdict["reason"] = (
            f"Only {len(checks)} complete cycle(s) observed — not enough to "
            f"check a {crop} declaration against."
        )
        return verdict

    n_dur = sum(1 for c in checks if c["duration_ok"])
    n_peak = sum(1 for c in checks if c["peak_ok"])
    n_all = sum(1 for c in checks if c["duration_ok"] and c["peak_ok"])
    share = n_all / len(checks)

    verdict["evidence"].update({
        "n_duration_consistent": n_dur,
        "n_peak_consistent": n_peak,
        "n_fully_consistent": n_all,
        "consistent_share": round(share, 3),
    })

    min_share = float(getattr(P, "CROP_VERIFY_MIN_CONSISTENT_SHARE", 0.5))
    max_conf = float(getattr(P, "CROP_VERIFY_MAX_CONFIDENCE", 0.55))

    if share >= min_share:
        # Scales with agreement but never reaches certainty: corroboration of a
        # self-report, not a measurement of the crop.
        verdict["outcome"] = CONSISTENT
        verdict["confidence"] = round(max_conf * share, 3)
        verdict["reason"] = (
            f"Observed phenology is consistent with a {crop} declaration "
            f"({n_all} of {len(checks)} cycles match the {ref['min_days']}-"
            f"{ref['max_days']} day range and expected canopy). Treated as a "
            f"corroborated self-report, not a detection."
        )
    else:
        verdict["outcome"] = INCONSISTENT
        verdict["confidence"] = 0.0
        mismatches = [
            f"{int(c['duration_days'])}d" for c in checks if not c["duration_ok"]
        ]
        verdict["reason"] = (
            f"Observed phenology does not match a {crop} declaration: only "
            f"{n_all} of {len(checks)} cycles fit the expected "
            f"{ref['min_days']}-{ref['max_days']} day range"
            + (f" (observed {', '.join(mismatches[:4])})" if mismatches else "")
            + ". Scored crop-agnostically."
        )
        logger.info(
            "Declared crop %r inconsistent with observed phenology "
            "(%d/%d cycles matched) — crop-agnostic scoring used.",
            crop, n_all, len(checks),
        )

    return verdict
