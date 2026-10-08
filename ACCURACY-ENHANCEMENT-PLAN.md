# Crop Classification, Delineation and Monitoring: Accuracy Enhancement Plan

**Goal:** field-level crop, boundary, sowing, stage, stress and yield outputs that are accurate enough, and honest enough about uncertainty, to share with banks and other stakeholders.

**Trigger:** audit of the Dhaswadi Kharif 2026 run (`Crop_Monitoring/Results/`), 2 Oct 2026.
**Status:** revision 3 (3 Oct 2026). Tracks A and B and the monitoring rework are in. Revision 3 corrects three results from the Dhaswadi Kharif 2026 cotton run after those changes (§12).

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

## 11. Implementation status (3 Oct 2026)

### 11.1 What was built

| Plan item | Status | Where | Tests |
|---|---|---|---|
| **B1** no rescaling over requested crops; "Not requested" outcome | Done | `crop_analysis/area_classifier.py` (`decide_crop`) | `tests/test_classification_decisions.py` |
| **B2** per field: `model_top_crop`, `top2_crop`, `p_top1/p_top2`, `margin`, `status`, `abstain_reason` | Done (GeoJSON + CSV audit columns) | `area_classifier.py`, `api/classification_export.py` | same + `test_area_classifier.py` |
| **B3** window ends at the run date; only real in-cycle scenes, using the same helpers training uses (`cycle_scene_date_bounds`, `collect_scenes_between`) | Done | `area_classifier.py`, `crop_detector.py` | same |
| **B4** in-season mode | Partial: `provisional` status, `season_progress` warning, `season_complete` flag. Truncated-cycle accuracy is measured in Track A | `area_classifier.py` | same |
| **B5** region guard + season mask switched on | Done. The crop calendar is now one shared module | `crop_analysis/region_guard.py`, `crop_analysis/crop_calendar.py` (training copy re-exports it) | same |
| **B6** model provenance (`model.name`, `sha256`, `extractor_version`) | Done | `area_classifier.py` | same |
| **B7** Fallow split into Fallow / Insufficient data / Unclassified | Done | `area_classifier.py` (`classify_without_cycle`) | same |
| **A1–A2** Marathwada ingest + frozen spatial split | Done | `Crop_classification_model/src/ingest_mh.py`, `data/splits/mh2023_split.json` | dry-run audit |
| **A3** extraction | Done: 2,314 parcels × 80 bins, both bbox and polygon footprints, 71% valid | `data/01_scenes_mh.parquet` | — |
| **A4–A6** label QA, retrain, evaluation | See §11.3 | `src/run_mh.py`, `src/mh_*.py` | `src/test_run_mh.py` |
| **C1.1** delineation benchmark on Marathwada parcels | Done for 35 of 40 sites (stopped at a time limit) | `src/eval_delineation.py --gt mh2023` | — |
| **C1.3** cadastral co-registration + segment constraint | Done; wired into classification via the `cadastral_plots` input | `crop_analysis/cadastral_align.py` | `tests/test_cadastral_align.py` |
| **5.1** raster data layer: per-scene S2, Landsat, S1; village grid; interior / inner / full pixel tiers; cache | Done | `Crop_Monitoring/src/raster/{grid,stack,fetch}.py` | `tests/test_raster_core.py` |
| Landsat → S2 cross-calibration | Done; median of per-pair fits (a pooled fit is flattened by noise) | `raster/indices.py` | yes |
| **5.2** Whittaker upper-envelope smoothing, bare-soil-first phenology, gap length | Done | `raster/smooth.py`, `raster/phenology.py` | yes |
| Crop reference curves from labelled data | Done: Cotton (388 parcels), Soyabean (446), Tur (35), from 2023 training blocks only; other crops keep parametric curves | `build_reference_curves.py`, `reference/crop_reference_curves.json` | yes |
| **5.3** sowing: onset prior, curve-fit and radar cues, robust posterior, P10–P90 | Done | `raster/sowing.py`, `raster/reference.py` | yes |
| **5.4** crop check, crop group, cotton multi-pick window, fits stop after harvest | Done | `raster/reference.py`, `raster/engine.py` | `tests/test_raster_engine.py` |
| **5.5** stress vs same-crop fields at the same days after sowing (spread widened by sowing uncertainty), persistence, waterlogging, damage, village normal 2019–2025 | Done | `raster/stress.py`, `raster/village_normal.py` | yes |
| **5.6** yield Stage 1 (district-anchored index; withheld when the crop is unknown or disputed) and Stage 2 `calibrate()` | Done; Stage 2 waits for ≥ 30 client records per crop | `raster/yield_index.py` | yes |
| **5.7** handoff: real probability, farmer sowing dates, raster engine as default | Done. `MONITORING_ENGINE=point` keeps the old engine for shadow runs | `api/monitoring_runner.py` | `tests/test_monitoring_runner_raster.py` |
| **5.8** COG + PNG overlays with fixed colour-blind-safe scales and hatched no-data; raster file endpoint; farm report card | Done | `raster/products.py`, `api/app.py`, `api/monitoring_export.py` | `tests/test_monitoring_export_records.py` |
| Frontend: raster overlay control, field record card, report download, classification audit fields, single-crop warning | Done (`tsc` clean, `next build` passes) | `frontend/app/...` | type-check + build |
| **Track D**: metrics, Olofsson area, D1 sampling + blind sheets + image chips, kappa, D3 client intake, D4 official checks, gate report | Done | `evaluation/` | `evaluation/tests` (86) |
| Environment | A foreign `PROJ_LIB`/`GDAL_DATA` (PostGIS) is dropped at import; conda DLL folders registered in backend `config.py` | `Crop_Monitoring/src/_bootstrap.py`, `backend/.../config.py` | suites pass under `.conda` and miniconda |

