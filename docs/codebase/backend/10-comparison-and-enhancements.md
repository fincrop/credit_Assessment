# Backend — Comparison & Enhancements (Working Plan)

**Status:** Draft for review — do not implement until this doc is validated.  
**Date:** 2026-07-13  
**Sources:** Updated stage deep-dives (`00`–`09`) × live tree after cleanup pass (`enhancement-roadmap.md` / `technical-debt-and-cleanup.md`).

This is the single working backlog for backend work. Stage files (`01`–`09`) remain the deep-dive rationale; this file answers: *what is true in code today, what the docs still claim incorrectly, and what we should change next — in order.*

---

## 1. How to read this doc

| Column / tag | Meaning |
|--------------|---------|
| **Docs claim** | What the updated deep-dive says about present condition |
| **Code today** | Verified against the current tree |
| **Verdict** | `Aligned` · `Doc drift` (docs stale) · `Gap` (docs right, code needs work) · `Product decision` |
| **Wave** | Sequencing for implementation after you approve this plan |

---

## 2. Doc drift first (deep-dives vs cleanup already done)

Several “Present Condition” / “Do immediately” lines in `00`–`09` were written against pre-cleanup reality. **Do not re-implement these.**

| Topic | Docs still say | Code today | Verdict |
|-------|----------------|------------|---------|
| Satellite cache | `force_fresh_download = True` hardcoded; reads dead | Env-gated `SATELLITE_FORCE_FRESH` (default off → cache reads used) | **Doc drift** — update `00` / `02` |
| SHAP `ci` NameError | Urgent fix needed | Fixed: `ci = float(ca.get('cropping_intensity', 0))` | **Doc drift** — update `00` / `08` |
| Legacy `credit_scorer.py` | Still present / edit risk | Deleted; package exports `AdvancedCreditScorer` only | **Doc drift** — update `00` / `07` |
| `CREDIT_WEIGHTS` / ₹/ha tables | Config vs Advanced diverge | Aligned; scorer + counterfactuals read `PipelineConfig.CREDIT_WEIGHTS` / `CREDIT_LIMITS_PER_HA` | **Doc drift** — update `07` |
| Season calendars | Three conflicting definitions | `SEASONS` (May/Oct windows) + `SEASON_SNAP_ANCHORS` (Jun 15 / Oct 15) documented as intentionally distinct | **Mostly aligned** — keep clarifying comments; not three “live” seasons |
| Cloud caps | Instance attrs vs single `MAX_CLOUD_COVER=60` | `MAX_CLOUD_COVER_KHARIF/RABI/CONTINUOUS` in config | **Doc drift** — update `02` |
| LGD → eco | UP only | Major state LGD codes mapped in `india_geo_context` | **Doc drift** — update `01` |
| Cycle hints | Discarded via `_ = (...)` | Logged; `hints_applied: false` — still not used in detection math | **Partial** — docs overstate “silent discard”; gap remains on *application* |
| AI `--explain` gate | Stale “not auto-run” | Enrichment runs SHAP/CF when `AI_CONFIG` enables them | **Doc drift** if any leftover claims remain |
| Dead seasonal collector | ~1200 commented lines | Removed | **Doc drift** — update `02` |

**Housekeeping after validation:** patch `00`–`09` “Present Condition” sections so they match this table, or point them here so engineers do not re-fix closed items.

---

## 3. Stage-by-stage: present condition vs expected enhancements

### Stage 01 — Geospatial prep & snapping

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Polygon vs point bbox; 500 m floor | Live (`MIN_FIELD_BUFFER_KM = 0.5`) | Adaptive / smaller buffer for smallholders | **Gap** |
| Registered vs geometry area duality | Live (registry drives credit area) | Geometry QA gate (area ratio + validity) | **Gap** |
| Snap anchors Jun 15 / Oct 15 | Live via `SEASON_SNAP_ANCHORS` | Dynamic monsoon-onset anchors | **Gap** (major) |
| Eco-context computed | Live; LGD hints for major states | Wire into Stage 03 thresholds | **Gap** (consumed unused) |
| Assessment metadata for snap/geometry source | Not stamped | `season_anchor_used` / snap version on assessment | **Gap** |

