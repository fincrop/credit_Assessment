# Backend Enhancements — v6

**Status:** Backend work complete. Blocked on an ingest defect (§0.3) before pilot.
**Date:** 2026-08-16
**Scope:** `backend/Credit_assessment/` only. Frontend and integrations follow in a separate pass.
**Supersedes:** `backend/Credit_assessment/Backend_enhancements.md` (which documents *intended* v5 behaviour, much of which was never wired — see §1.2).

---

## 0. Status

### 0.1 What shipped

Seven commits, `e8943ef9` → `669a9344`. Tests: **6 → 214**.

| # | Item | Status | Commit |
|---|---|---|---|
| 1 | Fix silently-wrong persisted data | ✅ | `e8943ef9` |
| 2 | Refuse to score non-agricultural land | ✅ | `ddf2cd3b` |
| 3 | Physically meaningful vegetation signal | ✅ | `53ed4a71` |
| 4 | Perennials, seasons, detection hygiene | ✅ | `e385adb2` |
| 5 | Use the crop name safely | ⬜ **not started** | — |
| 6 | Persist the evidence behind a score | ✅ | `0ce4bf4f` |
| 7 | Honest explanations | ◐ **half** — overclaiming removed; peer cohort, narrative persistence and driver captions remain | `e8943ef9` |
| 8 | Report backend | ⬜ **not started** | — |
| + | Score drift harness | ✅ | `023cf693` |
| + | INSUFFICIENT_DATA outcome | ✅ | `b023f600` |
| + | Parcel viability gate | ✅ | `669a9344` |

### 0.2 Three terminal states, deliberately distinct

Collapsing these loses exactly the information a loan officer needs:

| Status | Meaning |
|---|---|
| `SUCCESS` | We looked; here is the score. |
| `REJECTED_NOT_AGRICULTURAL` | We looked; it is not farmland. |
| `INSUFFICIENT_DATA` | We could not see it well enough to say — too small, too cloud-gapped, or an untrustworthy boundary. |

Previously every non-success collapsed to `FAILED`, so *"we refuse to score a lake"* was indistinguishable from *"Earth Engine timed out"*.

### 0.3 ⚠ The binding constraint is now the AgriStack ingest, not the backend

A geometry audit over the live database (19 farms, 113 parcels) found the parcel
records themselves are unusable, and the split by source is total:

| Source | Parcels | Area ratio in tolerance | Median size | Viable at 0.15 ha |
|---|---|---|---|---|
| App-drawn (UUID ids) | 7 | **7 / 7** | 1.59 ha | **6 viable, 1 marginal, 0 refused** |
| AgriStack (`UP*` ids) | 106 | **7 / 106** | 0.11 ha | **1 viable, 51 marginal, 54 refused** |

Polygon-vs-registered area ratios span **0.022 to 1841 in both directions**
(log₁₀ stdev 0.92). A unit error would cluster on one constant; this scatter
means the polygon and the registered area describe **different parcels**. Worst
observed: `UP119312246830`, registered 0.0036 ha against a 6.63 ha polygon.

**No amount of backend work fixes this.** The scoring pipeline can now detect and
refuse these parcels — which is why the problem is finally visible — but it
cannot repair the pairing. That belongs in the AgriStack ingest path
(`frontend/app/lib/ingestAgriStack.ts` and the Lambda), which is outside the
scope agreed for this pass.

### 0.4 Drift measured on real data

10 farmers re-assessed against their stored pre-cutover scores:

| Group | n | Mean change |
|---|---|---|
| Cycles detected | 5 | **−8.6** |
| Zero cycles | 3 | −50.5 → now correctly `INSUFFICIENT_DATA` |
| Non-agricultural | 2 | rejected (BARREN, WATER — both with clean geometry) |

**−8.6 is the honest drift figure.** The −24.3 headline from the first run mixed
unassessable parcels into the distribution.

The drift run also caught three defects that unit tests had missed — see §0.5.

### 0.5 What the drift run caught that tests did not

Worth recording, because it is the argument for running this before every release:

1. **The agro prior was dragging the peak gate to bare soil.** Its clamp was an
   absolute `[0.20, 0.35]`, written for the old scale, where 0.35 is now bare
   soil — silently re-creating the threshold inversion Phase 2 removed. Bounds
   are now relative to the configured threshold.
2. **NDBI/BSI/MNDWI never reached the gate.** A hand-written key list in the GEE
   binning step dropped every index added after it. Now driven by `INDEX_KEYS`.
3. **The no-evidence floor never fired.** `test_no_evidence_scoring` asserted it
   and passed — because the fixture omitted the key the real analyzer always
   supplies. A test that only exercises the shape it invented is not a test of
   the pipeline.

---

---

## 0. How to read this document

This is a findings-and-plan document, not a design contract. It has three parts:

- **§1–§2** — what the pipeline actually does today, verified line-by-line against the code. Every claim cites `file:line`. Where a shipped doc contradicts the code, the code wins and the contradiction is called out.
- **§3–§5** — the defect register, grouped by subsystem, each item with severity, evidence, and blast radius.
- **§6–§8** — the phased build plan, target data model, and the decisions I need from you before Phase 1 starts.

Severity scale used throughout:

| | Meaning |
|---|---|
| **P0** | Product is not sellable with this defect. Produces confidently wrong output to a lender. |
| **P1** | Materially degrades score quality or blocks the report. Fix before pilot. |
| **P2** | Correctness or maintainability debt. Fix within the v6 window. |
| **P3** | Cleanup. Fix when touching the file anyway. |

---

## 1. Executive summary

### 1.1 The five systemic problems

**① The pipeline scores non-agricultural land and reports it as a credit signal.**
There is zero land-cover logic anywhere in the backend. A grep for `worldcover|dynamicworld|landcover|lulc|ndbi|bsi|builtup|barren|waterbody` across the entire backend returns **one hit**, and it is a prose comment. Traced numerically, a parking lot yields `index ≈ 41`, `risk_category: HIGH`, `status: SUCCESS`. Dense forest scores *better* than a genuine fallow farm. This alone makes the product unmarketable, and it is not a tuning problem — the gate does not exist. §3.

**② The detection signal is not a vegetation index.**
The signal that crop-cycle detection runs on is min–max normalised per parcel over its own 3-year history (`satellite_collector.py:812`), then absolute thresholds are applied to it. Every parcel — barren, flooded, paved — produces a series spanning ~0 to ~1, guaranteeing threshold crossings. Compounding this, the backbone is kNDVI = `tanh(NDVI²)` (`satellite_collector.py:1010`), which destroys the sign of NDVI: open water at −0.30 and sparse crop at +0.30 map to the identical value 0.0876. Problem ① is unfixable without fixing ② first. §4.1.

**③ Season segmentation does not exist as an output.**
There are four mutually inconsistent season definitions in the repo, and the two month-map functions that actually run disagree with each other for February and March. `LandUtilizationAnalyzer` — the module that would produce a per-season breakdown — never reads `season_type` at all. `RiskIndexEngine` consumes no season field whatsoever. There is a cycle count and a label string; there is no Kharif/Rabi/Zaid analysis. §4.2.

**④ The pipeline computes far more than it stores, and stores it in the wrong place.**
The richest artefact in the database is `jobs.result` — a queue collection with **zero indexes**, no TTL, no schema, scanned in full every 2 seconds by the worker poll. It contains the full per-bin scene series, `yield_detail`, `health_detail`, `anomaly_events`, `weather_indicators`, and phenology. Meanwhile `credit_assessments` holds three indices (two of which are permanently `null` due to a key-name bug), cycle-window bins only, no quality flags, and no provenance. Your raw satellite time series survives only in a 30-day-TTL cache blob keyed by an opaque hash with no queryable `farmer_id`. §5.

**⑤ The narrative surface overstates the evidence.**
The Groq system prompt tells the LLM that vigor is "peer-relative NIRv-based potential"; the counterfactual copy says "raise peer-relative yield-potential"; the reason code `VIGOR_STRONG` reads "above **peers**." No peer comparison ever occurs — `upsert_cohort_stat` (`mongodb_helper.py:1132`) has **no caller anywhere in the repo**, so `cohort_stats` is empty and `PeerBenchmark` permanently cold-starts to the internal fallback. Separately, `_rule_based_shap` reports `shap_available: True` while computing a non-additive heuristic, and LLM narratives are never persisted — so text shown to a loan officer is unreproducible after the HTTP response ends. §4.4, §4.5.

### 1.2 Documentation drift — do not trust the shipped docs

Three shipped documents describe behaviour that does not exist. This matters because they will mislead anyone (including future us) planning work:

| Doc | Claim | Reality |
|---|---|---|
| `Backend_enhancements.md` §7 | `feature_store`, `index_versions`, `cohort_stats` are live persistence | `feature_store` and `index_versions` are written but **never read**. `cohort_stats` has **no writer**. |
| `Backend_enhancements.md` §4.2 | Signal is Whittaker-smoothed | Whittaker **is** computed (`satellite_collector.py:852-859`) and stored as `vs_smooth` — and read by **nothing**. Detection uses an unsmoothed value + a 70-day Bartlett window. |
| `docs/codebase/backend/07-credit-scoring.md` | `AdvancedCreditScorer`, live ₹/ha limit bands | That class was deleted. `risk_index_engine.py` has no limit logic and hardcodes `no_repayment_calibration: True`. |
| `mongodb_helper.py:20-62` docstring | Documents `recommended_credit_limit`, `extreme_events[]`, `location.field_area_ha`, `conditions`, `warnings`, `errors` | `AssessmentSchema.build()` writes **none** of them. Two analytics aggregations average fields that are never written. |

**Action:** Phase 0 includes correcting these four documents. A plan built on them would be built on sand.

### 1.3 What is genuinely good and should be preserved

Not everything needs rework. Worth protecting:

- **`risk_index_engine.py` is clean.** Deterministic, explainable, sub-indices carry `inputs` and `drivers`, reason codes are structured, and the `calibration` block reserves the right hooks for a future PD model. The `no_repayment_calibration: True` positioning is the correct call for an unlicensed agronomic index.
- **The tri-state benefits handling is correct** end-to-end in the engine (`_benefits_bonus`, `risk_index_engine.py:361-394`) — unknown never penalises. (It is undone at the persistence layer; see D-32.)
- **`peer_benchmark.py` is a correct percentile engine** with an honest cold-start fallback. It is starved of data, not broken.
- **`counterfactual_engine._generate_scenarios_v5`** computes index deltas that are dimensionally correct against the scoring formula (`counterfactual_engine.py:142`) — unlike the SHAP attribution.
- **`CropGrowthCurves`** (config.py:662–1813) is well-constructed agronomic reference data — 23 crops with ICAR durations, NDVI curves, yield parameters, critical stages. It is simply not wired to anything that executes.
- **The 10-day continuous grid architecture** is the right shape. The problems are in what is computed on it, not the grid itself.

---

## 2. Current pipeline — verified trace

