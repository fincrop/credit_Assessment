"""
FastAPI service: run the satellite credit pipeline by farmer_id (MongoDB-backed farms).

Run locally from backend/Credit_assessment:
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

# Load .env from package root (backend/Credit_assessment) before pipeline / config import
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

from api.job_runner import (
    last_reap_stats,
    process_assessment_job,
    reap_stuck_running_jobs,
    utc_now,
)
from api.report_payload import build_report_payload
from api.serialization import slim_assessment_for_api
from config import DEFAULT_CROP_MODEL_PATH, crop_classification_enabled, resolve_package_path

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

    model_path = str(
        resolve_package_path(
            os.environ.get("CROP_MODEL_PATH", DEFAULT_CROP_MODEL_PATH)
        )
    )
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

    # Warm GEE/Mongo pipeline in the background so the first Assess is not a cold start.
    # Port stays open for health checks (Render) while this runs.
    async def _warm_pipeline() -> None:
        try:
            await _ensure_pipeline()
            logger.info("Pipeline warm-up complete")
        except Exception:
            logger.exception("Pipeline warm-up failed (will retry on first job)")

    warm_task = asyncio.create_task(_warm_pipeline())

    yield

    warm_task.cancel()
    try:
        await warm_task
    except asyncio.CancelledError:
        pass

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
    pm_kisan_enrolled: Optional[bool] = None
    has_crop_insurance: Optional[bool] = None
    force_fresh_satellite: bool = False


class JobClassifyRequest(BaseModel):
    """Area-classification enqueue body (matches Next.js `/api/classification/enqueue`).

    `job_id` is supplied by the caller: the Next.js route inserts the row so the
    browser has an id to poll before this service is even reached, and a second
    insert here would orphan the first.
    """

    job_id: str = Field(..., min_length=1)
    areas: List[Dict[str, Any]] = Field(..., min_length=1)
    inputs: Dict[str, Any] = Field(default_factory=dict)


class MonitorRequestBody(BaseModel):
    """One classified parcel. Geometry is GeoJSON (Polygon or MultiPolygon)."""

    geometry: Dict[str, Any]
    crop: str = Field(..., min_length=1)
    confidence: float = Field(0.8, ge=0.0, le=1.0)
    season: Optional[str] = None
    ecoregion: Optional[str] = None
    as_of: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    sowing_date: Optional[str] = None
    sowing_source: str = "provided"
    district_yield_t_ha: Optional[float] = None
    peer_integrals: Optional[List[float]] = None


class AssessRequest(BaseModel):
    farmer_id: str = Field(..., min_length=1, description="Farmer / farm id in farm_info")
    include_heavy: bool = Field(
        False,
        description="If true, include trimmed satellite metadata only (never full scene arrays).",
    )
    pm_kisan_enrolled: Optional[bool] = Field(
        None,
        description="Optional override for PM-KISAN enrollment (None = unknown).",
    )
    has_crop_insurance: Optional[bool] = Field(
        None,
        description="Optional override for crop insurance (None = unknown).",
    )


def _benefits_override_from_body(body: Any) -> Optional[Dict[str, Any]]:
    """Only include benefit keys that are explicitly True/False (not None)."""
    out: Dict[str, Any] = {}
    if getattr(body, "pm_kisan_enrolled", None) is not None:
        out["pm_kisan_enrolled"] = body.pm_kisan_enrolled
    if getattr(body, "has_crop_insurance", None) is not None:
        out["has_crop_insurance"] = body.has_crop_insurance
    return out or None


def _classification_jobs_col() -> Optional[Any]:
    """The `classification_jobs` collection, or None when Mongo is down.

    Separate from `_jobs_col` ("jobs"): classification jobs have their own
    stage vocabulary and ownership rule, and sharing a collection would make
    every reader branch on a discriminator.
    """
    if _mongo_for_jobs is None:
        return None
    dbn = (
        os.environ.get("MONGODB_DATABASE")
        or os.environ.get("MONGODB_DB")
        or "agristack"
    )
    return _mongo_for_jobs[dbn]["classification_jobs"]


def _monitoring_jobs_col() -> Optional[Any]:
    """Present-season monitoring jobs. Separate from classification and credit jobs."""
    if _mongo_for_jobs is None:
        return None
    dbn = (
        os.environ.get("MONGODB_DATABASE")
        or os.environ.get("MONGODB_DB")
        or "agristack"
    )
    return _mongo_for_jobs[dbn]["monitoring_jobs"]


class JobMonitorRequest(BaseModel):
    job_id: str = Field(..., min_length=1)
    areas: List[Dict[str, Any]] = Field(default_factory=list)
    inputs: Dict[str, Any] = Field(default_factory=dict)


def verify_service_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = _optional_api_key()
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health/live")
async def health_live() -> Dict[str, str]:
    """Fast liveness probe — no Mongo or pipeline work (use before enqueue)."""
    return {"status": "ok"}


@app.get("/health")
async def health() -> Dict[str, Any]:
    use_mdb = os.environ.get("USE_MONGODB", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    reaped = 0
    if _jobs_col is not None:
        try:
            loop = asyncio.get_event_loop()
            reaped = await asyncio.wait_for(
                loop.run_in_executor(None, reap_stuck_running_jobs, _jobs_col),
                timeout=2.0,
            )
        except Exception as exc:
            logger.debug("health reaper skipped: %s", exc)
    return {
        "status": "ok",
        "pipeline_loaded": _pipeline is not None,
        "mongodb": use_mdb and _mongo_for_jobs is not None,
        "jobs_collection": _jobs_col is not None,
        "stuck_running_reaped": reaped,
    }


@app.get("/v1/jobs/health")
async def jobs_health(
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Queue depth, oldest queued age, and stuck RUNNING reaper pass.
    """
    mongodb_ok = False
    queued_count = 0
    running_count = 0
    oldest_queued_age_seconds: Optional[float] = None

    if _mongo_for_jobs is not None:
        try:
            _mongo_for_jobs.admin.command("ping")
            mongodb_ok = True
        except Exception:
            mongodb_ok = False

    if _jobs_col is not None and mongodb_ok:
        try:
            reap_stuck_running_jobs(_jobs_col)
            queued_count = int(_jobs_col.count_documents({"status": "QUEUED"}))
            running_count = int(_jobs_col.count_documents({"status": "RUNNING"}))
            oldest = _jobs_col.find_one(
                {"status": "QUEUED"},
                sort=[("created_at", 1)],
            )
            if oldest and oldest.get("created_at") is not None:
                created = oldest["created_at"]
                if getattr(created, "tzinfo", None) is None:
                    created = created.replace(tzinfo=timezone.utc)
                oldest_queued_age_seconds = max(
                    0.0, (utc_now() - created).total_seconds()
                )
        except Exception as exc:
            logger.warning("jobs_health query failed: %s", exc)
            mongodb_ok = False

    reap_meta = last_reap_stats()
    return {
        "queued_count": queued_count,
        "running_count": running_count,
        "oldest_queued_age_seconds": oldest_queued_age_seconds,
        "stuck_running_reaped": int(reap_meta.get("stuck_running_reaped") or 0),
        "reaped_at": reap_meta.get("reaped_at"),
        "mongodb": mongodb_ok,
    }


