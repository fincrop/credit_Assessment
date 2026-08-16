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

        raw_result: Dict[str, Any]
        use_multi = False
        try:
            from config import PipelineConfig
            from assessment.multi_farm_assessor import (
                MultiFarmAssessor,
                assign_plot_keys,
                counters_from_farm_assessments,
                persist_plot_keys,
            )

            multi_on = bool(getattr(PipelineConfig, "MULTI_FARM_ENABLED", True))
            mongo = getattr(pipeline, "db", None)
            farm_info = None
            if multi_on and mongo is not None and hasattr(mongo, "get_farm_by_id"):
                farm_info = mongo.get_farm_by_id(farmer_id)
            if multi_on and isinstance(farm_info, dict) and (farm_info.get("farms") or []):
                use_multi = True
                farm_copy = dict(farm_info)
                if benefits_override:
                    fb = dict(farm_copy.get("farmer_benefits") or {})
                    for k, v in benefits_override.items():
                        fb[k] = v  # only keys actually specified (tri-state safe)
                    farm_copy["farmer_benefits"] = fb
                max_plots = int(getattr(PipelineConfig, "MULTI_FARM_MAX_PLOTS", 12) or 12)
                keyed_farms = assign_plot_keys(list(farm_copy.get("farms") or []))
                farm_copy["farms"] = keyed_farms
                # Make the keys durable so per-plot history stays joinable
                # across runs even if a re-ingest reorders farms[].
                persist_plot_keys(mongo, farmer_id, keyed_farms)
                n_expected = len(keyed_farms)
                logger.info(
                    "[JOB %s] Multi-farm path (%d plots, cap=%d) — sequential stream",
                    job_id,
                    n_expected,
                    max_plots,
                )

                # Seed pending plot keys (no farmer_level — A1)
                try:
                    jobs_col.update_one(
                        {"_id": job["_id"]},
                        {
                            "$set": {
                                "progress": {
                                    "current_stage": f"plot 0/{n_expected}",
                                    "n_plots_total": n_expected,
                                    "n_plots_done": 0,
                                    "n_plots_scored": 0,
                                    "n_plots_skipped": 0,
                                    "n_plots_failed": 0,
                                    "pending_plot_keys": [f.get("plot_key") for f in keyed_farms],
                                    "partial_result": {
                                        "farmer_id": farmer_id,
                                        "farm_assessments": [],
                                        "n_plots_total": n_expected,
                                        "n_plots_scored": 0,
                                        "n_plots_skipped": 0,
                                        "n_plots_failed": 0,
                                        "n_plots_done": 0,
                                    },
                                },
                                "updated_at": utc_now(),
                            }
                        },
                    )
                except Exception as e:
                    logger.debug("Could not seed job progress: %s", e)

                def _on_plot_done(rows: list) -> None:
                    """A2: one $set of whole partial_result per plot-done; never farmer_level."""
                    counts = counters_from_farm_assessments(rows)
                    jobs_col.update_one(
                        {"_id": job["_id"]},
                        {
                            "$set": {
                                "progress": {
                                    "current_stage": (
                                        f"plot {counts['n_plots_done']}/{n_expected}"
                                    ),
                                    "n_plots_total": n_expected,
                                    "n_plots_done": counts["n_plots_done"],
                                    "n_plots_scored": counts["n_plots_scored"],
                                    "n_plots_skipped": counts["n_plots_skipped"],
                                    "n_plots_failed": counts["n_plots_failed"],
                                    "pending_plot_keys": [
                                        f.get("plot_key") for f in keyed_farms
                                    ],
                                    "partial_result": {
                                        "farmer_id": farmer_id,
                                        "farm_assessments": rows,
                                        **counts,
                                    },
                                },
                                "updated_at": utc_now(),
                            }
                        },
                    )

                raw_result = MultiFarmAssessor(
                    pipeline, max_plots=max_plots
                ).assess_farmer_multi(
                    farm_copy, save_to_db=True, on_plot_done=_on_plot_done
                )
            else:
                raw_result = pipeline.assess_farmer_from_db(
                    farmer_id=farmer_id,
                    farmer_benefits_override=(benefits_override or None),
                    enable_crop_classification=enable_crop_classification,
                    force_fresh_satellite=force_fresh_satellite,
                )
        except Exception:
            if use_multi:
                raise
            raw_result = pipeline.assess_farmer_from_db(
                farmer_id=farmer_id,
                farmer_benefits_override=(benefits_override or None),
                enable_crop_classification=enable_crop_classification,
                force_fresh_satellite=force_fresh_satellite,
            )

        # Progress from pipeline_stages before final status write
        _set_job_progress(jobs_col, job["_id"], raw_result)

        slim_result = slim_assessment_for_api(raw_result, include_heavy=False)

        raw_status = str(raw_result.get("status", "")).upper()
        pipeline_ok = raw_status == "SUCCESS"

        # A rejection is NOT a failure. Nothing went wrong — we declined to
        # score land that is not agricultural. Collapsing it into FAILED (as
        # this branch previously did for every non-SUCCESS status) makes
        # "we refuse to score a lake" indistinguishable from "Earth Engine
        # timed out", so the UI cannot explain either one.
        rejected = raw_status == "REJECTED_NOT_AGRICULTURAL"

        # Nor is "we could not see it well enough to say". Three distinct
        # outcomes, three distinct statuses — collapsing them loses exactly the
        # information a loan officer needs to act.
        insufficient = raw_status == "INSUFFICIENT_DATA"

        if pipeline_ok:
            job_status, err_msg = "SUCCESS", None
            warns = raw_result.get("warnings") or []
            if warns:
                # Surface partial plot failures without failing the job
                slim_result.setdefault("warnings", warns)
        elif rejected:
            job_status = "REJECTED_NOT_AGRICULTURAL"
            err_msg = None
            slim_result.setdefault("land_cover", raw_result.get("land_cover"))
            slim_result.setdefault(
                "rejection_reason", raw_result.get("rejection_reason")
            )
            slim_result.setdefault(
                "rejection_class", raw_result.get("rejection_class")
            )
            logger.info(
                "[JOB %s] Rejected as non-agricultural: %s",
                job_id, raw_result.get("rejection_reason"),
            )
        elif insufficient:
            job_status = "INSUFFICIENT_DATA"
            err_msg = None
            slim_result.setdefault(
                "data_sufficiency", raw_result.get("data_sufficiency")
            )
            slim_result.setdefault(
                "insufficient_reason", raw_result.get("insufficient_reason")
            )
            logger.info(
                "[JOB %s] Insufficient observation: %s",
                job_id, raw_result.get("insufficient_reason"),
            )
        else:
            job_status = "FAILED"
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