**Track A1–A2 detail:**
- 2,314 new parcels: 1,499 cotton, 815 soybean.
- 46 cotton and 300 soybean duplicates of existing training parcels are kept train-only.
- Test set: 499 cotton, 215 soybean and 100 tur, in 27 blocks.

**C1.1 detail:** median IoU was watershed 0.30, FTW 0.22, SNIC 0.34.

**C1.3 detail:**
- Gate: held-out residual ≤ 10 m and ≥ 60% edge agreement.
- Tie points are aperture-aware: a block of mostly parallel lines can't give a reliable local shift, so it anchors at zero.

### 11.2 Dhaswadi Kharif 2026, re-run with the fixes (as of 2 Oct 2026)

**Classification** (no crop filter, window ending 2 Oct): 1,786 of 2,355 fields return *Insufficient data*, 380 return *Unclassified*, and about 140 fields get a crop name.

Measured cause:
- Inside cycles still in progress, the classifier sees only 2–4 clear 10-day composites; it was trained with at least 5.
- 6 of the 16 bins were empty, partly because classification composites drop any scene more than 70% cloudy across the whole tile.

**The old "438 ha Cotton at confidence 1.0" came from the cotton-only rescaling plus a window filled through December, not from evidence.** Crop names for this village need the post-harvest run.

**Monitoring** (raster engine, all 2,355 fields; crop "unknown" where classification gave no name):

| | Old point engine | New raster engine |
|---|---|---|
| Clear looks per field | 1–4 points on 11-day composites | median 45 real scenes (37 S2 + 17 Landsat + 15 S1 dates) |
| Sowing in April–May | 685 of 1,090 (63%) | 18 of 1,946 dated fields (1%) |
| Sowing in June–July | 394 (36%) | 1,927 (99%); median P10–P90 window 13 days |
| Monsoon onset | not used | 25 June 2026 (75 mm rule) |
| Crop group decided | — | 368 fields (275 short-season, 93 long-season) |
| Crop group not decided | — | 875 "too early", 842 ambiguous, 270 no fit (details below) |
| Stress | relative to a farm's own 1–4 points | scored for 373 fields that have a valid cohort; withheld elsewhere |
| Yield | 0.40–0.65 t/ha, effectively constant | index only where a crop is named; withheld for unknown or disputed crops |
| Village vs 2019–2025 normal | — | near normal |

