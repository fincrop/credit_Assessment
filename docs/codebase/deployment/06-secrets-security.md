# Deployment Stage 06 — Secrets & Security

## Purpose & Role

Credentials for Mongo, GEE, JWT, optional API/LLM keys, and AgriStack sandbox. Several footguns exist in the current tree; this stage records them for the hardening pass.

## Core Files & Key Functions/Classes

| Path | Notes |
|------|-------|
| `.gitignore` | Ignores `.env*`, `api/gee_service_account.json`, `**/gee_service_account.json` |
| `api/gee_service_account.json` | **Present on local disk** (verified); gitignored — still a risk if force-added or copied into images |
| `data_acquisition/satellite_collector.py` `_initialize_gee` | Env-based SA loading |
| `frontend/app/lib/jwt.ts` | Falls back to `'fallback-secret'` |
| `api/app.py` | Optional `API_SERVICE_KEY` |
| `frontend/app/config/endpoints.ts` | May embed sandbox sample credentials in token body |
| `frontend/proxy.ts` | Public `/webhook` without signature verification |

## Detailed Methodology

### GEE

Preferred: `GEE_SERVICE_ACCOUNT_B64` or `GEE_SERVICE_ACCOUNT_JSON` or `GEE_SA_KEY_PATH` pointing at a **non-repo** file. Do not commit SA JSON. Old `codebase.md` correctly flagged committed keys; `.gitignore` now lists the path — confirm `git ls-files` does not track it before every release.

### App auth

HS256 JWT in httpOnly cookie. **Must** set `AUTH_SECRET` in production.

### Service-to-service

Set `API_SERVICE_KEY` on API and `PIPELINE_API_SERVICE_KEY` on frontend when exposing the API publicly.

### Webhooks

Currently open POST endpoints — need shared secret/HMAC (see frontend stage 06).

## Inputs & Outputs

Secrets injected via Render dashboard / local env; never via git.

## Configuration & Dependencies

See Stage 01 env table.

## Current Implementation Notes

- Dockerfile does not copy `.env` (dockerignore).
- `CORS_ORIGINS=*` on blueprint is permissive.
- bcrypt for admin passwords in Mongo `users`.

## Known Limitations & Issues

1. Local `api/gee_service_account.json` may still exist from older workflows — rotate key if it was ever committed to a remote.
2. JWT fallback secret.
3. Unauthenticated webhooks.
4. Possible hardcoded sandbox credentials in endpoint samples.
5. `EXPOSE_INTERNAL_ERRORS` must stay off in prod.
6. No secret scanning CI (Stage 04 gap).

## Strengths

- `.gitignore` explicitly calls out GEE JSON.
- AI keys optional; enrichment degrades cleanly.
- Optional API key dependency already coded.

## Enhancement Recommendations

1. **Quick win:** Rotate GEE key; delete local JSON; use B64 env only; verify `git check-ignore -v api/gee_service_account.json`.
2. **Quick win:** Remove JWT fallback in production builds.
3. **Medium:** Webhook HMAC; purge secrets from `endpoints.ts`.
4. **Medium:** Enable GitHub secret scanning / gitleaks in CI.
5. **Major:** Move admin auth to IdP (Auth.js/OIDC); short-lived job tokens.

## Interfaces to Other Stages

Blocks safe deploy of 02–03; related technical debt in maintenance docs.
