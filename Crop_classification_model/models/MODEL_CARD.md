# Model Card — Crop Classifier `tier1_v1`

**Artifact:** `crop_classifier_tier1_v1.joblib` (6.3 MB) — NOT the CROP_MODEL_PATH default; see "Deploying this model"
**Trained:** 2026-08-28 · **Estimator:** XGBoost 2.0.3 · **Extractor:** `tier1_v1`
**Task:** given one detected crop cycle, name the crop (18 classes) with a calibrated probability
**Design & audit:** [`../classification_model.md`](../classification_model.md)

> ## Recommendation: DO NOT enable in production yet
>
> The model fails **1 of 6** pre-registered ship gates, and it does not generalise
> to districts outside its training footprint. Run it in **shadow mode** first
> (log predictions, do not let them drive scores) as per Phase 7 of the design
> doc. `ENABLE_CROP_CLASSIFICATION` should stay `false` until the two issues in
> "Known failures" are resolved or explicitly accepted by the lender.

---

## Headline performance

All figures are **spatially-blocked** `GroupKFold(5)` over 0.25° blocks, on
7,907 attributed cycles. Chance is 0.0556.

| Metric | Value | Gate | |
|---|---|---|---|
| Balanced accuracy | **0.7492** | ≥ 0.55 | PASS |
| Macro F1 | 0.7348 | ≥ 0.50 | PASS |
| Expected calibration error | 0.0109 | ≤ 0.05 | PASS |
| Precision @ conf ≥ 0.25 | 0.765 | ≥ 0.70 | PASS |
| Coverage @ conf ≥ 0.25 | 0.959 | ≥ 0.40 | PASS |
| **Minimum class recall** | **0.214** (Gram) | ≥ 0.30 | **FAIL** |

### Numbers you should not quote

| Protocol | Balanced accuracy | What it means |
|---|---|---|
| **Blocked** | **0.7492** | the honest number — use this one |
| Random 5-fold | 0.8825 | **diagnostic only.** 233 of 280 spatial blocks contain a single crop, so a random split scores memorised neighbourhoods |
| **Leakage gap** | **+0.1333** | how much of a random-split score would be geography rather than crop |

---

## Known failures

### 1. Gram recall is 0.214 — below the 0.30 gate

Gram is misclassified as **Chilli 40.9%** of the time (and Tobacco 9.5%). Chilli's
precision is correspondingly poor at 0.509 — it absorbs false positives.

The mechanism is geographic, not agronomic. Chilli occupies only **6 spatial
blocks**, all in `SOUTHERN_PENINSULA`, and Gram has substantial southern
presence. The model appears to be learning a regional spectral signature and
labelling it Chilli. This is the dataset's spatial confounding (§A.5) surfacing
as one specific confusion.

**Consequence:** a Gram parcel has a ~41% chance of being called Chilli, which
would route it to the wrong ICAR reference curve in Stage 6.

### 2. It does not transfer to unseen districts

| | Value |
|---|---|
| Leave-one-ecoregion-out balanced accuracy | **0.1898** |
| LOEO minimum class recall | **0.0000** (Chilli) |
| LOEO precision @ 0.25 | 0.195 |
| Adversarial: predicting *ecoregion* from the same features | 0.5323 (chance 0.1667) |

Plainly: **this model works in districts that resemble its training data and must
not be trusted in a district it has never seen.** The adversarial figure confirms
the features carry substantial location signal. Any rollout should be
allow-listed to the ecoregions below, not enabled nationally.

---

## Per-class performance (blocked)

| Crop | Precision | Recall | F1 | N |
|---|---|---|---|---|
| Groundnut | 0.901 | 0.933 | 0.917 | 343 |
| Mustard | 0.800 | 0.922 | 0.856 | 498 |
| Cotton | 0.808 | 0.912 | 0.857 | 479 |
| Grapes | 0.894 | 0.882 | 0.888 | 468 |
| Tur | 0.837 | 0.865 | 0.851 | 422 |
| Sugarcane | 0.764 | 0.848 | 0.804 | 421 |
| Rice | 0.840 | 0.833 | 0.837 | 480 |
| Chilli | 0.509 | 0.825 | 0.630 | 309 |
| Maize | 0.683 | 0.824 | 0.747 | 493 |
| Banana | 0.847 | 0.792 | 0.818 | 404 |
| Onion | 0.754 | 0.791 | 0.772 | 488 |
| Potato | 0.737 | 0.773 | 0.755 | 499 |
| Soyabean | 0.705 | 0.738 | 0.721 | 252 |
| Jowar | 0.806 | 0.678 | 0.736 | 490 |
| Tobacco | 0.613 | 0.649 | 0.630 | 390 |
| Bajra | 0.529 | 0.526 | 0.528 | 477 |
| Wheat | 0.685 | 0.480 | 0.564 | 498 |
| **Gram** | 0.606 | **0.214** | 0.316 | 496 |

