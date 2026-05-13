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
from pathlib import Path

# Load .env from repo root
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

from main import SatelliteBasedCreditPipeline
from api.job_runner import process_assessment_job, utc_now as _utc_now

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DB_NAME = os.environ.get("MONGODB_DATABASE") or os.environ.get("MONGODB_DB") or "agristack"
POLL_INTERVAL_SEC = 2.0


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
