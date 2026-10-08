# Marathwada retrain — evaluation (`crop_classifier_tier1_mh_v1`)

Generated 2026-10-03T05:30:32.867449+00:00 · split `mh2023_v1` (hash `e7fdbe2ea9a87954`) · training data hash `f6ff5a272b307e89`

## Gate check (plan §8: Cotton & Soyabean recall and precision ≥ 0.85, Wilson LB ≥ 0.80, ECE ≤ 0.05)

| Footprint | Model | Scoring | Cotton R / P | Soyabean R / P | ECE | Pass |
|---|---|---|---|---|---|---|
| bbox | new_mh_v1 | argmax | 0.951 (LB 0.9282) / 0.9588 (LB 0.9373) | 0.741 (LB 0.6624) / 0.9035 (LB 0.8354) | 0.0698 | **FAIL** |
| bbox | new_mh_v1 | at abstain rule | 0.9265 (LB 0.9) / 0.9619 (LB 0.9405) | 0.6978 (LB 0.617) / 0.9238 (LB 0.8568) | 0.0698 | **FAIL** |
| bbox | shipped_tier1_v1 | argmax | 0.5857 (LB 0.5416) / 0.9863 (LB 0.9652) | 0.8058 (LB 0.7321) / 0.9492 (LB 0.8935) | 0.0842 | **FAIL** |
| bbox | shipped_tier1_v1 | at abstain rule | 0.5449 (LB 0.5006) / 0.9852 (LB 0.9627) | 0.7914 (LB 0.7164) / 0.9565 (LB 0.9022) | 0.0842 | **FAIL** |
| poly | new_mh_v1 | argmax | 0.9554 (LB 0.9334) / 0.9874 (LB 0.9728) | 0.8083 (LB 0.7288) / 0.9238 (LB 0.8568) | 0.0856 | **FAIL** |
| poly | new_mh_v1 | at abstain rule | 0.9432 (LB 0.9191) / 0.9915 (LB 0.9783) | 0.775 (LB 0.6924) / 0.93 (LB 0.8625) | 0.0856 | **FAIL** |
| poly | shipped_tier1_v1 | argmax | 0.5862 (LB 0.5422) / 0.9863 (LB 0.9654) | 0.85 (LB 0.7753) / 0.9273 (LB 0.863) | 0.1 | **FAIL** |
| poly | shipped_tier1_v1 | at abstain rule | 0.5578 (LB 0.5137) / 0.9857 (LB 0.9637) | 0.8167 (LB 0.738) / 0.9515 (LB 0.8914) | 0.1 | **FAIL** |

## Frozen Marathwada test set — per class (95% Wilson intervals)

### Footprint: bbox

Scored parcels with scenes of this footprint: {'Cotton': 499, 'Soyabean': 215, 'Tur': 100} · with an attributed cycle: {'Cotton': 490, 'Soyabean': 139, 'Tur': 94}

**new_mh_v1** — n=723, ECE=0.0698, argmax accuracy=0.8728, bundle rule {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5}: coverage 0.9433, precision on kept 0.8988; design rule 0.35: coverage 0.6791, precision 0.9145

| Crop | n | Recall (argmax) | Precision (argmax) | F1 | Recall @rule | Precision @rule | End-to-end recall @rule |
|---|---|---|---|---|---|---|---|
| Cotton | 490 | 0.951 [0.928, 0.967] | 0.959 [0.937, 0.973] | 0.9549 | 0.926 [0.900, 0.947] | 0.962 [0.941, 0.976] | 0.910 [0.881, 0.932] |
| Soyabean | 139 | 0.741 [0.662, 0.807] | 0.903 [0.835, 0.945] | 0.8142 | 0.698 [0.617, 0.768] | 0.924 [0.857, 0.961] | 0.451 [0.386, 0.518] |
| Tur | 94 | 0.660 [0.559, 0.747] | 0.827 [0.726, 0.896] | 0.7337 | 0.660 [0.559, 0.747] | 0.838 [0.738, 0.905] | 0.620 [0.522, 0.709] |

Confusion (argmax; rows = truth):

| truth \ pred | Cotton | Soyabean | Tur | Others | abstained |
|---|---|---|---|---|---|
| Cotton | 466 | 3 | 9 | 12 | 0 |
| Soyabean | 3 | 103 | 4 | 29 | 0 |
| Tur | 17 | 8 | 62 | 7 | 0 |

OOD (score > training q99): rate 0.0152 by class {'Cotton': 0.002, 'Soyabean': 0.0432, 'Tur': 0.0426}; accuracy in-dist 0.8736 vs OOD 0.8182

