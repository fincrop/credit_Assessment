"""
Download products for a completed area classification.

Only the formats the browser cannot build itself need to live here. The
frontend writes GeoJSON and CSV from the result it already holds, so these
implementations exist for service-to-service use and as a fallback -- the
common path does not depend on this module being reachable.

GeoTIFF and PNG are burned from the field polygons: this pipeline classifies
SNIC objects and vectorises them, so there is no per-pixel model raster to
emit. A 10 m label grid (Sentinel-2 native scale) where each pixel takes the
crop of the field it falls in *is* the classified raster for this product.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
import tempfile
import zipfile
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# 10 m matches TARGET_SCALE_M in area_classifier. Cap the long side so a
# 50,000 ha strip cannot allocate a multi-GB array.
_GEOTIFF_PIXEL_M = 10.0
_GEOTIFF_MAX_SIDE = 8192


def result_to_csv(result: Dict[str, Any]) -> str:
    """Per-field rows, then the per-class summary under a comment header.

    One file rather than two because the pair is always wanted together, and a
    reader that stops at the blank line still gets a valid per-field table.
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["field_id", "crop", "area_ha", "confidence", "lon", "lat", "note"])

    features: List[Dict[str, Any]] = (result.get("fields") or {}).get("features") or []
    for i, f in enumerate(features, 1):
        p = f.get("properties") or {}
        c = p.get("centroid") or {}
        w.writerow([
            p.get("field_id", i),
            p.get("crop", "Unclassified"),
            f'{float(p.get("area_ha") or 0.0):.4f}',
            f'{float(p.get("confidence") or 0.0):.4f}',
            f'{float(c.get("lng") or 0.0):.6f}',
            f'{float(c.get("lat") or 0.0):.6f}',
            p.get("note", ""),
        ])

    w.writerow([])
    w.writerow(["# Summary"])
    w.writerow(["crop", "field_count", "area_ha", "area_share", "mean_confidence"])
    for s in result.get("stats") or []:
        w.writerow([
            s.get("crop"),
            s.get("field_count"),
            f'{float(s.get("area_ha") or 0.0):.3f}',
            f'{float(s.get("area_share") or 0.0):.4f}',
            f'{float(s.get("mean_confidence") or 0.0):.4f}',
        ])

    w.writerow([])
    w.writerow(["# Provenance"])
    for key in ("aoi_name", "season", "year", "total_area_ha", "classified_area_ha",
                "unclassified_area_ha", "field_count", "mean_confidence", "model_version"):
        w.writerow([key, result.get(key)])
    return buf.getvalue()


def _fields_gdf(result: Dict[str, Any]):
    """GeoDataFrame of classified fields in EPSG:4326.

    Raises ImportError when geopandas is absent, ValueError when there is
    nothing to draw.
    """
    try:
        import geopandas as gpd  # noqa: PLC0415
    except ImportError as exc:                               # pragma: no cover
        raise ImportError("geopandas is not installed on this service") from exc

    fc = result.get("fields") or {"type": "FeatureCollection", "features": []}
    feats = fc.get("features") or []
    if not feats:
        raise ValueError("No fields to export")

    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError("No fields to export")
    gdf = gdf.copy()
    # Vectorised GEE rings are occasionally self-touching; buffer(0) is the
    # usual repair and rasterio refuses to burn an invalid polygon.
    gdf["geometry"] = gdf.geometry.buffer(0)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError("No fields to export")
    if "crop" not in gdf.columns:
        gdf["crop"] = "Unclassified"
    gdf["crop"] = gdf["crop"].fillna("Unclassified").astype(str)
    return gdf.reset_index(drop=True)


def result_to_shapefile_zip(result: Dict[str, Any]) -> bytes:
    """Zip a shapefile written from the field FeatureCollection.

    Raises ImportError when geopandas is absent, which the endpoint turns into
    a 501 rather than a 500 -- a missing optional dependency is a capability
    gap, not a server fault.
    """
    gdf = _fields_gdf(result)

    # Shapefile DBF caps field names at 10 characters and cannot hold a nested
    # value, so the centroid dict is flattened and long names shortened here
    # rather than letting the driver truncate them into collisions.
    if "centroid" in gdf.columns:
        gdf["cent_lon"] = gdf["centroid"].apply(
            lambda c: (c or {}).get("lng") if isinstance(c, dict) else None
        )
        gdf["cent_lat"] = gdf["centroid"].apply(
            lambda c: (c or {}).get("lat") if isinstance(c, dict) else None
        )
        gdf = gdf.drop(columns=["centroid"])
    gdf = gdf.rename(columns={
        "confidence": "conf",
        "cycle_duration_days": "cycle_days",
    })

    with tempfile.TemporaryDirectory() as tmp:
        stem = os.path.join(tmp, "crop_classification")
        gdf.to_file(f"{stem}.shp", driver="ESRI Shapefile")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(os.listdir(tmp)):
                zf.write(os.path.join(tmp, name), arcname=name)
            # The statistics do not fit the attribute table's one-row-per-field
            # shape, so they travel alongside it.
            zf.writestr("statistics.csv", result_to_csv(result))
        return buf.getvalue()


