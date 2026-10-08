# `evaluation/`: Track D, validation without field visits

This package implements Track D of `ACCURACY-ENHANCEMENT-PLAN.md` (§6), the output-contract area rule (§7) and the release-gate report (§8).

**Rule:** nothing here invents ground truth or official statistics. Some data has to come from people: interpreters, clients and official tables. For that data the package ships header-only templates. Until the templates are filled, it reports `not_available` or `NOT MEASURED`, and never `PASS`.

| Module | What it does |
|---|---|
| `metrics.py` | Per-class precision and recall with Wilson 95% CIs, F1, balanced accuracy, macro F1, ECE (10 equal-width bins), coverage and precision at a confidence threshold, Cohen's kappa with Landis & Koch wording, confusion matrix |
| `area.py` | Stratified error matrix in area proportions, user's / producer's / overall accuracy with SE and 95% CI, and bias-adjusted area with 95% CI (Olofsson et al. 2014, generalised per Stehman 2014 for strata that differ from map classes) |
| `d1_sampling.py` | D1 sample draw, blind interpreter sheet and key, Sentinel-2 chips and contact sheets, two-interpreter import (kappa, disagreements, adjudication, final reference) |
| `d3_client.py` | Client-record JSON Schema and CSV template, validation, unit normalisation to t/ha, matching (by `field_id`, or by spatial join with the largest overlap and IoU ≥ 0.3), comparison table and disagreement log |
| `d4_official.py` | Mapped crop shares vs official shares (flag beyond ±15 pts), village yield vs the district's 5-year range, village sowing CDF vs weekly sowing progress |
| `sowing_checks.py` | Upper-bound violations, onset lower bound, optical–radar agreement within 10 days, client-date median and P90 error |
| `gates.py` | Evaluates every §8 gate as PASS / FAIL / NOT MEASURED and writes JSON and Markdown |
| `run.py` | CLI (`python -m evaluation.run ...`) |
| `reference/` | Header-only templates: `official_crop_shares.csv`, `district_yields.csv`, `sowing_progress.csv`, `client_records_template.csv`, `client_record.schema.json` |

Run the tests:

```bash
cd C:/Users/gopik/Downloads/agri_credit_pipeline
C:/Users/gopik/miniconda3/python.exe -m pytest -q evaluation/tests
```

---

## D1: independent image interpretation

### 1. Draw the sample, build the blind sheets and fetch the chips

```bash
python -m evaluation.run sample \
  --results Crop_Monitoring/Results/Dhaswadi_kharif_2026.geojson \
  --out d1_dhaswadi_pass1 --n 300 --min 30 --seed 20261102 \
  --chips --season-start 2026-05-01 --season-end 2026-12-31
```

**Strata:**
- predicted crop;
- `Other/Abstained` (status `abstained` / `out_of_support` / `insufficient_evidence`, or crop Abstained / Other);
- `Monitoring-flagged` (status `phenology_disagrees`, or ids passed with `--flagged-ids file.txt`).

**Allocation:** proportional to area, with a minimum of `--min` per stratum (or every field when a stratum has fewer). Within a stratum, fields are drawn with probability proportional to area by default (`--selection area`). This mimics "random points within strata", which is the design the Olofsson estimator assumes. The draw is deterministic for a given `--seed`.

**Outputs:**

| File | Who sees it |
|---|---|
| `interpretation_sheet_A.csv`, `interpretation_sheet_B.csv` | Interpreters. Columns: `sample_id, lat, lon, chip_dir, label, confidence, notes`. Blind: no crop, status, probabilities, stratum or field_id, checked by `assert_blind`. Sample ids are assigned after a random shuffle. |
| `adjudication_sheet_C.csv` | The third person (header only) |
| `labels_allowed.txt` | Interpreters (label vocabulary) |
| `chips/<sample_id>/contact_sheet.html` | Interpreters. Months in columns, true colour (B4/B3/B2) and false colour (B8/B4/B3) rows, plus a view-only satellite basemap link. `chips/index.html` lists every sample. |
| `KEY_do_not_share_with_interpreters.csv` | Analyst only. Maps sample_id to field_id, stratum, map_class and geometry. |
| `sample.csv`, `strata.csv`, `map_areas.csv` | Analyst only. Needed by `area`. |

**How the chips are made:**
- `COPERNICUS/S2_SR_HARMONIZED`, masked where Cloud Score+ `cs_cdf` < 0.6, then the monthly median;
- a box of at least 600 m around the field (enlarged for big fields), with the field outline drawn;
- **one fixed stretch for every chip**: true colour 0–2500 with gamma 1.2, false colour B8 0–5000 and B4/B3 0–2500;
- masked pixels show as dark grey, and months with no scene are marked "no Sentinel-2 scene";
- the parameters are written to `chips/manifest.json`.

