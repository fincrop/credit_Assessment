# Backend Stage 03 — Crop Cycle Detection & Land Use (Updated Deep-Dive)

## Purpose & Role

Given classification is off by default (Stage 04), this stage's output — cycle boundaries, count, and land-utilization metrics — is not just an intermediate step, it is the **crop-agnostic backbone** of the entire credit decision for the majority of live assessments. Weather windows (05), performance scoring (06), and two of the three heaviest credit weights (`crop_detection` 35, `crop_performance` 25, plus `cropping_intensity` 5 — 65 of 100 points) all trace back to what this stage decides a "cycle" is. Getting cycle boundaries wrong here doesn't get corrected later; it gets scored as if it were true.

## Present Condition — How It Actually Works Today

1. **Preprocessing:** cloud-gap flagging (≥25 days), regularization to the grid step (10 days by default from Stage 02), then imputation — short gaps (<48 days) linear-filled, long gaps (≥36 days) either "hat"-profile imputed (if post-gap decline ≥0.028) or flat/line-context filled.
2. **Composite index (CVI)** = 0.5·NDVI + 0.3·EVI + 0.2·NDMI, smoothed with a 7-point Bartlett (triangular) moving average.
3. **Cycle walk:** local maxima above a peak-CVI floor (0.28, with a class-level fallback that inconsistently reads 0.28 vs. config's 0.30 baseline — a real source of confusion when reading the code), walk backward to sowing, forward to harvest via CVI/NDVI thresholds or a max-135-day trough window, duration-gated to 40–195 days, with overlap-based merge logic for duplicate cycles.
4. **Adaptive/density passes:** if too few cycles are found, a second pass lowers the peak threshold (~0.22) and rise sensitivity (~0.07); a density pass compares against an expected-cycles-per-year benchmark (1.15/year) and further loosens prominence.
5. **Confidence scoring:** four ~25-point buckets (variation, duration, peak, shape) minus a gap penalty, floored at 10, capped at 100.
6. **Land utilization:** unique-calendar-days-covered-by-any-cycle ÷ total days (overlap-safe), plus `crop_intensity = n_cycles / years`. **Important distinction:** the "cropping intensity" that actually feeds credit scoring (Path B, Stage 04/07) is cycles/year, not this LUI fraction — two different intensity concepts exist in the codebase under similar names.
7. **Dead parameters:** `sowing_date_hint`, `crop_hint`, `agro_profile` are accepted by `detect_cycles` and then explicitly discarded (`_ = (...)`). Despite Stage 01 computing eco-region context and Stage 04's registry potentially carrying a sowing-date hint, none of it anchors or constrains cycle detection today.

## Ground Reality — What This Means Operationally

- **A single unified peak-CVI floor (0.28) and baseline (0.30) applied nationally ignores India's agro-climatic diversity.** A vegetation index threshold tuned to, say, Indo-Gangetic plain irrigated wheat/rice will behave very differently against rainfed pulses in Vidarbha, hill agriculture in the Northeast, or arid-zone bajra in Rajasthan — crops with structurally lower peak canopy density can fail to clear a flat threshold and simply never register as a "cycle," which for a credit-scoring system means a real, cultivated season silently becomes invisible land-use, understating the farmer's cropping intensity and directly lowering their score for reasons that have nothing to do with their actual farming.
- **The dead `sowing_date_hint`/`agro_profile` parameters represent a genuinely low-hanging but structurally important fix**, because this is exactly where region- or crop-aware calibration would plug in without needing to touch the core CVI/Bartlett math — it's an anchoring problem, not an algorithm problem. Right now the system has built the plumbing for this (Stage 01 computing ecoregion, Stage 04 having registry crop/sowing fields) but not the last-mile connection.
- **Duration gating (40–195 days) and the 135-day max-days-after-peak window implicitly assume annual field crops.** Long-duration crops explicitly called out as a known gap — sugarcane (10–18 month cycles), and similarly perennial/plantation crops (banana, some horticulture) — will either get truncated into a false "cycle" or fail duration gating entirely and vanish from the land-use picture. Given sugarcane alone is grown on ~5 million hectares in India concentrated in specific belts (UP, Maharashtra, Karnataka), any lender operating in those belts is scoring a nontrivial share of farms against a detector that structurally can't see their actual crop.
- **The LUI-vs-cycles/year naming collision** ("cropping intensity" meaning two different things depending on which code path you're reading) is a real operational risk for anyone doing QA, model validation, or explaining a score to a farmer/auditor — it's easy to pull the wrong number when writing a dashboard or explaining "why this farmer got this cropping-intensity score."

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Wire the already-computed ecoregion context into threshold selection.** Rather than a single global peak-CVI floor, maintain a small per-ecoregion (or per-agro-climatic-zone, see Stage 01's recommended ICAR/NARP zone join) lookup table of peak/baseline thresholds, informed by published NDVI phenology curves for dominant regional crops. This is a calibration-table change, not a rewrite of the detection algorithm — directly closes the gap flagged above.
2. **Add a perennial/long-duration branch.** Rather than forcing every signal through the 40–195-day annual-crop gate, detect a "sustained-high-canopy, no clear senescence" pattern separately and label it distinctly (e.g., `cycle_type: perennial`) so sugarcane/horticulture farms get a land-use signal instead of falling out of detection entirely. This aligns with how operational crop-monitoring systems (e.g., ICAR-NRSC's crop inventory work) typically handle sugarcane/horticulture as a separate detection class rather than forcing it through annual-cycle logic.
3. **Rename the two "intensity" concepts distinctly in code and schema** (e.g., `land_utilization_fraction` vs. `cycles_per_year`) to eliminate the naming collision — a pure clarity fix but one that reduces real audit/debugging risk.
4. **Use the sowing-date hint as a soft prior, not a hard anchor**, e.g., biasing the backward sow-walk to prefer dates near the hint rather than overriding detected dates outright — this preserves the detector's ability to catch a farmer switching crops/timing unexpectedly while still benefiting from registry information when it agrees.
5. **Validate imputation logic (hat-profile for long gaps) against ground-truth parcels** where possible — this is flagged as a "major" item in the original doc and remains one of the harder-to-validate pieces of the pipeline since it directly fabricates data during multi-week cloud gaps, which are common exactly during the Kharif window Stage 02 already struggles with.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Rename/disambiguate the two "cropping intensity" concepts (LUI fraction vs. cycles/year) in code, schema, and docs | Prevents wrong-number errors in Stage 04/07 consumption and in audit/dashboard work |
| Quick win | Reconcile class-level `LOW_CVI=0.28` vs. config `MIN_BASELINE_CVI=0.30` into one constant | Removes a documented source of reader confusion in the exact threshold that decides cycle boundaries |
| Medium | Implement region/ecoregion-aware peak-CVI thresholds using Stage 01's already-computed context | Reduces silent under-detection of legitimate cycles for crops/regions with lower canopy density |
| Medium | Persist `cycle_detection_diag` schema formally for QA dashboards | Enables systematic review of false negatives/positives instead of ad hoc debugging |
| Major | Add a perennial/long-duration crop branch (sugarcane, horticulture) instead of forcing everything through the 40–195-day annual gate | Prevents entire crop categories from becoming invisible land-use in belts where they're economically significant |
| Major | Wire `sowing_date_hint`/`agro_profile` in as soft priors rather than discarding them | Connects Stage 01/04's already-built context into the stage that most needs it, without losing detector independence |
| Major | Ground-truth validation of hat-profile imputation against known parcels | Long Kharif cloud gaps are common; fabricated data here directly shapes cycle boundaries used everywhere downstream |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stage 02 continuous series |
| Downstream | 04–07 all keyed by cycle dates |
| Shared | `PipelineConfig` cycle block; `agro_geo_context` intended but unused here |

---
*This document supersedes the original `03-crop-cycle-detection.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
