# Crop Classification Model — Data Audit, Pipeline Contract & Architecture

**Status:** implemented and trained — see [PART L — Results](#part-l--results) and [`models/MODEL_CARD.md`](models/MODEL_CARD.md)
**Outcome:** tier-1 model at **0.7492** blocked balanced accuracy, **5 of 6 ship gates pass**; classification stays DISABLED pending the Gram-recall and cross-region failures in L.5/L.7
**Scope:** the ML crop classifier that fills the `enable_crop_classification=False` hole in Stage 5
**Training data:** `Crop_classification_model/crop_classification_train_500.gpkg` (9,000 parcels, 18 crops)
**Target artifact:** `backend/Credit_assessment/models/crop_classifier_model.joblib`
**Companion docs:** `docs/codebase/backend/04-ml-crop-classification.md`, `docs/codebase/backend/03-crop-cycle-detection.md`

---

## 0. Executive summary

We have 9,000 labelled farm polygons across 18 crops. That is enough to train a real classifier — but **not** by loading the GeoPackage into scikit-learn. The labels are attached to *polygons*, while the pipeline classifies *detected crop cycles* from a 45-dimensional satellite trajectory. The entire engineering effort sits in the middle: building the feature-extraction bridge, and refusing to let four separate leakage channels in this dataset manufacture a fake 99% accuracy.

Four findings drive the whole design:

| # | Finding | Consequence |
|---|---------|-------------|
| 1 | **`Area` is in acres, not hectares** (measured: median m²/`Area` = 4046.9) | Feeding it as `field_area_ha` makes `validate_farm_geometry` reject **100%** of polygons (ratio 0.405 < `GEOMETRY_AREA_RATIO_MIN` 0.5) and silently fall back to point+buffer — destroying train/serve parity |
| 2 | **`Date` is a survey snapshot, not a sowing date, and is degenerate per class** (Cotton: 1 unique date; Soyabean: 1; Wheat/Mustard/Onion: 100% January) | `Date` and anything derived from it (month, year) is effectively a class ID. Using it as a feature yields a worthless model that scores ~99% |
| 3 | **`Area` is a per-crop selection artifact** — the prep script took *top-500 by Area* from each single-crop source file | Area separates crops by construction (Cotton p25 = 6.50 ac sits above most crops' maximum). It is not agronomy. Ban it as a feature |
| 4 | **Extreme spatial clustering** — 233 of 280 25 km blocks contain only one crop; Chilli occupies 6 blocks; 64.8% of Tur sits in one block | Random train/test splits are meaningless. Spatially-blocked CV is mandatory and honest accuracy will be materially lower |

And one structural gap in the *existing* pipeline contract:

> The current 45-feature vector interpolates every cycle onto a **normalized 0→1 time axis**. Cycle duration is therefore invisible to the model: Bajra (85 days) and Sugarcane (330 days) present the same 15-point shape. Duration is the most discriminative agronomic variable available, and the contract discards it.

The plan is therefore two-tier: ship a **Tier 0** model that honours the existing contract exactly (zero pipeline code change, immediately deployable), then ship **Tier 1** which extends the contract with duration, season and richer indices — with a mandatory guard, because `crop_detector.py` silently zero-fills any feature name it does not recognise.

---

# PART A — The data we actually have

## A.1 Inventory

| Property | Value |
|---|---|
| File | `crop_classification_train_500.gpkg` (3.17 MB) |
| Layer | `train_500` |
| Geometry type | MultiPolygon (all single-part — 0 multi-part features) |
| CRS | EPSG:4326 |
| Rows | 9,000 |
| Classes | 18, perfectly balanced at 500 each |
| Date range | 2022-01-10 → 2024-12-10 |
| Spatial extent | 69.10–87.53 °E, 12.34–31.47 °N (peninsular + Indo-Gangetic India) |
| Provenance | `prepare_training_gpkg.py` — 9 single-crop source files + `Others_16000.gpkg` |

### Schema

| Column | Type | Meaning | Verdict as a model feature |
|---|---|---|---|
| `sample_id` | int64 | Row index + 1 | **Never** — identifier |
| `Crop_Name` | object | Crop label — the target | Target |
| `Area` | float64 | **Acres** (documented as ha — wrong) | **Banned** — selection artifact (§A.4) |
| `Date` | datetime64[ms] | Survey/observation snapshot | **Window anchor only, never a feature** (§A.3) |
| `source_file` | object | Origin GeoPackage | **Never** — near-perfect proxy for the label |
| `geometry` | MultiPolygon | Farm boundary | The key to everything — defines the AOI for satellite extraction |

### Class list vs. pipeline reference set

All 18 classes are covered by `CropGrowthCurves.CROP_DURATIONS`, so every prediction can drive the downstream crop-specific path. Five reference crops have no training data.

| Crop | min / typ / max days | Reference season | In data |
|---|---|---|---|
| Bajra | 65 / 85 / 110 | kharif | yes |
| Jowar | 90 / 110 / 130 | both | yes |
| Maize | 80 / 100 / 120 | both | yes |
| Rice | 100 / 120 / 150 | kharif | yes |
| Wheat | 110 / 130 / 150 | rabi | yes |
| Groundnut | 90 / 110 / 130 | kharif | yes |
| Mustard | 110 / 130 / 150 | rabi | yes |
| Soyabean | 90 / 110 / 130 | kharif | yes |
| Tobacco | 130 / 160 / 180 | rabi | yes |
| Gram | 100 / 120 / 140 | rabi | yes |
| Tur | 150 / 180 / 220 | kharif | yes |
| Cotton | 150 / 180 / 210 | kharif | yes |
| Chilli | 120 / 150 / 180 | both | yes |
| Onion | 100 / 120 / 150 | both | yes |
| Potato | 90 / 110 / 130 | rabi | yes |
| Grapes | 120 / 150 / 180 | rabi (de facto perennial) | yes |
| Banana | 270 / 330 / 365 | **perennial** | yes |
| Sugarcane | 270 / 330 / 365 | kharif (330 d) | yes |
| Sunflower | 85 / 100 / 120 | both | — no data |
| Cabbage | 60 / 80 / 100 | rabi | — no data |
| Papaya | 240 / 300 / 365 | perennial | — no data |
| Pomegranate | 150 / 180 / 210 | perennial | — no data |
| Others | 90 / 120 / 150 | both | — no data (see §E.6) |

## A.2 Geometry quality — good news

This is the healthiest part of the dataset.

| Metric | Value |
|---|---|
| Invalid geometries | 0 |
| Empty geometries | 0 |
| Multi-part features | 0 |
| Vertices per polygon | median 6, mean 8, max 107 |
| **Duplicate geometries (identical WKB)** | **109 — must be de-duplicated** |
| True parcel area (EPSG:6933) | median 0.86 ha, p5 0.35 ha |
| Sentinel-2 10 m pixels per parcel | min 20, p5 35, **median 86**, p95 294 |
| 20 m pixels per parcel (SWIR / red-edge) | p5 8.7, median 21 |
| 10 m "core" pixels after a −10 m inset | p5 10.5, median 48 |
| Parcels losing all core pixels at −10 m | **21 (0.2%)** |
| Parcels with < 10 core pixels | 406 (4.5%) |

**Interpretation.** Parcel-mean index extraction is well supported — a median of 86 pixels is a solid spatial average, and a one-pixel negative buffer to suppress boundary mixing costs almost nothing (0.2% total loss). The 20 m bands are thin but usable, and the pipeline already resamples everything to 10 m (`TARGET_RESOLUTION_M = 10`). Polygons are coarse (median 6 vertices) — cadastral approximations, not surveyed boundaries — which is a further argument for the negative buffer.

## A.3 Defect 1 — `Date` is not what the schema claims, and it is a leak

The prep script filters on `Date` being non-null and describes it as "cultivation season/year". It is neither a sowing date nor a harvest date. It is a survey/ingest snapshot, and it is close to constant within each class.

**Unique `Date` values per crop:**

```
Cotton 1    Soyabean 1   Gram 2     Onion 2     Mustard 3    Wheat 3
Chilli 4    Maize 5      Potato 5   Tur 5       Groundnut 6  Tobacco 7
Rice 9      Jowar 10     Grapes 16  Banana 20   Sugarcane 24 Bajra 28
```

**Month concentration:**

| Crop | Month signature |
|---|---|
| Wheat | 100% January |
| Mustard | 100% January |
| Onion | 100% January |
| Cotton | 100% July |
| Soyabean | 100% August |
| Gram | 100% November |
| Maize | 98.4% March |

**Year confounding:** Cotton is 100% 2023. Soyabean is 100% 2023. Gram is 99.6% 2022.

A model given raw `Date`, `month`, or `year` will memorise a lookup table and report near-perfect accuracy. It will then fail completely in production, where the observation window is set by the assessment request, not by a survey batch.

**Rulings:**

- `Date` has exactly one legitimate role: **anchoring the satellite observation window** so we extract the season the label refers to (§D).
- `year` — **banned**, unconditionally.
- `month` — **banned** as a raw feature.
- Derived agronomic **season** (kharif / rabi / zaid) is a *genuine* agronomic prior and *is* available at inference time. But in this dataset season is nearly degenerate with the label, so it must not be a free input feature. It is admitted only as an explicit, auditable structural prior (§E.5), and only if an ablation shows the model still works with it removed.

## A.4 Defect 2 — `Area` is a selection artifact, not agronomy

Two separate problems.

**(a) The unit is wrong.** Measured in an equal-area projection, median m² / `Area` = **4046.9**, which is exactly 1 acre (4046.86 m²). The column is acres; `prepare_training_gpkg.py` and its `AREA_MIN=0.5 / AREA_MAX=50.0` filters are documented as hectares.

This is not cosmetic. `SatelliteDataCollector.collect_historical_data(..., field_area_ha=...)` routes into `GeometryUtils.validate_farm_geometry`, which computes `ratio = true_area_ha / field_area_ha` and rejects outside `[GEOMETRY_AREA_RATIO_MIN=0.5, GEOMETRY_AREA_RATIO_MAX=2.0]`. Our ratio is a constant **0.405**. Passing `Area` unconverted rejects **every polygon** with `area_ratio_out_of_range:0.405`, and the collector then silently falls back to point + adaptive buffer — so training features would come from a circular buffer while production features come from the parcel. A total parity break that produces no error message.

> **Mandatory:** convert once at ingest — `area_ha = Area * 0.40468564224` — and carry `area_ha` everywhere downstream.

**(b) The distribution is manufactured.** `select_top_n()` takes the **top 500 by `Area`** from each source file. Each class's area distribution is therefore the upper tail of its own source file, chosen by the sampler.

Observed `Area` (acres) by crop:

| Crop | min | p25 | median | p75 | max |
|---|---|---|---|---|---|
| Cotton | 5.78 | 6.50 | 7.55 | 9.44 | 22.31 |
| Rice | 2.83 | 3.09 | 3.68 | 4.70 | 24.20 |
| Banana | 2.43 | 2.76 | 3.21 | 3.79 | 9.60 |
| Mustard | 2.30 | 2.61 | 3.09 | 4.16 | 13.06 |
| Tobacco | 0.78 | 0.84 | 0.90 | 0.99 | 6.67 |
| Bajra | 0.71 | 0.87 | 1.08 | 1.53 | 4.35 |
| Groundnut | 0.81 | 0.88 | 1.06 | 2.30 | 14.44 |

Cotton's 25th percentile (6.50 ac) exceeds the maximum of most other classes. Tobacco's interquartile range is 0.84–0.99 — a 0.15 acre window. These separations are sampler output, not field reality.

**Ruling:** `Area` / `area_ha` is **banned as a model feature** in v1. It may be used as a *gate* (minimum pixel count) and it must be passed to the collector in correct units. Revisit only if a future dataset is sampled without an area-ranked rule.

## A.5 Defect 3 — extreme spatial clustering

At a 0.25° (~25 km) grid:

- **280** occupied blocks
- **233 of 280 (83%) contain exactly one crop**
- Blocks per class: Chilli **6**, Tobacco 7, Tur 11, Groundnut 12, Jowar 15, Gram 15 … Cotton 38
- Largest single-block share of a class: **Tur 64.8%**, Mustard 51.0%, Groundnut 48.0%, Maize 47.4%, Tobacco 46.6%, Chilli 45.2%

Agro-ecoregion (using the pipeline's own `infer_agro_ecoregion` boxes) is nearly a class label for several crops:

| Crop | Concentration |
|---|---|
| Chilli | 500/500 SOUTHERN_PENINSULA |
| Tobacco | 500/500 SOUTHERN_PENINSULA |
| Tur | 499/500 DECCAN |
| Grapes | 498/500 DECCAN |
| Rice | 499/500 GANGETIC_EASTERN |
| Maize | 498/500 GANGETIC_EASTERN |
| Mustard | 496/500 GANGETIC_EASTERN |

**Why this bites even without lat/lon as a feature.** Satellite index trajectories are themselves spatially autocorrelated — regional climate, soil, atmospheric water vapour, sensor viewing geometry and even the acquisition calendar are shared within a Sentinel-2 tile. Parcels in the same 25 km block have correlated trajectories. A random split places near-neighbours on both sides and the model is scored on memorised neighbourhoods.

**Rulings:**

- Random or stratified-random k-fold is **forbidden** as the reported metric.
- **`GroupKFold` on 0.25° spatial blocks is the primary protocol** (§F).
- **Leave-one-ecoregion-out** is a required secondary stress test, reported separately and expected to be much worse. It measures the thing a lender actually cares about: generalisation to a district we have never seen.
- Expect the blocked number to land far below the random number. That gap is the honest measurement of how much of the dataset is geography rather than crop.

## A.6 Defect 4 — 109 duplicate geometries

109 rows share WKB-identical geometry with another row. De-duplicate on geometry hash **before** splitting, otherwise the same parcel lands in both train and test.

## A.7 What the data does *not* contain

Worth stating plainly, because it shapes the whole plan:

- **No satellite data.** Not one index value. Every feature must be acquired.
- **No sowing or harvest dates.** The cycle window must be *detected*, using the existing `CropCycleDetector`.
- **No irrigation, soil, yield, variety or management attributes.**
- **No negatives / non-crop class.** `prepare_training_gpkg.py` explicitly drops `NON_CROP_LABELS` (Forest, Built up, Barren, Water). The classifier cannot learn "not a crop" — which is fine, because `land_cover_gate.py` already handles that upstream, but it means the model must never be asked to.
- **No multi-year sequences per parcel.** One label, one season, per polygon.

---

# PART B — What the pipeline requires (the hard contract)

## B.1 Where classification sits

```
Stage 1  Geospatial prep / snapping         GeometryUtils.validate_farm_geometry
Stage 2  Satellite observation grid         SatelliteDataCollector
             3-year lookback (CONTINUOUS_LOOKBACK_YEARS = 3)
             10-day fixed bins (CONTINUOUS_SCENE_INTERVAL_DAYS = 10)
             ~110 bins/parcel, empty bins = NaN placeholders
             16 canonical indices per scene (INDEX_KEYS)
Stage 2b Land-cover gate                    land_cover_gate.py — is this farmland at all?
Stage 3  Crop cycle detection               CropCycleDetector -> List[CropCycle]
             sowing / peak / harvest dates, duration, peak_ndvi, cycle_kind
---------------------------------------------------------------------------
Stage 4  ML CROP CLASSIFICATION  <-- THIS DOCUMENT
             CropDetector.analyze_cycles(crop_cycles, all_continuous_scenes)
             per cycle: slice scenes -> 45 features -> predict -> calibrate
---------------------------------------------------------------------------
Stage 5  Weather analysis                   per-growth-stage, needs a crop name
Stage 6  Yield / performance                crop_specific vs. crop-agnostic fork
Stage 7  Risk index / credit score          landuse 30 · vigor 25 · stability 20 · weather 25
```

**Design philosophy already committed to in code** (`crop_detector.py` v4.1 header): classification is **enrichment-only**. Cultivation *activity* and *intensity* are the primary signals; a crop name is a bonus. A classification failure must never block a cycle, and never blocks the credit score. Our model must preserve that: **it is always better to abstain than to guess wrong.**

## B.2 The inference unit is a detected cycle — not a parcel, not a year

`analyze_cycles()` iterates `CropCycle` objects. For each:

1. `start_date` = cycle sowing, `end_date` = cycle harvest.
2. Window padded by `CROP_DETECTOR_CYCLE_SCENE_PADDING_DAYS = 5` on each side (ML features only; reported dates stay exact).
3. Only **real** (non-placeholder) scenes in that window are collected.
4. If `len(cycle_scenes) < MIN_OBSERVATIONS_PER_SEASON (5)` -> the cycle is **not classified** at all.
5. Otherwise a relaxed temporal gate runs (`cycle_based=True`), then classification is attempted inside a `try/except` that swallows all errors into `classification_note = 'ml_error: ...'`.

**This is the single most important fact for training-set construction.** Our labels are attached to polygons; our features must come from a *detected cycle*. Bridging that is §D.

## B.3 Model artifact contract — exact

`CropDetector.__init__` does:

```python
model_data         = joblib.load(crop_model_path)
self.model         = model_data['model']            # needs .predict() and .predict_proba()
self.label_encoder = model_data['label_encoder']    # needs .inverse_transform()
self.feature_names = model_data['feature_names']    # ordered list[str]
self.crop_names    = model_data['crop_names']       # ordered, aligned to predict_proba
```

| Requirement | Detail |
|---|---|
| Path | `models/crop_classifier_model.joblib`, override via env `CROP_MODEL_PATH` |
| Enable | env `ENABLE_CROP_CLASSIFICATION=true`, or job field `require_classification` |
| `model` | any estimator exposing `predict(X)` -> encoded int, `predict_proba(X)` -> (1, n_classes) |
| `label_encoder` | `inverse_transform([int]) -> [str]`; strings **must** be keys of `CropGrowthCurves.CROP_DURATIONS` (or resolvable via `_normalize_crop_name`) |
| `feature_names` | drives column order via `[feature_dict.get(fn, 0.0) for fn in self.feature_names]` |
| `crop_names` | zipped with `predict_proba` output -> `all_probabilities` dict. **Ordering must match `label_encoder.classes_` exactly** or every probability is mislabelled |

### The silent zero-fill trap

```python
feature_vector = [feature_dict.get(fn, 0.0) for fn in self.feature_names]
```

`feature_dict` is built only from `ML_FEATURE_INDICES` × `ML_FEATURE_SCENES`. **Any feature name the extractor does not produce becomes `0.0` with no warning, no log line, and no exception.** A Tier-1 model trained with `duration_days` and `season_onehot` would receive zeros for them in production and degrade catastrophically while reporting healthy confidences.

> **Mandatory guard.** Any model whose `feature_names` is not exactly the Tier-0 set must ship together with the matching `crop_detector.py` extractor change, and `CropDetector.__init__` must gain a fail-fast assertion:
> ```python
> missing = set(self.feature_names) - set(self._extractor_feature_names())
> if missing:
>     raise ValueError(f"Model expects features the extractor cannot produce: {sorted(missing)[:8]}")
> ```
> Store `extractor_version` in the joblib dict and check it on load.

## B.4 Feature contract — exact (Tier 0)

From `PipelineConfig`:

```python
ML_FEATURE_SCENES  = 15
ML_FEATURE_INDICES = ['NDVI_mean', 'EVI_mean', 'NDMI_mean']
```

`_classify_crop_chronological()` builds **45 features**:

1. Sort the cycle's scenes by date.
2. Map observations onto a normalized time axis `t_obs = linspace(0, 1, n)`.
3. Target grid `t_grid = linspace(0, 1, 15)`.
4. For each of NDVI / EVI / NDMI: fill NaNs by linear interpolation over valid points (falling back to 0.0 if fewer than 2 valid), then `np.interp` onto the 15-point grid. `n == 1` -> constant fill; `n == 0` -> zeros.
5. Name as `{index}_t{NN}` with the `_mean` suffix stripped and a **1-based, zero-padded** counter.

**Exact feature name list (order as produced; `feature_names` defines the model's own order):**

```
NDVI_t01 ... NDVI_t15      (15)
EVI_t01  ... EVI_t15       (15)
NDMI_t01 ... NDMI_t15      (15)
                     total 45
```

Finally `X = np.nan_to_num(np.array([feature_vector]), nan=0.0)`.

### What this contract loses

| Lost information | Why it matters |
|---|---|
| **Cycle duration** | Time is normalized to [0,1]. Bajra (85 d) and Sugarcane (330 d) produce identically-shaped vectors. The most discriminative agronomic variable available, discarded |
| **Absolute dates / season** | Kharif vs. rabi is invisible. Wheat and Rice can be shape-similar but never co-occur in season |
| **Observation density** | 5 scenes and 30 scenes both become 15 interpolated points. A 5-scene vector is mostly synthetic; the model cannot tell |
| **13 of 16 available indices** | NDRE, PSRI, MSAVI2, kNDVI, LSWI, GCVI, NDWI, NIRv, NDVI_std, NDVI_p90 are all computed and thrown away. NDRE and PSRI in particular are strong for chlorophyll and senescence |
| **Spatial texture** | Only the parcel mean survives. Row-crop vs. orchard structure is gone (and at median 86 pixels, texture was marginal anyway) |

Tier 0 accepts all of this in exchange for zero pipeline risk. Tier 1 fixes the top three.

## B.5 Confidence contract — a real decision boundary

```python
sorted_probs    = np.sort(probabilities)[::-1]
raw_confidence  = probabilities.max()
top2_gap        = sorted_probs[0] - sorted_probs[1]
calibrated_conf = min(raw_confidence, raw_confidence * min(1.0, top2_gap / 0.10 + 0.5))
```

Effect: when `top2_gap >= 0.05`, `calibrated_conf == raw_confidence`. Below that it is shrunk, down to `0.5 x p_max` at gap 0.

**Downstream thresholds this feeds:**

| Threshold | Location | Effect |
|---|---|---|
| `crop_confidence < 0.25` | `crop_detector.py` | tagged `classification_note = 'low_confidence (N%)'` — name kept, flag raised |
| `crop_confidence >= 0.25` | `performance_analyzer.py:145` | **unlocks the `crop_specific` scoring path** — ICAR reference curves, crop-specific health & yield |
| `CROP_VERIFY_MAX_CONFIDENCE = 0.55` | `crop_verification.py` | ceiling a *declared* crop can earn. Our model should beat this to be worth more than a self-report |

**Consequence: probability calibration is not optional.** 0.25 is a hard fork in the credit score. An uncalibrated 18-class softmax that emits 0.4 when it is right 25% of the time will route unreliable crop names into ICAR curve comparison, and a wrong crop name swings up to 45% of the index by weight (the exact concern `crop_verification.py` was written to address). Calibration is a first-class deliverable (§E.7), not a nicety.

Note also: with 18 classes, chance is 0.056, so the 0.25 gate is only ~4.5x chance. It is a **low** bar. Our reject rule (§E.8) should be stricter than the pipeline's.

## B.6 Interaction with declared-crop verification

`crop_verification.py` already checks a farmer-declared crop against observed phenology (duration band ±30%, peak NDVI as a floor) and caps confidence at 0.55. When the ML model is on, both signals exist. This document does **not** change that logic, but two things follow:

1. The registry crop hint is a legitimate **Bayesian prior** on the ML posterior — flagged "Major" in `04-ml-crop-classification.md` and still unbuilt. Out of scope for v1; the model must emit clean, calibrated probabilities so a prior can be composed later without retraining.
2. Every `INCONSISTENT` verdict is a **negative label** — a declared crop that demonstrably did not grow. That is the first real-world crop-label signal the system collects, and it is our retraining corpus (§I, Phase 9).

## B.7 Satellite acquisition parameters (fixed — must match at training time)

| Parameter | Value |
|---|---|
| Provider | GEE default (`SATELLITE_PROVIDER=gee`); STAC / Planetary Computer fallback |
| Lookback | `CONTINUOUS_LOOKBACK_YEARS = 3`, season-anchored (15 Jun / 15 Oct) |
| Bin width | `CONTINUOUS_SCENE_INTERVAL_DAYS = 10` -> ~110 bins per parcel |
| Bin fill | lowest-cloud scene per bin, else NaN placeholder |
| Cloud caps | kharif 80%, rabi 60%, continuous 70% |
| Cloud mask | Cloud Score+ (`cs_cdf` >= 0.60) when available |
| Resolution | all bands resampled to **10 m** (`TARGET_RESOLUTION_M = 10`) |
| Bands | B02 B03 B04 B05 B06 B07 B08 B8A B11 B12 |
| Min valid pixel ratio | `MIN_VALID_PIXEL_RATIO = 0.20` |
| Indices per scene | 16 (`SatelliteDataCollector.INDEX_KEYS`) |

**Any deviation between training-time and serving-time acquisition is a parity bug.** Do not write a bespoke GEE extractor without validating it against `SatelliteDataCollector` (§D.4).

---

# PART C — The task, stated precisely

## C.1 Formal statement

> Given a single detected crop cycle — a window `[sowing, harvest]` over one farm polygon, together with the >=5 real Sentinel-2 observations inside it (padded ±5 days) — predict the crop type from an 18-class set, and emit a **calibrated** probability distribution over those classes, such that thresholding at 0.25 yields a decision reliable enough to drive crop-specific credit scoring.

## C.2 Non-goals

- Detecting *whether* cultivation happened — `_detect_crop_temporal` + `land_cover_gate` own that
- Finding cycle boundaries — `CropCycleDetector` owns that
- Classifying non-crop land cover — `land_cover_gate` owns that
- Blocking the pipeline on failure — enrichment-only, always
- Yield estimation — Stage 6
- Predicting Sunflower, Cabbage, Papaya, Pomegranate — no training data

## C.3 Success criteria

Two numbers matter. The random-split number is diagnostic only and **must not be quoted as performance**.

| Metric | Protocol | Gate to ship |
|---|---|---|
| Balanced accuracy | **`GroupKFold` on 0.25° spatial blocks** | **>= 0.55** (~10x the 0.056 chance rate) |
| Macro F1 | same | >= 0.50 |
| Per-class recall | same | >= 0.30 for **every** class — no silently dead class |
| Expected Calibration Error | same, 10 bins | **<= 0.05** |
| Precision at `conf >= 0.25` | same | **>= 0.70** — the number that actually protects the credit score |
| Coverage at `conf >= 0.25` | same | >= 0.40 (below this the model adds little) |
| Balanced accuracy | Leave-one-ecoregion-out | report; no gate in v1 (expected weak) |
| Random 5-fold | diagnostic only | report *alongside* the blocked number to expose the leakage gap |

Rationale for 0.55: 18 spatially-confounded classes, features restricted to 3 parcel-mean indices on a normalized time axis with no duration signal. Published Sentinel-2 crop-type work reaches 0.80–0.90 — but with 10+ bands, absolute phenology, duration, and geographically stratified reference data. Promising 0.85 here would be dishonest. The relevant comparison is the 0.55 confidence cap on a self-reported crop: **the model must be worth more than a farmer's declaration.**

## C.4 Expected confusion structure

Plan for these; they drive the hierarchical design in §E.4.

| Confusable group | Why | Discriminator we have |
|---|---|---|
| Wheat <-> Mustard | Both rabi, ~130 d, both peak ~0.8 (called out verbatim in `crop_verification.py`) | NDRE, PSRI (mustard's yellow flowering phase), peak timing — **Tier 1 only** |
| Rice <-> Sugarcane (early) | Both flooded / high-NDMI at establishment | Duration (120 vs 330 d) — **Tier 1** |
| Banana <-> Sugarcane <-> Grapes | All perennial/long, evergreen, high sustained NDVI, no clean senescence | Amplitude of seasonal modulation; Grapes' pruning dip |
| Bajra <-> Jowar <-> Maize | All kharif cereals, 85–110 d, similar canopy | Peak NDVI magnitude; NDMI |
| Gram <-> Potato | Both rabi, 110–120 d | Potato's abrupt harvest drop |
| Soyabean <-> Groundnut | Both kharif legumes, 110 d, low spreading canopy | Very hard. Expect residual confusion |
| Onion <-> Chilli | Overlapping windows, both "both"-season | Chilli's 150 d vs Onion's 120 d — **Tier 1** |

Note how many discriminators are *duration* or *red-edge/senescence* — i.e. exactly what Tier 0 lacks. This is the quantitative argument for Tier 1.

---

# PART D — The missing bridge: from polygon labels to cycle features

This is the actual engineering work, and it does not exist in any form today.

## D.1 The problem

```
WE HAVE:   (polygon, Crop_Name, survey Date)
WE NEED:   (45-dim trajectory of a DETECTED CYCLE, Crop_Name)
```

`Date` is a survey snapshot, not a season boundary. So for each polygon we must (a) pull satellite history, (b) detect cycles, (c) decide **which detected cycle the label refers to**, and (d) reject the sample if no cycle can carry the label.

Step (c) is a label-attribution problem with no ground truth. Get it wrong and we train on mislabelled trajectories, which is worse than having no model.

## D.2 Extraction pipeline (reuse production code — do not reimplement)

```
FOR each de-duplicated polygon:
  1. area_ha = Area * 0.40468564224                 <- §A.4, non-negotiable
  2. centroid = polygon centroid (projected, not geographic)
  3. window   = anchored on Date (see D.3) — NOT the full 3-year lookback
  4. scenes   = SatelliteDataCollector.collect_historical_data(
                    latitude=lat, longitude=lon,
                    field_area_ha=area_ha,
                    geometry=polygon)
     -> assert geospatial_prep.geometry_source == "polygon"
        (if it says "point", QA rejected the polygon -> DROP the sample; do not
         train on buffer-derived features)
  5. gate     = land_cover_gate(scenes)             <- drop non-farmland
  6. cycles   = CropCycleDetector().detect(scenes)
  7. cycle    = attribute_label(cycles, Date, Crop_Name)   <- §D.3
  8. features = the SHARED extractor factored out of
                CropDetector._classify_crop_chronological  <- §B.3
  9. emit (features, Crop_Name, block_id, ecoregion, provenance)
```

**Non-negotiable:** step 8 must call **the same code** production calls. Factor the feature-building half of `_classify_crop_chronological` into a standalone, importable function used by both trainer and server. Every train/serve skew bug in ML crop mapping starts with two copies of the extractor.

Proposed refactor in `crop_detector.py`:

```python
def build_feature_dict(scenes, n_feat, indices) -> Dict[str, float]:
    """Pure function. The ONLY place features are constructed."""
    ...

def extractor_feature_names(n_feat, indices) -> List[str]:
    """Ground truth for the fail-fast check in B.3."""
    ...
```

`_classify_crop_chronological` then becomes `build_feature_dict(...)` -> vector -> predict.

## D.3 Label-to-cycle attribution rules

The survey `Date` tells us roughly *when* the crop was recorded. It does not tell us whether it marks sowing, mid-season or post-harvest. Given per-class degeneracy (Cotton: one date for 500 parcels), treat it as a **loose anchor with wide tolerance**.

**Observation window:** `[Date - 400 days, Date + 400 days]` — wide enough to contain the referenced cycle regardless of where in the season the survey landed, and to give `CropCycleDetector` the baseline context it needs. ~80 ten-day bins per parcel.

**Attribution:**

| Rule | Action |
|---|---|
| Exactly one detected cycle whose `[sowing, harvest]` contains `Date` | Accept, `attribution=contains` |
| No containing cycle, but exactly one cycle within ±90 days of `Date` | Accept, `attribution=nearest`, flagged for review |
| Multiple containing cycles | Accept the one whose duration best fits `CropGrowthCurves[crop]` band; `attribution=duration_matched` |
| `cycle_kind == 'perennial'` and crop in {Banana, Sugarcane, Grapes} | Accept the production year overlapping `Date`; `attribution=perennial_year` |
| `cycle_kind == 'perennial'` and crop is annual (or vice versa) | **Reject** — kind/label contradiction |
| No cycle within ±90 days | Reject, `no_cycle_detected` |
| `< 5` real scenes in the cycle window | Reject, `insufficient_observations` (matches `MIN_OBSERVATIONS_PER_SEASON`) |
| `land_cover_gate` says not farmland | Reject, `land_cover_fail` |
| `geometry_source != 'polygon'` | Reject, `geometry_qa_fail` |
| Cycle duration outside `CropGrowthCurves[crop]` band ±50% | Keep but tag `duration_outlier`; train both with and without and compare |

**Every rejection must be logged with its reason.** The rejection ledger is a primary deliverable: if 40% of Chilli is rejected for `no_cycle_detected`, that is not a data problem, it is a finding about `CropCycleDetector` on small southern-peninsula plots — and it must reach Stage 3's owners, not be silently dropped.

**Yield expectation.** Between geometry QA, land-cover gating, cycle detection and the >=5-scene rule, a **30–50% loss is realistic**. 9,000 -> roughly 4,500–6,300 usable samples, and the loss will not be uniform across classes. **Class balance will break.** Plan for it (`class_weight='balanced'`, and report per-class support in the model card).

## D.4 Cost, and how to keep it sane

| Quantity | Estimate |
|---|---|
| Parcels | 9,000 (8,891 after geometry de-dup) |
| Bins per parcel (±400 d @ 10 d) | ~80 |
| Parcel-scene reductions | **~712,000** |

Naive per-parcel `reduceRegion` looping will take days and will be throttled.

**Mitigations:**

1. **Narrow the window.** ±400 days, not the production 3-year lookback. Saves ~65%.
2. **Batch by GEE `FeatureCollection`.** Group parcels by Sentinel-2 tile and date bin, then one `reduceRegions` per (tile, bin) instead of one `reduceRegion` per (parcel, bin). Orders of magnitude fewer calls. This *is* a bespoke path, so it must be validated against `SatelliteDataCollector` output on a 200-parcel sample — **agreement to <= 0.005 NDVI** before trusting it at scale.
3. **Cache aggressively.** Persist raw per-parcel scene series to Parquet keyed by geometry hash. Extraction runs once; feature engineering and model iteration run hundreds of times against the cache.
4. **Stage the work.** Extract a stratified 1,800-parcel subset (100/crop) first. Validate the whole chain end to end, then scale.
5. **Checkpoint and resume.** Write per-parcel results incrementally. A 700k-call job will be interrupted.

## D.5 Intermediate artifacts

```
Crop_classification_model/
├── crop_classification_train_500.gpkg        # input (unchanged)
├── prepare_training_gpkg.py                  # existing; fix the ha/acre docstring
├── classification_model.md                   # this document
├── data/
│   ├── 00_parcels_clean.parquet              # de-duped, area_ha corrected, block_id, ecoregion
│   ├── 01_scenes_raw.parquet                 # (geom_hash, date, 16 indices) — THE expensive artifact
│   ├── 02_cycles.parquet                     # detected cycles + attribution verdict
│   ├── 03_features_tier0.parquet             # 45 features + label + block_id
│   ├── 03_features_tier1.parquet             # extended feature set
│   └── rejections.csv                        # every dropped sample + reason  <- deliverable
├── src/
│   ├── ingest.py            # gpkg -> 00, unit fix, dedup, blocks, ecoregion
│   ├── extract.py           # 00 -> 01 (batched GEE + validation vs collector)
│   ├── cycles.py            # 01 -> 02 (CropCycleDetector + attribution rules)
│   ├── features.py          # 02 -> 03 (imports the SHARED extractor)
│   ├── train.py             # 03 -> model + calibration + CV report
│   └── evaluate.py          # blocked CV, LOEO, calibration curves, confusion
└── models/
    ├── crop_classifier_model.joblib          # -> backend/Credit_assessment/models/
    └── MODEL_CARD.md
```

---

# PART E — Model architecture

## E.1 Two tiers, deliberately

| | **Tier 0 — Contract-Faithful** | **Tier 1 — Extended** |
|---|---|---|
| Features | exactly the 45 contract features | 45 + duration + season + extra indices + phenology |
| Pipeline change | **none** | `crop_detector.py` extractor + `PipelineConfig` |
| Deploy risk | drop in the joblib, flip the env flag | needs coordinated release + fail-fast guard |
| Expected blocked balanced acc. | 0.45 – 0.60 | 0.60 – 0.75 |
| Role | **ship first** — proves the chain, unblocks Stage 5/6 | the model that is actually good |

Ship Tier 0 first even if Tier 1 is obviously better. Tier 0 validates the extraction bridge, the attribution rules, the calibration layer and the deployment path with one moving part. Then change the feature space.

## E.2 Tier 0 — estimator

**Primary: gradient-boosted trees — `XGBoost`.**

Rationale: the 45 features are 3 smooth curves sampled at 15 points, so adjacent features are highly collinear. GBDTs handle collinearity, need no scaling, are robust on 4.5–6k samples, give native `predict_proba` and feature importances, and serialise cleanly through joblib.

> **Revised during implementation.** This section originally specified LightGBM. `backend/Credit_assessment/requirements.txt` already pins `xgboost==2.0.3` and `scikit-learn==1.6.1` and does **not** ship LightGBM. Adding a new runtime dependency in order to serve one model is a worse trade than using the gradient booster already in production — same model family, same behaviour on 45 collinear features, zero deployment risk. Implemented in `src/train.py::_make_model`.

```python
XGBClassifier(
    objective="multi:softprob", num_class=18,
    n_estimators=600, learning_rate=0.05,
    max_depth=6, min_child_weight=5,
    subsample=0.8, colsample_bytree=0.7,
    reg_lambda=1.0, tree_method="hist",
    random_state=42, n_jobs=-1,
)
```

Class balance is applied through `sample_weight` rather than `class_weight`, because the same vector also carries the spatial de-clustering term (`1/sqrt(n_block)`) that stops Tur's single 65%-of-class block from defining that class. See `src/train.py::_sample_weights`.

**Baselines to report alongside** (a strong baseline that beats the fancy model is a finding, not an embarrassment):

| Model | Purpose |
|---|---|
| Stratified-random dummy | the 0.056 floor |
| Multinomial logistic on the 45 features | is the problem linear? |
| Random Forest (500 trees, balanced) | GBDT sanity check |
| 1-NN with DTW on the NDVI curve | classic time-series baseline |
| **`CropGrowthCurves` template match** — nearest expected NDVI curve, no learning | **the most important baseline.** The pipeline already has these curves. If a zero-parameter template match matches the ML model, we do not need the ML model |

**Deliberately excluded from Tier 0:** deep sequence models (1D-CNN / LSTM / Transformer). 45 collinear features over ~5,000 spatially-clustered samples is not a regime where they win; they will overfit geography faster than GBDTs and are harder to calibrate. Revisit only at Tier 2 with more data.

## E.3 Tier 1 — extended feature space

Every addition below is available in production (all are already computed by `SatelliteDataCollector` or carried on `CropCycle`) and none reintroduces a leak. Requires the `crop_detector.py` extractor change **plus** the §B.3 fail-fast guard.

**(a) Duration and observation-quality scalars (5)** — the highest-value change

| Feature | Source | Why |
|---|---|---|
| `duration_days` | `CropCycle.duration_days` | separates Bajra (85 d) from Sugarcane (330 d). Currently invisible |
| `log_duration` | derived | linearises the 65 -> 365 d span |
| `n_scenes_real` | count | lets the model discount interpolated vectors |
| `observed_fraction` | `CropCycle.observed_fraction` | already tracked; distinguishes observation from reconstruction |
| `peak_observed` | `CropCycle.peak_observed` | a cycle whose peak sits on a gap-filled bin is an inference |

**(b) Extra indices on the same 15-point grid (+90 features)**

| Index | Discriminates |
|---|---|
| `NDRE_mean` | chlorophyll / N status — **the Wheat<->Mustard hope** |
| `PSRI_mean` | senescence timing — sharp-harvest crops (Potato) vs. gradual |
| `kNDVI_mean` | saturation-resistant, better at dense canopy (Sugarcane, Banana) |
| `LSWI_mean` | surface water — **rice flooding signature** |
| `GCVI_mean` | green chlorophyll, correlates with LAI / biomass |
| `NDVI_std` | within-parcel heterogeneity — orchard rows vs. broadcast field |

Total Tier 1: 45 + 5 + 90 = **140 features**. With ~5,000 samples that is acceptable for GBDTs with `colsample_bytree`, but run recursive feature elimination under blocked CV and keep the smallest set within 1 SE of the best.

**(c) Phenology scalars from `CropCycle.phenology` (Pillar-2 double-logistic, already computed)**
`peak_ndvi`, `baseline_ndvi`, `ndvi_rise`, `integral_ndvi_days`, `peak_evi`, `peak_ndmi`, plus green-up / senescence rates. These are *shape* summaries robust to the normalized-time distortion.

**(d) Agro-ecoregion — admit with care.** `infer_agro_ecoregion` is available at inference and encodes real agronomy (Rice does not grow in NW_SEMI_ARID the way it does in the Gangetic plain). But §A.5 shows it is nearly a class label *in this dataset*. **Rule: include it only as a one-hot, only if leave-one-ecoregion-out balanced accuracy does not fall when it is added.** If LOEO drops, the model is using it as an ID. Default to excluding it in v1.

## E.4 Hierarchical structure for confusable groups

A flat 18-way softmax spends most of its capacity on distinctions we cannot make and gets the easy ones wrong. Two-stage instead:

```
STAGE 1 — Cycle-kind / duration coarse class (4 groups, easy, high accuracy)
  |-- PERENNIAL_LONG   Banana, Sugarcane, Grapes            (270-365 d)
  |-- LONG_ANNUAL      Cotton, Tur, Tobacco, Chilli         (130-220 d)
  |-- MED_ANNUAL       Rice, Wheat, Mustard, Gram, Onion, Jowar, Maize,
  |                    Potato, Groundnut, Soyabean          (90-150 d)
  +-- SHORT_ANNUAL     Bajra                                (65-110 d)

STAGE 2 — One specialist classifier per coarse group
```

Benefits: each specialist sees a smaller, better-conditioned problem; the coarse stage is duration-driven and therefore accurate (Tier 1); and the coarse label alone is useful downstream even when the fine label is uncertain.

Final probability `P(crop) = P(group) x P(crop | group)` — a proper distribution, so it drops straight into the `all_probabilities` contract.

**Fallback semantics.** When Stage 2 is uncertain but Stage 1 is confident, we can still return the coarse group. That is worth real money downstream: `CropGrowthCurves` for the group is far better than `Unknown`. Recommend adding an optional `predicted_crop_group` + `group_confidence` to `season_results` — a purely additive schema change.

## E.5 Season as a structural prior, not a feature

`CropGrowthCurves.CROP_DURATIONS[crop]['season']` is real agronomic knowledge already in the codebase: Wheat/Mustard/Gram/Potato/Tobacco are rabi; Rice/Bajra/Groundnut/Soyabean/Tur/Cotton/Sugarcane are kharif; Jowar/Maize/Chilli/Onion are both; Banana is perennial.

Rather than feeding season as an input (leaky in this dataset — §A.3), apply it as an **explicit posterior mask** derived from the *cycle's own* dates:

```python
season  = season_from_cycle_dates(cycle.sowing_date, cycle.harvest_date)  # kharif|rabi|zaid
mask    = SEASON_COMPATIBILITY[season]      # 18-vector of 1.0 / SOFT_PENALTY
p_final = normalize(p_model * mask)
```

Why this and not a feature:

- **Auditable** — the mask is a table a lender or agronomist can read and challenge
- **Not learned** — cannot absorb the dataset's month/label degeneracy
- **Soft** — `SOFT_PENALTY ~ 0.15`, not 0. Real farmers plant rabi crops off-season, and our own dates are quantised to 10-day bins
- **Derived from the detected cycle**, so it is available at inference by construction

Validate: report blocked CV with and without the mask. Keep it only if it helps.

## E.6 The `Others` class

`CropGrowthCurves` defines `Others` (90/120/150 d, both seasons) and `_normalize_crop_name` maps to it, but we have no training data for it, and `prepare_training_gpkg.py` deliberately dropped every non-crop label.

**Decision: do not train an `Others` class in v1.** A learned catch-all with no examples becomes an attractor that swallows genuine predictions. Instead, "not one of our 18" is expressed by the **reject option** (§E.8) — the model abstains and the pipeline uses its existing crop-agnostic path, which is exactly what it was built to do. This also keeps the 4 untrained reference crops (Sunflower, Cabbage, Papaya, Pomegranate) honest: a Sunflower field should produce a low-confidence abstention, not a confident "Others".

## E.7 Calibration — a required component

Because 0.25 is a hard fork in the credit score (§B.5), raw GBDT probabilities are not acceptable. GBDTs with many trees are typically overconfident.

**Method:** multiclass calibration fitted on **held-out spatial blocks** — never on training folds, and never on a random split (a randomly-split calibration set inherits the spatial leak and will report a beautifully calibrated model that is not).

```
Outer GroupKFold (spatial blocks)
  |-- train folds        -> fit GBDT
  |-- calibration blocks -> fit temperature scaling / one-vs-rest isotonic
  +-- test blocks        -> report accuracy AND ECE
```

Prefer **temperature scaling** (one parameter, cannot distort the ranking, robust on small calibration sets) over per-class isotonic (18 monotone fits on ~1,000 samples will overfit). Report reliability diagrams per class, not just aggregate ECE.

**Interaction with the pipeline's own `top2_gap` shrinkage.** `crop_detector.py` applies its own transform *after* ours. Two rules:

1. Calibrate the raw `predict_proba`, so the pipeline's shrinkage operates on well-calibrated inputs.
2. Evaluate the **composed** confidence — `min(p, p * min(1, gap/0.1 + 0.5))` — because that is the number compared against 0.25. Report precision/coverage at 0.25 on the composed value.

## E.8 Reject option — abstain rather than mislead

Our own threshold should be stricter than the pipeline's 0.25 (only ~4.5x chance with 18 classes).

```python
ABSTAIN if:
    calibrated_p_max < 0.35          # our threshold, above the pipeline's 0.25
 or top2_gap        < 0.10           # genuinely ambiguous between two crops
 or n_scenes_real   < 8              # 5 satisfies the pipeline gate, but 8 is
                                     # where an interpolated 15-point grid stops
                                     # being mostly synthetic
```

On abstention, return `predicted_crop = None` with the full probability vector and `classification_note = 'abstained: <reason>'`. `analyze_cycles` already handles `predicted_crop = None` — the cycle survives, `cultivation_signal` carries scoring, and `performance_analyzer` takes the crop-agnostic path. **Zero pipeline change required to abstain.** This is the single most important safety property of the design.

## E.9 Handling the geography problem directly

Beyond blocked validation, three mitigations:

1. **Spatially-aware sample weights.** Down-weight parcels in dense blocks (`w = 1/sqrt(n_block)`) so Tur's 324-parcel block does not dominate its class.
2. **Geographic augmentation is not available.** We cannot generate new locations. Accept the ceiling and state it in the model card.
3. **Adversarial validation.** Train a classifier to predict *ecoregion* from the 45 features. If it succeeds easily (it will), the AUC quantifies how much geography the features carry — a number for the model card and a check on whether feature changes reduce the dependence.

---

# PART F — Validation protocol

## F.1 Splitting — the rules

```python
# 1. De-duplicate on geometry hash            (109 duplicates)
# 2. Assign spatial blocks
block_id = f"{floor(lat/0.25)}_{floor(lon/0.25)}"      # 280 blocks
# 3. Primary CV
GroupKFold(n_splits=5, groups=block_id)
# 4. Nested calibration split — also grouped by block
# 5. Secondary stress test
LeaveOneGroupOut(groups=ecoregion)                      # 5-6 folds
# 6. Diagnostic only, clearly labelled
StratifiedKFold(n_splits=5)
```

**Never** `train_test_split(X, y, stratify=y)`. It is wrong here and will produce a number someone quotes in a deck.

**Fold quality check.** With Chilli in 6 blocks and Tobacco in 7, a 5-fold block split can leave a class almost absent from a training fold. Before training, assert every class has >= 20 training samples in every fold; if not, fall back to `StratifiedGroupKFold` and log the achieved per-class support per fold.

## F.2 Reporting

Mandatory in every report:

1. **Both numbers side by side** — blocked and random. The gap is the leakage measurement and the most informative single line in the report.
2. **18x18 confusion matrix**, row-normalised, from the blocked folds.
3. **Per-class precision / recall / F1 / support.** No aggregate-only reporting.
4. **Reliability diagram** + ECE, on the composed confidence.
5. **Precision–coverage curve** with the 0.25 operating point marked.
6. **Feature importance** (gain + permutation on held-out blocks). If `NDVI_t01` dominates, the model is reading sowing date, i.e. season, i.e. label.
7. **Rejection ledger summary** from §D.3, by class and reason.
8. **Adversarial-validation AUC** for ecoregion.
9. **`CropGrowthCurves` template-match baseline** on the same folds.

## F.3 Ablations

| Ablation | Question it answers |
|---|---|
| Tier 0 vs Tier 1 features | is the extractor change worth the deployment risk? |
| With / without duration | quantifies the cost of normalized time — the headline finding |
| With / without season mask | is the prior helping or leaking? |
| With / without ecoregion one-hot | is the model using geography as an ID? (watch LOEO) |
| Flat 18-way vs hierarchical | does the two-stage design pay off? |
| With / without `duration_outlier` samples | are attribution errors hurting? |
| 45 features vs top-k by RFE | can we shrink without loss? |
| Random split vs blocked | the leakage magnitude |

---

# PART G — Feature whitelist / blacklist

## G.1 Banned — never a model input

| Feature | Reason |
|---|---|
| `sample_id` | identifier |
| `source_file` | near-perfect label proxy (9 of 18 classes come from a dedicated file) |
| `Date` (raw) | survey snapshot; degenerate per class (§A.3) |
| `year` | Cotton 100% 2023, Soyabean 100% 2023, Gram 99.6% 2022 |
| `month` (raw) | Wheat/Mustard/Onion 100% January; Cotton 100% July |
| `Area` / `area_ha` | per-crop selection artifact (top-500-by-area) (§A.4) |
| `lat`, `lon`, centroid coords | 83% of blocks are single-crop — direct memorisation |
| Anything derived from the above | e.g. day-of-year, "days since 2022-01-01", area percentile-within-class |

## G.2 Allowed

| Feature | Tier | Available at inference? |
|---|---|---|
| `NDVI_t01…t15`, `EVI_t01…t15`, `NDMI_t01…t15` | 0 | yes — contract |
| `duration_days`, `log_duration` | 1 | yes — `CropCycle.duration_days` |
| `n_scenes_real`, `observed_fraction`, `peak_observed` | 1 | yes — `CropCycle` |
| `NDRE`, `PSRI`, `kNDVI`, `LSWI`, `GCVI`, `NDVI_std` on the 15-point grid | 1 | yes — `INDEX_KEYS` |
| `peak_ndvi`, `baseline_ndvi`, `ndvi_rise`, `integral_ndvi_days`, `peak_evi`, `peak_ndmi` | 1 | yes — `CropCycle` |
| Phenology scalars (green-up / senescence rate, double-logistic params) | 1 | yes — `CropCycle.phenology` |
| Season **mask** derived from *cycle* dates | 0/1 | yes — posterior prior, not an input (§E.5) |
| `agro_ecoregion` one-hot | 1 | conditional — only if LOEO does not degrade (§E.3d) |

## G.3 Grouping / weighting columns — never features

`block_id`, `ecoregion`, `geom_hash`, `attribution` verdict, `rejection_reason`. Used for splitting, weighting and auditing only.

---

# PART H — Deliverables

## H.1 The joblib

```python
joblib.dump({
    # -- contract keys (required by CropDetector.__init__) --
    "model":              calibrated_estimator,   # predict + predict_proba
    "label_encoder":      label_encoder,          # inverse_transform -> CropGrowthCurves keys
    "feature_names":      feature_names,          # ordered
    "crop_names":         list(label_encoder.classes_),   # MUST match predict_proba column order
    # -- provenance (new; enables the fail-fast guard) --
    "extractor_version":  "tier0_v1",
    "ml_feature_scenes":  15,
    "ml_feature_indices": ["NDVI_mean", "EVI_mean", "NDMI_mean"],
    "trained_at":         "<iso8601>",
    "training_data":      "crop_classification_train_500.gpkg",
    "n_train":            n,
    "sklearn_version":    sklearn.__version__,
    "lightgbm_version":   lightgbm.__version__,
    "cv_protocol":        "GroupKFold(5) on 0.25deg spatial blocks",
    "metrics":            {...},                  # the §C.3 table
    "abstain_rule":       {"p_min": 0.35, "gap_min": 0.10, "n_scenes_min": 8},
    "season_mask":        {...} or None,
}, "models/crop_classifier_model.joblib")
```

**Pin `scikit-learn` and `lightgbm` in `requirements.txt`.** joblib pickles are version-fragile; an unpinned upgrade silently changes or breaks predictions. This is already flagged as "Medium" in `04-ml-crop-classification.md`.

## H.2 `MODEL_CARD.md`

Must state, prominently and without softening:

- The 18 classes, and the 5 reference crops the model **cannot** predict
- **Blocked** balanced accuracy as the headline; random-split accuracy shown only as the leakage diagnostic
- Per-class recall, including the worst class
- The geographic footprint (69–88 °E, 12–31 °N) and that **generalisation outside sampled districts is unvalidated**
- The temporal footprint (2022–2024) and that no year is a feature
- Known confusions (Wheat<->Mustard, Soyabean<->Groundnut, the perennial group)
- The abstain rule and measured coverage
- The four dataset defects from §0 and how each was handled
- Adversarial-validation AUC for ecoregion — how much geography the features carry
- **That the training labels are unverified self-reported registry values**, so the model's ceiling is the label noise it was trained on

## H.3 Code changes to the pipeline

| File | Change | Tier |
|---|---|---|
| `crop_analysis/crop_detector.py` | factor out `build_feature_dict()` / `extractor_feature_names()` — the shared extractor | 0 |
| `crop_analysis/crop_detector.py` | fail-fast `feature_names` subset check + `extractor_version` assert | 0 |
| `crop_analysis/crop_detector.py` | apply the abstain rule; emit `classification_note='abstained: …'` | 0 |
| `requirements.txt` | pin `scikit-learn`, `lightgbm` | 0 |
| `crop_analysis/crop_detector.py` | extend the extractor with duration / quality / extra indices | 1 |
| `config.py` | `ML_FEATURE_INDICES` += NDRE, PSRI, kNDVI, LSWI, GCVI; add `ML_INCLUDE_DURATION` | 1 |
| `crop_analysis/crop_detector.py` | season posterior mask | 1 |
| `crop_analysis/crop_detector.py` | `predicted_crop_group` + `group_confidence` (hierarchical) | 1 |
| `docs/codebase/backend/04-ml-crop-classification.md` | update "Missing / next" | 0 |

Note `_build_unclassified_analysis` and the whole `enable_crop_classification=False` path stay untouched. It remains the fallback, and the default, until Tier 0 clears the §C.3 gates.

---

# PART I — Implementation plan

| Phase | Work | Output | Gate to proceed |
|---|---|---|---|
| **1. Ingest & audit** | De-dup (109), acres->ha, block_id, ecoregion; reproduce every number in Part A | `00_parcels_clean.parquet` + audit notebook | numbers reproduce |
| **2. Extraction spike** | 1,800-parcel stratified subset (100/crop). Batched GEE path **validated against `SatelliteDataCollector`** on 200 parcels | `01_scenes_raw.parquet` (subset) | NDVI agreement <= 0.005; `geometry_source == "polygon"` for >= 95% |
| **3. Cycles & attribution** | Run `CropCycleDetector`, apply §D.3 rules, build the rejection ledger | `02_cycles.parquet`, `rejections.csv` | >= 60% attribution rate, >= 40 samples/class on the subset |
| **4. Tier 0 model** | Shared extractor refactor, 45 features, GBDT + baselines, blocked CV, temperature scaling, abstain rule | `03_features_tier0.parquet`, joblib, model card | **§C.3 gates met**, incl. precision >= 0.70 at conf >= 0.25 |
| **5. Full extraction** | Scale to all 8,891 parcels; retrain Tier 0 | full parquet set, Tier 0 v2 | metrics hold or improve |
| **6. Tier 1** | Extended features + fail-fast guard + season mask + hierarchical heads; full ablation suite | Tier 1 joblib + report | beats Tier 0 on **blocked** CV by >= 5 points balanced accuracy |
| **7. Shadow deploy** | `ENABLE_CROP_CLASSIFICATION=true` on a staging cohort. Log predictions; **do not** let them drive scores. Compare against `crop_verification.py` verdicts and registry hints | agreement report | agreement with `CONSISTENT` verdicts >= 0.60; no crash across N runs |
| **8. Production** | Enable per-region behind the `require_classification` job flag | — | — |
| **9. Feedback loop** | Harvest `INCONSISTENT` verification verdicts as negative labels; quarterly retrain | growing real-world corpus | — |

**Phase 2 is the risk concentration.** If batched GEE extraction cannot be made to agree with `SatelliteDataCollector`, the honest fallback is to accept the slower per-parcel path on a reduced subset (e.g. 200/crop = 3,600 parcels) rather than train on features production will never see.

---

# PART J — Risks and open questions

## J.1 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Blocked accuracy lands below 0.50, i.e. barely above the 0.55 confidence cap on a self-report | **High** | Ship Tier 1 with duration before judging. If still below, keep classification off and say so — a bad crop name is worse than none. The hierarchical coarse label may still be shippable on its own |
| Attribution errors put wrong labels on trajectories | **High** | Conservative rules, mandatory rejection ledger, `duration_outlier` ablation, manual review of 100 random attributions |
| Train/serve skew via a duplicated extractor | **High** | Single shared function (§D.2) + `extractor_version` assert + a parity test that runs both paths on 50 parcels and asserts bit-identical vectors |
| Silent zero-fill on a Tier-1 model deployed without the extractor change | **High** | Fail-fast guard in `__init__` (§B.3). **Non-negotiable** |
| GEE quota / cost overrun on ~712k reductions | Medium | Narrow window, batch by tile, cache to Parquet, checkpoint, staged rollout |
| Class imbalance after 30–50% attribution loss | Medium | `class_weight='balanced'`, per-class support in the model card, minimum-support assertions per fold |
| Labels themselves are unverified self-reports | Medium | Stated in the model card as the accuracy ceiling. Phase 9 feedback loop is the long-term answer |
| Miscalibration routes bad crop names into ICAR curves | Medium | Temperature scaling on held-out blocks; evaluate the **composed** confidence at 0.25; stricter internal abstain threshold |
| `CropCycleDetector` under-detects on small southern-peninsula plots (Chilli, Tobacco) | Medium | Rejection ledger by class surfaces it; report to Stage 3 rather than papering over it |
| Perennials (Banana / Sugarcane / Grapes) have no annual cycle | Low | `cycle_kind` branch in attribution; `PERENNIAL_LONG` coarse group |

## J.2 Open questions

1. **What does `Date` actually mean?** Survey date, ingest date, or something else? The whole ±400-day attribution window is a hedge against not knowing. If the source system can tell us, attribution gets sharply better. **Worth asking before Phase 3.**
2. **Can we get the full source files?** `Others_16000.gpkg` and the nine `Elai_*_10000.gpkg` files hold ~106,000 rows; we sampled 9,000 with an area-ranked rule that created defect §A.4. Re-sampling **randomly, spatially stratified** from the full files would remove the Area artifact *and* widen geographic coverage. **This is the single highest-leverage change available to us** — larger than any model choice in Part E.
3. **Are the non-crop labels recoverable?** `NON_CROP_LABELS` (Forest, Built up, Barren, Water) were dropped. They are not needed for the classifier (`land_cover_gate` handles that), but they would let us *validate* the gate — separate work, worth noting.
4. **Sunflower, Cabbage, Papaya, Pomegranate** — do the source files contain them below the 500-sample threshold? Even 150 samples each would be worth a `Tier1+` extension.
5. **Should the registry crop hint enter as a Bayesian prior?** Flagged "Major" in the existing doc. Out of v1 scope, but the model must emit clean calibrated probabilities so it can be composed later. Note the trap: a prior on a self-report makes the model agree with the registry, which then makes the `INCONSISTENT` feedback signal (§B.6) stop working. Sequence matters — build the feedback loop first.
6. **Sentinel-1 SAR.** The collector already has an RVI path and `VS_mean` fusion. SAR is cloud-immune and strong for rice (flooding) and structural crops. Not in `ML_FEATURE_INDICES` today. A Tier 2 candidate, contingent on SAR coverage in the training window.

---

---

# PART K — Implementation log

What was actually built, and every place reality differed from the plan above.
Code lives in `Crop_classification_model/src/`.

## K.1 What runs

| Stage | Module | Output | Status |
|---|---|---|---|
| 0 | `src/_bootstrap.py` | sys.path, `.env`, GEE init, native DLL registration | done |
| 1 | `src/ingest.py` | `data/00_parcels_clean.parquet` | **done — every Part A number reproduced** |
| 2 | `src/extract.py` | `data/01_scenes_raw.parquet` (+ `data/shards/`) | done |
| 2-gate | `src/validate_parity.py` | `reports/parity_validation.csv` | **done — gate passed** |
| 3 | `src/cycles.py` | `data/02_cycles.parquet`, `data/rejections.csv` | done |
| 4a | `src/features.py` | `data/03_features_tier{0,1}.parquet` | done |
| 4b | `src/train.py` | `models/crop_classifier_model.joblib`, `reports/cv_report_tier*.json` | done |
| 4c | `src/evaluate.py` | `reports/evaluation_tier*.md` + PNGs | done |
| — | `src/smoke_test.py` | 17-check synthetic end-to-end test | **done — 17/17 pass** |

Order of operations:

```bash
python -m src.ingest
python -m src.extract --validate 12
python -m src.extract --workers 4 --worker 0
python -m src.cycles
python -m src.features
python -m src.train --tier 0
python -m src.evaluate --tier 0
python -m src.train --tier 0 --export
```

## K.2 Pipeline changes shipped (Tier 0)

All in `backend/Credit_assessment/crop_analysis/crop_detector.py`. The existing
314-test suite passes unchanged.

1. **Shared extractor.** `build_feature_dict()` and `extractor_feature_names()`
   are now module-level functions, and `_classify_crop_chronological` calls
   them. `src/features.py` imports the same two functions, so train/serve parity
   is structural rather than a convention someone has to remember.
   `EXTRACTOR_VERSION = "tier0_v1"`.
2. **Fail-fast guard** in `CropDetector.__init__` against three ways a bundle can
   be silently wrong: a feature the extractor cannot produce (the zero-fill
   trap), an `extractor_version` mismatch, and `crop_names` not matching
   `label_encoder.classes_` — which would attach every probability to the wrong
   crop.
3. **Abstain rule** — `p_min 0.35 / gap_min 0.10 / n_scenes_min 8`, stricter than
   the pipeline's own 0.25 gate. On abstention `_classify_crop_chronological`
   returns `crop=None` plus the full probability vector and
   `classification_note = 'abstained: <reason>'`; the argmax is kept as
   `top_crop_unreliable` for audit only. **No other pipeline code changed** —
   `analyze_cycles` already handles a null crop, so the cycle survives and
   `performance_analyzer` takes its crop-agnostic path.

## K.3 Findings that changed the design

### K.3.1 The reduction grid — a silent, systematic parity break

The first parity run compared the batched extractor against
`SatelliteDataCollector` over 331 bins: **not one bin matched.** Median
difference 0.0027 NDVI, p95 0.0165, worst 0.209.

`ee.ImageCollection.mosaic()` returns an image whose default projection is
**EPSG:4326 at a 111 km nominal scale**, not the Sentinel-2 tile's native UTM.
Reducing that at `scale=10` rasterises on a lat/lon grid instead of the sensor
grid. Measured on a single parcel and bin:

| Variant | NDVI_mean | delta vs production |
|---|---|---|
| production `reduceRegion` on the native scene | 0.346007 | — |
| mosaic, default projection | 0.346924 | 0.000917 |
| `reduceRegions` on the native scene | 0.346007 | **0.000000** |
| mosaic + `setDefaultProjection(native)` | 0.346007 | **0.000000** |
| mosaic + client-built `EPSG:32642 @ 10 m` | 0.346007 | **0.000000** |

Fix: requests are chunked by UTM zone and the mosaic grid is pinned with
`setDefaultProjection(ee.Projection(epsg).atScale(10))`. Re-run over 687 bins
across 12 crops:

- **96.2% of bins agree to within 1e-7** (float serialisation noise)
- p95 difference **4e-9**, against a gate of 0.005
- 2.8% differ by more than 0.005 — the intended mosaic-fill bins, where the
  clearest scene is masked over the parcel and we recover a pixel production
  discards. Separately verified on a 3-scene bin that the mosaic introduces
  **no** error when the clearest scene does cover the parcel (0.000000).

This class of bug deserves naming: no error, no warning, plausible numbers.
Without a like-for-like parity harness it ships as a quietly degraded model.

### K.3.2 Production reduces over the bounding box, not the polygon

The GEE branch builds `aoi = ee.Geometry.Rectangle(bbox)`. Even when a polygon
passes QA, index means come from its **envelope**. Measured over 8,891 parcels:

| Statistic | bbox area / polygon area |
|---|---|
| p5 | 1.07 |
| median | **1.49** |
| p95 | 2.50 |
| max | 7.71 |

A median of only **67%** of the pixels production averages are inside the field;
40% at the 5th percentile. Worst affected: Potato 1.85x, Bajra 1.77x,
Groundnut 1.69x.

Handling: `bbox` is what the shipped model trains on — parity with production as
it exists beats cleaner features the server will never produce. A 25% sample is
*also* reduced over the true polygon (`--poly-fraction`) so the dilution can be
quantified and a Stage-2 change argued from evidence. **This is a Stage-2
finding, not a classifier finding**, and it affects cycle detection and the
land-cover gate as much as classification.

### K.3.3 Extraction architecture is benchmark-driven, not guessed

The first implementation — per-scene reductions, few parcels, many bins per
request — did not finish 36 parcels in 10 minutes. It was request-latency bound.
Benchmarking showed throughput is ~50 feature-reductions/second essentially
regardless of grouping, so the only lever is **minimising requests**.

Rewritten bin-centric: one request per (10-day bin x UTM zone x ~3,000-feature
chunk), reducing thousands of parcels at once. 8,891 parcels x ~81 bins x 1.25
geometries = **893,017 reductions in 752 requests**. Four parallel workers give
~1.6x over one (8.2 vs 5.3 requests/min) with negligible 503 throttling.

Two further requirements surfaced only under load:

- **atomic shard writes** (`.tmp` then `replace`) — parallel workers and the
  consolidator read the shard directory concurrently, and a half-written parquet
  is unreadable;
- a **masked blank base image** in every mosaic. An empty 10-day bin yields a
  band-less mosaic and `reduceRegions` raises "Image has no bands", which killed
  the whole server-side bin loop rather than just that bin.

### K.3.4 `Area` is acres — and the consequence is worse than a unit label

Confirmed at ingest: median m2/`Area` = **4046.9** (1 acre = 4046.86 m2).

The damage is not cosmetic. `validate_farm_geometry` rejects when
`true_ha / field_area_ha` falls outside `[0.5, 2.0]`; the unconverted ratio is a
constant **0.405**, so *every* polygon would be rejected as
`area_ratio_out_of_range:0.405` and the collector would silently fall back to
point + adaptive buffer. Training features would come from circles while
production features come from parcels, with no error message anywhere.
`src/ingest.py` converts once and asserts the post-fix ratio lands inside the
accepted band (measured: min 0.722, median 1.000). `src/extract.py` additionally
asserts `geometry_source == "polygon"` and drops any parcel that fell back.

### K.3.5 All 109 duplicate geometries are Groundnut

De-duplication took 9,000 to 8,891 rows, and every removed row was Groundnut
(500 to 391). This is a source-data defect rather than a sampling artifact, and
it leaves Groundnut starting from a 22% smaller base than every other class —
worth raising with whoever produced `Others_16000.gpkg`.

### K.3.6 A native crash no `try/except` could catch

Running `.conda/python.exe` without activating the environment leaves conda's
LAPACK DLLs off the search path. `import scipy` still succeeds, but the first
`scipy.optimize.curve_fit` — inside `CropCycleDetector._fit_double_logistic` —
died with Windows fatal exception `0xc06d007f` (delay-load failure): no
traceback, exit code 127, unmaskable. Over a multi-hour run it would have
vanished mid-way with nothing to debug.

`_bootstrap._register_conda_dlls()` now registers `Library/bin`,
`Library/mingw-w64/bin`, `Library/usr/bin` and `DLLs` through
`os.add_dll_directory` at import; a no-op on Linux/macOS and on non-conda
interpreters. Note that the pipeline's own 314-test suite passes *without* this
fix, because those tests never reach `curve_fit` — the suite gave false
assurance about phenology fitting.

### K.3.7 One bin grid, or none — a misalignment that looks exactly like missing data

Extraction anchors its 10-day grid on the **earliest `win_start` across all
parcels** (2020-12-06), which is what makes bins comparable between parcels.
`cycles._build_continuous` was regenerating bins from each parcel's **own**
`win_start`. The two grids coincide only when the offset happens to be a
multiple of 10 days, so most lookups missed.

The symptom was indistinguishable from having no imagery: **291 of 300 parcels
rejected as `no_valid_observations`** while the shards demonstrably held ~15
valid observations each.

Two fixes, both necessary:

1. `extract.global_bin_anchor()` / `bins_for_window()` are now the single grid
   definition, imported by every consumer. `bins_for_window` snaps the window
   start *down* onto the shared grid.
2. An **assertion that distinguishes the two failure modes**: if a parcel yields
   zero usable bins while its shard rows contain finite NDVI, `_build_continuous`
   raises with both grids printed, instead of quietly returning `None`.
   Consolidation additionally asserts every `bin_start` is congruent to the
   anchor mod 10.

The same investigation turned up a second, related trap: **13 leftover shards
from the abandoned per-cell architecture** were still in `data/shards/`, and
those *had* been written on per-parcel grids. Mixed in, they put bins on six
different grids at once. Consolidation now reads only files matching the current
shard-name pattern and warns about the rest. Lesson: shard directories need to
be keyed by the architecture that wrote them.

### K.3.8 A hard `cycle_kind` rule would have deleted three classes

D.3 originally rejected any cycle whose `cycle_kind` contradicted the label's
expected kind — perennial label needs a perennial cycle. On real data that rule
cost **Banana 44 of its usable parcels and left it with exactly zero**, and the
same logic applied to Sugarcane and Grapes. A rule intended as a sanity check
would have silently removed three of eighteen classes.

The reasoning was wrong. A 330-day banana stand that `CropCycleDetector` happens
to label `annual` is still the cycle the label refers to; the disagreement is
about the detector's kind heuristic, not about which cycle grew.

`cycle_kind` is now a **soft preference**: kind-matching cycles are considered
first, then everything else, and a fallback match is tagged
`kindmismatch_<mode>` with a `kind_mismatch` boolean carried through as metadata
(never as a feature) so it can be reported and ablated. Banana went from 0 to 9
attributed parcels on the same partial data, median duration 186 days, and
`cycle_kind_mismatch` disappeared as a rejection reason.

### K.3.9 XGBoost refuses the label sets blocked CV actually produces

XGBoost 2.0.3 rejects non-contiguous labels outright:

```
Invalid classes inferred from unique values of `y`.
Expected: [0 1 2 3], got [0 1 3 5]
```

This is not a corner case. Chilli occupies 6 spatial blocks and Tobacco 7, so a
5-fold `GroupKFold` will hand some fold a training set missing at least one
class. Verified that it fails both with an explicit `num_class` and with the
inferred one.

`ContiguousLabelXGB` (module-level, therefore picklable) remaps labels to a
contiguous range inside `fit` and exposes the **original** labels as `classes_`
in `predict_proba` column order, so the existing `_expand()` re-embeds each
fold's probabilities into the full 18-class space. `CalibratedBundle` was
hardened the same way: it pads to full width rather than letting an absent class
shift every subsequent column — which would have mislabelled every probability
`CropDetector` zips against `crop_names`.

### K.3.10 The tier-1 extractor, and a bundle that could not be deployed

Tier 1's measured advantage (+10.7 points, and it fixes the precision gate)
justified extending the live extractor, so `crop_detector.py` now builds the six
extra index grids and twelve cycle scalars as well. Verified at **exact
147-feature parity** with the training matrix: nothing missing, nothing spare.

One genuinely new requirement: tier 1 needs the `CropCycle`, not just its
scenes. `analyze_cycles` has it and passes it through. The legacy
`analyze_cropping_pattern` season path does not — so a cycle-dependent model
**abstains** there with `cycle_metadata_unavailable` rather than zero-filling
duration and every phenology scalar and returning a confident answer.

`EXTRACTOR_VERSION` moved to `tier1_v1`, with `COMPATIBLE_EXTRACTOR_VERSIONS =
{tier0_v1, tier1_v1}` because the 45 tier-0 features are computed identically —
a tier-0 bundle stays valid.

**And then the export could not be loaded at all:**

```
AttributeError: Can't get attribute 'CalibratedBundle' on <module '__main__'>
```

A joblib pickle records each custom class's **import path**, not its code. The
wrapper classes were defined in `src/train.py`, which runs as
`python -m src.train`, so they were pickled as `__main__.CalibratedBundle`. Worse,
even with the module name corrected, `Crop_classification_model/src/` **is not
deployed** — production ships only `backend/Credit_assessment/`. The model would
have been dead on arrival, and the failure would have surfaced at first
inference rather than at build time.

Fix: `TemperatureScaler`, `ContiguousLabelXGB` and `CalibratedBundle` now live in
`backend/Credit_assessment/crop_analysis/model_bundle.py`, inside the deployed
package. The trainer imports them from there, so the pickle records
`crop_analysis.model_bundle.*`. `xgboost` is imported lazily inside `fit`, so
inference needs only the already-pinned runtime.

This was the **third** bug of the same family, after the reduction-grid
projection (K.3.1) and the bin-grid misalignment (K.3.7): a train/serve
asymmetry that raises nothing until it matters. `tests/test_crop_classifier_bundle.py`
now guards it with eight checks that import **only** from the deployed package —
including that the pickled model is not from `__main__` or `src.*`, that every
predicted class resolves in `CropGrowthCurves`, that `crop_names` matches the
encoder's ordering, that a cycle-dependent bundle abstains without a cycle, and
that the bundle's recorded metrics are the blocked ones. Backend suite: 322 pass.

### K.3.11 A baseline comparison that flattered the baselines

`_baselines` fitted on the whole training fold while the candidate estimators
fitted on the smaller subset that excludes the calibration blocks — roughly 25%
more data for the baselines. That made logistic regression look like it beat the
selected model (0.7646 vs 0.7492) when the like-for-like comparison had it
losing (0.7448 vs 0.7492). Both now fit on the identical subset.

A comparison that is not apples-to-apples is worse than no comparison: it would
have argued for shipping the wrong estimator.

## K.4 Verification before real data existed

`src/smoke_test.py` drives the whole downstream chain on synthetic
double-logistic phenology, so plumbing bugs surface in seconds instead of after
a multi-hour extraction. **17/17 checks pass**, covering the 45-feature contract
and its naming, cycle detection and attribution (59 of 72 synthetic parcels
attributed), blocked CV with calibration and baselines, the exact
`min(p, p*min(1, gap/0.1+0.5))` confidence formula, all four fail-fast refusals,
and abstention arriving as a null crop with an intact probability vector.

It proves the code is correct. It says nothing about model quality — only real
imagery can.

## K.5 Still open

- Full extraction is the long pole; `--resume` plus sharding makes it
  restartable and parallel.
- Tier 1 needs the matching `crop_detector.py` extractor extension before its
  bundle can be exported. `src/train.py::_export` **refuses** to write a tier-1
  bundle whose features the live extractor cannot produce, rather than letting
  production zero-fill them.
- The Stage-2 bbox-to-polygon change (K.3.2) is deliberately out of scope here:
  it moves cycle detection and land-cover gating for every parcel, a far larger
  blast radius than the classifier.

---

# PART L — Results

Everything below is measured on the complete dataset: 752/752 extraction requests
(zero hard failures), 893,017 parcel-bin reductions, 7,907 attributed cycles.

## L.1 Attribution — 88.9%

7,907 of 8,891 parcels carried a label onto a detected cycle, against the 60%
Phase-3 gate. All 18 classes survived (minimum Soyabean 252, Chilli 309).

| Rejection reason | Share |
|---|---|
| `cycle_too_few_scenes` | 4.9% |
| `no_cycle_near_survey_date` | 4.7% |
| `too_few_core_pixels` | 1.4% |
| `no_cycle_detected` | **0.02%** |

`CropCycleDetector` failed to find any cycle on **2 parcels out of 8,891**. The
earlier concern that Stage 3 might be the binding constraint is settled: it is
not.

Detected durations independently reproduce the ICAR reference bands the
attribution never saw as a constraint — Bajra 97 d (65-110), Potato 94 d
(90-130), Rice 111 d (100-150), Wheat 120 d (110-150), Gram 120 d (100-140),
Cotton 196 d (150-210). Banana (178 d) and Sugarcane (174 d) fall well under
their 270-365 d bands: the detector splits a continuous perennial stand into
shorter cycles, which is also the bulk of the 16.4% `kind_mismatch` rate. Had
D.3's original hard `cycle_kind` reject survived, those two classes plus Grapes
would have been deleted entirely (see K.3.8).

## L.2 Tier 0 vs Tier 1

| Metric (blocked GroupKFold) | Tier 0 (45 feat) | Tier 1 (147 feat) | Gate |
|---|---|---|---|
| Balanced accuracy | 0.6420 | **0.7492** | ≥ 0.55 PASS |
| Macro F1 | 0.6323 | **0.7348** | ≥ 0.50 PASS |
| ECE | 0.0218 | **0.0109** | ≤ 0.05 PASS |
| Precision @ 0.25 | 0.666 FAIL | **0.765** | ≥ 0.70 PASS |
| Coverage @ 0.25 | 0.928 | 0.959 | ≥ 0.40 PASS |
| Min class recall | 0.230 (Gram) | 0.214 (Gram) | ≥ 0.30 **FAIL** |
| Random (diagnostic) | 0.7798 | 0.8825 | — |
| **Leakage gap** | +0.1378 | **+0.1333** | — |
| Leave-one-ecoregion-out | 0.1618 | 0.1898 | — |

**B.4 was right.** Restoring cycle duration and the richer index set is worth
**+10.7 points** of blocked balanced accuracy and converts the failing precision
gate into a passing one. `log_duration` and `duration_days` both land in the top
ten features by gain, and the top three are all tier-1 additions (`LSWI_t14`,
`PSRI_t03`, `LSWI_t11`).

Tier 1 is shipped, which required extending the live extractor (K.3.10).

## L.3 The leakage gap is the headline finding

A random split reports **0.8825**. The honest blocked number is **0.7492**. Had
the project used the conventional `train_test_split(..., stratify=y)`, it would
have shipped a "88% accurate" classifier that was 13 points geography.

Corroborating evidence: predicting *ecoregion* from the same features reaches
**0.5323** against 0.1667 chance, and leave-one-ecoregion-out collapses to
**0.1898** with Chilli at zero recall. The model works where it has seen
neighbours and nowhere else.

## L.4 The model earns its complexity — but the features, not the estimator

| Model | Blocked balanced accuracy |
|---|---|
| XGBoost (shipped) | **0.7492** |
| Logistic regression | 0.7448 |
| Random forest | 0.7311 |
| **`CropGrowthCurves` template match (zero parameters)** | **0.0935** |
| Stratified dummy | 0.0465 |

The ICAR template match — the competitor F.2 insisted on precisely because it
would have been embarrassing to skip — scores barely above chance, so the ML
model is worth ~8x it. But it beats plain logistic regression by 0.004. The value
is in the feature construction, not the estimator.

Worth recording: on the *partial-data* rehearsal, XGBoost at the doc's original
600-tree/depth-6 capacity **lost** to a random forest (0.3738 vs 0.4479). That
was overfitting, and it is why capacity was cut and why estimator choice is now
made by measurement on the blocked folds rather than asserted.

## L.5 The one failing gate

Gram recall **0.214**, confused with **Chilli 40.9%** of the time. Chilli's
precision is 0.509 — it absorbs the false positives. Chilli occupies just 6
spatial blocks, all in SOUTHERN_PENINSULA, so this reads as the model learning a
regional signature and Gram parcels in the south being swept into it. A.5's
spatial confounding, surfacing as one specific confusion.

Six of the eight worst confusions were predicted in advance in C.4 from agronomy
(Wheat->Mustard 14.1%, Jowar->Bajra 15.7%, Soyabean->Tur 13.5%,
Banana->Sugarcane 9.2%), which suggests they are structural rather than
tuning-fixable. Gram->Chilli was not predicted.

## L.6 The abstain rule, measured

E.8's judgement call (`p_min 0.35`, `n_scenes_min 8`) discarded **43.7%** of
cycles before the confidence gate applied. Measured scene counts are median 8,
p25 6, p10 5 — the threshold was simply too strict for the data.

The sweep now picks the loosest setting still clearing 0.70 precision:

| | p_min | n_scenes_min | Coverage | Precision |
|---|---|---|---|---|
| doc's guess | 0.35 | 8 | 0.679 | 0.718 |
| **shipped (measured)** | **0.25** | **5** | **0.911** | **0.789** |

Higher precision *and* 23 points more coverage. `_abstain_rule_for()` reads this
from the tier's own sweep at export time, and logs a refusal-to-recommend if no
setting reaches 0.70.

## L.6b A pre-existing model was already in the repo, claiming 95.8%

`backend/Credit_assessment/models/crop_classifier_model.joblib` (49.8 MB, tracked
in git since commit `f54e5eb`) holds a 23-class RandomForest on 24 features,
trained 2026-02-16, with recorded `test_accuracy = 0.9584` and
`cv_accuracy_mean = 0.9545`.

Its own `training_config` explains the number: `total_samples = 25981` drawn from
`total_farms = 1129` at `n_samples_per_farm = 25`. Twenty-five samples per farm,
then a random split — **the same farm on both sides**. No spatial blocking. This
is the leakage failure mode A.5 and F.1 were written to prevent, sitting in the
repository with a 95.8% label on it.

A second, quieter problem: its features are `t01..t08` while
`ML_FEATURE_SCENES = 15`, so served through the live extractor it reads only the
first 8 of 15 normalised grid points — about the first half of each cycle. Train
and serve never agreed.

It is left in place (still the `CROP_MODEL_PATH` default, and it still loads
under the new fail-fast guard); the tier-1 model ships beside it as
`crop_classifier_tier1_v1.joblib`. This also answers a question the audit could
not: `ENABLE_CROP_CLASSIFICATION=false` was almost certainly the right call
already.

## L.7 Verdict

**5 of 6 gates pass. Recommendation: do not enable in production yet.**

`ENABLE_CROP_CLASSIFICATION` stays `false`. The blockers are Gram's recall and
the LOEO collapse, and both are properties of the *dataset's geography*, not of
the modelling. Shadow mode (Phase 7) is the correct next step.

The highest-leverage remaining action is not modelling at all: **re-sample the
training set randomly and spatially stratified from the full ~106,000 source
rows** (J.2 item 2). That attacks the leakage gap, the LOEO failure and the
`Area` artifact simultaneously, and no estimator change can substitute for it.

## Appendix — reproducing the audit

Every figure in Part A comes from the following, run in `Crop_classification_model/`:

```python
import geopandas as gpd, numpy as np

g  = gpd.read_file('crop_classification_train_500.gpkg', engine='pyogrio')
ga = g.to_crs('EPSG:6933')

print('acres check  ', round((ga.area / g.Area).median(), 1), '(1 acre = 4046.86 m2)')
print('dup geoms    ', int(g.geometry.to_wkb().duplicated().sum()))
print('10m px median', round((ga.area / 100).median(), 1))
print('core px p5   ', round((ga.buffer(-10).area / 100).quantile(.05), 1))

c   = ga.centroid.to_crs(4326)
blk = np.floor(c.y / .25).astype(int).astype(str) + '_' + np.floor(c.x / .25).astype(int).astype(str)
print('blocks       ', blk.nunique(),
      '| single-crop', int((g.groupby(blk).Crop_Name.nunique() == 1).sum()))

print('unique dates per crop')
print(g.groupby('Crop_Name').Date.nunique().sort_values().to_string())
```
