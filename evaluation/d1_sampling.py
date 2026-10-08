"""D1: independent image interpretation of a village (plan section 6, D1).

Workflow
--------
1. :func:`draw_sample` - stratified random sample of fields by predicted
   class, allocation proportional to area with a per-stratum minimum.
2. :func:`interpretation_sheet` - a BLIND sheet for interpreters plus a
   separate key (sample_id -> field_id / stratum / map class).
3. :func:`make_chips` - monthly Sentinel-2 true / false colour chips and an
   HTML contact sheet per sampled field (Earth Engine).
4. :func:`import_labels` - two interpreters' sheets (+ optional adjudication
   sheet) -> Cohen's kappa, disagreement list, final reference table.

The interpretation evidence (chips, contact sheets) never shows the model's
crop, status, probabilities or stratum.
"""

from __future__ import annotations

import calendar
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from . import metrics
from .labels import is_blank, min_confidence, normalise_confidence, normalise_label

REPO_ROOT = Path(__file__).resolve().parents[1]

# Columns that would un-blind an interpreter. Checked by assert_blind().
FORBIDDEN_SHEET_COLUMNS = {
    "crop", "status", "model_top_crop", "top2_crop", "top2", "p_top1", "p_top2",
    "p_crop", "margin", "stratum", "map_class", "map_label", "field_id",
    "color", "note", "abstain_reason", "cycle_complete", "n_obs_cycle",
    "cycle_duration_days", "sowing_date", "stage", "yield_t_ha", "stress_type",
}
SHEET_COLUMNS = ["sample_id", "lat", "lon", "chip_dir", "label", "confidence", "notes"]

ABSTAIN_STATUSES = {"abstained", "out_of_support", "insufficient_evidence"}
ABSTAIN_CROPS = {"Abstained", "Other", "Unknown", "Other/Unknown"}
FLAG_STATUSES = {"phenology_disagrees"}

# Fixed stretch for every chip (no per-image stretch). Surface reflectance x 1e4.
STRETCH = {
    "tc": {"bands": ["B4", "B3", "B2"], "min": [0, 0, 0], "max": [2500, 2500, 2500],
           "gamma": 1.2, "label": "True colour (B4/B3/B2)", "outline": "FFFF00"},
    "fc": {"bands": ["B8", "B4", "B3"], "min": [0, 0, 0], "max": [5000, 2500, 2500],
           "gamma": 1.0, "label": "False colour (B8/B4/B3)", "outline": "00FFFF"},
}
S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CS_COLLECTION = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
CS_BAND = "cs_cdf"
CS_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_feature_collection(obj) -> dict:
    """Path / JSON string / dict / list of features -> FeatureCollection dict."""
    if isinstance(obj, (str, Path)) and Path(str(obj)).exists():
        with open(obj, encoding="utf-8") as fh:
            obj = json.load(fh)
    elif isinstance(obj, str):
        obj = json.loads(obj)
    if isinstance(obj, list):
        obj = {"type": "FeatureCollection", "features": obj}
    if not isinstance(obj, dict) or "features" not in obj:
        raise ValueError("expected a GeoJSON FeatureCollection")
    return obj


def _id_key(v):
    s = str(v)
    try:
        return (0, float(s), s)
    except ValueError:
        return (1, 0.0, s)


def default_stratum(props: Mapping, flagged: bool = False) -> str:
    """Stratum used by D1: predicted crop, with abstained/other merged and a
    separate stratum for monitoring-flagged fields."""
    if flagged:
        return "Monitoring-flagged"
    status = str(props.get("status") or "").strip().lower()
    crop = normalise_label(props.get("crop")) or "Abstained"
    if status in ABSTAIN_STATUSES or crop in ABSTAIN_CROPS:
        return "Other/Abstained"
    return crop


