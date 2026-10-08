"""D3: client-record intake, validation, unit normalisation and matching.

Client records are NOT a random sample of fields. They are used for
calibration and error discovery, and as accuracy evidence only alongside D1
(plan section 6, D3).
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Tuple, Union

import numpy as np
import pandas as pd

from .labels import is_blank, normalise_label

ACRE_HA = 0.40468564224
QUINTAL_T = 0.1  # 1 quintal = 100 kg = 0.1 t

# multiply a value in the unit by this to get t/ha
UNIT_TO_T_HA = {
    "t_ha": 1.0,
    "q_ha": QUINTAL_T,
    "q_acre": QUINTAL_T / ACRE_HA,
    "kg_acre": 0.001 / ACRE_HA,
}

CSV_COLUMNS = ["record_id", "field_id", "boundary", "crop", "season", "year",
               "sowing_date", "harvest_date", "yield_value", "yield_unit",
               "irrigated", "source", "consent"]

SEASONS = ["kharif", "rabi", "zaid", "summer", "perennial"]

JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Client field record (Track D3)",
    "description": "One field-season outcome supplied by a client. Requires consent. "
                   "Either field_id or boundary must be given.",
    "type": "object",
    "properties": {
        "record_id": {"type": ["string", "null"]},
        "field_id": {"type": ["string", "null"]},
        "boundary": {"description": "WKT string or GeoJSON geometry (EPSG:4326)",
                     "type": ["string", "object", "null"]},
        "crop": {"type": "string", "minLength": 1},
        "season": {"type": "string", "enum": SEASONS},
        "year": {"type": "integer", "minimum": 2000, "maximum": 2100},
        "sowing_date": {"type": ["string", "null"], "format": "date"},
        "harvest_date": {"type": ["string", "null"], "format": "date"},
        "yield_value": {"type": ["number", "null"], "minimum": 0},
        "yield_unit": {"type": ["string", "null"], "enum": list(UNIT_TO_T_HA) + [None]},
        "irrigated": {"type": ["boolean", "null"]},
        "source": {"type": "string", "minLength": 1},
        "consent": {"const": True},
    },
    "required": ["crop", "season", "year", "source", "consent"],
    "anyOf": [
        {"required": ["field_id"], "properties": {"field_id": {"type": "string", "minLength": 1}}},
        {"required": ["boundary"], "properties": {"boundary": {"type": ["string", "object"]}}},
    ],
    "dependentRequired": {"yield_value": ["yield_unit"]},
    "additionalProperties": True,
}


def write_templates(ref_dir: Union[str, Path]) -> None:
    ref = Path(ref_dir)
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "client_record.schema.json").write_text(json.dumps(JSON_SCHEMA, indent=2), encoding="utf-8")
    (ref / "client_records_template.csv").write_text(",".join(CSV_COLUMNS) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------
_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}


def _bool(v) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if is_blank(v):
        return None
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"not a yes/no value: {v!r}")


def _date(v) -> Optional[dt.date]:
    if is_blank(v):
        return None
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v).strip()[:10])


def _geom(v):
    from shapely import wkt
    from shapely.geometry import shape

    if is_blank(v):
        return None
    if isinstance(v, Mapping):
        return shape(v)
    s = str(v).strip()
    if s.startswith("{"):
        return shape(json.loads(s))
    return wkt.loads(s)


def load_records(obj) -> List[dict]:
    """CSV path, JSON path (list of records), DataFrame or list of dicts."""
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, list):
        return [dict(r) for r in obj]
    p = Path(str(obj))
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return data["records"] if isinstance(data, dict) else data
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    return df.to_dict(orient="records")


def validate_records(records: Iterable[Mapping]) -> Tuple[pd.DataFrame, List[dict]]:
    """Validate and normalise records.

    Returns (clean table, errors). Each error is
    ``{"row": i, "record_id": ..., "column": ..., "message": ...}``. Records
    with any error are excluded from the clean table. Yields are converted
    to ``yield_t_ha``.
    """
    clean, errors = [], []
    for i, r in enumerate(records):
        rid = r.get("record_id")
        rid = str(i + 1) if is_blank(rid) else str(rid).strip()
        errs = []

        def err(col, msg):
            errs.append({"row": i + 1, "record_id": rid, "column": col, "message": msg})

        out = {"record_id": rid}
        fid = r.get("field_id")
        out["field_id"] = None if is_blank(fid) else str(fid).strip()
        try:
            out["geometry"] = _geom(r.get("boundary"))
            if out["geometry"] is not None and not out["geometry"].is_valid:
                out["geometry"] = out["geometry"].buffer(0)
        except Exception as exc:  # noqa: BLE001
            out["geometry"] = None
            err("boundary", f"cannot parse boundary as WKT or GeoJSON ({exc})")
        if out["field_id"] is None and out["geometry"] is None and not any(
                e["column"] == "boundary" for e in errs):
            err("field_id", "either field_id or boundary is required")

        crop = normalise_label(r.get("crop"))
        if crop is None:
            err("crop", "crop is required")
        out["crop"] = crop

        season = None if is_blank(r.get("season")) else str(r.get("season")).strip().lower()
        if season not in SEASONS:
            err("season", f"season must be one of {SEASONS}")
        out["season"] = season

        try:
            year = int(float(str(r.get("year")).strip()))
            if not 2000 <= year <= 2100:
                raise ValueError
            out["year"] = year
        except Exception:  # noqa: BLE001
            out["year"] = None
            err("year", "year must be an integer between 2000 and 2100")

        for col in ("sowing_date", "harvest_date"):
            try:
                out[col] = _date(r.get(col))
            except Exception:  # noqa: BLE001
                out[col] = None
                err(col, f"{col} must be an ISO date YYYY-MM-DD")
        if out.get("sowing_date") and out.get("harvest_date") and out["harvest_date"] <= out["sowing_date"]:
            err("harvest_date", "harvest_date must be after sowing_date")

        yv, yu = r.get("yield_value"), r.get("yield_unit")
        out["yield_value"], out["yield_unit"], out["yield_t_ha"] = None, None, None
        if not is_blank(yv):
            try:
                val = float(str(yv).strip())
                if val < 0 or math.isnan(val):
                    raise ValueError
                out["yield_value"] = val
            except Exception:  # noqa: BLE001
                err("yield_value", "yield_value must be a non-negative number")
            unit = None if is_blank(yu) else str(yu).strip().lower().replace("/", "_")
            if unit not in UNIT_TO_T_HA:
                err("yield_unit", f"yield_unit must be one of {list(UNIT_TO_T_HA)} when yield_value is given")
            else:
                out["yield_unit"] = unit
                if out["yield_value"] is not None:
                    out["yield_t_ha"] = out["yield_value"] * UNIT_TO_T_HA[unit]
        elif not is_blank(yu):
            out["yield_unit"] = str(yu).strip().lower()

        try:
            out["irrigated"] = _bool(r.get("irrigated"))
        except ValueError as exc:
            out["irrigated"] = None
            err("irrigated", str(exc))

        src = r.get("source")
        if is_blank(src):
            err("source", "source is required")
        out["source"] = None if is_blank(src) else str(src).strip()

        try:
            consent = _bool(r.get("consent"))
        except ValueError:
            consent = None
        if consent is not True:
            err("consent", "consent must be true; record rejected")
        out["consent"] = consent

        if errs:
            errors.extend(errs)
        else:
            clean.append(out)
    cols = ["record_id", "field_id", "geometry", "crop", "season", "year", "sowing_date",
            "harvest_date", "yield_value", "yield_unit", "yield_t_ha", "irrigated",
            "source", "consent"]
    return pd.DataFrame(clean, columns=cols), errors


def to_t_ha(value: float, unit: str) -> float:
    return float(value) * UNIT_TO_T_HA[unit]


# ---------------------------------------------------------------------------
# Our results
# ---------------------------------------------------------------------------
def _first(d: Mapping, *keys):
    for k in keys:
        if k in d and not is_blank(d[k]) and not isinstance(d[k], (dict, list)):
            return d[k]
    return None


def load_results(obj) -> pd.DataFrame:
    """Monitoring / classification results -> one row per field.

    Accepts a FeatureCollection, a list of field records, or the monitoring
    analysis JSON (``{"farms": [...], "fields": FeatureCollection}``). Keys
    are read flexibly; missing keys become NaN.
    """
    from shapely.geometry import shape

    if isinstance(obj, (str, Path)):
        with open(obj, encoding="utf-8") as fh:
            obj = json.load(fh)
    geoms = {}
    recs: List[dict] = []
    if isinstance(obj, dict) and "farms" in obj:
        recs = [dict(x) for x in obj["farms"]]
        fc = obj.get("fields") or {}
        parts: dict = {}
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            fid = p.get("source_field_id", p.get("field_id"))
            if f.get("geometry") and fid is not None:
                parts.setdefault(str(fid), []).append(shape(f["geometry"]))
        from shapely.ops import unary_union
        geoms = {k: (v[0] if len(v) == 1 else unary_union(v)) for k, v in parts.items()}
        # per-field sowing windows live in zones
        zones = {}
        for z in obj.get("zones", []) or []:
            fid = str(z.get("zone_id", "")).split("-z")[0]
            s = z.get("sowing") or {}
            if fid and fid not in zones and s:
                zones[fid] = s
        for r in recs:
            s = zones.get(str(r.get("field_id")))
            if s:
                r.setdefault("sowing_p10", s.get("early"))
                r.setdefault("sowing_p90", s.get("late"))
                r.setdefault("sowing_sources", s.get("sources"))
    elif isinstance(obj, dict) and "features" in obj:
        for f in obj["features"]:
            p = dict(f.get("properties") or {})
            if f.get("geometry"):
                p["_geometry"] = shape(f["geometry"])
            recs.append(p)
    elif isinstance(obj, list):
        recs = [dict(x) for x in obj]
    else:
        raise ValueError("unrecognised results format")

    rows = []
    for r in recs:
        fid = _first(r, "field_id", "id")
        rows.append({
            "field_id": None if fid is None else str(fid),
            "crop": normalise_label(_first(r, "crop", "model_top_crop")),
            "status": _first(r, "status"),
            "area_ha": _first(r, "area_ha"),
            "sowing_date": _first(r, "sowing_date"),
            "sowing_p10": _first(r, "sowing_p10"),
            "sowing_p90": _first(r, "sowing_p90"),
            "harvest_date": _first(r, "harvest_date"),
            "yield_t_ha": _first(r, "yield_t_ha"),
            "yield_p10": _first(r, "yield_p10"),
            "yield_p90": _first(r, "yield_p90"),
            "stage": _first(r, "stage"),
            "stress_type": _first(r, "stress_type", "stress"),
            "geometry": r.get("_geometry") or geoms.get(None if fid is None else str(fid)),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def _spatial_match(rec_geom, results: pd.DataFrame, min_iou: float):
    import geopandas as gpd

    cand = results[results["geometry"].notna()]
    if cand.empty or rec_geom is None:
        return None, None
    g = gpd.GeoDataFrame(cand[["field_id"]].copy(), geometry=list(cand["geometry"]), crs="EPSG:4326")
    hits = g[g.intersects(rec_geom)]
    if hits.empty:
        return None, 0.0
    crs = gpd.GeoSeries([rec_geom], crs="EPSG:4326").estimate_utm_crs()
    rg = gpd.GeoSeries([rec_geom], crs="EPSG:4326").to_crs(crs).iloc[0]
    hp = hits.to_crs(crs)
    inter = hp.geometry.intersection(rg).area
    best = inter.idxmax()
    union = hp.geometry.loc[best].union(rg).area
    iou = float(inter.loc[best] / union) if union > 0 else 0.0
    if iou < min_iou:
        return None, iou
    return str(hp.loc[best, "field_id"]), iou


def _d(v) -> Optional[dt.date]:
    try:
        return _date(v)
    except Exception:  # noqa: BLE001
        return None


def match_records(clean: pd.DataFrame, results, min_iou: float = 0.3,
                  sowing_tol_days: int = 7, yield_tol_pct: float = 25.0
                  ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Match client records to our results; returns (comparison, disagreements).

    Matching: by ``field_id`` when it exists in the results, otherwise by
    largest-overlap spatial join with IoU >= ``min_iou``.
    Errors are ours minus client (days, %).
    """
    res = results if isinstance(results, pd.DataFrame) else load_results(results)
    by_id = {str(f): i for i, f in zip(res.index, res["field_id"]) if f is not None}
    comp, log = [], []
    for _, r in clean.iterrows():
        fid, method, iou = None, None, None
        if r["field_id"] is not None and r["field_id"] in by_id:
            fid, method = r["field_id"], "field_id"
        elif r["geometry"] is not None:
            fid, iou = _spatial_match(r["geometry"], res, min_iou)
            method = "spatial" if fid else None
        row = {"record_id": r["record_id"], "client_field_id": r["field_id"],
               "matched_field_id": fid, "match_method": method, "iou": iou,
               "season": r["season"], "year": r["year"], "source": r["source"],
               "irrigated": r["irrigated"], "crop_client": r["crop"]}
        if fid is None:
            row["match_method"] = "unmatched"
            comp.append(row)
            log.append({"record_id": r["record_id"], "type": "unmatched",
                        "client": r["field_id"] or "boundary", "ours": None,
                        "detail": f"no result with matching field_id or IoU >= {min_iou}"
                                  + (f" (best IoU {iou:.2f})" if iou else "")})
            continue
        o = res.loc[by_id[fid]] if fid in by_id else res[res["field_id"] == fid].iloc[0]
        row["crop_ours"] = o["crop"]
        row["status_ours"] = o.get("status")
        row["crop_match"] = (o["crop"] == r["crop"]) if o["crop"] else None
        if row["crop_match"] is False:
            log.append({"record_id": r["record_id"], "type": "crop_mismatch",
                        "client": r["crop"], "ours": o["crop"], "detail": ""})
        sc, so = r["sowing_date"], _d(o.get("sowing_date"))
        row["sowing_client"], row["sowing_ours"] = sc, so
        if sc and so:
            e = (so - sc).days
            row["sowing_error_days"] = e
            p10, p90 = _d(o.get("sowing_p10")), _d(o.get("sowing_p90"))
            row["sowing_in_p10_p90"] = (p10 <= sc <= p90) if (p10 and p90) else None
            if abs(e) > sowing_tol_days:
                log.append({"record_id": r["record_id"], "type": "sowing_error",
                            "client": sc.isoformat(), "ours": so.isoformat(),
                            "detail": f"{e:+d} days"})
        yc = r["yield_t_ha"]
        yo = o.get("yield_t_ha")
        yo = None if is_blank(yo) else float(yo)
        row["yield_client_t_ha"], row["yield_ours_t_ha"] = yc, yo
        if yc is not None and yo is not None and not (isinstance(yc, float) and math.isnan(yc)):
            if yc > 0:
                pe = 100.0 * (yo - yc) / yc
                row["yield_pct_error"] = pe
                if abs(pe) > yield_tol_pct:
                    log.append({"record_id": r["record_id"], "type": "yield_error",
                                "client": round(yc, 3), "ours": round(yo, 3),
                                "detail": f"{pe:+.1f}%"})
            y10, y90 = o.get("yield_p10"), o.get("yield_p90")
            if not is_blank(y10) and not is_blank(y90):
                row["yield_in_p10_p90"] = float(y10) <= yc <= float(y90)
        comp.append(row)
    comp_df = pd.DataFrame(comp)
    log_df = pd.DataFrame(log, columns=["record_id", "type", "client", "ours", "detail"])
    return comp_df, log_df


