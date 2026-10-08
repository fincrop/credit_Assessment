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

        if _engine() == "raster":
            document = _score_raster(jobs_col, job_id, inputs, fields)
            document["job_id"] = job_id
            # The parcel store wants each zone's geometry and record; the job
            # document keeps them once (fields / records) to stay small.
            geom = {str((f.get("properties") or {}).get("field_id")): f.get("geometry")
                    for f in (document.get("fields") or {}).get("features") or []}
            rec = {str(r.get("field_id")): r for r in document.get("records") or []}
            _store_parcels(jobs_col, job_id, {
                "lineage": document.get("lineage") or {}, "season": document.get("season"),
                "as_of": document.get("as_of"), "crop": document.get("crop"),
                "zones": [{**z, "geometry": geom.get(str(z.get("source_field_id"))),
                           "record": rec.get(str(z.get("source_field_id")))}
                          for z in document.get("zones") or []],
            }, None)
        elif len(fields) == 1:
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

        document = _fit_document(document)
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


def _store_parcels(jobs_col: Any, job_id: str, document: dict, _parent_geometry: Any) -> None:
    """One slim row per parcel, for a later query by village and crop.

    The field polygon is stored once. The full zone, the village outline, and
    the phenology series are not copied onto every row: that second copy is
    what filled the cluster. Those stay on the monitoring job and in the
    local monitoring file. `parent_geometry` is accepted so existing callers
    keep working; it is not written.
    """
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
        # No crop to monitor: the classifier did not name one, or named one the
        # user did not request ("Not requested"), or saw too little.
        skip = {"", "Unclassified", "Abstained", "Fallow", "Non-agricultural", "Water",
                "Not requested", "Others", "Insufficient data"}
        found = []
        for feature in features:
            props = feature.get("properties") or {}
            fid = str(props.get("field_id") or "")
            crop = str(props.get("crop") or "")
            if fid not in wanted or crop in skip or not feature.get("geometry"):
                continue
            # The classifier's real probability, not a renormalised 1.0 (older
            # results stored 1.0 for every field when one crop was requested).
            conf = props.get("p_top1", props.get("confidence"))
            found.append({
                "field_id": fid,
                "crop": crop,
                "confidence": float(conf if conf is not None else inputs.get("confidence") or 0.8),
                "geometry": feature["geometry"],
                "area_ha": props.get("area_ha"),
                "name": f"{crop} {fid}",
                "classification": {
                    k: props.get(k) for k in (
                        "status", "model_top_crop", "top2_crop", "p_top1", "p_top2",
                        "margin", "cycle_complete", "cycle_peak",
                    ) if props.get(k) is not None
                },
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


MAX_RESULT_BYTES = 14_000_000      # Mongo caps a document at 16 MB


def _fit_document(document: dict) -> dict:
    """Keep the job result under Mongo's document limit.

    Per-look stress intervals are the first thing to leave the job document
    when a large village would overflow it. The local monitoring file keeps
    them. The document says when it was trimmed.
    """
    import json

    size = len(json.dumps(document, default=str))
    if size <= MAX_RESULT_BYTES:
        return document
    for zone in document.get("zones") or []:
        zone.pop("intervals", None)
    document["trimmed"] = {
        "reason": f"result was {size / 1e6:.1f} MB; per-look intervals omitted. The local monitoring file keeps them",
    }
    return document


def _engine() -> str:
    """raster (default): 10 m pixel stacks per village. point: the legacy
    sample-point engine, kept for shadow-mode comparison only."""
    import os

    return (os.environ.get("MONITORING_ENGINE") or "raster").strip().lower()


def raster_root() -> Path:
    import os

    root = os.environ.get("MONITORING_RASTER_ROOT")
    return Path(root) if root else Path(__file__).resolve().parents[3] / "Crop_Monitoring" / "outputs"


def _district_rows(inputs: dict, crops: list[str]) -> list[dict]:
    """Official district yields: the evaluation reference table, else the single
    figure typed on the job (treated as one year, so the band uses the default spread)."""
    import csv

    rows: list[dict] = []
    table = Path(__file__).resolve().parents[3] / "evaluation" / "reference" / "district_yields.csv"
    district = str(inputs.get("district") or "").strip().lower()
    if table.exists():
        with table.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("yield_t_ha"):
                    continue
                if district and str(r.get("district") or "").strip().lower() != district:
                    continue
                rows.append({"crop": r.get("crop"), "year": int(r.get("year") or 0),
                             "yield_t_ha": float(r["yield_t_ha"]), "source": r.get("source") or "official"})
    typed = _float(inputs.get("district_yield_t_ha"))
    if typed and not rows and len(crops) == 1:
        rows.append({"crop": crops[0], "year": 0, "yield_t_ha": typed,
                     "source": "entered on the monitoring request"})
    return rows


def _score_raster(jobs_col: Any, job_id: str, inputs: dict, fields: list[dict]) -> dict:
    """Village raster engine (accuracy plan Track C)."""
    from datetime import date as date_cls

    from src.raster import engine

    as_of = _day(inputs.get("as_of")) or date_cls.today()
    season = str(inputs.get("season") or "kharif")
    year = as_of.year if season != "rabi" or as_of.month >= 9 else as_of.year - 1
    records: dict = {}
    hint = _day(inputs.get("sowing_date"))
    if hint and len(fields) == 1 and str(inputs.get("sowing_source") or "provided") == "provided":
        records[str(fields[0]["field_id"])] = hint
    out_dir = raster_root() / job_id
    req = engine.VillageRequest(
        fields=fields, year=year, as_of=as_of, season=season, out_dir=out_dir,
        district_yields=_district_rows(inputs, sorted({f["crop"] for f in fields})),
        sowing_records=records, name=str(inputs.get("name") or "Monitoring"),
        village_normal=True,
        lineage={
            "cluster_id": _text(inputs.get("cluster_id")),
            "village": _text(inputs.get("village")),
            "classification_job_id": _text(inputs.get("classification_job_id")),
        },
    )
    steps = iter(range(20, 90, 7))

    def progress(message: str) -> None:
        _stamp(jobs_col, job_id, stage="analysing" if "Sentinel" not in message else "observing",
               percent=next(steps, 88), message=message)

    document = _jsonable(engine.run(req, progress=progress))
    document["raster_dir"] = str(out_dir)
    return document


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
    from datetime import date as _date
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value.isoformat()
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