On the undecided crop groups:
- **Too early (875):** 62–88 days after sowing, before short-season crops visibly senesce.
- **Ambiguous (842):** the curve fits both groups within noise, which is what intercrops and mixed pixels look like.

**Outputs:**
- `Crop_Monitoring/outputs/dhaswadi_2026_final/`: `monitoring.json`, `cog/`, `png/`, `products.json`.
- `Crop_Monitoring/Results/Dhaswadi_kharif_2026_raster_records.csv`.
- `Crop_Monitoring/Results/Dhaswadi_kharif_2026_rerun.geojson`.

**What this means for bank reporting now:**
- Supportable for the fields above: sowing dates (with windows), stage and crop group.
- Not supportable on 2 Oct for this village: crop names, area by crop and yield.
- Re-run monitoring around 25 Oct, when most "too early" fields pass 90 days after sowing.
- Re-run classification after harvest (soybean late Oct–Nov; cotton picking Oct–Jan).

### 11.3 Track A results (full report: `Crop_classification_model/reports/mh_eval.md`)

New model `crop_classifier_tier1_mh_v1` (same estimator, calibration and bundle format as the shipped model). Trained on 9,859 cycles: the augmented set plus Marathwada, minus the held-out blocks, with augmentation and capped class weights.

**Frozen Marathwada test set, true-polygon features (what production uses), with 95% Wilson intervals:**

| | Shipped `tier1_v1` | New `tier1_mh_v1` | Gate §8 |
|---|---|---|---|
| Cotton recall (n=493) | 0.586 [0.54, 0.63] | **0.955 [0.93, 0.97]** | pass |
| Cotton precision | 0.986 | **0.987 [0.97, 0.99]** | pass |
| Soybean recall (n=120) | 0.850 [0.78, 0.90] | 0.808 [0.73, 0.87] | **fail** (needs ≥ 0.85) |
| Soybean precision | 0.927 | 0.924 [0.86, 0.96] | pass |
| Tur recall (n=23) | 0.913 (in-sample for shipped) | 0.652 | reported |
| Calibration error (ECE) | 0.100 | 0.086 | **fail** (needs ≤ 0.05) |
| Bounding-box features, cotton / soybean recall | 0.586 / 0.806 | 0.951 / 0.741 | — |

- **Cotton:** the shipped model called 135 of 493 Marathwada cotton parcels "Tur". The new model fixes this.
- **Soybean:** misses go mostly to other short-season classes.
- **No regression:** blocked 5-fold cross-validation on identical rows gives balanced accuracy 0.656 for the new recipe vs 0.646 for the shipped recipe. The shipped card's 0.749 was on a different row set.
- **In season:** features truncated at 90 days after sowing are buildable for only about 10% of test cycles; at 120 days for about 50% (accuracy 0.75). This confirms crop names are a post-harvest product, and crop groups are the in-season one.
- **Deployment (switched 3 Oct 2026, on request):**
  - `tier1_mh_v1` is the production model: `.env` `CROP_MODEL_PATH` and `config.DEFAULT_CROP_MODEL_PATH` both point to it.
  - It loads its own region-support table (`crop_classifier_tier1_mh_v1.region_support.json`).
  - `tier1_v1` is kept for rollback.
  - The soybean-recall and calibration gates are still open, so soybean names stay `provisional` where the monitoring agreement rule is not met.
- **Leakage gap:** recorded in the bundle as +0.224. Random split 0.870 vs blocked 0.646, same recipe and rows. Never quote the random-split figure.
- **Open issues:**
  - Soybean recall and calibration need more local soybean (the 516 Unchecked rows are down-weighted, and 103 of them are on the label-review list), plus a recalibration on Marathwada blocks.
  - The label-shift prior was not applied because there is no official crop-shares file.

### 11.4 Not done / needs input

