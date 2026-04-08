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
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load .env from repo root before pipeline / config import
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env")
    except ImportError:
        pass


_load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.serialization import slim_assessment_for_api
from main import SatelliteBasedCreditPipeline

logger = logging.getLogger(__name__)

_pipeline: Optional[SatelliteBasedCreditPipeline] = None


def _cors_origins() -> List[str]:
    raw = os.environ.get("CORS_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _optional_api_key() -> Optional[str]:
    k = os.environ.get("API_SERVICE_KEY", "").strip()
    return k or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    model_path = os.environ.get("CROP_MODEL_PATH", "models/crop_classifier_model.joblib")
    ml_mode = os.environ.get("ML_MODE", "rule_based")
    use_mdb = os.environ.get("USE_MONGODB", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    _pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=model_path,
        ml_mode=ml_mode,
        verbose=os.environ.get("PIPELINE_VERBOSE", "").strip().lower()
        in ("1", "true", "yes"),
        use_mongodb=use_mdb,
    )
    logger.info("Pipeline ready (ml_mode=%s, mongodb=%s)", ml_mode, use_mdb)
    yield
    _pipeline = None


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
    return {
        "status": "ok",
        "pipeline_loaded": _pipeline is not None,
        "mongodb": bool(_pipeline and getattr(_pipeline, "use_mongodb", False)),
    }


async def _run_assessment(body: AssessRequest) -> Dict[str, Any]:
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")

    fid = body.farmer_id.strip()
    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, Any]:
        return _pipeline.assess_farmer_from_db(
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
