# Backend Stage 06 — Yield & Performance Evaluation (Updated Deep-Dive)

## Purpose & Role

Feeds the two heaviest non-detection credit weights (`crop_performance` 25, `yield_potential` 15 — 40 of 100 points combined) plus the `anomaly_penalty` (7 points). Because classification is off by default, the **enhanced (crop-agnostic) path is the real production primary**, not the crop-specific curve-fitting path the original documentation historically emphasized — this is an important expectation-reset for anyone reviewing scoring logic assuming curve-fit yield modeling is the norm.

## Present Condition — How It Actually Works Today

1. **Path selection:** `signal_only` when scenes are unusable; `crop_specific` (fits observed NDVI trajectory against `CropGrowthCurves`) only when a reliable named crop exists *and* the cycle is complete; **`enhanced`** crop-agnostic for everything else, including all active/in-progress cycles regardless of crop status.
2. **Enhanced health score:** blends CVI-based arc shape (~30%), biomass proxy (~25%), stability (~20%), momentum (~15%), canopy duration (~10%) — a heuristic composite, not a physically calibrated yield model.
3. **Anomaly detection:** IQR-fence (2.0×IQR) flags sudden drops; sustained drops require ≥4 scenes; impact severity (HIGH/MEDIUM/LOW) weighted by magnitude × growth stage.
4. **Yield potential:** trapezoidal/AUC integration of NDVI relative to adaptive baselines, combined with anomaly-free fraction, peak level, and peak consistency — explicitly a **proxy**, not a physical tonnes-per-hectare estimate. Active (in-progress) cycles are AUC-capped since their full-season integral isn't complete yet.

## Ground Reality — What This Means Operationally

- **"Yield potential" is a name that invites misinterpretation the moment it reaches a loan officer, farmer-facing dashboard, or auditor.** An NDVI-AUC proxy correlates with vigor and biomass accumulation, but the relationship between NDVI integral and actual harvested yield varies by crop, variety, planting density, and season in ways this proxy doesn't model — treating a dashboard number labeled "yield potential" as a forecasted quantity (e.g., "this farmer will produce X quintals") would be a real misuse risk in a lending context, and the original doc already flags this correctly; it's worth escalating from a documentation note to an actual UI/labeling fix given how directly it could mislead a credit decision-maker.
- **Enhanced-path scores lacking crop-curve priors means performance is measured on an absolute, not crop-relative, scale.** A well-managed pulse crop (structurally lower peak NDVI/biomass than paddy or sugarcane) will tend to score lower on absolute vigor/biomass terms than a mediocre paddy field, even if the pulse farmer is doing everything right for their crop. Since classification is off by default, this isn't a rare edge case — it's the default comparison basis for most farms, and it systematically disadvantages farmers growing lower-biomass crops (often exactly the drought-resilient, lower-input crops that agronomic policy in semi-arid India tends to encourage).
- **Anomaly detection on short cycles is explicitly flagged as unstable** — for a fast-cycle crop (short-duration pulses, some vegetables) with fewer total scenes even under a 10-day grid, the IQR-based fence has less data to establish a reliable "normal" baseline before flagging deviations, meaning shorter-cycle, often smaller/marginal-farmer crops are more prone to noisy anomaly penalties than long-season crops with abundant scenes.
- **AUC-capping active cycles is agronomically sensible (you can't integrate a season that hasn't finished) but creates a structural timing bias**: a farmer assessed mid-season always looks "less complete" on yield potential than one assessed post-harvest, even if both are on an identical trajectory — worth confirming this is normalized for or explicitly flagged in the credit-weight consumption (Stage 07), since otherwise assessment timing itself becomes a scoring variable unrelated to farm quality.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Rename dashboard/API labels immediately** ("yield proxy" or "vigor index" rather than "yield potential") — already flagged as a quick win in the original doc, and worth treating as near-mandatory given the lending context; this is the cheapest fix with the highest mislabeling-risk reduction.
2. **Introduce a coarse crop-family baseline even without full classification.** Rather than requiring full crop-specific curve-fitting (which needs Stage 04 classification on), a lightweight middle path — bucketing into a handful of crop-family vigor bands (e.g., "cereal/paddy-like," "pulse/oilseed-like," "horticulture-like") using cheap discriminating features already computed (peak CVI level, canopy duration, cycle length) — could reduce the cross-crop absolute-scale unfairness without requiring the full ML classifier to be trusted or enabled.
3. **Blend crop-specific and enhanced scoring when classification confidence is mid-range** (already flagged as medium) — rather than a hard binary switch between paths, a confidence-weighted blend would let partially-confident classifications still contribute crop-appropriate context instead of being all-or-nothing.
4. **Calibrate AUC-to-yield-quantile mapping against regional ground truth where available** (already flagged as major) — e.g., district-level agriculture department yield statistics (many states publish these) could anchor the proxy to at least a regional relative-yield scale, moving it from "internally consistent proxy" to "externally calibrated estimate," which matters if the number is ever used in loan-sizing decisions.
5. **Explicitly flag or normalize for assessment timing (mid-season vs. post-harvest) in how Stage 07 consumes `yield_potential`**, so two structurally similar farms assessed at different calendar points don't get systematically different credit outcomes purely due to timing.
6. **Unit-test the enhanced scoring function on synthetic NDVI arcs** (already flagged) — given how much weight this single scoring function carries (40 of 100 credit points combined with anomaly penalty), synthetic-arc regression tests (steady healthy season, mid-season stress-and-recovery, early failure, late-season stress) would catch unintended scoring drift when the function is inevitably tuned over time.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Rename "yield potential" to "yield proxy / vigor index" in dashboard and API | Reduces real risk of the score being misread as a forecasted physical yield in a lending decision |
| Quick win | Unit tests for enhanced scoring on synthetic NDVI arcs | Protects the single highest-weight scoring function (40/100 credit points) from silent drift |
| Medium | Coarse crop-family vigor banding even without full classification | Reduces cross-crop absolute-scale unfairness that's structural given classification-off default |
| Medium | Confidence-weighted blend of crop-specific + enhanced when classification confidence is mid-range | Avoids all-or-nothing loss of crop context at exactly the confidence range where it's most available |
| Medium | Explicitly normalize/flag assessment-timing bias (mid-season vs. post-harvest) in Stage 07 consumption | Prevents assessment date itself from becoming an unintended scoring variable |
| Major | Calibrate AUC → regional yield quantiles using published district agri-department ground truth | Moves the proxy from internally-consistent to externally-anchored, important if used in loan sizing |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stage 04 `season_results` (+ scenes from 02/03) |
| Downstream | Stage 07 weights; Stage 08 explainability |
| Shared | `PERFORMANCE_*` and `CropGrowthCurves` in `config.py` |

---
*This document supersedes the original `06-yield-performance-evaluation.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