- **Official statistics** (district crop shares, district yields, weekly sowing progress): the templates in `evaluation/reference/` are header-only, and no figures were invented. Yield in t/ha stays off until they are filled.
- **D1 image interpretation:** sampling, blind sheets and image chips are built; interpreters are needed (two passes: early Nov and Jan).
- **Cadastral alignment on real plots:** built and tested on synthetic villages; needs one pilot village's survey-number plots.
- **Classification cloud handling in season:** short gaps are now filled for cycle detection only, and a short clear record is classified as Others with the model's lean (§12.1). Composites still use the 70% tile cloud cap that training used. A SAR-and-weather retrain is the step that turns a short record into a calibrated crop name.
- **C1.2 delineation tuning** against the Marathwada benchmark, and a full 40-site benchmark report.
- ESLint in `frontend/` fails on startup (a pre-existing `brace-expansion` override), unrelated to these changes.

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

## 12. Revision 3 — Dhaswadi cotton run, 3 Oct 2026

The cotton-only run after Tracks A and B (`crop_classifier_tier1_mh_v1`, observations through 3 Oct) came back as:

| Class | Fields | Area | Share |
|---|---|---|---|
| Insufficient data | 1,383 | 382.1 ha | 57.3% |
| Not requested | 518 | 148.6 ha | 22.3% |
| Unclassified | 219 | 63.8 ha | 9.6% |
| Abstained | 191 | 56.8 ha | 8.5% |
| Cotton | 59 | 15.3 ha | 2.3% |

Field outlines covered a half or a quarter of the visible farm. Three causes, and the rule that replaces each.

### 12.1 Cloudy optical scenes were being skipped

"Insufficient data" was not a lack of weather or radar. The optical classifier refused a field when fewer than 5 clear Sentinel-2 looks fell inside its cycle (`MIN_CYCLE_SCENES`), and a green field with a monsoon gap longer than 45 days was treated the same way. June–August cloud in Marathwada makes that the common case in early October, so most of the village never reached the model.

What does **not** fix it: filling those holes and handing the filled values to `tier1_mh_v1` as if they were clear scenes. That model was trained on real clear looks. Invented green values are the bug Track B removed.

What does fix it, and is now the inference rule:

1. **Cycle detection** fills a gap of one or two 10-day composites (linearly, between two real looks). A longer hole stays empty. The classifier never sees those filled values.
2. **The model still runs** when a cycle has at least 2 real clear looks. The feature grid already interpolates the real looks onto its fixed time axis, and `n_scenes_real` tells the model the record is short.
3. **A named crop** (Cotton, when Cotton was requested) is printed only when the call clears the confidence gate and the cycle has at least 5 real looks. Below that, the printed class is **Others** and the model's lean is kept for the hover. The field is not dropped.
4. **Insufficient data** remains only when the field was barely seen: fewer than 3 clear looks in the season, or a long gap and no canopy. A visible canopy with no annual cycle is Others, not a blank.
5. **A SAR-and-weather retrain is the next training step, not a silent swap.** Sentinel-1 and village weather are not features of `tier1_mh_v1`. Training them in (radar phenology through the monsoon, optical features only from real clear looks, weather as a season prior and not a field feature) is what lets a short optical record become a calibrated crop name. Until that model exists, a short record is Others plus the current model's lean, which is an honest answer rather than a skipped field.

### 12.2 One requested crop needs one other class

"Not requested", "Unclassified" and "Abstained" were three names for the same map fact: this is not the crop the user asked to see, and the model may still have a lean. On a cotton-only run the printed classes are now:

| Printed class | When | Hover |
|---|---|---|
| Cotton | Model names Cotton, confidence clears the gate, and the cycle has at least 5 clear looks | — |
| Others | Any other model name, a weak call, a region or season guard, or a green field with no annual cycle | The model's crop, in brackets: Others (Soyabean) |
| Fallow | Bare on the clear looks we do have | — |
| Insufficient data | The field was barely seen | Why |

The model's probabilities are still not renormalised over the requested list. Others is never relabelled Cotton. Older results that say "Not requested", "Unclassified" or "Abstained" still draw.

### 12.3 A farm was being cut on a faint interior edge

Watershed merging treated the 75th percentile of *shared-edge* strengths as a bund. In a village full of faint ridges (moisture streaks, partial canopy, a residual cloud edge) that percentile sits on the ridges, so a rectangular farm stays in two or three pieces. Those pieces then get different classes and cannot be dissolved back together.

