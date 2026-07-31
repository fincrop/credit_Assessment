# Agronomic Credit-Risk Engine — Backend Re-Architecture (v5, "index_v5")

**Positioning:** India-first, smallholder KCC/agri-loan context, thousands of parcels.
**Deliverable:** an *expert-weighted, explainable **agronomic risk index*** (0–100) — **not** a
loan amount, **not** a repayment-calibrated PD model (yet). Every design choice below is made so a
**validation / calibration layer** and a **performance-feedback loop** can be added later without
touching the analysis stages.

This document is the contract we implement against. It makes concrete decisions (marked **LOCKED**),
proposes defaults where your input is ideal (marked **DEFAULT — confirm**), and lists the few things
that depend on your environment (marked **NEEDS YOU**).

---

## 0. Non-negotiable design principles

1. **Deterministic & attributable.** Every point in the final index traces to a named driver with a
   reason code. Rule-based first; any ML is an optional, decoupled challenger. (Matches RBI-style
   model-governance expectations and your existing rule-based instinct.)
2. **Provenance + versioning from day one.** Persist raw features, the index version, weights, data
   sources, and confidence per assessment. The moat is the future feedback loop — it's only possible
   if we store the inputs now.
3. **Decoupled sub-indices.** No signal family may dominate multiple pillars. Each sub-index is
   computed from distinct signals so it can be validated and recalibrated independently.
4. **Confidence-aware, never silently confident.** Cloud gaps, SAR fallback, small parcels, and short
   series all reduce a **Data-Confidence** meta-signal that gates/annotates the score.
5. **Parcel ≠ farmer.** Satellites see land; credit goes to a person. Linkage, multi/leased parcels,
   and tenure are first-class (Phase-1 stub, Phase-2 hardened).
6. **Peer-relative, not absolute.** Score each parcel against the distribution of similar parcels in
   the same agro-climatic zone × season window. "Doing well *for its context*" is more robust and
   more explainable than an absolute number.
7. **Crop as soft context, not hard input.** Inferred crop/season conditions what "normal" phenology
   and stress look like; it is **never** a hard dependency (classification stays unreliable/off).

---

## 1. Banking frame (how we sell it) — 5 Cs → signal map

| Banker's C | Our signal | Pillar |
|---|---|---|
| **Capacity** | Cropping intensity, cycles/year, land-use trend over 3 yrs, fallow frequency | Land-Use & Activity |
| **Character** | Yield-potential stability, stress-management track record (how parcel recovered) | Vigor + Stability |
| **Conditions** | Weather resilience (coped with past adverse events) + forward climate exposure | Weather |
| **Capital/Collateral** | (out of scope for the index; comes from land record / lender) | — |
| **(meta)** | Data coverage / confidence | Data-Confidence |

This mapping is how the index is explained to a lender and is the backbone of the reason codes.

---

## 2. Target stage architecture

```
S0  Ingest & Geo-QA          parcel geometry, area QA, agro-climatic zone tag,
                             season-aware window, parcel→farmer linkage stub
S1  Data Acquisition         optical (S2) + SAR (S1) + weather (IMD/ERA5/CHIRPS/POWER)
                             scalar-first (batched reduceRegions), tiff-on-demand
S2  Signal Construction      multi-index + optical–SAR fusion → smoothed veg signal + NIRv + LSWI
S3  Phenology Engine         double-logistic fit → SOS/POS/EOS → cycles (kharif/rabi/zaid +
                             long-duration branch), cloud-gap inference
S4  Crop Context (optional)  registry-first soft prior; ML challenger stays off/decoupled
S5  Stress & Yield-Potential stage-weighted anomalies vs per-parcel baseline; NIRv-AUC potential
S6  Weather Engine           backward resilience + forward exposure; SPI/SPEI/dry-spell/GDD/onset
S7  Risk Index               5 decoupled sub-indices → weights → peer-percentile → 0–100 + reason codes
S8  Explainability           deterministic driver attribution, reason codes, counterfactuals
S9  Persistence & Provenance versioned feature + index docs; calibration/outcome hooks reserved
S10 Orchestration            batch + bounded-concurrency queue; scaled for thousands of parcels
```

The two "later" layers (validation/calibration + performance feedback) attach only to **S7** and
**S9**; nothing else changes when they arrive.

