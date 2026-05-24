"""
FastAPI service: run the satellite credit pipeline by farmer_id (MongoDB-backed farms).

Run locally (repo root):
  pip install fastapi uvicorn python-dotenv
  set MONGODB_URI=...   # Windows
  uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

Render sets PORT; Docker CMD uses $PORT.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId
from pymongo import MongoClient, ReturnDocument

# Load .env from repo root before pipeline / config import
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env")
    except ImportError:
        pass


_load_dotenv()

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.job_runner import process_assessment_job, utc_now
from api.serialization import slim_assessment_for_api

logger = logging.getLogger(__name__)

_pipeline: Any = None
_pipeline_init_lock: Optional[asyncio.Lock] = None
_mongo_for_jobs: Optional[MongoClient] = None
_jobs_col: Any = None
_pipeline_job_lock: Optional[asyncio.Lock] = None


def _cors_origins() -> List[str]:
    raw = os.environ.get("CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _optional_api_key() -> Optional[str]:
    k = os.environ.get("API_SERVICE_KEY", "").strip()
    return k or None


def _create_pipeline() -> Any:
    """Load geospatial stack + model in a worker thread (slow on cold start)."""
    from main import SatelliteBasedCreditPipeline

    model_path = os.environ.get("CROP_MODEL_PATH", "models/crop_classifier_model.joblib")
    ml_mode = os.environ.get("ML_MODE", "rule_based")
    use_mdb = os.environ.get("USE_MONGODB", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=model_path,
        ml_mode=ml_mode,
        verbose=os.environ.get("PIPELINE_VERBOSE", "").strip().lower()
        in ("1", "true", "yes"),
        use_mongodb=use_mdb,
    )
    logger.info("Pipeline ready (ml_mode=%s, mongodb=%s)", ml_mode, use_mdb)
    return pipeline


async def _ensure_pipeline() -> Any:
    """Lazy-init pipeline so Render can detect an open PORT before heavy imports finish."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    if _pipeline_init_lock is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    async with _pipeline_init_lock:
        if _pipeline is not None:
            return _pipeline
        loop = asyncio.get_event_loop()
        try:
            _pipeline = await loop.run_in_executor(None, _create_pipeline)
        except Exception as exc:
            logger.exception("Pipeline initialization failed")
            raise HTTPException(status_code=503, detail=f"Pipeline init failed: {exc}") from exc
        return _pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo_for_jobs, _jobs_col, _pipeline_job_lock, _pipeline_init_lock
    _pipeline_job_lock = asyncio.Lock()
    _pipeline_init_lock = asyncio.Lock()

    uri = os.environ.get("MONGODB_URI", "").strip()
    if uri:
        try:
            _mongo_for_jobs = MongoClient(uri, serverSelectionTimeoutMS=8000)
            _mongo_for_jobs.admin.command("ping")
            dbn = (
                os.environ.get("MONGODB_DATABASE")
                or os.environ.get("MONGODB_DB")
                or "agristack"
            )
            _jobs_col = _mongo_for_jobs[dbn]["jobs"]
            logger.info("Mongo job queue ready (%s.jobs)", dbn)
        except Exception as exc:
            logger.warning("Mongo job queue unavailable: %s", exc)
            _jobs_col = None
            _mongo_for_jobs = None
    else:
        _jobs_col = None

    yield

    global _pipeline
    _pipeline = None
    _jobs_col = None
    if _mongo_for_jobs is not None:
        _mongo_for_jobs.close()
        _mongo_for_jobs = None


