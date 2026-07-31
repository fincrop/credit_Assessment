# Backend enhancements — implementation status

**Last updated:** 2026-07-14  
**Canonical backlog:** [../10-comparison-and-enhancements.md](../10-comparison-and-enhancements.md)

## Done (shipped)

| Stage | IDs | What landed |
|-------|-----|-------------|
| 01 | A1 A2 A3 | Adaptive point buffer (~0.15 km floor), geometry QA + fallback, `geospatial_prep` |
| 01→03 | A4 / C1 | Soft agro + sowing priors; LUI `land_utilization_fraction` |
| 02 | B1 B2 B5 (+ stamp) | `indices_available` / provider / `cloud_mask_version`; per-job force-fresh; slim cache |
| 04 | D4a | Registry crop → `predicted_crop` when ML off |
| 03/04 | C2 | `cycles_per_year` + LUI aliases |
| 05 | E1 E2 | POWER cache; `weather_degraded` / status |
| 06 | F1–F3 | Yield proxy labels; assessment_timing; crop_family_band |
| 07 | G2 G3 | Golden tests; tri-state benefits |
| 08 | H1–H3 | `AI_ENRICHMENT_ENABLE`; attribution labels; model_snapshot |
| 09 | J1–J3 | Reaper; `/v1/jobs/health`; progress + dashboard |

## Missing now (near-term)

- Product confirm ₹/ha bands (G1)
- Classification dashboard toggle + model card (D4c / M5)
- Weather risk + enhanced-health unit tests
- Job ownership authz; optional concurrency pool
- Stronger STAC/GEE mask/index parity (optional)

## Deferred majors

Dynamic monsoon snap · ICAR shapefile · SAR · PlanetScope · perennial cycles · IMD/ERA5 · score vs policy split · compliance export · distributed queue · ML Bayesian prior + retrain

## Verify

```bash
python tests/test_credit_scoring_golden.py
# GET /v1/jobs/health  ·  GET /health
```