---

## 3. S1–S2 — Data acquisition, indices & SAR fusion  **(Pillar 1 — implement first)**

### 3.1 Index set (LOCKED unless noted)

Keep existing: `NDVI, EVI, NDMI, PSRI, NDRE, NDWI`. **Add:**

| Index | Formula | Buys us |
|---|---|---|
| **MSAVI2** | `(2·NIR+1 − √((2·NIR+1)² − 8·(NIR−Red)))/2` | Robust **early-season/sowing** signal on bare/sparse soil (NDVI is noisy there) |
| **NIRv** | `NIR × NDVI` | **Yield-potential backbone** (GPP/biomass proxy) — decoupled from detection CVI |
| **LSWI** | `(NIR−SWIR1)/(NIR+SWIR1)` | **Water stress** + paddy flood signal (complements NDMI) |
| **GCVI** | `NIR/Green − 1` | High-LAI vigor (maize/sugarcane) — **DEFAULT, optional** |
| **kNDVI** | `tanh(NDVI²)` | Saturation-resistant, higher SNR — **DEFAULT** as smoother backbone |
| **RVI (SAR)** | `4·VH/(VV+VH)` | Cloud-proof biomass proxy for monsoon gap-fill |
| **VH/VV, VV, VH** | backscatter | Paddy transplanting/flood signature; sowing confirmation |

### 3.2 Composite vegetation signal `VS` (LOCKED)

Per 10-day bin, build a **quality-weighted composite** rather than a fixed 0.5/0.3/0.2 blend:

```
VS_optical = w1·kNDVI' + w2·EVI' + w3·NDMI'      (indices min-max normalized to comparable scale)
quality    = f(valid_pixel_fraction, cloud_prob, parcel_size)   ∈ [0,1]
VS         = quality · VS_optical  +  (1−quality) · VS_sar
VS_sar     = scale(RVI)  (per-parcel RVI→NDVI regression when ≥ N paired clear bins, else global scale)
```

- Every bin stamped `signal_source ∈ {optical, sar, fused, imputed}` and `bin_quality`.
- **Cloud mask upgrade:** adopt GEE **Cloud Score+ (cs_cdf)** in addition to SCL/QA60; bump
  `cloud_mask_version` → `csplus_scl_v2`.
- **Smoothing:** replace ad-hoc linear/hat imputation with a **Whittaker smoother** (2nd-diff,
  λ tuned by grid step) as default; Savitzky–Golay fallback. Imputed bins flagged.

### 3.3 Season-aware window (LOCKED — fixes current bug)

Extend the lookback **backward to the nearest season anchor** so we never ingest a clipped season,
even if total span exceeds 3y by a few weeks. Retire the vestigial `NUM_SEASONS`/`MAX_YEARS_BACK`
knobs for the continuous path; keep anchors (Jun 15 / Oct 15) **+ add Feb 15 (zaid)**.

### 3.4 Real-time branch (LOCKED)

Second collection mode: from `sowing_date → today`, same binning/fusion, feeding S3 in "active-cycle"
mode (AUC-capped, projection notes) — you already have active-cycle plumbing in performance.

### 3.5 Scale (DEFAULT — confirm)

- **Scalar-first, batched:** one server-side `reduceRegions` over a FeatureCollection of parcels per
  bin, not per-parcel calls. GeoTIFF export stays an opt-in flag with parallel band reads.
- GEE treated as **R&D/compute backend**; schema designed so a precomputed **data-cube** can replace
  the pull later without touching S2+.

**Files touched:** `satellite_collector.py` (rewrite acquisition + fusion + Cloud Score+ + batching),
`data_processing.py` (add MSAVI2/NIRv/LSWI/GCVI/kNDVI/RVI, Whittaker), `config.py` (index list, mask
version, anchors, quality params), `geometry_utils.py` (parcel-size → confidence hook).

---

## 4. S3 — Phenology engine  **(Pillar 2)**

### 4.1 Method (LOCKED)

Per candidate season window, **fit a double-logistic** curve to the smoothed `VS`; derive phenometrics
from the fit (stable under gaps):

- **SOS** = first crossing of `base + 20% amplitude` on the rise; **POS** = fitted max;
  **EOS** = crossing back below `base + 20% amplitude` on the fall; confirmed by first-derivative
  sign change. Amplitude/base gate replaces brittle absolute CVI thresholds.

