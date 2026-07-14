# Backend — Overview (Updated Deep-Dive)

Orchestrator: `SatelliteBasedCreditPipeline` in `main.py` (v4.0, profile `continuous_v4`). Single production path — continuous Sentinel-2, crop cycles, `AdvancedCreditScorer`. Entry points: CLI/`assess_farmer`, `assess_farmer_from_db`, FastAPI (`api/app.py`), and `worker.py` via `api/job_runner.py`.

## Stage map (docs ↔ runtime)

```mermaid
flowchart LR
  S0[0 Farm DB] --> S1[1 Geospatial prep]
  S1 --> S2[2 Satellite grid]
  S2 --> S3[3 Cycle detection]
  S3 --> S4[4 Crop ML]
  S4 --> S5[5 Weather]
  S5 --> S6[6 Performance]
  S6 --> S7[7 Credit]
  S7 --> S8[8 AI enrichment]
  S8 --> S9[9 Jobs / worker]
```

| Doc | Summary | Primary code |
|-----|---------|--------------|
| [01-geospatial-prep-snapping.md](01-geospatial-prep-snapping.md) | Geometry/bbox, ~3y window snapped to Jun 15 / Oct 15 anchors, eco-region context | `satellite_collector`, `geometry_utils`, `india_geo_context`, `main` STEP 1 prelude |
| [02-satellite-observation-grid.md](02-satellite-observation-grid.md) | 10-day bins, GEE/STAC, indices, cache key (currently force-fresh) | `satellite_collector`, `data_processing` |
| [03-crop-cycle-detection.md](03-crop-cycle-detection.md) | Impute → CVI → sow/harvest → LUI | `crop_cycle_detector`, `land_utilization_analyzer` |
| [04-ml-crop-classification.md](04-ml-crop-classification.md) | Optional RF/XGBoost; default Path B Unclassified | `crop_detector`, `main._build_unclassified_analysis` |
| [05-weather-analysis.md](05-weather-analysis.md) | NASA POWER, dynamic extremes, cycle risk | `weather_analyzer` |
| [06-yield-performance-evaluation.md](06-yield-performance-evaluation.md) | Crop-specific vs enhanced vigor/yield/anomalies | `performance_analyzer` |
| [07-credit-scoring.md](07-credit-scoring.md) | Live `AdvancedCreditScorer` weights/limits; legacy scorer unused | `advanced_credit_scorer` |
| [08-ai-enrichment-explainability.md](08-ai-enrichment-explainability.md) | SHAP rule path, counterfactuals, optional Groq/Sarvam | `ai_integration/*` |
| [09-job-orchestration-worker.md](09-job-orchestration-worker.md) | Mongo `jobs`, worker poll, FastAPI inline BackgroundTasks | `worker.py`, `api/app.py`, `job_runner.py` |
| [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md) | Docs vs live code, doc-drift, Waves 0–4 backlog | Working plan (validate before implementing) |

## Call chain (live)

```text
assess_farmer[_from_db]
  → SatelliteDataCollector.collect_historical_data
  → CropCycleDetector.detect_cycles → LandUtilizationAnalyzer.analyze
  → CropDetector.analyze_cycles  OR  _build_unclassified_analysis
  → WeatherAnalyzer.analyze_cycle_weather
  → CropPerformanceAnalyzer.analyze_performance
  → AdvancedCreditScorer.calculate_credit_score / calculate_credit_limit
  → enrich_assessment_with_ai
  → MongoDBHelper.save_assessment  (credit_assessments)
```

## Defaults that matter for restructuring

- Crop classification **off** unless `ENABLE_CROP_CLASSIFICATION` or job `require_classification`.
- Credit ML blend **off** (`CREDIT_SCORE_ML_BLEND_ENABLED = False`) → always rule-based Advanced weights.
- Satellite provider default **`gee`** (`SATELLITE_PROVIDER`).
- Mongo DB name default **`agristack`**.
- Satellite cache **reads enabled by default**; set `SATELLITE_FORCE_FRESH=1` to skip cache (opt-in bypass).