```
main.py:468  assess_farmer()
  │
  ├─ S0  shell + geo QA ................ main.py:558-696
  │      geometry_utils.validate → area-ratio check only (0.5×–2.0×)
  │      ⚠ polygon rejection is NON-FATAL — falls back to a point buffer (satellite_collector.py:245-264)
  │
  ├─ S1  satellite pull ................ satellite_collector.py
  │      GEE (default) or STAC fallback; 10-day bins, single lowest-cloud scene per bin
  │      ⚠ NO compositing. ⚠ STAC path has NO per-pixel cloud mask at all (:1554-1598)
  │      ⚠ only 2 hard aborts exist: no scenes, or n_valid < 12 (main.py:690-696)
  │      ╳ NO LAND-COVER CHECK ANYWHERE
  │
  ├─ S2  signal construction ........... satellite_collector.py:790-896
  │      ⚠ per-parcel min-max normalisation (:812) → absolute vigor destroyed
  │      ⚠ kNDVI backbone = tanh(NDVI²) (:1010) → sign of NDVI destroyed
  │      ⚠ Whittaker computed (:852) → stored as vs_smooth → never read by anyone
  │
  ├─ S3  cycle detection ............... crop_cycle_detector.py:274
  │      70-day Bartlett smooth; local-max peaks (no prominence); walk-back/walk-forward
  │      ⚠ low_cvi (0.30) > peak_cvi (0.28) — threshold inversion
  │      ⚠ perennials structurally rejected → scored as abandoned land
  │      ⚠ _fill_long fabricates peaks inside cloud gaps, unflagged (:1014-1022)
  │
  ├─ S4  crop context .................. crop_detector.py  [OFF BY DEFAULT]
  │      fallback: main._build_unclassified_analysis, crop_confidence hardcoded 0.0
  │      ⚠ that 0.0 vs a >=0.25 gate means a registry crop hint can NEVER unlock crop-specific scoring
  │
  ├─ S5  performance ................... performance_analyzer.py  → always PATH B (crop-agnostic)
  ├─ S6  weather ....................... weather_analyzer.py  (cycle-aligned — this part is correct)
  │      ⚠ per-stage weather is dead: crop is None → empty critical-stage list → 30% term contributes 0
  │
  ├─ S7  scoring ....................... risk_index_engine.py  ← clean, keep
  ├─ S8  AI enrichment ................. ai_integration/*
  │      ⚠ SHAP is a non-additive heuristic reporting shap_available: True
  │      ⚠ peer language with no peer data
  │
  └─ S9  persist ....................... mongodb_helper.py
         ⚠ TWO incompatible schemas in credit_assessments
         ⚠ FAILED runs never persisted (save is inside the try, after status='SUCCESS')
```

---

## 3. P0 — Land classification

**This is the whole ballgame. Everything else in this document is secondary.**

### D-01 · P0 · No land-cover gate exists

No water mask, no built-up mask, no bare-soil index, no LULC raster lookup, no absolute-NDVI floor, no seasonal-amplitude floor in raw units, no minimum-area check. The pipeline accepts any valid polygon of plausible size drawn anywhere on Earth.

**Traced outcomes for non-farmland:**

| Input | What happens | Final output |
|---|---|---|
| **Water body** | SCL class 6 (WATER) is **not** in the mask list (`satellite_collector.py:975` excludes only 3/8/9/10). NDVI −0.4…−0.1 is finite → every bin counts as "valid". NDWI +0.3…+0.7 screams water and is read by nobody. Min-max normalisation turns turbidity noise into a full-amplitude series. | Cycles detected. `SUCCESS`. Scored. |
| **Built-up / concrete** | NDBI never computed (B11 is downloaded and used only for NDMI). NDVI 0.0–0.15 flat → normalised to span [0,1] → thermal/shadow noise becomes cycles. | Cycles detected. `SUCCESS`. Scored. |
| **Barren / rocky** | A real flatness gate exists (`crop_detector.py:610-613`, `MIN_NDVI_CV_FOR_CROP = 0.08`) but is **unreachable**: it only runs when classification is on (default off), it is called with `cycle_based=True` which drops the upper-CV gate, its `crop_detected: False` result never aborts, and it tests **raw NDVI** while detection ran on normalised VS. | Scored. |
| **Dense forest** | NDVI 0.7–0.9 → `peak_cvi` saturates → `_sub_vigor` awards `_clip(mean(peaks)/0.75*100)` = **100**. No upper bound, no "vegetation too persistent to be a crop" test, no bare-soil-return requirement. | **Scores better than a real fallow farm.** |
| **Zero cycles detected** | `landuse` = 15.0, `vigor` = 50.0 (default), `stability` = **84.0** (no anomalies → "stable"), `weather` ≈ 60. Gate ≈ 0.85 because the sky was cloud-free. | **index ≈ 41 · HIGH · SUCCESS** |

Note the perverse incentive in that last row: **`stability` rewards the absence of anomalies, so dead land scores 84/100 on that pillar.**

### D-02 · P0 · Fallow penalty is inverted for dead land

`main.py:907` guards the fallow-fraction copy with `if utilization_metrics`, and `utilization_metrics` is only computed when `crop_cycles` is non-empty (`main.py:778-791`). So a plot with **zero** detected cycles gets `fallow_fraction` defaulting to `0.0` → **zero fallow penalty**. A real farm with two cycles and long gaps *is* penalised.

Even when it fires, the penalty is capped at 40, weighted 0.15 within a 30%-weight sub-index → **maximum 1.8 points off a 100-point index.**

### D-03 · P1 · Existing guardrails and exactly what they let through

| Guard | Location | Catches | Lets through |
|---|---|---|---|
| Geometry QA | `geometry_utils.py:107-177` | empty/invalid geometry, area ratio outside 0.5×–2.0× | any valid polygon anywhere; **and rejection is non-fatal** (`satellite_collector.py:245-264` falls back to a point buffer) |
| Cloud caps | `config.py:93-95`, applied `:1077`/`:1359` | clouds | all land-cover semantics — SCL 4/5/6 (vegetation/bare/water) are never differentiated |
| Valid-pixel ratio | `config.py:141`, `satellite_collector.py:1539` | STAC nodata only | GEE path has no pixel-count check at all (`bestEffort=True`) |
| `bin_quality` | `satellite_collector.py:829-839` | — | **fed a fake input**: the parameter named `valid_pixel_fraction` receives a binary is-the-mean-finite flag. 3-of-400 valid pixels scores identically to a clear scene. This is the load-bearing input to the confidence gate. |
| Min observations | `main.py:690-696` | total imagery failure | a cloud-free desert scores *best* on observation count |
| Confidence gate | `risk_index_engine.py:306-335` | thin data | floored at 0.60 — **garbage retains 60% of its raw index**. Measures density, never plausibility. |
| Crop-pattern gates | `crop_detector.py:576-619` | would catch barren | **dormant** (see D-01) |

### D-04 · P1 · Dead config keys that were never read

`BIN_MIN_VALID_PIXEL_RATIO` (`config.py:220`) is declared and never referenced. `SAR_FUSION_QUALITY_FLOOR` (`config.py:228`) never read. `CROP_CYCLE_FALLBACK_PROMINENCE_FLOOR` (`config.py:319`) never read — which is why peak detection has no prominence criterion at all.

### 3.1 What we already have to build the gate with — **no new data acquisition required**

Every index below is already computed, already transferred, already persisted in `continuous_data`:

| Available now | Key | Value for land cover |
|---|---|---|
| NDWI | `NDWI_mean`, `ndwi_values[]` | **water discriminator — computed, stored, read by zero consumers.** Highest-leverage free win in the codebase. |
| NDVI + std + p90 | `NDVI_mean`, `NDVI_std`, `NDVI_p90` | absolute vigor floor, flatness, intra-parcel heterogeneity |
| NIRv | `NIRv_mean` | ≈0 for both water and concrete |
| LSWI | `LSWI_mean` | permanent-water / flood signature |
| MSAVI2 | `MSAVI2_mean` | soil-adjusted → barren |
| PSRI | `PSRI_mean` | senescence — a real crop must senesce |
| SAR RVI | `rvi_values[]` | GEE path only |
| Signal quality | `signal_quality_summary` | valid fraction, source counts |

**Three indices to add — one line each, from bands already downloaded:**
- **NDBI** = `(B11 − B08)/(B11 + B08)` — built-up. B11 already fetched (`:1564`), used only for NDMI.
- **BSI** = `((B11+B04) − (B08+B02)) / ((B11+B04) + (B08+B02))` — bare soil. All four bands present.
- **MNDWI** = `(B03 − B11)/(B03 + B11)` — open water, better than NDWI. Both bands present.

**One retention fix worth more than all three:** SAR VV/VH backscatter in dB is computed at `satellite_collector.py:683-685` and **only RVI is retained** — the raw VV/VH means are discarded. VV/VH is the single best discriminator for built-up (very high VV, double-bounce) and open water (very low, specular). Retaining them is a ~3-line change at `:686-693` and makes the water/concrete cases near-unbeatable.

**Also discarded for free:** GEE's reducer already computes `stdDev` and `p90` for **every** index (`satellite_collector.py:1017-1021`). They are billed, transferred over the wire, and thrown away at read-out (`:1147-1164`), which keeps only `*_mean` plus NDVI's std/p90. `EVI_stdDev`, `NDMI_p90`, `NIRv_stdDev`, `LSWI_p90` all arrive and are dropped.

### 3.2 Insertion point

**Primary hook: `main.py`, between lines 696 and 739** — post-acquisition, pre-cycle-detection. Zero new network calls, and it sits immediately after the two existing `raise ValueError` aborts so it can reuse the identical failure mechanism.

Variables confirmed live at that exact point: `continuous_data` (`:687`), `scenes_list`/`n_valid` (`:688-689`), `clat`/`clon` (`:671-672`), `eco_key`/`eco_profile` (`:674-678`), `assessment['field_area_ha']` and both geometry/registered variants (`:648-669`), `assessment['geospatial_prep']['geometry_qa']` (`:622-633`), `indices_available`/`indices_sparse` (`:638-643`).

**Secondary hook (only if we adopt raster LULC): `satellite_collector.py:285`** — the only place with a live Earth Engine session. `linkCollection` is already used at `:1088`, so the pattern for joining an auxiliary collection is established. Caveat: this path is skipped entirely on a satellite cache hit (`main.py:594-598`), so the verdict must be cached too — top-level keys **do** survive caching (`_slim_satellite_for_cache` does `out = dict(satellite_data)`, `main.py:383`), per-scene fields do not.

### 3.3 Rejection propagation — a channel already exists

`job_runner.py:272` collapses **everything** that is not literally `"SUCCESS"` into `FAILED`. So today, "we refuse to score non-farmland" and "Earth Engine timed out" are indistinguishable to the caller.

But `multi_farm_assessor.py` already models the right semantics. `counters_from_farm_assessments` (`:67-89`) buckets by reason **prefix**: `"error:"` → `n_plots_failed`, any other reason → `n_plots_skipped`. Precedents at `:153` (`"excluded_or_no_tenure"`), `:157` (`"over_plot_cap"`), `:161` (`"no_geometry"`). Adding `"not_cropland"` counts as *skipped*, not *failed* — exactly the semantics we want.

**Design decision required (§7, D-1):** hard reject vs. score-with-flag. My recommendation is a **third status**, not a boolean: `SUCCESS` / `REJECTED_NOT_AGRICULTURAL` / `FAILED`, with the rejection carrying the full evidence block so the UI can explain *why* rather than showing a generic failure.

---

## 4. P1 — Analysis quality