Earth Engine starts through `Crop_Monitoring/src/_bootstrap.py:init_ee`, which uses `GEE_PROJECT` and `GEE_SA_KEY_PATH` from `backend/Credit_assessment/.env`. That file is loaded by path, so the monitoring pipeline is not imported. Chip failures are logged per chip in `chips/chip_log.csv` and do not stop the run. Re-runs reuse PNGs that already exist.

**Timing:** run pass 1 in early November (soybean / tur / cotton split) and pass 2 in January (cotton confirmation). Use `--season-end` to extend the chip months. The model's NDVI curve is not an allowed basis for a label.

### 2. Import the two interpretations

```bash
python -m evaluation.run kappa --a sheet_A_filled.csv --b sheet_B_filled.csv \
  --key d1_dhaswadi_pass1/KEY_do_not_share_with_interpreters.csv \
  [--c adjudication_filled.csv] --out d1_dhaswadi_pass1
```

**Outputs:**
- `d1_agreement.json`: overall and per-class (one-vs-rest) kappa, counts, `usable_for_gating`;
- `disagreements.csv`: the items to send to the third person;
- `reference.csv`: the final reference, with a `resolution` of agreed / adjudicated / unresolved / missing_label / unclear.

**Rules:**
- Labels are normalised for spelling only, for example "soyabean" becomes Soyabean and "arhar" becomes Tur.
- If kappa < 0.70, `usable_for_gating = false`, and every gate that relies on D1 reports NOT MEASURED.
- "Unclear" labels and unresolved disagreements get no reference label.

### 3. Area and accuracy (Olofsson et al. 2014)

```bash
python -m evaluation.run area --reference d1_dhaswadi_pass1/reference.csv \
  --strata d1_dhaswadi_pass1/strata.csv --map-areas d1_dhaswadi_pass1/map_areas.csv \
  --out d1_dhaswadi_pass1
```

This writes `d1_area.json` and `d1_area_table.csv`. Village summaries must report area **with these CIs** (§7). The gate report takes D1 precision and recall from this estimator's user's and producer's accuracy. It does not use naive counts, because the D1 sample is stratified by map class, which biases naive recall.

**Validation of `area.py`:**
- `tests/test_area.py` reproduces the Olofsson et al. (2014) worked example (strata: deforestation, forest gain, stable forest, stable non-forest; counts 66/0/5/4, 0/55/8/12, 1/0/153/11, 2/1/9/313; 200,000 / 150,000 / 3,200,000 / 6,450,000 pixels of 0.09 ha).
- These published values are matched:
  - bias-adjusted areas and 95% CIs, to within 1 ha: 21,158 ± 6,158; 11,686 ± 3,756; 285,770 ± 15,510; 581,386 ± 16,282 ha;
  - user's accuracies ± CI, to 2 dp;
  - overall accuracy 0.95 ± 0.02;
  - producer's accuracy point values.
- **Caveat:** I was not certain of the published producer's-accuracy CI half-widths. Those are therefore tested against an independent implementation of the paper's eq. 7, not against printed numbers.
- A hand-computed two-stratum case and a "strata ≠ map classes" case are also tested.

---

## D3: client records

The template is `reference/client_records_template.csv` and the schema is `reference/client_record.schema.json`.

**Fields:**
- `field_id` **or** `boundary` (WKT or GeoJSON, EPSG:4326);
- `crop`, `season`, `year`, `sowing_date`, `harvest_date`;
- `yield_value` and `yield_unit`, where the unit is one of `t_ha`, `q_ha`, `q_acre` or `kg_acre`;
- `irrigated`, `source`;
- `consent`, which must be true or the record is rejected.

**Unit conversion:** 1 quintal = 100 kg and 1 acre = 0.40468564224 ha, so 10 q/acre = 2.471 t/ha.

```bash
python -m evaluation.run client --records client_records.csv \
  --results Crop_Monitoring/Results/Dhaswadi_kharif_analysis.json --out d3_out
```

**Outputs:**
- `client_validation_errors.csv`: row, column and message;
- `client_comparison.csv`: crop match, sowing error in days (ours − client) and whether the client date falls inside our P10–P90, yield % error and whether it falls inside the band;
- `client_disagreements.csv`: crop mismatch, sowing error > 7 d, yield error > 25 %, unmatched;
- `client_summary.json`.

**Results input:** a classification or monitoring FeatureCollection, a list of field records, or the monitoring analysis JSON (`farms` plus `fields`, with zone geometries merged per field). Keys are read flexibly.

