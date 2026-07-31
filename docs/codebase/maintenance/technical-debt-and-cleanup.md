# Technical Debt & Cleanup

Last cleanup pass applied in-repo. Remaining items are intentional follow-ups (CI, hint wiring, key rotation ops).

## Resolved in cleanup pass

| Item | Resolution |
|------|------------|
| `shap_explainer.py` undefined `ci` | Fixed — `ci = float(ca.get('cropping_intensity', 0))` |
| `force_fresh_download = True` | Env-gated via `SATELLITE_FORCE_FRESH` (default off / cache used) |
| Commented satellite collector (~1200 lines) | Removed; active continuous collector only |
| Legacy `assessment/credit_scorer.py` | Deleted; `assessment` exports `AdvancedCreditScorer` only |
| `utils/utils_init.py` | Deleted |
| `CREDIT_WEIGHTS` / `CREDIT_LIMITS_PER_HA` mismatch | Aligned with Advanced scorer; scorer reads from `PipelineConfig` |
| Season comment / snap mismatch | Comments fixed; `SEASON_SNAP_ANCHORS` + `MAX_CLOUD_COVER_*` in config |
| AI_CONFIG “--explain flag” comment | Corrected — SHAP/CF run from enrichment when enabled |
| JWT `fallback-secret` | Production requires `AUTH_SECRET`; dev-only insecure default |
| Hardcoded AgriStack password in `endpoints.ts` | Removed — empty / env placeholders |
| Public webhooks | Optional `WEBHOOK_SECRET` + `x-webhook-secret` header |
| LGD map only UP | Extended major state codes → eco labels |
| Root clutter scripts | Moved to `scripts/devtools/` |
| Missing `.env.example` | Added at repo root (index) + `backend/Credit_assessment/.env.example` |
| Stale README / `DEPLOYMENT_AND_FRONTEND.md` | Rewritten to point at `docs/codebase/` |
| Registry hints silently discarded | Now logged; `hints_applied: false` in detection meta |

## Still open (ops / larger work)

| Item | Notes |
|------|-------|
| GEE SA JSON on disk | `api/gee_service_account.json` gitignored & untracked — **rotate** if ever pushed; prefer `GEE_SERVICE_ACCOUNT_B64` |
| Cycle hints / `agro_profile` not applied | Logged only — implement anchoring in a later enhancement |
| No CI workflows | See `docs/codebase/deployment/04-cicd-pipeline.md` |
| AgriStack may not send `x-webhook-secret` | Keep secret unset until gateway can attach the header, or terminate TLS at a proxy that injects it |

## Do not reintroduce

- Legacy `CreditScorer` / commented seasonal collector
- Hardcoded sandbox passwords
- `force_fresh_download = True` without env gate
- Divergent credit weight tables outside `PipelineConfig.CREDIT_WEIGHTS`