**shipped_tier1_v1** (Tur in-sample for this model) — n=723, ECE=0.0842, argmax accuracy=0.6763, bundle rule {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5}: coverage 0.9129, precision on kept 0.7076; design rule 0.35: coverage 0.6252, precision 0.6836

| Crop | n | Recall (argmax) | Precision (argmax) | F1 | Recall @rule | Precision @rule | End-to-end recall @rule |
|---|---|---|---|---|---|---|---|
| Cotton | 490 | 0.586 [0.542, 0.628] | 0.986 [0.965, 0.995] | 0.735 | 0.545 [0.501, 0.589] | 0.985 [0.963, 0.994] | 0.535 [0.491, 0.578] |
| Soyabean | 139 | 0.806 [0.732, 0.863] | 0.949 [0.893, 0.977] | 0.8716 | 0.791 [0.716, 0.851] | 0.957 [0.902, 0.981] | 0.512 [0.445, 0.578] |
| Tur | 94 | 0.957 [0.896, 0.983] | 0.397 [0.335, 0.461] | 0.5607 | 0.957 [0.896, 0.983] | 0.417 [0.353, 0.483] | 0.900 [0.826, 0.945] |

Confusion (argmax; rows = truth):

| truth \ pred | Cotton | Soyabean | Tur | Others | abstained |
|---|---|---|---|---|---|
| Cotton | 287 | 5 | 130 | 68 | 0 |
| Soyabean | 4 | 112 | 7 | 16 | 0 |
| Tur | 0 | 1 | 90 | 3 | 0 |

### Footprint: poly

Scored parcels with scenes of this footprint: {'Cotton': 499, 'Soyabean': 215, 'Tur': 24} · with an attributed cycle: {'Cotton': 493, 'Soyabean': 120, 'Tur': 23}

**new_mh_v1** — n=636, ECE=0.0856, argmax accuracy=0.9167, bundle rule {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5}: coverage 0.9638, precision on kept 0.9331; design rule 0.35: coverage 0.7154, precision 0.9582

| Crop | n | Recall (argmax) | Precision (argmax) | F1 | Recall @rule | Precision @rule | End-to-end recall @rule |
|---|---|---|---|---|---|---|---|
| Cotton | 493 | 0.955 [0.933, 0.970] | 0.987 [0.973, 0.994] | 0.9711 | 0.943 [0.919, 0.960] | 0.992 [0.978, 0.997] | 0.932 [0.906, 0.951] |
| Soyabean | 120 | 0.808 [0.729, 0.869] | 0.924 [0.857, 0.961] | 0.8622 | 0.775 [0.692, 0.841] | 0.930 [0.863, 0.966] | 0.433 [0.368, 0.499] |
| Tur | 23 | 0.652 [0.449, 0.812] | 0.652 [0.449, 0.812] | 0.6522 | 0.609 [0.408, 0.778] | 0.700 [0.481, 0.855] | 0.583 [0.388, 0.755] |

Confusion (argmax; rows = truth):

| truth \ pred | Cotton | Soyabean | Tur | Others | abstained |
|---|---|---|---|---|---|
| Cotton | 471 | 3 | 8 | 11 | 0 |
| Soyabean | 3 | 97 | 0 | 20 | 0 |
| Tur | 3 | 5 | 15 | 0 | 0 |

OOD (score > training q99): rate 0.0142 by class {'Cotton': 0.0041, 'Soyabean': 0.0333, 'Tur': 0.1304}; accuracy in-dist 0.9171 vs OOD 0.8889

**shipped_tier1_v1** (Tur in-sample for this model) — n=636, ECE=0.1, argmax accuracy=0.6478, bundle rule {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5}: coverage 0.8962, precision on kept 0.6895; design rule 0.35: coverage 0.6384, precision 0.6921

| Crop | n | Recall (argmax) | Precision (argmax) | F1 | Recall @rule | Precision @rule | End-to-end recall @rule |
|---|---|---|---|---|---|---|---|
| Cotton | 493 | 0.586 [0.542, 0.629] | 0.986 [0.965, 0.995] | 0.7354 | 0.558 [0.514, 0.601] | 0.986 [0.964, 0.994] | 0.551 [0.507, 0.594] |
| Soyabean | 120 | 0.850 [0.775, 0.903] | 0.927 [0.863, 0.963] | 0.887 | 0.817 [0.738, 0.876] | 0.952 [0.891, 0.979] | 0.456 [0.391, 0.523] |
| Tur | 23 | 0.913 [0.732, 0.976] | 0.133 [0.089, 0.195] | 0.232 | 0.870 [0.679, 0.955] | 0.150 [0.100, 0.221] | 0.833 [0.641, 0.933] |

Confusion (argmax; rows = truth):

