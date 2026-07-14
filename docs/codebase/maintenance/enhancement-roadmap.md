# Enhancement Roadmap

Aggregated from stage docs. Cleanup pass marked below; remaining items are the restructuring backlog.

## Quick wins

| ID | Item | Status |
|----|------|--------|
| Q1 | Fix `ci` NameError in `shap_explainer._extract_features` | **Done** |
| Q2 | Env-gate cache bypass (`SATELLITE_FORCE_FRESH`) | **Done** |
| Q3 | Remove commented satellite/credit_scorer; delete `utils_init.py` | **Done** |
| Q4 | Align `PipelineConfig.CREDIT_*` with Advanced scorer | **Done** |
| Q5 | `SEASON_SNAP_ANCHORS` + corrected season comments | **Done** |
| Q6 | `.env.example`; prod requires `AUTH_SECRET` | **Done** |
| Q7 | Rotate GEE key if ever pushed; use B64 env only | **Ops** (file gitignored/untracked) |
| Q8 | Fix stale AI_CONFIG comments | **Done** (UI “yield proxy” rename still open) |
| Q9 | Rewrite README; point `DEPLOYMENT_AND_FRONTEND.md` at docs | **Done** |
| Q10 | Job reaper for stuck `RUNNING` | Open |
| Q11 | Remove sandbox secrets from `endpoints.ts` | **Done** |
| Q12 | Dashboard: show `pipeline_stages` / elapsed while polling | Open |
| Q13 | Optional `WEBHOOK_SECRET` gate | **Done** |

## Medium effort

| ID | Item | Status |
|----|------|--------|
| M1 | Wire registry sowing/crop hint + `agro_profile` into cycle detector | Open (now logged, not applied) |
| M2 | Extend LGD → eco-region map beyond UP | **Done** (major states) |
| M3 | Unify cloud thresholds in `PipelineConfig` | **Done** (`MAX_CLOUD_COVER_*`) |
| M4 | NASA POWER cache; unit tests for weather/credit math | Open |
| M5 | Expose `require_classification` in dashboard; sklearn pin | Open |
| M6 | Full webhook HMAC + auto-ingest + TTL | Partial (`WEBHOOK_SECRET` done) |
| M7 | Farmer picker; job ownership on status GET | Open |
| M8 | GitHub Actions CI | Open |
| M9 | Sentry + `/api/health`; progress on jobs | Open |
| M10 | Pin Miniconda; optional worker service | Open |
| M11 | Snapshot LLM prompt/model ids | Open |
| M12 | Tests for parcel cluster + Path B UI | Open |

## Major rework

| ID | Item |
|----|------|
| X1 | Recalibrate ₹/ha policy; separate model score from policy limit |
| X2 | Retrain crop classifier on production features; soft registry prior |
| X3 | Horizontal workers / external queue; stage tracing & SLOs |
| X4 | Calibrate NDVI AUC → regional yield quantiles |
| X5 | Slim Docker / staging vs prod blueprints |
| X6 | IdP admin auth; secret scanning in CI |
| X7 | Compliance explanations vs optional LLM narrative |
| X8 | Multi-parcel weather/satellite aggregation |

## Suggested next sequencing

1. **Ops:** Q7 GEE key rotation if needed; set `AUTH_SECRET` / `.env` from `.env.example`.
2. **Product:** Q12 progress UI → M1 hint wiring → M5 classification toggle.
3. **Platform:** M8 CI → Q10 job reaper → M9 monitoring.
