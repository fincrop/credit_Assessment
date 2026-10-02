# Crop Classification, Delineation and Monitoring: Accuracy Enhancement Plan

**Goal:** field-level crop, boundary, sowing, stage, stress and yield outputs that are accurate enough, and honest enough about uncertainty, to share with banks and other stakeholders.

**Trigger:** audit of the Dhaswadi Kharif 2026 run (`Crop_Monitoring/Results/`), 2 Oct 2026.
**Status:** revision 2 (constraints confirmed 2 Oct 2026). Nothing in this plan has been implemented yet.

### Constraints confirmed for this plan
| Item | Status | Effect on the plan |
|---|---|---|
| Field survey | **Not possible now** | Validation uses held-out labelled 2023 data, independent image interpretation, official statistics, and client records as they come in (§6) |
| More crops / second season | **Not available now** | Use the local tur, soybean, jowar, sugarcane and bajra labels already in the training set; handle maize and other crops as "other / unknown"; build year-robust features (§1.4, A5) |
| Yield data | **None now; client records later** | Yield anchored to official district average yields, published with honest bands and a "not field-calibrated" label; client records feed calibration over time (§5.6, §6) |
| Cadastral | Village outlines for most villages, survey-number plots for some, **offset 50–100 m from real field lines** | Add a co-registration step that aligns plots to satellite-visible edges before use (§4, C1.3) |
| Accuracy targets (§8) | **Accepted** | Each gate states what it is measured on now and what waits for client records |
| Cotton naming | **"Cotton" only** | All outputs say "Cotton"; no kapas/lint/seed-cotton wording (§5.6) |

---

## 0. Summary

The Dhaswadi run failed for structural reasons, not tuning ones.

**Classification**
- The run was restricted to Cotton only, and the code rescales probabilities over the allowed crops, so every confident field became "Cotton 1.0".
- The model's inputs at run time don't match training: the window runs past the run date into December, and empty periods are counted as observations.
- The model was trained on cotton from Gujarat, not Marathwada.

**Monitoring**
- Village runs sample 1–4 points per farm on an 11-day median composite. That is point analysis, not raster.
- A field that is already green in the first image is treated as "emergence", which produces the April sowing dates.
- A season-length filter run before the season ends removes correctly dated fields.
- Yield is close to a constant; stress compares a farm only with itself.

**The fix has four parallel tracks:**

| Track | What | Can start |
|---|---|---|
| **A. Training data and model** | Ingest the new Marathwada cotton and soybean, retrain, evaluate on a held-out area | Now |
| **B. Inference correctness** | Remove the single-crop rescaling, fix the run-time window and inputs, export probabilities and provenance | Now |
| **C. Monitoring rework** | Pixel stacks, harmonised sensors, curve fitting for sowing and phenology, stress measured against comparable fields, calibrated yield, raster map products and frontend overlay (§5.8) | After B1–B3 |
| **D. Validation without field visits** | Held-out labelled data, independent image interpretation of Dhaswadi, official statistics checks, client-record feedback loop, shadow mode, release report | Now |

Nothing goes to a bank until it passes the gates in §8.

---

## 1. The new Maharashtra data: what it can and cannot do

### 1.1 What we have (checked 2 Oct 2026)

| | `Cottondata_1500_gpkg.gpkg` | `SoyabeanData_1000.gpkg` |
|---|---|---|
| Polygons | 1,545 (all valid, no duplicates) | 1,115 (all valid, no duplicates) |
| Location | 18.81–19.55 N, 76.39–77.19 E (Marathwada) | 19.02–20.45 N, 75.96–76.76 E (Jalna–Parbhani–Hingoli belt) |
| Distance to Dhaswadi | 114 within 25 km, 796 within 50 km | Closest 38 km, 44 within 50 km |
| Field size | Median 0.27 ha (≈27 Sentinel-2 pixels), min 0.04 ha | Median 0.28 ha, min 0.20 ha |
| Dates | `G_Date` = 2023-07-10 for all | `Date` = 2023-08-10; `GDate` = Sep or Oct 2023 |
| Quality flag | none | 599 "Checked", 516 "Unchecked" |
| Overlap with current training data | 2 polygons | **218 polygons already in `crop_classification_train_500.gpkg`** |
| Adjacent parcels | 258 parcels sit in groups of ≥5 touching parcels | 212 parcels in groups of ≥5 |

**Data quirks:**
- **`Area` is in acres** (ratio to true hectares = 2.471). `ingest.py` already converts it; never use the raw column as hectares.
- **`G_Date`/`GDate` are survey dates, not sowing dates.** They tell us the crop existed on that date.
- Touching polygons share edges (0 pairs overlap by more than 5%). They are adjacent real fields, which makes them useful for boundary evaluation.

### 1.2 Answer to "can we use at least 200 of each?"

Yes, and we should use far more than 200 to train. The best use of 200 per crop is as the **untouched test set**:

- **Test set:** at least 200 cotton and 200 soybean, chosen as whole spatial blocks, never seen in training or tuning.
  - At n = 200 and recall ≈ 0.85, the 95% confidence interval is ±5 percentage points. That is the precision a bank-facing accuracy figure needs.
  - Fewer than 200 gives intervals too wide to quote.
- **Training:** everything else, about 1,300 cotton and 900 soybean, plus the existing 18-class set.

### 1.3 What it improves, and its limits

| Component | What the data enables | Limit |
|---|---|---|
| Classification | First real Marathwada examples of the two dominant Kharif crops. Fixes the "cotton = Saurashtra" problem | Only two new crops. Partly covered by local labels already in the training set (§1.4); maize and fallow have no local labels |
| Delineation | 470 parcels in adjacent groups: a real benchmark at Dhaswadi field sizes | Partial labels: we can score matched-parcel overlap, but not over-segmentation in unlabelled land |
| Monitoring | Reference NDVI and radar curves for Marathwada cotton and soybean (shape, length, peak timing), used for sowing curve fitting, crop sanity checks and stress references | No sowing dates or yields. Survey dates only give an upper bound ("sown by 10 Jul 2023") |
| Yield | None directly | District-anchored until client records arrive (§5.6) |

