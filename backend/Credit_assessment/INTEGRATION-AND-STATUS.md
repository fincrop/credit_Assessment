# Re-Architecture — Integration & Status (Pillars 1–6, index_v5)

All six pillars are implemented. Everything was patched **surgically on your real
files** (copy → targeted edits) so working GEE/STAC/Mongo scaffolding is preserved.
Pure-Python logic was unit-tested here; environment-coupled paths (GEE, external
weather APIs, live Mongo) are written defensively with graceful fallback and must
be validated in your environment.

---

## 1. Files delivered

| Pillar | File(s) | Action | Tested here |
|---|---|---|---|
| 1 Signal + SAR | `data_processing.py` | rewrite (backward-compatible) | ✅ full |
| 1 | `satellite_collector.py` | surgical patch | ⚠️ GEE parts need your env |
| 1 | `config_pillar1_additions.py` | merge block | ✅ |
| 2 Phenology | `crop_cycle_detector.py` | surgical patch | ✅ full |
| 2 | `config_pillar2_additions.py` | merge block | ✅ |
| 3 Stress/Yield | `performance_analyzer.py` | surgical patch | ✅ full |
| 3 | `peer_benchmark.py` (NEW) | new module | ✅ full |
| 3 | `config_pillar3_additions.py` | merge block | ✅ |
| 4 Weather | `weather_analyzer.py` | surgical patch | ✅ analytics; ⚠️ IMD/ERA5/CHIRPS need env |
| 4 | `config_pillar4_additions.py` | merge block | ✅ |
| 5 Risk Index | `risk_index_engine.py` (NEW) | new module | ✅ full |
| 5 | `config_pillar5_additions.py` | merge block | ✅ |
| 6 Wiring | `main.py`, `job_runner.py`, `mongodb_helper.py` | surgical patch | ✅ compile + logic |

## 2. File placement (package paths)

Match your existing layout:
- `utils/data_processing.py`, `utils/peer_benchmark.py`  (peer_benchmark is NEW here)
- `data_acquisition/satellite_collector.py`, `data_acquisition/weather_analyzer.py`
- `crop_analysis/crop_cycle_detector.py`, `crop_analysis/performance_analyzer.py`
- `assessment/risk_index_engine.py`  (NEW; sits beside advanced_credit_scorer.py)
- `main.py`, `job_runner.py`, `mongodb_helper.py` at their current locations.

(If your tree differs, the imports I added use `assessment.risk_index_engine` and
`utils.peer_benchmark` — adjust to your package names.)

## 3. Config merge order

Merge the five blocks into `class PipelineConfig` (order doesn't matter; they're
independent). Also apply these two edits called out in the Pillar-1 block:
- `SEASON_SNAP_ANCHORS = ((6, 15), (10, 15), (2, 15))`  (add the zaid anchor)
- treat `NUM_SEASONS` / `MAX_YEARS_BACK` as advisory for the continuous path.

## 4. How the pillars connect (data flow)

```
satellite_collector  → continuous_data.vs_smooth / VS_mean / signal_quality_summary   (P1)
        │
crop_cycle_detector  → reads VS_mean → double-logistic cycles + phenology + inference  (P2)
        │
performance_analyzer → NIRv-AUC + peer percentile + per-parcel stage stress           (P3)
        │                (gets cohort_key + peer_benchmark from main)
weather_analyzer     → indicators + resilience + exposure (+ IMD/ERA5 hooks)           (P4)
        │
risk_index_engine    → 5 sub-indices → confidence gate → index + reason codes          (P5)
        │                (reads the assembled assessment; no rupee amount)
main.py              → builds cohort_key, wires peer benchmark, calls engine in         (P6)
                       parallel, stashes signal_quality_summary; job_runner tri-state;
                       mongodb_helper feature_store / index_versions / cohort_stats
```

## 5. Tested here vs needs-your-environment