app = FastAPI(
    title="Agri Credit Assessment API",
    description="Satellite-based credit pipeline: assess by farmer_id (farm record in MongoDB).",
    version="4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JobAssessRequest(BaseModel):
    """Dashboard job enqueue body (matches Next.js `/api/assess/enqueue`)."""

    farmer_id: str = Field(..., min_length=1)
    pm_kisan_enrolled: bool = False
    has_crop_insurance: bool = False


class AssessRequest(BaseModel):
    farmer_id: str = Field(..., min_length=1, description="Farmer / farm id in farm_info")
    include_heavy: bool = Field(
        False,
        description="If true, include trimmed satellite metadata only (never full scene arrays).",
    )
    pm_kisan_enrolled: bool = Field(
        False,
        description="Optional override for PM-KISAN enrollment.",
    )
    has_crop_insurance: bool = Field(
        False,
        description="Optional override for crop insurance enrollment.",
    )


def verify_service_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = _optional_api_key()
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health")
async def health() -> Dict[str, Any]:
    use_mdb = os.environ.get("USE_MONGODB", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return {
        "status": "ok",
        "pipeline_loaded": _pipeline is not None,
        "mongodb": use_mdb and _mongo_for_jobs is not None,
        "jobs_collection": _jobs_col is not None,
    }


async def _run_assessment(body: AssessRequest) -> Dict[str, Any]:
    pipeline = await _ensure_pipeline()

    fid = body.farmer_id.strip()
    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, Any]:
        return pipeline.assess_farmer_from_db(
            fid,
            farmer_benefits_override={
                "pm_kisan_enrolled": body.pm_kisan_enrolled,
                "has_crop_insurance": body.has_crop_insurance,
            },
        )

    try:
        result: Dict[str, Any] = await loop.run_in_executor(None, _run)
    except Exception as e:
        logger.exception("assess_farmer failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return slim_assessment_for_api(result, include_heavy=body.include_heavy)


async def _claim_and_run_job(job_id: str) -> None:
    """
    After HTTP returns, claim the QUEUED job and run the pipeline in a thread pool.
    Serialized with _pipeline_job_lock so only one heavy run uses the pipeline at a time.
    """
    global _jobs_col, _pipeline_job_lock
    if _jobs_col is None or _pipeline_job_lock is None:
        logger.error("Inline job %s skipped: Mongo jobs not ready", job_id)
        return

    try:
        pipeline = await _ensure_pipeline()
    except HTTPException:
        logger.exception("Inline job %s skipped: pipeline init failed", job_id)
        return

    try:
        job = _jobs_col.find_one_and_update(
            {"_id": ObjectId(job_id), "status": "QUEUED"},
            {
                "$set": {
                    "status": "RUNNING",
                    "started_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except Exception:
        logger.exception("Failed to claim job %s", job_id)
        return

    if not job:
        logger.info("Job %s not in QUEUED state (already processed or claimed)", job_id)
        return

    _env_classify = os.environ.get("ENABLE_CROP_CLASSIFICATION", "false").strip().lower()
    env_classification_enabled = _env_classify in ("1", "true", "yes")

    loop = asyncio.get_event_loop()
    async with _pipeline_job_lock:
        try:
            await loop.run_in_executor(
                None,
                partial(
                    process_assessment_job,
                    _jobs_col,
                    pipeline,
                    job,
                    env_classification_enabled,
                ),
            )
        except Exception as exc:
            logger.exception("Job %s executor failure: %s", job_id, exc)
            try:
                _jobs_col.update_one(
                    {"_id": ObjectId(job_id)},
                    {
                        "$set": {
                            "status": "FAILED",
                            "error": str(exc),
                            "completed_at": utc_now(),
                            "updated_at": utc_now(),
                        }
                    },
                )
            except Exception:
                logger.exception("Could not persist FAILED for job %s", job_id)


@app.post("/v1/jobs/assess")
async def enqueue_assess_job(
    body: JobAssessRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Enqueue a dashboard assessment job and process it inside this API process
    (no separate `worker.py`). Inserts `QUEUED` then runs the same pipeline as the worker.

    The Next.js server can call this when `PIPELINE_API_URL` is set instead of relying on
    a dedicated worker service.
    """
    if _jobs_col is None:
        raise HTTPException(
            status_code=503,
            detail="MongoDB jobs collection not available (set MONGODB_URI)",
        )
    now = datetime.now(timezone.utc)
    job_doc: Dict[str, Any] = {
        "farmer_id": body.farmer_id.strip(),
        "pm_kisan_enrolled": body.pm_kisan_enrolled,
        "has_crop_insurance": body.has_crop_insurance,
        "status": "QUEUED",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
    }
    result = _jobs_col.insert_one(job_doc)
    job_id = str(result.inserted_id)
    background_tasks.add_task(_claim_and_run_job, job_id)
    return {"success": True, "job_id": job_id, "status": "QUEUED"}


@app.post("/v1/assess")
async def assess_farmer_post(
    body: AssessRequest,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Run the full pipeline for a farmer_id present in MongoDB (farm_info).

    Typical runtime: several minutes (satellite + STAC). Ensure your host
    idle timeout (e.g. Render) is high enough or use a job queue for production.
    """
    return await _run_assessment(body)


@app.get("/v1/assess/{farmer_id}")
async def assess_farmer_get(
    farmer_id: str,
    include_heavy: bool = False,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    body = AssessRequest(farmer_id=farmer_id, include_heavy=include_heavy)
    return await _run_assessment(body)


# Alias for load balancers that probe /
@app.get("/")
async def root() -> Dict[str, str]:
    return {"service": "agri-credit-pipeline", "docs": "/docs"}
