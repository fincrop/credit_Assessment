"""
Shared MongoDB `jobs` assessment execution (used by `worker.py` and FastAPI inline runner).

Keeps one implementation of: run pipeline → slim JSON → update job document.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from api.serialization import slim_assessment_for_api

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

        raw_result = pipeline.assess_farmer_from_db(
            farmer_id=farmer_id,
            farmer_benefits_override={
                "pm_kisan_enrolled": job.get("pm_kisan_enrolled", False),
                "has_crop_insurance": job.get("has_crop_insurance", False),
            },
            enable_crop_classification=enable_crop_classification,
        )

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

        jobs_col.update_one(
            {"_id": job["_id"]},
            {
                "$set": {
                    "status": job_status,
                    "result": slim_result,
                    "error": err_msg,
                    "completed_at": utc_now(),
                    "updated_at": utc_now(),
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
