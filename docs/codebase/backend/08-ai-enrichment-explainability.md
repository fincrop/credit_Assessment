# Backend Stage 08 — AI Enrichment & Explainability (Updated Deep-Dive)

## Purpose & Role

This is where the raw score becomes something a farmer, loan officer, or regulator can actually read and (in principle) contest. Given lending decisions are involved, this stage carries real compliance weight — "why did this farmer get this score" needs a defensible answer, and this stage is where that answer gets constructed, in three layers of decreasing determinism: rule-based SHAP-style attribution (deterministic), counterfactual scenarios (deterministic), and optional LLM narrative (non-deterministic).

## Present Condition — How It Actually Works Today

1. **SHAP:** enabled by default; enrichment always constructs `SHAPExplainer(model=None, feature_names=[])`, meaning it runs the **rule-based** attribution path on `component_scores`, not a true TreeExplainer over the crop-classifier RF. A live bug — undefined `ci` at `shap_explainer.py` L148 inside `_extract_features` — is currently dormant only because the Tree/ML SHAP path is never invoked; enabling it without a fix will crash.
2. **Counterfactuals:** up to 3 scenarios, no external API dependency, deterministic score-gain projections with roadmap text — enabled by default.
3. **Groq LLM narrative:** off unless both `GROQ_ENABLE` and `GROQ_API_KEY` are set; default model `llama-3.1-70b-versatile`, 400 max tokens.
4. **Sarvam translation:** runs only if an API key is present and an English narrative already exists; translates into Indic languages, default Hindi.
5. **Failure handling:** each block is wrapped in its own try/except; the assessment is still marked SUCCESS even if all AI enrichment fails — a deliberate "never fail the loan score over an explainability add-on" design.
6. **Stale documentation:** `PIPELINE_STAGES.md` historically claimed SHAP/counterfactuals were "not auto-run" and needed an `--explain` flag — that's incorrect for current code, which auto-runs both whenever `AI_CONFIG.shap.enabled` is true (the default).

## Ground Reality — What This Means Operationally

- **"SHAP-style" is doing a lot of work in that phrase.** True SHAP (Shapley Additive exPlanations) has specific game-theoretic guarantees (local accuracy, consistency) that a rule-based attribution approximation does not inherently carry just because it's named similarly — for a regulator or auditor familiar with the SHAP literature, calling a rule-based heuristic "SHAP" without qualification risks setting an expectation of formal attribution guarantees the current implementation doesn't actually provide. This matters more than a naming nitpick in a lending-compliance context, where explanation methodology can itself be subject to scrutiny (this is an active regulatory topic in credit-scoring generally, e.g., under evolving fair-lending and AI-explainability expectations globally).
- **The dormant `ci` NameError is a landmine, not a resolved issue** — it's currently invisible specifically because nobody has flipped on the Tree/ML SHAP path, which means the bug will surface at the exact moment someone tries to extend explainability sophistication (arguably the natural next step for this stage), turning a planned enhancement into an unplanned incident.
- **LLM narratives being off by default is the practical reality for "many deploys" per the original doc, and worth treating that soberly**: whatever explainability story a stakeholder imagines ("the farmer gets a plain-language explanation") may not actually be happening in a given deployment unless Groq credentials are specifically configured — the SHAP+counterfactual layer (deterministic, no external dependency) is the one universally-available explanation path, and product expectations should be set against that baseline, not the LLM layer.
- **Non-determinism in LLM narratives is explicitly and correctly flagged as unsuitable for sole compliance evidence** — this is the right call, but it implies a governance requirement: if a farmer or auditor disputes a decision, the deterministic SHAP/counterfactual layer needs to be the actual evidentiary record, with the LLM narrative treated purely as a readability layer on top, never as the record of "why." That separation should probably be enforced structurally (e.g., a compliance export that only pulls the deterministic blocks) rather than left as an implicit convention.
- **Groq's `llama-3.1-70b-versatile` and similar third-party hosted models introduce a live external dependency and (depending on contractual terms) potential data-residency/data-handling considerations** for what is, in this context, farmer financial and location data being sent to a third-party inference API — worth an explicit compliance review if not already done, independent of whether the feature is enabled by default.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Fix the `ci` NameError now, while it's cheap, rather than waiting for it to surface as a production incident** — `float(ca.get('cropping_intensity', 0))` or equivalent, as already correctly identified; this is the easiest, most time-sensitive item across the entire stage docs precisely because it's currently invisible.
2. **Rename the rule-based explainer to something that doesn't borrow SHAP's specific technical connotation** (e.g., "rule-based driver attribution" or "component-contribution breakdown") unless/until true Shapley-value attribution (via a proper TreeExplainer against an actual trained model) is implemented — a naming fix now avoids a harder conversation with an auditor later.
3. **Separate a "compliance explanation" export path from the "narrative" path structurally**, as already flagged as a major recommendation — concretely, this could mean a dedicated Mongo field/API endpoint that only ever returns the deterministic SHAP+counterfactual blocks, explicitly excluding any LLM-generated text, so downstream consumers (a compliance dashboard, a regulator export) cannot accidentally pull non-deterministic content as evidentiary.
4. **Snapshot prompt templates and model IDs on the assessment when Groq/Sarvam are used** (already flagged as medium) — for any future dispute or audit involving a farmer who received an LLM-generated narrative, being able to reconstruct exactly what prompt and model version produced it is a standard responsible-AI practice, not just a nice-to-have.
5. **Correct the stale `AI_CONFIG`/`PIPELINE_STAGES.md` comments claiming an `--explain` flag gate** — small, but exactly the kind of drift that causes someone to assume explainability is opt-in when it's actually always-on, potentially leading to under-preparedness for the compliance load it actually carries.
6. **Add an `AI_ENRICHMENT_ENABLE` master switch** (already flagged as quick win) — useful not just for feature control but as an incident-response lever (e.g., if a Groq dependency issue starts causing widespread warning-log noise or latency, being able to disable the whole stage cleanly rather than relying on per-block try/except is operationally cleaner).

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Urgent/Quick win | Fix the `ci` NameError in `shap_explainer.py` L148 now | Currently dormant only by accident (model=None path); will crash the moment Tree/ML SHAP is enabled |
| Quick win | Correct stale AI_CONFIG/PIPELINE_STAGES.md comments about an `--explain` flag gate | Prevents under-preparedness for the always-on compliance/explainability load this stage actually carries |
| Quick win | Add `AI_ENRICHMENT_ENABLE` master switch | Gives a clean incident-response lever independent of per-block try/except |
| Medium | Rename "SHAP-style" rule attribution to avoid borrowing formal Shapley-value connotations | Avoids setting a false expectation of game-theoretic attribution guarantees to auditors/regulators |
| Medium | Snapshot prompts + model IDs on the assessment for audit | Standard responsible-AI practice; needed if any LLM-narrated decision is ever disputed |
| Major | Structurally separate deterministic "compliance explanation" export from LLM "marketing narrative" | Ensures non-deterministic content can never be mistaken for the evidentiary record in a dispute/audit |
| Major | Formal compliance review of third-party LLM data handling (Groq) given farmer financial/location data involved | Independent of default-off status, this is a live external-dependency and data-governance question |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Complete assessment through Stage 07 (+ payload) |
| Downstream | Dashboard `AIEnrichmentSection`; Mongo `ai_enrichment` preview |
| Shared | `component_scores` schema from Advanced scorer |

---
*This document supersedes the original `08-ai-enrichment-explainability.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