A line now separates two regions only when it is at least `merge_ratio` of the **95th percentile of the edge image** (a real bund). A faint streak through one farm is absorbed. A bund at full strength still separates neighbouring fields, including a one-pixel bund. Pieces below the minimum field size still join their neighbour.

### 12.4 Cloud-robust classifier: radar and optical fused per field

This is the retrain that 12.1 item 5 called for. The code is in `crop_analysis/fused_features.py` and `fused_classifier.py`, and it is trained by `src/train_fused.py`.

- **Inputs:**
  - Sentinel-1 VV/VH (dB) and cloud-masked Sentinel-2 reflectance (Cloud Score+ ≥ 0.6), from 1 May, on the shared 10-day grid.
  - A SAR→NDVI imputer, scored on the frozen test parcels: R² 0.886, RMSE 0.095.
  - Weighted Whittaker smoothing (λ 400) with an optical-only upper envelope; imputed steps get weight 0.3.
- **Features:**
  - Per-step fused NDVI, VH, cross-ratio and NDMI, plus an observed/imputed flag.
  - Season summaries.
  - (v2) the same curve aligned to each field's own green-up.
- **Partial seasons:** the model learns from rows cut at 1 Jul, 1 Aug, 1 Sep, 1 Oct, 1 Nov and full season.
- **"Insufficient data"** now means only that neither radar nor optical saw the field at all. Thin evidence lowers confidence instead.
- **Calibration:** temperature plus per-class bias, fitted on Maharashtra out-of-fold rows, separately for in-season reads (< 1 Nov) and late-season reads.

Frozen Marathwada test (814 parcels, never used for training or tuning), v2:

| As of | Cotton R / P (argmax) | Cotton P @ abstain rule [95% CI] | Soyabean R / P | ECE |
|---|---|---|---|---|
| 1 Oct | 0.894 / 0.798 | 0.844 [0.810, 0.873] | 0.358 / 0.507 | 0.055 |
| Full season | 0.956 / 0.964 | 0.979 [0.962, 0.989] | 0.828 / 0.868 | 0.129 |

The model is **under-confident in every bin**: fields at 0.7–0.9 confidence are right 91% of the time at 1 Oct and 96% at full season. That is the safe direction for a bank.

**It was installed, run on Dhaswadi, and withdrawn the same day.**
- On Dhaswadi as of 3 Oct 2026 it left no field as Insufficient data: median 9 optical and 10 radar looks per field, 36% of steps imputed.
- But its lean was "Onion" for 1,013 of 1,368 fields.

The cause is a **year-timing confound**:
- Almost all cotton and all soybean labels are from 2023, a late-monsoon year.
- The earliest-greening class in training is Onion (Nashik belt, 2022–23).
- Dhaswadi (Latur district, 18.8°N 76.8°E) greened earlier in 2026, and its median curve matches training Onion almost exactly.

The frozen test is the same year (2023), so it cannot see this. `src/eval_timeshift.py` now replays the frozen test with every observation date moved:

| v2, frozen test | Cotton R | Soyabean R | Tur R | Predicted mix moves to |
|---|---|---|---|---|
| 1 Oct, as observed | 0.894 | 0.358 | 0.35 | — |
| 1 Oct, dates −20 d (early monsoon) | 0.705 | 0.591 | 0.01 | Onion, Groundnut |
| 1 Oct, dates +20 d (late monsoon) | 0.834 | **0.000** | **0.000** | Cotton, Rice, Banana |

**v3** (`crop_classifier_fused_v3`) trains on every training parcel a second time with all dates moved by ±10 and ±20 days, label unchanged, so timing alone stops being a shortcut. It fixed the timing failure and lifted every class:

| v3, frozen test | Cotton R / P | Soyabean R / P | Tur R | ECE |
|---|---|---|---|---|
| 1 Oct | 0.852 / 0.810 | 0.419 / 0.506 | 0.39 | 0.072 |
| Full season | 0.962 / 0.978 | 0.958 / 0.936 | 0.84 | 0.096 |
| Full season, dates −20 d | Cotton R 0.964 | Soyabean R 0.967 | 0.77 | — |
| Full season, dates +20 d | Cotton R 0.968 | Soyabean R 0.800 | 0.70 | — |