> **Client records are not a random sample.** Use them for calibration and error discovery, and as accuracy evidence only alongside D1. The yield gate needs ≥ 30 records per crop.

---

## D4: official statistics

First fill the templates in `reference/` with real published figures, and keep the `source` column. `python -m evaluation.run templates` recreates any missing template and never overwrites a filled one.

```bash
python -m evaluation.run official --results Crop_Monitoring/Results/Dhaswadi_kharif_2026.geojson \
  --level village --name Dhaswadi --season kharif --year 2026 \
  --state Maharashtra --district Latur --crop Cotton Soyabean --out d4_out
# or: --config d4_config.json  with {"crop_shares": {...}, "yields": [...], "sowing": [...]}
```

**What each check reports:**
- **Crop shares.** Shares are taken over cropped area: fallow, abstained and non-crop are excluded from the mapped denominator and reported separately. A major crop (share ≥ 10 %, or listed explicitly) is flagged when the difference exceeds 15 pts.
- **Yields.** The area-weighted village mean is compared with the min–max of the 5 most recent prior years, and at least 3 years are needed. While yields are district-anchored (§5.6), this check is partly circular, and the result says so.
- **Sowing.** The village CDF is compared with weekly cumulative sowing, normalised to the final week by default, because reports are often given as % of normal area.

When the tables are empty or have no matching rows, each check returns `status: "not_available"`.

---

## Sowing checks and held-out metrics

```bash
# table columns: field_id, sowing_date, survey_date, irrigated, sowing_optical, sowing_radar, sowing_ours, sowing_client
python -m evaluation.run sowing --csv mh2023_cotton_sowing.csv --source mh2023_cotton --onset 2023-06-12 --out sowing_mh2023.json
# held-out classification (Track A6 output): y_true, y_pred, p_top1
python -m evaluation.run metrics --csv heldout_predictions.csv --source mh2023_heldout --threshold 0.6 --out heldout_metrics.json
```

---

## Gate report (§8)

```bash
python -m evaluation.run gates heldout_metrics.json d1_dhaswadi_pass1/d1_agreement.json \
  d1_dhaswadi_pass1/d1_area.json d4_out/official_checks.json d3_out/client_summary.json \
  sowing_mh2023.json external_gates.json \
  --results Crop_Monitoring/Results/Dhaswadi_kharif_2026.geojson --out gate_report_2026-11
```

Each gate row gives the value, threshold, result, data source and the §8 "status if not met" text. The report also lists the components that cannot be released.

**How inputs are recognised:** by their `kind`. Classification metrics are treated as held-out unless their `source` mentions d1.

**Gates measured by other tracks** (delineation, stage, stress, raster, audit) are supplied as a `gate_values` file:

```json
{"kind": "gate_values", "values": {
  "delineation_median_iou": {"value": 0.63, "source": "MH adjacent-parcel benchmark"},
  "cadastral_c13_pass": {"value": false, "source": "C1.3 pilot"},
  "stage_d1_agreement": {"value": 0.84, "source": "D1 pass 2"},
  "stress_plausible_rate": {"value": 0.81, "source": "D1 + weather"},
  "stress_rule_compliance": {"value": 1.0, "source": "audit"},
  "raster_checks_pass_rate": {"value": 1.0, "source": "raster tests"},
  "insufficient_evidence_compliance": {"value": 1.0, "source": "audit"}}}
```

**Rules:**
- A missing input, D1 with kappa < 0.70, or an input that cannot be evaluated gives **NOT MEASURED**.
- The classification precision/recall gate passes only when **both** the MH 2023 held-out set and a usable D1 pass it. That means ≥ 0.85, with a 95% CI lower bound ≥ 0.80, for Cotton and Soyabean. Tur is reported but not gated.

---

## Limitations

- **Point-sample design.** The area estimator treats each sampled field as a point sample. With `--selection area` that is approximately right: successive PPS sampling without replacement is not exactly proportional for very large fields. With `--selection uniform`, area estimates are only approximate.
- **Scene counts.** The monthly scene counts in the chip log count scenes before cloud masking, so a month can show scenes and still be mostly masked. This happens with monsoon haze in July.
- **October 2026 chips.** In the 2 Oct 2026 smoke test, October had no Sentinel-2 scenes yet. Re-run the chips for pass 1 in November.
- **CIs.** F1 is a point estimate with no CI. Kappa SE is the simple large-sample approximation.
- **D4 tables.** The D4 templates are empty. Every D4 check reports `not_available` until real tables are supplied.
- **Non-local gates.** The stage, stress, delineation, raster and audit gates are evaluated only from supplied `gate_values`. This package does not compute them.
