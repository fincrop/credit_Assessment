# Backend Stage 06 — Yield & Performance Evaluation

## Purpose

Feeds `crop_performance` (25), `yield_potential` key / **yield proxy** (15), and `anomaly_penalty` (7). With classification often off, **enhanced** path is the production primary.

## Present condition (after 2026-07 enhancement)

1. Paths: `signal_only` | `crop_specific` (named reliable crop + complete cycle) | **`enhanced`** (default agnostic).
2. Yield measure is an NDVI/CVI **proxy** — UI/docs say “yield proxy”; API still has `yield_potential_*` keys plus `average_yield_proxy`.
3. **`assessment_timing`**: flags in-progress / AUC-capped cycles so mid-season scoring is visible.
4. **`crop_family_band`**: coarse cereal / pulse / horticulture-style band from named crop or phenology heuristic.
5. Anomalies: IQR-based stress flags with stage-weighted impact.

## Done

| Item | Notes |
|------|--------|
| Yield proxy labeling | FE `humanizeKey` + PerformanceSection; API alias |
| Assessment timing metadata | Prevent silent mid-season bias |
| Crop-family banding | Soft fairness without full ML |

## Missing / next

| Priority | Item |
|----------|------|
| Medium | Broader synthetic-arc unit tests for enhanced health/yield functions |
| Medium | Confidence-weighted blend when ML confidence is mid-range |
| Major | Calibrate AUC → regional yield quantiles (district agri stats) |

## Interfaces

Upstream: Stage 04 `season_results` · Downstream: Stage 07 weights, Stage 08 explainability

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