**v3 still called 1,619 of 2,480 Dhaswadi fields "Onion"**, so it was also withdrawn. The remaining cause is **levels, not timing**:
- Dhaswadi's 0.2 ha delineated fields include tree-lined bunds and mixed pixels.
- In May they sit at NDVI ~0.19, VH −19.6 dB and cross-ratio −7.8 dB. Training Onion (irrigated Nashik belt) sits at 0.21, −20.1 dB and −9.2 dB. Training cotton and soybean sit at 0.15–0.17, about −22 dB and about −10 dB.
- A model that reads absolute levels learns the place, not the crop.

**v4** (feature version `fused_v4`) reads every NDVI, NDMI, VH and cross-ratio value as an offset from the field's own pre-season floor: the low end of its first six 10-day steps from 1 May. Absolute peak and floor are no longer model inputs. Absolute NDVI is still used for the fallow test. A bundle trained on another feature version is refused at load.

**Root cause: crop labels are tied to one year.** A quick v4 model scored the same 120 Dhaswadi fields in three seasons:

| Season | Leans (all-years training) | Leans (2023-only training) | Median NDVI peak |
|---|---|---|---|
| 2023 | Cotton 77, Soyabean 33 | Cotton 79, Soyabean 33 | 0.89 |
| 2024 | Tobacco 80, Cotton 31 | Tobacco 50, Soyabean 46, Cotton 20 | 0.75 |
| 2026 | Onion 43, Cotton 25, Banana 17 | Rice 40, Cotton 40, Tobacco 21 | 0.71 |

Training labels by year:
- Soyabean: 1,176 of 1,176 parcels from 2023.
- Cotton: 2,078 of 2,138 from 2023.
- Tur: 463 of 467 from 2023.
- 2024 rows: 179 of 266 are Tobacco.

A model can therefore learn "not a 2023-looking season, so not Soyabean or Cotton". Training on 2023 alone removes that shortcut but cannot teach inter-annual variation. With one season of labels for the crops that matter, no model's 2026 crop names can be validated.

On relative features alone, frozen-test Soyabean recall at 1 Oct rises from 0.42 to 0.66–0.68. That improvement carries forward.

**What unblocks it: labelled fields from at least one more season**, even 100–200 Cotton, Soyabean and Tur fields with a location or survey number, from any year since 2024. Candidate sources:
- client and loan records (crop declared at sanction);
- 7/12 extracts or E-Peek Pahani crop entries for the plots;
- PMFBY insured-crop records.

These become a second-year validation set. If there are enough of them, they also become training data and a calibration year.

**Until then:**
- Area classification stays on `tier1_mh_v1`.
- The fused path stays built and tested but uninstalled.
- "Insufficient data" remains an honest answer for fields without enough clear looks.

**Deployment gate for any fused model:**
1. The frozen test at least matches v3.
2. Under ±20-day shifts, full-season Cotton and Soyabean recall stay within 10 points. In-season rows are excluded, because a shifted crop is genuinely less advanced at a fixed cut-off.
3. **Known-region sanity.** On Dhaswadi (Latur, a soybean district), the model's lean must be led by kharif field crops (Soyabean, Cotton, Tur), not by an irrigated-belt class.

The backend picks up `models/crop_classifier_fused_v4.joblib` only when that file is installed. Until then area classification runs on `tier1_mh_v1`.

### 12.5 Monitoring starts on 1 May; bare soil comes from the radar reference

- **Season window.** The raster engine read imagery from March for kharif, so a pre-monsoon crop or an orchard could put "sowing" in April. The kharif window now starts on 1 May (rabi on 15 Sep). Dhaswadi re-run as of 2 Oct: Sentinel-2 read from 2026-05-01, median sowing 30 June, 1,787 fields dated.
- **Canopy already up on 1 May.** A sowing median before the window is an extrapolation, not a measurement. Those fields now get status `before_window` and no date: sugarcane, orchards and early irrigated plots, 6 fields at Dhaswadi. The UI shows "Before 1 May (canopy already up at season start)".
- **Bare soil from radar.** Bare soil no longer comes from boundary or other in-village pixels, which may carry a crop. It comes from `Crop_Monitoring/reference/sar_reference.json`, Sentinel-1 signatures consistent with the literature: bare VH ≤ −20 dB and VH−VV ≤ −8.5 dB; canopy VH ≥ −19 dB and VH−VV ≥ −9 dB. The cross-ratio is the moisture-robust term. A radar emergence needs a bare look first, then two consecutive canopy looks. The radar-to-sowing lag is derived per crop from the reference curves (Cotton 24 d, Soyabean 14 d, Tur 20 d).

