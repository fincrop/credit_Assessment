# Crop Classifier — Tier 2 Evaluation

Generated 2026-09-28T08:55:13.798090+00:00  ·  8996 cycles  ·  271 features  ·  18 classes

## Headline

| Protocol | Balanced acc. | Macro F1 | ECE | Min class recall | Coverage@0.25 | Precision@0.25 |
|---|---|---|---|---|---|---|
| **Blocked (the real number)** | **0.8315** | 0.8312 | 0.0336 | 0.6803 (Jowar) | 0.961 | 0.853 |
| Random *(diagnostic only)* | 0.9357 | 0.9366 | 0.0054 | 0.8569 | 0.996 | 0.937 |
| Leave-one-ecoregion-out | 0.2664 | 0.2313 | 0.2998 | 0.0000 | 0.917 | 0.315 |

**Leakage gap (random − blocked): +0.1042** balanced accuracy. This is how much of a random-split score would have been geography rather than crop.
Chance is 0.0556.

## Ship gates

| Gate | Result |
|---|---|
| `balanced_accuracy>=0.55` | PASS |
| `macro_f1>=0.50` | PASS |
| `min_class_recall>=0.30` | PASS |
| `ece<=0.05` | PASS |
| `precision_at_gate>=0.70` | PASS |
| `coverage_at_gate>=0.40` | PASS |

**ALL GATES PASSED**

## Competitors

| Model | Blocked balanced acc. |
|---|---|
| **XGBoost (this model)** | **0.8315** |
| dummy | 0.0492 |
| logistic | 0.8087 |
| random_forest | 0.8313 |
| CropGrowthCurves template match (zero parameters) | 0.0955 |

## Abstain-rule sweep

`n_scenes_min` and `p_min` were set by judgement in the design doc. This is the measured version: **take the loosest setting that still clears 0.70 precision.** A stricter one only discards cycles the model was getting right; a looser one lets wrong crop names reach ICAR-curve scoring, where they swing up to 45% of the risk index.

| n_scenes_min | p_min | Coverage | Precision | N kept |
|---|---|---|---|---|
| 5 | 0.25 | 0.928 | 0.868 | 8350 **<-** |
| 5 | 0.30 | 0.920 | 0.872 | 8280 **<-** |
| 5 | 0.35 | 0.907 | 0.879 | 8162 **<-** |
| 5 | 0.40 | 0.890 | 0.885 | 8009 **<-** |
| 5 | 0.50 | 0.849 | 0.902 | 7638 **<-** |
| 6 | 0.25 | 0.887 | 0.869 | 7977 **<-** |
| 6 | 0.30 | 0.879 | 0.873 | 7909 **<-** |
| 6 | 0.35 | 0.867 | 0.879 | 7800 **<-** |
| 6 | 0.40 | 0.851 | 0.885 | 7658 **<-** |
| 6 | 0.50 | 0.813 | 0.902 | 7310 **<-** |
| 7 | 0.25 | 0.835 | 0.870 | 7508 **<-** |
| 7 | 0.30 | 0.828 | 0.874 | 7447 **<-** |
| 7 | 0.35 | 0.817 | 0.880 | 7347 **<-** |
| 7 | 0.40 | 0.803 | 0.886 | 7221 **<-** |
| 7 | 0.50 | 0.767 | 0.903 | 6897 **<-** |
| 8 | 0.25 | 0.770 | 0.873 | 6927 **<-** |
| 8 | 0.30 | 0.764 | 0.877 | 6873 **<-** |
| 8 | 0.35 | 0.754 | 0.883 | 6784 **<-** |
| 8 | 0.40 | 0.742 | 0.887 | 6672 **<-** |
| 8 | 0.50 | 0.709 | 0.904 | 6376 **<-** |
| 10 | 0.25 | 0.605 | 0.886 | 5445 **<-** |
| 10 | 0.30 | 0.601 | 0.888 | 5408 **<-** |
| 10 | 0.35 | 0.594 | 0.893 | 5340 **<-** |
| 10 | 0.40 | 0.584 | 0.897 | 5255 **<-** |
| 10 | 0.50 | 0.558 | 0.914 | 5021 **<-** |

Loosest setting clearing 0.70 precision: **n_scenes_min=5, p_min=0.25** (coverage 0.928, precision 0.868).

## Geography check (adversarial validation)

Predicting **ecoregion** from the same features reaches 0.6097 balanced accuracy across 6 regions (chance 0.1667). The higher this is, the more location signal the features carry — and the more a high random-split score should be distrusted.

## Per-class performance (blocked)

| Crop | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Bajra | 0.665 | 0.765 | 0.712 | 473 |
| Banana | 0.939 | 0.920 | 0.929 | 451 |
| Chilli | 0.814 | 0.863 | 0.838 | 430 |
| Cotton | 0.803 | 0.894 | 0.846 | 899 |
| Gram | 0.850 | 0.706 | 0.771 | 496 |
| Grapes | 0.901 | 0.917 | 0.909 | 468 |
| Groundnut | 0.959 | 0.953 | 0.956 | 343 |
| Jowar | 0.711 | 0.680 | 0.695 | 466 |
| Maize | 0.792 | 0.725 | 0.757 | 611 |
| Mustard | 0.939 | 0.958 | 0.949 | 500 |
| Onion | 0.771 | 0.755 | 0.763 | 559 |
| Potato | 0.953 | 0.911 | 0.931 | 530 |
| Rice | 0.957 | 0.845 | 0.898 | 530 |
| Soyabean | 0.690 | 0.815 | 0.747 | 346 |
| Sugarcane | 0.867 | 0.914 | 0.890 | 421 |
| Tobacco | 0.755 | 0.722 | 0.738 | 406 |
| Tur | 0.831 | 0.853 | 0.842 | 428 |
| Wheat | 0.810 | 0.770 | 0.790 | 639 |

## Confusions above 8% of a class

| True | Predicted as | Share |
|---|---|---|
| Jowar | Bajra | 11.8% |
| Wheat | Maize | 11.6% |
| Gram | Jowar | 11.3% |
| Soyabean | Tur | 11.0% |
| Maize | Wheat | 9.8% |
| Gram | Chilli | 9.7% |
| Tur | Soyabean | 8.4% |
| Jowar | Gram | 8.2% |

## Top features

| Feature | Gain |
|---|---|
| `S1VV_t08` | 0.0266 |
| `RB8A_t04` | 0.0264 |
| `log_gdd_total` | 0.0228 |
| `RB12_t02` | 0.0220 |
| `S1RATIO_t04` | 0.0210 |
| `S1VV_t09` | 0.0209 |
| `RB8A_peak` | 0.0201 |
| `gdd_total` | 0.0145 |
| `RB6_t03` | 0.0132 |
| `S1RATIO_mean` | 0.0120 |
| `S1RATIO_t03` | 0.0117 |
| `RB8A_t03` | 0.0113 |

> Endpoint features dominate. On a normalised time axis the endpoints encode where the cycle starts and ends, i.e. season — check the leakage gap and the LOEO row before trusting this.

## Rejection ledger

Parcels dropped before training, by reason. A class dominated by `no_cycle_detected` is a finding about Stage 3, not a data problem.

| Reason | N |
|---|---|
| cycle_too_few_scenes | 643 |
| no_cycle_near_survey_date | 289 |
| too_few_core_pixels | 124 |
| no_cycle_detected | 2 |

## Figures

- `confusion_tier2.png`
- `reliability_tier2.png`
- `precision_coverage_tier2.png`
- `importance_tier2.png`
