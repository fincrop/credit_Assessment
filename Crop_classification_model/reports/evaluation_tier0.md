# Crop Classifier — Tier 0 Evaluation

Generated 2026-08-27T17:30:51.579121+00:00  ·  7907 cycles  ·  45 features  ·  18 classes

## Headline

| Protocol | Balanced acc. | Macro F1 | ECE | Min class recall | Coverage@0.25 | Precision@0.25 |
|---|---|---|---|---|---|---|
| **Blocked (the real number)** | **0.6420** | 0.6323 | 0.0218 | 0.2298 (Gram) | 0.928 | 0.666 |
| Random *(diagnostic only)* | 0.7798 | 0.7805 | 0.0107 | 0.6065 | 0.970 | 0.794 |
| Leave-one-ecoregion-out | 0.1618 | 0.1372 | 0.4037 | 0.0000 | 0.900 | 0.165 |

**Leakage gap (random − blocked): +0.1378** balanced accuracy. This is how much of a random-split score would have been geography rather than crop.
Chance is 0.0556.

## Ship gates

| Gate | Result |
|---|---|
| `balanced_accuracy>=0.55` | PASS |
| `macro_f1>=0.50` | PASS |
| `min_class_recall>=0.30` | FAIL |
| `ece<=0.05` | PASS |
| `precision_at_gate>=0.70` | FAIL |
| `coverage_at_gate>=0.40` | PASS |

**NOT READY TO SHIP**

## Competitors

| Model | Blocked balanced acc. |
|---|---|
| **XGBoost (this model)** | **0.6420** |
| dummy | 0.0465 |
| logistic | 0.6099 |
| random_forest | 0.6366 |
| CropGrowthCurves template match (zero parameters) | 0.0935 |

## Abstain-rule sweep

`n_scenes_min` and `p_min` were set by judgement in the design doc. This is the measured version: **take the loosest setting that still clears 0.70 precision.** A stricter one only discards cycles the model was getting right; a looser one lets wrong crop names reach ICAR-curve scoring, where they swing up to 45% of the risk index.

| n_scenes_min | p_min | Coverage | Precision | N kept |
|---|---|---|---|---|
| 5 | 0.25 | 0.856 | 0.694 | 6766 |
| 5 | 0.30 | 0.843 | 0.700 | 6666 |
| 5 | 0.35 | 0.810 | 0.714 | 6402 **<-** |
| 5 | 0.40 | 0.761 | 0.735 | 6021 **<-** |
| 5 | 0.50 | 0.659 | 0.776 | 5215 **<-** |
| 6 | 0.25 | 0.824 | 0.696 | 6516 |
| 6 | 0.30 | 0.812 | 0.701 | 6423 **<-** |
| 6 | 0.35 | 0.779 | 0.716 | 6163 **<-** |
| 6 | 0.40 | 0.734 | 0.737 | 5801 **<-** |
| 6 | 0.50 | 0.635 | 0.777 | 5023 **<-** |
| 7 | 0.25 | 0.780 | 0.697 | 6164 |
| 7 | 0.30 | 0.769 | 0.702 | 6080 **<-** |
| 7 | 0.35 | 0.738 | 0.716 | 5832 **<-** |
| 7 | 0.40 | 0.696 | 0.736 | 5506 **<-** |
| 7 | 0.50 | 0.604 | 0.777 | 4774 **<-** |
| 8 | 0.25 | 0.717 | 0.699 | 5669 |
| 8 | 0.30 | 0.708 | 0.704 | 5596 **<-** |
| 8 | 0.35 | 0.679 | 0.718 | 5370 **<-** |
| 8 | 0.40 | 0.641 | 0.737 | 5068 **<-** |
| 8 | 0.50 | 0.555 | 0.776 | 4390 **<-** |
| 10 | 0.25 | 0.555 | 0.697 | 4389 |
| 10 | 0.30 | 0.547 | 0.702 | 4328 **<-** |
| 10 | 0.35 | 0.525 | 0.715 | 4149 **<-** |
| 10 | 0.40 | 0.495 | 0.734 | 3914 **<-** |
| 10 | 0.50 | 0.430 | 0.772 | 3402 **<-** |