### Confusions above 8% of a class

| True | Predicted as | Share | Predicted in advance? |
|---|---|---|---|
| Gram | Chilli | 40.9% | no — this is the surprise |
| Jowar | Bajra | 15.7% | yes (§C.4) |
| Wheat | Mustard | 14.1% | yes (§C.4) — both rabi, ~130 d, both peak ≈0.8 |
| Soyabean | Tur | 13.5% | yes (§C.4) |
| Wheat | Maize | 13.5% | no |
| Gram | Tobacco | 9.5% | no |
| Banana | Sugarcane | 9.2% | yes (§C.4) — perennial group |
| Bajra | Maize | 8.8% | yes (§C.4) |

Six of eight were called in advance from agronomy, which suggests they are
structural rather than fixable by tuning.

---

## Competitors

| Model | Blocked balanced accuracy |
|---|---|
| **XGBoost (shipped)** | **0.7492** |
| Logistic regression | 0.7448 |
| Random forest | 0.7311 |
| `CropGrowthCurves` template match (zero parameters) | 0.0935 |
| Stratified dummy | 0.0465 |

Two things worth reading off this table. The trained model beats the
zero-parameter ICAR template match by **8×**, so it earns its complexity. But it
beats plain logistic regression by only **0.004** — the gradient booster is not
where the value is; the *features* are.

Estimator choice was made by measurement on the same blocked folds, not asserted.

---

## Tier 0 vs Tier 1 — why duration mattered

| | Tier 0 (45 feat) | Tier 1 (147 feat) |
|---|---|---|
| Blocked balanced accuracy | 0.6420 | **0.7492** |
| Precision @ 0.25 | 0.666 (FAIL) | **0.765 (PASS)** |
| ECE | 0.0218 | 0.0109 |

Tier 0 normalises time to [0,1], which discards cycle duration entirely — an
85-day Bajra cycle and a 330-day Sugarcane cycle produce identically shaped
vectors. Adding duration, the observation-quality scalars, six index grids and
the phenology summaries is worth **+10.7 points** and converts the failing
precision gate into a passing one.

Most important features (gain), confirming the tier-1 rationale:

| Feature | Gain | |
|---|---|---|
| `LSWI_t14` | 0.0265 | tier-1 addition — surface water |
| `PSRI_t03` | 0.0177 | tier-1 addition — senescence |
| `LSWI_t11` | 0.0177 | tier-1 addition |
| `integral_ndvi_days` | 0.0170 | tier-1 scalar |
| `observed_fraction` | 0.0168 | tier-1 scalar |
| `NDMI_t15` | 0.0157 | tier-0 |
| `cycle_confidence` | 0.0156 | tier-1 scalar |
| `log_duration` | 0.0155 | **tier-1 — the discarded feature** |
| `duration_days` | 0.0153 | **tier-1** |

No single feature exceeds 2.7% of total gain, so the model is not leaning on one
leaky endpoint.

---

## Abstain rule (shipped in the bundle)

```
p_min = 0.25    gap_min = 0.10    n_scenes_min = 5
```

Chosen by **measurement**, not judgement: the loosest setting whose precision
still clears 0.70. It yields **precision 0.789 at 91.1% coverage**. The design
doc's guessed `(p_min 0.35, n_scenes_min 8)` would have discarded 43.7% of cycles
for less precision.

On abstention the classifier returns `predicted_crop = None` plus the full
probability vector and `classification_note = "abstained: <reason>"`. The cycle
survives, `cultivation_signal` carries scoring, and `performance_analyzer` takes
its crop-agnostic path. **No pipeline change is required to abstain safely.**

A cycle-dependent bundle also abstains with `cycle_metadata_unavailable` if
called without a `CropCycle`, rather than zero-filling duration and returning a
confident answer.

---

## Scope and provenance

**Classes (18):** Bajra, Banana, Chilli, Cotton, Gram, Grapes, Groundnut, Jowar,
Maize, Mustard, Onion, Potato, Rice, Soyabean, Sugarcane, Tobacco, Tur, Wheat.

**Cannot predict (no training data):** Cabbage, Papaya, Pomegranate, Sunflower,
Others. Fields of these crops will produce a low-confidence abstention, which is
the intended behaviour — there is deliberately no learned catch-all class.