---

## Cross-Stage Themes (What Ties the Whole Deep-Dive Together)

Having gone through all nine stages in detail, three structural threads run across nearly all of them and are worth holding in mind as a set, not just per-stage:

### 1. "Classification off by default" reshapes the *entire* pipeline's real-world behavior
Stage 04 being off isn't a Stage 04-only fact — it means Stage 03's cycle detection becomes the crop-agnostic backbone for weather stage-fractions (05), performance's enhanced path becoming primary rather than curve-fit (06), and 60+ of Stage 07's 100 credit points effectively resting on crop-agnostic signal. Any future enhancement to Stage 04 (registry-crop-as-soft-prior, in particular) has an outsized multiplier effect precisely because it would unlock more accurate behavior in 05, 06, and 07 simultaneously, not just 04.

### 2. Small-holding, monsoon-dependent India ground reality is a recurring, structural constraint — not a set of isolated edge cases
The 500 m buffer floor (01) vs. ~1 ha average holding size, Kharif cloud cover (02) vs. the season farmers most need signal, national CVI thresholds (03) vs. regional crop-canopy diversity, absolute-scale vigor scoring (06) vs. lower-biomass but agronomically sound crops, and point-based weather (05) vs. topographically complex regions — these all trace back to the same underlying tension: **a pipeline built on globally standard remote-sensing tooling (Sentinel-2, NASA POWER, generic ML) serving a farmer population whose plot sizes, crop diversity, and monsoon dependency are more extreme than what those tools were originally optimized for.** Treating this as one throughline (rather than nine separate "known limitations" lists) should inform prioritization: fixes that address the underlying small-holding/monsoon reality (adaptive buffering, SAR blending, regional threshold calibration) have compounding value across stages.

### 3. Documentation/config drift and dead code are a recurring integrity risk, most acute in Stage 07
The credit-weight table, the ₹/ha scale, stale cloud-cover docs, stale "not auto-run" SHAP claims, and two dead legacy modules (`credit_scorer.py`, the commented seasonal satellite collector) all point to the same operational risk: **a well-intentioned engineer reading the "obvious" reference file will get the wrong answer for how the live system actually behaves.** For a lending system specifically, the Stage 07 weight/limit-scale discrepancy is the most urgent instance of this pattern because it's not just a documentation problem — it may represent an actual unresolved product decision about loan sizing.

## Prioritized Cross-Stage Roadmap

> **Execution plan:** see [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md) — verified present state vs deep-dive expectations, doc-drift from the cleanup pass, and Waves 0–4. Several “do immediately” items below are **already done** in code (SHAP `ci`, cache env-gate, legacy scorer removal); treat that file as the source of truth for what to implement next.

**Still open (cheap, high-risk-reduction):**
- Confirm the ₹/ha credit-limit scale with business/lending policy owners (Stage 07)
- Add a reaper for stuck RUNNING jobs (Stage 09)
- Use registry-declared crop directly as `predicted_crop` even with ML classification off (Stage 04)

**Do next (moderate effort, compounding value):**
- Region/ecoregion-aware CVI thresholds using Stage 01's already-computed but currently unused context (Stages 01 → 03)
- Rename misleading labels — "yield potential" → "yield proxy," rule-based "SHAP" → "driver attribution" (Stages 06, 08)
- Golden-file regression tests for credit score/limit (Stage 07)
- Health/status visibility for the job queue (Stage 09)

**Plan for (larger investments, structural improvements):**
- Sentinel-1 SAR blending for monsoon cloud gaps (Stage 02)
- Perennial/long-duration crop branch for sugarcane/horticulture (Stage 03)
- IMD/ERA5-Land blending for India-appropriate weather resolution (Stage 05)
- Separate deterministic "model score" from tunable "policy limit" (Stage 07)
- Migrate job orchestration to a proper distributed task queue (Stage 09)

---
*Cross-stage synthesis on top of the stage map and call chain. For comparison vs live code and sequenced enhancements, use [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md).*