def _hex_rgba(color: str) -> Tuple[int, int, int, int]:
    h = (color or "#B0A89C").lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) < 6:
        h = "B0A89C"
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


def _stable_class_ids() -> Dict[str, int]:
    """Fixed crop → integer id so Rice is the same code on every download.

    0 is reserved for nodata / outside any field.
    """
    from crop_analysis.area_classifier import CROP_COLORS, NON_CROP_COLORS

    names = list(CROP_COLORS.keys()) + list(NON_CROP_COLORS.keys())
    return {name: i + 1 for i, name in enumerate(names)}


def _codes_for(gdf) -> Tuple[Dict[str, int], List[int]]:
    codes = _stable_class_ids()
    next_id = max(codes.values(), default=0) + 1
    for name in gdf["crop"].unique():
        if name not in codes:
            codes[name] = next_id
            next_id += 1
    return codes, [int(codes[c]) for c in gdf["crop"]]


def _season_label(season: Any) -> str:
    return str(season or "").replace("_", " ").strip().title()


def _classified_raster(
    result: Dict[str, Any],
    pixel_m: float = _GEOTIFF_PIXEL_M,
    max_side: int = _GEOTIFF_MAX_SIDE,
) -> Tuple[Any, Any, Any, Dict[str, int], float]:
    """Burn field polygons onto a class-id grid. Returns labels, transform, CRS, codes, pixel size."""
    try:
        from rasterio.features import rasterize
        from rasterio.transform import from_origin
    except ImportError as exc:                               # pragma: no cover
        raise ImportError("rasterio is not installed on this service") from exc

    gdf = _fields_gdf(result)
    codes, class_ids = _codes_for(gdf)

    try:
        utm = gdf.estimate_utm_crs()
    except Exception:                                        # noqa: BLE001
        utm = "EPSG:4326"
    gdf_utm = gdf.to_crs(utm)

    minx, miny, maxx, maxy = gdf_utm.total_bounds
    pixel = pixel_m
    if str(utm) in ("EPSG:4326", "OGC:CRS84"):
        # Degree-grid fallback (should not happen for Indian AOIs).
        pixel = pixel_m / 111_320.0

    pad = pixel * 2
    minx -= pad
    miny -= pad
    maxx += pad
    maxy += pad
    width = max(1, int(math.ceil((maxx - minx) / pixel)))
    height = max(1, int(math.ceil((maxy - miny) / pixel)))
    if max(width, height) > max_side:
        scale = max(width, height) / max_side
        pixel *= scale
        width = max(1, int(math.ceil((maxx - minx) / pixel)))
        height = max(1, int(math.ceil((maxy - miny) / pixel)))

    transform = from_origin(minx, maxy, pixel, pixel)
    shapes = ((geom, cid) for geom, cid in zip(gdf_utm.geometry, class_ids))
    labels = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    return labels, transform, gdf_utm.crs, codes, pixel


def _colorize(labels: Any, codes: Dict[str, int]) -> Any:
    import numpy as np
    from crop_analysis.area_classifier import class_color

    h, w = labels.shape
    rgb = np.empty((h, w, 3), dtype=np.uint8)
    rgb[:] = (231, 225, 214)  # unmapped / nodata
    id_to_name = {cid: name for name, cid in codes.items()}
    for cid in np.unique(labels):
        cid = int(cid)
        if cid == 0:
            continue
        r, g, b, _ = _hex_rgba(class_color(id_to_name.get(cid, "Unclassified")))
        rgb[labels == cid] = (r, g, b)
    return rgb


