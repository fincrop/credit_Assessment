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

_DEFAULT_MODEL   = "llama-3.3-70b-versatile"   # latest Groq free model
_GROQ_API_URL    = "https://api.groq.com/openai/v1/chat/completions"
_REQUEST_TIMEOUT = 30


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

            payload = json.dumps({
                "model":    self.model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "max_tokens":  600,
                "temperature": 0.3,
            }).encode("utf-8")

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
                logger.info("✔ Groq English report generated successfully")
                return report

        except Exception as exc:
            logger.error(f"Groq API call failed: {exc} — rule-based fallback")
            return self._rule_based_report(assessment)

    # =========================================================================
    # PROMPT BUILDER
    # =========================================================================

    @staticmethod
    def _build_prompt(assessment: Dict) -> str:
        """Build enriched JSON summary for the LLM prompt."""
        ca  = assessment.get("cropping_analysis",  {})
        pa  = assessment.get("performance_analysis", {})
        wa  = assessment.get("weather_analysis",   {})
        cr  = assessment.get("credit_assessment",  {})
        crr = assessment.get("credit_recommendations", {})

        # Cycle-level performance summary (Stage 5)
        sp = pa.get("seasonal_performance", [])
        cycle_summaries = []
        for p in sp:
            cycle_summaries.append({
                "cycle":          f"{p.get('season','?').upper()} {p.get('year','?')}",
                "crop":           p.get("crop", "Unknown"),
                "is_active":      p.get("is_active_cycle", False),
                "health_score":   p.get("health_score", 0),
                "yield_pct":      p.get("yield_potential_pct", "?"),
                "n_anomalies":    len(p.get("anomaly_events", [])),
                "n_high_impact":  sum(1 for e in p.get("anomaly_events", [])
                                      if e.get("impact") == "HIGH"),
                "narrative":      p.get("performance_narrative", "")[:200],
                "scoring_method": p.get("scoring_method", ""),
            })

        # Weather cycle risk (Stage 4)
        cycle_risks = wa.get("cycle_risk_scores", [])
        cycle_risk_summary = [
            {
                "cycle": cr_item.get("cycle_label", ""),
                "risk":  cr_item.get("risk_category", ""),
                "score": cr_item.get("risk_score", 0),
            }
            for cr_item in cycle_risks[:5]
        ]

        # Anomaly events (top HIGH-impact only)
        top_events: List = []
        for p in sp:
            for ev in p.get("anomaly_events", []):
                if ev.get("impact") == "HIGH":
                    top_events.append({
                        "cycle":       f"{p.get('season','?').upper()} {p.get('year','?')}",
                        "type":        ev.get("type", ""),
                        "stage":       ev.get("stage", ""),
                        "description": ev.get("description", "")[:100],
                    })
        top_events = top_events[:5]

        summary = {
            "farmer_id":              assessment.get("farmer_id"),
            "analysis_date":          assessment.get("analysis_date", ""),
            "credit_score":           cr.get("credit_score"),
            "risk_category":          cr.get("risk_category"),
            "credit_limit_INR":       crr.get("recommended_limit"),
            "interest_rate_pct":      crr.get("recommendations", {}).get("interest_rate"),
            "scoring_method":         cr.get("method"),
            "scoring_narrative":      cr.get("scoring_narrative", ""),
            # Component breakdown
            "component_scores":       cr.get("component_scores", {}),
            "weak_components":        cr.get("weak_components", []),
            # Stage 2
            "cropping_intensity_per_year": ca.get("cropping_intensity"),
            "cultivation_signal":     ca.get("cultivation_signal"),
            "n_complete_cycles":      pa.get("n_complete_cycles", 0),
            "n_active_cycles":        pa.get("n_active_cycles", 0),
            # Stage 5 per-cycle
            "cycle_performance":      cycle_summaries,
            # Stage 4 weather
            "weather_risk_score":     wa.get("weather_risk_score"),
            "total_extreme_events":   wa.get("total_extreme_events"),
            "cycle_weather_risks":    cycle_risk_summary,
            "crop_impact_narrative":  wa.get("crop_impact_narrative", ""),
            # High-impact anomaly events
            "high_impact_events":     top_events,
        }
        return (
            "Generate a credit assessment report for the following farmer data:\n\n"
            + json.dumps(summary, indent=2, default=str)
        )

    # =========================================================================
    # RULE-BASED FALLBACK REPORT
    # =========================================================================

    @staticmethod
    def _rule_based_report(assessment: Dict) -> str:
        """Deterministic fallback report — no LLM needed."""
        cr   = assessment.get("credit_assessment", {})
        ca   = assessment.get("cropping_analysis",  {})
        pa   = assessment.get("performance_analysis", {})
        wa   = assessment.get("weather_analysis",   {})
        crr  = assessment.get("credit_recommendations", {})

        score    = cr.get("credit_score",   0)
        risk     = cr.get("risk_category", "UNKNOWN")
        limit    = crr.get("recommended_limit", 0)
        rate     = crr.get("recommendations", {}).get("interest_rate", "N/A")
        ci       = ca.get("cropping_intensity",  0)
        signal   = ca.get("cultivation_signal",  0)
        perf     = pa.get("average_performance_score", 0)
        yield_s  = pa.get("average_yield_score",  0)
        n_active = pa.get("n_active_cycles",  0)
        n_comp   = pa.get("n_complete_cycles", 0)
        w_risk   = wa.get("weather_risk_score", 50)
        weak     = cr.get("weak_components",    [])
        narr     = cr.get("scoring_narrative",  "")

        sp = pa.get("seasonal_performance", [])
        n_high = sum(
            len([e for e in p.get("anomaly_events", []) if e.get("impact") == "HIGH"])
            for p in sp
        )

        # Approval recommendation
        if   risk == "LOW":       rec = "Approve with standard terms."
        elif risk == "MEDIUM":    rec = "Approve with mandatory crop insurance."
        elif risk == "HIGH":      rec = "Conditional approval — collateral required."
        else:                     rec = "High risk — re-assess after 2 full growing seasons."

        # Anomaly callout
        anom_line = ""
        if n_high > 0:
            anom_line = (
                f"\nStress Alert: {n_high} high-impact vegetation stress event(s) "
                "detected by satellite. Crop insurance strongly advised."
            )

        # Active cycle note
        active_note = ""
        if n_active > 0:
            active_note = (
                f"\nNote: {n_active} crop cycle(s) currently active — "
                "harvest income expected. Final repayment capacity may be higher."
            )

        return (
            f"CREDIT ASSESSMENT REPORT\n"
            f"{'='*40}\n"
            f"Farmer ID:        {assessment.get('farmer_id','N/A')}\n"
            f"Analysis Date:    {assessment.get('analysis_date','')}\n\n"
            f"Credit Score:     {score}/100  |  Risk: {risk}\n"
            f"Credit Limit:     ₹{limit:,.0f}  |  Interest Rate: {rate}%\n\n"
            f"FIELD PERFORMANCE\n"
            f"-----------------\n"
            f"Cropping Intensity:  {ci:.1f} cycles/year\n"
            f"Cultivation Signal:  {signal:.0f}/100\n"
            f"Complete Cycles:     {n_comp}  |  Active: {n_active}\n"
            f"Avg Health Score:    {perf:.1f}/100\n"
            f"Avg Yield Potential: {yield_s:.1f}/100\n"
            f"Weather Risk:        {w_risk:.1f}/100\n"
            f"{anom_line}"
            f"{active_note}\n\n"
            f"SCORE SUMMARY\n"
            f"-------------\n"
            + (f"Key weaknesses: {', '.join(weak)}.\n" if weak else "All components adequate.\n")
            + (f"\n{narr}\n" if narr else "")
            + f"\nRECOMMENDATION: {rec}\n"
        )


# ============================================================================
# LLM System Prompt
# ============================================================================

_SYSTEM_PROMPT = """You are an expert agricultural credit analyst in India, working with a \
satellite-based crop monitoring pipeline (v4.0).

The pipeline uses dynamic crop cycle detection (not fixed calendar seasons) to assess:
1. Cultivation signal strength (how consistently and intensely the farmer cultivates)
2. Crop health trajectories across NDVI/EVI/NDMI indices
3. Anomaly events at specific crop growth stages (vegetative, flowering, grain-fill)
4. Cycle-aligned weather risk (not generic seasonal averages)

Your job: Write a concise professional credit assessment report for a loan officer in India.

Structure your report as:
1. CREDIT SUMMARY (2 lines): score, risk category, recommended limit
2. STRENGTHS (2-3 bullet points): what the farmer is doing well
3. RISK FACTORS (2-3 bullet points): specific weaknesses, anomaly events, weather risks
4. ACTIVE CYCLES (1 line if applicable): note any in-progress crops and expected harvest
5. RECOMMENDATION (1 specific sentence): approval decision and key condition

Keep it under 280 words. Be specific — mention actual scores and cycle data.
Avoid generic statements. Focus on actionable insights for the loan officer."""
