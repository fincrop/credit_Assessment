# Frontend Stage 05 — Dashboard Components (Updated Deep-Dive)

## Purpose & Role

This is where all nine backend stages' work finally becomes legible to a human — a loan officer deciding whether to extend credit, or (potentially, eventually) a farmer trying to understand their own score. The component split (Summary/Location/Cropping/Cycles/Performance/Weather/AI-Enrichment) mirrors the backend stage boundaries closely, which is a real strength for maintainability, but it also means every backend-side caveat flagged in the previous nine documents (Path B "Unclassified" farms, yield-as-proxy-not-forecast, dormant Groq narratives, credit-limit-scale uncertainty) surfaces here as a UI/UX problem if it isn't handled deliberately.

## Present Condition — How It Actually Works Today

1. **Flow:** farmer_id (+ optional benefit toggles) submitted → `runAssessmentJob` returns `job_id` → poll every 3s → map `result` into section props → tab-switch visible panels.
2. **Components:** `SummaryHero` (score dial, risk, limit, tenure, rate, component bars), `LocationStrip`, `CroppingSection`, `CropCyclesSection`, `PerformanceSection`, `WeatherSection`, `AIEnrichmentSection`.
3. **Data contract:** expects the slim shape produced by backend `slim_assessment_for_api` — no heavy scene arrays, just summaries/credit blocks/`ai_enrichment`/`crop_cycles`.
4. **Failure UX:** failed jobs show the raw `error` string from the job document; no described richer failure taxonomy.
5. **No WebSocket** — HTTP poll only, no live `pipeline_stages` feed unless explicitly present on the job document.

## Ground Reality — What This Means Operationally

- **`PerformanceSection` rendering a "yield potential" number without qualification directly inherits backend Stage 06's flagged mislabeling risk** — this is the exact point where an NDVI-AUC proxy, if displayed as a plain percentage or score without framing, risks being read by a loan officer as a forecasted physical yield rather than a vigor proxy. Since the backend doc already recommends renaming the underlying concept, this component is where that fix actually becomes user-visible or doesn't.
- **`CroppingSection`/`CropCyclesSection` need explicit, deliberate handling of the "Unclassified" Path B state, not just graceful non-crash behavior** — since classification is off by default at the backend (Stage 04), the *majority* of farms a loan officer reviews will show `dominant_crop: null` / "Unclassified" labels. If the UI treats this as a degraded edge case (small greyed-out placeholder text) rather than a first-class, well-explained state ("crop not identified — scoring based on cultivation pattern and vigor"), loan officers may reasonably lose confidence in the tool for the majority of farms they actually see it used on, even though Path B's scoring is a deliberate, documented design choice rather than a failure state.
- **`AIEnrichmentSection` needs to visually distinguish the deterministic SHAP/counterfactual content from the optional, non-deterministic Groq narrative** — echoing backend Stage 08's governance recommendation, if a loan officer or auditor can't tell at a glance which part of the explanation is the reproducible evidentiary record versus AI-generated prose, a dispute or audit request ("show me why this farmer got this score") risks being answered with the wrong artifact. This is squarely a UI labeling/layout decision, not a backend one — the backend already keeps the data separable; the UI is where that separation either gets preserved for the reader or collapsed into one undifferentiated "explanation" block.
- **A raw `error` string as the entire failure UX** is a real usability problem for a non-engineer audience — a loan officer seeing a stack-trace-adjacent string with no plain-language "what happened / what to do next" guidance is likely to either misinterpret it or simply give up and re-submit, which (absent frontend Stage 03's recommended rate limiting) could compound load on the backend during exactly the failure conditions it's least equipped to handle.
- **3-second polling with no stage-level feedback** is the same issue flagged in frontend Stage 01, but it's worth restating here specifically because this is the component layer that actually has the data to fix it (the job document can carry `pipeline_stages`) — this is a "the data exists, the UI just isn't using it yet" gap, one of the cheaper fixes available across the whole frontend review.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Give "Unclassified" a first-class, confidently-worded UI treatment** rather than a degraded placeholder — e.g., `CroppingSection` explicitly stating "Crop classification not run for this assessment — scoring uses cultivation-pattern and vigor signals instead," framed as a deliberate methodology rather than a gap, directly building loan-officer trust in Path B scoring rather than eroding it through unexplained blank fields.
2. **Rename and re-frame `PerformanceSection`'s yield display** in lockstep with the backend Stage 06 recommendation — "Vigor / Yield Proxy" with a brief tooltip explaining it's a vegetation-index-based indicator, not a forecasted harvest quantity, directly closing the misreading risk at the one place a human actually sees the number.
3. **Visually separate `AIEnrichmentSection` into two distinct zones** — a "Score Explanation" block (SHAP drivers + counterfactuals, labeled as the deterministic record) and a clearly bordered/badged "AI Narrative (Groq)" block, so the distinction backend Stage 08 recommends structurally is also obvious visually, not just present in the underlying data.
4. **Replace the raw `error` string with a small failure-taxonomy mapping** — even a lightweight client-side lookup translating common backend failure categories (satellite validation failure, weather API timeout, Mongo unavailability) into one-line plain-language messages with a suggested next action ("Try again in a few minutes" vs. "Contact support") would meaningfully improve the non-engineer experience without needing a backend contract change.
5. **Surface `pipeline_stages` as a lightweight progress list during RUNNING status** (already flagged as a quick win in the original doc and echoed from frontend Stage 01/backend Stage 09) — since the underlying data already exists, this is likely the single cheapest, highest-perceived-value fix available in the entire dashboard component set.
6. **Consider a print/export-friendly summary view** aligned with the already-flagged "Export PDF/report from the same slim payload" major recommendation — for a lending workflow, being able to hand a loan committee or the farmer themselves a static, shareable version of the assessment (rather than requiring them to view a live dashboard) is a common real-world requirement in credit workflows generally.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters |
|---|---|---|
| Quick win | Surface `pipeline_stages` progress during RUNNING status | Cheapest available fix; data already exists, addresses the "did this hang?" concern from frontend Stage 01 |
| Quick win | Replace raw `error` string with a plain-language failure-taxonomy mapping | Meaningfully improves non-engineer failure UX without a backend contract change |
| Medium | First-class, confidently-worded "Unclassified" state in `CroppingSection`/`CropCyclesSection` | Prevents loan-officer trust erosion on the majority-default Path B scoring outcome |
| Medium | Rename/reframe yield display per backend Stage 06's recommendation | Closes the yield-as-forecast misreading risk at the actual point of human reading |
| Medium | Visually separate deterministic SHAP/counterfactual content from Groq narrative in `AIEnrichmentSection` | Preserves backend Stage 08's evidentiary/narrative distinction all the way to the reader |
| Major | Export PDF/report from the same slim payload | Standard real-world requirement for credit workflows — shareable static record beyond the live dashboard |

## Interfaces to Other Stages (unchanged, restated for continuity)

Data from backend stages 03–08 via jobs; enqueue/status from frontend 03/04.

---
*This document supersedes the original `05-dashboard-components.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
