# Backend Stage 04 — ML Crop Classification (Updated Deep-Dive)

## Purpose & Role

This stage is the fork in the road between two fundamentally different credit-scoring experiences: **Path A** (a named crop with a matched growth curve, crop-value multiplier, and crop-specific weather stage fractions) versus **Path B** (Unclassified — everything downstream treated crop-agnostically). Since classification is **off by default in production**, Path B is not a fallback edge case — it is the primary live experience for most farmers this system scores. Any discussion of "what the model does" has to be read as "what Path B does" unless classification has been explicitly enabled for that deployment/job.

## Present Condition — How It Actually Works Today

1. **Enablement gate:** `assess_farmer(..., enable_crop_classification=False)` by default; worker/API path requires `ENABLE_CROP_CLASSIFICATION` env or a per-job `require_classification` flag. When off, `CropDetector` (and its joblib model) is never even constructed — a deliberate cold-path optimization, not an oversight.
2. **Path A (ML on):** for each Stage-03 cycle, gathers real scenes in a ±5-day padded window, applies relaxed cycle-based temporal cultivation checks, interpolates NDVI/EVI/NDMI onto a fixed 15-point chronological grid (45 features total), and predicts crop + a calibrated top-1-vs-top-2-gap confidence.
3. **Path B (Unclassified, default):** `_build_unclassified_analysis` builds one `season_results` row per cycle with `predicted_crop=None`, a `cultivation_signal` proxy (`min(100, peak_ndvi*120)`), and `cropping_intensity = min(3.5, n_cycles/years_span)` — cycles/year, not the Stage 03 LUI fraction (see Stage 03's naming-collision note).
4. **Registry hints (crop, sowing date from Mongo):** logged and stored as `crop_intelligence_source` metadata, but there is **no actual probability boosting** toward the registry-declared crop in the classifier — despite this being previously documented ("registry override") in older `codebase.md`, that behavior does not exist in current code.

## Ground Reality — What This Means Operationally

- **The default-off posture is a defensible, conservative choice, not a shortcut.** Crop classifiers trained on India-wide Sentinel-2 phenology face real generalization risk across agro-climatic zones, sowing-date variability, and mixed/intercropped fields common in Indian smallholder agriculture — a wrong crop label silently steering a farmer onto the wrong growth curve (Stage 06) or the wrong crop-value multiplier (Stage 07) could be worse for score integrity than admitting "we don't know the crop" and scoring cycle-agnostically. That said, it means the pipeline's crop-specific machinery (growth curves, crop-value multipliers) is essentially unused in most live deployments today — a fact worth stating plainly to anyone assuming "the system classifies crops."
- **Because classification is off, `crop_mult` in Stage 07 stays at 1.00 for essentially all farms**, meaning the "high-value crop → 1.15x credit limit" mechanism that exists in the credit-scoring code is dormant in practice. If the product intent is to reward farmers growing higher-value crops with larger limits, that intent currently isn't being delivered unless classification is explicitly turned on — worth flagging to product/business stakeholders since this is a scoring-fairness question, not just a technical one.
- **The registry crop/sowing-date fields being logged but not used is the same pattern as Stage 03's discarded hints** — the system has the input plumbing (farmers or field agents can supply a crop name at onboarding) but doesn't use it anywhere, meaning a farmer who correctly told the system "this is my sugarcane field" gets no benefit from that declaration versus a farmer who didn't supply it. This is arguably a missed low-cost accuracy win: self-declared crop is often reasonably reliable at the individual-farmer level even where a model isn't confident.
- **A 15-point interpolation grid per cycle, 45-D feature vector** is a reasonably lightweight, classical-ML-friendly feature representation (not deep learning), which likely favors fast inference and easy retraining but caps how much sequence/temporal nuance the classifier can exploit compared to, e.g., a recurrent or transformer-based time-series classifier — reasonable trade-off for a production system prioritizing speed and explainability over marginal accuracy gains.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Implement the registry soft-prior properly, even in Path B.** The absolute minimum useful version: when a registry crop name exists, use it directly as `predicted_crop` (with a `source: 'registry_self_report'` flag and no confidence pretense) to unlock crop-specific growth curves (Stage 06) and crop-value multiplier (Stage 07) *without* running or trusting a classifier — this captures much of Path A's downstream value at near-zero additional inference cost or model-generalization risk, and directly answers the "farmer declared sugarcane, why is the score treating it as generic" fairness gap.
2. **When the ML classifier is enabled, use registry crop as a Bayesian prior/temperature-scaled boost rather than a hard override** — this is standard practice in crop classification literature (e.g., combining remote-sensing classifiers with farmer-declared or administrative crop-registry data as a prior, common in EU CAP-style area-monitoring systems) and would resolve the "no working registry soft-prior despite being documented as existing" gap cleanly.
3. **Version-pin the sklearn/xgboost training environment and ship a model card next to the joblib artifact** — current sklearn version-mismatch warnings being merely filtered rather than resolved is a latent reproducibility risk; a documented model card (training data window, region coverage, known failure crops) would materially help anyone deciding whether to flip `ENABLE_CROP_CLASSIFICATION` on for a new region.
4. **Retrain features to match the production extractor exactly** (flagged already as a major item) — since padding/interpolation details in production may not match how training data was generated, this is worth prioritizing before ever turning classification on broadly, since a training/serving skew here would be invisible until confidence scores start looking wrong for reasons unrelated to actual crop-signal quality.
5. **Expose `require_classification` as a per-farmer or per-region toggle from the dashboard**, so classification can be piloted in agro-climatic zones/crops where the model is known-good (e.g., high-signal, well-represented crops like wheat/rice) while leaving Path B as the safe default elsewhere — a staged rollout is generally safer than a blanket on/off switch for a model with acknowledged generalization uncertainty.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Use registry-declared crop directly as `predicted_crop` (flagged as self-reported, not model-inferred) even when the ML classifier is off | Unlocks Stage 06 growth curves and Stage 07 crop-value multiplier for farmers who already told the system their crop, at zero model risk |
| Quick win | Remove or correct the stale "registry override" claim in `codebase.md` | Prevents future engineers from assuming a capability exists that doesn't |
| Medium | Version-pin sklearn/xgboost to training environment; publish a model card next to `crop_classifier_model.joblib` | Reduces reproducibility risk before scaling classification usage |
| Medium | Expose `require_classification` as a per-region/per-crop dashboard toggle | Enables a staged, risk-managed rollout instead of an all-or-nothing switch |
| Major | Implement a proper Bayesian/temperature-scaled registry prior for the ML path (not a hard override) | Combines self-reported and remote-sensing-inferred crop signal the way comparable ag-monitoring systems (e.g., CAP-style area monitoring) already do |
| Major | Retrain on cycle-sliced features that exactly match the production extractor | Removes train/serve skew risk before broader classification rollout |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stage 03 cycles + Stage 02 scenes |
| Downstream | 05–07 via `cropping_analysis.season_results` |
| Shared | Same dict schema for Path A/B; `enable_crop_classification` from jobs layer |

---
*This document supersedes the original `04-ml-crop-classification.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
