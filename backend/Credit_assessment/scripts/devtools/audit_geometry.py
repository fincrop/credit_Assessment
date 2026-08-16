#!/usr/bin/env python3
"""
Audit farm boundary quality across the database.

WHY
---
A wrong boundary and bad land are indistinguishable downstream. If a polygon is
drawn over the adjacent river, the land-cover gate correctly reports WATER — but
the farmer's land is fine and the DATA is wrong. Those are completely different
messages, and only one of them is the farmer's problem.

Worse, geometry rejection is currently NON-FATAL: when a polygon fails QA the
collector silently substitutes a circular buffer around the centroid
(geometry_source = "polygon_rejected_fallback_point"), so the pipeline analyses
a circle near the field rather than the field. Nothing downstream knows.

This reads farm_info only — no imagery, no quota, seconds to run.

WHAT IT FLAGS
-------------
  no_geometry        no polygon at all; a point buffer is all we can use
  area_mismatch      polygon area vs registered area diverges
  tiny_parcel        too few Sentinel-2 pixels for the mean to be meaningful
  degenerate         zero/near-zero area, too few vertices, or not closed
  suspicious_shape   extreme elongation — often a mis-drawn line or a canal
  out_of_india       centroid outside the plausible bounding box

USAGE
-----
  python scripts/devtools/audit_geometry.py
  python scripts/devtools/audit_geometry.py --farmers 12520694333 6a7e300006e485e13f575fb3
  python scripts/devtools/audit_geometry.py --json geometry_audit.json

Run from the package root (backend/Credit_assessment).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:  # pragma: no cover
    pass

# A 10 m pixel is 0.01 ha. Below ~20 pixels the AOI mean is dominated by
# neighbouring land and mixed pixels, whatever the boundary says.
PIXEL_HA = 0.01
MIN_USABLE_PIXELS = 20
INDIA_BBOX = (68.0, 6.0, 98.0, 37.5)   # lon_min, lat_min, lon_max, lat_max


def _ring(geometry):
    """First linear ring of a Polygon / MultiPolygon, as [(lon, lat), ...]."""
    if not isinstance(geometry, dict):
        return None
    gtype = str(geometry.get("type", "")).lower()
    coords = geometry.get("coordinates")
    if not coords:
        return None
    try:
        if gtype == "polygon":
            return [tuple(p[:2]) for p in coords[0]]
        if gtype == "multipolygon":
            return [tuple(p[:2]) for p in coords[0][0]]
    except (TypeError, IndexError):
        return None
    return None


def _area_ha(ring):
    """Shoelace area in hectares, with a latitude correction for degrees."""
    if not ring or len(ring) < 4:
        return None
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 110_540.0
    pts = [((lon * kx), (lat * ky)) for lon, lat in ring]
    s = 0.0
    for i in range(len(pts) - 1):
        s += pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1]
    return abs(s) / 2.0 / 10_000.0


def _elongation(ring):
    """Bounding-box aspect ratio. Very high means a line, not a field."""
    if not ring:
        return None
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    lat0 = sum(lats) / len(lats)
    w = (max(lons) - min(lons)) * 111_320.0 * math.cos(math.radians(lat0))
    h = (max(lats) - min(lats)) * 110_540.0
    lo, hi = min(w, h), max(w, h)
    return (hi / lo) if lo > 1e-6 else float("inf")


def audit_farm(farmer_id, doc):
    """Audit the envelope geometry and each farms[] entry."""
    out = {"farmer_id": farmer_id, "flags": [], "parcels": []}

    def _one(label, geom, registered_ha, lat, lon):
        rec = {"label": label, "registered_ha": registered_ha, "flags": []}
        ring = _ring(geom)

        if not ring:
            rec["flags"].append("no_geometry")
            rec["geometry_area_ha"] = None
        else:
            area = _area_ha(ring)
            rec["geometry_area_ha"] = None if area is None else round(area, 4)
            rec["n_vertices"] = len(ring)
            elong = _elongation(ring)
            rec["elongation"] = None if elong is None else round(elong, 1)

            if len(ring) < 4:
                rec["flags"].append("degenerate")
            elif ring[0] != ring[-1]:
                rec["flags"].append("degenerate")
            if area is not None and area < 1e-4:
                rec["flags"].append("degenerate")
            if elong is not None and elong > 12:
                rec["flags"].append("suspicious_shape")

            if area and registered_ha:
                ratio = area / float(registered_ha)
                rec["area_ratio"] = round(ratio, 3)
                if ratio < 0.5 or ratio > 2.0:
                    rec["flags"].append("area_mismatch_severe")
                elif ratio < 0.8 or ratio > 1.25:
                    rec["flags"].append("area_mismatch")

            effective = area or (float(registered_ha) if registered_ha else 0.0)
            rec["approx_pixels"] = int(effective / PIXEL_HA) if effective else 0
            if rec["approx_pixels"] < MIN_USABLE_PIXELS:
                rec["flags"].append("tiny_parcel")

        if lat is not None and lon is not None:
            lo_lon, lo_lat, hi_lon, hi_lat = INDIA_BBOX
            if not (lo_lon <= lon <= hi_lon and lo_lat <= lat <= hi_lat):
                rec["flags"].append("out_of_india")
            rec["centroid"] = [round(lat, 6), round(lon, 6)]
            rec["imagery"] = (
                f"https://www.google.com/maps/@{lat},{lon},600m/data=!3m1!1e3"
            )
        return rec

    out["parcels"].append(_one(
        "envelope", doc.get("geometry"), doc.get("field_area_ha"),
        doc.get("latitude"), doc.get("longitude"),
    ))
    for i, farm in enumerate(doc.get("farms") or []):
        centroid = farm.get("centroid") or {}
        out["parcels"].append(_one(
            f"farms[{i}] {farm.get('plot_key') or farm.get('farm_id') or ''}".strip(),
            farm.get("geometry"), farm.get("area_ha"),
            centroid.get("lat"), centroid.get("lng"),
        ))

    for p in out["parcels"]:
        out["flags"].extend(p["flags"])
    out["flags"] = sorted(set(out["flags"]))
    out["clean"] = not out["flags"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--farmers", nargs="*", help="limit to these farmer ids")
    ap.add_argument("--json", help="write full results to this path")
    ap.add_argument("--only-flagged", action="store_true")
    args = ap.parse_args()

    from mongodb_helper import MongoDBHelper

    db = MongoDBHelper()
    try:
        query = {"farmer_id": {"$in": args.farmers}} if args.farmers else {}
        docs = list(db.farms.find(query))
        if not docs:
            print("No farm_info documents found.")
            return 1

        results = [audit_farm(d.get("farmer_id"), d) for d in docs]
        flagged = [r for r in results if not r["clean"]]

        print(f"Farms audited : {len(results)}")
        print(f"Clean         : {len(results) - len(flagged)}")
        print(f"Flagged       : {len(flagged)}")
        print()

        counts: dict = {}
        for r in results:
            for f in r["flags"]:
                counts[f] = counts.get(f, 0) + 1
        if counts:
            print("Flag counts:")
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"  {k:24s} {v}")
            print()

        show = flagged if args.only_flagged else results
        for r in show:
            status = "FLAGGED" if r["flags"] else "ok"
            print(f"{'=' * 66}")
            print(f"{r['farmer_id']}  [{status}]  {', '.join(r['flags']) or ''}")
            for p in r["parcels"]:
                if not p.get("geometry_area_ha") and "no_geometry" not in p["flags"]:
                    continue
                print(f"  {p['label']}")
                print(f"    registered {p.get('registered_ha')} ha  |  "
                      f"geometry {p.get('geometry_area_ha')} ha  |  "
                      f"ratio {p.get('area_ratio')}")
                print(f"    ~{p.get('approx_pixels')} S2 pixels  |  "
                      f"vertices {p.get('n_vertices')}  |  "
                      f"elongation {p.get('elongation')}")
                if p.get("flags"):
                    print(f"    FLAGS: {', '.join(p['flags'])}")
                if p.get("imagery"):
                    print(f"    {p['imagery']}")
            print()

        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, default=str)
            print(f"Wrote {args.json}")

        print("A boundary problem and a land problem look identical downstream.")
        print("Any parcel flagged here should be re-drawn before its score or")
        print("its rejection is believed.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
