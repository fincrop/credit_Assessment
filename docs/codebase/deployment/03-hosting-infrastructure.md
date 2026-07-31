# Deployment Stage 03 — Hosting Infrastructure

## Purpose & Role

Describes how services are expected to run in production vs local development, based on `render.yaml`, Docker, and the dual job-execution modes in code.

## Core Files & Key Functions/Classes

| Path | Role |
|------|------|
| `render.yaml` | Blueprint: API Docker (`dockerContext: backend/Credit_assessment`) + frontend Node (`rootDir: frontend`) |
| `backend/Credit_assessment/api/app.py` | Lazy pipeline init for port scan; `/health`; jobs + sync assess |
| Root `README.md` | Local uvicorn + Next + cloudflared notes |

## Detailed Methodology

### Production (Render blueprint)

| Service | Runtime | Start | Notes |
|---------|---------|-------|-------|
| `agri-credit-pipeline-api` | Docker | uvicorn on `$PORT` | `healthCheckPath: /health`; free plan; buildFilter on `backend/Credit_assessment/**` |
| `agri-credit-frontend` | Node 20.11 | `npm ci && npm run build` / `npm start` | Needs `PIPELINE_API_URL` → API URL; Mongo; `AUTH_SECRET` |

Frontend talks to API for enqueue when `PIPELINE_API_URL` set; both share Mongo.

**Not configured in blueprint:** dedicated `worker.py` service — inline BackgroundTasks on the API process is the intended Render path.

### Local

| Piece | Command / config |
|-------|------------------|
| API | `cd backend/Credit_assessment && uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload` |
| Jobs A | `PIPELINE_API_URL=http://127.0.0.1:8000` in frontend env |
| Jobs B | Unset URL + `cd backend/Credit_assessment && python worker.py` |
| Frontend | `cd frontend && npm run dev` |
| Webhooks | cloudflared tunnel to :3000 |

### Vercel

No `vercel.json`. Frontend *could* deploy to Vercel, but webhooks + long server routes + Mongo need careful runtime config; Render Node service is what the blueprint assumes. **Gap if targeting Vercel:** document serverless limits vs webhook/ingest.

## Inputs & Outputs

Hosted URLs for UI and API; Mongo Atlas (typical) as data plane.

## Configuration & Dependencies

See Stage 01. Blueprint marks `MONGODB_URI`, `NEXT_PUBLIC_APP_DOMAIN`, `PIPELINE_API_URL` as `sync: false` (set in dashboard).

## Current Implementation Notes

- Free Render: cold starts + HTTP timeouts — sync `/v1/assess` is risky; use `/v1/jobs/assess`.
- API serializes jobs with in-process lock — one heavy assessment at a time per instance.
- `buildFilter` avoids rebuilding API on frontend-only commits.

## Known Limitations & Issues

- No IaC beyond blueprint; no staging/prod split documented.
- Worker-only scaling path not in `render.yaml`.
- `DEPLOYMENT_AND_FRONTEND.md` outdated (Vite, static `dist`, wrong DB default).

## Strengths

- Two-service split matches real coupling (Next BFF + Python geospatial).
- Health check + lazy init improve deploy success on free tier.

## Enhancement Recommendations

1. **Quick win:** Update/remove stale `DEPLOYMENT_AND_FRONTEND.md` or point it here.
2. **Medium:** Paid plan / background worker service for concurrent farms.
3. **Medium:** Custom domains + non-`*` CORS for the real frontend origin.
4. **Major:** Separate staging blueprint with smaller models/fixtures.

## Interfaces to Other Stages

Build (02), env (01), CI (04), monitoring (05).