Loosest setting clearing 0.70 precision: **n_scenes_min=6, p_min=0.30** (coverage 0.812, precision 0.701).

## Geography check (adversarial validation)

Predicting **ecoregion** from the same features reaches 0.4501 balanced accuracy across 6 regions (chance 0.1667). The higher this is, the more location signal the features carry — and the more a high random-split score should be distrusted.

## Per-class performance (blocked)

| Crop | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Bajra | 0.379 | 0.386 | 0.383 | 477 |
| Banana | 0.688 | 0.705 | 0.697 | 404 |
| Chilli | 0.449 | 0.699 | 0.547 | 309 |
| Cotton | 0.551 | 0.762 | 0.640 | 479 |
| Gram | 0.603 | 0.230 | 0.333 | 496 |
| Grapes | 0.675 | 0.701 | 0.688 | 468 |
| Groundnut | 0.813 | 0.875 | 0.843 | 343 |
| Jowar | 0.761 | 0.610 | 0.677 | 490 |
| Maize | 0.537 | 0.556 | 0.546 | 493 |
| Mustard | 0.754 | 0.875 | 0.810 | 498 |
| Onion | 0.737 | 0.770 | 0.753 | 488 |
| Potato | 0.621 | 0.717 | 0.665 | 499 |
| Rice | 0.759 | 0.690 | 0.723 | 480 |
| Soyabean | 0.672 | 0.651 | 0.661 | 252 |
| Sugarcane | 0.599 | 0.646 | 0.622 | 421 |
| Tobacco | 0.442 | 0.408 | 0.424 | 390 |
| Tur | 0.852 | 0.820 | 0.836 | 422 |
| Wheat | 0.643 | 0.456 | 0.533 | 498 |

## Confusions above 8% of a class

| True | Predicted as | Share |
|---|---|---|
| Gram | Chilli | 37.1% |
| Wheat | Mustard | 19.9% |
| Gram | Tobacco | 14.7% |
| Jowar | Bajra | 13.5% |
| Tobacco | Bajra | 13.1% |
| Wheat | Potato | 12.7% |
| Tobacco | Cotton | 11.3% |
| Bajra | Maize | 10.5% |
| Soyabean | Tur | 10.3% |
| Sugarcane | Cotton | 10.2% |
| Potato | Wheat | 9.6% |
| Gram | Maize | 9.5% |
| Banana | Sugarcane | 8.9% |
| Sugarcane | Banana | 8.6% |
| Grapes | Cotton | 8.3% |
| Soyabean | Groundnut | 8.3% |

## Top features

| Feature | Gain |
|---|---|
| `EVI_t01` | 0.0434 |
| `NDMI_t15` | 0.0392 |
| `NDMI_t11` | 0.0347 |
| `NDMI_t01` | 0.0335 |
| `NDMI_t12` | 0.0330 |
| `EVI_t06` | 0.0318 |
| `EVI_t15` | 0.0309 |
| `NDMI_t10` | 0.0306 |
| `NDMI_t09` | 0.0288 |
| `NDMI_t13` | 0.0283 |
| `NDVI_t01` | 0.0279 |
| `NDMI_t08` | 0.0258 |

> Endpoint features dominate. On a normalised time axis the endpoints encode where the cycle starts and ends, i.e. season — check the leakage gap and the LOEO row before trusting this.

## Rejection ledger

Parcels dropped before training, by reason. A class dominated by `no_cycle_detected` is a finding about Stage 3, not a data problem.

| Reason | N |
|---|---|
| cycle_too_few_scenes | 436 |
| no_cycle_near_survey_date | 422 |
| too_few_core_pixels | 124 |
| no_cycle_detected | 2 |

## Figures

- `confusion_tier0.png`
- `reliability_tier0.png`
- `precision_coverage_tier0.png`
- `importance_tier0.png`
