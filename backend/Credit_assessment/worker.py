"""
Worker for Agristack Credit Pipeline (MongoDB Job Queue)

This script continuously polls the MongoDB `jobs` collection for new assessment
requests enqueued by the Next.js API (`/api/assess/enqueue`).

Run from backend/Credit_assessment:
    python worker.py
"""

import time
import os
import logging
from pathlib import Path

# Load .env from package root (backend/Credit_assessment)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

from config import DEFAULT_CROP_MODEL_PATH, crop_classification_enabled, resolve_package_path
from main import SatelliteBasedCreditPipeline
from api.job_runner import (
    process_assessment_job,
    reap_stuck_running_jobs,
    utc_now as _utc_now,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = os.environ.get("MONGODB_DATABASE") or os.environ.get("MONGODB_DB") or "agristack"
POLL_INTERVAL_SEC = 2.0
# Reap stuck RUNNING jobs about once per minute (every N empty/busy poll cycles)
REAP_EVERY_N_POLLS = max(1, int(60 / POLL_INTERVAL_SEC))


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
        
    model_path = str(
        resolve_package_path(
            os.environ.get("CROP_MODEL_PATH", DEFAULT_CROP_MODEL_PATH)
        )
    )
    ml_mode = os.environ.get("ML_MODE", "rule_based")

    env_classification_enabled = crop_classification_enabled()
    logger.info(
        "Crop classification default (env): %s | model=%s",
        "ENABLED" if env_classification_enabled else "DISABLED",
        model_path,
    )
    
    # Pre-load pipeline (heavy operation, do once at startup)
    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=model_path,
        ml_mode=ml_mode,
        verbose=os.environ.get("PIPELINE_VERBOSE", "").strip().lower() in ("1", "true", "yes"),
        use_mongodb=True,
    )
    
    logger.info(f"Pipeline ready (ml_mode={ml_mode}). Listening for jobs...")

    poll_n = 0
    while True:
        try:
            poll_n += 1
            if poll_n % REAP_EVERY_N_POLLS == 0:
                reap_stuck_running_jobs(jobs_col)

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
                
            process_assessment_job(
                jobs_col, pipeline, job, env_classification_enabled
            )

        except (ConnectionFailure, OperationFailure) as e:
            logger.warning(f"MongoDB connection issue during polling: {e}. Retrying in 5s...")
            time.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected worker error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
