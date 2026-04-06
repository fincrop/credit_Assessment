# Agri credit pipeline + FastAPI for Linux (Render, Fly, etc.)
# Geospatial stack matches environment.yml via Conda; pip pins from requirements.txt.

FROM continuumio/miniconda3:latest

WORKDIR /app

# Conda env (Python 3.11 + GDAL / rasterio / geopandas)
COPY environment.yml .
RUN conda env create -f environment.yml && conda clean -afy

ENV PATH=/opt/conda/envs/agri_credit/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Python deps (pinned) + API stack
COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt

# Application code + model artifacts (ensure crop_classifier_model.joblib is in repo or image build context)
COPY . .

# Render injects PORT; default 8000 for local docker run
EXPOSE 8000
CMD ["sh", "-c", "uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