def result_to_geotiff(result: Dict[str, Any]) -> bytes:
    """Single-band paletted GeoTIFF: class id per 10 m pixel, UTM.

    Raises ImportError when rasterio/geopandas is missing, ValueError when
    there are no fields.
    """
    try:
        import rasterio
    except ImportError as exc:                               # pragma: no cover
        raise ImportError("rasterio is not installed on this service") from exc

    from crop_analysis.area_classifier import class_color

    labels, transform, crs, codes, pixel = _classified_raster(result)

    colormap = {0: (247, 244, 238, 0)}
    present = {int(v) for v in labels.ravel() if v}
    id_to_name = {cid: name for name, cid in codes.items()}
    for cid in present:
        colormap[cid] = _hex_rgba(class_color(id_to_name.get(cid, "Unclassified")))

    tags = {
        "AREA_OR_POINT": "Area",
        "CLASSIFICATION": "crop",
        "AOI_NAME": str(result.get("aoi_name") or ""),
        "SEASON": str(result.get("season") or ""),
        "YEAR": str(result.get("year") or ""),
        "MODEL_VERSION": str(result.get("model_version") or ""),
        "PIXEL_M": f"{pixel:.4f}",
    }
    for cid in sorted(present):
        tags[f"CLASS_{cid}"] = id_to_name.get(cid, f"class_{cid}")

    with rasterio.MemoryFile() as mem:
        with mem.open(
            driver="GTiff",
            height=labels.shape[0],
            width=labels.shape[1],
            count=1,
            dtype="uint8",
            crs=crs,
            transform=transform,
            nodata=0,
            compress="lzw",
            photometric="palette",
        ) as dst:
            dst.write(labels, 1)
            dst.write_colormap(1, colormap)
            dst.set_band_description(1, "crop class id")
            dst.update_tags(**tags)
        return mem.read()


def result_to_png(result: Dict[str, Any]) -> bytes:
    """Cartographic PNG of the classified fields with a colour legend.

    Built with Pillow from the same burned label grid as the GeoTIFF.
    Matplotlib is available in this stack but its Agg backend crashes on this
    Windows runtime (delay-load 0xc06d007f), so it is not used here.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:                               # pragma: no cover
        raise ImportError("Pillow is not installed on this service") from exc

    from crop_analysis.area_classifier import class_color

    labels, _transform, _crs, codes, _pixel = _classified_raster(result, max_side=1800)
    rgb = _colorize(labels, codes)

    map_img = Image.fromarray(rgb, mode="RGB")
    # Always present a readable overview: native 10 m pixels on a village AOI
    # are a few hundred cells, which would download as a stamp.
    target = 1200
    scale = target / max(map_img.size)
    if abs(scale - 1.0) > 0.05:
        map_img = map_img.resize(
            (max(1, int(map_img.width * scale)), max(1, int(map_img.height * scale))),
            Image.NEAREST,
        )

    pad = 28
    title_h = 56
    footer_h = 40
    legend_w = 260
    canvas_w = pad + map_img.width + pad + legend_w + pad
    canvas_h = pad + title_h + map_img.height + footer_h
    canvas = Image.new("RGB", (canvas_w, canvas_h), (247, 244, 238))
    canvas.paste(map_img, (pad, pad + title_h))

    draw = ImageDraw.Draw(canvas)
    title_font = _png_font(22)
    legend_title_font = _png_font(13)
    body_font = _png_font(14)
    footer_font = _png_font(12)

    title = " · ".join(
        p for p in (
            str(result.get("aoi_name") or "Classification"),
            f"{_season_label(result.get('season'))} {result.get('year') or ''}".strip(),
        ) if p
    )
    draw.text((pad, pad), title, fill=(41, 37, 36), font=title_font)

    stats = [s for s in (result.get("stats") or []) if float(s.get("area_ha") or 0) > 0]
    if not stats:
        present_ids = {int(v) for v in labels.ravel() if v}
        id_to_name = {cid: name for name, cid in codes.items()}
        stats = [{"crop": id_to_name[cid], "area_ha": None} for cid in sorted(present_ids) if cid in id_to_name]

    lx = pad + map_img.width + pad
    ly = pad + title_h
    draw.text((lx, ly - 8), "LEGEND", fill=(120, 113, 108), font=legend_title_font)
    ly += 22
    swatch = 14
    row_h = 26
    for s in stats:
        crop = str(s.get("crop") or "Unclassified")
        r, g, b, _ = _hex_rgba(class_color(crop))
        draw.rounded_rectangle((lx, ly + 2, lx + swatch, ly + 2 + swatch), radius=2, fill=(r, g, b))
        area = s.get("area_ha")
        label = f"{crop}  {float(area):.1f} ha" if area is not None else crop
        draw.text((lx + swatch + 8, ly), label, fill=(68, 64, 60), font=body_font)
        ly += row_h
        if ly > canvas_h - footer_h - 8:
            break

    n_fields = int(result.get("field_count") or 0)
    classified = result.get("classified_area_ha")
    footer_bits = [f"{n_fields} field{'s' if n_fields != 1 else ''}"]
    if classified is not None:
        footer_bits.append(f"{float(classified):.1f} ha classified")
    if result.get("model_version"):
        footer_bits.append(str(result["model_version"]))
    draw.text(
        (pad, canvas_h - footer_h + 8),
        "  ·  ".join(footer_bits),
        fill=(120, 113, 108),
        font=footer_font,
    )

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _png_font(size: int):
    """Prefer a real TrueType face so the legend is readable; fall back to bitmap."""
    from PIL import ImageFont

    candidates = (
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()