| truth \ pred | Cotton | Soyabean | Tur | Others | abstained |
|---|---|---|---|---|---|
| Cotton | 289 | 7 | 135 | 62 | 0 |
| Soyabean | 3 | 102 | 2 | 13 | 0 |
| Tur | 1 | 1 | 21 | 0 | 0 |

## Blocked GroupKFold(5) on training rows (regression check vs shipped 0.7492)

| Recipe | rows | balanced acc | macro F1 | ECE | min recall (class) |
|---|---|---|---|---|---|
| mh_recipe | 9859 | 0.6561 | 0.6403 | 0.056 | 0.0544 (Gram) |
| mh_recipe_no_aug | 9859 | 0.641 | 0.6246 | 0.0619 | 0.0464 (Gram) |
| shipped_recipe_same_rows | 9859 | 0.6464 | 0.6297 | 0.0579 | 0.0504 (Gram) |
| mh_recipe_existing_rows_only | 8850 | 0.6585 | 0.6432 | 0.0675 | 0.0544 (Gram) |
| shipped_recipe_same_rows_existing_only | 8850 | 0.6501 | 0.6361 | 0.0665 | 0.0504 (Gram) |

Shipped model card: 0.7492 on 7,907 rows of the original set. The rows here differ (augmented set + Marathwada, held-out blocks removed), so `shipped_recipe_same_rows` is the like-for-like comparison.

Per-class (MH recipe, blocked OOF): Cotton R 0.8544 P 0.801, Soyabean R 0.6471 P 0.7229, Tur R 0.5 P 0.8146

## In-season truncation (features from scenes up to D days after detected sowing)

### bbox

| D | model | featurised / cycles | status | argmax acc | Cotton R | Soyabean R | Tur R | Cotton P | Soyabean P |
|---|---|---|---|---|---|---|---|---|---|
| 60 | new_mh_v1 | 6 / 723 | {'no_matching_cycle': 692, 'too_few_scenes': 25, 'ok': 6} | 0.1667 | 1.0 | None | 0.0 | 1.0 | 0.0 |
| 60 | shipped_tier1_v1 | 6 / 723 | {'no_matching_cycle': 692, 'too_few_scenes': 25, 'ok': 6} | 0.0 | 0.0 | None | 0.0 | None | 0.0 |
| 90 | new_mh_v1 | 62 / 723 | {'no_matching_cycle': 536, 'too_few_scenes': 125, 'ok': 62} | 0.5806 | 0.825 | 0.3333 | 0.1053 | 0.8919 | 0.25 |
| 90 | shipped_tier1_v1 | 62 / 723 | {'no_matching_cycle': 536, 'too_few_scenes': 125, 'ok': 62} | 0.1935 | 0.125 | 0.0 | 0.3684 | 1.0 | 0.0 |
| 120 | new_mh_v1 | 348 / 723 | {'ok': 348, 'too_few_scenes': 238, 'no_matching_cycle': 137} | 0.7155 | 0.7657 | 0.6613 | 0.5319 | 0.9531 | 0.7885 |
| 120 | shipped_tier1_v1 | 348 / 723 | {'ok': 348, 'too_few_scenes': 238, 'no_matching_cycle': 137} | 0.3046 | 0.1423 | 0.5161 | 0.8511 | 1.0 | 0.7273 |

### poly

| D | model | featurised / cycles | status | argmax acc | Cotton R | Soyabean R | Tur R | Cotton P | Soyabean P |
|---|---|---|---|---|---|---|---|---|---|
| 60 | new_mh_v1 | 3 / 636 | {'no_matching_cycle': 605, 'too_few_scenes': 28, 'ok': 3} | 0.0 | 0.0 | None | 0.0 | None | None |
| 60 | shipped_tier1_v1 | 3 / 636 | {'no_matching_cycle': 605, 'too_few_scenes': 28, 'ok': 3} | 0.0 | 0.0 | None | 0.0 | None | None |
| 90 | new_mh_v1 | 60 / 636 | {'no_matching_cycle': 453, 'too_few_scenes': 123, 'ok': 60} | 0.7833 | 0.8519 | 0.5 | 0.0 | 0.9787 | 1.0 |
| 90 | shipped_tier1_v1 | 60 / 636 | {'no_matching_cycle': 453, 'too_few_scenes': 123, 'ok': 60} | 0.1 | 0.0926 | 0.0 | 0.25 | 1.0 | 0.0 |
| 120 | new_mh_v1 | 316 / 636 | {'ok': 316, 'too_few_scenes': 195, 'no_matching_cycle': 125} | 0.75 | 0.784 | 0.66 | 0.5 | 0.9849 | 0.825 |
| 120 | shipped_tier1_v1 | 316 / 636 | {'ok': 316, 'too_few_scenes': 195, 'no_matching_cycle': 125} | 0.2278 | 0.14 | 0.48 | 0.8125 | 0.9722 | 0.8 |