def summarise(comp: pd.DataFrame, min_yield_records: int = 30) -> dict:
    """Per-crop summary for the gate report (kind ``client_comparison``)."""
    out = {"kind": "client_comparison", "n_records": int(len(comp)),
           "note": "Client records are not a random sample; use for calibration and "
                   "error discovery, and as accuracy evidence only alongside D1.",
           "min_yield_records_per_crop": min_yield_records}
    if comp.empty:
        out.update(n_matched=0, crop_agreement=None, sowing=None, yield_by_crop={})
        return out
    m = comp[comp["match_method"] != "unmatched"]
    out["n_matched"] = int(len(m))
    cm = m["crop_match"].dropna() if "crop_match" in m else pd.Series(dtype=bool)
    out["crop_agreement"] = {"n": int(len(cm)), "rate": float(cm.mean()) if len(cm) else None}
    if "sowing_error_days" in m:
        e = m["sowing_error_days"].dropna().abs()
        out["sowing"] = {"n": int(len(e)),
                         "median_abs_error_days": float(e.median()) if len(e) else None,
                         "p90_abs_error_days": float(np.percentile(e, 90)) if len(e) else None}
    else:
        out["sowing"] = {"n": 0, "median_abs_error_days": None, "p90_abs_error_days": None}
    ybc = {}
    if "yield_pct_error" in m:
        for crop, g in m[m["yield_pct_error"].notna()].groupby("crop_client"):
            yc = g["yield_client_t_ha"].astype(float)
            yo = g["yield_ours_t_ha"].astype(float)
            ybc[crop] = {
                "n": int(len(g)),
                "field_median_abs_pct_error": float(g["yield_pct_error"].abs().median()),
                "aggregate_pct_error": float(100 * (yo.mean() - yc.mean()) / yc.mean()) if yc.mean() > 0 else None,
                "enough_records": bool(len(g) >= min_yield_records),
            }
    out["yield_by_crop"] = ybc
    return out
