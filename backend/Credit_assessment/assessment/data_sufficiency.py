"""
Data sufficiency
================
Decides whether we observed a parcel well enough to make ANY claim about it.

WHY THIS EXISTS
───────────────
The first real drift run split cleanly into two groups:

    cycles detected (n=5)   mean score change  -8.6   (healthy correction)
    zero cycles     (n=3)   mean score change -50.5   (all landing at ~26)

The second group had previously scored 71-83 and now score ~26 VERY_HIGH. Both
numbers are wrong. The old pipeline manufactured cycles from noise; the new one
correctly finds none — but then reports a confident VERY_HIGH for a parcel it
could barely see. One of those parcels had TWO 240-day stretches with no usable
optical observation at all: 41% of the window blind.

You cannot conclude "nothing grew here" from a period you did not observe.
A 240-day gap is longer than any annual crop cycle, so an entire season could
have been sown, grown and harvested inside it without leaving a trace in our
data. Scoring that parcel VERY_HIGH is not caution — it is a false claim, and it
denies a farmer credit on the strength of a satellite's cloud luck.

THE RULE
────────
Physically grounded, so it needs no ground truth to justify:

    If NO cycles were detected, and the record contains a contiguous
    unobserved gap longer than the shortest crop cycle we would recognise,
    then the absence of cycles is not evidence — it is ignorance.

Such a parcel returns INSUFFICIENT_DATA: a third terminal state, distinct from
both a score and a rejection.

    SUCCESS                     we looked, and here is the score
    REJECTED_NOT_AGRICULTURAL   we looked, and it is not farmland
    INSUFFICIENT_DATA           we could not see well enough to say

Note the asymmetry, which is deliberate: this only applies when NO cycles were
found. If we DID observe cycles, gaps elsewhere in the record reduce confidence
(via the data-confidence gate) but do not invalidate what we saw.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import PipelineConfig

logger = logging.getLogger(__name__)

SUFFICIENCY_VERSION = "data_sufficiency_v1"

SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"

__all__ = [
    "assess_data_sufficiency",
    "SUFFICIENCY_VERSION",
    "SUFFICIENT",
    "INSUFFICIENT",
]


def _parse(d: Any) -> Optional[datetime]:
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _largest_observed_gap_days(dates: List, observed: List[bool]) -> Dict:
    """
    Longest run of consecutive UNOBSERVED bins, in days.

    Uses the bin dates so the figure is in calendar days rather than bin counts,
    which is what makes it comparable to a crop duration.
    """
    parsed = [_parse(d) for d in dates]
    best_days, best_span = 0, None
    run_start_idx = None

    for i, ok in enumerate(observed):
        if not ok:
            if run_start_idx is None:
                run_start_idx = i
            continue
        if run_start_idx is not None:
            lo = parsed[max(0, run_start_idx - 1)] or parsed[run_start_idx]
            hi = parsed[i]
            if lo and hi:
                days = (hi - lo).days
                if days > best_days:
                    best_days = days
                    best_span = (lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"))
            run_start_idx = None

    # Trailing unobserved run.
    if run_start_idx is not None:
        lo = parsed[max(0, run_start_idx - 1)] or parsed[run_start_idx]
        hi = parsed[-1]
        if lo and hi:
            days = (hi - lo).days
            if days > best_days:
                best_days = days
                best_span = (lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"))

    return {"days": int(best_days), "span": best_span}


def assess_data_sufficiency(
    continuous_data: Optional[Dict],
    *,
    n_cycles: int,
    field_area_ha: Optional[float] = None,
) -> Dict:
    """
    Can we make a claim about this parcel at all?

    Returns a verdict dict, always stamped onto the assessment so the evidence
    is visible whether or not it blocks.
    """
    P = PipelineConfig
    cd = continuous_data or {}
    dates = list(cd.get("dates") or [])
    sources = list(cd.get("signal_source") or [])
    n_bins = len(dates)

    # A bin is observed if it carries real imagery — optical or SAR-fused.
    # 'imputed' means reconstructed, and 'sar' alone is a weaker inference.
    observed_flags: List[bool] = []
    for i in range(n_bins):
        src = str(sources[i]).lower() if i < len(sources) else ""
        observed_flags.append(src in ("optical", "fused"))

    n_observed = sum(observed_flags)
    observed_fraction = (n_observed / n_bins) if n_bins else 0.0
    gap = _largest_observed_gap_days(dates, observed_flags) if n_bins else {"days": 0, "span": None}

    min_cycle_days = int(getattr(P, "CROP_CYCLE_MIN_DURATION_DAYS", 40))
    blind_gap_days = int(getattr(P, "DATA_SUFFICIENCY_BLIND_GAP_DAYS", 0)) or min_cycle_days
    min_observed = float(getattr(P, "DATA_SUFFICIENCY_MIN_OBSERVED_FRACTION", 0.35))

    evidence = {
        "n_bins": n_bins,
        "n_observed_bins": n_observed,
        "observed_fraction": round(observed_fraction, 3),
        "largest_blind_gap_days": gap["days"],
        "largest_blind_gap_span": gap["span"],
        "n_cycles_detected": int(n_cycles),
        "field_area_ha": field_area_ha,
        "thresholds": {
            "blind_gap_days": blind_gap_days,
            "min_observed_fraction": min_observed,
        },
    }

    # Only bites when there is nothing to go on. Observed cycles are evidence
    # regardless of gaps elsewhere; those reduce confidence, not validity.
    if n_cycles > 0:
        return {
            "version": SUFFICIENCY_VERSION,
            "outcome": SUFFICIENT,
            "reason": f"{n_cycles} cycle(s) observed.",
            "evidence": evidence,
        }

    reasons: List[str] = []
    if gap["days"] >= blind_gap_days:
        span = gap["span"]
        where = f" ({span[0]} to {span[1]})" if span else ""
        reasons.append(
            f"a {gap['days']}-day stretch{where} has no usable observation — "
            f"longer than the {blind_gap_days}-day minimum crop cycle, so an "
            f"entire season could have been grown unseen"
        )
    if observed_fraction < min_observed:
        reasons.append(
            f"only {observed_fraction:.0%} of the window was directly observed "
            f"(minimum {min_observed:.0%})"
        )

    if reasons:
        reason = (
            "No crop cycles were detected, but the parcel was not observed well "
            "enough to conclude that none occurred: " + "; ".join(reasons) + "."
        )
        logger.warning("Data sufficiency: INSUFFICIENT — %s", reason)
        return {
            "version": SUFFICIENCY_VERSION,
            "outcome": INSUFFICIENT,
            "reason": reason,
            "evidence": evidence,
        }

    return {
        "version": SUFFICIENCY_VERSION,
        "outcome": SUFFICIENT,
        "reason": (
            "No crop cycles detected, and observation coverage was good enough "
            "for that to be a meaningful finding."
        ),
        "evidence": evidence,
    }