def fields_frame(fc, flagged_ids: Optional[Iterable] = None,
                 stratum_fn: Optional[Callable[[Mapping, bool], str]] = None):
    """FeatureCollection -> GeoDataFrame (EPSG:4326) with field_id, map_class,
    stratum, area_ha, lat, lon, geometry."""
    import geopandas as gpd
    from shapely.geometry import shape

    fc = load_feature_collection(fc)
    flagged = {str(x) for x in (flagged_ids or [])}
    sfn = stratum_fn or default_stratum
    rows = []
    for i, f in enumerate(fc["features"]):
        p = f.get("properties") or {}
        geom = shape(f["geometry"]) if f.get("geometry") else None
        fid = str(p.get("field_id", f.get("id", i)))
        is_flag = fid in flagged or str(p.get("status") or "").lower() in FLAG_STATUSES
        rows.append({
            "field_id": fid,
            "map_class": normalise_label(p.get("crop")) or "Abstained",
            "stratum": sfn(p, is_flag),
            "area_ha": p.get("area_ha"),
            "geometry": geom,
        })
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    # area: property first, else geodesic
    missing = gdf["area_ha"].isna()
    if missing.any():
        from pyproj import Geod
        geod = Geod(ellps="WGS84")
        gdf.loc[missing, "area_ha"] = [
            abs(geod.geometry_area_perimeter(g)[0]) / 1e4 if g is not None else np.nan
            for g in gdf.loc[missing, "geometry"]
        ]
    gdf["area_ha"] = gdf["area_ha"].astype(float)
    pts = [g.representative_point() if g is not None else None for g in gdf.geometry]
    gdf["lat"] = [round(p.y, 6) if p is not None else np.nan for p in pts]
    gdf["lon"] = [round(p.x, 6) if p is not None else np.nan for p in pts]
    gdf = gdf.iloc[sorted(range(len(gdf)), key=lambda i: _id_key(gdf.iloc[i]["field_id"]))]
    return gdf.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Allocation and sampling
# ---------------------------------------------------------------------------
def allocate(sizes: Mapping[str, int], areas: Mapping[str, float], n_total: int,
             n_min: int = 30) -> Dict[str, int]:
    """Proportional-to-area allocation with a per-stratum minimum.

    Each stratum gets ``max(min(n_min, N_h), proportional share)`` capped at
    ``N_h``; strata fixed at the minimum or the cap are removed and the rest
    of the sample is re-shared by area until stable. Integer rounding uses
    largest remainders. If the minimums alone exceed ``n_total``, every
    stratum gets its minimum (the total then exceeds ``n_total``).
    """
    strata = sorted(sizes)
    N = {h: int(sizes[h]) for h in strata}
    A = {h: max(float(areas.get(h, 0.0)), 0.0) for h in strata}
    floor = {h: min(n_min, N[h]) for h in strata}
    n_total = min(int(n_total), sum(N.values()))
    if sum(floor.values()) >= n_total:
        return floor
    fixed: Dict[str, float] = {}
    free = [h for h in strata if N[h] > 0]
    while True:
        remaining = n_total - sum(fixed.values())
        a_free = sum(A[h] for h in free)
        share = {h: (remaining * A[h] / a_free if a_free > 0 else remaining / len(free))
                 for h in free}
        changed = False
        for h in list(free):
            if share[h] < floor[h]:
                fixed[h] = floor[h]
                free.remove(h)
                changed = True
            elif share[h] > N[h]:
                fixed[h] = N[h]
                free.remove(h)
                changed = True
        if not changed or not free:
            break
    alloc = {h: int(fixed[h]) for h in fixed}
    if free:
        remaining = n_total - sum(alloc.values())
        a_free = sum(A[h] for h in free)
        exact = {h: (remaining * A[h] / a_free if a_free > 0 else remaining / len(free))
                 for h in free}
        base = {h: int(math.floor(exact[h])) for h in free}
        left = remaining - sum(base.values())
        order = sorted(free, key=lambda h: (-(exact[h] - base[h]), h))
        for h in order:
            if left <= 0:
                break
            if base[h] < N[h]:
                base[h] += 1
                left -= 1
        alloc.update(base)
    for h in strata:
        alloc.setdefault(h, 0)
    return alloc


