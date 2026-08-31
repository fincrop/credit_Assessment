# Crop Classifier — Tier 1 Evaluation

Generated 2026-08-27T18:07:16.047207+00:00  ·  7907 cycles  ·  147 features  ·  18 classes

## Headline

| Protocol | Balanced acc. | Macro F1 | ECE | Min class recall | Coverage@0.25 | Precision@0.25 |
|---|---|---|---|---|---|---|
| **Blocked (the real number)** | **0.7492** | 0.7348 | 0.0109 | 0.2137 (Gram) | 0.959 | 0.765 |
| Random *(diagnostic only)* | 0.8825 | 0.8823 | 0.0048 | 0.7987 | 0.989 | 0.887 |
| Leave-one-ecoregion-out | 0.1898 | 0.1587 | 0.3905 | 0.0000 | 0.901 | 0.195 |

**Leakage gap (random − blocked): +0.1333** balanced accuracy. This is how much of a random-split score would have been geography rather than crop.
Chance is 0.0556.

## Ship gates

| Gate | Result |
|---|---|
| `balanced_accuracy>=0.55` | PASS |
| `macro_f1>=0.50` | PASS |
| `min_class_recall>=0.30` | FAIL |
| `ece<=0.05` | PASS |
| `precision_at_gate>=0.70` | PASS |
| `coverage_at_gate>=0.40` | PASS |

**NOT READY TO SHIP**

## Competitors

| Model | Blocked balanced acc. |
|---|---|
| **XGBoost (this model)** | **0.7492** |
| dummy | 0.0481 |
| logistic | 0.7448 |
| random_forest | 0.7311 |
| CropGrowthCurves template match (zero parameters) | 0.0935 |

## Abstain-rule sweep

`n_scenes_min` and `p_min` were set by judgement in the design doc. This is the measured version: **take the loosest setting that still clears 0.70 precision.** A stricter one only discards cycles the model was getting right; a looser one lets wrong crop names reach ICAR-curve scoring, where they swing up to 45% of the risk index.

| n_scenes_min | p_min | Coverage | Precision | N kept |
|---|---|---|---|---|
| 5 | 0.25 | 0.911 | 0.789 | 7202 **<-** |
| 5 | 0.30 | 0.904 | 0.792 | 7147 **<-** |
| 5 | 0.35 | 0.890 | 0.799 | 7037 **<-** |
| 5 | 0.40 | 0.865 | 0.811 | 6839 **<-** |
| 5 | 0.50 | 0.794 | 0.845 | 6282 **<-** |
| 6 | 0.25 | 0.875 | 0.787 | 6919 **<-** |
| 6 | 0.30 | 0.868 | 0.790 | 6866 **<-** |
| 6 | 0.35 | 0.855 | 0.797 | 6760 **<-** |
| 6 | 0.40 | 0.831 | 0.809 | 6570 **<-** |
| 6 | 0.50 | 0.764 | 0.843 | 6038 **<-** |
| 7 | 0.25 | 0.827 | 0.786 | 6535 **<-** |
| 7 | 0.30 | 0.820 | 0.789 | 6486 **<-** |
| 7 | 0.35 | 0.808 | 0.796 | 6387 **<-** |
| 7 | 0.40 | 0.785 | 0.808 | 6205 **<-** |
| 7 | 0.50 | 0.722 | 0.842 | 5706 **<-** |
| 8 | 0.25 | 0.761 | 0.787 | 6014 **<-** |
| 8 | 0.30 | 0.755 | 0.790 | 5973 **<-** |
| 8 | 0.35 | 0.744 | 0.797 | 5881 **<-** |
| 8 | 0.40 | 0.724 | 0.808 | 5724 **<-** |
| 8 | 0.50 | 0.665 | 0.842 | 5261 **<-** |
| 10 | 0.25 | 0.589 | 0.783 | 4656 **<-** |
| 10 | 0.30 | 0.585 | 0.786 | 4626 **<-** |
| 10 | 0.35 | 0.577 | 0.792 | 4559 **<-** |
| 10 | 0.40 | 0.561 | 0.803 | 4438 **<-** |
| 10 | 0.50 | 0.516 | 0.837 | 4084 **<-** |

Loosest setting clearing 0.70 precision: **n_scenes_min=5, p_min=0.25** (coverage 0.911, precision 0.789).

## Geography check (adversarial validation)

Predicting **ecoregion** from the same features reaches 0.5323 balanced accuracy across 6 regions (chance 0.1667). The higher this is, the more location signal the features carry — and the more a high random-split score should be distrusted.

## Per-class performance (blocked)

| Crop | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Bajra | 0.529 | 0.526 | 0.528 | 477 |
| Banana | 0.847 | 0.792 | 0.818 | 404 |
| Chilli | 0.509 | 0.825 | 0.630 | 309 |
| Cotton | 0.808 | 0.912 | 0.857 | 479 |
| Gram | 0.606 | 0.214 | 0.316 | 496 |
| Grapes | 0.894 | 0.882 | 0.888 | 468 |
| Groundnut | 0.901 | 0.933 | 0.917 | 343 |
| Jowar | 0.806 | 0.678 | 0.736 | 490 |
| Maize | 0.683 | 0.824 | 0.747 | 493 |
| Mustard | 0.800 | 0.922 | 0.856 | 498 |
| Onion | 0.754 | 0.791 | 0.772 | 488 |
| Potato | 0.737 | 0.773 | 0.755 | 499 |
| Rice | 0.840 | 0.833 | 0.837 | 480 |
| Soyabean | 0.705 | 0.738 | 0.721 | 252 |
| Sugarcane | 0.764 | 0.848 | 0.804 | 421 |
| Tobacco | 0.613 | 0.649 | 0.630 | 390 |
| Tur | 0.837 | 0.865 | 0.851 | 422 |
| Wheat | 0.685 | 0.480 | 0.564 | 498 |

## Confusions above 8% of a class

| True | Predicted as | Share |
|---|---|---|
| Gram | Chilli | 40.9% |
| Jowar | Bajra | 15.7% |
| Wheat | Mustard | 14.1% |
| Soyabean | Tur | 13.5% |
| Wheat | Maize | 13.5% |
| Gram | Tobacco | 9.5% |
| Banana | Sugarcane | 9.2% |
| Bajra | Maize | 8.8% |

## Top features

| Feature | Gain |
|---|---|
| `LSWI_t14` | 0.0265 |
| `PSRI_t03` | 0.0177 |
| `LSWI_t11` | 0.0177 |
| `integral_ndvi_days` | 0.0170 |
| `observed_fraction` | 0.0168 |
| `NDMI_t15` | 0.0157 |
| `cycle_confidence` | 0.0156 |
| `log_duration` | 0.0155 |
| `NDMI_t11` | 0.0153 |
| `duration_days` | 0.0153 |
| `baseline_ndvi` | 0.0150 |
| `PSRI_t15` | 0.0147 |

## Rejection ledger

Parcels dropped before training, by reason. A class dominated by `no_cycle_detected` is a finding about Stage 3, not a data problem.

| Reason | N |
|---|---|
| cycle_too_few_scenes | 436 |
| no_cycle_near_survey_date | 422 |
| too_few_core_pixels | 124 |
| no_cycle_detected | 2 |

## Figures

- `confusion_tier1.png`
- `reliability_tier1.png`
- `precision_coverage_tier1.png`
- `importance_tier1.png`
