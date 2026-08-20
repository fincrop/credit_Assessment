"""
Stage 12 — AI / explainability enrichment after the assessment payload is complete.

- Groq: used when GROQ_API_KEY is set (GROQ_ENABLE=0 still disables it).
- Sarvam: translates the English narrative when SARVAM_API_KEY is set.
- Driver attribution: rule-based credit attribution + optional Tree SHAP on crop classifier.
- Counterfactuals: actionable score-improvement scenarios (no external API).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _prompt_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


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
                    "action": s.get("action"),
                }
            )

    roadmap = full.get("improvement_roadmap")
    slim_roadmap: Any
    if isinstance(roadmap, list):
        slim_roadmap = []
        for step in roadmap[:8]:
            if isinstance(step, dict):
                slim_roadmap.append(
                    {
                        "step": step.get("step"),
                        "action": step.get("action"),
                        "timeframe": step.get("timeframe"),
                        "score_gain": step.get("score_gain"),
                        "feasibility": step.get("feasibility"),
                    }
                )
    elif isinstance(roadmap, str):
        slim_roadmap = roadmap[:1500]
    else:
        slim_roadmap = []

    return {
        "current_score": full.get("current_score"),
        "current_risk_category": full.get("current_risk_category"),
        "projected_score_all_improvements": full.get(
            "projected_score_all_improvements"
        ),
        "scenarios": slim,
        "improvement_roadmap": slim_roadmap,
    }


def enrich_assessment_with_ai(assessment: Dict[str, Any]) -> bool:
    """
    Populate ``assessment['ai_enrichment']`` from config flags.

    Credit attribution uses rule-based scoring on ``component_scores`` (crop RF uses
    different features than the credit feature vector, so Tree SHAP is not mixed here).

    Returns:
        True if any substantive block was written (attribution / counterfactuals / LLM).
        False immediately when ``PipelineConfig.AI_ENRICHMENT_ENABLE`` is off.
    """
    from config import PipelineConfig

    if not getattr(PipelineConfig, "AI_ENRICHMENT_ENABLE", True):
        logger.info("AI enrichment skipped (AI_ENRICHMENT_ENABLE=0)")
        return False

    cfg = PipelineConfig.AI_CONFIG
    block: Dict[str, Any] = {}
    model_snapshot: Dict[str, Any] = {}
    did = False

    gcfg = cfg.get("groq") or {}
    if gcfg.get("enabled") and gcfg.get("api_key"):
        try:
            from ai_integration.groq_report_generator import GroqReportGenerator

            gen = GroqReportGenerator()
            prompt = gen._build_prompt(assessment)
            narrative = gen.generate(assessment)

            if getattr(gen, "dry_run", False):
                # In dry-run mode generate() returns the prompt itself. Storing
                # that as english_narrative with groq_used=True made a dry run
                # indistinguishable from a real report everywhere downstream.
                # Fall through to the deterministic narrative instead.
                block["groq_used"] = False
                block["groq_skipped_reason"] = "dry_run"
                block["groq_dry_run_prompt"] = narrative
                logger.info("Groq DRY RUN — prompt captured, narrative not used")
            else:
                block["english_narrative"] = narrative
                block["narrative_source"] = "groq"
                block["groq_used"] = True
                model_snapshot["groq"] = {
                    "model": getattr(gen, "model", None) or gcfg.get("model"),
                    "prompt_hash": _prompt_hash(prompt),
                }
                did = True
        except Exception as e:
            logger.warning("Groq narrative skipped: %s", e)
            block["groq_used"] = False
            block["groq_skipped_reason"] = f"error:{str(e)[:120]}"
    else:
        block["groq_used"] = False
        block["groq_skipped_reason"] = "disabled_or_no_key"

    # Always emit a narrative — deterministic report when Groq did not produce one.
    if not block.get("english_narrative"):
        try:
            from ai_integration.groq_report_generator import GroqReportGenerator

            block["english_narrative"] = GroqReportGenerator._rule_based_report(assessment)
            block["narrative_source"] = "deterministic"
            did = True
        except Exception as e:
            logger.warning("Deterministic narrative skipped: %s", e)
            block["english_narrative"] = (
                "Agronomic risk index assessment completed. "
                "Detailed narrative unavailable."
            )
            block["narrative_source"] = "minimal"
            did = True

    scfg = cfg.get("sarvam") or {}
    if scfg.get("enabled") and block.get("english_narrative"):
        try:
            from ai_integration.sarvam_translator import SarvamTranslator

            lang = scfg.get("default_language", "hi")
            src = block["english_narrative"]
            block["translated_narrative"] = SarvamTranslator(
                target_language=lang
            ).translate(src)
            block["translation_language"] = lang
            model_snapshot["sarvam"] = {
                "model": "mayura:v1",
                "prompt_hash": _prompt_hash(src[:2000]),
                "target_language": lang,
            }
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
            logger.warning("Driver attribution enrichment skipped: %s", e)
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

    if model_snapshot:
        block["model_snapshot"] = model_snapshot

    if block:
        assessment["ai_enrichment"] = block
    return bool(did)