def draw_sample(classification_geojson, n_total: int = 300, strata_min: int = 30,
                seed: int = 20261102, selection: str = "area",
                flagged_ids: Optional[Iterable] = None,
                stratum_fn: Optional[Callable] = None) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Stratified random sample of fields by predicted class.

    Parameters
    ----------
    selection : ``"area"`` (default) selects fields within a stratum with
        probability proportional to area (successive sampling without
        replacement), which mimics dropping random points in the stratum and
        taking the field under each point - the design the Olofsson area
        estimator assumes. ``"uniform"`` gives every field equal probability.

    Returns
    -------
    sample : field_id, stratum, map_class, lat, lon, area_ha, geometry_wkt,
        draw_order (one row per sampled field)
    strata : stratum, n_fields, area_ha, n_sample (needed later by
        :func:`evaluation.area.stratified_estimate`)
    """
    if selection not in ("area", "uniform"):
        raise ValueError("selection must be 'area' or 'uniform'")
    gdf = fields_frame(classification_geojson, flagged_ids, stratum_fn)
    gdf = gdf[gdf.geometry.notna()]
    sizes = gdf.groupby("stratum").size().to_dict()
    areas = gdf.groupby("stratum")["area_ha"].sum().to_dict()
    alloc = allocate(sizes, areas, n_total, strata_min)
    rng = np.random.default_rng(seed)
    picks = []
    for h in sorted(alloc):
        sub = gdf[gdf["stratum"] == h]
        k = alloc[h]
        if k <= 0:
            continue
        if selection == "area":
            w = sub["area_ha"].clip(lower=0).to_numpy(dtype=float)
            if (w > 0).sum() < k:
                w = np.where(w > 0, w, 1e-12)
            idx = rng.choice(len(sub), size=k, replace=False, p=w / w.sum())
        else:
            idx = rng.choice(len(sub), size=k, replace=False)
        chosen = sub.iloc[np.asarray(idx)].copy()
        chosen["draw_order"] = range(1, len(chosen) + 1)
        picks.append(chosen)
    sample = pd.concat(picks) if picks else gdf.iloc[0:0].copy()
    sample["geometry_wkt"] = [g.wkt for g in sample.geometry]
    sample = pd.DataFrame(sample.drop(columns="geometry"))
    sample = sample[["field_id", "stratum", "map_class", "lat", "lon", "area_ha",
                     "draw_order", "geometry_wkt"]].reset_index(drop=True)
    strata = pd.DataFrame({
        "stratum": sorted(alloc),
        "n_fields": [int(sizes[h]) for h in sorted(alloc)],
        "area_ha": [float(areas[h]) for h in sorted(alloc)],
        "n_sample": [int(alloc[h]) for h in sorted(alloc)],
    })
    strata.attrs["selection"] = selection
    strata.attrs["seed"] = seed
    return sample, strata


def map_class_areas(classification_geojson, flagged_ids=None) -> pd.DataFrame:
    gdf = fields_frame(classification_geojson, flagged_ids)
    return (gdf.groupby("map_class")["area_ha"].sum().rename("area_ha")
            .reset_index().sort_values("map_class").reset_index(drop=True))


# ---------------------------------------------------------------------------
# Blind interpretation sheet
# ---------------------------------------------------------------------------
def assert_blind(sheet: pd.DataFrame) -> None:
    bad = sorted(c for c in sheet.columns if c.strip().lower() in FORBIDDEN_SHEET_COLUMNS
                 or c.strip().lower().startswith(("p_", "model_")))
    if bad:
        raise ValueError(f"interpretation sheet is not blind; remove columns {bad}")


def interpretation_sheet(sample: pd.DataFrame, seed: int = 7,
                         chip_root: str = "chips",
                         id_prefix: str = "D1") -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Blind interpreter sheet + separate key.

    Sample ids are assigned after a random shuffle, so they carry no
    information about field_id order or stratum.
    """
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(sample))
    shuffled = sample.iloc[order].reset_index(drop=True)
    width = max(4, len(str(len(sample))))
    ids = [f"{id_prefix}-{i + 1:0{width}d}" for i in range(len(shuffled))]
    sheet = pd.DataFrame({
        "sample_id": ids,
        "lat": shuffled["lat"].round(6).values,
        "lon": shuffled["lon"].round(6).values,
        "chip_dir": [f"{chip_root}/{i}" for i in ids],
        "label": "",
        "confidence": "",
        "notes": "",
    })[SHEET_COLUMNS]
    assert_blind(sheet)
    key_cols = [c for c in ("field_id", "stratum", "map_class", "area_ha",
                            "geometry_wkt") if c in shuffled.columns]
    key = pd.concat([pd.DataFrame({"sample_id": ids}),
                     shuffled[key_cols].reset_index(drop=True)], axis=1)
    return sheet, key