**One year only (Kharif 2023).** 2023 was an El Niño year with a late monsoon onset over Maharashtra. Curves will be shifted later than normal. All features and reference curves must be **aligned to the crop's own cycle**, not to calendar dates. Weather-based features learned from 2023 must not drive 2026 predictions (§3.4).

### 1.4 Local labels we already have, and how we cover the gaps

New data is not available now. But `crop_classification_train_500.gpkg` already holds local labels (checked 2 Oct 2026):

| Crop | Inside Marathwada (18–20.5 N, 75–77.5 E) | Wider region (17–21.5 N, 74–78.5 E) | Years | Median size |
|---|---|---|---|---|
| Tur | 493 | 499 | 2023 (495), 2022 (4) | 0.44 ha |
| Soybean | 173 | 173 | 2023 | 0.48 ha |
| Jowar | 42 | 364 | 2023 (294), 2024 (70) | 0.98 ha |
| Sugarcane | 6 | 61 | 2022–2024 | 0.95 ha |
| Bajra | — | 174 | 2023–2024 | 0.50 ha |
| Cotton | 2 | 17 | 2023 | 2.73 ha |
| Maize | 0 | 0 | — | — |

Combined with the new files, the **Marathwada Kharif regional set** is about 1,545 cotton, ~1,070 soybean (after removing overlaps), ~500 tur, plus jowar, sugarcane and bajra from the wider region.

**Gaps and how we cover them:**

1. **Maize, fallow and other crops have no local labels.** Handle these as open-set:
   - An "other / unknown" outcome when a field's curve fits none of the regional crop reference curves well (§5.4).
   - Calibrated abstention.
   - The region guard (B5).
   - Grouping Dhaswadi fields by curve shape (unsupervised clustering) to surface groups that match no known crop. These are flagged for review and **never auto-labelled**.
2. **Jowar labels may be rabi.** Maharashtra jowar is largely rabi. Each jowar label is assigned to its cycle (A3), and only Kharif cycles train the Kharif model.
3. **Cotton, soybean and tur are single-year (2023).** Year-robust design (A5):
   - Features aligned to monsoon onset and to the crop's own cycle, not calendar dates.
   - Time-shift (±20 days) and stretch (±10%) augmentation using the existing `augment.py`.
   - No year-specific weather features.
   - Model confidence is lowered where 2026 curves fall outside the 2023 range (out-of-distribution check).
4. **Soybean–tur intercrop** is common here and mixes at 10 m. The model gets an explicit `Soyabean+Tur` outcome where the two have the highest probabilities and the margin is small. It is reported as an intercrop, not forced into one crop.
5. **No sowing dates or yields.** Covered by §5.3 validation and §5.6/§6 (official statistics plus client records).

---

## 2. Track A: Training data integration and retraining

Reuse the existing training pipeline in `Crop_classification_model/src/` (`ingest → extract → cycles → features → train → evaluate`). Don't build a parallel one.

### A1. Ingest and register (0.5 day)
- Run both files through `ingest.py`. It already converts acres, measures true area, applies the 10 m inward buffer and the minimum-pixel check.
- Add columns:
  - `source` = `elai_mh_2023`
  - `qa_checked` (soybean Checked/Unchecked; cotton = unknown)
  - `survey_date`
- Remove the **218 soybean polygons that already appear in `train_500`** (geometry hash or IoU > 0.5). They stay in training and are never allowed into the test set.
- Fields under the minimum core-pixel count stay in the registry, flagged `px_gate_ok = false`. They're used for monitoring evaluation, not for classifier training.
- Tag the local tur, soybean, jowar, sugarcane and bajra rows from `train_500` (§1.4) as `region = marathwada` so the regional evaluation can use them.

