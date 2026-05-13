"""
Worker for Agristack Credit Pipeline (MongoDB Job Queue)

This script continuously polls the MongoDB `jobs` collection for new assessment
requests enqueued by the Next.js API (`/api/assess/enqueue`).

Run it as a background process:
    python worker.py
"""

import time
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from bson.objectid import ObjectId

# Load .env from repo root
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

from main import SatelliteBasedCreditPipeline
from api.serialization import slim_assessment_for_api

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = os.environ.get("MONGODB_DATABASE") or os.environ.get("MONGODB_DB") or "agristack"
POLL_INTERVAL_SEC = 2.0


def _utc_now():
    return datetime.now(timezone.utc)


def get_mongo_collection():
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        raise ValueError("MONGODB_URI is not set.")
    client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    client.admin.command('ping')
    return client[DB_NAME]["jobs"]

def main():
    logger.info("Initializing Worker and ML Pipeline...")
    
    # Init MongoDB Jobs Collection
    try:
        jobs_col = get_mongo_collection()
        logger.info(f"Connected to MongoDB ({DB_NAME}.jobs)")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        return
        
    model_path = os.environ.get("CROP_MODEL_PATH", "models/crop_classifier_model.joblib")
    ml_mode = os.environ.get("ML_MODE", "rule_based")

    # Global env flag — set ENABLE_CROP_CLASSIFICATION=true to enable ML crop
    # classification for ALL jobs. Individual jobs can still override via payload.
    _env_classify = os.environ.get("ENABLE_CROP_CLASSIFICATION", "false").strip().lower()
    env_classification_enabled = _env_classify in ("1", "true", "yes")
    logger.info(
        "Crop classification default (env): %s",
        "ENABLED" if env_classification_enabled else "DISABLED",
    )
    
    # Pre-load pipeline (heavy operation, do once at startup)
    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=model_path,
        ml_mode=ml_mode,
        verbose=os.environ.get("PIPELINE_VERBOSE", "").strip().lower() in ("1", "true", "yes"),
        use_mongodb=True,
    )
    
    logger.info(f"Pipeline ready (ml_mode={ml_mode}). Listening for jobs...")

    while True:
        try:
            # Find and lock the next QUEUED job
            job = jobs_col.find_one_and_update(
                {"status": "QUEUED"},
                {
                    "$set": {
                        "status": "RUNNING",
                        "started_at": _utc_now(),
                        "updated_at": _utc_now()
                    }
                },
                return_document=True,
                sort=[("created_at", 1)]
            )
            
            if not job:
                time.sleep(POLL_INTERVAL_SEC)
                continue
                
            job_id = str(job["_id"])
            farmer_id = job["farmer_id"]
            logger.info(f"\n[JOB STARTED] Processing farmer '{farmer_id}' (Job {job_id})")
            
            try:
                # ── Determine classification flag for this specific job ────────────────
                # Priority: job payload > env default.
                # Set {"require_classification": true} in the MongoDB job document to
                # request ML crop classification for a single farmer assessment.
                job_classify = job.get("require_classification", False)
                enable_crop_classification = bool(job_classify) or env_classification_enabled
                if enable_crop_classification:
                    logger.info(
                        "[JOB %s] Crop classification ENABLED (job_flag=%s env_flag=%s)",
                        job_id, bool(job_classify), env_classification_enabled,
                    )
                else:
                    logger.info(
                        "[JOB %s] Crop classification DISABLED — "
                        "running in generic cycle-count mode.",
                        job_id,
                    )

                # 1. Run the heavy synchronous pipeline
                raw_result = pipeline.assess_farmer_from_db(
                    farmer_id=farmer_id,
                    farmer_benefits_override={
                        "pm_kisan_enrolled": job.get("pm_kisan_enrolled", False),
                        "has_crop_insurance": job.get("has_crop_insurance", False),
                    },
                    enable_crop_classification=enable_crop_classification,
                )
                
                # 2. Serialize for frontend consumption
                slim_result = slim_assessment_for_api(raw_result, include_heavy=False)

                # assess_farmer catches errors and returns dict with status FAILED (no exception)
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
                            "completed_at": _utc_now(),
                            "updated_at": _utc_now(),
                        }
                    },
                )
                if pipeline_ok:
                    logger.info(f"[JOB SUCCESS] Completed farmer '{farmer_id}'")
                else:
                    logger.warning(
                        f"[JOB FAILED] Farmer '{farmer_id}' — pipeline status={raw_result.get('status')!r}: {err_msg}"
                    )
            
            except Exception as e:
                logger.exception(f"[JOB FAILED] Error processing farmer '{farmer_id}'")
                jobs_col.update_one(
                    {"_id": job["_id"]},
                    {
                        "$set": {
                            "status": "FAILED",
                            "error": str(e),
                            "completed_at": _utc_now(),
                            "updated_at": _utc_now()
                        }
                    }
                )
                
        except (ConnectionFailure, OperationFailure) as e:
            logger.warning(f"MongoDB connection issue during polling: {e}. Retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected worker error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
