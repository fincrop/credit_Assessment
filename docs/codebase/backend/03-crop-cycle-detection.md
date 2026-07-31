# Backend Stage 03 — Crop Cycle Detection & Land Use

## Purpose

Crop-agnostic backbone when Stage 04 ML is off: cycle boundaries feed weather, performance, and most of the credit score.

## Present condition (after 2026-07 enhancement)

1. Preprocess → CVI (0.5 NDVI + 0.3 EVI + 0.2 NDMI) → Bartlett smooth → peak/sow/harvest walk.
2. **Soft priors applied** when provided:
   - `agro_profile`: peak-CVI floor nudge (clamped ~0.20–0.35), optional expected-cycles tweak.
   - `sowing_date_hint`: prefer sow candidates within ±21 days of hint (no hard override).
3. Meta: `hints_applied`, `applied_knobs` on `cycle_detection_diag` / detector `last_detection_meta`.
4. Land use: `land_utilization_index` **and** alias `land_utilization_fraction`.
5. Credit intensity uses **cycles/year** (`cycles_per_year` / `cropping_intensity`), not LUI fraction.

## Done

| Item | Notes |
|------|--------|
| Soft agro / sowing priors | Stage 01 context finally consumed |
| Intensity naming aliases | LUI fraction vs cycles/year clarified in schema |

## Missing / next

| Priority | Item |
|----------|------|
| Major | Perennial / long-duration branch (sugarcane, horticulture) |
| Major | Ground-truth validation of hat-profile imputation |
| Nice | Further collapse class fallback thresholds vs `PipelineConfig.CROP_CYCLE_*` |

## Interfaces

Upstream: Stage 02 series + Stage 01 eco/hints · Downstream: 04–07 by cycle dates

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
