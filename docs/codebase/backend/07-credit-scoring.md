# Backend Stage 07 — Risk Index Construction

> **Rewritten 2026-08-15.** The previous version of this page described
> `AdvancedCreditScorer`, a 7-component weight table, and live ₹/ha limit bands.
> **None of that exists.** `advanced_credit_scorer.py` was deleted in the
> index_v5 cutover and there is no rupee-limit logic anywhere in the codebase.
> Anyone planning work from the old page would have been planning against a
> system that had already been replaced.

## Purpose

Converts upstream components into an explainable **agronomic risk index** (0–100,
higher = lower risk). Live path: `assessment/risk_index_engine.py`
(`RiskIndexEngine`) + `PipelineConfig.SUBINDEX_WEIGHTS`, with
`assessment/legacy_credit_shim.py` reshaping the output into the older
`credit_assessment` dict the dashboard still reads.

**This stage does not produce a loan amount.** Every `risk_assessment` carries
`no_repayment_calibration: True` and `positioning: "agronomic_risk_index"`.
There is no ₹/ha table, no interest rate, no tenure, no approve/reject decision.
Rupee calibration requires repayment outcomes we do not have — see
[`BACKEND-ENHANCEMENTS.md`](../../../BACKEND-ENHANCEMENTS.md) §6.9, items F-8/F-9.

## Present condition

1. **Four additive sub-indices** (weights must sum to 100, validated at import by
   `PipelineConfig.validate_config()`):

   | Key | Weight | Banker's C |
   |-----|--------|------------|
   | `landuse` | 30 | Capacity |
   | `vigor` | 25 | Capacity / Character |
   | `stability` | 20 | Character |
   | `weather` | 25 | Conditions |

   Weights are **AHP-provisional and pending expert sign-off** — disclose them as
   provisional in any UI.

2. **Data-confidence is a multiplicative gate**, not an additive component:
   `index_score = raw_index × gate`, with `gate ∈ [CONFIDENCE_GATE_MIN (0.60), 1.0]`.
   Note the floor: even a parcel with very poor observation quality retains 60%
   of its raw index.

3. **Benefits tri-state:** `None` = unknown (neutral, no penalty), `False` =
   confirmed absent, `True` = enrolled (+bonus, capped at `BENEFITS_BONUS_MAX`).
   Unknown is **not** scored like "no". This is preserved end-to-end including
   through persistence.

4. **Risk bands** (`RISK_THRESHOLDS`): `LOW` ≥ 70, `MEDIUM` ≥ 50, `HIGH` ≥ 30,
   `VERY_HIGH` < 30.

5. **Reason codes** are deterministic `{code, message, polarity}` triples. The
   `VIGOR_STRONG` message is conditional on `sub_indices.vigor.inputs.peer_relative`
   — it only claims a peer comparison when one actually ran.

6. Golden regression tests: `tests/test_credit_scoring_golden.py`.

7. Legacy `credit_scorer.py` and `advanced_credit_scorer.py` are **removed**. Do
   not reintroduce divergent weight tables.

## Known limitations

| Severity | Item |
|----------|------|
| **P0** | No land-cover gate upstream — this stage will score water bodies, built-up land and forest, because nothing before it checks the parcel is farmland. Fixing that is Phase 1, not a change to this stage. |
| **P0** | Perennial plots (orchard/banana/sugarcane) reach this stage with zero detected cycles and score as abandoned land. |
| **P1** | `vigor` is described in places as peer-relative but is a self-calibrated absolute score until `cohort_stats` is populated. |
| **P1** | `stability` rewards the *absence* of anomalies, so a parcel with no vegetation signal at all scores well on it. |
| **P2** | Weights are provisional; no outcome data exists to calibrate them. |

## Interfaces

Upstream: Stages 03–06 · Downstream: Stage 08 (AI enrichment), Mongo
`credit_assessments`, dashboard hero + `SubIndexBars`

---
*Current defect register and phased plan: [`BACKEND-ENHANCEMENTS.md`](../../../BACKEND-ENHANCEMENTS.md)*
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