**Cross-theme:** Smallholding / neighbor contamination starts here and pollutes 02–07.

---

### Stage 02 — Satellite observation grid

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Cache reads | Working unless `SATELLITE_FORCE_FRESH` | Env-gate (done) | **Aligned** |
| 10-day bins, missing placeholders | Live | Keep | **Aligned** |
| Cloud caps | Config `MAX_CLOUD_COVER_*` | Pixel-level mask (s2cloudless/SCL) | **Gap** (medium→major) |
| Index parity GEE vs STAC | GEE ≈ NDVI/EVI/NDMI; others often NaN | `indices_available` + GEE parity | **Gap** |
| Monsoon optical gaps | Higher cloud % only | Sentinel-1 SAR blend | **Gap** (major) |

---

### Stage 03 — Crop cycle detection & LUI

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| CVI + Bartlett + cycle walk | Live backbone (classification off) | Keep; calibrate regionally | **Aligned** core |
| Hints / `agro_profile` | Received + logged; **not applied** | Soft priors / ecoregion thresholds | **Gap** |
| Peak floor 0.28 vs baseline 0.30 | Config keys exist; class fallbacks remain | Single source of truth, no confusion | **Mostly aligned** — simplify further if desired |
| Intensity naming | LUI fraction vs cycles/year both exist | Rename distinctly in schema/API | **Gap** (clarity) |
| Duration 40–195 / annual assumption | Live | Perennial / long-duration branch | **Gap** (major) |
| Hat-profile imputation | Live | Ground-truth validation | **Gap** (major / research) |

---

### Stage 04 — ML crop classification

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Default Path B (Unclassified) | Live; ML opt-in via env / job flag | Keep conservative default | **Aligned** |
| Registry crop → `predicted_crop` | Hint logged only; Path B still `predicted_crop=None` | Use registry crop as self-reported label when ML off | **Gap** (high leverage) |
| Registry soft prior on ML path | Not implemented | Bayesian / temperature boost | **Gap** (when ML on) |
| Model card / sklearn pin / train=serve | Open | Before broad enable | **Gap** |
| Dashboard `require_classification` | Job field exists; UI toggle open | Per-region/crop toggle | **Gap** (frontend+API) |

---

### Stage 05 — Weather

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| NASA POWER + dynamic thresholds | Live | Keep; document formula + tests | **Gap** (tests/docs) |
| POWER cache | None (unlike satellite) | Mongo cache by lat/lon/range | **Gap** |
| Degraded-data flag | Silent partial failure risk | Explicit `weather_degraded` | **Gap** |
| Spatial resolution | Centroid point | Parcel-weighted / IMD / ERA5 blend | **Gap** (medium→major) |

---

### Stage 06 — Yield & performance

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Enhanced path primary (class off) | Live | Expectation reset correct | **Aligned** |
| Label “yield potential” | Still in API, scorer keys, dashboard | Rename to yield proxy / vigor index | **Gap** |
| Absolute vigor unfair to low-biomass crops | Structural under Path B | Crop-family banding without full ML | **Gap** |
| Mid-season AUC cap | Live | Flag/normalize for assessment timing | **Gap** |
| Synthetic-arc unit tests | Missing / thin | Protect 40/100 credit weight | **Gap** |

---

### Stage 07 — Credit scoring & limits

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Live weights 35/25/15/8/7/5/5 | In `PipelineConfig` + Advanced | Docs that still show old config table | **Doc drift** then keep |
| ₹/ha bands (~3–15k live) | In config; legacy 15–80k gone from tree | Business confirm scale is intended | **Product decision** |
| Golden-file regression tests | Open | Fixture score/limit tests | **Gap** |
| Unknown vs no benefits | Collapsed to neutral | Distinct states | **Gap** |
| Model score vs policy limit | Coupled in one function | Separate layers | **Gap** (major) |