### 4.2 Kharif cloud-gap inference (LOCKED — formalizes your logic)

Decision layer on pre-/post-monsoon signal + SAR:

```
pre_monsoon (Apr–May) LOW  &  post_monsoon (Sep–Oct) HIGH  → sowing in Jun–Aug gap  ⇒ KHARIF cycle
   └─ if SAR flood signature (VV/VH sharp drop→rise) present ⇒ confirm paddy transplant
pre_monsoon HIGH  &  stays HIGH through Aug–Sep            → summer/long-duration already present
                                                            ⇒ NO new kharif sowing (don't invent a cycle)
```

### 4.3 Seasons & durations (LOCKED — fixes current bug)

- Three seasons: **kharif / rabi / zaid**.
- **Long-duration / perennial branch:** duration cap driven by crop-family (sugarcane/banana ≈330 d),
  **not** the global `CROP_CYCLE_MAX_DURATION_DAYS=195` that currently splits/rejects them.

**Files touched:** `crop_cycle_detector.py` (rewrite core to double-logistic + inference layer +
long-duration branch + zaid), `config.py` (per-family duration table, amplitude gates),
`india_geo_context.py` (zone → season/onset priors), `land_utilization_analyzer.py` (3-season LUI).

---

## 5. S4 — Crop context (LOCKED: soft only)

Registry crop → soft context prior (conditions expected phenology/stress + selects peer cohort).
ML classifier remains an **off-by-default, decoupled challenger** (kept, pinned, model-carded, never
in the critical path). No crop identity is required to produce a score.

**Files touched:** `crop_detector.py` (demote to optional challenger + soft-prior emitter),
`main.py` (wire soft prior, not hard input).

---

## 6. S5 — Stress & yield-potential  **(Pillar 3)**

### 6.1 Stress (LOCKED)

- Baseline = **the parcel's own 3-yr, per-growth-stage** distribution (not just within-cycle IQR).
- Flag **z-score / percentile-fence** departures; water stress from **LSWI/NDMI** drops.
- **Stage-weighted impact:** flowering & grain-fill >> vegetative > senescence (your `_stage()` gets
  promoted to drive the penalty).

### 6.2 Yield potential (LOCKED — potential, not tonnage)

- `potential = NIRv-AUC (∫ over cycle)` → **peer-normalized percentile** (agro-zone × season × crop-family).
- Output `potential_index` (0–100) **plus** an empty `quantified_yield` slot that a crop coefficient
  can fill later — preserving your "quantify only if crop is known" principle.

**Files touched:** `performance_analyzer.py` (rewrite: NIRv-AUC potential, per-parcel baseline,
stage-weighted anomalies, peer-percentile), new `peer_benchmark.py`.

---

## 7. S6 — Weather engine  **(Pillar 4 — make it a real pillar)**

### 7.1 Two-directional (LOCKED)

- **Backward = resilience:** correlate historical weather anomalies with the parcel's *observed*
  vegetation response (did vigor hold through the 2023 dry spell?). This is the high-value signal.
- **Forward = exposure:** regional climate-risk profile for the zone (drought/flood/heat propensity).

### 7.2 Indicators (LOCKED)

`SPI-1/-3`, `SPEI` (folds heat into drought), **rainfall departure vs LPA** in IMD bands
(normal ±19% / deficient / excess), **dry-spell & wet-spell run lengths**, **GDD** + heat/cold-stress
days, **monsoon onset/withdrawal timing anomaly** — all **aligned to detected growth stages** so
"heat stress at flowering" is a first-class reason code.

### 7.3 Data blend (NEEDS YOU on availability)

Priority: **IMD 0.25° gridded** (authoritative for India) → **ERA5-Land** (temp/ET) → **CHIRPS**
(rainfall cross-check) → **NASA POWER** (current fallback). Source stamped per cycle; cache extended
from the existing POWER cache.

**Files touched:** `weather_analyzer.py` (rewrite: multi-source blend, SPI/SPEI/spell/GDD/onset,
resilience correlation, stage alignment), `config.py` (weather sources, index params),
`mongodb_helper.py` (generalize weather cache).

---

## 8. S7 — Risk index construction  **(Pillar 5 — the core)**

