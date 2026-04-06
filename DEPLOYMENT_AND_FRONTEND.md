# Deployment (Render + Docker) and frontend integration

This document matches the **FastAPI** layer in `api/app.py`, the **MongoDB** schema built in `mongodb_helper.py`, and the **pipeline** output shape from `main.py`.

---

## 1. What was added

| Piece | Role |
|--------|------|
| `api/app.py` | FastAPI app: `POST /v1/assess`, `GET /v1/assess/{farmer_id}`, `GET /health` |
| `api/serialization.py` | Shrinks JSON (no satellite scene arrays; optional strip of `satellite_data`) |
| `Dockerfile` | Conda env from `environment.yml` + `pip install -r requirements.txt`, runs **uvicorn** |
| `.env.example` | **Template only** — copy to `.env` locally; set real vars on Render |
| `.dockerignore` | Keeps `.conda`, `.env`, git metadata out of the image |

**Secrets:** `MONGODB_URI` is read from the environment. Hardcoded Mongo credentials were removed from `mongodb_helper.py`.

---

## 2. Environment variables (deployment)

Copy `.env.example` → `.env` for local runs. For **Render**, define the same keys in **Environment**.

| Variable | Required | Purpose |
|----------|----------|---------|
| `MONGODB_URI` | **Yes** (for DB-backed runs) | MongoDB connection string |
| `MONGODB_DATABASE` | No | DB name (default `agricultural_credit_db`) |
| `API_SERVICE_KEY` | No | If set, require `X-API-Key` on assess routes |
| `CROP_MODEL_PATH` | No | Default `models/crop_classifier_model.joblib` |
| `ML_MODE` | No | Default `rule_based` |
| `USE_MONGODB` | No | `true` / `false` |
| `CORS_ORIGINS` | No | `*` or comma-separated origins for your frontend |
| `GROQ_API_KEY`, `GROQ_ENABLE`, `SARVAM_API_KEY` | No | AI enrichment (see `config.py`) |
| `EXPOSE_INTERNAL_ERRORS` | No | `1` only in dev — includes tracebacks in API JSON |

---

## 3. API contract (for your future webpage)

**Base URL:** `https://<your-render-service>.onrender.com` (or custom domain).

**Health**

- `GET /health` → `{ "status": "ok", "pipeline_loaded": true, "mongodb": true }`

**Run assessment (farmer must exist in `farm_info`)**

- `POST /v1/assess`  
  - Body (JSON): `{ "farmer_id": "potato_05", "include_heavy": false }`  
  - Header (if `API_SERVICE_KEY` is set): `X-API-Key: <secret>`
- `GET /v1/assess/{farmer_id}?include_heavy=false`  
  - Same header rule when `API_SERVICE_KEY` is set.

**Response shape**

- Same logical structure as the in-process pipeline dict, but:
  - **Default:** `satellite_data` omitted; `continuous_data_stats` remains on the root when present.
  - **`include_heavy: true`:** includes trimmed `satellite_data` (still **no** per-scene pixel/NDVI arrays).

**Failure**

- `status: "FAILED"` with `error` message; `traceback` omitted in production unless `EXPOSE_INTERNAL_ERRORS=1`.

**Interactive docs**

- `GET /docs` (Swagger UI) when the service is running.

---

## 4. MongoDB and workflow (how the page should think about data)

1. **`farm_info`**  
   - One document per farm: `farmer_id`, `latitude`, `longitude`, `field_area_ha`, `geometry`, optional `crop`, `sowing_date`, `farmer_benefits`, etc.  
   - The API only needs **`farmer_id`**; the pipeline loads the rest from Mongo.

2. **`credit_assessments`**  
   - Each successful run (CLI or API) can persist a **denormalized** document via `AssessmentSchema.build` (scores, seasonal NDVI rows, weather summary, `ai_enrichment` previews, etc.).  
   - Good for the **frontend**: list history by `farmer_id`, show last score, drill down into one assessment id.

3. **Suggested UI flow**

   - **List farmers** (read-only): either a small admin API you add later, or direct **read** from Mongo/Atlas (server-side BFF), or static JSON for demos.  
   - **“Run assessment”** button: `POST /v1/assess` with `farmer_id` → show loading state for **several minutes**.  
   - **Results**: map JSON fields to cards — credit score, risk, limit, cropping summary, weak components, optional `ai_enrichment.explainability` / `counterfactuals`.

---

## 5. Render + Docker

1. Push this repo to GitHub (include `models/crop_classifier_model.joblib` **or** inject it at build time; the default path must exist in the image).  
2. **New Web Service** → **Docker** → root `Dockerfile`.  
3. Set **Environment** variables (at least `MONGODB_URI`).  
4. **Important — timeouts:** one assessment often runs **many minutes** (satellite + STAC). Render’s HTTP **request timeout** may kill long synchronous requests on smaller plans. Options:
   - Use a **paid** instance / higher timeout if available; or  
   - Evolve to **async jobs** (enqueue run → poll status → fetch result from Mongo); or  
   - Run the heavy pipeline on a **worker** and keep the web service thin.

5. **Cold start:** first request after idle may be slow (Conda env + model load).

---

## 6. Bundled dashboard (`frontend/`)

A **Vite + React** app lives in **`frontend/`**: run assessment by `farmer_id`, tabbed report (credit, components, cropping, performance, weather, cycles, AI, raw JSON). See **`frontend/README.md`**.

## 7. Frontend integration checklist (next steps)

1. **Host:** deploy `frontend/dist` to Vercel / Netlify / Render Static / S3+CloudFront, *or* iterate inside `frontend/` and move the folder out later.  
2. **CORS:** set `CORS_ORIGINS` on the API to your frontend origin (not `*` if you use cookies later).  
3. **Secrets:** never put `MONGODB_URI` or `API_SERVICE_KEY` in frontend code; only the **public** API base URL + optional `X-API-Key` if you expose a **scoped** key (better: **BFF** — your frontend talks to your Next/Express server, which holds the key).  
4. **UX:** progress indicator + cancel is hard for sync HTTP; prefer messaging like “Analysis in progress (2–8 min)” or move to job + polling.  
5. **Data refresh:** after success, either use returned JSON or query **`credit_assessments`** for the latest `assessment_date` for that `farmer_id`.

---

## 8. Local quick test

```bash
cp .env.example .env   # edit MONGODB_URI
conda activate ./.conda   # or your env
uvicorn api.app:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` and try `POST /v1/assess`.

---

## 9. Optional hardening (later)

- Rate limiting (e.g. `slowapi`).  
- JWT or OAuth instead of static `X-API-Key`.  
- Separate **read** API for `credit_assessments` so the browser never needs Mongo credentials.  
- OpenAPI-generated client for TypeScript (`openapi-typescript-codegen`).