### 4.1 Signal construction

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| **D-10** | **P0** | Detection signal is min–max normalised per parcel over its own 3-year series, then absolute thresholds are applied. Every parcel spans ~0→1 regardless of raw dynamic range. Barren land manufactures cycles; a uniform perennial gets artificial seasonality; one outlier bin rescales the whole trajectory. `peak_cvi` is stored and reported as if it were an index but is not comparable across farms. | `satellite_collector.py:812`, `data_processing.py:283-293` |
| **D-11** | **P0** | kNDVI backbone = `tanh(NDVI²)` destroys the sign of NDVI. Water (−0.30) and sparse crop (+0.30) → identical 0.0876. Weighted 0.50 in the composite. | `satellite_collector.py:1010`, `config.py:205` |
| **D-12** | **P1** | Whittaker smoother is computed, stored as `vs_smooth`, and **read by nothing**. `VS_mean` on each scene is written from the *unsmoothed* fused array. Actual smoothing is a 70-day Bartlett window applied downstream — which erases any 60–90 day crop (Bajra 85d, Cabbage 80d) before detection starts. | `satellite_collector.py:852-859` vs `:867-868`; `crop_cycle_detector.py:1043-1064` |
| **D-13** | **P1** | `bin_quality` receives a binary finite-flag where it expects a valid-pixel *fraction*. Because `q` is therefore never ≈1.0 for a smallholder parcel, the pure-optical branch is effectively dead and **even a 2%-cloud scene takes ~17% of its value from scaled radar**. | `satellite_collector.py:829-839`, `data_processing.py:726-747` |
| **D-14** | **P1** | STAC path fetches no SCL and no QA60 — **there is no per-pixel cloud mask at all** — yet the output is stamped `'cloud_mask_version': 'scl_qa60_v1'`. Also, year-split segments put the midpoint at ~2 July, so the Kharif cloud cap of 80 is applied to Rabi too. | `satellite_collector.py:1554-1598`, `:437`, `:1246-1250` |
| **D-15** | **P1** | SAR fusion is an OLS fit of RVI→VS trained **only on paired bins — which are by construction the clear dry-season ones** — then extrapolated onto monsoon RVI well outside its training domain. Ascending passes only (~half discarded), no speckle filter, no terrain flattening. STAC path has no SAR at all and degrades silently. | `data_processing.py:705-723`, `satellite_collector.py:678-679`, `:467` |
| **D-16** | **P2** | SAR-recovered bins keep `missing: True`, so they don't count toward the `n_valid < 12` gate — a monsoon-heavy field can be hard-rejected even though its composite was recoverable. | `main.py:689-696` |
| **D-17** | **P2** | No compositing. Each bin is the AOI spatial mean of a **single** lowest-cloud acquisition, carrying all its haze and BRDF. The emitted `date` is the bin start, not the acquisition date — ±9 days of date error on every point. GEE additionally caps at `.limit(50)` scenes/year against ~70–140 real acquisitions. | `:1470-1476`, `:937-944`, `:1298`, `:1078` |

### 4.2 Crop cycles and seasons

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| **D-20** | **P1** | **Threshold inversion.** `low_cvi = 0.30` > `peak_cvi = 0.28` — the "bare soil / harvest" floor sits *above* the "this is a peak" floor. Drives D-21 and D-22. | `config.py:282-283` |
| **D-21** | **P1** | `low_cvi = 0.30` is used as the *bare-soil* crossing, but on the fallback CVI scale bare soil is ≈0.11 and 0.30 corresponds to NDVI ≈ 0.40 — mid-vegetative. So the walked window is "time spent above 40% canopy", not sowing→harvest. **Every `duration_days`, AUC, and land-utilization figure is biased short.** | `crop_cycle_detector.py:664`, `:745` |
| **D-22** | **P0** | **Perennials are structurally rejected.** If the signal never troughs below `low_cvi`, `default_sow = peak − 19 bins` and harvest ≥ `peak + 4 bins` → 230 days > the 195-day cap → unconditional rejection. A productive banana/orchard plot returns **zero cycles** → `crop_intensity = 0`, `cropping_pattern = 'NO_DATA'`, `fallow_fraction = 1.0` → **scored as abandoned land.** The relaxation passes cannot rescue it: they relax `peak_cvi` and `min_rise`, never `low_cvi` — the constraint that actually failed. | `:743`, `:687`, `:480`, `:491`, `land_utilization_analyzer.py:48-50` |
| **D-23** | **P1** | `_fill_long` **synthesises a hat-shaped peak up to 0.82 inside cloud gaps**, and the peak finder then detects it as a real cycle with a `peak_cvi` and a `confidence` computed from imputed values. **There is no flag marking a cycle whose peak was fabricated rather than observed.** | `:1014-1022`, `:619-624` |
| **D-24** | **P1** | Cloud-gap attribution requires the gap to be fully *inside* the cycle. A monsoon blackout that **straddles the sowing date** — the most common case in India — is excluded, so `has_cloud_gap=False` on exactly the cycles that most need flagging. | `:692-694` |
| **D-25** | **P1** | **Four inconsistent season definitions**, and the two functions that run disagree for Feb–Mar (`_season_label` says Rabi, `_assign_season_type` says Zaid). `'cross_season'` is unreachable dead code — the month branches cover all 12. Harvest date is accepted and ignored: a crop sown Sept and harvested Feb is 100% "kharif". No region adjustment — Punjab wheat and Tamil Nadu samba paddy use the same North-India month map, despite latitude, LGD code, and eco-region all being in scope. | `:1334-1341`, `:1343-1354`; `config.py:59-83`, `:86-90`, `:476-479` |
| **D-26** | **P1** | **No per-season output exists.** `LandUtilizationAnalyzer` (all 317 lines) never reads `season_type` or `season_label`. `RiskIndexEngine` consumes no season field. `INTENSITY_WEIGHTS` has no Zaid slot. `SEASONS` and `CROSS_SEASON_NDVI_CONTINUITY` are read by **zero** files outside config. The architecture doc lists "3 seasons, trend/fallow features" as planned — never implemented. | grep-verified; `BACKEND-ARCHITECTURE-v5.md:320` |
| **D-27** | **P2** | No prominence criterion. Peak detection is a bare local-max with a 0.005 tolerance — two 5-day noise wiggles 45 days apart both qualify. Relaxation is asymmetric: extra passes fire only when *too few* cycles are found; there is no consolidation/merge pass and no upper bound. `CROP_CYCLE_MERGE_*` keys exist and are never read. | `:619-624`, `:476-494`, `config.py:297-298`, `:319` |
| **D-28** | **P2** | One long cycle can swallow the next. When the harvest walk fails both triggers, `argmin` can land mid-rise of the *next* crop, pushing `used_up_to` forward and discarding the following peak. Systematically merges cycles 2 and 3 on irrigated triple-crop rotations. | `:674-677`, `:722`, `:641` |
| **D-29** | **P2** | **Green-up rate `m_s` and senescence rate `m_a` are fitted by the double-logistic and then thrown away** — two lines to expose. Also missing: LOS as a named metric, baseline-subtracted (small) AUC, peak width, 50% SOS/EOS, EOS→next-SOS fallow. The existing AUC is computed over the *walked* window on raw NDVI, not the phenological SOS→EOS window, and is not baseline-subtracted. | `:1133`, `:1159-1162`, `:713-714` |
| **D-30** | **P2** | `confidence` is not a confidence. `shape_score = peak_cvi×25 + std×50` is a rescaled duplicate of the peak and variation terms, so the score is effectively `2×peak + 2×variation + duration` — **with no goodness-of-fit term, despite R² being computed on the adjacent line.** | `:1291-1321`, `:1132` |
| **D-31** | **P2** | The double-logistic fit is usually computed and then discarded: adoption requires `0.6 ≤ fit_duration/walked_duration ≤ 1.4`, but per D-21 the walked duration is systematically *short*, so an honest fit frequently exceeds 1.4× and is rejected in favour of the biased dates. **The gate protects the wrong reference.** | `:1222` |
| **D-32** | **P3** | Intercropping, mixed cropping and staggered sowing have no handling. The parcel is collapsed to one AOI spatial mean per bin; there is no sub-field segmentation, no bimodality test, no pixel-level clustering anywhere. | `:1612`, `:1016-1026` |
| **D-33** | **P3** | `crop_type` returns vigor buckets (`SHORT_HIGH_VIGOR`/`MEDIUM_MODERATE`/`LONG_DURATION`), and `_analyze_crop_diversity` computes a **Shannon diversity index over those buckets** and surfaces it to lenders as `crop_diversity`. It measures duration spread, not crop diversity. | `:1323-1332`, `land_utilization_analyzer.py:250-278` |
| **D-34** | **P2** | `land_utilization_index` uses a union-of-days set (overlap-safe) while `fallow_fraction` uses `sum(duration_days)` (double-counts overlaps). They do not sum to 1, and `fallow_fraction` feeds the risk index at 15% weight. | `land_utilization_analyzer.py:111-142` vs `:238-241` |
| **D-35** | **P3** | 21 cycle-config keys have zero readers; 2 more (`CROP_CYCLE_MIN_CVI_RISE`, `CROP_CYCLE_PEAK_WIN_DAYS`) are **read but never defined**, silently falling back to hardcoded values. The config documents a far more sophisticated detector than the one that exists — actively dangerous for anyone tuning it. | grep-verified against `config.py:275-349` |

### 4.3 Crop classification

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| **D-40** | **P1** | **Train/serve skew — the classifier sees only the first half of every cycle.** The artifact expects 24 features = **8** time steps × 3 indices. The code builds a **15**-point grid and then does `[feature_dict.get(fn, 0.0) for fn in self.feature_names]` — a name lookup that silently selects grid positions t01–t08, i.e. normalized time **0.0→0.5**. Peak, senescence and harvest — the most discriminative part of a phenology curve — are computed and discarded. Dimensionally valid, never errors, never warns. **Any accuracy claim from the artifact's metrics is void at inference.** | `config.py:268` vs artifact `feature_names`; `crop_detector.py:710` |
| **D-41** | **P1** | **A registry crop hint can never unlock crop-specific scoring.** `_build_unclassified_analysis` hardcodes `crop_confidence: 0.0` against a `>= 0.25` gate, so `is_crop_reliable` is always False. The docstring explicitly claims the opposite. Consequence: `performance_analyzer` always takes crop-agnostic PATH B, so `EXPECTED_NDVI_CURVES`, `CANOPY_TYPE`, `CRITICAL_STAGES` and `EXPECTED_CUMULATIVE_NDVI` never execute. | `main.py:312`, `:266-267`; `performance_analyzer.py:143-153` |
| **D-42** | **P2** | Model metadata **is embedded in the joblib and never read**: `trained_date: 2026-02-16`, `training_config.gpkg_path: /content/crops_classification_final_clean.gpkg` (a Colab path), `total_farms: 1129`, `total_samples: 25981` at 25/farm, `test_accuracy: 0.958`, plus per-class metrics. No training script exists in the repo. **Caveat: 25 near-duplicate samples per farm means that 95.8% is leakage-inflated unless the split was grouped by farm — unverifiable without the notebook. Do not quote it to lenders.** | artifact pickle; `crop_detector.py:70-74` |
| **D-43** | **P2** | No rejection threshold — a sub-0.25 prediction gets an annotation string but the crop name is still written and counted. Silent zero-fill (`get(fn, 0.0)` + `nan_to_num`) means missing EVI/NDMI yields 8 zeroed features and a confident prediction. Confidence "calibration" clamps to 1.0 whenever `top2_gap ≥ 0.05`, so it is a no-op for all but near-ties. | `:431-432`, `:710-711`, `:720` |
| **D-44** | **P2** | `Others` is a real trained class that passes `is_crop_reliable`, routing a cycle down crop-specific scoring with a meaningless benchmark. | `crop_detector.py`; `performance_analyzer.py:143-153` |
| **D-45** | **P2** | 51 MB model deserialized **per assessment** — `CropDetector` is constructed inside `assess_farmer`, not at pipeline init. | `main.py:707` |
| **D-46** | **P3** | One national RandomForest. A single `Rice` decision boundary from Punjab to Tamil Nadu. `sklearn==1.6.1` pinning happens to match the artifact but nothing asserts it at load. | `crop_detector.py:77-83`; `requirements.txt:29` |
| **D-47** | **P2** | ~1150 of config.py's 1815 lines are dead in the default run. **`YIELD_PARAMETERS` (210 lines of ICAR yields and MSP prices) has zero readers repo-wide.** `HIGH_VALUE_CROPS`/`HIGH_VALUE_MULTIPLIER`, `ML_MODE`, `crop_family_band`, `CropDetector.analyze_cropping_pattern` — all computed or configured, none consumed. | grep-verified |