# ---------------------------------------------------------------------------
# Sentinel-2 chips
# ---------------------------------------------------------------------------
def months_between(season_start: str, season_end: str) -> List[Tuple[str, str, str]]:
    """[(YYYY-MM, first_day, first_day_of_next_month)] covering the season."""
    s = dt.date.fromisoformat(str(season_start)[:10]).replace(day=1)
    e = dt.date.fromisoformat(str(season_end)[:10])
    out = []
    cur = s
    while cur <= e:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((cur.strftime("%Y-%m"), cur.isoformat(), nxt.isoformat()))
        cur = nxt
    return out


def chip_box(lat: float, lon: float, geom=None, box_m: float = 600.0,
             margin_m: float = 50.0) -> List[float]:
    """[west, south, east, north] of a square box of at least ``box_m``
    around the point, enlarged to contain the field plus a margin."""
    m_lat = 111_320.0
    m_lon = 111_320.0 * math.cos(math.radians(lat))
    half = box_m / 2.0
    if geom is not None:
        minx, miny, maxx, maxy = geom.bounds
        half = max(half,
                   (maxx - minx) * m_lon / 2 + margin_m,
                   (maxy - miny) * m_lat / 2 + margin_m)
        lon = (minx + maxx) / 2
        lat = (miny + maxy) / 2
    return [lon - half / m_lon, lat - half / m_lat, lon + half / m_lon, lat + half / m_lat]


def _load_init_ee() -> Callable[[], str]:
    """Reuse Crop_Monitoring/src/_bootstrap.py:init_ee without importing the
    ``src`` package (whose __init__ pulls in the whole pipeline)."""
    import importlib.util

    path = REPO_ROOT / "Crop_Monitoring" / "src" / "_bootstrap.py"
    if path.exists():
        spec = importlib.util.spec_from_file_location("_cm_bootstrap", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.init_ee

    def _init() -> str:  # fallback: same logic, reading backend/Credit_assessment/.env
        import ee
        backend = REPO_ROOT / "backend" / "Credit_assessment"
        env = {}
        envp = backend / ".env"
        if envp.exists():
            for raw in envp.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        project = os.environ.get("GEE_PROJECT") or env.get("GEE_PROJECT")
        key_path = os.environ.get("GEE_SA_KEY_PATH") or env.get("GEE_SA_KEY_PATH")
        if not project or not key_path:
            raise RuntimeError("GEE_PROJECT / GEE_SA_KEY_PATH missing")
        kp = Path(key_path)
        if not kp.is_absolute():
            kp = backend / kp
        sa = json.loads(kp.read_text(encoding="utf-8"))["client_email"]
        ee.Initialize(ee.ServiceAccountCredentials(sa, str(kp)), project=project)
        return project

    return _init


class EEChipSource:
    """Builds Earth Engine thumbnail URLs for monthly cloud-masked medians."""

    def __init__(self, ee_module=None, initialise: bool = True,
                 cs_threshold: float = CS_THRESHOLD, stretch: Mapping = STRETCH):
        if ee_module is None:
            import ee as ee_module  # noqa: N813
            if initialise:
                _load_init_ee()()
        self.ee = ee_module
        self.cs_threshold = cs_threshold
        self.stretch = stretch

    def _collection(self, region, start: str, end: str):
        ee = self.ee
        thr = self.cs_threshold
        s2 = ee.ImageCollection(S2_COLLECTION).filterBounds(region).filterDate(start, end)
        cs = ee.ImageCollection(CS_COLLECTION)
        return s2.linkCollection(cs, [CS_BAND]).map(
            lambda img: img.updateMask(img.select(CS_BAND).gte(thr)))

    def month_counts(self, box: Sequence[float], months) -> List[int]:
        ee = self.ee
        region = ee.Geometry.Rectangle(list(box))
        sizes = ee.List([
            ee.ImageCollection(S2_COLLECTION).filterBounds(region).filterDate(s, e).size()
            for _, s, e in months])
        return [int(x) for x in sizes.getInfo()]

    def thumb_url(self, geom_geojson: Mapping, box: Sequence[float], start: str, end: str,
                  kind: str, dimensions: int = 256) -> str:
        ee = self.ee
        st = self.stretch[kind]
        region = ee.Geometry.Rectangle(list(box))
        img = self._collection(region, start, end).median()
        vis = img.visualize(bands=st["bands"], min=st["min"], max=st["max"],
                            gamma=st["gamma"])
        background = (ee.Image.constant([40, 40, 40]).toByte()
                      .rename(["vis-red", "vis-green", "vis-blue"]))
        outline = (ee.Image().byte()
                   .paint(ee.FeatureCollection([ee.Feature(ee.Geometry(json.loads(json.dumps(geom_geojson))))]), 1, 2)
                   .visualize(palette=[st["outline"]]))
        out = background.blend(vis).blend(outline)
        return out.getThumbURL({"region": region, "dimensions": int(dimensions),
                                "format": "png"})


def _default_fetch(url: str) -> bytes:
    import requests

    last = None
    for _ in range(3):
        try:
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            return r.content
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"thumbnail download failed: {last}")


