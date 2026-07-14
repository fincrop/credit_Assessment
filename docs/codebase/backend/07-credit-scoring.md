# Backend Stage 07 — Credit Scoring & Limits (Updated Deep-Dive)

## Purpose & Role

This is the stage every upstream imperfection converges into a single number a lender acts on. It's also, notably, the stage with the clearest and most consequential **documentation-vs-code drift** in the whole system: the live scorer's weights and credit-limit table are materially different from what older docs (`codebase.md`, `PipelineConfig`) describe — a genuinely risky gap for a lending system, since it means anyone (auditor, new engineer, product owner) reading the "obvious" reference source gets the wrong mental model of how a farmer's limit is actually computed.

## Present Condition — How It Actually Works Today

1. **Mode gate:** `CREDIT_SCORE_ML_BLEND_ENABLED = False` forces any requested unsupervised/supervised/hybrid mode down to pure `rule_based`, with `ml_components_silenced=True` — the live system is, today, a rule-based scorer regardless of what mode is requested.
2. **Live weights** (`AdvancedCreditScorer._WEIGHTS`): crop_detection 35, crop_performance 25, yield_potential 15, weather_safety 8, anomaly_penalty 7, cropping_intensity 5, govt_benefits 5. The legacy `PipelineConfig.CREDIT_WEIGHTS` (35/30/15/12/5/3) is a **different table entirely** and belongs to the unused legacy scorer — using it as a reference for the live path is simply wrong.
3. **Credit limit:** base ₹/ha by score band (₹15,000 at ≥80 down to ₹3,000 below 40), multiplied by intensity (0.80–1.20), area bracket (0.90–1.15), high-value-crop flag (1.15 or 1.00), and benefits (+0.05 each for PM-KISAN/insurance), floored at ₹1,000, ceiled to the nearest ₹1,000. The legacy `CREDIT_LIMITS_PER_HA` table (₹15k–₹80k) is **unused** — a full order-of-magnitude different scale from what's actually live, which is a significant enough discrepancy that "product intent must be confirmed" (as the original doc already flags) should be treated as urgent, not exploratory.
4. **Weather safety** is 100 minus blended weather risk, deliberately avoiding double-counting extremes already reflected elsewhere.
5. **Hybrid mode** (dormant): documented as rule × 0.65 + unsupervised × 0.35, but inactive while the ML blend kill switch is off.

## Ground Reality — What This Means Operationally

- **`crop_detection` at 35 points is the single largest weight in the system, and given Stage 04 is off by default, this component is really measuring "did we detect any plausible cycles at all," not "what crop and how confidently."** That's a reasonable proxy for basic land-use verification, but it means over a third of a farmer's credit score rests on cycle-detection quality (Stage 03) — reinforcing why Stage 03's ecoregion-threshold and duration-gating issues (perennial crops, regional CVI floors) aren't abstract code-quality concerns; they translate close to linearly into credit-score error for real farmers.
- **The 3–15k/ha limit scale vs. the unused 15–80k/ha legacy table is not a cosmetic discrepancy — it's potentially a 5x difference in loan sizing.** If the legacy table reflects an earlier, deliberately-superseded product decision, that's fine, but if it instead reflects an actual target loan size that was never correctly ported into the live Advanced scorer, farmers today may be receiving materially smaller limits than the product was designed to offer. This is the single highest-priority open question flagged across all nine stage docs, precisely because it's a product/business question masquerading as a code discrepancy, and it can't be resolved by reading code alone.
- **Two live scorers existing side-by-side** (`advanced_credit_scorer.py` wired in, `credit_scorer.py` with ~557 commented lines not wired) is a classic "accidental edit to the dead path" risk — a well-intentioned bug fix or weight tweak applied to the wrong file would silently do nothing in production while looking correct in a code review, unless the reviewer specifically knows only `advanced_credit_scorer.py` is live.
- **Benefits (PM-KISAN, crop insurance) being treated as neutral/unknown when `None`** is a reasonable default-safe choice, but worth flagging: it means farmers whose benefits status simply wasn't captured (a data-completeness problem, not a benefits-eligibility problem) are scored identically to farmers confirmed not enrolled — conflating "unknown" and "no" in a scoring signal such that a rural digital-divide gap (some farmers' benefit status is easier to look up than others') could quietly shape their limit multiplier.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Resolve the ₹/ha scale question first, before any other Stage 07 work** — this needs a business/lending-policy decision (which scale reflects actual intended loan sizing), not an engineering fix; until resolved, any other tuning of the live scorer is operating on top of an unconfirmed foundation.
2. **Delete or clearly archive the legacy `credit_scorer.py` and its config table**, or if there's a reason to keep the two-scorer possibility alive (e.g., future A/B testing), rename/relocate the legacy path so it can never be mistaken for the live one — a README pointer alone (already flagged as a quick win) is good but a physical move to an `archive/` or `legacy/` directory is a stronger guard against accidental edits.
3. **Introduce golden-file / fixture-based regression tests on score and limit output** (already flagged as medium) — given this stage is where a single-digit weight typo becomes a real lending-limit change, deterministic fixture tests (known input assessment → known expected score/limit) are the standard practice for financial scoring systems and would catch silent regressions before deployment rather than after farmers are affected.
4. **Separate "model score" from "policy limit" explicitly** (already flagged as major) — this is a standard credit-risk-modeling pattern (probability-of-default / risk score kept distinct from the business-policy layer that converts risk into an actual offered limit) that would let the lending policy (₹/ha bands, multipliers) be tuned or A/B tested by a business owner without touching the underlying agronomic risk model, and vice versa.
5. **Distinguish "unknown benefits" from "confirmed not enrolled" as separate states** rather than collapsing both to neutral, so score fairness doesn't inadvertently correlate with data-collection completeness.
6. **Given Stage 04/06 recommendations (registry crop as soft prior, crop-family vigor banding), revisit whether `crop_detection`'s 35-point weight should be split** — e.g., a smaller "land-use verified" component plus a separate, smaller "crop confidence" component — so that turning on better crop-family signal (from Stage 04/06 improvements) has a clean place to plug into credit scoring rather than requiring a full re-weighting exercise later.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Urgent/Quick win | Get explicit business confirmation on ₹/ha scale (3–15k live vs. 15–80k legacy) | Potential 5x loan-sizing discrepancy; the single highest-priority open item across all nine stages |
| Quick win | Physically archive/move (not just document) the unused legacy `credit_scorer.py` and its config table | Removes the "accidental edit to dead path" risk entirely, stronger than a README pointer alone |
| Quick win | Align or clearly deprecate `PipelineConfig.CREDIT_*` vs. Advanced scorer values | Prevents documentation from actively misleading auditors/new engineers |
| Medium | Golden-file/fixture regression tests for score and credit limit | Standard practice for financial scoring; catches silent regressions before farmers are affected |
| Medium | Distinguish "unknown" vs. "confirmed not enrolled" benefits status | Prevents data-completeness gaps from masquerading as benefits-eligibility signal |
| Major | Separate deterministic "model score" from tunable "policy limit" layer | Lets business/lending-policy tuning happen independently of the agronomic risk model |
| Major | Recalibrate ₹/ha bands with confirmed lender policy once the scale question is resolved | Ensures the actual live product matches intended lending outcomes |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stages 03–06 outputs |
| Downstream | Stage 08 enrichment; Mongo `credit_assessments`; dashboard hero |
| Shared | Benefits flags from jobs/ingest; field area from Stage 01 |

---
*This document supersedes the original `07-credit-scoring.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
