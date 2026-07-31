# Backend — Overview

**Location:** `backend/Credit_assessment/` (cwd for uvicorn / worker / CLI).

Orchestrator: `SatelliteBasedCreditPipeline` in `main.py` (v4.0, profile `continuous_v4`). Single production path — continuous Sentinel-2, crop cycles, `AdvancedCreditScorer`. Entry points: CLI/`assess_farmer`, `assess_farmer_from_db`, FastAPI (`api/app.py`), and `worker.py` via `api/job_runner.py`. Env file: `backend/Credit_assessment/.env`.

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
| [01-geospatial-prep-snapping.md](01-geospatial-prep-snapping.md) | Adaptive buffer, geometry QA, snap provenance, eco soft priors | `satellite_collector`, `geometry_utils`, `india_geo_context` |
| [02-satellite-observation-grid.md](02-satellite-observation-grid.md) | 10-day bins, GEE/STAC, cache, indices_available | `satellite_collector` |
| [03-crop-cycle-detection.md](03-crop-cycle-detection.md) | CVI cycles + soft agro/sow priors + LUI aliases | `crop_cycle_detector`, `land_utilization_analyzer` |
| [04-ml-crop-classification.md](04-ml-crop-classification.md) | ML opt-in; Path B; registry crop as label when ML off | `crop_detector`, `main._build_unclassified_analysis` |
| [05-weather-analysis.md](05-weather-analysis.md) | NASA POWER + cache + degraded flag | `weather_analyzer` |
| [06-yield-performance-evaluation.md](06-yield-performance-evaluation.md) | Enhanced vigor / yield proxy + timing + crop-family band | `performance_analyzer` |
| [07-credit-scoring.md](07-credit-scoring.md) | Advanced scorer + tri-state benefits + golden tests | `advanced_credit_scorer` |
| [08-ai-enrichment-explainability.md](08-ai-enrichment-explainability.md) | Driver attribution, CF, optional LLM; master switch | `ai_integration/*` |
| [09-job-orchestration-worker.md](09-job-orchestration-worker.md) | Jobs, reaper, health, progress | `worker.py`, `api/app.py`, `job_runner.py` |
| [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md) | **Done vs missing vs next** (living backlog) | — |
| [stage-actions/IMPLEMENTATION-STATUS.md](stage-actions/IMPLEMENTATION-STATUS.md) | Implementation checklist from approved wave | — |

## Call chain (live)

```text
assess_farmer[_from_db]  (± force_fresh_satellite)
  → geometry QA / adaptive buffer → geospatial_prep
  → SatelliteDataCollector (cache unless force-fresh; slim write)
  → CropCycleDetector.detect_cycles (soft agro/sow priors)
  → LandUtilizationAnalyzer.analyze
  → CropDetector.analyze_cycles  OR  _build_unclassified_analysis(registry_crop?)
  → WeatherAnalyzer (POWER cache; weather_degraded)
  → CropPerformanceAnalyzer (yield proxy / timing / crop_family_band)
  → AdvancedCreditScorer (tri-state benefits)
  → enrich_assessment_with_ai  (if AI_ENRICHMENT_ENABLE)
  → MongoDBHelper.save_assessment
```

## Defaults that matter

- Crop classification **off** unless `ENABLE_CROP_CLASSIFICATION` or job `require_classification`.
- When ML is off but farm has a registry crop → used as `predicted_crop` (`registry_self_report`).
- Credit ML blend **off** → rule-based Advanced weights from `PipelineConfig`.
- Satellite provider default **`gee`**; cache **on** unless `SATELLITE_FORCE_FRESH` or per-job `force_fresh_satellite`.
- Point buffer floor ≈ **0.15 km** (`MIN_FIELD_BUFFER_KM`); `FIELD_BUFFER_LEGACY=1` restores 0.5 km.
- AI enrichment on unless `AI_ENRICHMENT_ENABLE=0`.
- Mongo DB name default **`agristack`**.

---

## Enhancement status (2026-07-14)

### Shipped (this wave)

Adaptive buffer + geometry QA + `geospatial_prep` · soft cycle priors · registry Path B crop · indices/provider metadata · per-job force-fresh · slim satellite cache · POWER weather cache + degraded flag · yield-proxy labeling + timing + crop-family band · tri-state benefits · credit golden tests · AI master switch + model snapshot · job reaper · `/v1/jobs/health` · job progress / dashboard stage text.

### Still missing / next (priority order)

| Priority | Item | Stage |
|----------|------|--------|
| **Product** | Confirm live ₹/ha limit scale with lending owners before any band change | 07 |
| Open | Dashboard toggle for `require_classification`; sklearn pin + model card | 04 |
| Open | Stronger STAC/GEE pixel-mask parity audit; optional GEE PSRI/NDRE/NDWI | 02 |
| Open | Job ownership authz on status GET; bounded concurrency pool | 09 |
| Open | Dedicated weather risk unit tests (formula doc already partly code) | 05 |
| Major | Perennial / long-duration cycle branch | 03 |
| Major | Sentinel-1 SAR monsoon gap-fill | 02 |
| Major | IMD / ERA5-Land weather blend | 05 |
| Major | Separate model score vs policy limit; recalibrate ₹/ha after product OK | 07 |
| Major | Distributed task queue + DLQ | 09 |
| Major | Dynamic monsoon-onset snap; ICAR/NARP zone shapefile | 01 |
| Major | Compliance-only explanation export (deterministic vs LLM) | 08 |
| Major | Retrain classifier + Bayesian registry prior when ML on | 04 |

### Cross-stage themes (still valid)

1. **Classification off by default** — Path B is production; registry crop now unlocks some crop-specific downstream behavior without ML.
2. **Smallholding + monsoon India** — buffer/QA shipped; SAR and regional climate data still open.
3. **Lending integrity** — weights/limits aligned in config; **₹/ha product confirmation** remains the main open policy risk.

---
*Full done/gap tables: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md) · wave checklist: [stage-actions/IMPLEMENTATION-STATUS.md](stage-actions/IMPLEMENTATION-STATUS.md)*
