"""
Parcel viability
================
Is this parcel physically measurable with 10 m imagery, and do we trust its
boundary enough to spend quota on it?

WHY THIS EXISTS
───────────────
A geometry audit over the live database found two systemic problems that no
amount of signal processing can fix:

  1. SIZE. A Sentinel-2 pixel is 100 m² (0.01 ha). Of 113 stored parcels, 60
     were under 20 pixels and 24 were under FIVE. At four pixels the AOI mean is
     mostly neighbouring land — roads, the next field, a building — whatever the
     boundary says. No index computed over that describes the farmer's crop.

  2. BOUNDARY TRUST. Among AgriStack-ingested parcels only 7 of 106 had a
     plausible ratio between polygon area and registered area. Ratios spanned
     0.022 to 1841 in BOTH directions (log10 stdev 0.92). A constant unit error
     would cluster on one value; this scatter means the polygon and the
     registered area are describing DIFFERENT parcels. App-drawn boundaries, by
     contrast, were 7/7 within tolerance.

Scoring either kind of parcel produces a confident number about land we did not
actually measure. That is worse than no number, because a number gets used.

WHAT THIS IS NOT
────────────────
It does not decide which of the two areas is correct — we cannot know that from
here, and guessing would just move the error. It reports that they disagree.
Fixing the pairing is an ingest concern, not a scoring one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config import PipelineConfig

logger = logging.getLogger(__name__)

VIABILITY_VERSION = "parcel_viability_v1"

VIABLE = "viable"
MARGINAL = "marginal"
NOT_VIABLE = "not_viable"

# One Sentinel-2 pixel at 10 m resolution.
PIXEL_HA = 0.01

__all__ = [
    "assess_parcel_viability",
    "VIABILITY_VERSION",
    "VIABLE",
    "MARGINAL",
    "NOT_VIABLE",
]


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def assess_parcel_viability(
    *,
    registered_ha: Optional[float] = None,
    geometry_ha: Optional[float] = None,
    farmer_id: Optional[str] = None,
) -> Dict:
    """
    Decide whether this parcel can be honestly measured.

    Returns a verdict dict, always stamped onto the assessment.

        viable      big enough, and the two area figures agree
        marginal    measurable but small, or the areas disagree — score with a
                    confidence discount and a visible flag
        not_viable  too few pixels for any index to describe this parcel
    """
    P = PipelineConfig
    reg = _f(registered_ha)
    geo = _f(geometry_ha)

    hard_px = int(getattr(P, "PARCEL_MIN_PIXELS_HARD", 5))
    reliable_px = int(getattr(P, "PARCEL_MIN_PIXELS_RELIABLE", 20))
    mismatch_lo = float(getattr(P, "PARCEL_AREA_RATIO_MIN", 0.8))
    mismatch_hi = float(getattr(P, "PARCEL_AREA_RATIO_MAX", 1.25))

    # Prefer the polygon: it is what we actually measure over. The registered
    # figure is a claim about the parcel, not a description of the footprint.
    effective = geo or reg
    pixels = int(effective / PIXEL_HA) if effective else 0

    ratio = (geo / reg) if (geo and reg) else None
    areas_disagree = bool(ratio is not None and not (mismatch_lo <= ratio <= mismatch_hi))

    evidence = {
        "registered_ha": reg,
        "geometry_ha": geo,
        "effective_ha": effective,
        "approx_pixels": pixels,
        "area_ratio": round(ratio, 3) if ratio is not None else None,
        "areas_disagree": areas_disagree,
        "thresholds": {
            "min_pixels_hard": hard_px,
            "min_pixels_reliable": reliable_px,
            "area_ratio_range": [mismatch_lo, mismatch_hi],
        },
    }

    reasons = []

    if not effective:
        return {
            "version": VIABILITY_VERSION,
            "outcome": NOT_VIABLE,
            "reason": "No usable area for this parcel — neither a polygon nor a registered area.",
            "evidence": evidence,
        }

    if pixels < hard_px:
        reason = (
            f"Parcel covers about {pixels} Sentinel-2 pixel(s) "
            f"({effective:.4f} ha). Below {hard_px} pixels the measurement is "
            f"dominated by neighbouring land, so no index describes this "
            f"farmer's crop."
        )
        logger.warning("Parcel viability: NOT_VIABLE (%s) — %s", farmer_id, reason)
        return {
            "version": VIABILITY_VERSION,
            "outcome": NOT_VIABLE,
            "reason": reason,
            "evidence": evidence,
        }

    if pixels < reliable_px:
        reasons.append(
            f"only about {pixels} Sentinel-2 pixels ({effective:.3f} ha), so the "
            f"signal carries some neighbouring land"
        )

    if areas_disagree:
        reasons.append(
            f"the polygon ({geo:.4f} ha) and the registered area ({reg:.4f} ha) "
            f"disagree by {ratio:.2f}x — one of them describes a different "
            f"parcel, and we cannot tell which"
        )

    if reasons:
        return {
            "version": VIABILITY_VERSION,
            "outcome": MARGINAL,
            "reason": "Parcel is measurable but not fully trustworthy: "
                      + "; ".join(reasons) + ".",
            "evidence": evidence,
        }

    return {
        "version": VIABILITY_VERSION,
        "outcome": VIABLE,
        "reason": (
            f"Parcel covers about {pixels} Sentinel-2 pixels and its polygon "
            f"agrees with the registered area."
        ),
        "evidence": evidence,
    }


def viability_gate_penalty(verdict: Optional[Dict]) -> float:
    """
    Confidence-gate multiplier for a marginal parcel.

    A score measured over a partly-neighbouring footprint, or over a boundary
    we do not trust, must not carry the same confidence as a clean one.
    """
    if not isinstance(verdict, dict) or verdict.get("outcome") != MARGINAL:
        return 1.0
    return float(getattr(PipelineConfig, "PARCEL_MARGINAL_GATE_PENALTY", 0.85))
