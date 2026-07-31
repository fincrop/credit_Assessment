# Backend — Comparison & Enhancements (Living backlog)

**Status:** Active — reflects code after cleanup + 2026-07-14 enhancement wave  
**Companion:** [stage-actions/IMPLEMENTATION-STATUS.md](stage-actions/IMPLEMENTATION-STATUS.md) · per-stage deep-dives `01`–`09`

Use this file to answer: **what is live now**, **what we already shipped**, **what is still missing**, **what to build next**.

---

## 1. Legend

| Tag | Meaning |
|-----|---------|
| **Done** | In code as of 2026-07-14 |
| **Missing** | Approved or clearly needed; not done yet |
| **Deferred** | Explicitly postponed (major / product / research) |
| **Product** | Needs business/lending decision before code |

---

## 2. Wave already shipped (do not re-implement)

| Area | Done |
|------|------|
| Cleanup | SHAP `ci` fix; cache env-gate; legacy `credit_scorer` removed; `CREDIT_*` aligned; cloud caps in config; LGD beyond UP; dead seasonal collector removed |
| 01 | Adaptive buffer (~0.15 km); geometry QA + fallback; `geospatial_prep` provenance |
| 01→03 | Soft `agro_profile` / sowing priors; `hints_applied` + `applied_knobs` |
| 02 | `indices_available` / provider / `cloud_mask_version`; per-job `force_fresh_satellite`; slim cache |
| 03 | `land_utilization_fraction` alias; soft priors (above) |
| 04 | Registry crop → `predicted_crop` (`registry_self_report`) when ML off |
| 04/03 | `cycles_per_year` alongside `cropping_intensity` |
| 05 | POWER Mongo + memory cache; `weather_degraded` / `weather_data_status` |
| 06 | Yield-proxy FE/API aliases; `assessment_timing`; `crop_family_band` |
| 07 | Tri-state benefits; `tests/test_credit_scoring_golden.py` |
| 08 | `AI_ENRICHMENT_ENABLE`; driver-attribution labels; LLM `model_snapshot` |
| 09 | Reaper; `GET /v1/jobs/health`; job `progress`; dashboard stage message |

---

## 3. Stage-by-stage: live vs missing vs next

### Stage 01 — Geospatial

| Item | Status |
|------|--------|
| Adaptive / smaller point buffer | **Done** |
| Geometry QA (validity + area ratio) | **Done** |
| Stamp snap/geometry on assessment | **Done** |
| Soft wire eco/hints → Stage 03 | **Done** |
| Dynamic monsoon-onset snap | **Deferred** |
| ICAR/NARP / full agro-climatic shapefile join | **Deferred** |

### Stage 02 — Satellite

| Item | Status |
|------|--------|
| Cache reads + env / per-job force-fresh | **Done** |
| `indices_available` + provider stamp | **Done** |
| Slim cache payloads | **Done** |
| Stamp cloud mask version | **Done** (audit stamp; deepen STAC parity still open) |
| Full GEE ↔ STAC index parity (PSRI/NDRE/NDWI) | **Missing** / defer unless needed |
| Stronger pixel cloudless / SCL audit & tests | **Missing** |
| Sentinel-1 SAR monsoon fill | **Deferred** |
| Commercial high-res fallback | **Deferred** (drop unless product mandates) |

### Stage 03 — Cycle detection

| Item | Status |
|------|--------|
| Soft agro / sowing priors | **Done** |
| Disambiguate LUI vs cycles/year in schema | **Done** (aliases; keep cleaning consumers) |
| Perennial / long-duration branch | **Deferred** |
| Hat-imputation ground-truth validation | **Deferred** (research) |

### Stage 04 — Crop classification

| Item | Status |
|------|--------|
| Path B default (ML off) | **Done** (by design) |
| Registry crop as `predicted_crop` when ML off | **Done** |
| Bayesian / temperature registry prior when ML on | **Missing** |
| Model card + sklearn pin + train=serve retrain | **Missing** |
| Dashboard `require_classification` toggle | **Missing** |

### Stage 05 — Weather

| Item | Status |
|------|--------|
| POWER cache | **Done** |
| Explicit degraded / unavailable flag | **Done** |
| Risk-formula unit tests (synthetic series) | **Missing** |
| Parcel-weighted multi-point sampling | **Deferred** |
| IMD / ERA5-Land blend | **Deferred** |

### Stage 06 — Performance

| Item | Status |
|------|--------|
| Yield proxy labeling (API/FE) | **Done** |
| Assessment-timing metadata | **Done** |
| Crop-family vigor banding | **Done** (heuristic; not full calibration) |
| Broader synthetic-arc unit tests for enhanced health | **Missing** (partial via credit golden only) |
| AUC → district yield quantile calibration | **Deferred** |

### Stage 07 — Credit

| Item | Status |
|------|--------|
| Config weights/limits aligned with Advanced scorer | **Done** |
| Tri-state benefits (unknown ≠ absent) | **Done** |
| Golden score/limit tests | **Done** |
| Confirm ₹/ha scale with product/lending | **Product** (blocker for band changes) |
| Split model score vs policy limit layer | **Deferred** |
| Recalibrate ₹/ha after product confirmation | **Deferred** (after Product) |

### Stage 08 — AI enrichment

| Item | Status |
|------|--------|
| Master `AI_ENRICHMENT_ENABLE` | **Done** |
| User-facing driver-attribution / yield-proxy labels | **Done** |
| Snapshot prompt hash + model id | **Done** |
| Structural compliance-only export (no LLM) | **Deferred** |
| Formal third-party LLM data-handling review | **Deferred** (ops/legal) |

### Stage 09 — Jobs

| Item | Status |
|------|--------|
| Stuck RUNNING reaper | **Done** |
| Queue health endpoint | **Done** |
| Progress / stage on job + dashboard | **Done** |
| Bounded concurrency (replace single lock) | **Missing** |
| Job ownership authz on status GET | **Missing** |
| Distributed queue + DLQ | **Deferred** |

---

## 4. Recommended next sequencing

1. **Product:** settle ₹/ha bands (Stage 07) — no limit-table edits until then.  
2. **Near-term eng:** classification dashboard toggle + model card (04); weather risk unit tests (05); enhanced-health synthetic tests (06); job authz + optional concurrency pool (09).  
3. **Structural later:** perennial cycles (03); SAR (02); IMD/ERA5 (05); score vs policy split (07); distributed queue (09); dynamic snap / zone shapefile (01).

---

## 5. Verify

```bash
python tests/test_credit_scoring_golden.py
# With API up:
#   GET /health
#   GET /v1/jobs/health
```

Env knobs of note: `SATELLITE_FORCE_FRESH`, `FIELD_BUFFER_LEGACY`, `AI_ENRICHMENT_ENABLE`, `JOB_RUNNING_TIMEOUT_MINUTES`, `ENABLE_CROP_CLASSIFICATION`.

---
*Supersedes earlier “draft for review” plan. Stage deep-dives should match this backlog’s Done/Missing tags.*