---

### Stage 08 — AI enrichment

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Rule-based “SHAP” default | Live (`model=None`) | Rename to driver attribution | **Gap** (naming/compliance) |
| `ci` bug | Fixed | — | **Aligned** |
| LLM / Sarvam | Opt-in | Snapshot prompt + model id; compliance export | **Gap** |
| Master kill switch | Per-block config only | `AI_ENRICHMENT_ENABLE` | **Gap** (ops) |

---

### Stage 09 — Jobs & worker

| Aspect | Code today | Docs expect / recommend | Verdict |
|--------|------------|-------------------------|---------|
| Dual consumer (worker + FastAPI BackgroundTasks) | Live | Keep until queue migration | **Aligned** |
| Single `asyncio.Lock` | One heavy job / process | Bounded pool 2–4 | **Gap** |
| Stuck RUNNING | No reaper | Timeout → FAILED + retry path | **Gap** (urgent) |
| Queue health visibility | Absent | Depth / oldest / consumer alive | **Gap** |
| Progress field | Stages emitted internally; UI weak | Surface `pipeline_stages` while polling | **Gap** |
| Job status authz | Login cookie; ownership open | Farmer/account scoping | **Gap** |
| Distributed queue | Not present | Celery/RQ/SQS later | **Gap** (major) |

---

## 4. Cross-stage themes (still valid)

These three threads from `00-overview.md` remain the right prioritization lens:

1. **Classification off by default** — Path B is production. Registry-as-`predicted_crop` and crop-family banding unlock 05/06/07 without trusting ML.
2. **Smallholding + monsoon India** — 500 m buffer, Kharif clouds, national CVI floors, absolute vigor, POWER coarseness are one problem set; prefer compounding fixes (adaptive buffer, SAR later, regional CVI).
3. **Integrity / lending risk** — ₹/ha policy confirmation, label honesty (“yield proxy”, “driver attribution”), golden tests, stuck-job reaper matter more than neat refactors.

---

## 5. Proposed waves (implement only after you sign off)

### Wave 0 — Documentation hygiene (no behavior change)

1. Refresh `00`–`09` Present Condition to match §2 (or add “as of cleanup: see `10-comparison…`”).
2. Mark closed roadmap rows in stage “Enhancement Recommendations” tables as **Done** with pointers to `technical-debt-and-cleanup.md`.

**Exit criteria:** An engineer reading only stage docs cannot be told to re-fix `ci`, force-fresh, or revive `credit_scorer.py`.

---

### Wave 1 — Integrity & ops (cheap, high risk reduction)

| ID | Item | Stage | Notes |
|----|------|-------|-------|
| B1 | Confirm ₹/ha limit scale with lending/product | 07 | **Blocker for any limit tuning** — product, not code |
| B2 | Job reaper: RUNNING past timeout → FAILED | 09 | Matches open Q10 |
| B3 | Job queue health endpoint (depth, oldest QUEUED, consumer heartbeat) | 09 | |
| B4 | Dashboard progress from `pipeline_stages` / elapsed | 09 + FE | Open Q12 |
| B5 | Rename UI/API display strings: yield potential → yield proxy / vigor | 06 + FE | Keep internal keys stable initially if needed |
| B6 | `AI_ENRICHMENT_ENABLE` master switch | 08 | |

**Exit criteria:** Stuck jobs self-heal; operators see queue health; product has answered B1.

---

### Wave 2 — Unlock Path B accuracy (compounding)

| ID | Item | Stage | Notes |
|----|------|-------|-------|
| B7 | Registry crop → `predicted_crop` with `source: registry_self_report` when ML off | 04 | Highest leverage quick win still open |
| B8 | Soft-apply `agro_profile` / sowing hint in cycle detector (thresholds / sow bias) | 01→03 | Open M1 |
| B9 | Disambiguate schema names: `land_utilization_fraction` vs `cycles_per_year` | 03/04/07 | |
| B10 | Crop-family vigor banding for enhanced performance path | 06 | Reduces absolute-scale unfairness |
| B11 | Golden-file tests for credit score + limit | 07 | |
| B12 | NASA POWER response cache + weather degraded flag | 05 | |

