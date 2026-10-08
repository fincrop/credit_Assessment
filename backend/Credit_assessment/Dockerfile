# Agri credit pipeline + FastAPI for Linux (Render, Fly, etc.)
# Geospatial stack matches environment.yml via Conda; pip pins from requirements.txt.
# Build context: backend/Credit_assessment (see render.yaml).

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

# Field-boundary model (crop_analysis/ftw_model.py): CPU-only torch (the default
# wheel pulls CUDA, several GB) + the CC-BY FTW checkpoint. Delineation still
# runs without them, on satellite edges and FTW Global outlines only.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 \
 && pip install --no-cache-dir segmentation-models-pytorch==0.5.0
ENV FTW_MODEL_PATH=/opt/ftw/3_Class_CCBY_FTW_Pretrained.ckpt
ENV FTW_CACHE_DIR=/tmp/ftw_global
RUN mkdir -p /opt/ftw && python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/fieldsoftheworld/ftw-baselines/releases/download/v1/3_Class_CCBY_FTW_Pretrained.ckpt', '/opt/ftw/3_Class_CCBY_FTW_Pretrained.ckpt')"

# Application code + model artifacts (ensure crop_classifier_tier1_mh_v1.joblib and its
# .region_support.json are in the repo / image build context)
COPY . .

# Render injects PORT (often 10000); bind 0.0.0.0 so the port scan succeeds.
EXPOSE 10000
CMD ["sh", "-c", "echo \"Listening on 0.0.0.0:${PORT:-8000}\" && exec uvicorn api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