### 4.4 Weather

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| **D-50** | **P1** | **Per-stage weather is entirely dead in the default config.** `_get_critical_stage_fracs_v4` bails when crop is `None` (the default), returning an empty critical-fracs list → `crop_stage_critical` is False for **every** event → the 30%-weighted critical-stage term contributes exactly zero and `critical_stage_events` is always 0. | `weather_analyzer.py:1712-1713`, `:1537-1543` |
| **D-51** | **P2** | No crop-name normalisation at the stage lookup — `'paddy'`, `'RICE'`, or trailing whitespace silently disables stage detection with no log line, while the adjacent duration lookup *does* normalise. | `:1715-1717` vs `config.py:1700` |
| **D-52** | **P2** | Monsoon onset uses one pan-India constant (`MONSOON_NORMAL_ONSET_DOY = 160`) — no state, district or latitude-band normal. `self.region` is hardcoded `'DEFAULT'` and never reassigned, yet is reported out. | `config.py:406`, `weather_analyzer.py:121` |
| **D-53** | **P2** | Percentile baselines are computed from **the cycle's own 90–180 days**, not a multi-year climatology — a uniformly hot season raises its own bar. | `:1072-1169` |
| **D-54** | **P2** | ~190 lines of dead code: `analyze_seasonal_weather`, `_detect_extreme_events`, `SEASONAL_RAINFALL_NORMS` have no caller anywhere. | `:142-248`, `:1381-1480`, `:88-91` |
| **D-55** | **P3** | Two inconsistent dry-day definitions coexist: 2.5 mm for spells vs a dynamic 0.25–6.0 mm for drought events. Top-level `thresholds_used` reports static defaults even when the mode is `dynamic_percentile`. `weather_indicators_present` is hardcoded `True` even when status is `unavailable`. | `:920`, `:1148-1157`, `:436`, `:431` |

### 4.5 Explainability and narrative

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| **D-60** | **P0** | **Peer language with no peer data.** `upsert_cohort_stat` has **no caller anywhere in the repo**, so `cohort_stats` is empty and `PeerBenchmark` always cold-starts to `internal_cvi_auc`. Meanwhile the Groq system prompt states vigor is "peer-relative NIRv-based potential", the counterfactual copy says "raise peer-relative yield-potential", and reason code `VIGOR_STRONG` says "above peers." That 25%-weighted sub-index is a **self-calibrated absolute score**. This is a representation problem, not just a bug. | `mongodb_helper.py:1132`; `groq_report_generator.py:341`; `counterfactual_engine.py:110-112`; `risk_index_engine.py:445` |
| **D-61** | **P1** | `_rule_based_shap` reports `shap_available: True` while computing a hand-rolled heuristic, and its `/10.0` scaling makes contributions **~10× too small to reconcile with `base_value: 50.0`** — a directional ranking device labelled as an additive decomposition. Real SHAP exists in the file but is unreachable (`model=None` is hardcoded at the only construction site). `force_plot_data`, promised by the docstring, never appears in production output. | `shap_explainer.py:264-267`, `:318`, `:84`; `enrichment.py:172` |
| **D-62** | **P1** | **LLM narratives are never persisted.** `english_narrative`, `translated_narrative`, `translation_language`, and `model_snapshot` (model id + prompt hash) exist only in the live HTTP response — `_build_ai_enrichment` doesn't copy them. Text shown to a loan officer is unreproducible and unauditable after the request ends, and the prompt hash captured for exactly that purpose is discarded with it. | `mongodb_helper.py:497-522`; `enrichment.py:117-120` |
| **D-63** | **P2** | `GROQ_DRY_RUN=1` output — the literal `"[DRY RUN — API not called]\n\nPROMPT:{...}"` blob — is stored as a genuine narrative with `groq_used: True`. Indistinguishable downstream from a real report. | `enrichment.py:114-116` |
| **D-64** | **P2** | `.env.example:37` and `config.py:551` default `GROQ_MODEL` to `llama-3.1-70b-versatile`, a **retired** model. The module default (`llama-3.3-70b-versatile`) is correct, but an operator who copies `.env.example` verbatim gets 404s into the fallback on every call. Worst-case blocking time is ~95 s synchronous (3 attempts × 30 s + backoff) inside the assessment. | `groq_report_generator.py:29`, `:70`, `:125-146` |
| **D-65** | **P2** | Sarvam per-chunk failure returns the **original English chunk**, yielding a silently mixed-language document with no flag. No retry logic. | `sarvam_translator.py:138-140` |
| **D-66** | **P2** | The rich counterfactual content — descriptions, multi-item `actions[]`, `change_needed`, `timeframe`, PM-KISAN/PMFBY/advisory guidance — lives entirely in `_generate_scenarios` (243 lines), which is **unreachable and would `KeyError` if reached** (`_WEIGHTS['cropping_intensity']` no longer exists post-v5). The live v5 path carries none of it, so every roadmap step has `"timeframe": ""`. | `counterfactual_engine.py:160-402`, `:198`, `:417` |

---

## 5. P1 — Persistence and data model

### 5.1 The structural problem

**The richest artefact in the database is `jobs.result`.** `slim_assessment_for_api` pops only `satellite_data`; everything else — full `season_results[].scenes[]` with all 13 indices per bin, `performance_analysis` with `yield_detail`/`health_detail`/`anomaly_events`, `weather_analysis` with `weather_indicators`, `crop_cycles` with phenology, `cycle_detection_diag`, `geospatial_prep`, full `ai_enrichment` — is written to `jobs.result`.

That is accidental. `jobs` is a queue collection with zero indexes, no TTL, no schema, no versioning — and the frontend already treats it as a data fallback.

**So the fix is not "start storing indices." It is: stop discarding what `jobs.result` already holds, and give it a real home.**

### 5.2 Two incompatible schemas in one collection

| | Single-farm | Multi-farm |
|---|---|---|
| Writer | `AssessmentSchema.build()` (`mongodb_helper.py:190-312`) | `dict(farmer_result)` inserted **raw** (`:1195-1201`) |
| `credit_score` | ✅ | ❌ absent |
| `risk_category` | ✅ | ❌ absent at top level |
| `assessment_date` | `Date` | **ISO string** |
| `location` | ✅ | ❌ |
| `seasonal_ndvi` | ✅ | ❌ |
| Scene arrays | stripped | **full per-bin scene dicts pass through** (the downsampling code at `multi_farm_assessor.py:403-407` is dead — it strips a key that never exists) |

Consequence: every analytics aggregation (`mongodb_helper.py:973-1104`) and the frontend `$group` silently mis-handles every multi-plot farmer. `get_farmer_score_trend` returns empty or garbage for them. `get_portfolio_statistics` buckets them all under `_id: null`.

### 5.3 What dies where

| Data | Verdict | Where it dies |
|---|---|---|
| Per-pixel band arrays, all per-pixel index rasters | **DEAD** | collapsed to `float(np.nanmean(arr))` on the line they're created |
| **Valid-pixel counts** | **DEAD** | used only as a `> 0` gate. *There is no sample size behind any NDVI mean anywhere in the output* — the single most damaging omission for a defensible report. |
| GEE `stdDev`/`p90` for every index | **DEAD** | computed, billed, transferred, dropped at read-out |
| Pre-fusion optical composite `vs_optical` | **DEAD** | fusion is unauditable |
| Full per-bin index series (13 indices × ~110 bins) | **CACHE-ONLY, 30-day TTL** | `satellite_stats_cache`, opaque SHA-256 key, no queryable `farmer_id` |
| Green-up `m_s` / senescence `m_a` | **DEAD** | fitted at `:1133`, never exported |
| Full `crop_cycles` incl. phenology | **JOB-ONLY** | `AssessmentSchema` never reads it |
| `yield_detail` (21 keys), `health_detail` (17 keys) | **JOB-ONLY** | only `peak_cvi` leaks, as a mean |
| **`anomaly_events`** (typed, dated, staged, z-scored) | **JOB-ONLY** | collapsed to three integer counts. *"Your crop was water-stressed on 2023-08-14 during FLOWERING" — the most explainable content the pipeline produces — is discarded.* |
| Full NASA POWER daily series | **CACHE-ONLY, 30-day TTL** | then gone permanently |
| `weather_indicators` (GDD, PET, water balance, spells, SPI/SPEI) | **JOB-ONLY** | `_build_weather_intervals` copies 12 keys and drops every agronomic indicator |
| `cycle_detection_diag` | **JOB-ONLY** | the entire "why did we find N cycles" audit trail |
| LLM narrative + model snapshot | **NEVER PERSISTED** | see D-62 |
| `warnings[]`, `errors[]`, traceback | **NEVER PERSISTED** | no mapping in `AssessmentSchema` |

### 5.4 Bug register

| ID | Bug | Location |
|---|---|---|
| **B1** | `interval_indices` reads `SAVI_mean`/`GCI_mean`; the collector emits `MSAVI2_mean`/`GCVI_mean` → **2 of 5 stored indices are permanently null** | `mongodb_helper.py:441-442` |
| **B2** | `kharif_avg_rainfall_mm`/`rabi_avg_rainfall_mm` always null — season labels are `cycle_N` and never contain "kharif"/"rabi" | `:365-372` vs `main.py:305` |
| **B3** | `avg_ndvi`, `ndvi_rise`, `arc_score`, `frac_above_thresh`, `confidence`, `is_cross_season` always null in production (classification off) | `:336-346` |
| **B4** | `get_portfolio_statistics` averages `$recommended_credit_limit` and `$location.field_area_ha` — neither is ever written | `:988-989` |
| **B5** | `pipeline_version` hardcoded `"3.0"`; the pipeline is `5.0` | `:96` vs `main.py:95` |
| **B6** | `clear_satellite_cache.py` deletes on root `farmer_id`; the field lives at `metadata.farmer_id` → always matches 0 docs | `scripts/devtools/clear_satellite_cache.py:27` |
| **B7** | Multi-farm scene downsampling is dead code → unbounded document size | `multi_farm_assessor.py:403-407` |
| **B8** | `upsert_cohort_stat` has no callers → peer benchmarking permanently cold | `mongodb_helper.py:1132` |
| **B9** | `_clean_obj` strips `null` → **tri-state "unknown" collapses to "key absent"**, undoing the engine's correct tri-state handling at the storage layer | `:113-142`, `:289-299` |
| **B10** | Multi-farm docs break every analytics query and the score-trend endpoint | `:1195-1201` |
| **B11** | **FAILED runs are never persisted** — the save call sits inside the `try`, after `status='SUCCESS'` | `main.py:990-1021` |
| **B12** | `save_assessment` swallows every exception and returns `None`; callers ignore it → **a failed write is invisible** | `mongodb_helper.py:861-863` |
| **B13** | `weather_power_cache` key omits the parameter list and source → a config change silently serves stale-schema blobs | `weather_analyzer.py:698-706` |
| **B17** | `_infer_crop_family_band` reads `peak_ndvi`/`peak_cvi`/`duration_days` off entries that never carry them | `performance_analyzer.py:379-393` |
| **B18** | The crop-specific path never runs anomaly detection → `anomaly_events` always `[]` and the narrative always claims "no significant anomalies" | `:208-213`, `:1041-1044` |
| **B20** | `_missing_scene_placeholder` emits 8 of the 13 index keys real scenes carry, while the docstring claims alignment | `satellite_collector.py:1421-1433` |

