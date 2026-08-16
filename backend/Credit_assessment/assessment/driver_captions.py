"""
Sub-index driver captions
=========================
One plain sentence per sub-index, saying what actually drove it.

WHY DETERMINISTIC
─────────────────
The report needs a short explanation under each sub-index bar, and every one of
them must cite a real computed number. An LLM asked to write these would produce
fluent sentences containing invented figures — the exact failure mode the
provenance contract forbids (rule P-7: generated prose may only RESTATE computed
values, never produce them).

So these are templates filled from `sub_indices[*].inputs` and `drivers`, which
the risk engine already emits. Every number in the output is traceable to a
field in the payload. If a value is absent the caption says so rather than
guessing — a caption is worthless if a lender cannot trust its numbers.

An LLM may still POLISH these downstream. It must not be the source.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["build_driver_captions", "CAPTIONS_VERSION"]

CAPTIONS_VERSION = "driver_captions_v1"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _caption_landuse(sub: Dict) -> str:
    inp = sub.get("inputs") or {}
    drv = sub.get("drivers") or {}

    if str(inp.get("cycle_kind", "")).lower() == "perennial":
        years = inp.get("n_production_years")
        persistence = _num(drv.get("canopy_persistence"))
        stability = _num(drv.get("inter_annual_stability"))
        bits = []
        if years:
            bits.append(f"{years} production year(s) observed")
        if persistence is not None:
            bits.append(f"canopy persistence {persistence:.0f}/100")
        if stability is not None:
            bits.append(f"year-to-year stability {stability:.0f}/100")
        return (
            "Perennial planting: " + ", ".join(bits) + "."
            if bits else
            "Perennial planting; insufficient detail to describe the drivers."
        )

    cpi = _num(inp.get("cycles_per_year"))
    n = inp.get("n_complete_cycles")
    years = _num(inp.get("years"))
    coverage = _num(inp.get("season_coverage"))
    fallow = _num(inp.get("fallow_fraction"))
    basis = str(inp.get("fallow_basis") or "")

    if cpi is None and n is None:
        return "No cropping activity could be measured on this parcel."

    parts = []
    if n is not None and years:
        parts.append(f"{n} cycle(s) over {years:.0f} years ({cpi:.2f}/year)")
    elif cpi is not None:
        parts.append(f"{cpi:.2f} cycles per year")
    if coverage is not None:
        parts.append(f"{_pct(coverage)} season coverage")
    if fallow is not None:
        if basis == "inferred_no_cycles":
            parts.append("fallow throughout (no cycles detected)")
        else:
            parts.append(f"{_pct(fallow * 100)} of the window fallow")
    return "Driven by " + "; ".join(parts) + "."


def _caption_vigor(sub: Dict) -> str:
    inp = sub.get("inputs") or {}
    mean_yield = _num(inp.get("mean_yield_potential"))
    peak = _num(inp.get("mean_peak_cvi"))
    n = inp.get("n_cycles_scored")
    peer = bool(inp.get("peer_relative"))
    n_peer = inp.get("n_cycles_peer_scored") or 0

    if not n:
        return (
            "No crop cycle was scored, so there is no vigour evidence for this "
            "parcel."
        )
    parts = [f"mean yield potential {mean_yield:.0f}/100 across {n} cycle(s)"]
    if peak is not None:
        parts.append(f"average peak canopy {peak:.2f}")
    # Only claim a peer comparison when one actually ran.
    parts.append(
        f"benchmarked against {n_peer} peer-scored cycle(s)" if peer
        else "measured on an absolute scale (no peer cohort available yet)"
    )
    return "Driven by " + "; ".join(parts) + "."


def _caption_stability(sub: Dict) -> str:
    inp = sub.get("inputs") or {}
    if inp.get("no_cultivation_evidence"):
        return (
            "No cultivation was observed, so the absence of stress events is "
            "not evidence of stability."
        )
    n_h = inp.get("anomalies_high") or 0
    n_m = inp.get("anomalies_medium") or 0
    n_l = inp.get("anomalies_low") or 0
    seasons = inp.get("n_seasons_observed")
    cv = _num(inp.get("vigor_cv"))

    total = n_h + n_m + n_l
    if total == 0:
        head = f"No stress anomalies across {seasons or 0} observed season(s)"
    else:
        head = (
            f"{total} stress event(s) across {seasons or 0} season(s) "
            f"({n_h} high, {n_m} medium, {n_l} low)"
        )
    tail = (
        f"; year-to-year yield variation {cv:.0%}" if cv is not None
        else "; too few cycles to measure year-to-year variation"
    )
    return head + tail + "."


def _caption_weather(sub: Dict) -> str:
    inp = sub.get("inputs") or {}
    drv = sub.get("drivers") or {}
    resilience = _num(drv.get("resilience"))
    exposure = _num(inp.get("forward_exposure"))
    risk = _num(inp.get("weather_risk_score"))

    parts = []
    if resilience is not None:
        parts.append(f"held vigour through past adverse weather at {resilience:.0f}/100")
    if exposure is not None:
        parts.append(f"forward climate exposure {exposure:.0f}/100")
    if risk is not None:
        parts.append(f"observed weather risk {risk:.0f}/100")
    return (
        "Driven by " + "; ".join(parts) + "."
        if parts else
        "Weather analysis was not available for this parcel."
    )


def _caption_data_confidence(sub: Dict, gate: Optional[float]) -> str:
    inp = sub.get("inputs") or {}
    valid = _num(inp.get("valid_fraction"))
    quality = _num(inp.get("mean_bin_quality"))
    sar = _num(inp.get("sar_fallback_fraction"))
    n_cycles = inp.get("n_cycles")

    parts = []
    if valid is not None:
        parts.append(f"{_pct(valid * 100)} of observation slots usable")
    if quality is not None:
        parts.append(f"mean scene quality {quality:.2f}")
    if sar:
        parts.append(f"{_pct(sar * 100)} of the signal from radar rather than optical")
    if n_cycles is not None:
        parts.append(f"{n_cycles} cycle(s) of history")
    body = "; ".join(parts) if parts else "observation quality could not be summarised"
    if gate is not None:
        return f"{body}. The score was scaled by ×{gate:.2f} to reflect this."
    return body + "."


_BUILDERS = {
    "landuse": _caption_landuse,
    "vigor": _caption_vigor,
    "stability": _caption_stability,
    "weather": _caption_weather,
}


def build_driver_captions(risk_assessment: Optional[Dict]) -> Dict[str, str]:
    """
    One caption per sub-index, keyed by sub-index name.

    Returns {} when there is no risk assessment — an empty caption set is
    honest; a set of generic sentences would not be.
    """
    if not isinstance(risk_assessment, dict):
        return {}
    subs = risk_assessment.get("sub_indices") or {}
    if not subs:
        return {}

    out: Dict[str, str] = {}
    for name, builder in _BUILDERS.items():
        sub = subs.get(name)
        if isinstance(sub, dict):
            try:
                out[name] = builder(sub)
            except Exception:  # a caption must never break an assessment
                out[name] = "Driver detail unavailable."

    dc = subs.get("data_confidence")
    if isinstance(dc, dict):
        try:
            out["data_confidence"] = _caption_data_confidence(
                dc, _num(risk_assessment.get("confidence_gate"))
            )
        except Exception:
            out["data_confidence"] = "Data confidence detail unavailable."

    # Footprint caveat belongs with the captions: a score measured over a
    # substituted boundary should say so wherever it is explained.
    footprint = risk_assessment.get("footprint") or {}
    if footprint.get("geometry_substituted"):
        out["footprint"] = (
            "This score was measured over a substituted boundary, not the one "
            "supplied for this parcel. Re-draw the boundary before relying on it."
        )
    return out
