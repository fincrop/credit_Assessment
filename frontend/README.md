# Agri Credit Risk — Frontend dashboard

Self-contained **Vite + React + TypeScript** UI for the satellite credit pipeline API (`POST /v1/assess`).

## Quick start

```bash
cd frontend
npm install
# edit .env (optional)
npm run dev
```

Open **http://localhost:5173**. Set **API base URL** to your FastAPI service (e.g. `http://127.0.0.1:8000` or your Render URL) and enter a **farmer_id** that exists in MongoDB `farm_info`.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `VITE_API_BASE_URL` | Default API origin in the form (optional; defaults to `http://127.0.0.1:8000` in dev). |
| `VITE_API_KEY` | Pre-fills the API key field if your backend uses `API_SERVICE_KEY`. |

## Production build

```bash
npm run build
npm run preview   # serves ./dist
```

Deploy **`dist/`** to any static host (Netlify, Vercel, Render Static, S3, etc.). Set `VITE_API_BASE_URL` at **build time** if you want a baked-in default for production.

## CORS

The browser calls the API directly. Configure **`CORS_ORIGINS`** on the FastAPI service to include your frontend origin (see repo root `DEPLOYMENT_AND_FRONTEND.md`).

## Moving this folder

This directory has no imports from parent repo paths. You can copy `frontend/` elsewhere and run `npm install` independently.