### 12.6 Field boundaries: licence-clean evidence from Fields of The World

**Benchmark.** Frozen Marathwada parcels: 40 sites, 520 surveyed fields, median 0.256 ha. Metric is the median of each field's best IoU. Script: `src/eval_delineation.py`.

| Delineation | Median IoU | Predicted / true area |
|---|---|---|
| Watershed, 12-month S2 edges (production until now) | 0.177 | 5.1× |
| + season-profile merge | 0.132 | 7.1× |
| Kharif-only edges | 0.098 | 9.6× |
| + FTW Global same-field join only | 0.140 | 6.6× |
| + FTW Global outlines in the edge map | 0.228 | — |
| + FTW U-Net boundary on own-year S2 | 0.297 | — |
| **+ FTW U-Net + FTW Global outlines** | **0.331** | — |

What this means:
- **Merging is not the cure for split farms.** On these parcels our fields are already about 5× too large, and every merge variant (season profile, FTW same-field join) lowered IoU. Season-profile merging is now off by default (`DELINEATION_PROFILE_MERGE=1` turns it on).
- **Adding the right lines is the cure.** The split farms in the Dhaswadi screenshots and the over-large fields here are the same faint-edge problem. The fix is better boundary evidence, which also stops cuts along non-boundaries.
- **FTW Global (CC-BY-4.0).** 2024/2025 field polygons from the PRUE model, read over HTTPS from `data.source.coop/ftw/global-data`. Only the overlapping row groups are fetched (about 170 MB around a village, cached). The old `tge-labs` S3 path is gone, and the reader was moved. Its outlines join the edge map at weight 0.35. Used directly as fields it scored 0.169 (earlier India-10k run), so it is evidence, never the answer. Default on (`DELINEATION_USE_FTW`).
- **FTW U-Net** (`crop_analysis/ftw_model.py`). The CC-BY checkpoint `3_Class_CCBY_FTW_Pretrained` (EfficientNet-B3 U-Net) runs on our own two-date Sentinel-2 for the run year (rabi 15 Jan–15 Mar, late kharif 1 Sep–31 Oct): L2A DN / 3000, B4 B3 B2 B8 × 2. On these smallholder plots it calls most pixels "boundary" in absolute terms, so its probability is scaled by its own 98th percentile before blending. It captures this season's re-bunding, which a 2024/25 snapshot cannot. Default on (`DELINEATION_USE_FTW_MODEL`). It needs CPU torch, `segmentation-models-pytorch` and the checkpoint; the Dockerfile installs all three, and without them delineation skips this step.
- **Not used.** The non-commercial `FTW_PRUE_EFNET_B5` checkpoint; Delineate Anything (AGPL-3.0); Google or Esri basemap tiles. The Google Maps Platform terms prohibit tracing and ML-derived content from Maps imagery, and Esri's basemap terms restrict analysis outside a licensed ArcGIS deployment. A segmentation model run over those tiles produces exactly that derived content, whether or not the tiles are kept. Licensed routes plug into the same edge map through `vhr_imagery_path`: the ALU API, purchased VHR, or ArcGIS with an analysis licence.

### References
- Atzberger, C. & Eilers, P. (2011). Weighted Whittaker smoothing of NDVI time series.
- Beck, P. et al. (2006). Double-logistic fitting for phenology.
- Sakamoto, T. et al. (2005). Shape-model fitting for crop phenology.
- Saerens, M. et al. (2002). Adjusting classifier outputs to new class priors.
- Olofsson, P. et al. (2014). Good practices for estimating area and assessing accuracy of land change.
