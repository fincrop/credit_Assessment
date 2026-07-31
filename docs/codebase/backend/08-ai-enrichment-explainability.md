# Backend Stage 08 — AI Enrichment & Explainability

## Purpose

Turn the score into readable, contestable explanations: deterministic driver attribution + counterfactuals, optional Groq narrative + Sarvam translation.

## Present condition (after 2026-07 enhancement)

1. Master switch: `PipelineConfig.AI_ENRICHMENT_ENABLE` / env `AI_ENRICHMENT_ENABLE` (default on).
2. Rule-based attribution runs by default (`model=None`); user-facing wording prefers **driver attribution** / **yield proxy** (not unqualified “SHAP” / “yield potential”).
3. Counterfactuals: up to 3 scenarios, no external API.
4. Groq / Sarvam: opt-in via keys + `GROQ_ENABLE`; when used, `ai_enrichment.model_snapshot` stores model id + prompt hash.
5. Enrichment failures never flip assessment status off SUCCESS.

## Done

| Item | Notes |
|------|--------|
| `AI_ENRICHMENT_ENABLE` | Ops kill switch |
| Label hygiene | Compliance-friendlier naming |
| LLM model/prompt snapshot | Audit trail when narrative enabled |

## Missing / next

| Priority | Item |
|----------|------|
| Major | Compliance export that **only** returns deterministic blocks (exclude LLM) |
| Major / ops | Formal review of third-party LLM data handling for farmer PII/location |
| Optional | True TreeExplainer path only after intentional ML wiring (and tests) |

## Interfaces

Upstream: complete Stage 07 assessment · Downstream: dashboard AI section, Mongo `ai_enrichment`

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
