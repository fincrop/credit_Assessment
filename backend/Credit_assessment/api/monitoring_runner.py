"""Background job for one present-season monitoring run."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

COLLECTION = "monitoring_jobs"


def _monitoring_package() -> None:
    root = Path(__file__).resolve().parents[3]
    package = root / "Crop_Monitoring"
    if str(package) not in sys.path:
        sys.path.insert(0, str(package))


def _stamp(jobs_col: Any, job_id: str, **fields: Any) -> None:
    from bson.objectid import ObjectId

    fields["updated_at"] = datetime.now(timezone.utc)
    jobs_col.update_one({"_id": ObjectId(job_id)}, {"$set": fields})


def process_monitoring_job(jobs_col: Any, job_id: str) -> None:
    """Score one field. Progress is written onto the job row Next.js inserted."""
    from bson.objectid import ObjectId

    _monitoring_package()
    job = jobs_col.find_one({"_id": ObjectId(job_id)})
    if not job:
        logger.error("monitoring job %s disappeared before it started", job_id)
        return
    if job.get("stage") in ("complete", "failed"):
        return

    try:
        _stamp(
            jobs_col, job_id,
            stage="observing", percent=15,
            message="Pulling Sentinel-2, Sentinel-1, Landsat and buffer weather.",
            started_at=job.get("started_at") or datetime.now(timezone.utc),
            error=None,
        )
        inputs = job.get("inputs") or {}
        fields = _fields_from_job(jobs_col, job)
        if not fields:
            raise ValueError("The job has no field boundary.")

        from src.models import MonitorRequest
        from src.pipeline import combine_runs, run_monitoring

        if len(fields) == 1:
            field = fields[0]
            _stamp(jobs_col, job_id, stage="analysing", percent=55, message="Scoring zones, sowing, stress and yield.")
            request = _request(inputs, field)
            document = _jsonable(run_monitoring(request))
            from src.yield_model import strip_yield_internals
            strip_yield_internals(document)
            document["name"] = str(inputs.get("name") or field.get("name") or field["crop"])
            document["job_id"] = job_id
            document["farm_count"] = 1
            _store_parcels(jobs_col, job_id, document, field["geometry"])
        else:
            document = _score_many(jobs_col, job_id, inputs, fields)
            document["job_id"] = job_id
            parcel_doc = {
                "lineage": document.get("lineage") or {},
                "season": document.get("season"),
                "as_of": document.get("as_of"),
                "crop": document.get("crop"),
                "zones": document.pop("parcel_zones", []),
            }
            _store_parcels(jobs_col, job_id, parcel_doc, None)

        _stamp(jobs_col, job_id, stage="publishing", percent=90, message="Writing the farm document and map.")
        _stamp(
            jobs_col, job_id,
            stage="complete", percent=100, message="Complete",
            result=document,
            finished_at=datetime.now(timezone.utc),
            error=None,
        )
    except Exception as exc:
        logger.exception("monitoring job %s failed", job_id)
        _stamp(
            jobs_col, job_id,
            stage="failed", percent=None, error=str(exc)[:500],
            message="Monitoring failed.",
            finished_at=datetime.now(timezone.utc),
        )


def _store_parcels(jobs_col: Any, job_id: str, document: dict, parent_geometry: Any) -> None:
    """One row per updated parcel, so a later cluster comparison can query by village and crop."""
    lineage = document.get("lineage") or {}
    rows = []
    now = datetime.now(timezone.utc)
    for zone in document.get("zones") or []:
        if zone.get("kind") == "non_crop":
            continue
        sowing = zone.get("sowing") or {}
        harvest = zone.get("harvest") or {}
        yld = zone.get("yield") or {}
        latest = None
        for item in reversed(zone.get("intervals") or []):
            stress = (item.get("stress") or {}).get("type")
            if stress:
                latest = stress
                break
        rows.append({
            "job_id": job_id,
            "parcel_id": zone.get("zone_id"),
            "cluster_id": lineage.get("cluster_id"),
            "village": lineage.get("village"),
            "classification_job_id": lineage.get("classification_job_id"),
            "source_field_id": zone.get("source_field_id") or lineage.get("source_field_id"),
            "crop": zone.get("crop") or document.get("crop"),
            "season": document.get("season"),
            "as_of": document.get("as_of"),
            "split_reason": zone.get("split_reason"),
            "sowing_date": sowing.get("date"),
            "harvest_date": harvest.get("date"),
            "harvest_observed": harvest.get("observed"),
            "yield_t_ha": yld.get("t_ha"),
            "stress": latest,
            "geometry": zone.get("geometry"),
            "parent_geometry": zone.get("parent_geometry") or parent_geometry,
            "phenology": zone.get("phenology") or document.get("phenology"),
            "analysis": zone,
            "updated_at": now,
        })
    collection = jobs_col.database["monitoring_parcels"]
    collection.delete_many({"job_id": job_id})
    if rows:
        collection.insert_many(rows)


def _fields_from_job(jobs_col: Any, job: dict) -> list[dict]:
    """Classified fields by id, or the one boundary drawn on the page."""
    inputs = job.get("inputs") or {}
    field_ids = inputs.get("field_ids")
    class_id = _text(inputs.get("classification_job_id"))
    if class_id and isinstance(field_ids, list) and field_ids:
        from bson.objectid import ObjectId

        doc = jobs_col.database["classification_jobs"].find_one({"_id": ObjectId(class_id)})
        if not doc:
            raise ValueError("The classification run was not found.")
        features = (((doc.get("result") or {}).get("fields") or {}).get("features")) or []
        wanted = {str(item) for item in field_ids}
        skip = {"", "Unclassified", "Abstained", "Fallow", "Non-agricultural", "Water"}
        found = []
        for feature in features:
            props = feature.get("properties") or {}
            fid = str(props.get("field_id") or "")
            crop = str(props.get("crop") or "")
            if fid not in wanted or crop in skip or not feature.get("geometry"):
                continue
            found.append({
                "field_id": fid,
                "crop": crop,
                "confidence": float(props.get("confidence") or inputs.get("confidence") or 0.8),
                "geometry": feature["geometry"],
                "area_ha": props.get("area_ha"),
                "name": f"{crop} {fid}",
            })
        if not found:
            raise ValueError("None of the selected fields are on that classification.")
        return found

    areas = job.get("areas") or []
    boundary = (areas[0] or {}).get("boundary") if areas else None
    crop = str(inputs.get("crop") or "").strip()
    if not boundary:
        return []
    if not crop:
        raise ValueError("A crop name is required.")
    area = areas[0]
    return [{
        "field_id": str(inputs.get("source_field_id") or area.get("aoi_id") or "field"),
        "crop": crop,
        "confidence": float(inputs.get("confidence") or 0.8),
        "geometry": boundary,
        "area_ha": area.get("area_ha"),
        "name": str(inputs.get("name") or area.get("name") or crop),
    }]


def _request(inputs: dict, field: dict, *, hint: bool = True):
    from src.models import MonitorRequest

    return MonitorRequest(
        crop=field["crop"],
        geometry=field["geometry"],
        confidence=float(field.get("confidence") if field.get("confidence") is not None else inputs.get("confidence") or 0.8),
        season=(inputs.get("season") or None),
        ecoregion=(inputs.get("ecoregion") or None),
        as_of=_day(inputs.get("as_of")),
        sowing_hint=_day(inputs.get("sowing_date")) if hint else None,
        sowing_hint_source=str(inputs.get("sowing_source") or "provided"),
        district_yield_t_ha=_float(inputs.get("district_yield_t_ha")),
        cluster_id=_text(inputs.get("cluster_id")),
        village=_text(inputs.get("village")),
        classification_job_id=_text(inputs.get("classification_job_id")),
        source_field_id=str(field["field_id"]),
    )


def _score_many(jobs_col: Any, job_id: str, inputs: dict, fields: list[dict]) -> dict:
    """Read Earth Engine once per crop, then score every field from that stack."""
    from collections import defaultdict
    from datetime import date as date_cls

    from src.library import get_prior, resolve_window
    from src.observe import fetch_field_stacks
    from src.pipeline import combine_runs, run_monitoring

    as_of = _day(inputs.get("as_of")) or date_cls.today()
    season = inputs.get("season") or None
    groups: dict[str, list] = defaultdict(list)
    for field in fields:
        groups[field["crop"]].append(field)
    shared = dict(inputs)
    if len(groups) > 1:
        shared["district_yield_t_ha"] = None
    pieces = []
    done = 0
    total = max(len(fields), 1)
    for crop, group in groups.items():
        window = resolve_window(crop, as_of, season, get_prior(crop))
        _stamp(
            jobs_col, job_id,
            stage="observing", percent=15,
            message=f"Reading satellite data once for {len(group)} {crop} fields.",
        )
        logger.info("Reading 10-day composites for %s %s fields", len(group), crop)
        stacks = fetch_field_stacks(group, window.monitor_start, window.monitor_end, crop)
        group_pieces = []
        for field in group:
            done += 1
            _stamp(
                jobs_col, job_id,
                stage="analysing",
                percent=20 + int(70 * done / total),
                message=f"Scoring {crop} field {done} of {total}.",
            )
            stack = stacks.get(str(field["field_id"]))
            if stack is None or not stack.pixels:
                group_pieces.append((field, None))
                continue
            group_pieces.append((field, run_monitoring(_request(shared, field, hint=False), stack)))
        _apply_village_peers(group_pieces)
        pieces.extend(group_pieces)
    lineage = {
        "cluster_id": _text(inputs.get("cluster_id")),
        "village": _text(inputs.get("village")),
        "classification_job_id": _text(inputs.get("classification_job_id")),
        "source_field_id": None,
    }
    return _jsonable(combine_runs(
        pieces,
        name=str(inputs.get("name") or "Monitoring"),
        season=str(season or ""),
        as_of=as_of.isoformat(),
        lineage=lineage,
    ))


def _apply_village_peers(pieces: list) -> None:
    """Rank yield against the other farms of this crop in the same run."""
    from src.yield_model import strip_yield_internals, with_village_peers

    integrals = []
    for _field, doc in pieces:
        if not doc:
            continue
        for zone in doc.get("zones") or []:
            yielded = zone.get("yield") or {}
            value = yielded.get("cover_integral")
            if isinstance(value, (int, float)):
                integrals.append(float(value))
    if len(integrals) >= 15:
        for _field, doc in pieces:
            if not doc:
                continue
            for zone in doc.get("zones") or []:
                if zone.get("yield"):
                    zone["yield"] = with_village_peers(zone["yield"], integrals)
    for _field, doc in pieces:
        if doc:
            strip_yield_internals(doc)


def _text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _day(value: Any):
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _jsonable(value: Any) -> Any:
    """Mongo cannot store numpy scalars. Plain Python values pass through."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    item = getattr(value, "item", None)
    if callable(item) and type(value).__module__.startswith("numpy"):
        try:
            return item()
        except Exception:
            return value
    return value


def _float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