### A2. Spatial split (0.5 day)
- Use 0.1° blocks. The data is clustered, and a random split would measure memorised neighbourhoods (see the model card's +0.13 leakage gap).
- Hold out whole blocks until there are **≥200 cotton and ≥200 soybean** in the test set.
  - Prefer cotton blocks near Dhaswadi (18.8–19.0 N), since that's the area we'll report on.
  - Test soybean only from **"Checked"** polygons.
  - Also hold out ≥100 local tur in whole blocks. Tur is the main local confusion partner for soybean, and without field visits this test set is our main accuracy evidence.
- Freeze the split in `data/splits/mh2023_split.json` and commit it. Nobody tunes against the test blocks.

### A3. Satellite extraction (1–2 days, mostly Earth Engine time)
- Run `extract.py` for the 2023 Kharif window with the same acquisition settings as production (classification_model.md §B.7).
- Assign each label to a detected crop cycle using `crop_calendar.pick_cycle_by_season`. The survey date must fall inside the cycle's active period: cotton survey 10 Jul (just after sowing), soybean survey Sep–Oct (pod fill to harvest).
- Drop and log polygons with no cycle that matches the season (into `rejections.csv`).

### A4. Label quality checks (1 day)
- Check each curve's phenology against its label:
  - Cotton: cycle ≥ 140 days, peak in Sep–Oct.
  - Soybean: 85–125 days, peak in Aug, senescence Sep–Oct.
  - Polygons that fail are flagged, not silently dropped.
- Cross-validated label-noise check: polygons whose label gets low probability across folds go to a review list.
- Unchecked soybean trains with sample weight 0.5 until reviewed.

### A5. Retrain (1–2 days)
- **Tier 1 only** for production candidates. Tier 2's top features include weather variables shared by a whole village, which can't separate fields and won't transfer across years.
- Class balance: inverse-frequency sample weights, capped. The new data must not make Cotton and Soyabean dominant classes.
- Keep temperature calibration and the abstain option as they are (model card §E.7–E.8).
- **Correct for local crop shares** (label-shift correction, Saerens et al. 2002):
  `p'(c|x) ∝ p(c|x) · π_district(c) / π_train(c)`
  - `π_district` = district Kharif area shares from official crop-area statistics.
  - This is the principled way to say "this region is mostly soybean, cotton and tur". It changes the starting odds, not the evidence.
- **Year robustness** (one season of labels, §1.4 item 3):
  - Features computed on onset-aligned, cycle-relative time.
  - `augment.py` time-shift and stretch augmentation.
  - An out-of-distribution score per field (distance to the training feature distribution). Above the threshold, the result becomes `provisional`.
- **Intercrop outcome:** add `Soyabean+Tur` as a reported outcome (§1.4 item 4), decided by a rule on the calibrated probabilities. It is not a trained class, because we have no intercrop labels.

### A6. Evaluate (1 day)
Report all three; none replaces another:
1. Existing blocked 5-fold cross-validation on the full set (to check we haven't regressed).
2. **Marathwada held-out test** (A2): per-class precision, recall, F1 with 95% Wilson intervals; confusion between cotton, soybean, tur and Others; calibration error; coverage at the abstain threshold.
3. **Dhaswadi 2026 re-run** (no crop filter) compared with:
   - the independent image-interpretation sample (D1),
   - the official taluka/district crop shares (D4).

Save results in `models/MODEL_CARD.md` with the training data hash and split hash.

---

## 3. Track B: Classification inference correctness (P0, start now)

These are bugs. Each fix gets a regression test.

| # | Fix | Where | Test |
|---|---|---|---|
| **B1** | **Remove the rescaling over allowed crops.** Keep the full 18-class probabilities. If the model's top crop isn't in the user's list, output `not_in_requested_set` with the model's crop shown. Warn, or refuse, when only one crop is selected | `area_classifier.py:1021-1028`, `ClassifyInputsForm.tsx:46-52` | With allow = {Cotton} and a soybean-like curve, the output must not be "Cotton 1.0" |
| **B2** | Export per field: `top1`, `top2`, `p_top1`, `p_top2`, `margin`, `abstain_reason`, `model_top_crop`, `n_obs_real`, `cycle_complete`. Abstained fields carry their real (low) probability | `area_classifier.py:1032-1040`, `merge_field_objects`, `build_result`, `classification_export.py` | Schema test; Abstained confidence ≤ 0.25 or margin < 0.10 |
| **B3** | **Stop the window at the run date.** Drop empty periods instead of filling them with the last value. Use the real observation count. Label cycles not finished by the run date `cycle_complete = false` | `area_classifier.py:171, 972, 1008`; `crop_detector.py:155, 227` | Parity test: features from the village path equal training features on the same parcel (extend `validate_parity.py`) |
| **B4** | **In-season mode.** Before the expected harvest, classify only with features available that early in the season (training must use truncated cycles too), or label the result `provisional` | `crop_detector.build_features`, train truncated variants | Accuracy measured at 60, 90, 120 days after sowing on the held-out test |
| **B5** | Turn on `apply_region_guard` (`region_support.json`) and `apply_season_mask`. They're currently read in but never used | `area_classifier.py:123-141`; port `region_guard.py` into the backend | A field outside the training region gets `out_of_support`, not a crop |
| **B6** | Record which model actually ran: model file path, file hash, extractor version, training data hash. Fix the misleading `tier2_v1` label | `area_classifier.py:1049`, `crop_detector.py:63`, `.env` `CROP_MODEL_PATH` | Result contains `model.sha256` |
| **B7** | Split "Fallow" into `bare_verified` (bare soil seen through the season), `non_crop`, and `no_data` (too few clear observations) | `area_classifier.py:995-1000` | Counts add up; `no_data` is never shown as Fallow |

---

## 4. Track C1: Delineation

Today: watershed segmentation on a 10 m edge map scaled up to 5 m, then traced along pixel edges. Median IoU 0.39 on the repo's own benchmark, staircase shapes, about 8 core pixels per median field.

| # | Task | Method |
|---|---|---|
| C1.1 | **Benchmark on the new adjacent parcels** (470 parcels in groups of ≥5) | Per parcel: best-match IoU, over- and under-segmentation rates, boundary F1 at 10 m tolerance. Run every delineation method (watershed, FTW, SNIC; ALU if licensed) through `eval_delineation.py` |
| C1.2 | Tune watershed and FTW against that benchmark | `tune_delineation.py`, tuning blocks only, test blocks held out |
| C1.3 | **Align cadastral maps, then use them** (detail below). Village outlines exist for most villages and survey-number plots for some, but plot lines sit **50–100 m** from the real field lines | Per-village co-registration to satellite-visible edges, then snap segments to the aligned plot lines |
| C1.4 | **Boundary confidence per field** | `boundary_confidence` from edge strength and size; fields under ~0.15 ha or under 8 core pixels get `low_resolution` and are scored by "management zone" (§5.1), not as precise farms |
| C1.5 | Output | Keep traced polygons for analysis. Simplify only for display, and say so. No smoothing that hides uncertainty |

### C1.3 detail: cadastral co-registration (50–100 m offset)

An offset of 50–100 m is 5–10 Sentinel-2 pixels, larger than many fields. Snapping without alignment would assign plots to the wrong field, so alignment comes first.

1. **Reference edges:** a multi-date Sentinel-2 edge map for the village. Use the same 10 m gradient stack delineation already builds (`field_delineation.py:737-809`), plus road and stream lines where visible.
2. **Global fit per village:**
   - Rasterise the survey-plot lines.
   - Search over translation (±150 m), rotation (±3°) and scale (±2%) for the transform that best matches the reference edges (chamfer distance or edge cross-correlation).
   - Then refine with RANSAC on automatically matched corner and road-junction control points.
3. **Local correction:** a thin-plate spline on the RANSAC control points removes residual warping from old map sheets. Keep it heavily regularised so it can't overfit.
4. **Quality gate per village:**
   - Median residual of held-out control points **≤ 10 m (one pixel)**.
   - Share of aligned plot-line length within 10 m of an image edge ≥ 60%.
   - Villages that fail keep image-delineated boundaries only; the map says `cadastral_alignment = failed`.
5. **Use after alignment:** field = aligned survey plot ∩ crop segment.
   - Survey numbers are often subdivided among several farmers, so one plot can hold several segments, and segment edges inside a plot are kept.
   - Plot lines are a constraint, not ground truth. A strong image edge more than 15 m from any plot line is kept.
6. **Village outlines** (available for most villages) are aligned the same way. They're used to clip the area of interest and for village totals, never as field boundaries.
7. **Provenance per field:** `boundary_source` = `aligned_cadastral` / `delineated`, `alignment_residual_m`.

**Target:** median matched IoU ≥ 0.60 on the benchmark. For any field shown to a bank: aligned-cadastral boundaries that pass the gate, or delineated boundaries with `boundary_confidence` shown.

---

## 5. Track C2: Monitoring rework

### 5.1 Data layer: from points to pixel stacks
Replace `fetch_field_stacks`' 4–12 point sample (`observe.py:139-145`) with real pixel stacks.

- **Raster pull per village tile:** one Earth Engine `computePixels` / `getDownloadURL` request per tile per scene date, returning arrays for the village bounding box.
  - At 10 m, a village of about 700 ha is roughly 70,000 pixels; × ~40 scenes it's small enough to cache locally (`.npz` / Zarr).
- **Per-field pixel masks:** rasterise each field, shrink it by one pixel (10 m), and flag edge pixels. Field statistics use core pixels. Fields with fewer than 8 core pixels are labelled `low_resolution`.
- **Individual scenes, not 11-day medians.** Keep the real acquisition date of every clear observation; remove `period_length` compositing (`observe.py:452-455`). Smoothing happens later, per pixel, with weights for observation quality.
- **Harmonised optical data:** use HLS (`NASA/HLS/HLSS30/v002`, `NASA/HLS/HLSL30/v002`) so Sentinel-2 and Landsat are on the same grid and cross-calibrated. If staying on raw collections, apply published Landsat→S2 band adjustments and **never store two sensors under one date**.
- **Cloud masking:** keep Cloud Score+ `cs_cdf ≥ 0.6`. Add a per-pixel clear-observation count; any period with no clear view must be visible in the output.
- **Sentinel-1 radar:**
  - Per orbit (ascending and descending separately).
  - VV, VH and VH/VV in linear units; multi-temporal speckle filter or field-mean over ≥ 20 pixels.
  - Revisit is about 6 days with S1A + S1C in 2026, versus 12 days in 2023. Features must not depend on revisit frequency.
- **Drop the MODIS 250 m village-average gap fill** from field series (`state.py` `_coarse_on`). Keep it only as a village-level context layer.
- **Weather stays village-level and is labelled that way:** CHIRPS (5 km) and ERA5-Land (9 km). Don't present it as field-specific.
  - Add a soil water balance using soil available water capacity (NBSS&LUP or SoilGrids). Vertisols store far more water than the current one-bucket model assumes.
- **Restore the inward buffer and minimum-pixel rules** from `crop_monitoring_framework.md` §5. The batch path dropped them.

### 5.2 Phenology: smoothed per-pixel curves, then milestones
- Smooth each pixel's NDVI (and NDRE) with a **weighted Whittaker smoother** (Atzberger & Eilers 2011). Weights come from cloud score and sensor; give the upper envelope more trust, because residual cloud only lowers NDVI.
- Fit a **double-logistic curve** (Beck et al. 2006) per pixel → start of season, peak, end of season, amplitude, and fit error.
- **Curve fitting against crop reference curves** (Sakamoto et al. 2005):
  - Build median curves for Marathwada cotton and soybean from the 2023 data (Track A3).
  - Fit each field's curve by time-shift (→ sowing date) and stretch (→ season length), with fit error.
  - This gives sowing date and stage from the whole curve, not from a single threshold crossing.

### 5.3 Sowing date
Replaces `sowing.py` cue fusion and `zones.py` green-up rules.

1. **Emergence needs a bare-soil observation first:** NDVI < 0.20 and bare-soil index high, then a sustained rise. A first observation already above threshold is **never** emergence (fixes `zones.py:71-73`, `sowing.py:105-106`).
   - Start monitoring on **1 March** so the pre-season bare state is always seen.
2. **Monsoon onset sets the prior for rainfed Kharif crops.**
   - Onset = first date cumulative rain since 1 June reaches 75–100 mm with no 10-day dry spell after it (the state's sowing advisory threshold).
   - Rainfed cotton/soybean sowing prior: onset + 0 to 21 days, triangular.
   - Before 15 May the prior is zero **unless** there's irrigation evidence: a wet-up on the field's own moisture index with no rain on the grid and no wet-up in neighbouring fields (pre-monsoon irrigated cotton).
3. **Radar fills the cloudy June–July gap:**
   - Sowing and tillage: VH drop and VV soil-moisture jump after onset.
   - Emergence: sustained VH and VH/VV rise.
   - The baseline is the 30 days **before onset** on the same orbit, not the first two images of the season.
4. **Combine evidence properly:** a Gaussian mixture or particle posterior on a day grid, with a fixed calendar and onset prior. **Remove the self-widening window** (`sowing.py:263-267`) and the 1e-6 floor that turns fusion into a vote (`:214`).
5. **Output:** sowing date, P10–P90 range, sources used, and `insufficient_evidence` when the posterior is wider than 25 days. No invented dates.
6. **Validation (no farmer dates available now):**
   - **Upper-bound test** on 1,545 cotton parcels from 2023: estimated sowing must be ≤ 10 Jul 2023. The share that violates this is a hard error rate.
   - **Lower-bound test:** for rainfed fields, sowing must be ≥ the 2023 onset date minus 5 days, unless irrigation evidence exists.
   - **Agreement test:** optical-only and radar-only estimates on fields that have both must agree within 10 days in ≥ 80% of fields. This shows whether the two independent methods support each other.
   - **Plausibility vs official calendar:** the village distribution of sowing dates must fall in the state's sowing-progress window for that week (Maharashtra agriculture department weekly sowing reports).
   - **Client records** with sowing dates are added to the registry as they arrive. The ≤ 7 / ≤ 15 day target is measured on them.

### 5.4 Stage, harvest, and the crop check
- **No rejection on season length while the season is running.** Length is only checked once the season is finished (end of season observed) (`pipeline.py:325-339`, `progress.py:127-136`).
- **Crop check, not silent removal.** When the curve fits a different crop's reference better (fit error is a big enough multiple of the error for the claimed crop), output `phenology_disagrees_with_class` with the better-fitting crop. Send it back to classification review; don't drop it.
  - This would have caught the Dhaswadi soybean.
- **Cotton:**
  - Stages from the fitted reference curve plus growing degree days (base 15.6 °C).
  - Picking detection only inside the picking calendar (≥ 140 days after sowing, Oct–Jan) (`progress.py:99-111`).
  - Harvest window Oct–Jan with multiple picks, not a single date.
- **Soybean:** harvest = end of season on the smoothed curve, normally late Sep–Oct.
- **Stage as of the run date** comes from the fitted curve with uncertainty, not from "45 days after peak" (`progress.py:48-51`).

### 5.5 Stress
Replaces "a farm compared with its own top 25% of points" (`stress.py:63-70`).

- **Reference:** same crop, same sowing week (±7 days), same village/soil group, same days after sowing, and only fields that pass all QA checks. A field's index (NDVI, NDRE, NDMI) is expressed as a percentile or z-score within that group.
  - A uniformly stressed farm is now detected.
  - A whole village under stress shows up against the reference curve and the long-term normal.
- **Within-field stress maps (raster):** pixel z-scores against the field's own sowing group, only for fields with ≥ 30 core pixels; edge pixels excluded.
  - This is where raster matters. It shows *where* in the field the problem is.
- **Assigning a stress type:**

  | Signal | Likely cause | Confidence | Field-specific? |
  |---|---|---|---|
  | Moisture-index drop without a matching NDRE drop, plus soil-water deficit | Water stress | Medium | No: weather is village-level |
  | NDRE / CIre drop at the same canopy cover | Nutrient deficiency | Medium | Yes |
  | Patchy spatial pattern, sudden drop between clear scenes | Damage (pest, disease, hail, waterlogging) | Low | Yes |

  - "Tissue Damage" from a bare-soil index rise on edge pixels is removed by the edge mask.
- **Minimum evidence:** a stress call needs a clear observation within 10 days, or a radar observation within 6 days. Otherwise `condition_unavailable`.
- **Multi-year normal (village level):** build a 2019–2025 Sentinel-2 normal per village. It's the median NDVI/NDMI curve of cropland pixels in each sowing-week group, aligned to monsoon onset.
  - This shows when a **whole village** is below its normal (drought, excess rain), which comparing a field with its neighbours can't show.
  - It's per village, not per field, because crop rotation breaks per-field history.
- **Duration and severity:** a field is reported as stressed only when the anomaly lasts across **≥ 2 consecutive clear observations** (or 1 optical + 1 radar observation agreeing). A single bad scene is shown but marked `unconfirmed`.
  - Severity = mean z-score × stressed area share × days stressed. This feeds the yield index (§5.6).
- **Waterlogging and flood** (Kharif in Marathwada has heavy-rain episodes):
  - Radar VV/VH drop to open-water levels, or the optical water index (MNDWI) above 0, on crop pixels, during or after heavy rain on CHIRPS/IMERG.
  - Reported as `Waterlogging` with the affected area share and number of days.
- **Sudden damage** (hail, lodging, pest/disease outbreak): a sharp drop between consecutive clear scenes that is patchy in space and has no matching weather cause. Reported as `Canopy damage (cause unconfirmed)`.
  - **Pest and disease are not identified by name.** 10 m multispectral data can't tell them apart reliably, and the output says so.
- **Stage-aware thresholds:** what counts as an anomaly depends on growth stage. Low NDVI at emergence isn't stress; a drop during boll filling (cotton) or pod filling (soybean) is weighted most in the yield index.

### 5.6 Yield
Replaces "median of biomass, peak canopy and peers" (`yield_model.py:94-106`), which collapses onto the 0.45 baseline.

No field yield records exist now. Client records arrive later, after clients compare our analysis with their own. So yield is built in two stages, and the published number always says which stage it came from.

- **Naming:** the crop is reported as **"Cotton"**. No kapas, lint or seed-cotton wording anywhere in outputs.
  - The number means **harvested produce as the farmer records it**, so client records can be compared directly.
  - Units: t/ha, with a display option for quintal/acre if banks prefer it.
  - The 0.45 t/ha lint constant in `library.py:36` is removed.

**Stage 1 (now): anchored to official statistics, honest bands**

- **Baseline:** the official district (or taluka) average yield for that crop.
  - Use the latest 5 years available from public area-production-yield statistics (Directorate of Economics & Statistics / Maharashtra agriculture department).
  - Store the source, years and value with every result.
  - This replaces the hard-coded national defaults in `library.py:23-45`.
- **Field signal:** a **relative yield index**.
  - Inputs: the field's integrated canopy (fAPAR / NDVI) over the reproductive window, plus a stress penalty.
  - Measured against crops of the same type and sowing group, over the same window, in the same village and in the district.
  - Stated as a percentile and a ratio to the group median.
- **Published estimate:** `district_avg_yield × field_ratio`.
  - The P10–P90 band comes from the spread of district yields across years combined with the index uncertainty.
  - Label: **"Estimate anchored to district average; not field-calibrated."**
- **Rule:** a yield estimate is shown to banks **as a band with this label**, never as a bare point value, until Stage 2 gates pass.

**Stage 2 (as client records arrive): calibration**

- Each client record (field, crop, season, yield, optional sowing date) enters the registry with consent and provenance (§6 D3).
- Fit `yield = a + b · reproductive-window canopy integral + c · water stress + ε` per crop, once **≥ 30 records per crop** exist.
- Prediction intervals come from cross-validated residuals.
- Calibration is refit each season. The published label changes to "calibrated on N client records (season, region)".

**Both stages**

- **In season:** a forecast with widened bands before peak. Frozen at observed harvest.
- **Remove:**
  - Ranking against peers in the same run (circular).
  - The cap on the peak-canopy estimator.
  - The median-of-three rule.
- **Fix irrigation detection:** it can never fire with 11-day spacing (`weather.py:66` needs ≤ 6 days). Recompute it on real scene dates.

### 5.7 Handoff between classification and monitoring
- Pass `top1`, `top2`, `p_top1`, `margin` and `cycle_complete` from classification (`monitoring_runner.py:157-171`). Monitoring gates crop-specific yield on calibrated probability, not on the rescaled 1.0.
- Use farmer sowing dates in village runs (`monitoring_runner.py:257` currently passes `hint=False`).
- Remove duplicated logic: the crop calendar and phenology reference curves live in one module used by both classification and monitoring.

### 5.8 Raster products and map representation

Raster maps are the right way to **show** spatial patterns: where in a field or village stress starts, how sowing moves across the landscape, which parts of a boundary are not crop.

For **decisions**, banks need the field-level numbers *derived from* those rasters. So every raster has a matching field summary computed from the same pixels, and the two always agree.

**Scale check for our fields.** The median Dhaswadi field is 0.27 ha, about 27 pixels at 10 m, of which only about 8 are interior after removing edges.
- Within-field maps are informative for fields ≥ 30 interior pixels (≈ 0.5 ha+).
- For smaller fields, the raster's value is at **village scale**: patterns across many neighbouring farms.
- Small fields get a field-level colour plus a village raster backdrop, not a misleading 8-pixel "within-field map".

**Products**

All are 10 m, EPSG:32643, one Cloud-Optimised GeoTIFF (COG) per village per product per date:

| Product | Content | When written |
|---|---|---|
| Index maps | NDVI, NDRE, NDMI (optical); VH, VH/VV (radar) | **Only on dates with a real clear observation.** Never for gap-filled or modelled days (framework §8.4) |
| Data-quality layer | Per pixel: clear/cloud/shadow/no-data, sensor, days since last clear look | Every product date |
| Anomaly map | Pixel z-score vs same-crop, same-sowing-week reference (§5.5) and vs the village normal | Each clear date |
| Stress class map | Healthy / mild / moderate / severe + type (water, nutrient, waterlogging, damage) | Each clear date; type only where §5.5 evidence rules pass |
| Sowing date map | Pixel sowing date + P10–P90 width (§5.3) | Updated each run until sowing is stable |
| Stage map | Pixel stage from the fitted curve (§5.2) | Each run |
| Crop-consistency map | Per pixel: which regional reference curve fits best, and the fit error. Shows mixed fields, intercrop strips and non-crop patches inside a boundary | Each run after peak |
| Season summary | Season start/peak/end, canopy integral, stressed-days count | End of season (and provisional mid-season) |

Pixel-level crop *classification* is not produced. The classifier is trained on field-level features. The crop-consistency map from curve fitting is the per-pixel crop evidence instead.

**Rules for honest maps** (what makes them shareable with banks)

1. **Fixed colour scales** across all dates and villages (e.g. NDVI 0–0.9, z-score −3…+3). No per-image contrast stretch, so maps from different dates and villages can be compared.
2. **No-data looks like no-data.** Masked pixels are shown hatched, never interpolated or filled with a nearby value.
3. **Every map shows its date, sensor and clear-pixel share** on the map face, plus a legend with units.
4. **Colour-blind-safe palettes** (sequential for indices, diverging for anomalies, distinct categories for stress types).
5. **Field outline drawn over the raster.** Edge pixels can be shown dimmed, so it's visible which pixels went into the field statistics.
6. **Raster and field always agree:** a field's reported stress share equals the share of its interior pixels in stressed classes on that date. This is enforced by a test.

**Pipeline and storage**
- **Extraction:** Earth Engine export of village tiles. `computePixels` for small villages, batch export to Cloud Storage for many villages.
  - Cache raw per-scene arrays locally (Zarr) so reruns don't re-download.
- **Processing:** numpy/xarray per village tile. Smoothing, curve fitting, anomalies and classes run per pixel, vectorised. A village of about 70,000 pixels × ~40 scenes is seconds to minutes per run.
- **Storage:** COGs in object storage keyed by `village/season/product/date`. Field summaries in MongoDB link to their COG paths.
- **Serving to the frontend:** pre-rendered XYZ PNG tiles with the fixed colour ramps for fast map display; COGs for download and GIS users.

**Frontend** (`frontend/app/monitoring/`)
- Raster overlay on the existing Leaflet map: product selector, date slider (clear dates only), opacity, legend, data-quality toggle.
- Click a field → its time series, stress history, and the raster pixels behind its numbers.
- **Bank report export:** for each farm, a map snapshot (field outline over the anomaly/stress raster on the latest clear date), the field summary table, the data-quality line and the limitation labels from §7.

---

## 6. Track D: Validation without field visits

Field deployment isn't possible now. Accuracy evidence therefore comes from four independent sources. Each is reported separately, and none is presented as field ground truth.

| Source | Measures | Strength | Limit |
|---|---|---|---|
| **MH 2023 held-out blocks** (A2) | Classification accuracy, sowing upper bound, phenology reference curves | Real labels, local, ≥ 200 per crop | Different year (2023) |
| **D1 independent image interpretation, Dhaswadi 2026** | Classification and area accuracy this season | This village, this season | Interpretation, not a field visit |
| **D4 official statistics** | Village/taluka crop shares, district yields, sowing progress | Independent, official | Aggregate only, not per field |
| **D3 client records** | Crop, sowing, yield per field | Real field outcomes | Arrives later; not a random sample |

### D1. Independent image interpretation of Dhaswadi (replaces the field survey)
- **Sample:** 300 fields, stratified random across the re-run's predicted classes (cotton, soybean, tur, other/abstained, fallow, monitoring-flagged).
  - Larger than 200 because interpreted labels are noisier than field labels.
  - Fields picked by random points within strata, not hand-picked.
- **Evidence the interpreters use** must be **independent of the model's method**, so the check isn't circular:
  - Full-season Sentinel-2 true and false colour image chips.
  - Harvest timing visible in images: soybean bare by mid-Oct, cotton green into Nov–Dec.
  - Very-high-resolution imagery where available, for row patterns and canopy texture.
  - The model's NDVI curve fit alone is **not** an allowed basis for a label.
- **Protocol:**
  - Two interpreters label each field independently, **blind to the model's output**.
  - Disagreements go to a third person for a decision.
  - Each label carries interpreter confidence (high/medium/low).
  - Report agreement between interpreters (Cohen's kappa). If kappa < 0.7, the reference isn't reliable enough to gate on.
- **Timing:** the season's harvest signal (soybean harvest Oct, cotton picking Oct–Jan) is the strongest separator. Run D1 in **two passes**: early November (soybean/tur/cotton split), then January (cotton confirmation). The imagery is archived, so there's no field deadline.
- **Use:** an error matrix → accuracy and area estimates with 95% intervals (Olofsson et al. 2014). Reported as "reference interpretation accuracy".

### D3. Client-record feedback loop
- A standard intake schema: field id or boundary, crop, sowing date, harvest date, yield + unit, irrigation, source, consent.
- Each record is matched to our result for that field and season. Disagreements are logged with both values.
- Records feed:
  - sowing validation (§5.3),
  - yield calibration (§5.6 Stage 2),
  - new training labels for later model versions, kept out of the test blocks.
- **Bias caution:** client records aren't a random sample. They're used for calibration and error discovery, and as accuracy evidence only alongside D1.

### D4. Official-statistics consistency checks
- **Crop shares:** compare village and taluka mapped area by crop with official Kharif crop-area statistics (district/taluka APY; Maharashtra e-Peek Pahani crop records if accessible).
  - Differences beyond ±15 percentage points for a major crop trigger review before release.
  - This alone would have stopped the "everything is cotton" Dhaswadi result.
- **Yields:** village average estimated yield vs the district average (and its range over 5 years).
- **Sowing progress:** the village sowing-date distribution vs the state's weekly sowing-progress reports.

### D2. Evaluation harness
- `evaluation/` module with a frozen reference registry (2023 test blocks, D1 interpretation labels, D4 statistics, D3 client records with timestamps).
- One command reruns every metric in §8 and writes a versioned report.
- Every model or pipeline change runs the harness. Regressions block merge.

### D3. Shadow mode
New outputs run alongside the current ones for one cycle (Dhaswadi plus 2–3 villages) and are logged, not shown to lenders, until §8 is met.

---

## 7. Output contract for banks and stakeholders

Every field record shared externally carries:

| Group | Fields |
|---|---|
| Identity | `field_id`, boundary source (`cadastral` / `delineated`), `boundary_confidence`, `area_ha` (true geodesic) |
| Crop | `crop`, `p_crop`, `top2`, `margin`, `status` (`confirmed` / `provisional` / `intercrop` / `abstained` / `out_of_support` / `phenology_disagrees`) |
| Dates | `sowing_date`, `sowing_p10`, `sowing_p90`, `sowing_sources`, `stage`, `harvest_window` |
| Condition | `stress_type`, `stress_percentile_vs_cohort`, `stress_confidence`, `last_clear_observation` |
| Yield | `yield_t_ha`, `yield_p10`, `yield_p90`, `yield_index`, `yield_basis` (`district_anchored` / `client_calibrated`), `baseline_source` |
| Provenance | model hashes, data dates, number of clear observations, pipeline version, QA flags |
| Raster links | COG paths for the field's village: latest index, anomaly, stress class, sowing, stage, crop-consistency and data-quality layers (§5.8) |

**Rules:**
- A field without enough evidence says so. It is not filled with a default.
- **Agreement rule for `confirmed`:** with no field visits, a crop is `confirmed` only when all three agree:
  - the classifier (calibrated probability ≥ threshold, margin ≥ 0.10),
  - the phenology curve fit (§5.4),
  - the crop calendar for the season.

  Anything less is `provisional`, with the reason given.
- Village summaries report area by class **with confidence intervals** computed from the D1 reference error matrix (Olofsson et al. 2014 area estimation). Bank-grade area totals must use this method; raw pixel counts are biased.

---

## 8. Release gates (accepted 2 Oct 2026)

Each gate is either measurable **now** or **waiting for client records**. A component whose gate is waiting can still be shared, but only with its limitation label.

| Component | Metric | Gate | Measured on (now) | Later | Status if not met |
|---|---|---|---|---|---|
| Classification | Recall and precision, cotton and soybean (tur reported) | ≥ 0.85 each (95% CI lower bound ≥ 0.80) | MH 2023 held-out blocks **and** D1 interpretation | + client records | Not released |
| Classification | Calibration error | ≤ 0.05 | MH 2023 held-out | — | Not released |
| Classification | Abstain rate | ≤ 20% | Dhaswadi re-run | — | Released; abstentions shown |
| Reference quality | Interpreter agreement (kappa) | ≥ 0.70 | D1 | — | D1 not used for gating |
| Area | Village crop area vs D1 area estimate; vs official shares | within ±10% (D1); within ±15 pts (official) | D1 + D4 | — | Not released |
| Delineation | Median matched IoU | ≥ 0.60, or aligned cadastral passing C1.3 | MH adjacent-parcel benchmark | — | Shown with `boundary_confidence` |
| Sowing | Upper-bound violations / optical–radar agreement | ≤ 5% / ≥ 80% within 10 days | MH 2023 cotton; Dhaswadi | — | Shown as window, not date |
| Sowing | Median / 90th-percentile abs. error | ≤ 7 / ≤ 15 days | — | Client records | Labelled "not yet validated against farm records" |
| Stage | Agreement with D1 harvest-timing evidence | ≥ 80% | D1 second pass | Client records | Labelled provisional |
| Yield | Field / village error | ≤ 25% / ≤ 10% | — | Client records (≥ 30 per crop) | Published only as a district-anchored band with label (§5.6) |
| Stress | Stress-type plausibility vs D1 evidence and weather (e.g. water stress only with a soil-water deficit); persistence rule applied | ≥ 80% plausible; 100% rule compliance | D1 + audit | Client records | Shown as anomaly only, type withheld |
| Raster | Field summary equals interior-pixel statistics; no filled pixels on index maps; fixed scales | 100% | Automated tests | — | Not released |
| All | `insufficient_evidence` used wherever applicable | 100% (nothing invented) | Audit | — | Not released |

---

## 9. Schedule and dependencies

Estimates assume one engineer plus Earth Engine quota; adjust after week 1.

| Week | Track A (data/model) | Track B (inference) | Track C (monitoring/delineation) | Track D (validation) |
|---|---|---|---|---|
| 1 | A1–A3 ingest (incl. local labels), split, extraction | B1–B3, B6 | C1.1 delineation benchmark | D2 harness skeleton; D4 collect official statistics; D3 intake schema |
| 2 | A4–A6 label checks, retrain (year-robust, intercrop rule), evaluate | B4, B5, B7 | 5.1 raster data layer + caching; C1.3 cadastral alignment on 1 pilot village | D1 sampling frame + interpretation protocol |
| 3 | Label-shift correction, model card | Dhaswadi re-run, no crop filter | 5.2–5.3 smoothing, curve fitting, sowing | D4 checks on re-run; sowing bound tests |
| 4–5 | — | — | 5.4–5.7 stage, crop check, stress (incl. village normal, waterlogging), handoff; C1.2–C1.4; 5.8 raster products (COG writer, tiles) | **D1 pass 1 (early Nov)**; shadow-mode runs (3 villages) |
| 6 | — | — | 5.6 yield Stage 1 (district-anchored); 5.8 frontend overlay + bank report export | Gate report §8 (measurable-now gates) |
| Jan | Retrain with client-record labels if any | — | Yield Stage 2 when ≥ 30 records per crop | D1 pass 2 (cotton confirmation); updated gate report |

**Critical path:** B1–B3, then the Dhaswadi re-run, then D1 pass 1 + D4 checks, then the 5.3 sowing rework, then the gate report.

---

## 10. Decisions

**Resolved 2 Oct 2026**
- No field survey → §6 four-source validation.
- No new crops or seasons → §1.4 local labels plus open-set handling.
- No yield data → §5.6 two-stage yield.
- Cadastral offset → C1.3 co-registration.
- Gates accepted → §8.
- "Cotton" naming → §5.6.

**Still open**
1. **D1 interpreters:** who in the team can do blind, two-person image interpretation (about 300 fields, roughly 2–3 person-days per pass)?
2. **Very-high-resolution imagery:** do we have licensed sub-metre imagery for D1 and cadastral alignment, or do we rely on Sentinel-2 plus public basemaps (view-only)?
3. **Client records:** what format do clients currently share (fields, units, quintal/acre or t/ha)? Needed for the D3 schema.
4. **Pilot village for cadastral alignment:** which village has survey-number plots? Ideally one near Dhaswadi.

---

## Appendix: audit findings mapped to plan items

| Finding (Dhaswadi 2026) | Evidence | Plan item |
|---|---|---|
| Single-crop filter turned confident fields into "Cotton 1.0" | `area_classifier.py:1021-1028`; 1,547 / 1,547 cotton at confidence 1.0; Abstained fields with "p_max 0.24" at 1.0 | B1, B2 |
| "Cotton" cycles median 110 days (training cotton 180–196) | classification geojson | A, B3, 5.4 crop check |
| Window runs to 15 Dec on a 2 Oct run; empty periods filled with last value | `area_classifier.py:171`, `crop_detector.py:155` | B3, B4 |
| Cotton training data centred on Saurashtra | train_500 median 21.5 N, 71.1 E | A1–A5 |
| Region guard and season mask never applied | `area_classifier.py:123-141` | B5 |
| 554 of 1,090 green-ups on the first composite (21 Apr) → sowing 17 Apr | `zones.py:71-73`, `sowing.py:105-106` | 5.3 (1) |
| Calendar prior widens to fit the cue; 1e-6 floor makes fusion a vote | `sowing.py:214, 263-267` | 5.3 (4) |
| 425 fields rejected on season length mid-season; wrong April dates pass | `pipeline.py:325-339`, `progress.py:127-136` | 5.4 |
| 1–4 points per farm in village runs | `observe.py:139-145`; 996 / 1,090 zones | 5.1 |
| 11-day median composites; shared date grid | `observe.py:452-455` | 5.1 |
| Zone split impossible (needs 12 pixels, max 9) | `library.py:17` | 5.1, 5.5 |
| Stress missing for 560 farms; relative to farm's own points | `stress.py:63-70` | 5.5 |
| "Tissue Damage" likely from edge bare-soil points | `stress.py:158`, no inward buffer in village path | 5.1, 5.5 |
| Yield range 0.40–0.65 (sd 0.04); median pinned to peak-canopy ≈ 0.45 baseline | `yield_model.py:94-106`; retention = 1 for 876 farms | 5.6 |
| Irrigation detection never fires | `weather.py:66` vs 11-day spacing; 0 events | 5.6 |
| Cotton picks in Aug–Sep; harvest windows 7–20 Oct | `progress.py:99-111`, `:141-156` | 5.4 |
| Rain 605.5 mm identical for every field | village buffer | 5.1 (labelled village-level) |
| Farmer sowing dates ignored in village runs | `monitoring_runner.py:257` | 5.7 |
| Map label "tier2_v1" is the extractor version, not the model | `crop_detector.py:63`, `area_classifier.py:1049` | B6 |
| Boundaries traced on a 5 m grid; IoU 0.39 | `field_delineation.py:1083-1107` | C1 |

### References
- Atzberger, C. & Eilers, P. (2011). Weighted Whittaker smoothing of NDVI time series.
- Beck, P. et al. (2006). Double-logistic fitting for phenology.
- Sakamoto, T. et al. (2005). Shape-model fitting for crop phenology.
- Saerens, M. et al. (2002). Adjusting classifier outputs to new class priors.
- Olofsson, P. et al. (2014). Good practices for estimating area and assessing accuracy of land change.