### 5.5 Indexes and scale

**`jobs` has no indexes at all.** It is queried by `{"status":"QUEUED"}` sorted by `created_at` — **a full collection scan plus in-memory sort, 30 times per minute, forever** (`worker.py:96-107`). Because `jobs.result` embeds a multi-MB payload, that scan drags every result blob through memory. Job documents are never pruned.

Also missing: `credit_assessments` has no `status` index (the frontend's 3-key sort is uncovered); `feature_store`, `cohort_stats`, `index_versions` have none; `satellite_stats_cache.metadata.farmer_id` is unindexed; `farm_info` has no index on `created_by`/`user_id`/`created_at`. Three analytics aggregations `$unwind` the **entire collection with no `$match`** and no result cap.

### 5.6 No schema, no validation, no serializer

There is no Mongo serialization layer — documents go to `insert_one` as raw Python dicts. Only two Pydantic models exist, both request bodies; both endpoints return bare `Dict[str, Any]` with no response models.

- **numpy** is avoided by convention (`float(...)` wrapping), not by construction. A single unwrapped `np.float64` in the raw multi-farm insert path raises `InvalidDocument` — and per B12 the assessment is then **silently lost with only a log line**.
- **NaN/Inf are not handled anywhere.** `round(float('nan'), 4)` is `nan`, which lands in Mongo as BSON NaN, breaking `$avg`/`$sum` and strict JSON round-trips.
- **datetimes are inconsistent**: naive `utcnow()` in one write path, aware `now(timezone.utc)` in the other, ISO strings for `risk_assessment.assessment_date` and every cycle date — so no date range query is possible on cycles, and multi-farm docs cannot be sorted against single-farm docs.
- `_f(v, d=4)` returns `None` on error and `_clean_obj` then **deletes the key** — a malformed value is indistinguishable from an uncomputed one.

### 5.7 Relational model

`farmer_id` (string) is the only real join key. `farm_id` exists only inside the `farm_info.farms[]` subdocument array — there is no `farms` collection. **`plot_key` is runtime-only** and never persisted back, so plot keys are **not stable across runs** if ingest reorders the array.

**Per-plot assessments are never written to `credit_assessments`** — they exist only as slim rows inside the aggregate, with sub-index `inputs`/`drivers` flattened to bare scalars. You cannot query a plot's score history, and you cannot index or aggregate per-plot.

The single-farm path uses only the **envelope** (largest owned plot) and silently ignores every other plot.

---

## 6. The build plan

### 6.0 Operating constraints

Two constraints govern everything below. They are not temporary annoyances to work around — they define what is allowed to ship.

**Constraint A — no local ground truth exists.** We have no field-verified crop labels, no observed sowing/harvest dates, no measured yields, and no repayment outcomes. Therefore we cannot train a classifier, calibrate a yield model *to local units*, tune a threshold against observed reality, or state an accuracy figure of our own. Anything that *requires* our own ground truth is deferred to §6.9.

**Constraint B — no invented numbers.** Every value we emit must trace to a named source. This bans the plausible-looking default, which is the failure mode that actually ships.

#### The line is *sourced vs invented*, not *rule-based vs learned*

This distinction matters more than the ground-truth constraint, and getting it wrong in either direction is costly.

**Published, established science is fully in scope.** A peer-reviewed spectral index with a documented threshold, a standard phenology-fitting method, an agro-climatic season calendar from a national institute, a physically-grounded index range — these are not guesses. They are methods whose accuracy someone else already measured, published, and subjected to review. Using them is *stronger* evidence than anything we could produce ourselves right now, and refusing them would leave the pipeline worse, not safer. **We should be using more of this, not less.**

**What is actually banned:**

| Banned | Why | Example in the current code |
|---|---|---|
| A threshold we tuned by eye with no source | Unfalsifiable, unreviewable | Several `CROP_CYCLE_*` constants have no documented basis |
| A default that reads as a measurement | Indistinguishable from a real value downstream | `vigor = 50.0` when no cycles exist; `fallow_fraction = 0.0` for dead land |
| Placeholder or dummy data of any kind | — | none currently, and it stays that way |
| An accuracy or confidence figure we did not measure | Misrepresents evidence to a lender | the artifact's embedded 95.8% |
| A conversion to units we cannot calibrate | The relationship is real; the local coefficient is not | NDVI/NIRv → ₹ per hectare |
| LLM-generated specifics | Fabrication by construction | any number the model produces rather than restates |

**The enforcement mechanism is a source register.** Every threshold and coefficient in `config.py` gains a citation field naming its origin — paper, institutional dataset, or "internal, provisional, pending validation". Anything in that third category is visible and reviewable rather than hiding among the sourced values. This is a mechanical change and it is what makes the whole contract auditable.

#### Scientific basis available to us today

Method families we can and should draw on, each with published validation. **Exact citations to be verified and recorded in the source register when implemented** — the names below are the starting point for that lookup, not the final reference:

| Area | Established basis | Where it lands |
|---|---|---|
| Water discrimination | NDWI (McFeeters), MNDWI (Xu) — long-standing, widely validated | Phase 1 land gate |
| Built-up discrimination | NDBI (Zha et al.) plus SAR VV/VH double-bounce behaviour | Phase 1 land gate |
| Bare soil | BSI, MSAVI2 — both with documented soil-line behaviour | Phase 1 land gate |
| Phenology extraction | Double-logistic fitting (Beck et al.); TIMESAT conventions for SOS/EOS amplitude thresholds, small vs large integral | Phase 3.6 — **and note the code already implements double-logistic; we're recovering what it computes** |
| Time-series smoothing | Whittaker (Eilers) — **already implemented and discarded** (D-12) | Phase 2.3 |
| Photosynthetic capacity | NIRv → GPP relationship (Badgley et al.) — this is why NIRv-AUC is a defensible *relative* yield-potential proxy | Phase 3, existing |
| Thermal accumulation | GDD with published per-crop base temperatures | Weather, existing |
| Crop phenology reference | ICAR crop durations, stage definitions, expected NDVI trajectories | `CropGrowthCurves` — **already in the repo, currently unwired** |
| Season calendars | State agriculture department / ICAR cropping calendars; IMD monsoon onset normals by region | Phase 3.3, replacing the pan-India constant |
| Land cover | ESA WorldCover, Google Dynamic World — published validation reports | Phase 1.6 |

Note how much of this the codebase **already contains and simply does not use**: the Whittaker smoother, the double-logistic fit's rate parameters, 1150 lines of ICAR crop reference data, NDWI. A large share of the "add more science" work is really *stop discarding the science already implemented*.

#### The one genuinely hard case: `CropGrowthCurves` against a declared crop

The curves themselves are sound reference data. The risk is the *label*: a farmer declares "Cotton", actually grows something else, and we benchmark against the wrong curve — swinging up to 45% of the index by weight.

**But that is testable without ground truth.** Cotton has a published duration of ~165–180 days and a characteristic NDVI trajectory. If the observed cycle runs 90 days with a different curve shape, the declaration is inconsistent with the observation — and we can detect that from data we already have. So:

> Apply crop-specific benchmarks **only when the observed phenology is consistent with the declared crop's published duration and curve**, gated on fit quality. Where they disagree, fall back to crop-agnostic scoring and **record the inconsistency as a data-quality signal**.

This turns the weakest input into a check rather than a liability, needs no field data, and is exactly the kind of published-science use that should be in scope. It supersedes my earlier blanket "no" — see revised task 4.4.

#### The provenance contract

These become enforced rules in the codebase, not aspirations. Several current defects are direct violations, which is why they're already in the register.

| Rule | Meaning | Violated today by |
|---|---|---|
| **P-1 · Absent ≠ zero** | If a value was not computed, it is absent or explicitly `null`. Never a plausible-looking default. | `_clean_obj` collapsing tri-state `null` → key-absent (B9); `fallow_fraction` defaulting to `0.0` for zero-cycle plots (D-02); `vigor` defaulting to `50.0` when no cycles exist |
| **P-2 · Every derived number names its source** | Each metric carries a source enum: `observed` / `interpolated` / `imputed` / `declared` / `third_party` / `modelled`. | `signal_source` exists per bin and is dropped at persist; imputed cycle peaks are indistinguishable from observed (D-23) |
| **P-3 · No statistic without its sample size** | An NDVI mean is meaningless without the valid-pixel count behind it. | Valid-pixel counts computed and discarded — *no sample size exists anywhere in the output* (§5.3) |
| **P-4 · Labels must match the computation** | If no peer comparison ran, nothing may say "peer". If no SHAP ran, `shap_available` is false. | D-60, D-61 |
| **P-5 · Cold-start stays cold** | A feature with insufficient data is reported as unavailable, never back-filled with a synthetic distribution. | Currently handled correctly by `peer_benchmark.py` — preserve this behaviour |
| **P-6 · Every threshold cites its origin** | Each constant in `config.py` carries a source: paper, institutional dataset, or an explicit `provisional_unsourced` marker. Third-party datasets are recorded with their published per-class, per-region accuracy — read, not assumed. **Published science is encouraged; unsourced science must be visibly labelled as such.** | ~21 `CROP_CYCLE_*` constants have no documented basis (D-35) |
| **P-7 · Generated prose may only restate computed values** | The LLM renders numbers; it does not produce them. Post-generation numeric consistency check required. | No grounding check exists today (D-62 area) |

#### What this changes versus the original plan

| Original | Revised | Why |
|---|---|---|
| Land gate = our own spectral rules | **Published thresholds + third-party LULC + temporal evidence, combined** | Sourced science on all three streams. Nothing we invented. |
| Phase 4 = fix and retrain the classifier | **Quarantine the ML classifier; verify the declared crop instead** | Retraining needs labels (F-1). Verifying a declaration against published crop curves does not. |
| Crop-specific benchmarks off | **On, gated by phenology consistency** (task 4.4) | The curves are published reference data; the check makes the unverified label safe to use. Revives per-stage weather as a bonus. |
| Wire up `YIELD_PARAMETERS` to ₹ | **Relative yield-potential yes; absolute ₹/t-ha no** | NIRv→photosynthetic capacity is published. The local yield coefficient is not derivable without harvest records. |
| Report shows "Suggested action", "projected band" | **Drop from v6** | No policy layer, no forecast validation. §6.9. |
| Report shows district-median NDVI | **Only once the cohort is genuinely warm** | Real once we have parcels to compare; absent until then. Never synthesised. |
| — | **New: source register on every config threshold** | The enforcement mechanism. Makes "provisional, unsourced" visible instead of indistinguishable from established science. |

---

Eight phases. Phases 1–3 are sequential (each depends on the previous); 5 runs in parallel throughout; 6–8 depend on 5.

**Phase 5 (persistence) is now the highest-value work in the plan** — and not only for the report. *You cannot retrospectively validate what you did not store.* Every month we run without persisting the per-bin series and phenology is a month of ground-truthing opportunity permanently lost. When field data does arrive, Phase 5 is what makes it usable against historical assessments.

```
P0 ─┬─ P1 land gate ── P2 signal ── P3 cycles/seasons ─┬─ P7 report payload ── P8 report doc
    │                                  │               │
    │                                  ├─ P4 crop cls ─┤
    │                                  └─ P6 explain ──┤
    └─ P5 persistence ──────────────────────────────────┘
```

---

### Phase 0 — Truth and triage
**Goal:** stop producing wrong stored data and wrong claims. No behaviour redesign.
**Depends on:** nothing. **Blocks:** nothing — ship immediately.

| Task | Items |
|---|---|
| 0.1 Fix silently-wrong persisted fields | B1, B2, B3, B4, B5, B20 |
| 0.2 Make write failures visible | B11 (persist FAILED runs), B12 (stop swallowing), B6 |
| 0.3 Preserve tri-state through storage | B9 — `_clean_obj` must not strip explicit `null` on benefit flags |
| 0.4 Add `jobs` indexes + TTL | `{status,created_at}`, `{status,started_at}`, `{farmer_id,completed_at}`, TTL on `created_at` |
| 0.5 Remove overstated claims | Strip "peer-relative"/"above peers" from the Groq prompt, counterfactual copy, and reason codes until Phase 6 makes them true (D-60). Set `shap_available: False` on the heuristic path (D-61). |
| 0.6 Fix `GROQ_MODEL` default + cap blocking time | D-64 — correct `.env.example` and `config.py`; reduce worst case from ~95 s |
| 0.7 Correct the four drifted documents | §1.2 |

**Acceptance:** a fresh assessment produces no permanently-null stored field; a forced write failure appears in logs *and* leaves a FAILED document; `jobs` queries use an index plan.

---

### Phase 1 — Land classification gate  ⭐ **the product blocker**
**Goal:** the pipeline refuses to emit a credit signal for non-agricultural land, and says why.
**Depends on:** P0. **Blocks:** everything downstream that claims credibility.

| Task | Detail |
|---|---|
| 1.1 Compute the missing discriminators | Add **NDBI**, **BSI**, **MNDWI** to both GEE (`_scene_to_feature`) and STAC (`_calculate_indices`) paths — all from bands already downloaded. Retain **raw VV/VH backscatter in dB** (currently discarded at `satellite_collector.py:686-693`) — the strongest built-up/water discriminator available. |
| 1.2 Retain the free statistics | Stop dropping GEE's per-index `stdDev`/`p90` at read-out. Emit a real `valid_pixel_fraction` per bin (fixes D-13 as a side effect). |
| 1.3 Build the gate | New `crop_analysis/land_cover_gate.py`. Signature and evidence schema per §3.2. Classes: `CROPLAND / WATER / BUILTUP / BARREN / FOREST / PLANTATION / UNKNOWN`. Per **D-1**, emits a calibrated `confidence`, not a boolean — driving three outcomes: reject / flag-and-score / pass. Per **D-6**, `PLANTATION` is a **distinct non-rejecting verdict** and must never be collapsed into `FOREST` or `BARREN`.<br><br>**Three independent evidence streams, combined — not one rule:**<br>① **Third-party LULC** (1.6) — the only stream with external validation, therefore the primary.<br>② **Spectral**, on **raw un-normalised** indices (this is why it must not depend on VS): published, citable thresholds only — NDWI/MNDWI for water, NDBI for built-up, BSI/MSAVI2 for bare soil. **Cite the source paper for every threshold in config.** No threshold we invented and cannot defend.<br>③ **Temporal** — does the parcel green up and senesce seasonally at all? This one needs no external validation because it is a statement about the parcel's own signal, and it is the stream that separates *fallow farmland* (greens up in some years) from *permanently barren* (never does).<br><br>**Agreement → high confidence → act. Disagreement → the flag band from D-1.** Encoding the disagreement is the honest output; forcing a verdict is not. |
| 1.4 Wire it in | `main.py` between :696 and :739. Stamp `assessment['land_cover']` always — including on pass, so a lender can see the evidence. A flagged (borderline) verdict must additionally discount the confidence gate, so a parcel we are unsure about cannot score as if it were clean cropland. |
| 1.5 New terminal status | `REJECTED_NOT_AGRICULTURAL` distinct from `FAILED`. Branch `job_runner.py:272`. Multi-farm uses the existing non-`error:` skip channel → counts as *skipped*, not *failed*. Plantation parcels are **held, not rejected**, until Phase 3.2 lands. |
| **1.6 Third-party LULC — now the PRIMARY evidence, not optional** | Per Constraint A, this is the only land-cover evidence we have that carries **someone else's published validation**. Two candidates, both reachable from the existing GEE session via `linkCollection` (`satellite_collector.py:285`): **ESA WorldCover** (10 m, annual, discrete classes, ships a formal validation report) and **Google Dynamic World** (10 m, near-real-time, derived from the same Sentinel-2 we already read, and — importantly — exposes **per-class probability bands** including `crops`, which map directly onto D-1's confidence banding). **Before either is relied on, read the actual published per-class accuracy for the cropland class over South Asia and record it in config** (rule P-6). Do not assume the global headline number applies to Indian smallholder parcels — sub-hectare fields are the known weak case for 10 m global products. Cache the verdict with the satellite blob (top-level key survives; per-scene does not). |
| 1.7 Fix the perverse incentives | D-02 (zero cycles → zero fallow penalty); cap `_sub_vigor` so persistent forest canopy cannot saturate to 100; require a bare-soil return between cycles for a cropland verdict. |
| 1.8 Golden tests | Fixture parcels: water body, rooftop, quarry, reserved forest, genuine fallow farm, genuine double-cropped farm, mango orchard. Assert verdict and status for each. **This test file is the deliverable that makes the claim defensible.** |

