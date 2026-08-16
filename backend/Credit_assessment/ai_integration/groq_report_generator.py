"""
Groq Report Generator — VERSION 2.0
=====================================
Generates human-readable English credit assessment narratives using
Groq's ultra-fast LLM inference API.

Stage 7 update:
  - Prompt now includes Stage 4/5 outputs: cycle_risk_scores, anomaly_events,
    cultivation_signal, n_active_cycles, per-cycle performance narratives
  - Rule-based fallback upgraded to include anomaly + cycle summary
  - Added dry-run mode: when GROQ_DRY_RUN=1 returns prompt instead of calling API
  - System prompt updated to understand dynamic interval-based pipeline

Configuration:
    GROQ_API_KEY  — required for LLM generation (free tier available)
    GROQ_MODEL    — override model (default: llama-3.3-70b-versatile)
    GROQ_DRY_RUN  — set to "1" to return the prompt without calling API

Free API: https://console.groq.com  (sign up, get a free key, ~14,400 tokens/min)
"""

import os
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MODEL   = "llama-3.3-70b-versatile"


def _risk_view(assessment: Dict) -> Dict:
    """
    Unified read over the new `risk_assessment` (agronomic risk index v5,
    preferred) or the legacy `credit_assessment` (fallback). Keeps the AI layer
    working during the parallel-wiring period and after full cutover.
    """
    ra = assessment.get("risk_assessment")
    if isinstance(ra, dict) and ra.get("sub_indices"):
        subs = ra["sub_indices"]
        return {
            "schema": "risk_index_v5",
            "score": float(ra.get("index_score", 50)),
            "raw_index": ra.get("raw_index"),
            "risk_category": ra.get("risk_category", "UNKNOWN"),
            "component_scores": {k: float(v.get("score", 50)) for k, v in subs.items()},
            "weak_components": ra.get("weak_sub_indices", []),
            "weights": ra.get("weights", {}),
            "reason_codes": ra.get("reason_codes", []),
            "confidence_gate": ra.get("confidence_gate", 1.0),
            "benefits": ra.get("benefits", {}),
            "sub_indices": subs,
        }
    cr = assessment.get("credit_assessment", {}) or {}
    return {
        "schema": "credit_legacy",
        "score": float(cr.get("credit_score", 50)),
        "raw_index": None,
        "risk_category": cr.get("risk_category", "UNKNOWN"),
        "component_scores": cr.get("component_scores", {}),
        "weak_components": cr.get("weak_components", []),
        "weights": {},
        "reason_codes": [],
        "confidence_gate": 1.0,
        "benefits": assessment.get("farmer_benefits", {}) or {},
        "sub_indices": {},
    }

_GROQ_API_URL    = "https://api.groq.com/openai/v1/chat/completions"


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    """Read a bounded integer from the environment, falling back on junk input."""
    try:
        return max(lo, min(hi, int(os.environ.get(name, "").strip() or default)))
    except (TypeError, ValueError):
        return default


# Narrative generation is a synchronous, blocking step inside the assessment, so
# these bound how long a degraded Groq endpoint can stall a job. Defaults give a
# worst case of ~20*2 + 1.5 ≈ 42s; previously 30*3 + 4.5 ≈ 95s.
_REQUEST_TIMEOUT = _env_int("GROQ_TIMEOUT", 20, 5, 120)
_MAX_RETRIES     = _env_int("GROQ_MAX_RETRIES", 1, 0, 5)


