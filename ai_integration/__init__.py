"""
AI Integration Package
======================
Provides LLM-based report generation, multilingual translation,
SHAP explainability, and counterfactual analysis for the agri-credit pipeline.

Modules:
    groq_report_generator  - English credit narrative via Groq API
    sarvam_translator      - Hindi (and other Indian language) translation via SarvamAI
    shap_explainer         - Feature-level SHAP explanations
    counterfactual_engine  - "What-if" score projections
"""

from ai_integration.groq_report_generator import GroqReportGenerator
from ai_integration.sarvam_translator import SarvamTranslator
from ai_integration.shap_explainer import SHAPExplainer
from ai_integration.counterfactual_engine import CounterfactualEngine

__all__ = [
    'GroqReportGenerator',
    'SarvamTranslator',
    'SHAPExplainer',
    'CounterfactualEngine',
]
