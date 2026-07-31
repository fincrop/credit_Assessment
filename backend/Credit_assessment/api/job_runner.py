"""
Shared MongoDB `jobs` assessment execution (used by `worker.py` and FastAPI inline runner).

Keeps one implementation of: run pipeline → slim JSON → update job document.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from api.serialization import slim_assessment_for_api

logger = logging.getLogger(__name__)

# Last reaper pass stats (surfaced by GET /v1/jobs/health)
_LAST_REAP: Dict[str, Any] = {
    "stuck_running_reaped": 0,
    "reaped_at": None,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def last_reap_stats() -> Dict[str, Any]:
    return dict(_LAST_REAP)


def reap_stuck_running_jobs(
    jobs_col: Any,
    timeout_minutes: Optional[int] = None,
) -> int:
    """
    Mark RUNNING jobs whose started_at is older than timeout as FAILED.

    Returns the number of documents modified.
    """
    if jobs_col is None:
        return 0

    if timeout_minutes is None:
        try:
            from config import PipelineConfig

            timeout_minutes = int(
                getattr(PipelineConfig, "JOB_RUNNING_TIMEOUT_MINUTES", 45) or 45
            )
        except Exception:
            timeout_minutes = 45

    timeout_minutes = max(1, int(timeout_minutes))
    cutoff = utc_now() - timedelta(minutes=timeout_minutes)

    try:
        result = jobs_col.update_many(
            {
                "status": "RUNNING",
                "started_at": {"$lt": cutoff},
            },
            {
                "$set": {
                    "status": "FAILED",
                    "error": "timed_out_reaped",
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
        )
        n = int(getattr(result, "modified_count", 0) or 0)
    except Exception as e:
        logger.warning("reap_stuck_running_jobs failed: %s", e)
        n = 0

    _LAST_REAP["stuck_running_reaped"] = n
    _LAST_REAP["reaped_at"] = utc_now().isoformat()
    if n:
        logger.warning(
            "Reaped %d stuck RUNNING job(s) older than %d minutes",
            n,
            timeout_minutes,
        )
    return n


def _set_job_progress(jobs_col: Any, job_id: Any, assessment: Dict[str, Any]) -> None:
    stages = assessment.get("pipeline_stages") or []
    if not isinstance(stages, list):
        stages = []
    current = stages[-1] if stages else None
    try:
        jobs_col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "progress": {
                        "pipeline_stages": stages,
                        "current_stage": current,
                    },
                    "updated_at": utc_now(),
                }
            },
        )
    except Exception as e:
        logger.debug("Could not set job progress: %s", e)


def process_assessment_job(
    jobs_col: Any,
    pipeline: Any,
    job: Dict[str, Any],
    env_classification_enabled: bool,
) -> None:
    """
    Run the pipeline for a job document that is already marked RUNNING in MongoDB.

    Updates the same document with SUCCESS / FAILED, result, error, timestamps.
    """
    job_id = str(job["_id"])
    farmer_id = job["farmer_id"]
    logger.info("[JOB STARTED] Processing farmer '%s' (Job %s)", farmer_id, job_id)

    try:
        job_classify = job.get("require_classification", False)
        enable_crop_classification = bool(job_classify) or env_classification_enabled
        if enable_crop_classification:
            logger.info(
                "[JOB %s] Crop classification ENABLED (job_flag=%s env_flag=%s)",
                job_id,
                bool(job_classify),
                env_classification_enabled,
            )
        else:
            logger.info(
                "[JOB %s] Crop classification DISABLED — running in generic cycle-count mode.",
                job_id,
            )

        force_fresh_satellite = bool(job.get("force_fresh_satellite", False))
        # Tri-state benefits: only pass a flag the job ACTUALLY specifies. Absent
        # or None stays unknown instead of being coerced to False.
        benefits_override: Dict[str, Any] = {}
        if "pm_kisan_enrolled" in job and job.get("pm_kisan_enrolled") is not None:
            benefits_override["pm_kisan_enrolled"] = job.get("pm_kisan_enrolled")
        if "has_crop_insurance" in job and job.get("has_crop_insurance") is not None:
            benefits_override["has_crop_insurance"] = job.get("has_crop_insurance")
        raw_result = pipeline.assess_farmer_from_db(
            farmer_id=farmer_id,
            farmer_benefits_override=(benefits_override or None),
            enable_crop_classification=enable_crop_classification,
            force_fresh_satellite=force_fresh_satellite,
        )

        # Progress from pipeline_stages before final status write
        _set_job_progress(jobs_col, job["_id"], raw_result)

        slim_result = slim_assessment_for_api(raw_result, include_heavy=False)

        pipeline_ok = str(raw_result.get("status", "")).upper() == "SUCCESS"
        job_status = "SUCCESS" if pipeline_ok else "FAILED"
        if pipeline_ok:
            err_msg = None
        else:
            err_msg = raw_result.get("error")
            errs = raw_result.get("errors")
            if not err_msg and isinstance(errs, list) and errs:
                err_msg = str(errs[0])
            if not err_msg:
                err_msg = "Pipeline returned non-success status"

        stages = raw_result.get("pipeline_stages") or []
        if not isinstance(stages, list):
            stages = []
        jobs_col.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "status": job_status,
                    "result": slim_result,
                    "error": err_msg,
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                    "progress": {
                        "pipeline_stages": stages,
                        "current_stage": stages[-1] if stages else None,
                    },
                }
            },
        )
        if pipeline_ok:
            logger.info("[JOB SUCCESS] Completed farmer '%s'", farmer_id)
        else:
            logger.warning(
                "[JOB FAILED] Farmer '%s' — pipeline status=%r: %s",
                farmer_id,
                raw_result.get("status"),
                err_msg,
            )

    except Exception as e:
        logger.exception("[JOB FAILED] Error processing farmer '%s'", farmer_id)
        jobs_col.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "status": "FAILED",
                    "error": str(e),
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
                }
            },
        )
