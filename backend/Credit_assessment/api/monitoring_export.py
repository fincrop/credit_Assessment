"""Downloads for a completed monitoring run.

GeoJSON, CSV and the full JSON document are built in the browser. Shapefile,
GeoTIFF and PNG need geopandas, rasterio and Pillow, same as classification.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
import zipfile
from typing import Any, Dict

from api.classification_export import (
    _classified_raster,
    _fields_gdf,
    _hex_rgba,
    result_to_geotiff,
)


RECORD_COLUMNS = (
    "field_id", "crop", "status", "area_ha", "pixel_basis", "n_interior_pixels", "low_resolution",
    "n_clear_looks", "last_clear_observation", "phenology_status",
    "sowing_status", "sowing_date", "sowing_p10", "sowing_p90", "sowing_sources", "sowing_regime",
    "das", "stage", "harvest_observed", "harvest_date", "harvest_window_start", "harvest_window_end",
    "stress_latest_class", "stress_latest_type", "stress_confirmed", "stress_looks_stressed",
    "stress_reference", "yield_basis", "yield_index", "yield_index_p10", "yield_index_p90",
    "yield_t_ha", "yield_p10", "yield_p90", "yield_baseline_source", "phenology_best_alternative",
    "phenology_ratio", "qa_flags",
)


def _record_row(r: Dict[str, Any]) -> list:
    s, st, y = r.get("sowing") or {}, r.get("stress") or {}, r.get("yield") or {}
    h, pc = r.get("harvest") or {}, r.get("phenology_check") or {}
    w = h.get("window") or {}
    vals = {
        "field_id": r.get("field_id"), "crop": r.get("crop"), "status": r.get("status"),
        "area_ha": r.get("area_ha"), "pixel_basis": r.get("pixel_basis"),
        "n_interior_pixels": r.get("n_interior_pixels"), "low_resolution": r.get("low_resolution"),
        "n_clear_looks": r.get("n_clear_looks"), "last_clear_observation": r.get("last_clear_observation"),
        "phenology_status": r.get("phenology_status"), "sowing_status": s.get("status"),
        "sowing_date": s.get("date"), "sowing_p10": s.get("p10"), "sowing_p90": s.get("p90"),
        "sowing_sources": ";".join(s.get("sources") or []), "sowing_regime": s.get("regime"),
        "das": r.get("das"), "stage": r.get("stage"), "harvest_observed": h.get("observed"),
        "harvest_date": h.get("date"), "harvest_window_start": w.get("start"),
        "harvest_window_end": w.get("end"),
        "stress_latest_class": st.get("latest_class"), "stress_latest_type": st.get("latest_type"),
        "stress_confirmed": st.get("latest_confirmed"), "stress_looks_stressed": st.get("looks_stressed"),
        "stress_reference": st.get("reference"), "yield_basis": y.get("basis"),
        "yield_index": y.get("yield_index"), "yield_index_p10": y.get("index_p10"),
        "yield_index_p90": y.get("index_p90"), "yield_t_ha": y.get("yield_t_ha"),
        "yield_p10": y.get("yield_p10"), "yield_p90": y.get("yield_p90"),
        "yield_baseline_source": y.get("baseline_source"),
        "phenology_best_alternative": pc.get("best_alternative") if pc.get("disagrees") else "",
        "phenology_ratio": pc.get("ratio"), "qa_flags": ";".join(r.get("qa_flags") or []),
    }
    return ["" if vals[c] is None else vals[c] for c in RECORD_COLUMNS]


def monitoring_csv(result: Dict[str, Any]) -> str:
    """Raster engine: one row per field record (plan section 7). Point engine:
    zone summary, then one row per observation interval."""
    if result.get("records"):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(RECORD_COLUMNS)
        for r in result["records"]:
            w.writerow(_record_row(r))
        w.writerow([])
        w.writerow(["# Provenance"])
        for key in ("name", "season", "as_of", "engine", "farm_count"):
            w.writerow([key, result.get(key)])
        onset = result.get("onset") or {}
        w.writerow(["monsoon_onset", onset.get("date")])
        for line in result.get("limits") or []:
            w.writerow(["limit", line])
        return buf.getvalue()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([
        "zone_id", "crop", "kind", "area_share", "pixel_count",
        "sowing_date", "sowing_early", "sowing_late", "sowing_confidence", "sowing_sources",
        "split_reason",
        "stage", "tau", "harvest", "harvest_observed", "duration_days", "duration_outlier",
        "yield_t_ha", "yield_low", "yield_high", "reference_pool",
    ])
    for zone in result.get("zones") or []:
        if zone.get("kind") == "non_crop":
            continue
        sowing = zone.get("sowing") or {}
        harvest = zone.get("harvest") or {}
        progress = zone.get("progress") or {}
        yld = zone.get("yield") or {}
        w.writerow([
            zone.get("zone_id"),
            zone.get("crop"),
            zone.get("kind"),
            zone.get("area_share"),
            zone.get("pixel_count"),
            sowing.get("date"),
            sowing.get("early"),
            sowing.get("late"),
            sowing.get("confidence"),
            ";".join(sowing.get("sources") or []),
            zone.get("split_reason"),
            progress.get("stage"),
            progress.get("tau"),
            harvest.get("date") or progress.get("harvest"),
            harvest.get("observed"),
            progress.get("duration_days"),
            progress.get("duration_outlier"),
            yld.get("t_ha"),
            yld.get("low"),
            yld.get("high"),
            yld.get("reference_pool"),
        ])

    w.writerow([])
    w.writerow(["# Intervals"])
    w.writerow([
        "zone_id", "date", "kind", "stage", "tau", "cover", "water",
        "biomass_kg_ha", "uncertainty", "stress", "stressed_fraction",
        "nitrogen_score", "nitrogen_band",
    ])
    for zone in result.get("zones") or []:
        for item in zone.get("intervals") or []:
            stress = item.get("stress") or {}
            nitrogen = item.get("nitrogen") or {}
            w.writerow([
                zone.get("zone_id"),
                item.get("date"),
                item.get("kind"),
                item.get("stage"),
                item.get("tau"),
                item.get("cover"),
                item.get("water"),
                item.get("biomass_kg_ha"),
                item.get("uncertainty"),
                stress.get("type"),
                stress.get("stressed_fraction"),
                nitrogen.get("score"),
                nitrogen.get("band"),
            ])

    w.writerow([])
    w.writerow(["# Provenance"])
    for key in ("name", "crop", "season", "as_of", "confidence", "typed", "reference_pool", "zone_count"):
        w.writerow([key, result.get(key)])
    return buf.getvalue()


def monitoring_shapefile_zip(result: Dict[str, Any]) -> bytes:
    gdf = _fields_gdf(result)
    wanted = [
        c for c in (
            "field_id", "zone_id", "crop", "crop_name", "stress",
            "area_ha", "confidence", "sowing_date", "harvest_date", "split_reason", "stage", "yield_t_ha",
        ) if c in gdf.columns
    ]
    geom = gdf.geometry.name
    slim = gdf.loc[:, wanted + ([geom] if geom not in wanted else [])].copy()
    slim = slim.rename(columns={
        "crop_name": "crop_nm",
        "confidence": "conf",
        "sowing_date": "sowing",
        "harvest_date": "harvest",
        "split_reason": "split",
        "yield_t_ha": "yield_t",
        "field_id": "zone",
    })

    with tempfile.TemporaryDirectory() as tmp:
        stem = os.path.join(tmp, "crop_monitoring")
        slim.to_file(f"{stem}.shp", driver="ESRI Shapefile", encoding="utf-8")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(os.listdir(tmp)):
                zf.write(os.path.join(tmp, name), arcname=name)
            zf.writestr("monitoring.csv", monitoring_csv(result))
        return buf.getvalue()


def monitoring_png(result: Dict[str, Any]) -> bytes:
    """PNG coloured from each zone's own stress colour, not the classification palette."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ImportError("Pillow is not installed on this service") from exc
    import numpy as np

    labels, _transform, _crs, codes, _pixel = _classified_raster(result, max_side=1400)
    id_to_name = {cid: name for name, cid in codes.items()}
    color_of = {}
    for feat in (result.get("fields") or {}).get("features") or []:
        props = feat.get("properties") or {}
        if props.get("crop") and props.get("color"):
            color_of[str(props["crop"])] = str(props["color"])

    h, w = labels.shape
    rgb = np.empty((h, w, 3), dtype="uint8")
    rgb[:] = (231, 225, 214)
    for cid in np.unique(labels):
        cid = int(cid)
        if cid == 0:
            continue
        rgb[labels == cid] = _hex_rgba(color_of.get(id_to_name.get(cid, ""), "#2E7D4F"))[:3]

    map_img = Image.fromarray(rgb, mode="RGB")
    target = 1100
    scale = target / max(map_img.size)
    if abs(scale - 1.0) > 0.05:
        map_img = map_img.resize(
            (max(1, int(map_img.width * scale)), max(1, int(map_img.height * scale))),
            Image.NEAREST,
        )

    pad, title_h, legend_w = 24, 52, 280
    canvas = Image.new("RGB", (pad + map_img.width + pad + legend_w + pad, pad + title_h + map_img.height + 36), (247, 244, 238))
    canvas.paste(map_img, (pad, pad + title_h))
    draw = ImageDraw.Draw(canvas)
    name = str(result.get("name") or result.get("crop") or "Crop monitoring")
    season = str(result.get("season") or "")
    draw.text((pad, 16), f"{name} · {season}".strip(" ·"), fill=(28, 25, 23))

    x = pad + map_img.width + pad
    y = pad + title_h
    draw.text((x, y), "Stress (latest clear look)", fill=(87, 83, 78))
    y += 28
    seen = set()
    for feat in (result.get("fields") or {}).get("features") or []:
        props = feat.get("properties") or {}
        # Polygons are coloured by stress, so the legend must be keyed on stress.
        # Keying on `crop` (e.g. "2-z1 · Cotton") drew a legend unrelated to the map.
        key = str(props.get("stress") or props.get("crop") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        draw.rectangle((x, y, x + 14, y + 14), fill=_hex_rgba(str(props.get("color") or "#2E7D4F"))[:3])
        draw.text((x + 22, y), key[:32], fill=(28, 25, 23))
        y += 22
    return _png_bytes(canvas)


def monitoring_geotiff(result: Dict[str, Any]) -> bytes:
    return result_to_geotiff(result)


def _png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def monitoring_report_card(result: Dict[str, Any], field_id: str, raster_dir: str | None) -> bytes:
    """One farm, one page (plan section 5.8, bank report export).

    Map: the field outline over the stress-class raster of the latest clear look
    that scored this field (pixels hatched where not observed). Text: the field
    record, the data-quality line, and the limitation labels. Nothing on the
    card is computed here; it only lays out the record.
    """
    from PIL import Image, ImageDraw

    rec = next((r for r in result.get("records") or [] if str(r.get("field_id")) == str(field_id)), None)
    if rec is None:
        raise ValueError(f"No record for field {field_id}")
    feature = next((f for f in (result.get("fields") or {}).get("features") or []
                    if str((f.get("properties") or {}).get("field_id")) == str(field_id)), None)
    W, H = 1240, 1754                       # A4 portrait at 150 dpi
    page = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(page)
    y = 50
    d.text((60, y), f"{result.get('name') or 'Crop monitoring'} - field {field_id}", fill=(20, 20, 20))
    y += 30
    d.text((60, y), f"Season {result.get('season')}  |  as of {result.get('as_of')}  |  "
                    f"engine {result.get('engine')}", fill=(90, 90, 90))
    y += 40

    map_box = (60, y, W - 60, y + 620)
    snap = _field_snapshot(result, rec, feature, raster_dir,
                           (map_box[2] - map_box[0], map_box[3] - map_box[1]))
    if snap is not None:
        page.paste(snap, (map_box[0], map_box[1]))
    else:
        d.rectangle(map_box, outline=(180, 180, 180))
        d.text((map_box[0] + 20, map_box[1] + 20), "No scored clear look for this field yet.",
               fill=(90, 90, 90))
    y = map_box[3] + 30

    s, st, yv = rec.get("sowing") or {}, rec.get("stress") or {}, rec.get("yield") or {}
    h = rec.get("harvest") or {}
    win = h.get("window") or {}
    if s.get("date"):
        sowing = (f"{s.get('date')}  (P10 {s.get('p10')} - P90 {s.get('p90')}; "
                  f"{', '.join(s.get('sources') or [])})")
    else:
        sowing = f"{s.get('status')}: window {s.get('p10')} - {s.get('p90')}"
    if st.get("status") == "scored":
        cond = (f"{st.get('latest_class')} {('- ' + st['latest_type']) if st.get('latest_type') else ''}"
                f"{' (confirmed)' if st.get('latest_confirmed') else ''}  on {st.get('latest_date')}")
    else:
        cond = "condition unavailable"
    if yv.get("yield_t_ha") is not None:
        yld = f"{yv.get('yield_t_ha')} t/ha (P10 {yv.get('yield_p10')} - P90 {yv.get('yield_p90')})"
    elif yv.get("yield_index") is not None:
        yld = (f"index {yv.get('yield_index')} of village median "
               f"(P10 {yv.get('index_p10')} - P90 {yv.get('index_p90')})")
    else:
        yld = str(yv.get("note") or yv.get("basis"))
    rows = [
        ("Crop", f"{rec.get('crop')}   (status: {rec.get('status')})"),
        ("Area", f"{rec.get('area_ha')} ha"),
        ("Sowing", sowing),
        ("Monsoon onset", str(s.get("onset"))),
        ("Stage as of run", f"{rec.get('stage')}  ({rec.get('das')} days after sowing)"),
        ("Harvest", f"observed {h.get('date')}" if h.get("observed")
         else f"window {win.get('start')} - {win.get('end')}"),
        ("Condition", cond),
        ("Yield", yld),
        ("Yield basis", str(yv.get("label") or yv.get("basis") or "")),
        ("Data quality", f"{rec.get('n_clear_looks')} clear looks, last {rec.get('last_clear_observation')}; "
                         f"pixels: {rec.get('pixel_basis')} ({rec.get('n_interior_pixels')} interior)"),
        ("Checks", ", ".join(rec.get("qa_flags") or []) or "none raised"),
    ]
    for k, v in rows:
        d.text((60, y), k, fill=(90, 90, 90))
        d.text((300, y), str(v)[:120], fill=(20, 20, 20))
        y += 34
    y += 20
    d.text((60, y), "Limits", fill=(90, 90, 90))
    y += 28
    for line in result.get("limits") or []:
        d.text((60, y), "- " + line[:150], fill=(60, 60, 60))
        y += 26
    return _png_bytes(page)


def _field_snapshot(result, rec, feature, raster_dir, size):
    """Crop the latest scored stress-class COG around the field and draw the outline."""
    if not raster_dir or feature is None:
        return None
    import json as _json
    import sys
    from pathlib import Path

    import numpy as np
    from PIL import Image, ImageDraw

    root = Path(raster_dir)
    index = root / "products.json"
    if not index.exists():
        return None
    items = _json.loads(index.read_text(encoding="utf-8")).get("products") or []
    scored = {iv.get("date") for z in result.get("zones") or []
              if str(z.get("source_field_id")) == str(rec.get("field_id"))
              for iv in z.get("intervals") or []
              if (iv.get("stress") or {}).get("class") not in (None, "pre_sowing")}
    cands = sorted((i for i in items if i["product"] == "stress_class" and i.get("date") in scored),
                   key=lambda i: i["date"])
    if not cands:
        return None
    item = cands[-1]
    mon = Path(__file__).resolve().parents[3] / "Crop_Monitoring"
    if str(mon) not in sys.path:
        sys.path.insert(0, str(mon))
    import src._bootstrap  # noqa: F401  (PROJ/GDAL data before rasterio)
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import from_bounds
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform
    from src.raster.products import STRESS_CLASSES, colorize_classes

    with rasterio.open(root / item["cog"]) as src:
        to = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform
        geom = shp_transform(to, shape(feature["geometry"]))
        xmin, ymin, xmax, ymax = geom.bounds
        pad = max(xmax - xmin, ymax - ymin) * 0.6 + 30
        win = from_bounds(xmin - pad, ymin - pad, xmax + pad, ymax + pad, src.transform)
        arr = src.read(1, window=win, boundless=True, fill_value=np.nan)
        wt = src.window_transform(win)

    rgba = colorize_classes(arr, STRESS_CLASSES)
    img = Image.fromarray(rgba, "RGBA").convert("RGB")
    scale = min(size[0] / img.width, (size[1] - 60) / img.height)
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.NEAREST)
    draw = ImageDraw.Draw(img)
    inv = ~wt
    rings = [geom.exterior] if geom.geom_type == "Polygon" else [g.exterior for g in geom.geoms]
    for ring in rings:
        pts = []
        for x, y_ in ring.coords:
            c, r = inv * (x, y_)
            pts.append((c * scale, r * scale))
        draw.line(pts + [pts[0]], fill=(0, 0, 0), width=3)
    canvas = Image.new("RGB", size, (255, 255, 255))
    canvas.paste(img, (0, 0))
    d = ImageDraw.Draw(canvas)
    d.text((0, img.height + 8), f"Stress class, Sentinel-2 {item['date']}  |  clear pixels in fields "
                                 f"{item.get('clear_fraction')}  |  hatched = not observed", fill=(40, 40, 40))
    x = 0
    for _, (label, color) in STRESS_CLASSES.items():
        d.rectangle((x, img.height + 32, x + 14, img.height + 46), fill=color)
        d.text((x + 20, img.height + 32), label, fill=(40, 40, 40))
        x += 140
    return canvas