**Acceptance:** each of the seven fixture parcels returns the correct verdict; no non-cropland fixture produces an `index_score`; every verdict carries auditable raw-index evidence.

---

### Phase 2 — Signal integrity
**Goal:** the detection signal is a physically meaningful, cross-farm-comparable quantity.
**Depends on:** P1 (the gate needs raw indices, which this phase makes primary).

| Task | Detail |
|---|---|
| 2.1 Kill per-parcel min-max normalisation | D-10. Normalise against a **fixed physical range** per index, not the parcel's own history. This is the single highest-leverage change in the backend after the land gate. |
| 2.2 Reconsider the kNDVI backbone | D-11. Either drop `tanh(NDVI²)` in favour of a sign-preserving blend, or keep kNDVI for vigor and carry raw NDVI separately for discrimination. |
| 2.3 Actually use the Whittaker output | D-12. `vs_smooth` is already computed — feed it to the detector and retire the 70-day Bartlett window that erases short-duration crops. |
| 2.4 Flag imputation end-to-end | D-23. Every bin carries `signal_source ∈ {optical, fused, sar, imputed}`; every cycle carries `peak_observed: bool`. **A fabricated peak must never be indistinguishable from an observed one.** |
| 2.5 Fix cloud-gap attribution | D-24 — count straddling gaps. |
| 2.6 Honest STAC path | D-14. Either fetch SCL and mask properly, or stop stamping `scl_qa60_v1` and degrade the confidence gate accordingly. |
| 2.7 SAR discipline | D-15, D-16. Include descending passes; bound the RVI→VS extrapolation to its training domain or mark out-of-domain bins low-quality; count SAR-recovered bins toward `n_valid`. |

**Acceptance:** `peak_cvi` is comparable across two different farms; a barren fixture no longer produces threshold crossings; imputed bins are visible in the output.

---

### Phase 3 — Crop cycles and seasons
**Goal:** correct cycles, and Kharif/Rabi/Zaid as a first-class output.
**Depends on:** P2.

| Task | Detail |
|---|---|
| 3.1 Re-derive thresholds on the new signal scale | D-20, D-21. Fix the inversion and set `low_cvi` to an actual bare-soil value. Fixes duration/AUC/land-utilization bias in one move. |
| **3.2 Perennial / long-duration branch** ⭐ **P0 per D-6** | D-22. Detect "no trough" as a **signal**, not a failure — route to a plantation/perennial cycle model instead of rejecting. Must not require a registry crop hint. Needs its own productivity measure: for a perennial, *canopy persistence and inter-annual vigor stability* are the credit signal, not cycle count — so `_sub_landuse` needs a perennial branch rather than `cycles_per_year`. **Pull this forward: it can start alongside Phase 2 rather than waiting for all of Phase 3.** |
| 3.3 One canonical, region-aware season model | D-25. Delete the three redundant definitions. Use latitude/LGD/eco-region — all already in scope. Assign by cycle *overlap*, not sowing month alone. Add the Zaid slot to `INTENSITY_WEIGHTS`. |
| 3.4 Per-season output | D-26. `LandUtilizationAnalyzer` emits per-season intensity, coverage, and fallow. `RiskIndexEngine._sub_landuse` consumes season coverage properly. **This is what makes the "cropping steadiness over 3 seasons" report caption possible.** |
| 3.5 Detection quality | D-27 (prominence + a merge/consolidation pass — the config keys already exist), D-28 (stop swallowing the next cycle). |
| 3.6 Full phenology metric set | D-29. Export `m_s`/`m_a` (two lines), name LOS, add baseline-subtracted AUC over SOS→EOS, 50% crossings, EOS→next-SOS fallow. |
| 3.7 Honest cycle confidence | D-30 (include fit R²), D-31 (fix the adoption gate now that walked duration is unbiased). |
| 3.8 Land-utilization consistency | D-34 — one occupancy definition; `LUI + fallow = 1`. |

**Acceptance:** an orchard fixture returns cycles and is not scored as abandoned; a 3-season farm returns three labelled seasons with per-season metrics; `LUI + fallow_fraction == 1`.

---

### Phase 4 — Crop classification: **quarantine, do not repair**
**Goal:** make the broken path unreachable and the crop label honest. Retraining is deferred to §6.9.
**Depends on:** nothing. **Parallel with:** everything.

Per Constraint A there is no ground truth, so the classifier cannot be retrained or validated. It is already off by default, so it is not currently harming output — the risk is that someone switches it on. Close that door.

