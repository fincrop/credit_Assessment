# Agri-Credit Pipeline & Sandbox

Satellite-based agricultural credit assessment: continuous Sentinel-2 → crop cycles → weather/performance → rule-based credit score, with a Next.js operator UI (AgriStack sandbox + assessment dashboard).

**Codebase reference:** [docs/codebase/README.md](docs/codebase/README.md)

## Layout

| Path | Role |
|------|------|
| `backend/Credit_assessment/` | Python pipeline, FastAPI (`api/app.py`), worker, models |
| `frontend/` | Next.js app |
| `docs/codebase/` | Architecture and deployment docs |

## Local run

### 1. FastAPI backend (port 8000)

```bash
cd backend/Credit_assessment
# copy .env.example → .env and set MONGODB_URI (and GEE_* as needed)
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Assessment jobs (pick one)

- **In-process (recommended locally):** in `frontend/.env.local` set `PIPELINE_API_URL=http://127.0.0.1:8000` (and `PIPELINE_API_SERVICE_KEY` if the API uses `API_SERVICE_KEY`). Dashboard enqueue calls `POST /v1/jobs/assess`.
- **Separate worker:** leave `PIPELINE_API_URL` unset and from `backend/Credit_assessment` run `python worker.py`.

### 3. Next.js frontend (port 3000)

```bash
cd frontend
npm install
npm run dev
```

Set `AUTH_SECRET` (or `NEXTAUTH_SECRET`) and `MONGODB_URI` in `frontend/.env.local`.

### 4. Public tunnel for AgriStack webhooks (optional)

```bash
npx cloudflared tunnel --url http://localhost:3000
```

Point `NEXT_PUBLIC_APP_DOMAIN` at the tunnel URL. Optionally set `WEBHOOK_SECRET` and send header `x-webhook-secret`.

## Deploy

See [docs/codebase/deployment/00-overview.md](docs/codebase/deployment/00-overview.md) and `render.yaml` (Docker API under `backend/Credit_assessment` + Node frontend on Render).

## Dev scripts

One-off utilities live under `backend/Credit_assessment/scripts/devtools/` (not on the production path).


cd backend/Credit_assessment
uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

cd frontend
npm run dev