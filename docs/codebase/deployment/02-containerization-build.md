# Deployment Stage 02 — Containerization & Build

## Purpose & Role

Packages the **Python API + pipeline** for Linux hosts (Render/Fly-style). Frontend is **not** in the Docker image (excluded by `.dockerignore`) and builds via Node on Render instead.

## Core Files & Key Functions/Classes

| Path | Role |
|------|------|
| `Dockerfile` | Miniconda → `environment.yml` → `pip install -r requirements.txt` → `uvicorn api.app:app` |
| `.dockerignore` | Drops `frontend`, `.env`, `.git`, `.github`, `outputs`, etc. |
| `environment.yml` | Conda env `agri_credit` (Python 3.11 + GDAL/rasterio/geopandas stack) |
| `requirements.txt` | Pinned pip deps (FastAPI, EE, shap, …) |

## Detailed Methodology

1. Base: `continuumio/miniconda3:latest`.
2. `conda env create -f environment.yml` → PATH=`/opt/conda/envs/agri_credit/bin`.
3. `pip install -r requirements.txt`.
4. `COPY . .` (respecting dockerignore).
5. `EXPOSE 10000`; CMD listens on `0.0.0.0:${PORT:-8000}`.

Model artifacts under `models/` must be present in build context (e.g. `crop_classifier_model.joblib`).

## Inputs & Outputs

**In:** Repo context. **Out:** Image running FastAPI on `$PORT`.

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

- Matches Windows-dev Conda reality better than slim python slim images for GDAL.
- `.dockerignore` correctly keeps secrets and frontend out of API image.

## Enhancement Recommendations

1. **Quick win:** Pin Miniconda digest / env yaml versions.
2. **Medium:** Optional second Dockerfile target or Render worker service: `python worker.py`.
3. **Major:** Multi-stage or micromamba to cut image size/cold start.

## Interfaces to Other Stages

Used by hosting (03); secrets must be injected at runtime (06), not baked in (`.env` ignored).
