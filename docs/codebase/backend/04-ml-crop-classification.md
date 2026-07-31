# Backend Stage 04 — ML Crop Classification

## Purpose

Fork between **Path A** (named crop + crop-specific curves/multipliers) and **Path B** (unclassified / crop-agnostic). ML remains **off by default**.

## Present condition (after 2026-07 enhancement)

1. Enable via `ENABLE_CROP_CLASSIFICATION` or job `require_classification`.
2. When ML **off**: `_build_unclassified_analysis` builds cycle rows.
   - If registry/farm `crop` hint exists → `predicted_crop` set with `crop_label_source=registry_self_report` (unlocks crop-specific Stage 06/07 paths without trusting the classifier).
   - Else → Unclassified / `predicted_crop=None`.
3. Schema exposes `cycles_per_year` (and legacy `cropping_intensity`).
4. When ML **on**: cycle-window features → RF/XGBoost; **no** Bayesian registry prior yet.

## Done

| Item | Notes |
|------|--------|
| Registry crop as Path B label | High-leverage; zero ML risk |
| Cycles/year naming | Alongside intensity alias |

## Missing / next

| Priority | Item |
|----------|------|
| Medium | Dashboard / per-region `require_classification` toggle |
| Medium | sklearn/xgboost pin + model card beside joblib |
| Major | Soft Bayesian registry prior when ML is on |
| Major | Retrain on production feature extractor (train/serve parity) |

## Interfaces

Upstream: Stage 03 cycles + Stage 02 scenes · Downstream: 05–07 via `season_results`

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
