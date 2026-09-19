"""
Job orchestration for area-wide crop classification.

The Next.js route inserts the job row and hands the id here; this module owns
every write to it from that point on. Progress goes into the same document the
UI polls, so there is one source of truth for "what is this job doing" rather
than a status the API holds and a status the database holds.

Stage names are the ones `crop_analysis.area_classifier` emits and the ones the
frontend's `JobStage` union declares. They are not translated anywhere in
between -- a rename has to happen in all three places at once, which is the
point.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId

logger = logging.getLogger(__name__)

COLLECTION = "classification_jobs"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _oid(job_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(job_id)
    except Exception:                                        # noqa: BLE001
        return None


def process_classification_job(jobs_col: Any, job_id: str) -> None:
    """Run one job to completion, writing progress as it goes.

    Never raises: a background task that throws loses the error, and the row
    would sit at `queued` forever with nothing to show the user. Every failure
    path ends in a `failed` stage carrying a message.
    """
    from crop_analysis.area_classifier import ClassificationError, run_classification

    oid = _oid(job_id)
    if oid is None:
        logger.error("[classify] bad job id %r", job_id)
        return

    job = jobs_col.find_one({"_id": oid})
    if job is None:
        logger.error("[classify] job %s not found", job_id)
        return

    areas: List[Dict[str, Any]] = list(job.get("areas") or [])
    inputs: Dict[str, Any] = dict(job.get("inputs") or {})
    region = str(inputs.get("region_name") or "").strip()
    if region:
        inputs["region_name"] = region
        areas = [{**a, "name": region} if isinstance(a, dict) else a for a in areas]

    def progress(stage: str,
                 percent: Optional[float] = None,
                 message: Optional[str] = None,
                 checks: Optional[List[Dict[str, Any]]] = None) -> None:
        update: Dict[str, Any] = {"stage": stage, "updated_at": utc_now()}
        if percent is not None:
            update["percent"] = round(float(percent), 1)
        # An explicit None clears the previous stage's message rather than
        # leaving it stale under the new stage.
        update["message"] = message
        if checks is not None:
            update["checks"] = checks
        try:
            jobs_col.update_one({"_id": oid}, {"$set": update})
        except Exception:                                    # noqa: BLE001
            logger.exception("[classify] could not persist progress for %s", job_id)

    try:
        jobs_col.update_one(
            {"_id": oid},
            {"$set": {"stage": "validating", "percent": 0.0,
                      "started_at": utc_now(), "updated_at": utc_now(),
                      "error": None}},
        )
        result = run_classification(areas, inputs, progress)

        jobs_col.update_one(
            {"_id": oid},
            {"$set": {
                "stage": "complete",
                "percent": 100.0,
                "message": None,
                "result": result,
                "checks": result.get("validation_checks") or [],
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            }},
        )
        logger.info("[classify] job %s complete — %d fields", job_id,
                    result.get("field_count", 0))

    except ClassificationError as exc:
        # Expected, user-facing: a failed validation gate or an empty
        # segmentation. The message is written verbatim because it was authored
        # for the person who drew the area.
        logger.info("[classify] job %s rejected: %s", job_id, exc)
        jobs_col.update_one(
            {"_id": oid},
            {"$set": {"stage": "failed", "error": str(exc),
                      "finished_at": utc_now(), "updated_at": utc_now()}},
        )
    except Exception as exc:                                 # noqa: BLE001
        logger.exception("[classify] job %s crashed", job_id)
        jobs_col.update_one(
            {"_id": oid},
            {"$set": {
                "stage": "failed",
                "error": "Classification crashed: %s" % str(exc)[:300],
                "finished_at": utc_now(),
                "updated_at": utc_now(),
            }},
        )