| | |
|---|---|
| Training data | `crop_classification_train_500.gpkg`, 9,000 parcels → 8,891 after de-duplication |
| Attributed cycles | **7,907 (88.9%)**; class imbalance ratio 1.98 |
| Geographic footprint | 69–88 °E, 12–31 °N; ecoregions GANGETIC_AND_EASTERN_PLAINS, DECCAN_PLATEAU, SOUTHERN_PENINSULA, NORTH_WEST_SEMI_ARID, CENTRAL_HIGHLAND_MIXED, INDIA_UNSPECIFIED_PLAINS |
| Temporal footprint | survey dates 2022–2024; **no year or month is a feature** |
| Imagery | Sentinel-2 SR Harmonized, 10-day bins, Cloud Score+ ≥ 0.60, 10 m |
| Runtime pins | numpy 1.26.4 · scipy 1.11.4 · scikit-learn 1.6.1 · xgboost 2.0.3 · joblib 1.4.2 |

### Accuracy ceiling: the labels are unverified

Training labels are **self-reported registry crop names**, never ground-truthed.
The model can be no better than that label noise, and some of the residual error
is very likely mislabelled training data rather than model weakness. The
`INCONSISTENT` verdicts from `crop_verification.py` are the first real-world
label signal this system collects and are the intended retraining corpus.

### Features deliberately excluded

`Date`, `year`, `month`, `Area`/`area_ha`, `lat`, `lon`, `source_file`,
`sample_id`. Each is a leakage channel documented in §G.1 — `Date` is a survey
snapshot near-constant within a class (Cotton has 1 unique date across 500
parcels), and `Area` is an artifact of the top-500-by-area sampling rule.

### Trained on bounding boxes, matching production

Production's GEE path reduces over the polygon's **envelope**, which is a median
1.49× the parcel area — so only ~67% of averaged pixels are inside the field. The
model is trained the same way for parity. Fixing this is a Stage-2 change with a
much wider blast radius; see §K.3.2.

---

## There is a pre-existing model, and it is not what it appears

`backend/Credit_assessment/models/crop_classifier_model.joblib` already held a
**23-class RandomForest on 24 features** (8 timesteps x 3 indices), trained
2026-02-16. It is **left in place, untouched** — this model ships alongside it as
`crop_classifier_tier1_v1.joblib`.

Its recorded metrics claim **95.8% test accuracy, 95.5% CV accuracy.** Those
numbers should not be believed, for two reasons visible in its own
`training_config`:

| Field | Value |
|---|---|
| `total_samples` | 25,981 |
| `total_farms` | **1,129** |
| `n_samples_per_farm` | **25** |

It generated 25 samples per farm and then split randomly, so **the same farm
appears on both sides of the split** — the canonical form of the leakage this
project's blocked protocol exists to prevent. There is no spatial blocking of any
kind.

Separately, its 24 features are `t01..t08` while `ML_FEATURE_SCENES = 15`, so on
the live extractor it reads only the first 8 of 15 grid points — roughly the
first half of every cycle. It was trained against a different feature grid than
production now serves.

It still loads under the new fail-fast guard (its feature names are a subset of
what the extractor produces), so nothing is broken by leaving it as the default.

**Comparing honestly:** 0.7492 blocked is not worse than 0.958 random-with-
duplication; they are not the same measurement. The closest like-for-like figure
this model has is its own random-split diagnostic, 0.8825 — and even that does
not duplicate farms.

## Deploying this model

It is **not** the `CROP_MODEL_PATH` default. To use it:

```
CROP_MODEL_PATH=models/crop_classifier_tier1_v1.joblib
ENABLE_CROP_CLASSIFICATION=true
```

Do the second line only after the rollout steps below.

## Suggested rollout

1. **Shadow mode.** `ENABLE_CROP_CLASSIFICATION=true` on a staging cohort; log
   predictions, do not let them reach scores. Compare against
   `crop_verification.py` verdicts and registry hints.
2. **Allow-list by ecoregion.** Enable only where training coverage exists. LOEO
   0.19 means an unseen district is out of scope.
3. **Consider suppressing Gram and Chilli** from crop-specific scoring until the
   41% confusion is resolved — or accept it explicitly in writing.
4. **Re-sample the source data.** The single highest-leverage improvement is not
   modelling: re-drawing the training set *randomly and spatially stratified*
   from the full ~106,000 source rows would remove the `Area` artifact and widen
   geographic coverage, directly attacking the leakage gap and the LOEO failure.
