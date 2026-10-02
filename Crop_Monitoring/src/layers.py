"""Map layer for a monitoring run.

One feature per scored zone. A single cohort uses the field boundary. Mixed
cohorts use each cohort's pixel hull, clipped to that boundary, so a second
sowing date is a separate polygon on the map and in the downloads.
"""

from __future__ import annotations

from typing import Any, Optional

STRESS_COLORS = {
    "Healthy": "#2E7D4F",
    "Nutrient Deficit": "#C9A227",
    "Crop Water Deficit": "#D97A34",
    "Tissue Damage": "#B4553A",
    "Sub-optimal Growth": "#7C5FA8",
    "Unspecified": "#8F8779",
}


def build_fields(
    field_geometry: Optional[dict],
    zone_rows: list[tuple[Any, dict]],
) -> dict:
    """FeatureCollection the map and the download panel both draw."""
    features = []
    crop_rows = [
        (zone, doc) for zone, doc in zone_rows
        if doc.get("kind") not in ("non_crop", "excluded")
    ]
    masked = any(doc.get("kind") == "non_crop" for _, doc in zone_rows)
    # Bare or tree pixels were removed, so the published polygon is the remaining crop, not the original boundary.
    single = len(crop_rows) == 1 and field_geometry and not masked
    for zone, doc in crop_rows:
        stress = _latest_stress(doc)
        label = stress or doc.get("stage") or doc.get("zone_id") or "Zone"
        crop_name = doc.get("crop") or "Unspecified"
        title = f"{doc.get('zone_id', 'zone')} · {crop_name}"
        color = STRESS_COLORS.get(stress or "", STRESS_COLORS["Unspecified"])
        geom = field_geometry if single else _zone_geom(zone, field_geometry)
        if geom is None:
            continue
        yld = doc.get("yield") or {}
        sowing = doc.get("sowing") or {}
        progress = doc.get("progress") or {}
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "field_id": doc.get("zone_id"),
                "zone_id": doc.get("zone_id"),
                "crop": title,
                "crop_name": crop_name,
                "kind": doc.get("kind"),
                "color": color,
                "stress": stress or "—",
                "area_ha": _area_ha(geom),
                "area_share": doc.get("area_share"),
                "confidence": sowing.get("confidence"),
                "sowing_date": sowing.get("date"),
                "harvest_date": (doc.get("harvest") or {}).get("date"),
                "split_reason": doc.get("split_reason"),
                "stage": progress.get("stage"),
                "tau": progress.get("tau"),
                "yield_t_ha": yld.get("t_ha"),
                "yield_low": yld.get("low"),
                "yield_high": yld.get("high"),
                "note": f"{label}" + (f" · {yld['t_ha']} t/ha" if yld.get("t_ha") is not None else ""),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _latest_stress(doc: dict) -> str | None:
    for item in reversed(doc.get("intervals") or []):
        stress = item.get("stress") or {}
        kind = stress.get("type")
        if kind:
            return str(kind)
    return None


def _zone_geom(zone: Any, field_geometry: Optional[dict]) -> Optional[dict]:
    try:
        from shapely.geometry import MultiPoint, mapping, shape
        from shapely.ops import unary_union
    except Exception:
        return field_geometry

    pts = [(p.lon, p.lat) for p in getattr(zone, "pixels", []) if p.lon or p.lat]
    if len(pts) < 1:
        return field_geometry
    cloud = MultiPoint(pts)
    hull = cloud.convex_hull
    if hull.geom_type != "Polygon":
        hull = cloud.buffer(0.00035)
    if field_geometry:
        try:
            field = shape(field_geometry)
            clipped = hull.intersection(field)
            if not clipped.is_empty:
                hull = clipped
        except Exception:
            pass
    if hull.is_empty:
        return field_geometry
    # A GeometryCollection is awkward on the map. Keep the polygonal part.
    if hull.geom_type == "GeometryCollection":
        polys = [g for g in hull.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            return field_geometry
        hull = unary_union(polys)
    return mapping(hull)


def _area_ha(geom: dict) -> float:
    """Spherical ring area, hectares. Holes are subtracted."""
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return 0.0
    if geom["type"] == "Polygon":
        return round(_poly_ha(geom["coordinates"]), 4)
    return round(sum(_poly_ha(poly) for poly in geom["coordinates"]), 4)


def _poly_ha(rings: list) -> float:
    total = 0.0
    for i, ring in enumerate(rings):
        sign = 1.0 if i == 0 else -1.0
        total += sign * _ring_ha(ring)
    return abs(total)


def _ring_ha(ring: list) -> float:
    if len(ring) < 4:
        return 0.0
    radius = 6378137.0
    acc = 0.0
    for i in range(len(ring) - 1):
        lon1, lat1 = ring[i][0], ring[i][1]
        lon2, lat2 = ring[i + 1][0], ring[i + 1][1]
        acc += ((lon2 - lon1) * 3.141592653589793 / 180.0) * (
            2.0 + _sin(lat1) + _sin(lat2)
        )
    return abs((acc * radius * radius) / 2.0) / 10000.0


def _sin(deg: float) -> float:
    import math
    return math.sin(deg * math.pi / 180.0)
