# Deployment Stage 02 — Containerization & Build

## Purpose & Role

Packages the **Python API + pipeline** for Linux hosts (Render/Fly-style). Frontend is **not** in the Docker image (separate Node service; outside the API build context).

## Core Files & Key Functions/Classes

| Path | Role |
|------|------|
| `backend/Credit_assessment/Dockerfile` | Miniconda → `environment.yml` → `pip install -r requirements.txt` → `uvicorn api.app:app` |
| `backend/Credit_assessment/.dockerignore` | Drops `.env`, `__pycache__`, `outputs`, devtools, etc. |
| `backend/Credit_assessment/environment.yml` | Conda env `agri_credit` (Python 3.11 + GDAL/rasterio/geopandas stack) |
| `backend/Credit_assessment/requirements.txt` | Pinned pip deps (FastAPI, EE, shap, …) |
| `render.yaml` | `dockerContext` / `dockerfilePath` → `backend/Credit_assessment` |

## Detailed Methodology

1. Base: `continuumio/miniconda3:latest`.
2. `conda env create -f environment.yml` → PATH=`/opt/conda/envs/agri_credit/bin`.
3. `pip install --no-cache-dir -r requirements.txt`.
4. `COPY . .` with context = `backend/Credit_assessment` (respecting dockerignore).
5. `EXPOSE 10000`; CMD listens on `0.0.0.0:${PORT:-8000}`.

Model artifacts under `models/` must be present in build context (e.g. `crop_classifier_model.joblib`).

## Inputs & Outputs

**In:** `backend/Credit_assessment` context. **Out:** Image running FastAPI on `$PORT`.

## Configuration & Dependencies

Build needs network for conda/pip. Runtime needs env vars from Stage 01.

## Current Implementation Notes

- Comment in Dockerfile: geospatial stack matches `environment.yml`.
- Worker is **not** the Docker CMD — only uvicorn. Separate worker process would need a second service/start command.
- Image is large (Conda + geospatial).

## Known Limitations & Issues

- `miniconda3:latest` is floating — unreproducible builds over time.
- No multi-stage slim image.
- Frontend deploy path is entirely outside Docker.

## Strengths

- Matches Windows-dev Conda reality better than slim python images for GDAL.
- Build context is limited to the Python package (frontend never enters the API image).