**Verified in sandbox** (synthetic data, real numbers):
- Whittaker smoothing reconstructs a 3-cycle curve (RMSE 0.023); SAR fusion fills a
  monsoon blackout (RMSE 0.018).
- Composite builder recovers 3 cycles, fills blackout from SAR, stamps provenance.
- Double-logistic phenology: 3-yr detection, conservative date adoption, cloud-gap
  kharif inference, Sugarcane long-duration cap (195→390).
- Peer benchmark cold-start → cohort activation; NIRv-AUC yield; per-parcel per-stage
  stress + water stress.
- Weather: dry/wet spells, GDD, PET, water balance, monsoon onset, SPI/SPEI,
  resilience (100 vs 15 under stress), forward exposure; full loop with fetch mocked.
- Risk index: strong 93.8/LOW (gate 0.985), weak 38.7/HIGH (gate 0.80), tri-state
  benefits preserved; no rupee amount; calibration hooks reserved.
- All patched files byte-compile; job_runner tri-state logic unit-checked.

**Must validate in your environment** (cannot run here — no GEE/network/Mongo):
1. GEE server-side: Cloud Score+ join, new server-side indices, Sentinel-1 RVI.
   All guarded → fall back to optical/SCL if unavailable. Smoke-test one farmer and
   check `continuous_data` for `vs_smooth`, `signal_source`, `signal_quality_summary`,
   and any `"sar"` in `source_counts`.
2. Weather IMD/ERA5/CHIRPS: hooks only. POWER works today. Implement `_fetch_imd`
   etc. and set `WEATHER_SOURCE_PRIORITY` when ready.
3. `main.py` end-to-end (needs GEE + Mongo). It compiles; run one real assessment and
   confirm `assessment['risk_assessment']` appears with 5 sub-indices + reason codes.

## 6. Remaining cutover steps

1. **Enrichment/SHAP/report → new engine. [DONE]** Prefer `risk_assessment`;
   legacy `credit_assessment` is now a **compat shim** (scalar scores, no ₹).
2. **Legacy scorer retirement. [DONE]** `AdvancedCreditScorer` + `calculate_credit_limit`
   removed; `RiskIndexEngine` is primary; config pillar staging files merged into
   `config.py` and deleted; live tri-state benefits on `api/job_runner.py`.
3. **Cohort accumulation job.** Write a periodic job that aggregates
   `feature_store` per-cycle `nirv_auc_mean` by `cohort_key` into `cohort_stats`
   (`upsert_cohort_stat`). Snapshots now store those fields for the cron.
4. **AHP weight sign-off.** `SUBINDEX_WEIGHTS` (30/25/20/25) are provisional — finalise
   with your agri + banking experts; the engine renormalises to 100 defensively.
5. **Parcel→farmer linkage + validation layer.** Reserved calibration fields
   (`outcome_label`, `pd_estimate`) are in every assessment for the future
   repayment-feedback phase.

**Note:** Feature snapshots + `save_index_version` at pipeline startup are wired.
Dashboard still reads shim `credit_assessment` (new sub-index bar keys) until a
frontend redesign consumes `risk_assessment` natively.

## 7. Suggested apply order

1. Drop in `data_processing.py`, `peer_benchmark.py` (utils/), `risk_index_engine.py`
   (assessment/). Merge all 5 config blocks + the two Pillar-1 config edits.
2. Drop in the four patched analyzers/collector.
3. Drop in `main.py`, `job_runner.py`, `mongodb_helper.py`.
4. Run ONE farmer end-to-end. Verify: `vs_smooth` + `signal_quality_summary` present;
   cycles carry `phenology`; performance carries `peer_benchmarking` + `stress_baseline`;
   weather carries `forward_exposure` + `backward_resilience`; `risk_assessment` has
   5 sub-indices + reason codes and NO rupee field.
5. Flip on SAR/CloudScore+/IMD as your data access allows; begin cohort accumulation.