| Task | Detail |
|---|---|
| 4.1 Fail loudly instead of silently | D-40. Add a load-time assertion that `len(feature_names) // len(ML_FEATURE_INDICES) == ML_FEATURE_SCENES`, plus `len(feature_names) == model.n_features_in_` and the sklearn version. **The classifier should refuse to load rather than quietly see half of every crop cycle.** This is ~10 lines and it is the entire point of the phase. |
| 4.2 Mark it experimental in code, not just in docs | Rename the flag to `ENABLE_CROP_CLASSIFICATION_EXPERIMENTAL`, log a prominent warning when enabled, and stamp `crop_label_source: "ml_experimental_unvalidated"` on any output it produces. Surface `trained_date` and `training_config` (D-42) so its provenance is visible — but **do not surface `training_metrics`**: per Constraint B, a leakage-inflated 95.8% presented to a lender is worse than no number. |
| 4.3 Make the declared crop honest instead | The live path already uses the AgriStack/registry crop. Keep it, and label it precisely: `crop_label_source: "declared_unverified"`, `crop_confidence: null` (**not `0.0`** — rule P-1: absent is not zero). The report must render this as *"Cotton (farmer-declared, unverified)"*, never as *"Cotton"* alone. |
| **4.4 Open the crop-specific path — but gate it on phenology consistency** | D-41. Fixing it *as written* would let an unverifiable self-report swing 45% of the index. Fixing it **with a consistency check** is both safe and a genuine accuracy improvement. Implement `verify_declared_crop(observed_cycle, declared_crop)`: compare observed duration against `CROP_DURATIONS[crop]` min/max, and observed trajectory against `EXPECTED_NDVI_CURVES[crop]` (fit quality / RMSE). Three outcomes: **consistent** → set a real, bounded confidence and unlock crop-specific scoring; **inconsistent** → crop-agnostic PATH B, record `declared_crop_mismatch: true` with the evidence; **indeterminate** (too few observations to judge) → PATH B, no claim either way. This finally wires up `EXPECTED_NDVI_CURVES`, `CRITICAL_STAGES` and `CANOPY_TYPE`, and as a side effect revives the dead per-stage weather analysis (D-50). **The mismatch flag is also the first crop-label signal we will have ever collected — it feeds F-1.** |
| 4.5 Load once | D-45 — construct at pipeline init, if it is ever enabled at all. |
| 4.6 Preserve the training inputs | Ensure Phase 5 stores the per-cycle index series in the shape a future training run would need. **This phase's real deliverable is that the eventual retraining is possible.** |

---

### Phase 5 — Persistence redesign  ⭐ **the report enabler**
**Goal:** one queryable, versioned, validated store that holds everything the report needs.
**Depends on:** P0. **Parallel with:** P1–P4. **Blocks:** P7, P8.

| Task | Detail |
|---|---|
| 5.1 Unify the two schemas | D-5.2. A single-farm result becomes a one-plot multi-farm result. One writer, one shape. |
| 5.2 Split the collections | See §7.1 target model — `farmers`, `farm_parcels`, `assessments`, `assessment_evidence`, `score_history`. |
| 5.3 Persist the time series | Move the per-bin index series out of the 30-day cache into `assessment_evidence`, keyed by `(farmer_id, parcel_id, assessment_id)`. **All 13 indices, all bins, with `signal_source` and `bin_quality`** — not 3 indices over cycle windows. |
| 5.4 Persist the evidence blocks | `anomaly_events`, `yield_detail`, `health_detail`, `weather_indicators`, phenology, `cycle_detection_diag`. These already exist in `jobs.result` — stop throwing them away. |
| 5.5 Stable parcel identity | D-5.7. Persist `plot_key` at ingest; never derive it at runtime. |
| 5.6 Per-parcel assessment rows | So a plot has a queryable score history. |
| 5.7 Score history | Every run appends `{assessment_id, date, index_score, band, index_version, weights}`. **Required for the report's `▼ 47 pts vs Apr 2025` trend tile — nothing stores a prior score today.** |
| 5.8 Pydantic models + a real serializer | Numpy coercion, NaN/Inf policy, timezone-aware datetimes everywhere, dates as `Date` not string. Response models on the API. |
| 5.9 Indexes | §5.5. |
| 5.10 Retire `jobs.result` as a data store | Once `assessments` is authoritative, `jobs.result` shrinks to a status pointer. |

**Acceptance:** `db.assessment_evidence.find({farmer_id, parcel_id})` returns the full NDVI series; a 6-month-old assessment is retrievable and comparable; no document write can silently fail.

---

### Phase 6 — Explainability, narrative, peer cohort
**Depends on:** P3 (needs per-season and phenology outputs), P5 (needs persistence).

| Task | Detail |
|---|---|
| 6.1 Warm the peer cohort ✅ **no ground truth needed** | D-60. A percentile *among parcels we have ourselves assessed* is a real, self-referential fact — it needs no external validation, only volume. `feature_store` is **already accumulating** `nirv_auc_mean_by_cycle` + `cohort_key` on every run; build the aggregation job that populates `cohort_stats`. Once `n ≥ 20`, `peer_nirv` fires and the "peer-relative" language becomes true — **and not one day before** (rule P-5; the existing cold-start fallback is correct, preserve it). Two honesty requirements: state the cohort size wherever a percentile is shown, and describe it as *"compared to N parcels assessed in this zone"* — never as a population statistic, since our assessed set is not a random sample of Indian farms. The cohort key's hardcoded `'NA'` crop family stays `NA` until F-3. |
| 6.2 Additive attribution | D-61. Either make contributions reconcile to `base_value` or rename the block honestly. |
| 6.3 Persist narrative + provenance | D-62. `english_narrative`, `translated_narrative`, model id, prompt hash. Non-negotiable for lender audit. |
| 6.4 Per-sub-index driver captions | The report needs one grounded sentence per sub-index, each citing a specific metric. **Deterministic templates with optional LLM polish** — not free generation. |
| 6.5 Recover the counterfactual content | D-66. Port the rich actions/timeframes from the dead legacy path into the v5 shape. |
| 6.6 Narrative hygiene | D-63 (dry-run tagging), D-65 (translation-failure flag). |
| 6.7 Anomaly narrative | Surface the persisted `anomaly_events` as "water-stressed on 2023-08-14 during FLOWERING" — currently computed then collapsed to three integers. |

---

### Phase 7 — Report data contract
**Depends on:** P5, P6.

Build the endpoint that assembles the report payload. Per the design audit, the report needs **~120 distinct fields**; a large share do not exist today. Grouped by what's required:

| Report needs | v6? | Status |
|---|---|---|
| Score, band, sub-indices, weights, reason codes, gate, raw index | ✅ | exists today |
| Per-parcel score + band + area + tenure + crop | ✅ | computed; needs P5.6 to be queryable per-parcel |
| Land-cover verdict + evidence | ✅ | **new in Phase 1 — and arguably the most valuable panel in the report** |
| **Score trend vs a prior assessment** | ✅ | P5.7. **On a first assessment, show nothing — not a fabricated baseline** (rule P-1) |
| **Parcel NDVI series** | ✅ | P5.3 |
| **District-median NDVI comparison series** | ⚠️ | Real once P6.1's cohort is warm. Until `n ≥ 20` in that zone, **render the parcel line alone and omit the comparison** — never synthesise a median |
| Weather tiles: 7-day rain, temp avg, GDD, SPI-3, dry spell | ✅ | computed today, dropped at persist — P5.4 recovers them |
| Weather tile: **soil moisture** | ✅ | not computed today, but **NASA POWER already exposes root-zone soil wetness** and we already call POWER — this is a real added parameter, not a proxy |
| Observation counts, cloud-free %, window dates, S-1 pass count | ✅ | partially in `signal_quality_summary`; P5 persists it. Rule P-3 makes this mandatory, not optional |
| Sub-index driver captions | ✅ | P6.4 — **deterministic templates citing computed metrics**, LLM polish optional |
| Methodology provenance (model, optical, radar, weather, cadastral, window) | ✅ | scattered today; assemble in P7. Now includes the LULC source + its published accuracy (rule P-6) |
| Crop name per parcel | ✅ | **rendered as "declared, unverified"** per 4.3 — never as a detected fact |
| **Farmer PII** (name, address, PIN, masked mobile/Aadhaar, DOB, AgriStack ID) | ⚠️ | exists in `farm_info`/`farmer_profile`; **blocked on masking policy — decision D-5** |
| Survey numbers, tenure as enum | ✅ | survey number exists in `farms[]`; tenure needs an enum alongside the numeric factor |
| Owned/leased area split, holding centroid | ✅ | derivable from `farms[]`, just not computed |
| Report number, report hash, generation timestamp | ✅ | trivially real — deterministic hash over the assessment payload |
| **Policy action, committee threshold, re-assessment date, projected band on recovery** | ❌ | **F-12 / D-9.** No policy layer exists, and "projected band on recovery" is an unvalidated forecast. **Drop the panel from v6** rather than fill it with plausible text |
| Expected S-2 revisits / S-1 passes | ⚠️ | computable from orbit calendars, but low value — **defer** |
| Prepared-by / reviewed-by / review status | ❌ | **F-13.** Prepared-by can come from auth; the review workflow does not exist |

**Net: the v6 report is materially thinner than the mockup — and that is the correct outcome.** The panels being dropped are exactly the ones the design filled with invented specifics (a policy code, a committee threshold, a re-assessment date, a projected band). Shipping those would mean generating lending advice we have no basis for.

---

### Phase 8 — Report generation
**Depends on:** P7. Produces the document matching `enhancements/`, with the **KBS rendered as the gauge**, not the design's linear scale bar.

Two deviations from the supplied design, both already decided:

- **Scale (D-3):** the report renders **300–900 with the 4 agronomic bands**, not the design's 300–950 / 5 credit-policy bands. The design's band-card row collapses from 5 cards to 4, the `SCALE 300–950` labelling is dropped, and the existing `KbsGauge` is reused unchanged (its 4 equal 45° arcs already match). The per-band "policy consequence" lines in the design have no backend source and are parked under **D-9**.
- **Score presentation:** the design renders KBS as a linear gradient scale bar with a tick marker. We replace that with the semicircular gauge. Note the gauge exists (`KbsGauge.tsx`) but is **not currently used in the print report at all** — the print report renders the score as a plain text line.

Note: the design contains **no maps**, despite the app already having a working satellite boundary map (`PlotBoundaryMapInner.tsx`). Adding one is a new decision, not a carry-over.

---

### 6.9 Deferred — requires ground truth or field validation

Everything here is **blocked on data we do not have**, not on engineering effort. Attempting any of it now would mean inventing the missing evidence. Each entry names its unblocking condition, so this list doubles as the field-data collection spec.

| # | Deferred item | Blocked on | Unblocks when |
|---|---|---|---|
| **F-1** | **Retrain the crop classifier** | Field-verified crop labels, geolocated, with a season stamp | ≥ ~2–3k parcels with verified crop + season, spread across agro-climatic zones. Split **grouped by farm**, not by sample — the current artifact's headline accuracy is likely inflated precisely because this wasn't done. |
| **F-2** | Turn crop classification on in the live path | F-1, plus a held-out regional validation set | F-1 complete and per-zone accuracy measured, not assumed |
| ~~F-3~~ | ~~Open the crop-specific scoring path~~ | **Moved to now — task 4.4.** The published ICAR curves are sound reference data, and the declared crop can be *verified against observed phenology* without any field data. What remains deferred is trusting a crop label we cannot check at all. | — |
| **F-4** | **`YIELD_PARAMETERS` → absolute t/ha and ₹ estimates** | Observed yields paired with assessed parcels | ≥ 1 season of harvest records per crop × zone. **Note the distinction:** NIRv-AUC as a *relative* yield-potential proxy is published science and stays in scope; converting it to **absolute tonnes or rupees for a specific crop and region** needs a local coefficient we cannot derive. The relationship is real; the calibration constant is not. Keep the 210-line MSP/yield table unwired until then. |
| **F-5** | Calibrate NIRv-AUC → regional yield quantiles | Same as F-4 | Roadmap item X4 |
| **F-6** | Calibrate cycle-detection thresholds to real phenology | Observed sowing/harvest dates | ≥ a few hundred parcels with recorded sowing dates. *Note: Phase 3.1 still fixes the threshold **inversion** now — that's a coherence bug, provable without field data. What's deferred is tuning the values to reality.* |
| **F-7** | Validate the land-cover gate against field observation | Site visits or high-res imagery review on rejected/flagged parcels | First operational batch. **Phase 1 ships using third-party validated LULC (borrowed ground truth); F-7 is when we measure our own false-rejection rate.** Track it from day one. |
| **F-8** | PD / repayment calibration; `outcome_label`, `pd_estimate` | Loan performance outcomes | ≥ 1 full lending cycle with repayment data. The `calibration` hooks in `risk_index_engine.py` already reserve the fields — leave them `null`. |
| **F-9** | Convert the index into a lending limit (₹) | F-8 | Roadmap X1. Keep `no_repayment_calibration: True` until then. |
| **F-10** | **Publish any accuracy figure at all** | Whichever of F-1/F-7/F-8 the claim concerns | Never quote the artifact's embedded 95.8% (D-42). |
| **F-11** | Sub-index weights beyond "AHP provisional" | Expert panel sign-off and/or F-8 | Until then the UI must keep disclosing them as provisional |
| **F-12** | Policy action layer — "defer 30 days", committee thresholds, projected band on recovery | Lender policy definition (a business input, not a data one) | Decision **D-9**. This is the one deferred item unblocked by a conversation rather than by data. |
| **F-13** | Report "reviewed by" / review status | A review workflow that does not exist | Product decision |
| **F-14** | Regional/per-zone models of any kind | F-1 plus zone-stratified samples | Long horizon |
| **F-15** | Intercropping / staggered-sowing detection (D-32) | Sub-field ground truth | Long horizon; needs pixel-level labels |