## Label-shift prior (Saerens et al. 2002)

not applied (no official shares file)

## Label QA (A4)

Phenology flags (flagged, not dropped): {'Cotton': {'n': 2084, 'flagged': 375, 'by_reason': {'short_cycle': 315, 'peak_outside': 65, 'duration_outside': 0}}, 'Soyabean': {'n': 799, 'flagged': 589, 'by_reason': {'short_cycle': 0, 'peak_outside': 16, 'duration_outside': 584}}, 'new_only': {'Cotton': {'n': 1185, 'flagged': 216}, 'Soyabean': {'n': 453, 'flagged': 349}}}

Cross-validated label-noise review list (OOF p(label) < 0.2): {'threshold': 0.2, 'n': 2685, 'by_class': {'Gram': 461, 'Maize': 334, 'Tobacco': 318, 'Jowar': 248, 'Wheat': 183, 'Soyabean': 181, 'Cotton': 178, 'Tur': 129, 'Bajra': 121, 'Onion': 110, 'Sugarcane': 74, 'Banana': 69, 'Potato': 61, 'Chilli': 54, 'Rice': 53, 'Mustard': 50, 'Grapes': 32, 'Groundnut': 29}, 'new_by_class': {'Soyabean': 111, 'Cotton': 45}, 'new_soy_unchecked': 103, 'new_soy_checked': 8} — see `mh_label_review.csv`.

## Attribution (A3)

- **bbox**: {'geom_kind': 'bbox', 'attributed_total': 10637, 'new_attributed': 1641, 'new_kharif_ok': 1638, 'new_rejected_not_kharif': {'Soyabean': 3}, 'new_survey_in_cycle_rate': 0.8854, 'new_soy_gdate_in_cycle_rate': 0.9934, 'new_label_season_counts': {'kharif': 1641}, 'stage_rejections_new': {'Cotton:cycle_too_few_scenes': 9, 'Cotton:no_cycle_near_survey_date': 13, 'Cotton:too_few_core_pixels': 292, 'Soyabean:cycle_too_few_scenes': 100, 'Soyabean:too_few_core_pixels': 259}, 'stage_rejections_all': {'cycle_too_few_scenes': 752, 'too_few_core_pixels': 675, 'no_cycle_near_survey_date': 302, 'no_cycle_detected': 2}}
- **poly**: {'geom_kind': 'poly', 'attributed_total': 3634, 'new_attributed': 1613, 'new_kharif_ok': 1605, 'new_rejected_not_kharif': {'Cotton': 6, 'Soyabean': 2}, 'new_survey_in_cycle_rate': 0.8698, 'new_soy_gdate_in_cycle_rate': 0.9953, 'new_label_season_counts': {'kharif': 1613}, 'stage_rejections_new': {'Cotton:cycle_too_few_scenes': 1, 'Cotton:no_cycle_near_survey_date': 17, 'Cotton:too_few_core_pixels': 292, 'Soyabean:cycle_too_few_scenes': 132, 'Soyabean:too_few_core_pixels': 259}, 'stage_rejections_all': {'too_few_core_pixels': 582, 'cycle_too_few_scenes': 269, 'no_cycle_near_survey_date': 88, 'land_cover_fail': 1, 'no_cycle_detected': 1}}

## Model and training

- Abstain rule (selected on training OOF, loosest with precision ≥ 0.70): {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5} -> OOF {'p_min': 0.25, 'gap_min': 0.1, 'n_scenes_min': 5, 'coverage': 0.8986, 'precision': 0.7258}
- Augmentation: {'n_aug': 1, 'shift_days': 20, 'stretch': 0.1, 'level': 'cycle-scene window shift + duration stretch (src/mh_features.py)', 'applied_to': 'training rows only'}; builder parity with src.features: max |diff| = 0.0
- OOD: {'method': 'pca_whitened_mahalanobis', 'n_components': 26, 'threshold': 1.9074175199907535, 'threshold_quantile': 0.99}
- Loads through CropDetector: True

## Caveats

- Held-out blocks contain only Cotton, Soyabean and Tur. Test-set precision therefore counts only confusions among these three (and predictions of any other class as misses); it cannot see false positives from maize, jowar, fallow etc. Real-world precision will be lower.
- Tur test rows are existing data that the SHIPPED model trained on: the shipped model's Tur numbers are in-sample. The new model never saw them.
- All Cotton/Soyabean labels are one season (Kharif 2023) and one surveyor programme; spatial blocking guards against neighbourhood memorisation, not against year effects.
- Recall 'argmax' ignores abstention; 'bundle_rule' counts an abstention as a miss; 'end_to_end' also counts parcels with no attributed cycle as misses.
