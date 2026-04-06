"""
Stage 12 — AI / explainability enrichment after the assessment payload is complete.

- Groq: only if explicitly enabled (GROQ_ENABLE) and API key present.
- Sarvam: translates English narrative when key present and Groq produced text.
- SHAP: rule-based credit attribution + optional Tree SHAP on crop classifier.
- Counterfactuals: actionable score-improvement scenarios (no external API).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _summarize_shap_for_mongo(full: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(full, dict):
        return {}
    return {
        "method": full.get("method"),
        "shap_available": full.get("shap_available"),
        "base_value": full.get("base_value"),
        "top_positive_drivers": (full.get("top_positive_drivers") or [])[:5],
        "top_negative_drivers": (full.get("top_negative_drivers") or [])[:5],
    }


def _summarize_counterfactuals_for_mongo(full: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(full, dict):
        return {}
    scenarios = full.get("scenarios") or []
    slim = []
    for s in scenarios[:5]:
        if isinstance(s, dict):
            slim.append(
                {
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "score_gain": s.get("score_gain"),
                    "component": s.get("component"),
                    "feasibility": s.get("feasibility"),
                }
            )
    return {
        "current_score": full.get("current_score"),
        "current_risk_category": full.get("current_risk_category"),
        "projected_score_all_improvements": full.get(
            "projected_score_all_improvements"
        ),
        "scenarios": slim,
        "improvement_roadmap": (full.get("improvement_roadmap") or "")[:1500],
    }


def enrich_assessment_with_ai(assessment: Dict[str, Any]) -> bool:
    """
    Populate ``assessment['ai_enrichment']`` from config flags.

    Credit SHAP uses rule-based attribution on ``component_scores`` (crop RF uses
    different features than the credit feature vector, so Tree SHAP is not mixed here).

    Returns:
        True if any substantive block was written (SHAP / counterfactuals / LLM).
    """
    from config import PipelineConfig

    cfg = PipelineConfig.AI_CONFIG
    block: Dict[str, Any] = {}
    did = False

    gcfg = cfg.get("groq") or {}
    if gcfg.get("enabled") and gcfg.get("api_key"):
        try:
            from ai_integration.groq_report_generator import GroqReportGenerator

            narrative = GroqReportGenerator().generate(assessment)
            block["english_narrative"] = narrative
            block["groq_used"] = True
            did = True
        except Exception as e:
            logger.warning("Groq narrative skipped: %s", e)
    else:
        block["groq_used"] = False
        block["groq_skipped_reason"] = "disabled_or_no_key"

    scfg = cfg.get("sarvam") or {}
    if scfg.get("enabled") and block.get("english_narrative"):
        try:
            from ai_integration.sarvam_translator import SarvamTranslator

            lang = scfg.get("default_language", "hi")
            block["translated_narrative"] = SarvamTranslator(
                target_language=lang
            ).translate(block["english_narrative"])
            block["translation_language"] = lang
            did = True
        except Exception as e:
            logger.warning("Sarvam translation skipped: %s", e)

    shcfg = cfg.get("shap") or {}
    if shcfg.get("enabled", True):
        try:
            from ai_integration.shap_explainer import SHAPExplainer

            expl = SHAPExplainer(model=None, feature_names=[])
            shap_full = expl.explain_assessment(assessment)
            block["explainability"] = shap_full
            block["explainability_mongo"] = _summarize_shap_for_mongo(shap_full)
            did = True
        except Exception as e:
            logger.warning("SHAP enrichment skipped: %s", e)
            block["explainability_error"] = str(e)[:200]

    cf_cfg = cfg.get("counterfactual") or {}
    if cf_cfg.get("enabled", True):
        try:
            from ai_integration.counterfactual_engine import CounterfactualEngine

            cf_full = CounterfactualEngine().generate(assessment)
            block["counterfactuals"] = cf_full
            block["counterfactuals_mongo"] = _summarize_counterfactuals_for_mongo(
                cf_full
            )
            did = True
        except Exception as e:
            logger.warning("Counterfactual enrichment skipped: %s", e)
            block["counterfactuals_error"] = str(e)[:200]

    if block:
        assessment["ai_enrichment"] = block
    return bool(did)
