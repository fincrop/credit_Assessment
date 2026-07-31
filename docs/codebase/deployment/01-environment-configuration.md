# Deployment Stage 01 — Environment Configuration

## Purpose & Role

Central inventory of environment variables across Python API/worker and Next.js BFF. Defaults differ from some older markdown (notably Mongo DB name).

## Core Files & Key Functions/Classes

Variables are read in: `api/app.py`, `worker.py`, `main.py`, `mongodb_helper.py`, `config.py` (`AI_CONFIG`), `data_acquisition/satellite_collector.py`, `api/serialization.py`, and frontend `process.env` usages listed below.

## Detailed Methodology

Treat `backend/Credit_assessment/.env` (gitignored via `.env*`) for Python; `frontend/.env.local` for Next. Templates: `backend/Credit_assessment/.env.example` and root `.env.example` (index). Relative paths such as `CROP_MODEL_PATH` and `GEE_SA_KEY_PATH` resolve against `backend/Credit_assessment/`.

## Inputs & Outputs

N/A — configuration surface for all runtime modes.

## Configuration & Dependencies — full list

### Shared / Mongo

| Variable | Req? | Default | Where read | Purpose |
|----------|------|---------|------------|---------|
| `MONGODB_URI` | **Yes** for DB/jobs | none | `mongodb_helper.py`, `api/app.py`, `worker.py`, `frontend/lib/mongodb.ts` | Connection string |
| `MONGODB_DATABASE` | No | `agristack` | same + frontend routes | DB name |
| `MONGODB_DB` | No | (alias) | same | Alias for database name |

### Python pipeline / API

| Variable | Req? | Default | Where | Purpose |
|----------|------|---------|-------|---------|
| `CROP_MODEL_PATH` | No | `models/crop_classifier_model.joblib` | `api/app.py`, `worker.py` | Classifier artifact |
| `ML_MODE` | No | `rule_based` | API/worker | Scorer mode (blend usually off) |
| `USE_MONGODB` | No | `true` | `api/app.py` | Pipeline DB features |
| `PIPELINE_VERBOSE` | No | off | API/worker | Verbose pipeline logs |
| `ENABLE_CROP_CLASSIFICATION` | No | `false` | API/worker | Default Path A vs B |
| `API_SERVICE_KEY` | No | unset | `api/app.py` | Require `X-API-Key` |
| `CORS_ORIGINS` | No | `*` | `api/app.py` | CORS allow list |
| `EXPOSE_INTERNAL_ERRORS` | No | off | `api/serialization.py` | Include tracebacks |
| `PORT` | No | `8000` | Dockerfile CMD | Bind port (Render injects) |
| `SATELLITE_PROVIDER` | No | `gee` | collector, `main` cache key | `gee` or STAC |
| `SATELLITE_CACHE_TTL_DAYS` | No | `30` | `main.py` | Cache TTL |
| `GEE_PROJECT` | No* | | collector | EE project |
| `GEE_SA_KEY_PATH` | No* | | collector | Path to SA JSON |
| `GEE_SERVICE_ACCOUNT_JSON` | No* | | collector | Inline JSON |
| `GEE_SERVICE_ACCOUNT_B64` | No* | | collector | Base64 JSON |
| `GEE_SERVICE_ACCOUNT` | No* | | collector | SA email helper |
| `GROQ_API_KEY` | No | | `config.AI_CONFIG`, groq module | LLM |
| `GROQ_ENABLE` | No | off | `config.py` | Must enable Groq |
| `GROQ_MODEL` | No | `llama-3.1-70b-versatile` | config/groq | Model id |
| `GROQ_DRY_RUN` | No | `0` | groq generator | Skip real calls |
| `SARVAM_API_KEY` | No | | config/sarvam | Translation |

\*Required for GEE provider; STAC path can run without GEE creds.

### Frontend / BFF

| Variable | Req? | Default | Where | Purpose |
|----------|------|---------|-------|---------|
| `PIPELINE_API_URL` | Recommended | unset | `assess/enqueue` | FastAPI base (inline jobs) |
| `ASSESSMENT_API_URL` | No | | enqueue | Alias |
| `PIPELINE_API_SERVICE_KEY` | If API keyed | | enqueue | `X-API-Key` |
| `API_SERVICE_KEY` | No | | enqueue | Fallback key name |
| `AUTH_SECRET` | **Yes** in prod | fallback string | `jwt.ts` | JWT signing |
| `NEXTAUTH_SECRET` | No | | `jwt.ts` | Alias |
| `NEXT_PUBLIC_APP_DOMAIN` | For webhooks | `http://localhost:3000` | endpoints, agristack, WebhookResponses | Public URL |
| `NODE_ENV` | set by host | | cookies, TLS flags | |
| `NODE_VERSION` | Render | `20.11.0` in blueprint | render.yaml | |

## Current Implementation Notes

- `render.yaml` sets `MONGODB_DATABASE=agristack`, `ML_MODE=rule_based`, `USE_MONGODB=true`, `CORS_ORIGINS=*`, generates `AUTH_SECRET` for frontend.
- Templates: root `.env.example` (index) and `backend/Credit_assessment/.env.example` (Python).

## Known Limitations & Issues

- Old deployment doc default DB `agricultural_credit_db` contradicts code (`agristack`).
- JWT `'fallback-secret'` if unset (`frontend/app/lib/jwt.ts`).
- `CORS_ORIGINS=*` with credentials middleware flags needs careful review for cookie auth cross-origin.

## Strengths

- Sensible dual naming (`MONGODB_DATABASE`/`MONGODB_DB`, pipeline URL aliases).
- AI keys gated so missing keys do not crash scoring.

## Enhancement Recommendations

1. **Quick win:** Add root + `frontend/.env.example` from this table.
2. **Medium:** Fail fast in production when `AUTH_SECRET` or `MONGODB_URI` missing.
3. **Medium:** Split CORS for credentialed admin app vs public health.

## Interfaces to Other Stages

Consumed by container/host stages 02–03 and secrets 06; mirrors backend/frontend runtime.