### 8.1 Five decoupled sub-indices (LOCKED structure)

| Sub-index | Built from (distinct signals) | Banker's C |
|---|---|---|
| **Land-Use & Activity** | cycles/yr, LUI, fallow frequency, trend | Capacity |
| **Vigor & Yield-Potential** | NIRv-AUC potential, peak quality (peer-normalized) | Capacity/Character |
| **Stability & Stress** | anomaly load (stage-weighted), inter-year variability | Character |
| **Weather** | resilience + forward exposure | Conditions |
| **Data-Confidence** | valid-bin fraction, SAR-fallback share, parcel size, series length | meta |

Fixes the current problem where peak-greenness drove ~75/100 across three pillars.

### 8.2 Weighting (DEFAULT — needs AHP sign-off)

Proposed starting weights (to be finalized by **AHP** with your agri+banking experts):

```
Land-Use & Activity   30
Vigor & Yield-Pot.    25
Stability & Stress    20
Weather               25            ← up from today's 8; you asked weather to be a real pillar
--------------------------------
Data-Confidence       applied as a MULTIPLIER/gate (0.6–1.0), not additive
```

Rationale documented per weight so a model review can defend "why 25 not 8."

### 8.3 Normalization (LOCKED)

**Peer-percentile within agro-climatic zone × season × crop-family cohort**, then compose.
Reason codes emitted per sub-index ("70th pct vigor for zone/season; weather resilience high;
data confidence reduced by monsoon cloud gap").

### 8.4 Calibration hooks (LOCKED — build now, use later)

Persist: `index_version`, weight vector, every sub-index input feature, cohort id, confidence, and
**reserved empty fields** for outcome labels / reject-inference / score→PD. Champion/challenger and
PSI-drift monitors are stubs now.

### 8.5 Retire / fix

- Drop loan-amount/₹-limit outputs entirely (your instruction) — removes the score-vs-limit
  dual-threshold trap.
- Fix the **tri-state benefits collapse** (worker path currently forces unknown→False).

**Files touched:** `advanced_credit_scorer.py` → **rename/rewrite** to `risk_index_engine.py`
(sub-indices, AHP weights, percentile, confidence gate; drop limits), `config.py` (weights, cohorts),
`farmer_benefits.py` (preserve tri-state through job path), `job_runner.py` (stop forcing False).

---

## 9. S8 — Explainability (LOCKED)

Repurpose the existing narrative engine into **reason-code / adverse-action** output. Keep deterministic
driver attribution + counterfactuals (already present). Groq/Sarvam stay opt-in with prompt/model
snapshot. Add a **compliance-only export** that returns deterministic blocks with no LLM text.

**Files touched:** `enrichment.py`, `shap_explainer.py` (→ driver-attribution over new sub-indices),
`counterfactual_engine.py`, `groq_report_generator.py`, `sarvam_translator.py` (unchanged interface).

---

## 10. S0 & S9 — Ingest/linkage & persistence

- **S0:** geometry QA (keep), agro-zone tag, season-aware window, **parcel→farmer linkage stub**
  (farmer score = tenure-weighted aggregation over parcels; Phase-1 stub, flagged for real land-record
  integration). Tenancy noted as fraud vector.
- **S9 Mongo collections:**
  `farms`, `assessments` (slim), **`feature_store`** (per-parcel versioned raw features — NEW),
  `satellite_stats_cache`, `weather_cache` (generalized), `jobs`, **`index_versions`** (weights +
  formulas per version — NEW), **`cohort_stats`** (peer distributions — NEW).

**Files touched:** `main.py` (orchestration/linkage), `mongodb_helper.py` (+feature_store,
index_versions, cohort_stats, generalized cache), `serialization.py` (new schema).

---

## 11. S10 — Orchestration & scale (DEFAULT)

- Batched acquisition (§3.5), Mongo cache, and a **bounded-concurrency pool (2–4)** replacing the
  single global lock. Keep reaper + `/v1/jobs/health` + progress. Distributed queue (Celery/RQ) stays
  a documented future step.

**Files touched:** `worker.py`, `app.py`, `job_runner.py`.

---

## 12. File-by-file change map (all 24)

| File | Action |
|---|---|
| `satellite_collector.py` | **Rewrite** — S1/S2 acquisition, SAR fusion, Cloud Score+, batching |
| `data_processing.py` | **Rewrite/extend** — new indices, Whittaker/SG smoothing |
| `crop_cycle_detector.py` | **Rewrite** — double-logistic phenology, cloud-gap inference, zaid, long-duration |
| `land_utilization_analyzer.py` | **Modify** — 3 seasons, trend/fallow features |
| `performance_analyzer.py` | **Rewrite** — NIRv-AUC potential, per-parcel baseline, stage-weighted stress, peer-percentile |
| `weather_analyzer.py` | **Rewrite** — multi-source blend, SPI/SPEI/spell/GDD/onset, resilience, stage-align |
| `advanced_credit_scorer.py` | **Rewrite → `risk_index_engine.py`** — 5 sub-indices, AHP, percentile, confidence gate; drop limits |
| `crop_detector.py` | **Modify** — demote to optional soft-prior/challenger |
| `india_geo_context.py` | **Modify** — zone → season/onset/cohort priors |
| `geometry_utils.py` | **Modify** — parcel-size → confidence |
| `farmer_benefits.py` | **Modify** — preserve tri-state end-to-end |
| `enrichment.py` | **Modify** — reason codes, compliance-only export |
| `shap_explainer.py` | **Modify** — attribution over new sub-indices |
| `counterfactual_engine.py` | **Modify** — target sub-indices |
| `mongodb_helper.py` | **Modify** — feature_store, index_versions, cohort_stats, generalized cache |
| `serialization.py` | **Modify** — new schema |
| `main.py` | **Rewrite** — new stage orchestration, linkage, soft crop prior |
| `job_runner.py` | **Modify** — tri-state fix, progress |
| `worker.py` / `app.py` | **Modify** — bounded concurrency |
| `config.py` | **Rewrite** — index list, mask version, anchors+zaid, weather sources, sub-index weights, cohorts, durations |
| `groq_report_generator.py` / `sarvam_translator.py` | **Keep** — interface stable |
| `unsupervised_segmentation.py` | **Keep/park** — challenger only |
| `peer_benchmark.py` | **NEW** — cohort percentile engine |

**NEW modules:** `peer_benchmark.py`, `feature_store` (in mongodb_helper), `phenology fit` util.
**Retired:** ₹-limit logic, vestigial season-count knobs, ad-hoc hat imputation.

---

## 13. Decisions I need from you before writing code

**NEEDS YOU (environment — these gate what's LOCKED vs fallback):**
1. **Sentinel-1 SAR** — is S1 GRD reliably available for your parcels on your GEE account? (If not, SAR
   fusion becomes an optional path and cloud gaps lean harder on smoothing.)
2. **IMD gridded** — do you have access to IMD 0.25° rainfall / 1° temperature, or is it NASA POWER
   only for now? (Determines whether IMD is primary or a later upgrade.)
3. **GEE quota headroom** at your parcel volume — rough sense (are you near limits today?).
4. **Parcel→farmer linkage data** — do farmer records already link multiple parcels / tenure, or is the
   linkage stub Phase-2 only?

**DEFAULT — confirm or override:**
5. Sub-index **weights** (§8.2) and the Weather bump to 25 — OK as a starting point pending AHP?
6. kNDVI as the smoother backbone, and GCVI as optional (§3.1)?

Say **"use defaults"** and I'll proceed with the defaults above (SAR + IMD treated as available-with-
graceful-fallback, weights as proposed).

---

## 14. Implementation sequencing

Pillars are lockable, testable units. Order (each feeds the next):

1. **Pillar 1 — Signal + SAR fusion** (`satellite_collector`, `data_processing`, `config`)
2. **Pillar 2 — Phenology engine** (`crop_cycle_detector`, `land_utilization_analyzer`, `config`)
3. **Pillar 3 — Stress & yield-potential + peer benchmark** (`performance_analyzer`, `peer_benchmark`)
4. **Pillar 4 — Weather engine** (`weather_analyzer`, `mongodb_helper`)
5. **Pillar 5 — Risk index + explainability** (`risk_index_engine`, `enrichment`, `shap_explainer`)
6. **Wiring — orchestration, persistence, scale** (`main`, `mongodb_helper`, `serialization`, `worker`, `app`, `job_runner`)

We write **full, runnable files per pillar**, you review against your environment, then move on.