**Collect from day one, even before it's usable:** verified crop labels, sowing/harvest dates, and land-cover verdicts on rejected parcels. Phase 5 makes it possible to attach them retrospectively to assessments already run. That is the single highest-leverage thing this plan does for the *next* twelve months.

---

## 7. Decisions I need from you

These change the design; I don't want to guess.

### 7.0 Decided (2026-08-16)

| # | Decision | Resolution | Consequence |
|---|---|---|---|
| **D-10** | Minimum fundable plot size | **0.15 ha** (15 Sentinel-2 pixels) | Becomes `PARCEL_MIN_PIXELS_HARD`. Below it the pipeline returns `INSUFFICIENT_DATA` before pulling imagery, so no quota is spent on plots nobody would lend against. **48% of current parcels fall below this — 54 of 55 are AgriStack-sourced.** Note this is a *business* threshold; `PARCEL_MIN_PIXELS_RELIABLE` (0.50 ha) is a separate *physical* one, marking where boundary-pixel contamination stops dominating. Between the two a parcel is scored but flagged and confidence-discounted. |
| **D-7** | Crop classification | ML classifier quarantined; declared crop gated on phenology consistency | Task 4.4 remains unimplemented — see §0.1 item 5. |

### 7.1 Decided (2026-08-15)

| # | Decision | Resolution | Consequence for the plan |
|---|---|---|---|
| **D-1** | Non-agricultural land handling | **Confidence-banded.** Three states: high-confidence non-farmland → `REJECTED_NOT_AGRICULTURAL` with no score; borderline → score but flag prominently with evidence; clear cropland → normal. | `land_cover_gate` must emit a calibrated `confidence`, not a boolean. Two thresholds become config (`LANDCOVER_REJECT_CONFIDENCE`, `LANDCOVER_FLAG_CONFIDENCE`). The flagged band needs its own confidence-gate discount so a borderline parcel cannot score as if it were clean cropland. |
| **D-3** | KBS scale | **Keep 300–900 with 4 equal agronomic bands.** Backend and `KbsGauge` stay as-is; the **report design is updated to match**, not the code. | No score migration, no gauge geometry change. Phase 7/8 must reconcile the design's 5 credit-policy band cards down to the 4 agronomic bands, and drop the design's `SCALE 300–950` labelling. The design's per-band "policy consequence" lines still need a home — see D-9. |
| **D-6** | Perennials / plantations | **Fundable.** Orchards, banana and sugarcane are products we want to score. | **D-22 is confirmed P0 and Phase 3.2 is pulled forward.** Phase 1 must classify `PLANTATION` as a *distinct, non-rejecting* verdict (never lumped with FOREST or BARREN), and Phase 3.2 delivers a perennial cycle model that treats "no trough" as a signal rather than a rejection. Until 3.2 lands, a plantation parcel should be flagged-and-held, **not** scored as abandoned land. |
| **D-2** | Land-cover source | **Third-party validated LULC is primary; spectral rules corroborate.** Flipped by Constraint A — our own thresholds would need validation we cannot perform, whereas ESA WorldCover / Dynamic World ship with published validation. | Phase 1.6 is now core scope, not optional. Adds a GEE dependency and quota cost. Requires reading and recording the real per-class accuracy for cropland over South Asia before launch (rule P-6) — **10 m global products are weakest on sub-hectare fields, which is exactly our population**, so this number matters and must not be assumed. |

### 7.2 Still open

| # | Decision | Options | My recommendation |
|---|---|---|---|
| **D-7** | Crop classification | ML classifier off with a train/serve bug; declared crop currently unusable for scoring. | **Resolved.** ML classifier → quarantine (retraining is F-1). Declared crop → **use it, gated on phenology consistency** (task 4.4). No open question remains; the earlier blanket "no" is superseded. |
| **D-4** | Sub-index weights | Design labels 30/28/22/20; backend uses 30/25/20/25. | Backend wins unless you have an agronomic reason. The design is likely a mockup approximation — but confirm, since the doc calls them "AHP provisional, expert sign-off pending." |
| **D-5** | Farmer PII in reports | Name, address, DOB, masked mobile + Aadhaar are all in the design. | Need your masking/retention policy before I pipe Aadhaar into an assessment payload. Recommend: never store Aadhaar in `assessments`; join at render time from `farmers`, masked. |
| **D-8** | Legacy `credit_assessment` shim | Kept for dashboard compat. | Keep through the frontend pass, then delete. Flag it now so we don't build new surfaces on it. |
| **D-9** | Band → policy action mapping | The report design attaches a lending consequence to each band ("defer 30 days", committee thresholds, re-assessment dates). No policy layer exists in the backend. | Needs a decision on whether policy lives in the backend at all, or stays a lender-side config. Blocks the report's "Suggested action" panel (Phase 7). |

---

## 8. Appendix

### 8.1 Delete list (dead code, verified by grep)

- `counterfactual_engine._generate_scenarios` (`:160-402`) — unreachable, would `KeyError` if reached
- `shap_explainer._compute_shap` + `_extract_features` (`:104-240`) — unreachable while `model=None` is hardcoded
- `weather_analyzer.analyze_seasonal_weather`, `_detect_extreme_events`, `SEASONAL_RAINFALL_NORMS` (~190 lines) — no callers
- `CropDetector.analyze_cropping_pattern` — no callers in the live path
- `crop_cycle_detector._detect_cloud_gaps` — returns `[]` in production (the grid is uniform by construction)
- `multi_farm_assessor.py:403-407` — strips a key that never exists
- `mongodb_helper.upsert_latest_assessment` — no callers
- `ML_MODE`, `HIGH_VALUE_CROPS`, `HIGH_VALUE_MULTIPLIER`, `crop_family_band` — computed/configured, never consumed
- `YIELD_PARAMETERS` (210 lines) — **zero readers repo-wide**; keep the data, wire it or archive it
- 21 `CROP_CYCLE_*` config keys with no readers; 2 read-but-undefined

### 8.2 Test coverage to add

Today: one golden test file covering score bounds, tri-state benefits, shim shape, and config weight validity. Needed:

1. **Land-cover fixtures** (Phase 1.8) — the highest-value test in the plan
2. Cycle detection fixtures: perennial, short-duration, triple-crop, monsoon-gap
3. Season assignment: boundary months, cross-season cycles, regional variants
4. Persistence round-trip: numpy, NaN, timezone, tri-state survival
5. Schema contract: every field the report needs is present and correctly typed
6. Multi-farm aggregation with mixed skip/fail/success plots

### 8.2a Remaining backend work, in priority order

Everything below is scoped and unblocked; none of it is a prerequisite for the
ingest fix, which should run in parallel.

| # | Item | Why it matters | Size |
|---|---|---|---|
| **R1** | **Warm the peer cohort** (§6.1) | `feature_store` is already accumulating the inputs on every run. Once a zone has ≥20 assessed plots, `peer_nirv` fires and the peer language becomes true rather than removed. Needs no ground truth — a percentile among our own assessed parcels is a real, self-referential fact. | ~1 wk |
| **R2** | **Persist LLM narratives + model snapshot** (D-62) | Text shown to a loan officer currently vanishes after the HTTP response. No audit trail. The prompt hash is already computed and then discarded. | ~2 d |
| **R3** | **Per-sub-index driver captions** (§6.4) | Deterministic templates citing computed metrics. Required by the report, and the honest alternative to letting an LLM produce the numbers. | ~3 d |
| **R4** | **Declared-crop phenology gate** (task 4.4) | Compare observed duration/curve against `CROP_DURATIONS` and `EXPECTED_NDVI_CURVES`. Unlocks ~1150 lines of dead ICAR config, revives per-stage weather analysis, and produces the first crop-label signal we have ever collected — which feeds F-1. | ~1 wk |
| **R5** | **Report payload endpoint** (Phase 7) | Assemble what §7 lists as available. Blocked on R1–R3 for completeness, not for a first cut. | ~1 wk |
| **R6** | **Report document** (Phase 8) | KBS as the gauge, 300–900 / 4 bands per D-3. Materially thinner than the mockup — the "Suggested action" panel is dropped (F-12). | 1–2 wk |

Deliberately NOT scheduled: anything in §6.9. Those need field data, not effort.

### 8.2b Operational runbook

```bash
# Before any release: what moved, for whom, driven by what.
python scripts/devtools/score_drift.py --plan          # free, no imagery
python scripts/devtools/score_drift.py --limit 10 --out drift

# Data quality, no quota:
python scripts/devtools/audit_geometry.py --only-flagged

# Audit a single verdict against imagery:
python scripts/devtools/inspect_land_cover.py <farmer_id>
python scripts/devtools/inspect_assessment.py <farmer_id>
```

Run the drift report before every release. It caught three defects that the unit
tests missed (§0.5), including two I introduced.

### 8.3 Metrics to watch during rollout

- **% rejected as non-agricultural** — expect a real, non-zero number. If it's 0%, the gate isn't working
- **% flagged (borderline) vs rejected vs passed** — the D-1 confidence bands. A very small flag band means the thresholds are too confident for the evidence we actually have
- **Agreement rate between the three evidence streams** (LULC / spectral / temporal). Disagreement is the honest signal that a parcel is hard, and the rate tells us whether the gate is calibrated
- % of cycles with `peak_observed: False` (fabricated peaks, D-23)
- Distribution of `confidence_gate` before/after the `valid_pixel_fraction` fix — **expect it to drop, and that is correct**; today's gate is inflated by a stubbed input
- Peer cohort warm-up: count of `cohort_stats` keys reaching `n ≥ 20`
- Score drift on a held-out fixture set across each phase — **every phase changes scores; we need to know by how much and why**
- **Manual-review queue on rejections** — this is the seed of F-7. Every rejected parcel someone eyeballs is a ground-truth sample we didn't have yesterday. **Start this on day one of Phase 1**; it is the cheapest route out of Constraint A.
