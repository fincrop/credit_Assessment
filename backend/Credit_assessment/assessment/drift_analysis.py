"""
Score drift analysis
====================
Compares assessments produced by two versions of the pipeline and explains what
moved, and why.

WHY THIS EXISTS
───────────────
Phases 1, 2 and 4 changed scores deliberately — the signal was rescaled onto a
physical scale, cycle thresholds were re-derived, perennials became detectable,
and several sub-indices stopped rewarding the absence of evidence. All of that
is a correction, but "we fixed it, trust us" is not something to put in front of
a lender. Before any of these numbers are shown externally, we need to say
concretely: how much moved, in which direction, for whom, and driven by which
pillar.

APPROACH
────────
The old pipeline cannot be re-run — but it does not need to be. Its outputs are
already in `credit_assessments`. So the comparison is:

    baseline  = the most recent stored assessment written by the old code
    current   = a fresh run of the pipeline as it stands now

Everything here is pure: no database, no network. The CLI in
scripts/devtools/score_drift.py does the I/O and hands dicts to these functions,
which keeps the analysis unit-testable.

HONESTY RULES
─────────────
* A missing baseline is reported as missing, never treated as zero. A farmer
  with no prior assessment is not a farmer whose score fell to nothing.
* Newly-rejected parcels are reported SEPARATELY from score movement. A parcel
  that is now refused as non-agricultural has no "new score" — folding it in as
  a large negative delta would corrupt the distribution.
* Aggregates state their sample size, and small samples are labelled as such.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "compare_one",
    "compare_many",
    "summarise",
    "format_report",
    "describe_document",
    "RiskBand",
]

# Order matters: used to decide whether a band move is an upgrade or downgrade.
RiskBand = ("VERY_HIGH", "HIGH", "MEDIUM", "LOW")

REJECTED_STATUS = "REJECTED_NOT_AGRICULTURAL"
INSUFFICIENT_STATUS = "INSUFFICIENT_DATA"

# Movement larger than this is called out individually rather than only counted.
LARGE_MOVE_POINTS = 15.0


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _score_of(doc: Optional[Dict]) -> Optional[float]:
    """Index score from either document shape (single-farm or multi-farm)."""
    if not isinstance(doc, dict):
        return None
    for path in (
        ("index_score",),
        ("credit_score",),
        ("risk_assessment", "index_score"),
        ("farmer_level", "index_score"),
    ):
        node: Any = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        val = _num(node)
        if val is not None:
            return val
    return None


def _band_of(doc: Optional[Dict]) -> Optional[str]:
    if not isinstance(doc, dict):
        return None
    for path in (
        ("risk_category",),
        ("risk_assessment", "risk_category"),
        ("farmer_level", "risk_category"),
    ):
        node: Any = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node.upper()
    return None


def _sub_scores(doc: Optional[Dict]) -> Dict[str, float]:
    """Sub-index scores, tolerant of the several shapes in use."""
    if not isinstance(doc, dict):
        return {}
    subs = (
        (doc.get("risk_assessment") or {}).get("sub_indices")
        or (doc.get("farmer_level") or {}).get("sub_indices")
        or doc.get("component_scores")
        or {}
    )
    out: Dict[str, float] = {}
    for key, val in (subs or {}).items():
        score = val.get("score") if isinstance(val, dict) else val
        num = _num(score)
        if num is not None:
            out[str(key)] = num
    return out


def _cycle_count(doc: Optional[Dict]) -> Optional[int]:
    if not isinstance(doc, dict):
        return None
    for path in (
        ("crop_cycles", "cycles_count"),
        ("performance_summary", "n_complete_cycles"),
        ("risk_assessment", "sub_indices", "landuse", "inputs", "n_complete_cycles"),
    ):
        node: Any = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        val = _num(node)
        if val is not None:
            return int(val)
    cycles = (doc.get("crop_cycles") or {}).get("cycles")
    return len(cycles) if isinstance(cycles, list) else None


def _is_perennial(doc: Optional[Dict]) -> bool:
    if not isinstance(doc, dict):
        return False
    lu_inputs = (
        ((doc.get("risk_assessment") or {}).get("sub_indices") or {})
        .get("landuse", {})
    )
    if isinstance(lu_inputs, dict):
        if str((lu_inputs.get("inputs") or {}).get("cycle_kind", "")).lower() == "perennial":
            return True
    cycles = (doc.get("crop_cycles") or {}).get("cycles") or []
    return any(
        str((c or {}).get("cycle_kind", "")).lower() == "perennial"
        for c in cycles if isinstance(c, dict)
    )


def describe_document(doc: Optional[Dict]) -> Dict:
    """
    Compact description of a stored assessment: what shape it is, and whether a
    score can be recovered from it.

    Used by --plan so the operator can see WHY a baseline is or is not usable,
    rather than just seeing a blank. A document with no recoverable score is a
    finding in itself — it means that assessment cannot participate in drift
    measurement, and knowing which shape it is says how to fix that.
    """
    if not isinstance(doc, dict):
        return {"shape": "missing", "score": None, "band": None}

    if doc.get("assessment_type") == "multi_farm" or "farmer_level" in doc:
        shape = "multi_farm"
    elif "risk_assessment" in doc:
        shape = "single_farm_v5"
    elif "component_scores" in doc or "credit_score" in doc:
        shape = "single_farm_legacy"
    else:
        shape = "unrecognised"

    return {
        "shape": shape,
        "score": _score_of(doc),
        "band": _band_of(doc),
        "status": doc.get("status"),
        "n_plots_scored": doc.get("n_plots_scored"),
        "index_version": doc.get("index_version"),
        "top_level_keys": sorted(k for k in doc if not k.startswith("_"))[:12],
    }


def compare_one(farmer_id: str, baseline: Optional[Dict], current: Optional[Dict]) -> Dict:
    """
    Compare one farmer's baseline and current assessment.

    The `outcome` field is the important one — it separates cases that are NOT
    comparable (no baseline, newly rejected) from genuine score movement, so
    they never pollute the delta distribution.
    """
    result: Dict[str, Any] = {
        "farmer_id": farmer_id,
        "baseline_score": _score_of(baseline),
        "current_score": _score_of(current),
        "baseline_band": _band_of(baseline),
        "current_band": _band_of(current),
        "baseline_cycles": _cycle_count(baseline),
        "current_cycles": _cycle_count(current),
        "now_perennial": _is_perennial(current) and not _is_perennial(baseline),
    }

    current_status = str((current or {}).get("status", "")).upper()

    if current_status == REJECTED_STATUS:
        lc = (current or {}).get("land_cover") or {}
        result.update({
            "outcome": "newly_rejected" if baseline else "rejected_no_baseline",
            "delta": None,
            "rejection_class": lc.get("class") or (current or {}).get("rejection_class"),
            "rejection_confidence": _num(lc.get("confidence")),
            "rejection_reason": lc.get("reason") or (current or {}).get("rejection_reason"),
            # The raw index statistics the verdict rested on. A rejection that
            # cannot be audited is not usable: it either refuses a real farmer
            # or hides a broken gate, and there is no way to tell which without
            # the numbers.
            "rejection_evidence": lc.get("evidence"),
            "rejection_streams": lc.get("streams"),
            "rejection_notes": lc.get("notes"),
        })
        return result

    if current_status == INSUFFICIENT_STATUS:
        # Not a score and not a rejection: we could not observe the parcel well
        # enough to say anything. Excluded from the delta distribution for the
        # same reason rejections are — there is no new score to compare.
        # INSUFFICIENT_DATA arrives from two different gates with different
        # evidence, and conflating them printed empty fields: a parcel refused
        # for SIZE has no observation record at all, because it was stopped
        # before any imagery was pulled.
        ds = (current or {}).get("data_sufficiency") or {}
        pv = (current or {}).get("parcel_viability") or {}
        ds_ev = ds.get("evidence") or {}
        pv_ev = pv.get("evidence") or {}
        result.update({
            "outcome": "now_insufficient_data" if baseline else "insufficient_no_baseline",
            "delta": None,
            "insufficient_cause": "parcel_size" if pv_ev else "observation_coverage",
            "insufficient_reason": (
                ds.get("reason")
                or pv.get("reason")
                or (current or {}).get("insufficient_reason")
            ),
            # Observation coverage (only when imagery was actually collected)
            "observed_fraction": ds_ev.get("observed_fraction"),
            "largest_blind_gap_days": ds_ev.get("largest_blind_gap_days"),
            # Parcel size (only when the size gate stopped it)
            "approx_pixels": pv_ev.get("approx_pixels"),
            "effective_ha": pv_ev.get("effective_ha"),
            "area_ratio": pv_ev.get("area_ratio"),
        })
        return result

    if result["baseline_score"] is None:
        # Rule P-1: no baseline is not a baseline of zero.
        result.update({"outcome": "no_baseline", "delta": None})
        return result

    if result["current_score"] is None:
        result.update({
            "outcome": "current_failed",
            "delta": None,
            "current_error": (current or {}).get("error"),
        })
        return result

    delta = result["current_score"] - result["baseline_score"]
    result["delta"] = round(delta, 2)
    result["outcome"] = "compared"

    # Attribution: which pillar accounts for the movement.
    base_subs, cur_subs = _sub_scores(baseline), _sub_scores(current)
    shared = sorted(set(base_subs) & set(cur_subs))
    sub_deltas = {k: round(cur_subs[k] - base_subs[k], 2) for k in shared}
    result["sub_index_deltas"] = sub_deltas
    if sub_deltas:
        driver = max(sub_deltas, key=lambda k: abs(sub_deltas[k]))
        result["primary_driver"] = driver
        result["primary_driver_delta"] = sub_deltas[driver]
    result["sub_indices_only_in_current"] = sorted(set(cur_subs) - set(base_subs))

    # Band movement, directional.
    b_band, c_band = result["baseline_band"], result["current_band"]
    if b_band in RiskBand and c_band in RiskBand:
        move = RiskBand.index(c_band) - RiskBand.index(b_band)
        result["band_move"] = move
        result["band_direction"] = (
            "improved" if move > 0 else "worsened" if move < 0 else "unchanged"
        )
    result["large_move"] = abs(delta) >= LARGE_MOVE_POINTS
    return result


def compare_many(
    baselines: Dict[str, Dict],
    currents: Dict[str, Dict],
) -> List[Dict]:
    """Compare every farmer present in either set."""
    ids = sorted(set(baselines) | set(currents))
    return [compare_one(fid, baselines.get(fid), currents.get(fid)) for fid in ids]


def _quantile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def summarise(comparisons: List[Dict]) -> Dict:
    """
    Aggregate the comparisons.

    Every figure states the sample it was computed over, and non-comparable
    cases are counted separately rather than silently dropped.
    """
    compared = [c for c in comparisons if c.get("outcome") == "compared"]
    deltas = [c["delta"] for c in compared if c.get("delta") is not None]

    by_outcome: Dict[str, int] = {}
    for c in comparisons:
        key = str(c.get("outcome", "unknown"))
        by_outcome[key] = by_outcome.get(key, 0) + 1

    # Band migration matrix.
    migration: Dict[str, int] = {}
    for c in compared:
        b, n = c.get("baseline_band"), c.get("current_band")
        if b and n:
            migration[f"{b}->{n}"] = migration.get(f"{b}->{n}", 0) + 1

    # Which pillar drove movement most often.
    driver_counts: Dict[str, int] = {}
    for c in compared:
        d = c.get("primary_driver")
        if d:
            driver_counts[d] = driver_counts.get(d, 0) + 1

    insufficient = [
        c for c in comparisons if c.get("outcome") == "now_insufficient_data"
    ]
    rejected = [c for c in comparisons if c.get("outcome") == "newly_rejected"]
    rejection_classes: Dict[str, int] = {}
    for c in rejected:
        key = str(c.get("rejection_class") or "UNKNOWN")
        rejection_classes[key] = rejection_classes.get(key, 0) + 1

    mean_delta = (sum(deltas) / len(deltas)) if deltas else None

    return {
        "n_total": len(comparisons),
        "n_compared": len(compared),
        "outcomes": by_outcome,
        # Explicit: aggregates below are over n_compared, not n_total.
        "delta_sample_size": len(deltas),
        "small_sample": len(deltas) < 20,
        "mean_delta": round(mean_delta, 2) if mean_delta is not None else None,
        "median_delta": round(_quantile(deltas, 0.5), 2) if deltas else None,
        "p10_delta": round(_quantile(deltas, 0.10), 2) if deltas else None,
        "p90_delta": round(_quantile(deltas, 0.90), 2) if deltas else None,
        "max_increase": round(max(deltas), 2) if deltas else None,
        "max_decrease": round(min(deltas), 2) if deltas else None,
        "n_improved": sum(1 for d in deltas if d > 0),
        "n_worsened": sum(1 for d in deltas if d < 0),
        "n_unchanged": sum(1 for d in deltas if d == 0),
        "n_large_moves": sum(1 for c in compared if c.get("large_move")),
        "band_migration": dict(sorted(migration.items(), key=lambda kv: -kv[1])),
        "n_band_improved": sum(1 for c in compared if c.get("band_direction") == "improved"),
        "n_band_worsened": sum(1 for c in compared if c.get("band_direction") == "worsened"),
        "primary_drivers": dict(sorted(driver_counts.items(), key=lambda kv: -kv[1])),
        "n_newly_rejected": len(rejected),
        "rejection_classes": dict(sorted(rejection_classes.items(), key=lambda kv: -kv[1])),
        "n_now_insufficient": len(insufficient),
        "n_newly_perennial": sum(1 for c in comparisons if c.get("now_perennial")),
    }


def _fmt(v: Optional[float], width: int = 7) -> str:
    return "—".rjust(width) if v is None else f"{v:+.1f}".rjust(width)


def format_report(comparisons: List[Dict], summary: Dict, top_n: int = 15) -> str:
    """Human-readable report for the console or a file."""
    L: List[str] = []
    add = L.append

    add("=" * 72)
    add("SCORE DRIFT REPORT")
    add("=" * 72)
    add("")
    add(f"Farmers examined        : {summary['n_total']}")
    add(f"Directly comparable     : {summary['n_compared']}")
    for outcome, count in sorted(summary["outcomes"].items()):
        if outcome != "compared":
            add(f"  {outcome:22s}: {count}")
    add("")

    if not summary["delta_sample_size"]:
        add("No farmer had both a baseline and a current score.")
        add("Nothing can be concluded about drift from this run.")
        return "\n".join(L)

    if summary["small_sample"]:
        add(f"!! SMALL SAMPLE ({summary['delta_sample_size']}). Treat the")
        add("   distribution below as indicative, not representative.")
        add("")

    add(f"-- Score movement (n={summary['delta_sample_size']}) " + "-" * 30)
    add(f"  mean            {_fmt(summary['mean_delta'])}")
    add(f"  median          {_fmt(summary['median_delta'])}")
    add(f"  p10 / p90       {_fmt(summary['p10_delta'])} / {_fmt(summary['p90_delta'])}")
    add(f"  largest rise    {_fmt(summary['max_increase'])}")
    add(f"  largest fall    {_fmt(summary['max_decrease'])}")
    add(f"  improved / worsened / unchanged : "
        f"{summary['n_improved']} / {summary['n_worsened']} / {summary['n_unchanged']}")
    add(f"  moves >= {LARGE_MOVE_POINTS:.0f} points : {summary['n_large_moves']}")
    add("")

    if summary["band_migration"]:
        add("-- Risk band migration " + "-" * 45)
        for move, count in summary["band_migration"].items():
            marker = "" if move.split("->")[0] == move.split("->")[1] else "  <-- changed"
            add(f"  {move:28s} {count:4d}{marker}")
        add(f"  bands improved / worsened : "
            f"{summary['n_band_improved']} / {summary['n_band_worsened']}")
        add("")

    if summary["primary_drivers"]:
        add("-- What drove the movement " + "-" * 41)
        for driver, count in summary["primary_drivers"].items():
            add(f"  {driver:28s} {count:4d} farmer(s)")
        add("")

    if summary["n_newly_rejected"]:
        add("-- Newly rejected as non-agricultural " + "-" * 30)
        add(f"  {summary['n_newly_rejected']} parcel(s) previously scored are now refused.")
        for cls, count in summary["rejection_classes"].items():
            add(f"    {cls:26s} {count:4d}")
        add("  These are excluded from the movement statistics above:")
        add("  they have no new score, and counting them as a large fall")
        add("  would corrupt the distribution.")
        add("")
        add("  EVERY rejection below should be eyeballed against imagery before")
        add("  this is trusted. A false rejection refuses a real farmer.")
        add("")
        for c in [x for x in comparisons if x.get("outcome") == "newly_rejected"][:top_n]:
            add(f"    {c['farmer_id']:24s} was {c['baseline_score']:.1f} "
                f"-> {c.get('rejection_class')} "
                f"(confidence {c.get('rejection_confidence')})")
            ev = c.get("rejection_evidence") or {}
            if ev:
                add(f"      NDVI p10/p50/p90 : {ev.get('ndvi_p10')} / "
                    f"{ev.get('ndvi_p50')} / {ev.get('ndvi_p90')}")
                add(f"      NDVI amplitude   : {ev.get('ndvi_amplitude')}")
                add(f"      water+ fraction  : {ev.get('water_frac_positive')} "
                    f"({ev.get('water_index_source')})")
                add(f"      built-up+ frac   : {ev.get('ndbi_frac_positive')}   "
                    f"BSI p50: {ev.get('bsi_p50')}")
                add(f"      observations     : {ev.get('n_obs')}")
            for note in (c.get("rejection_notes") or [])[:3]:
                add(f"      - {note}")
            add("")

    if summary.get("n_now_insufficient"):
        add("-- Now reported as insufficiently observed " + "-" * 25)
        add(f"  {summary['n_now_insufficient']} parcel(s) previously scored cannot")
        add("  honestly be assessed: no cycles were found, but the record has a")
        add("  blind stretch long enough to have hidden an entire season.")
        add("  Scoring these would deny credit on the satellite's cloud luck.")
        add("")
        for c in [x for x in comparisons
                  if x.get("outcome") == "now_insufficient_data"][:top_n]:
            cause = c.get("insufficient_cause")
            add(f"    {c['farmer_id']:24s} was {c['baseline_score']:.1f}   "
                f"[{cause or 'unknown'}]")
            if cause == "parcel_size":
                add(f"      effective area    : {c.get('effective_ha')} ha "
                    f"(~{c.get('approx_pixels')} pixels)")
                if c.get("area_ratio") is not None:
                    add(f"      polygon/registered: {c.get('area_ratio')}x")
                add("      (stopped before imagery — no observation record)")
            else:
                add(f"      observed          : {c.get('observed_fraction')}")
                add(f"      largest blind gap : {c.get('largest_blind_gap_days')} days")
        add("")

    if summary["n_newly_perennial"]:
        add(f"-- Newly detected as perennial: {summary['n_newly_perennial']} parcel(s)")
        add("   These previously returned zero cycles and scored as abandoned land.")
        add("")

    large = sorted(
        [c for c in comparisons if c.get("outcome") == "compared" and c.get("large_move")],
        key=lambda c: -abs(c["delta"]),
    )
    if large:
        add(f"-- Largest movements (top {min(top_n, len(large))}) " + "-" * 34)
        add(f"  {'farmer':24s} {'before':>8s} {'after':>8s} {'delta':>8s}  driver")
        for c in large[:top_n]:
            add(f"  {c['farmer_id']:24s} {c['baseline_score']:8.1f} "
                f"{c['current_score']:8.1f} {c['delta']:+8.1f}  "
                f"{c.get('primary_driver', '—')} "
                f"({c.get('primary_driver_delta', 0):+.1f})")
        add("")

    add("=" * 72)
    return "\n".join(L)
