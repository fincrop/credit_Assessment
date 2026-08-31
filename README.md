# Agri-Credit Pipeline & Sandbox

Satellite-based agricultural credit assessment: continuous Sentinel-2 → crop cycles → weather/performance → rule-based credit score, with a Next.js operator UI (AgriStack sandbox + assessment dashboard).

**Codebase reference:** [docs/codebase/README.md](docs/codebase/README.md)  
**AgriStack / Lambda status:** [UPDATE.md](UPDATE.md)

## Layout

| Path | Role |
|------|------|
| `backend/Credit_assessment/` | Python pipeline, FastAPI (`api/app.py`), worker, models |
| `frontend/` | Next.js app |
| `infra/agristack-lambda/` | Mumbai Lambda: AgriStack Token/Seek/KDSS proxy + webhooks → Mongo |
| `docs/codebase/` | Architecture and deployment docs |

## Local run

### 1. FastAPI backend (port 8000)

Use the repo **`.conda`** interpreter (Python 3.11, gitignored at repo root). Do **not** use system/base `python` (often 3.12/3.13) — wrong versions and missing pins.

**One-time setup** (from repo root):

```bash
conda env create -f backend/Credit_assessment/environment.yml -p .conda
.conda/python -m pip install -r backend/Credit_assessment/requirements.txt
```

**Run API** (always via `.conda`):

```powershell
cd backend\Credit_assessment
.\scripts\dev-api.ps1 -Reload
```

Or manually:

```bash
cd backend/Credit_assessment
../../.conda/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
```

Use **`127.0.0.1`** (not `0.0.0.0`) so it matches `PIPELINE_API_URL=http://127.0.0.1:8000` in `frontend/.env.local` and you do not get two listeners on port 8000 on Windows.

On Linux/macOS use `../../.conda/bin/python` instead of `../../.conda/python.exe`.

**Assess flow:** the browser never calls uvicorn directly. Dashboard → Next.js `POST /api/assess/enqueue` → FastAPI `POST /v1/jobs/assess` (requires `PIPELINE_API_URL` in `frontend/.env.local`).

**Windows / `--reload`:** if Assess says the pipeline is unreachable but uvicorn looks running, stale reload processes may all be listening on port 8000. Stop them, then start one server:

```powershell
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
cd backend/Credit_assessment
../../.conda/python.exe -m uvicorn api.app:app --host 127.0.0.1 --port 8000 --reload
```

Verify: `curl http://127.0.0.1:8000/health/live` should return `{"status":"ok"}` immediately.

### 2. Assessment jobs (pick one)

- **In-process (recommended locally):** in `frontend/.env.local` set `PIPELINE_API_URL=http://127.0.0.1:8000` (and `PIPELINE_API_SERVICE_KEY` if the API uses `API_SERVICE_KEY`). Dashboard enqueue calls `POST /v1/jobs/assess`.
- **Separate worker:** leave `PIPELINE_API_URL` unset and from `backend/Credit_assessment` run `python worker.py`.

### 3. Next.js frontend (port 3000)

```bash
cd frontend
npm install
npm run dev
```

Set in `frontend/.env.local` (see root `.env.example`):

- `MONGODB_URI`, `AUTH_SECRET` / `NEXTAUTH_SECRET`
- `PIPELINE_API_URL=http://127.0.0.1:8000`
- **AgriStack via Mumbai Lambda (recommended):**
  - `AGRISTACK_PROXY_URL` = your Lambda Function URL (no trailing slash)
  - `AGRISTACK_PROXY_SECRET` = same value as Lambda env `AGRISTACK_PROXY_SECRET`
  - `NEXT_PUBLIC_APP_DOMAIN` = same Lambda base URL (AgriStack `sender_uri` / webhooks)

With those set, **Cloudflare tunnel is not required** for AgriStack Seek/webhooks: outbound calls and inbound callbacks go through the India Lambda into Mongo.

### 4. Cloudflare tunnel (optional / legacy only)

Only needed if you are **not** using the Mumbai Lambda and AgriStack must POST to your laptop:

```bash
npx cloudflared tunnel --url http://localhost:3000
```

Then point `NEXT_PUBLIC_APP_DOMAIN` at the tunnel URL.

## Deploy (Render + Lambda)

| Piece | Where | Notes |
|-------|--------|--------|
| FastAPI | Render (see `render.yaml`) | Set `MONGODB_URI`, GEE secrets, optional `API_SERVICE_KEY` |
| Next.js | Render frontend service | Set Mongo, `AUTH_SECRET`, `PIPELINE_API_URL` → API URL, **plus** `AGRISTACK_PROXY_*` and `NEXT_PUBLIC_APP_DOMAIN` → Lambda |
| AgriStack edge | AWS Lambda `ap-south-1` | Upload `infra/agristack-lambda/function.zip`; set Mongo + `AGRISTACK_PROXY_SECRET` + `LAMBDA_PUBLIC_BASE_URL` |

Do **not** commit secrets. Do **not** put AgriStack passwords in `NEXT_PUBLIC_*` (they ship to the browser).

## Dev scripts

One-off utilities live under `backend/Credit_assessment/scripts/devtools/` (not on the production path).
