# Backend Stage 07 — Credit Scoring & Limits

## Purpose

Converts upstream components into the score and ₹ limit a lender acts on. Live path: `AdvancedCreditScorer` + `PipelineConfig.CREDIT_WEIGHTS` / `CREDIT_LIMITS_PER_HA`.

## Present condition (after 2026-07 enhancement)

1. ML blend kill switch off → always rule-based weights: detection 35, performance 25, yield_potential 15, weather 8, anomaly 7, intensity 5, benefits 5.
2. Limit: live ₹/ha bands from config (same table as scorer) × intensity/area/crop/benefits multipliers; floor ₹1,000, ceil to ₹1,000.
3. **Benefits tri-state:** `None` = unknown (neutral), `False` = confirmed absent, `True` = enrolled — unknown is **not** scored like “no”.
4. Golden regression tests: `tests/test_credit_scoring_golden.py`.
5. Legacy `credit_scorer.py` **removed**; do not reintroduce divergent weight tables.

## Done

| Item | Notes |
|------|--------|
| Config ↔ Advanced alignment | Single source of truth |
| Tri-state benefits | Fairness vs data gaps |
| Golden score/limit tests | Catch silent regressions |

## Missing / next

| Priority | Item |
|----------|------|
| **Product** | Confirm whether live ~3–15k/ha bands are the intended loan scale |
| Major | Separate deterministic model score from tunable policy limit |
| Major | Recalibrate ₹/ha after product confirmation |
| Optional | Split `crop_detection` weight into land-use vs crop-confidence once ML/registry signal matures |

## Interfaces

Upstream: Stages 03–06 · Downstream: Stage 08, Mongo `credit_assessments`, dashboard hero

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