class GroqReportGenerator:
    """
    Generates English credit report narrative using Groq's LLM API.
    Falls back gracefully when the API key is not configured.
    """

    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY")
        self.model   = os.environ.get("GROQ_MODEL", _DEFAULT_MODEL)
        self.dry_run = os.environ.get("GROQ_DRY_RUN", "0") == "1"

        if self.dry_run:
            logger.info("GroqReportGenerator: DRY RUN mode — prompt returned, no API call")
        elif self.api_key:
            logger.info(f"GroqReportGenerator v2.0 initialized (model={self.model})")
        else:
            logger.warning(
                "GROQ_API_KEY not set — rule-based fallback active. "
                "Get a free key at https://console.groq.com"
            )

    def generate(self, assessment: Dict) -> str:
        """
        Generate an English credit assessment narrative.

        Returns:
            Human-readable English narrative string.
        """
        prompt = self._build_prompt(assessment)

        if self.dry_run:
            return f"[DRY RUN — API not called]\n\nPROMPT:\n{prompt}"

        if not self.api_key:
            return self._rule_based_report(assessment)

        try:
            import urllib.request
            import time

            payload = json.dumps({
                "model":    self.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens":  650,
                "temperature": 0.3,
            }).encode("utf-8")

            last_exc = None
            for attempt in range(1, _MAX_RETRIES + 2):  # 1, 2, 3
                try:
                    req = urllib.request.Request(
                        _GROQ_API_URL,
                        data=payload,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type":  "application/json",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                        result = json.loads(resp.read().decode("utf-8"))
                        report = result["choices"][0]["message"]["content"].strip()
                        logger.info("Groq report generated (attempt %d)", attempt)
                        return report
                except Exception as exc:
                    last_exc = exc
                    status = getattr(getattr(exc, 'code', None), '__str__', lambda: str(exc))()
                    logger.warning("Groq attempt %d failed: %s", attempt, exc)
                    if attempt <= _MAX_RETRIES:
                        time.sleep(1.5 * attempt)  # 1.5s, 3s backoff

            raise last_exc

        except Exception as exc:
            logger.error("Groq API failed after retries: %s — rule-based fallback", exc)
            return self._rule_based_report(assessment)

    # =========================================================================
    # PROMPT BUILDER
    # =========================================================================

    @staticmethod
    def _build_prompt(assessment: Dict) -> str:
        """Build an enriched JSON summary of the agronomic RISK INDEX for the LLM."""
        rv  = _risk_view(assessment)
        ca  = assessment.get("cropping_analysis",   {}) or {}
        pa  = assessment.get("performance_analysis", {}) or {}
        wa  = assessment.get("weather_analysis",    {}) or {}

        # Per-cycle performance + phenology summary
        sp = pa.get("seasonal_performance", []) or []
        cycle_summaries = []
        for p in sp:
            yd = p.get("yield_detail", {}) or {}
            cycle_summaries.append({
                "cycle":          f"{str(p.get('season','?')).upper()} {p.get('year','?')}",
                "is_active":      p.get("is_active_cycle", False),
                "health_score":   p.get("health_score", 0),
                "yield_potential": p.get("yield_potential_score", yd.get("yield_potential_pct", "?")),
                "yield_basis":    yd.get("yield_index_basis", ""),
                "n_high_impact":  sum(1 for e in p.get("anomaly_events", []) if e.get("impact") == "HIGH"),
            })

        # Phenology + season inference from detected cycles
        agro_meta = []
        for cyc in ((assessment.get('crop_cycles', {}) or {}).get('cycles') or []):
            ph = cyc.get('phenology', {}) or {}
            agro_meta.append({
                'season':        cyc.get('season_label', ''),
                'season_type':   cyc.get('season_type', ''),
                'sowing':        str(cyc.get('sowing_date', ''))[:10],
                'harvest':       str(cyc.get('harvest_date', ''))[:10],
                'duration_days': cyc.get('duration_days', 0),
                'phenology_fit': ph.get('fit_ok'),
                'fit_r2':        ph.get('r2'),
            })

        det_meta = assessment.get('cycle_detection_diag', {}) or {}
        detector_meta = (ca.get('detection_meta') or {})
        seasonal_inference = detector_meta.get('seasonal_inference', {}) if isinstance(detector_meta, dict) else {}

        # Weather two-directional (Pillar 4)
        fwd = wa.get("forward_exposure", {}) or {}
        bwd = wa.get("backward_resilience", {}) or {}

        summary = {
            "farmer_id":            assessment.get("farmer_id"),
            "analysis_date":        assessment.get("analysis_date", ""),
            "positioning":          "agronomic_risk_index (0-100; NOT a loan amount)",
            "index_version":        assessment.get("risk_assessment", {}).get("index_version"),
            "risk_index_score":     rv["score"],
            "risk_category":        rv["risk_category"],
            "data_confidence_gate": rv["confidence_gate"],
            "sub_indices":          rv["component_scores"],
            "sub_index_weights":    rv["weights"],
            "weak_sub_indices":     rv["weak_components"],
            "reason_codes":         rv["reason_codes"],
            "govt_benefits":        rv["benefits"],
            # cropping / performance
            "n_complete_cycles":    pa.get("n_complete_cycles", 0),
            "n_active_cycles":      pa.get("n_active_cycles", 0),
            "cropping_intensity_per_year": ca.get("cropping_intensity"),
            "cycle_performance":    cycle_summaries,
            "agronomic_cycles":     agro_meta,
            "seasonal_inference":   seasonal_inference.get("per_year", {}) if isinstance(seasonal_inference, dict) else {},
            "peer_benchmarking":    pa.get("peer_benchmarking", {}),
            # weather (two-directional)
            "weather_risk_score":   wa.get("weather_risk_score"),
            "forward_exposure":     fwd,
            "backward_resilience":  bwd,
            "weather_sources_used": wa.get("weather_sources_used", []),
            "n_kharif":             det_meta.get("n_kharif", 0),
            "n_rabi":               det_meta.get("n_rabi", 0),
            "n_zaid":               det_meta.get("n_zaid", 0),
        }
        return (
            "Generate an agronomic credit-risk assessment report for the following "
            "farmer data (this is an expert-weighted RISK INDEX, not a loan amount):\n\n"
            + json.dumps(summary, indent=2, default=str)
        )

    # =========================================================================
    # RULE-BASED FALLBACK REPORT
    # =========================================================================

    @staticmethod
    def _rule_based_report(assessment: Dict) -> str:
        """Deterministic agronomic risk-index report — no LLM, no loan amount."""
        rv   = _risk_view(assessment)
        ca   = assessment.get("cropping_analysis",   {}) or {}
        pa   = assessment.get("performance_analysis", {}) or {}
        wa   = assessment.get("weather_analysis",    {}) or {}

        score = rv["score"]
        risk  = rv["risk_category"]
        gate  = rv["confidence_gate"]
        subs  = rv["component_scores"]
        weak  = rv["weak_components"]

        n_active = pa.get("n_active_cycles", 0)
        n_comp   = pa.get("n_complete_cycles", 0)
        fwd = (wa.get("forward_exposure", {}) or {})
        bwd = (wa.get("backward_resilience", {}) or {})

        # High-impact stress events
        sp = pa.get("seasonal_performance", []) or []
        n_high = sum(len([e for e in p.get("anomaly_events", []) if e.get("impact") == "HIGH"]) for p in sp)

        # Recommendation by risk band (agronomic; no rupee amount / rate)
        rec = {
            "LOW":       "Favourable agronomic profile — supports standard lending consideration.",
            "MEDIUM":    "Moderate agronomic risk — consider crop-insurance linkage / staged limits.",
            "HIGH":      "Elevated agronomic risk — recommend safeguards and closer monitoring.",
            "VERY_HIGH": "High agronomic risk — re-assess after further monitoring; safeguards essential.",
        }.get(risk, "Manual review recommended.")

        # Sub-index lines
        sub_lines = "\n".join(
            f"  {k.replace('_',' ').title():24s} {subs[k]:5.1f}/100"
            for k in ("landuse", "vigor", "stability", "weather", "data_confidence")
            if k in subs
        ) or "  (sub-index breakdown unavailable)"

        # Reason codes
        pos = [c for c in rv["reason_codes"] if c.get("polarity") == "positive"]
        neg = [c for c in rv["reason_codes"] if c.get("polarity") == "negative"]
        cav = [c for c in rv["reason_codes"] if c.get("polarity") == "caveat"]
        def _codes(lst):
            return "\n".join(f"  - {c.get('message','')}" for c in lst) or "  - (none)"

        gate_note = ("" if gate >= 0.98 else
                     f"\nData-confidence gate applied: x{gate:.2f} "
                     "(cloud gaps / short history reduced certainty).")
        anom_line = (f"\nStress Alert: {n_high} high-impact vegetation stress event(s) "
                     "detected by satellite." if n_high > 0 else "")
        active_note = (f"\nNote: {n_active} crop cycle(s) currently active — harvest income "
                       "expected; final capacity may be higher." if n_active > 0 else "")

        ben = rv["benefits"]
        ben_line = ""
        if isinstance(ben, dict) and ben.get("conferred"):
            ben_line = f"\nGovt schemes: {', '.join(ben['conferred'])}."

        return (
            f"AGRONOMIC CREDIT-RISK ASSESSMENT\n"
            f"{'='*44}\n"
            f"Farmer ID:      {assessment.get('farmer_id','N/A')}\n"
            f"Analysis Date:  {assessment.get('analysis_date','')}\n\n"
            f"RISK INDEX:     {score:.1f}/100   |   Risk Category: {risk}\n"
            f"(agronomic risk index — not a loan amount)"
            f"{gate_note}\n\n"
            f"SUB-INDEX BREAKDOWN\n"
            f"-------------------\n"
            f"{sub_lines}\n\n"
            f"FIELD ACTIVITY\n"
            f"--------------\n"
            f"Complete Cycles:   {n_comp}   |   Active: {n_active}\n"
            f"Weather Risk:      {wa.get('weather_risk_score','?')}/100"
            + (f"   |   Resilience: {bwd.get('mean_resilience_score')}" if bwd.get('mean_resilience_score') is not None else "")
            + (f"   |   Exposure: {fwd.get('exposure_score')}" if fwd.get('exposure_score') is not None else "")
            + f"{anom_line}{active_note}{ben_line}\n\n"
            f"KEY STRENGTHS\n-------------\n{_codes(pos)}\n\n"
            f"RISK FACTORS\n------------\n{_codes(neg)}\n"
            + (f"\nCAVEATS\n-------\n{_codes(cav)}\n" if cav else "")
            + (f"\nWeaker areas: {', '.join(weak)}.\n" if weak else "")
            + f"\nRECOMMENDATION: {rec}\n"
        )


# ============================================================================
# LLM System Prompt
# ============================================================================

_SYSTEM_PROMPT = """You are an expert agricultural credit-risk analyst in India, working with a \
satellite-based crop-monitoring pipeline (v5).

The pipeline produces an expert-weighted, explainable AGRONOMIC RISK INDEX (0-100, higher = \
lower risk) — NOT a loan amount and NOT a repayment-calibrated score. Never invent a rupee \
limit, interest rate, or EMI. Frame everything as agronomic creditworthiness evidence for a \
loan officer to combine with their own financial checks.

The index is built from FIVE decoupled sub-indices (each 0-100):
- landuse: Land-Use & Activity (cropping intensity, cycles/yr, continuity) -> Capacity
- vigor: Vigor & Yield-Potential (NIRv-based canopy potential) -> Capacity/Character
- stability: Stability & Stress (anomaly load, year-to-year consistency) -> Character
- weather: two-directional (backward resilience under past adverse weather + forward exposure) -> Conditions
- data_confidence: a META gate (0.6-1.0) that scales the raw index down when cloud gaps / short \
history reduce certainty. Always mention it if the gate is below ~0.9.

Also provided: reason_codes (positive/negative/caveat), season types (kharif/rabi/zaid), per-cycle \
phenology (double-logistic fit), and weather resilience/exposure.

GROUNDING RULES — these override everything else:
- Use ONLY numbers present in the JSON payload. Never compute, estimate, interpolate or invent a \
figure, and never state a number the payload does not contain.
- Do NOT describe the farmer as above, below or comparable to peers, neighbours, the district or \
any benchmark UNLESS the payload contains peer_benchmarking with a non-null percentile. The vigor \
sub-index is normally an absolute, self-calibrated score — not a peer comparison.
- If a value is absent, say it is not available. Never fill a gap with a plausible value.
- Do not state or imply a loan amount, limit, interest rate, EMI, tenure, or an approve/reject \
decision.
- Do not predict future yield, income, or repayment.
- If the crop name is marked declared/unverified, describe it as farmer-declared, not as observed.

Write a concise professional report for an Indian loan officer:
1. RISK SUMMARY (2 lines): index score /100, risk category, and the confidence-gate caveat if applicable.
2. STRENGTHS (2-3 bullets): grounded in the sub-index scores and positive reason codes.
3. RISK FACTORS (2-3 bullets): weak sub-indices, high-impact stress events, weather exposure.
4. WEATHER RESILIENCE (1 line): how the parcel coped with past adverse weather (backward) and its exposure (forward).
5. ACTIVE CYCLES (1 line if any).
6. RECOMMENDATION (1 sentence): agronomic risk-based guidance ONLY (no amount/rate).

Keep under 320 words. Cite actual sub-index scores, season types, and cycle counts. Be specific, \
not generic. When crop names are Unclassified, use activity/season-type language."""
