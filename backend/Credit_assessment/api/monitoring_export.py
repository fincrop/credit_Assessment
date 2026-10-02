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


def monitoring_csv(result: Dict[str, Any]) -> str:
    """Zone summary, then one row per observation interval."""
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
    draw.text((x, y), "Zones", fill=(87, 83, 78))
    y += 28
    seen = set()
    for feat in (result.get("fields") or {}).get("features") or []:
        props = feat.get("properties") or {}
        key = str(props.get("crop") or "")
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
