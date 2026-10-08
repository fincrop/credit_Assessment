# Fused kharif classifier (`crop_classifier_fused_v4`) - evaluation

SAR->NDVI imputer on frozen test parcels: {'train_pairs': 97914, 'test_pairs': 7421, 'test_r2': 0.8864, 'test_rmse': 0.0951, 'test_mae': 0.07}

Blocked CV (training rows): {'temperature': 1.3035, 'class_bias': {'Bajra': -0.519, 'Banana': 0.104, 'Chilli': -0.611, 'Cotton': 0.377, 'Grapes': 0.326, 'Groundnut': -0.791, 'Jowar': 0.063, 'Onion': -0.026, 'Rice': 0.339, 'Soyabean': 0.555, 'Sugarcane': 0.143, 'Tobacco': -0.325, 'Tur': 0.364}, 'ece_full_mh': 0.0207, 'balanced_accuracy_full': 0.7706, 'ece_full': 0.0177, 'by_cutoff': {'07-01': 0.4654, '08-01': 0.501, '09-01': 0.6021, '10-01': 0.641, '11-01': 0.688, 'full': 0.7706}, 'shifted_rows_balanced_accuracy': 0.6275}

## Frozen Marathwada test blocks (95% Wilson intervals)

| As of | n | coverage @rule | Cotton R | Cotton P | Soyabean R | Soyabean P | Tur R | ECE |
|---|---|---|---|---|---|---|---|---|
| 07-01 | 814 | 0.6867 | 0.6573 [0.6146, 0.6976] | 0.6694 [0.6266, 0.7096] | 0.0372 [0.019, 0.0717] | 0.1778 [0.0929, 0.3133] | 0.57 | 0.0516 |
| 08-01 | 814 | 0.6572 | 0.7054 [0.664, 0.7437] | 0.6769 [0.6356, 0.7157] | 0.093 [0.061, 0.1393] | 0.198 [0.132, 0.2862] | 0.46 | 0.0963 |
| 09-01 | 814 | 0.8096 | 0.8617 [0.8287, 0.8893] | 0.789 [0.7528, 0.8212] | 0.3023 [0.2448, 0.3667] | 0.5159 [0.4294, 0.6014] | 0.55 | 0.1271 |
| 10-01 | 814 | 0.8194 | 0.8657 [0.833, 0.8929] | 0.7941 [0.7581, 0.826] | 0.2977 [0.2405, 0.3619] | 0.4776 [0.3948, 0.5616] | 0.46 | 0.0777 |
| 11-01 | 814 | 0.9361 | 0.9679 [0.9486, 0.9802] | 0.9718 [0.9533, 0.9831] | 0.8605 [0.8078, 0.9005] | 0.9204 [0.8746, 0.9504] | 0.79 | 0.1808 |
| full | 814 | 0.9631 | 0.9579 [0.9365, 0.9723] | 0.9896 [0.976, 0.9956] | 0.9163 [0.8716, 0.9464] | 0.9563 [0.9191, 0.9768] | 0.89 | 0.0898 |

## Where the three crops were sent

### 07-01
- Tur: {'Tur': 57, 'Soyabean': 25, 'Cotton': 12, 'Jowar': 3, 'Sugarcane': 2, 'Grapes': 1}
- Cotton: {'Cotton': 328, 'Tur': 89, 'Jowar': 46, 'Onion': 22, 'Soyabean': 12, 'Sugarcane': 2}
- Soyabean: {'Cotton': 150, 'Tur': 42, 'Onion': 8, 'Soyabean': 8, 'Jowar': 6, 'Sugarcane': 1}
### 08-01
- Tur: {'Tur': 46, 'Soyabean': 34, 'Cotton': 18, 'Banana': 1, 'Onion': 1}
- Cotton: {'Cotton': 352, 'Soyabean': 47, 'Tur': 47, 'Jowar': 40, 'Onion': 3, 'Tobacco': 2, 'Banana': 2, 'Chilli': 2, 'Sugarcane': 1, 'Rice': 1, 'Groundnut': 1, 'Grapes': 1}
- Soyabean: {'Cotton': 150, 'Tur': 25, 'Soyabean': 20, 'Jowar': 8, 'Banana': 5, 'Groundnut': 4, 'Onion': 2, 'Sugarcane': 1}
### 09-01
- Tur: {'Tur': 55, 'Soyabean': 35, 'Cotton': 10}
- Cotton: {'Cotton': 430, 'Tur': 32, 'Soyabean': 26, 'Onion': 4, 'Jowar': 3, 'Banana': 2, 'Grapes': 1, 'Sugarcane': 1}
- Soyabean: {'Cotton': 105, 'Soyabean': 65, 'Tur': 32, 'Onion': 11, 'Groundnut': 2}
### 10-01
- Tur: {'Tur': 46, 'Soyabean': 43, 'Cotton': 10, 'Groundnut': 1}
- Cotton: {'Cotton': 432, 'Tur': 33, 'Soyabean': 27, 'Banana': 3, 'Onion': 2, 'Groundnut': 1, 'Sugarcane': 1}
- Soyabean: {'Cotton': 102, 'Soyabean': 64, 'Tur': 38, 'Groundnut': 6, 'Onion': 5}
### 11-01
- Tur: {'Tur': 79, 'Soyabean': 13, 'Cotton': 8}
- Cotton: {'Cotton': 483, 'Tur': 9, 'Banana': 3, 'Soyabean': 3, 'Onion': 1}
- Soyabean: {'Soyabean': 185, 'Tur': 23, 'Cotton': 6, 'Onion': 1}
### full
- Tur: {'Tur': 89, 'Soyabean': 6, 'Cotton': 5}
- Cotton: {'Cotton': 478, 'Tur': 16, 'Soyabean': 3, 'Grapes': 1, 'Onion': 1}
- Soyabean: {'Soyabean': 197, 'Tur': 18}
