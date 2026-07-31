# Stages 03–09 — Approved action summary

**Status:** Approved 2026-07-14 (recommended sets from comparison Waves 1–3)  
**Deferred majors excluded:** SAR, PlanetScope, dynamic monsoon snap, ICAR shapefile join, distributed queue, ERA5/IMD blend, full classifier retrain, perennial branch (track for later).

## Stage 03 — Cycle detection
| ID | Decision | Work |
|----|----------|------|
| C1 | Approve | Soft-apply `agro_profile` / sowing hint (peak-CVI nudge, sow bias); `hints_applied: true` |
| C2 | Approve | Disambiguate `land_utilization_fraction` vs `cycles_per_year` in schema |
| C3 | Defer | Perennial / long-duration branch |
| C4 | Defer | Hat-imputation ground-truth study |

## Stage 04 — Crop classification
| ID | Decision | Work |
|----|----------|------|
| D4a | Approve | Registry crop → `predicted_crop` with `source: registry_self_report` when ML off |
| D4b | Defer | Bayesian registry prior on ML path |
| D4c | Defer | Model card / sklearn pin / dashboard toggle (FE light touch ok later) |

## Stage 05 — Weather
| ID | Decision | Work |
|----|----------|------|
| E1 | Approve | Mongo cache POWER by (lat, lon, date range) |
| E2 | Approve | Explicit `weather_degraded` / unavailable flag |
| E3 | Defer | Parcel-weighted multi-point / IMD / ERA5 |

## Stage 06 — Performance
| ID | Decision | Work |
|----|----------|------|
| F1 | Approve | Rename display to yield proxy / vigor (API aliases + FE labels) |
| F2 | Approve | Assessment-timing flag for active cycles |
| F3 | Approve | Coarse crop-family vigor banding when no full crop label |
| F4 | Approve | Synthetic-arc unit tests for enhanced scoring |

## Stage 07 — Credit
| ID | Decision | Work |
|----|----------|------|
| G1 | Product hold | ₹/ha scale confirmation — do **not** change bands without business OK |
| G2 | Approve | Golden-file regression tests score + limit |
| G3 | Approve | Distinguish unknown vs confirmed-absent benefits |
| G4 | Defer | Separate model score vs policy limit layers |

## Stage 08 — AI enrichment
| ID | Decision | Work |
|----|----------|------|
| H1 | Approve | `AI_ENRICHMENT_ENABLE` master switch |
| H2 | Approve | Rename rule attribution labels away from unqualified “SHAP” in user-facing strings |
| H3 | Approve | Snapshot Groq/Sarvam prompt + model id when used |
| H4 | Defer | Separate compliance export endpoint |

## Stage 09 — Jobs
| ID | Decision | Work |
|----|----------|------|
| J1 | Approve | Reaper: RUNNING past timeout → FAILED |
| J2 | Approve | Queue health endpoint (depth, oldest, consumer) |
| J3 | Approve | Progress field from `pipeline_stages` |
| J4 | Defer | Bounded pool / distributed queue / job ownership authz (authz = medium; do light if cheap) |