def _contact_sheet_html(sid: str, lat: float, lon: float, months, cells, stretch) -> str:
    esc = html.escape
    head = "".join(f"<th>{esc(m)}</th>" for m, _, _ in months)
    rows = []
    for kind in ("tc", "fc"):
        tds = []
        for m, _, _ in months:
            c = cells.get((kind, m), {})
            if c.get("file"):
                tds.append(f'<td><img src="{esc(c["file"])}" width="200" '
                           f'alt="{esc(kind)} {esc(m)}"><div class="n">{c.get("n_scenes", "?")} scenes</div></td>')
            else:
                tds.append(f'<td class="none">{esc(c.get("reason", "no image"))}</td>')
        rows.append(f"<tr><th>{esc(stretch[kind]['label'])}</th>{''.join(tds)}</tr>")
    gmaps = f"https://www.google.com/maps/@{lat},{lon},300m/data=!3m1!1e3"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(sid)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:16px}}
table{{border-collapse:collapse}} td,th{{border:1px solid #444;padding:4px;text-align:center;vertical-align:top}}
td.none{{color:#888;width:200px}} .n{{font-size:11px;color:#aaa}} a{{color:#8cf}}
</style></head><body>
<h2>{esc(sid)}</h2>
<p>Centre {lat:.6f}, {lon:.6f}. Field outline drawn in yellow (true colour) / cyan (false colour).
Monthly median of Sentinel-2 L2A with Cloud Score+ {CS_BAND} &ge; {CS_THRESHOLD}; dark grey = masked.
Fixed stretch for all chips. View-only basemap: <a href="{esc(gmaps)}" target="_blank" rel="noopener">satellite view</a>.</p>
<table><tr><th></th>{head}</tr>{''.join(rows)}</table>
</body></html>
"""


def make_chips(sample: pd.DataFrame, out_dir: Union[str, Path], season_start: str,
               season_end: str, source=None, fetch: Optional[Callable[[str], bytes]] = None,
               dimensions: int = 256, box_m: float = 600.0,
               overwrite: bool = False) -> pd.DataFrame:
    """Fetch monthly true/false colour chips and write a contact sheet per field.

    ``sample`` needs lat, lon and geometry_wkt; chips are written under
    ``out_dir/<sample_id>`` (or field_id when there is no sample_id). Pass a
    fake ``source`` / ``fetch`` in tests. Failures are recorded per chip and
    never abort the run.
    """
    from shapely import wkt as shp_wkt
    from shapely.geometry import mapping

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    months = months_between(season_start, season_end)
    src = source if source is not None else EEChipSource()
    fetch = fetch or _default_fetch
    id_col = "sample_id" if "sample_id" in sample.columns else "field_id"
    records = []
    index_rows = []
    for _, row in sample.iterrows():
        sid = str(row[id_col])
        d = out / sid
        d.mkdir(parents=True, exist_ok=True)
        geom = shp_wkt.loads(row["geometry_wkt"]) if not is_blank(row.get("geometry_wkt")) else None
        lat, lon = float(row["lat"]), float(row["lon"])
        box = chip_box(lat, lon, geom, box_m)
        gj = mapping(geom) if geom is not None else {"type": "Point", "coordinates": [lon, lat]}
        try:
            counts = src.month_counts(box, months)
        except Exception as exc:  # noqa: BLE001
            counts = [None] * len(months)
            records.append({"id": sid, "kind": "all", "month": "all", "status": "error",
                            "error": f"scene count failed: {exc}"})
        cells = {}
        for kind in ("tc", "fc"):
            for (m, s, e), n in zip(months, counts):
                fname = f"{kind}_{m}.png"
                fpath = d / fname
                rec = {"id": sid, "kind": kind, "month": m, "n_scenes": n, "file": None,
                       "status": None, "error": None}
                if n == 0:
                    rec["status"] = "no_scene"
                    cells[(kind, m)] = {"reason": "no Sentinel-2 scene"}
                elif fpath.exists() and not overwrite:
                    rec.update(status="cached", file=str(fpath))
                    cells[(kind, m)] = {"file": fname, "n_scenes": n}
                else:
                    try:
                        url = src.thumb_url(gj, box, s, e, kind, dimensions)
                        data = fetch(url)
                        fpath.write_bytes(data)
                        rec.update(status="ok", file=str(fpath))
                        cells[(kind, m)] = {"file": fname, "n_scenes": n}
                    except Exception as exc:  # noqa: BLE001
                        rec.update(status="error", error=str(exc)[:300])
                        cells[(kind, m)] = {"reason": "fetch failed"}
                records.append(rec)
        sheet = _contact_sheet_html(sid, lat, lon, months, cells,
                                    getattr(src, "stretch", STRETCH))
        (d / "contact_sheet.html").write_text(sheet, encoding="utf-8")
        index_rows.append(f'<li><a href="{html.escape(sid)}/contact_sheet.html">{html.escape(sid)}</a></li>')
    (out / "index.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>D1 chips</title></head>"
        f"<body><h1>D1 interpretation chips</h1><ul>{''.join(index_rows)}</ul></body></html>",
        encoding="utf-8")
    manifest = {
        "season_start": season_start, "season_end": season_end,
        "months": [m for m, _, _ in months], "collection": S2_COLLECTION,
        "cloud_mask": {"collection": CS_COLLECTION, "band": CS_BAND,
                       "threshold": getattr(src, "cs_threshold", CS_THRESHOLD)},
        "composite": "monthly median", "box_m_min": box_m, "dimensions": dimensions,
        "stretch": getattr(src, "stretch", STRETCH),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Label import and agreement
# ---------------------------------------------------------------------------
def _read(obj) -> pd.DataFrame:
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return obj.copy()
    return pd.read_csv(obj, dtype=str, keep_default_na=False)


def _prep_sheet(df: pd.DataFrame, tag: str) -> Tuple[pd.DataFrame, List[dict]]:
    issues = []
    for c in ("sample_id", "label"):
        if c not in df.columns:
            raise ValueError(f"sheet {tag} lacks column '{c}'")
    df = df.copy()
    df["sample_id"] = df["sample_id"].astype(str).str.strip()
    dup = df["sample_id"][df["sample_id"].duplicated()].tolist()
    if dup:
        issues.append({"sheet": tag, "issue": "duplicate sample_id", "sample_ids": dup})
        df = df.drop_duplicates("sample_id", keep="first")
    df[f"label_{tag}"] = df["label"].map(normalise_label)
    conf_raw = df["confidence"] if "confidence" in df.columns else pd.Series([""] * len(df), index=df.index)
    df[f"confidence_{tag}"] = conf_raw.map(normalise_confidence)
    bad_conf = df[(~conf_raw.map(is_blank)) & df[f"confidence_{tag}"].isna()]["sample_id"].tolist()
    if bad_conf:
        issues.append({"sheet": tag, "issue": "confidence not high/medium/low", "sample_ids": bad_conf})
    notes = df["notes"] if "notes" in df.columns else ""
    df[f"notes_{tag}"] = notes
    return df[["sample_id", f"label_{tag}", f"confidence_{tag}", f"notes_{tag}"]], issues


def import_labels(sheet_a, sheet_b, key, sheet_c=None, min_kappa: float = 0.70
                  ) -> Tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Combine two blind interpretations (+ optional adjudication sheet).

    Returns
    -------
    summary : JSON-ready dict (kind ``d1_agreement``) with overall and
        per-class kappa, counts and ``usable_for_gating``.
    disagreements : rows needing adjudication (both labels, confidences, notes).
    reference : final reference table (sample_id, field_id, stratum,
        map_class, reference_label, resolution, reference_confidence).
        Rows that are unresolved or unlabelled have reference_label empty.
    """
    a, ia = _prep_sheet(_read(sheet_a), "a")
    b, ib = _prep_sheet(_read(sheet_b), "b")
    k = _read(key)
    k["sample_id"] = k["sample_id"].astype(str).str.strip()
    issues = ia + ib
    df = k.merge(a, on="sample_id", how="left").merge(b, on="sample_id", how="left")
    extra = sorted((set(a["sample_id"]) | set(b["sample_id"])) - set(k["sample_id"]))
    if extra:
        issues.append({"sheet": "a/b", "issue": "sample_id not in key", "sample_ids": extra})

    both = df["label_a"].notna() & df["label_b"].notna()
    ka = df.loc[both, "label_a"].tolist()
    kb = df.loc[both, "label_b"].tolist()
    kappa = metrics.cohen_kappa(ka, kb)
    per_cls = metrics.per_class_kappa(ka, kb) if ka else {}
    agree = both & (df["label_a"] == df["label_b"])
    disagree = both & (df["label_a"] != df["label_b"])

    df["label_c"] = None
    df["confidence_c"] = None
    df["notes_c"] = None
    if sheet_c is not None:
        c, ic = _prep_sheet(_read(sheet_c), "c")
        issues += ic
        df = df.drop(columns=["label_c", "confidence_c", "notes_c"]).merge(c, on="sample_id", how="left")
        ignored = df.loc[agree & df["label_c"].notna(), "sample_id"].tolist()
        if ignored:
            issues.append({"sheet": "c", "issue": "adjudication given for agreed items (ignored)",
                           "sample_ids": ignored})

    ref_label, resolution, ref_conf = [], [], []
    for i, r in df.iterrows():
        if agree[i]:
            ref_label.append(r["label_a"])
            resolution.append("agreed")
            ref_conf.append(min_confidence(r["confidence_a"], r["confidence_b"]))
        elif disagree[i] and r.get("label_c") is not None and not is_blank(r.get("label_c")):
            ref_label.append(r["label_c"])
            resolution.append("adjudicated")
            ref_conf.append(r.get("confidence_c") or None)
        elif disagree[i]:
            ref_label.append(None)
            resolution.append("unresolved")
            ref_conf.append(None)
        else:
            ref_label.append(None)
            resolution.append("missing_label")
            ref_conf.append(None)
    df["reference_label"] = ref_label
    df["resolution"] = resolution
    df["reference_confidence"] = ref_conf
    # "Unclear" is not a usable reference class
    unclear = df["reference_label"] == "Unclear"
    df.loc[unclear, "resolution"] = "unclear"
    df.loc[unclear, "reference_label"] = None

    dis = df.loc[disagree, ["sample_id", "label_a", "confidence_a", "notes_a",
                            "label_b", "confidence_b", "notes_b", "label_c"]].reset_index(drop=True)
    keep = [c for c in ("sample_id", "field_id", "stratum", "map_class", "area_ha",
                        "reference_label", "resolution", "reference_confidence",
                        "label_a", "label_b", "label_c") if c in df.columns]
    reference = df[keep].reset_index(drop=True)

    usable = bool(kappa["n"] > 0 and not math.isnan(kappa["kappa"]) and kappa["kappa"] >= min_kappa)
    if kappa["n"] == 0:
        reason = "no item labelled by both interpreters"
    elif not usable:
        reason = f"kappa {kappa['kappa']:.3f} < {min_kappa}: D1 not used for gating"
    else:
        reason = f"kappa {kappa['kappa']:.3f} >= {min_kappa}"
    counts = reference["resolution"].value_counts().to_dict()
    summary = {
        "kind": "d1_agreement",
        "n_items": int(len(df)),
        "n_both_labelled": int(both.sum()),
        "n_agreed": int(agree.sum()),
        "n_disagreements": int(disagree.sum()),
        "n_adjudicated": int(counts.get("adjudicated", 0)),
        "n_unresolved": int(counts.get("unresolved", 0)),
        "n_missing_label": int(counts.get("missing_label", 0)),
        "n_unclear": int(counts.get("unclear", 0)),
        "n_reference": int(reference["reference_label"].notna().sum()),
        "kappa": kappa,
        "per_class_kappa": per_cls,
        "min_kappa": min_kappa,
        "usable_for_gating": usable,
        "reason": reason,
        "reference_complete": int(counts.get("unresolved", 0)) == 0,
        "issues": issues,
    }
    return summary, dis, reference