async def _run_assessment(body: AssessRequest) -> Dict[str, Any]:
    pipeline = await _ensure_pipeline()

    fid = body.farmer_id.strip()
    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, Any]:
        return pipeline.assess_farmer_from_db(
            fid,
            farmer_benefits_override=_benefits_override_from_body(body),
        )

    try:
        result: Dict[str, Any] = await loop.run_in_executor(None, _run)
    except Exception as e:
        logger.exception("assess_farmer failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return slim_assessment_for_api(result, include_heavy=body.include_heavy)


def _seed_job_progress_early(job_id: str, farmer_id: str) -> None:
    """
    Write plot keys into job.progress as soon as the job is RUNNING so the
    dashboard can flip the first farm to Analyzing before GEE starts.
    """
    if _jobs_col is None or _mongo_for_jobs is None:
        return
    try:
        from assessment.multi_farm_assessor import assign_plot_keys

        dbn = (
            os.environ.get("MONGODB_DATABASE")
            or os.environ.get("MONGODB_DB")
            or "agristack"
        )
        farm_info = _mongo_for_jobs[dbn]["farm_info"].find_one({"farmer_id": farmer_id})
        farms = list((farm_info or {}).get("farms") or [])
        if not farms:
            _jobs_col.update_one(
                {"_id": ObjectId(job_id)},
                {
                    "$set": {
                        "progress": {
                            "current_stage": "starting",
                            "n_plots_total": 0,
                            "n_plots_done": 0,
                        },
                        "updated_at": utc_now(),
                    }
                },
            )
            return
        keyed = assign_plot_keys(farms)
        # Only count plots that will actually be attempted (included).
        included = [
            f
            for f in keyed
            if f.get("included_in_assessment", True) is not False
        ]
        n = len(included) or len(keyed)
        keys = [f.get("plot_key") for f in (included or keyed)]
        _jobs_col.update_one(
            {"_id": ObjectId(job_id)},
            {
                "$set": {
                    "progress": {
                        "current_stage": f"plot 0/{n}",
                        "n_plots_total": n,
                        "n_plots_done": 0,
                        "n_plots_scored": 0,
                        "n_plots_skipped": 0,
                        "n_plots_failed": 0,
                        "pending_plot_keys": keys,
                        "partial_result": {
                            "farmer_id": farmer_id,
                            "farm_assessments": [],
                            "n_plots_total": n,
                            "n_plots_done": 0,
                            "n_plots_scored": 0,
                            "n_plots_skipped": 0,
                            "n_plots_failed": 0,
                        },
                    },
                    "updated_at": utc_now(),
                }
            },
        )
    except Exception as exc:
        logger.debug("Early progress seed skipped: %s", exc)


async def _claim_and_run_job(job_id: str) -> None:
    """
    After HTTP returns, claim the QUEUED job and run the pipeline in a thread pool.
    Claim RUNNING immediately (before pipeline warm) so the UI leaves the pending lag.
    Serialized with _pipeline_job_lock so only one heavy run uses the pipeline at a time.
    """
    global _jobs_col, _pipeline_job_lock
    if _jobs_col is None or _pipeline_job_lock is None:
        logger.error("Inline job %s skipped: Mongo jobs not ready", job_id)
        return

    try:
        job = _jobs_col.find_one_and_update(
            {"_id": ObjectId(job_id), "status": "QUEUED"},
            {
                "$set": {
                    "status": "RUNNING",
                    "started_at": utc_now(),
                    "updated_at": utc_now(),
                    "progress": {
                        "current_stage": "starting",
                        "n_plots_done": 0,
                    },
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

    farmer_id = str(job.get("farmer_id") or "")
    _seed_job_progress_early(job_id, farmer_id)

    try:
        pipeline = await _ensure_pipeline()
    except HTTPException as exc:
        logger.exception("Inline job %s skipped: pipeline init failed", job_id)
        try:
            _jobs_col.update_one(
                {"_id": ObjectId(job_id)},
                {
                    "$set": {
                        "status": "FAILED",
                        "error": f"Pipeline init failed: {exc.detail}",
                        "completed_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                },
            )
        except Exception:
            logger.exception("Could not persist FAILED for job %s", job_id)
        return

    # Refresh job doc after early progress write
    try:
        refreshed = _jobs_col.find_one({"_id": ObjectId(job_id)})
        if refreshed:
            job = refreshed
    except Exception:
        pass

    _env_classify = crop_classification_enabled()
    env_classification_enabled = _env_classify

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
        "force_fresh_satellite": bool(body.force_fresh_satellite),
        "status": "QUEUED",
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
    }
    # Tri-state: only persist benefit flags when explicitly True/False (never None→False).
    if body.pm_kisan_enrolled is not None:
        job_doc["pm_kisan_enrolled"] = body.pm_kisan_enrolled
    if body.has_crop_insurance is not None:
        job_doc["has_crop_insurance"] = body.has_crop_insurance
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


@app.get("/v1/report/{farmer_id}")
async def farmer_report(
    farmer_id: str,
    plot_key: Optional[str] = None,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Report data for a farmer's most recent assessment.

    Reads only — it never triggers a run, so a report can never be a different
    number from the assessment it claims to describe.

    Returns the assembled contract plus a `sections_present` map, so a renderer
    can tell "we have no data for this yet" apart from "this pipeline does not
    produce that". Panels in the supplied design with nothing real behind them
    (suggested action, district median, reviewer) are listed under `omitted`
    with the reason.

    Farmer identity is NOT included: the masking policy is unsettled, so the
    caller supplies it from whatever it is already authorised to display.
    """
    def _read() -> Dict[str, Any]:
        from mongodb_helper import MongoDBHelper

        db = MongoDBHelper()
        try:
            assessment = db.get_latest_assessment(farmer_id)
            if not assessment:
                return {}
            return {
                "assessment": assessment,
                "evidence": db.get_latest_evidence(farmer_id, plot_key),
                "score_history": db.get_score_history(farmer_id, plot_key),
            }
        finally:
            db.close()

    try:
        bundle = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.exception("report read failed for %s", farmer_id)
        raise HTTPException(status_code=503, detail=f"report unavailable: {exc}")

    if not bundle:
        raise HTTPException(
            status_code=404,
            detail=f"No assessment stored for farmer_id={farmer_id}",
        )

    payload = build_report_payload(
        bundle["assessment"],
        evidence=bundle.get("evidence"),
        score_history=bundle.get("score_history"),
    )
    payload["sections_present"] = {
        "score": payload["score"]["kbs"] is not None,
        "trend": payload["trend"] is not None,
        "sub_indices": bool(payload["sub_indices"]),
        "ndvi_trajectory": payload["ndvi_trajectory"] is not None,
        "land_cover": payload["land_cover"] is not None,
        "crop_verification": payload["crop_verification"] is not None,
        "narrative": bool(payload["narrative"]["text"]),
    }
    return {"success": True, "report": payload}


@app.post("/v1/jobs/classify")
async def enqueue_classify_job(
    body: JobClassifyRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Start an area-wide crop classification for a job row Next.js already wrote.

    Runs in this process via BackgroundTasks, the same shape as
    /v1/jobs/assess: a classification is minutes of GEE round-trips, so the
    request returns immediately and the browser polls the job document.
    """
    if _mongo_for_jobs is None:
        raise HTTPException(
            status_code=503,
            detail="MongoDB not available (set MONGODB_URI)",
        )
    col = _classification_jobs_col()
    if col is None:
        raise HTTPException(status_code=503, detail="classification_jobs collection unavailable")

    from api.classification_runner import process_classification_job

    # Areas and inputs come from the caller rather than the stored row so a
    # retry can adjust them without another insert; the row is the record of
    # what ran, so it is updated to match.
    try:
        col.update_one(
            {"_id": ObjectId(body.job_id)},
            {"$set": {"areas": body.areas, "inputs": body.inputs,
                      "stage": "queued", "error": None,
                      "updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from exc

    background_tasks.add_task(process_classification_job, col, body.job_id)
    return {"success": True, "job_id": body.job_id, "stage": "queued"}


@app.get("/v1/jobs/classify/{job_id}")
async def get_classify_job(
    job_id: str,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """Job state. Next.js reads Mongo directly for the UI poll; this exists for
    service-to-service checks and for debugging a stuck run."""
    col = _classification_jobs_col()
    if col is None:
        raise HTTPException(status_code=503, detail="classification_jobs collection unavailable")
    try:
        doc = col.find_one({"_id": ObjectId(job_id)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "stage": doc.get("stage"),
        "percent": doc.get("percent"),
        "message": doc.get("message"),
        "checks": doc.get("checks") or [],
        "error": doc.get("error"),
        "has_result": doc.get("result") is not None,
    }


@app.get("/v1/jobs/classify/{job_id}/download")
async def download_classify_product(
    job_id: str,
    format: str = "geojson",
    _: None = Depends(verify_service_key),
) -> Any:
    """
    Render a completed classification into a downloadable product.

    GeoJSON and CSV are served here for completeness, but the browser builds
    both from the result it already holds -- see the frontend DownloadPanel --
    so the common case does not depend on this service being up.
    """
    from fastapi.responses import JSONResponse, Response

    col = _classification_jobs_col()
    if col is None:
        raise HTTPException(status_code=503, detail="classification_jobs collection unavailable")
    try:
        doc = col.find_one({"_id": ObjectId(job_id)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if doc.get("stage") != "complete" or not doc.get("result"):
        raise HTTPException(status_code=409, detail="Job is not complete")

    result = dict(doc["result"])
    region = str((doc.get("inputs") or {}).get("region_name") or "").strip()
    if region:
        result["aoi_name"] = region
    fmt = (format or "geojson").lower()

    if fmt == "geojson":
        fields = dict(result.get("fields") or {})
        if region:
            fields["name"] = region
        return JSONResponse(fields)

    if fmt == "csv":
        from api.classification_export import result_to_csv
        return Response(
            content=result_to_csv(result),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
        )

    if fmt == "shapefile":
        from api.classification_export import result_to_shapefile_zip
        try:
            blob = result_to_shapefile_zip(result)
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail=f"Shapefile export needs geopandas on the service: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=blob,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'},
        )

    if fmt == "geotiff":
        # Burned from the field polygons at 10 m — this pipeline never keeps a
        # per-pixel model raster, so the classified raster *is* the vector layer
        # sampled onto the Sentinel-2 grid. See classification_export.
        from api.classification_export import result_to_geotiff
        try:
            blob = result_to_geotiff(result)
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail=f"GeoTIFF export needs rasterio on the service: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=blob,
            media_type="image/tiff",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.tif"'},
        )

    if fmt == "png":
        from api.classification_export import result_to_png
        try:
            blob = result_to_png(result)
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail=f"PNG export needs Pillow on the service: {exc}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=blob,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.png"'},
        )

    raise HTTPException(status_code=400, detail=f"Unknown format '{format}'")


@app.post("/v1/jobs/monitor")
async def enqueue_monitor_job(
    body: JobMonitorRequest,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """Start present-season monitoring for a job row Next.js already wrote."""
    if _mongo_for_jobs is None:
        raise HTTPException(status_code=503, detail="MongoDB not available (set MONGODB_URI)")
    col = _monitoring_jobs_col()
    if col is None:
        raise HTTPException(status_code=503, detail="monitoring_jobs collection unavailable")

    from api.monitoring_runner import process_monitoring_job

    try:
        col.update_one(
            {"_id": ObjectId(body.job_id)},
            {"$set": {
                "areas": body.areas,
                "inputs": body.inputs,
                "stage": "queued",
                "error": None,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from exc

    background_tasks.add_task(process_monitoring_job, col, body.job_id)
    return {"success": True, "job_id": body.job_id, "stage": "queued"}


@app.get("/v1/jobs/monitor/{job_id}/download")
async def download_monitor_product(
    job_id: str,
    format: str = "png",
    _: None = Depends(verify_service_key),
) -> Any:
    """Shapefile, GeoTIFF, PNG, or the analytical CSV for a finished monitoring job."""
    from fastapi.responses import Response

    col = _monitoring_jobs_col()
    if col is None:
        raise HTTPException(status_code=503, detail="monitoring_jobs collection unavailable")
    try:
        doc = col.find_one({"_id": ObjectId(job_id)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid job_id: {exc}") from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if doc.get("stage") != "complete" or not doc.get("result"):
        raise HTTPException(status_code=409, detail="Job is not complete")

    result = doc["result"]
    fmt = (format or "").lower()
    try:
        if fmt == "csv":
            from api.monitoring_export import monitoring_csv
            body = monitoring_csv(result).encode("utf-8")
            media, filename = "text/csv", f"{job_id}.csv"
        elif fmt in ("shapefile", "shp", "zip"):
            from api.monitoring_export import monitoring_shapefile_zip
            body = monitoring_shapefile_zip(result)
            media, filename = "application/zip", f"{job_id}.zip"
        elif fmt in ("geotiff", "tif", "tiff"):
            from api.monitoring_export import monitoring_geotiff
            body = monitoring_geotiff(result)
            media, filename = "image/tiff", f"{job_id}.tif"
        elif fmt == "png":
            from api.monitoring_export import monitoring_png
            body = monitoring_png(result)
            media, filename = "image/png", f"{job_id}.png"
        else:
            raise HTTPException(status_code=400, detail=f"Unknown format '{format}'")
    except ImportError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_monitor_day(text: Optional[str]):
    if not text:
        return None
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


@app.post("/v1/monitor")
def run_monitor(
    body: MonitorRequestBody,
    _: None = Depends(verify_service_key),
) -> Dict[str, Any]:
    """
    Present-season monitoring for one classified field.

    The browser (or a worker) sends the parcel, the crop, and the confidence.
    The pipeline pulls Sentinel-2, Sentinel-1, Landsat, and buffer weather from
    Earth Engine and returns the farm document. This call blocks for the
    satellite round-trip; it does not write a job row.
    """
    import sys

    root = Path(__file__).resolve().parents[3]
    package = root / "Crop_Monitoring"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))
    try:
        from src.models import MonitorRequest
        from src.observe import MonitorFetchError
        from src.pipeline import run_monitoring
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"monitoring package unavailable: {exc}") from exc

    geometry = body.geometry
    if geometry.get("type") == "Feature":
        geometry = geometry.get("geometry") or geometry
    elif geometry.get("type") == "FeatureCollection":
        features = geometry.get("features") or []
        if not features:
            raise HTTPException(status_code=400, detail="FeatureCollection has no features")
        geometry = features[0].get("geometry")

    request = MonitorRequest(
        crop=body.crop,
        geometry=geometry,
        confidence=body.confidence,
        season=body.season,
        ecoregion=body.ecoregion,
        as_of=_parse_monitor_day(body.as_of),
        start=_parse_monitor_day(body.start),
        end=_parse_monitor_day(body.end),
        sowing_hint=_parse_monitor_day(body.sowing_date),
        sowing_hint_source=body.sowing_source or "provided",
        district_yield_t_ha=body.district_yield_t_ha,
        peer_integrals=body.peer_integrals,
    )
    try:
        document = run_monitoring(request)
    except MonitorFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "monitor": document}


# Alias for load balancers that probe /
@app.get("/")
async def root() -> Dict[str, str]:
    return {"service": "agri-credit-pipeline", "docs": "/docs"}