**Exit criteria:** Declared crop and eco context affect live Path B; credit math has regression fixtures.

---

### Wave 3 — Spatial / temporal fidelity

| ID | Item | Stage |
|----|------|-------|
| B13 | Geometry QA (area ratio + validity) before trusting polygon | 01 |
| B14 | Adaptive point buffer (lower floor for small declared area) | 01 |
| B15 | Stamp snap/geometry provenance on assessment | 01 |
| B16 | `indices_available` on assessment; degrade Stage 06/08 gracefully | 02 |
| B17 | Pixel-level cloud/shadow masking | 02 |
| B18 | Assessment-timing flag/normalize for in-progress yield proxy | 06 |
| B19 | Unknown vs confirmed-absent benefits in credit | 07 |
| B20 | Rename rule “SHAP” → driver attribution in API/docs/UI | 08 |
| B21 | Bounded concurrency pool (replace single lock) | 09 |
| B22 | Job status ownership / authz scoping | 09 |

---

### Wave 4 — Structural investments (plan, don’t start until Waves 1–2 validated)

| ID | Item | Stage |
|----|------|-------|
| B23 | Separate model score vs policy limit layer; recalibrate ₹/ha after B1 | 07 |
| B24 | Perennial / long-duration cycle branch | 03 |
| B25 | Sentinel-1 SAR monsoon fill | 02 |
| B26 | IMD / ERA5-Land weather blend | 05 |
| B27 | Retrain classifier + soft registry prior; model card | 04 |
| B28 | Compliance explanation export (deterministic only) vs LLM narrative | 08 |
| B29 | Distributed task queue + DLQ | 09 |
| B30 | District yield quantile calibration for vigor proxy | 06 |
| B31 | Monsoon-onset-aware dynamic snap | 01 |

---

## 6. Suggested first implementation slice (after your OK)

If Wave 0 + Wave 1 are approved without changes, the first **code** PR sequence would be:

1. **B2** Job reaper  
2. **B3** Queue health  
3. **B6** AI enrichment master switch  
4. **B5** Yield-proxy labeling (FE + format strings; optional API alias)  
5. Then start Wave 2 with **B7** registry crop as `predicted_crop`

B1 stays a parallel product conversation; do not change limit bands in code until it returns.

---

## 7. Validation checklist (please confirm)

Tick or edit before we proceed:

- [ ] §2 doc-drift list matches your understanding of the cleanup pass  
- [ ] Wave order (0 → 1 → 2 → 3 → 4) is acceptable  
- [ ] B1 (₹/ha) is owned by product/lending — we only document until answered  
- [ ] B7 (registry crop without ML) is desired product behavior, not a compliance risk  
- [ ] Wave 4 items stay out of scope until Waves 1–2 land  
- [ ] Preferred first code slice: B2 → B3 → B6 → B5 → B7 (or propose a different order)  
- [ ] Any items to **drop**, **defer**, or **promote** from Waves 2–4  

---

## 8. Relationship to other docs

| Doc | Role after this file exists |
|-----|-----------------------------|
| `00`–`09` stage deep-dives | Why / ground reality / design options |
| This file (`10-comparison-and-enhancements.md`) | **What to do next** + verified present state |
| `maintenance/enhancement-roadmap.md` | Cross-cutting status board (FE/deploy too) — sync IDs when Waves complete |
| `maintenance/technical-debt-and-cleanup.md` | Historical cleanup log; “do not reintroduce” |

---

*Supersedes informal sequencing in `00-overview.md` “Prioritized Cross-Stage Roadmap” for execution planning; keep that section as narrative until Wave 0 updates it to point here.*
