# Crop Classification Model

Trains the ML crop classifier that fills the `enable_crop_classification=False`
hole in the pipeline's Stage 4/5.

**Design & data audit:** [`classification_model.md`](classification_model.md) —
read this first. It documents the four defects in the training data, the exact
contract the model must satisfy, and why the only reportable accuracy number is
the spatially-blocked one.

---

## Quick start

Use the repo's `.conda` interpreter: it matches `backend/Credit_assessment/requirements.txt`
exactly (`numpy 1.26.4`, `scipy 1.11.4`, `scikit-learn 1.6.1`, `xgboost 2.0.3`,
`joblib 1.4.2`), so a bundle trained here unpickles cleanly in production.

```bash
cd Crop_classification_model
../.conda/python.exe -m src.ingest
```

Then, in order:

| # | Command | Time | Produces |
|---|---|---|---|
| 1 | `-m src.ingest` | ~20 s | `data/00_parcels_clean.parquet` |
| 2 | `-m src.extract --validate 12` | ~10 min | parity gate vs the production collector |
| 3 | `-m src.extract --workers 4 --worker N` | ~2 h | `data/01_scenes_raw.parquet` |
| 4 | `-m src.cycles` | ~15 min | `data/02_cycles.parquet`, `data/rejections.csv` |
| 5 | `-m src.features` | ~2 min | `data/03_features_tier{0,1}.parquet` |
| 6 | `-m src.train --tier 0` | ~5 min | `reports/cv_report_tier0.json` |
| 7 | `-m src.evaluate --tier 0` | ~2 min | `reports/evaluation_tier0.md` + PNGs |
| 8 | `-m src.train --tier 0 --export` | ~2 min | `models/crop_classifier_model.joblib` |

Step 3 runs in parallel — launch one process per worker index:

```bash
for i in 0 1 2 3; do ../.conda/python.exe -u -m src.extract --workers 4 --worker $i & done
```

It shards to `data/shards/` and skips completed units, so it is safe to
interrupt and restart. To rebuild the consolidated parquet without re-fetching:

```bash
../.conda/python.exe -m src.extract --consolidate-only
```

Verify the whole downstream chain without touching Earth Engine:

```bash
../.conda/python.exe -m src.smoke_test        # 17 checks
```

---

## Requirements

- `backend/Credit_assessment/.env` with `GEE_PROJECT` and `GEE_SA_KEY_PATH`
  (the bootstrap resolves the key path to an absolute one, because the
  production collector opens it relative to the working directory).
- Earth Engine access to `COPERNICUS/S2_SR_HARMONIZED` and
  `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED`.

---

## The three things most likely to bite you

**1. Train/serve parity is structural, not a convention.**
The 45 tier-0 features are built by importing `build_feature_dict` from
`crop_analysis.crop_detector` — the same function the live pipeline calls. Do
not add a second implementation. If you change feature semantics, bump
`EXTRACTOR_VERSION`; `CropDetector.__init__` refuses to load a bundle whose
version or feature set does not match what the live extractor can produce.

**2. `Area` in the source GeoPackage is ACRES, not hectares.**
Passing it unconverted as `field_area_ha` makes `validate_farm_geometry` reject
100% of polygons (ratio 0.405 < the 0.5 floor) and silently fall back to
point+buffer, so training features come from circles while production features
come from parcels — with no error anywhere. `src/ingest.py` converts once and
asserts the result.

**3. Never quote the random-split accuracy.**
233 of 280 spatial blocks contain a single crop, so a random split scores the
model on memorised neighbourhoods. `src/train.py` reports both and prints the
gap; only the `GroupKFold`-on-spatial-blocks number is real.

---

## Layout

```
Crop_classification_model/
├── classification_model.md          design, data audit, contract, gates
├── crop_classification_train_500.gpkg
├── prepare_training_gpkg.py         (pre-existing) builds the gpkg
├── src/
│   ├── _bootstrap.py                paths, .env, GEE init, native DLL fix
│   ├── ingest.py                    stage 1
│   ├── extract.py                   stage 2  (batched GEE)
│   ├── validate_parity.py           stage 2 gate vs SatelliteDataCollector
│   ├── cycles.py                    stage 3  (detection + label attribution)
│   ├── features.py                  stage 4a (shared extractor)
│   ├── train.py                     stage 4b (blocked CV, calibration, export)
│   ├── evaluate.py                  stage 4c (reports and figures)
│   └── smoke_test.py                synthetic end-to-end test
├── data/                            intermediate parquet + shards (gitignored)
├── reports/                         CV reports, figures, parity CSV
└── models/                          crop_classifier_model.joblib
```

## Deploying

Copy the bundle next to the pipeline and enable the flag:

```bash
cp models/crop_classifier_model.joblib ../backend/Credit_assessment/models/
```

```
ENABLE_CROP_CLASSIFICATION=true
CROP_MODEL_PATH=models/crop_classifier_model.joblib
```

Do **not** enable it until `reports/evaluation_tier0.md` shows all ship gates
passing. Classification is enrichment-only: with the flag off, the pipeline
scores crop-agnostically and nothing downstream breaks. A wrong crop name is
worse than no crop name, because `crop_confidence >= 0.25` unlocks ICAR-curve
scoring where a bad label swings up to 45% of the risk index.
