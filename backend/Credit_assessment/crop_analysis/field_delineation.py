"""
Field boundary delineation — a provider chain, best source first.

WHY THE OLD APPROACH UNDER-PERFORMED
────────────────────────────────────
area_classifier used SNIC superpixels on a season-median Sentinel-2 stack.
SNIC grows clusters from a seed lattice, so object size is set by the seed
spacing rather than by the landscape: it both splits large fields and merges
neighbouring small ones. And 10 m imagery is at its limit here — the India
field-label set (Wang et al., 10,013 manually drawn fields) has a median field
of 0.24 ha, i.e. ~5x5 Sentinel-2 pixels. The Fields of The World benchmark
reports India object recall of 0.06 for 10 m models, the lowest of 24 countries.

What actually works in India is high-resolution imagery (Google ALU: 30 cm,
median field IoU 0.82 nationally). So the chain is:

  1. alu        Google Agricultural Understanding API (lookupLandscape).
                Sub-metre-derived field polygons, all of India. Needs an
                allowlisted Cloud project + API key (AG_UNDERSTANDING_API_KEY).
                LICENCE: the ALU data is CC BY-NC-ND 4.0 per the paper — confirm
                commercial-credit use with Google before enabling in production.
  2. watershed  (see below; measured best at 10 m, so it runs before FTW)
  3. ftw        Fields of The World Global 2024/2025 (PRUE, 10 m, CC-BY-4.0),
                read by bbox straight from Source Cooperative's GeoParquet with
                row-group pruning — no download, no model to host.
  -- watershed  Our own: a multi-temporal boundary-strength map computed in
                Earth Engine (monthly S2 gradients; AlphaEarth-embedding and
                Sentinel-1 terms are computed but weighted 0 by default — they
                did not help at this field size, see EDGE_WEIGHTS), then marker-controlled
                watershed and region merging locally. Splits on persistent
                discontinuities across the whole year — bunds, sowing-date
                differences, crop-history differences — instead of on a lattice.
  4. snic       Legacy, kept as the last resort inside area_classifier.

MEASURED (India-10k test split, 30 sites, 148 hand-drawn fields, median
0.24 ha; Crop_classification_model/reports/delineation_benchmark_*.json):

    method                 median IoU   IoU>=0.5   area ratio
    snic (old production)  0.292        6%         1.19
    ftw                    0.169        18%        1.30   precise, misses many
    hybrid (ftw+watershed) 0.317        24%        1.50   inherits FTW merges
    watershed (tuned)      0.388        27%        1.04

So `auto` runs alu -> watershed -> ftw, and area_classifier keeps SNIC as the
final fallback. `hybrid` stays selectable. No 10 m method is good at this field
size — sub-metre imagery (ALU) is the real fix.

`delineate()` returns plain GeoJSON polygons with `source` and `confidence`
properties; area_classifier consumes them unchanged. Every provider's output is
cleaned the same way (clip, make_valid, overlap removal, sliver filter).

Measured comparison: Crop_classification_model/src/eval_delineation.py.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

ALU_ENDPOINT = "https://agriculturalunderstanding.googleapis.com/v1:lookupLandscape"
ALU_KEY_ENV = "AG_UNDERSTANDING_API_KEY"
# A level-13 S2 cell is ~1.1-1.3 km across in India; sampling the AOI every
# ~600 m guarantees every intersecting cell is requested at least once.
ALU_SAMPLE_SPACING_M = 600
ALU_FIELD_TYPES = {"field", "fields", "agricultural_field", "farm", "cropland"}

FTW_BUCKET = "us-west-2.opendata.source.coop"
FTW_PREFIX = ("tge-labs/ftw-global-data/predictions/vectors/alpha/"
              "results-by-admin-conf/admin:country_code=IN")
FTW_HTTP = f"https://s3.us-west-2.amazonaws.com/{FTW_BUCKET}/" + FTW_PREFIX.replace("=", "%3D")
# README: `confidence >= 69` is the recommended reliability filter; null means
# "outside the modelled layer", not "low", so nulls are kept.
FTW_MIN_CONFIDENCE = 0.0

SCALE_M = 10
# Douglas-Peucker tolerance for straightening raster-traced field edges
# (regularize_partition). ~0.7 of a Sentinel-2 pixel: removes the 5 m / 10 m
# staircase entirely while a real bend in a bund (> 1 px) survives.
REGULARIZE_M = 7.0
DEFAULT_MIN_FIELD_HA = 0.05
MAX_PIXELS_PER_REQUEST = 3_000_000   # single-band float32 edge map


class DelineationError(RuntimeError):
    pass


@dataclass
class DelineationResult:
    fields: List[Dict[str, Any]]          # GeoJSON Features
    method: str
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def feature_collection(self) -> Dict[str, Any]:
        return {"type": "FeatureCollection", "features": self.fields}


# =============================================================================
# geometry helpers
# =============================================================================
def _shape(geom: Dict[str, Any]):
    from shapely.geometry import shape
    from shapely.validation import make_valid
    g = shape(geom)
    return g if g.is_valid else make_valid(g)


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _to_utm(geom, epsg: int):
    from pyproj import Transformer
    from shapely.ops import transform
    tr = Transformer.from_crs(4326, epsg, always_xy=True).transform
    return transform(tr, geom)


def _to_wgs(geom, epsg: int):
    from pyproj import Transformer
    from shapely.ops import transform
    tr = Transformer.from_crs(epsg, 4326, always_xy=True).transform
    return transform(tr, geom)


def clean_fields(polys: Sequence[Tuple[Any, Dict[str, Any]]], aoi_wgs, *,
                 min_field_ha: float, simplify_m: float = 1.0,
                 epsg: Optional[int] = None,
                 regularize_m: float = 0.0) -> List[Dict[str, Any]]:
    """
    Shared post-processing for every provider.

    Clip to the AOI, repair, drop slivers, and remove overlaps (a higher-
    confidence polygon keeps contested area), then simplify slightly. Output is
    a list of GeoJSON Features in WGS84 with `area_ha`.

    `regularize_m` > 0 replaces the per-polygon simplify with
    `regularize_partition` — straight shared edges, no gaps between
    neighbours. Use it for anything polygonised from a raster.
    """
    if regularize_m:
        simplify_m = 0.0
    from shapely.geometry import mapping
    from shapely.validation import make_valid

    if epsg is None:
        c = aoi_wgs.centroid
        epsg = _utm_epsg(c.x, c.y)
    aoi_u = _to_utm(aoi_wgs, epsg)

    items = []
    for g, props in polys:
        if g is None or g.is_empty:
            continue
        gu = _to_utm(g, epsg)
        if not gu.is_valid:
            gu = make_valid(gu)
        gu = gu.intersection(aoi_u)
        if gu.is_empty:
            continue
        items.append([gu, dict(props)])

    items.sort(key=lambda t: -(t[1].get("confidence") or 0.0))
    from shapely.strtree import STRtree
    kept: List[List[Any]] = []
    tree_geoms: List[Any] = []
    for gu, props in items:
        if tree_geoms:
            tree = STRtree(tree_geoms)
            for j in tree.query(gu):
                other = tree_geoms[int(j)]
                if gu.intersects(other):
                    gu = gu.difference(other)
                    if gu.is_empty:
                        break
        if gu.is_empty:
            continue
        gu = gu.buffer(0)
        polys_only = [p for p in getattr(gu, "geoms", [gu]) if p.geom_type == "Polygon"]
        for p in polys_only:
            if p.area / 10_000 < min_field_ha:
                continue
            if simplify_m:
                p = p.simplify(simplify_m, preserve_topology=True)
            kept.append([p, props])
            tree_geoms.append(p)

    if regularize_m:
        kept = regularize_partition(kept, aoi_u, tolerance_m=regularize_m,
                                    fill_gap_m2=max(min_field_ha, 0.05) * 10_000,
                                    min_area_m2=min_field_ha * 10_000)

    out = []
    for i, (p, props) in enumerate(kept, 1):
        w = _to_wgs(p, epsg)
        out.append({
            "type": "Feature",
            "geometry": mapping(w),
            "properties": {**props, "field_id": i, "area_ha": round(p.area / 10_000, 4)},
        })
    return out


def _close_narrow_gaps(polys: List[Any], aoi_u, width_m: float, max_gap_m2: float) -> List[Any]:
    """
    Fill background slivers narrower than `width_m` between fields.

    A morphological closing of the field coverage finds them -- including the
    ones open at both ends (a strip between two fields that runs out to a
    road), which polygonize never sees as a face. Each sliver is split between
    the fields that border it: every neighbour takes the part within half the
    gap width of itself, so the new shared edge lands near the sliver's middle.
    """
    from shapely.ops import unary_union
    from shapely.strtree import STRtree

    if not polys or width_m <= 0:
        return polys
    cov = unary_union(polys)
    h = width_m / 2.0
    closed = cov.buffer(h, join_style=2).buffer(-h, join_style=2)
    gaps = closed.difference(cov).intersection(aoi_u)
    if gaps.is_empty:
        return polys
    out = list(polys)
    tree = STRtree(polys)
    for g in getattr(gaps, "geoms", [gaps]):
        if g.geom_type != "Polygon" or g.area < 0.5 or g.area > max_gap_m2:
            continue
        gb = g.buffer(0.5)
        near = [int(j) for j in tree.query(gb)]
        near.sort(key=lambda j: -gb.intersection(polys[j].boundary).length)
        rest = g
        for j in near:
            if rest.is_empty:
                break
            piece = rest.intersection(polys[j].buffer(h + 0.5, join_style=2))
            if piece.is_empty or piece.area < 0.1:
                continue
            out[j] = out[j].union(piece)
            rest = rest.difference(piece)
    # Pieces handed to different neighbours can overlap by a hair along their
    # common buffer edge; the earlier owner keeps it.
    for i in range(len(out)):
        for j in tree.query(out[i]):
            j = int(j)
            if j > i and out[i].intersects(out[j]):
                out[j] = out[j].difference(out[i])
    return [g.buffer(0) for g in out]


def _corner_breaks(line, window_m: float, min_turn_deg: float) -> List[float]:
    """
    Distances along `line` of genuine corners.

    A staircase turns 90 degrees at every pixel, a real field corner turns
    once. Measuring the heading over +/- window_m instead of per segment
    separates them: across a staircase the averaged heading barely changes,
    across a true corner it swings by the corner angle. Local maxima above
    `min_turn_deg` are kept.
    """
    L = line.length
    if L < 2 * window_m:
        return []
    coords = list(line.coords)
    dists, d = [0.0], 0.0
    for (x0, y0), (x1, y1) in zip(coords[:-1], coords[1:]):
        d += math.hypot(x1 - x0, y1 - y0)
        dists.append(d)
    ring = line.is_ring
    cand = dists[:-1] if ring else dists[1:-1]
    turns = []
    for sd in cand:
        a_s, b_s = sd - window_m, sd + window_m
        if not ring:
            # Clamp at chain ends rather than skipping: a true corner 5 m from
            # a junction must still be found. A staircase next to an end still
            # averages to ~45 deg over the long side, below min_turn_deg.
            a_s, b_s = max(0.0, a_s), min(L, b_s)
            if sd - a_s < 2.0 or b_s - sd < 2.0:
                turns.append(0.0)
                continue
        p = line.interpolate(sd)
        a = line.interpolate(a_s % L if ring else a_s)
        b = line.interpolate(b_s % L if ring else b_s)
        h1 = math.atan2(p.y - a.y, p.x - a.x)
        h2 = math.atan2(b.y - p.y, b.x - p.x)
        turns.append(abs((math.degrees(h2 - h1) + 180) % 360 - 180))
    out: List[float] = []
    for i, (sd, t) in enumerate(zip(cand, turns)):
        if t < min_turn_deg:
            continue
        if t >= max(turns[max(0, i - 3):i + 4]):
            if not out or sd - out[-1] > window_m:
                out.append(sd)
    return out


def _simplify_chain(line, tolerance_m: float, window_m: float = 15.0,
                    min_turn_deg: float = 50.0):
    """Douglas-Peucker between pinned corners, so a real corner is never
    bevelled off just because a junction sits a few metres from it."""
    from shapely.geometry import LineString
    from shapely.ops import substring

    breaks = _corner_breaks(line, window_m, min_turn_deg)
    if not breaks:
        return line.simplify(tolerance_m, preserve_topology=False)
    L = line.length
    if line.is_ring:
        cuts = breaks + [breaks[0] + L]
    else:
        cuts = [0.0] + breaks + [L]
    coords: List[Any] = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b <= L:
            seg = substring(line, a, b)
        else:
            s1 = substring(line, a, L)
            s2 = substring(line, 0, b - L)
            seg = LineString(list(s1.coords) + list(s2.coords)[1:])
        sc = list(seg.simplify(tolerance_m, preserve_topology=False).coords)
        coords.extend(sc if not coords else sc[1:])
    return LineString(_drop_residual_steps(coords, tolerance_m))


def _drop_residual_steps(coords: List[Any], tolerance_m: float,
                         max_turn_deg: float = 60.0) -> List[Any]:
    """
    Remove the one-pixel step a pinned break leaves where a staircase meets a
    straight edge: an interior vertex with a short leg (< 1.5 x tolerance)
    that turns less than `max_turn_deg`. A real corner with a short leg turns
    ~90 deg and is kept; the residual stair step turns ~45 deg.
    Endpoints (junctions) are never touched.
    """
    pts = list(coords)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(1, len(pts) - 1):
            (x0, y0), (x1, y1), (x2, y2) = pts[i - 1][:2], pts[i][:2], pts[i + 1][:2]
            l1 = math.hypot(x1 - x0, y1 - y0)
            l2 = math.hypot(x2 - x1, y2 - y1)
            if min(l1, l2) >= 1.5 * tolerance_m:
                continue
            h1, h2 = math.atan2(y1 - y0, x1 - x0), math.atan2(y2 - y1, x2 - x1)
            turn = abs((math.degrees(h2 - h1) + 180) % 360 - 180)
            if turn < max_turn_deg:
                del pts[i]
                changed = True
                break
    return pts


def regularize_partition(items: Sequence[Sequence[Any]], aoi_u, *, tolerance_m: float,
                         fill_gap_m2: float, min_area_m2: float,
                         close_gap_m: float = 10.0) -> List[List[Any]]:
    """
    Straight, gap-free field edges for polygons traced from a raster.

    Tracing a label raster gives every field a staircase of pixel edges.
    Simplifying each polygon on its own straightens the staircase but moves
    the two copies of a shared edge differently, so neighbours pull apart and
    a sliver of background opens between them -- measured on Tondoli: 147
    sliver gaps and 41% of all field perimeter not shared with a neighbour.

    Instead:

      0. close background slivers narrower than `close_gap_m` between fields
         (_close_narrow_gaps), splitting each between its neighbours
      1. node every ring into a single linework (each shared edge appears once)
      2. merge it into chains between junctions (where 3+ fields meet)
      3. pin true corners (_corner_breaks) and Douglas-Peucker between them --
         junctions and corners never move, so the two fields either side of
         an edge get the identical straight line and corners stay square
      4. polygonize the simplified network back into faces
      5. give each face to the input polygon it overlaps most; faces that
         belong to nobody are gaps -- small ones (< fill_gap_m2) go to the
         neighbour they share the longest border with, larger ones (roads,
         settlements, non-crop land) stay empty.

    items: [[utm_polygon, props], ...]; returns the same shape.
    """
    import shapely
    from shapely.geometry import Polygon
    from shapely.ops import linemerge, polygonize, unary_union
    from shapely.strtree import STRtree

    polys = _close_narrow_gaps([it[0] for it in items], aoi_u, close_gap_m, fill_gap_m2)
    polys = [p if p.geom_type == "Polygon"
             else max(getattr(p, "geoms", [p]), key=lambda g: g.area)
             for p in polys]
    if not polys:
        return []
    rings = []
    for p in polys:
        rings.append(p.exterior)
        rings.extend(p.interiors)
    net = unary_union(rings)
    merged = linemerge(net) if net.geom_type == "MultiLineString" else net
    chains = list(getattr(merged, "geoms", [merged]))
    simp = [_simplify_chain(c, tolerance_m) for c in chains]
    # Re-node: two simplified chains may now cross; unary_union splits them at
    # the crossing so polygonize still sees a clean planar graph.
    faces = [f for f in polygonize(unary_union(simp)) if f.area > 1.0]

    tree = STRtree(polys)
    owner: List[int] = []
    for f in faces:
        best, best_a = -1, 0.0
        for j in tree.query(f):
            a = f.intersection(polys[int(j)]).area
            if a > best_a:
                best, best_a = int(j), a
        owner.append(best if best_a >= 0.5 * f.area else -1)

    # Gap faces: absorb the small ones into the neighbour with the longest
    # shared border. Large ones are real non-field land and stay empty.
    by_owner: Dict[int, List[Any]] = {}
    for f, o in zip(faces, owner):
        if o >= 0:
            by_owner.setdefault(o, []).append(f)
    gaps = [f for f, o in zip(faces, owner) if o < 0 and f.area < fill_gap_m2]
    if gaps and faces:
        ftree = STRtree(faces)
        for g in gaps:
            best, best_len = -1, 0.0
            for j in ftree.query(g):
                o = owner[int(j)]
                if o < 0:
                    continue
                ln = g.intersection(faces[int(j)]).length
                if ln > best_len:
                    best, best_len = o, ln
            if best >= 0:
                by_owner[best].append(g)

    # One polygon per field. Simplification near a junction can leave a field
    # with a small detached piece; dropping it would open a hole, so it goes
    # to the output polygon it shares the longest border with instead.
    mains: List[List[Any]] = []
    leftovers: List[Any] = []
    for o, parts in by_owner.items():
        geom = unary_union(parts)
        pieces = sorted((p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "Polygon"),
                        key=lambda p: -p.area)
        if not pieces:
            continue
        for p in pieces[1:]:
            if p.area >= min_area_m2:
                mains.append([p, items[o][1]])
            else:
                leftovers.append(p)
        mains.append([pieces[0], items[o][1]])
    if leftovers and mains:
        mtree = STRtree([m[0] for m in mains])
        for lp in leftovers:
            best, best_len = -1, 0.0
            for j in mtree.query(lp.buffer(0.05)):
                ln = lp.buffer(0.05).intersection(mains[int(j)][0].boundary).length
                if ln > best_len:
                    best, best_len = int(j), ln
            if best >= 0:
                mains[best][0] = unary_union([mains[best][0], lp])

    out: List[List[Any]] = []
    for g, props in mains:
        for p in getattr(g, "geoms", [g]):
            if p.geom_type != "Polygon" or p.area < min_area_m2:
                continue
            # unary_union of edge-sharing faces can leave collinear vertices;
            # a zero-tolerance simplify removes them without moving any edge.
            p = Polygon(p.exterior, [r for r in p.interiors if Polygon(r).area >= min_area_m2])
            out.append([shapely.simplify(p, 0.01), props])
    return out


# =============================================================================
# 1. Google ALU
# =============================================================================
def _sample_points(aoi_wgs, spacing_m: float) -> List[Tuple[float, float]]:
    c = aoi_wgs.centroid
    epsg = _utm_epsg(c.x, c.y)
    au = _to_utm(aoi_wgs, epsg)
    x0, y0, x1, y1 = au.bounds
    from shapely.geometry import Point
    pts = []
    ys = np.arange(y0 + spacing_m / 2, y1 + spacing_m / 2, spacing_m)
    xs = np.arange(x0 + spacing_m / 2, x1 + spacing_m / 2, spacing_m)
    buf = au.buffer(spacing_m)
    for y in ys:
        for x in xs:
            p = Point(x, y)
            if buf.contains(p):
                w = _to_wgs(p, epsg)
                pts.append((w.y, w.x))
    if not pts:
        pts = [(c.y, c.x)]
    return pts


def delineate_alu(aoi_geojson: Dict[str, Any], *, min_field_ha: float = DEFAULT_MIN_FIELD_HA,
                  api_key: Optional[str] = None, timeout: int = 60,
                  max_requests: int = 400) -> DelineationResult:
    """
    Field polygons from the Agricultural Understanding API.

    Contract (developers.google.com/agricultural-understanding): POST
    v1:lookupLandscape with {"locationSpecifier": {"coordinates": {latitude,
    longitude}}} (or a level-13 "s2CellId"); the response's
    `landscape.geojson` is a *string* holding a FeatureCollection whose
    features carry `alu_type`, `area_sq_m` and `class_confidence`. One call
    returns the whole S2 cell around the point, so the AOI is covered by a
    point lattice and features are de-duplicated.
    """
    import requests
    from shapely.geometry import shape

    key = api_key or os.getenv(ALU_KEY_ENV)
    if not key:
        raise DelineationError(f"{ALU_KEY_ENV} is not set")
    aoi = _shape(aoi_geojson)
    pts = _sample_points(aoi, ALU_SAMPLE_SPACING_M)
    if len(pts) > max_requests:
        raise DelineationError(
            f"AOI needs {len(pts)} ALU requests (> {max_requests}); split the area")

    seen: set = set()
    polys: List[Tuple[Any, Dict[str, Any]]] = []
    types_seen: Dict[str, int] = {}
    n_req = 0
    for lat, lon in pts:
        body = {"locationSpecifier": {"coordinates": {"latitude": lat, "longitude": lon}}}
        r = None
        for attempt in range(3):
            try:
                r = requests.post(ALU_ENDPOINT, json=body, params={"key": key}, timeout=timeout)
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(2 ** attempt)
                    continue
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
        n_req += 1
        if r is None or r.status_code != 200:
            code = None if r is None else r.status_code
            if code in (401, 403):
                raise DelineationError(f"ALU rejected the API key (HTTP {code}); is the project allowlisted?")
            logger.warning("ALU request failed at %.5f,%.5f: HTTP %s", lat, lon, code)
            continue
        land = (r.json() or {}).get("landscape") or {}
        gj = land.get("geojson")
        fc = json.loads(gj) if isinstance(gj, str) else (gj or {})
        for f in fc.get("features") or []:
            props = f.get("properties") or {}
            t = str(props.get("alu_type") or props.get("type") or "").lower()
            types_seen[t] = types_seen.get(t, 0) + 1
            if t and t not in ALU_FIELD_TYPES:
                continue
            geom = f.get("geometry")
            if not geom:
                continue
            fid = f.get("id") or props.get("id") or json.dumps(geom)[:200]
            if fid in seen:
                continue
            seen.add(fid)
            try:
                g = shape(geom)
            except Exception:                              # noqa: BLE001
                continue
            if not g.intersects(aoi):
                continue
            conf = props.get("class_confidence")
            polys.append((g, {"source": "alu",
                              "confidence": float(conf) if conf is not None else 0.9}))

    fields = clean_fields(polys, aoi, min_field_ha=min_field_ha)
    return DelineationResult(fields, "alu", {"requests": n_req, "types_seen": types_seen,
                                             "raw_features": len(polys)})


# =============================================================================
# 2. Fields of The World Global
# =============================================================================
_FTW_STATE_BBOXES: Optional[Dict[str, Tuple[float, float, float, float]]] = None
_FTW_META_CACHE: Dict[str, Any] = {}


def _ftw_state_bboxes() -> Dict[str, Tuple[float, float, float, float]]:
    """Per-partition bbox from each partition's STAC item (IN_XX.json).
    Listed once per process; the partition set is small (~40)."""
    global _FTW_STATE_BBOXES
    if _FTW_STATE_BBOXES is not None:
        return _FTW_STATE_BBOXES
    import re
    import requests

    base = f"https://s3.us-west-2.amazonaws.com/{FTW_BUCKET}"
    xml = requests.get(base, params={"list-type": "2", "prefix": FTW_PREFIX + "/"},
                       timeout=60).text
    keys = [k for k in re.findall(r"<Key>([^<]+)</Key>", xml)
            if re.search(r"/IN_[A-Z0-9]+\.json$", k)]
    out = {}
    for k in keys:
        part = k.rsplit("/", 1)[1][:-5]
        try:
            item = requests.get(f"{base}/{k.replace('=', '%3D')}", timeout=30).json()
            b = item.get("bbox")
            if b and len(b) >= 4:
                out[part] = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
        except Exception:                                  # noqa: BLE001
            continue
    _FTW_STATE_BBOXES = out
    return out


def _ftw_parquet(part: str):
    import pyarrow.fs as pafs
    import pyarrow.parquet as pq

    if part not in _FTW_META_CACHE:
        s3 = pafs.S3FileSystem(anonymous=True, region="us-west-2",
                               connect_timeout=20, request_timeout=120)
        f = pq.ParquetFile(s3.open_input_file(f"{FTW_BUCKET}/{FTW_PREFIX}/{part}.parquet"))
        m = f.metadata
        names = [m.row_group(0).column(i).path_in_schema for i in range(m.num_columns)]
        ix = {n: names.index(n) for n in ("bbox.xmin", "bbox.ymin", "bbox.xmax", "bbox.ymax")}
        stats = np.array([
            [m.row_group(g).column(ix["bbox.xmin"]).statistics.min,
             m.row_group(g).column(ix["bbox.ymin"]).statistics.min,
             m.row_group(g).column(ix["bbox.xmax"]).statistics.max,
             m.row_group(g).column(ix["bbox.ymax"]).statistics.max]
            for g in range(m.num_row_groups)
        ])
        _FTW_META_CACHE[part] = (f, stats)
    return _FTW_META_CACHE[part]


FTW_COLUMNS = ["geometry", "bbox", "confidence", "determination:datetime"]


def _ftw_cache_dir():
    from pathlib import Path
    d = Path(os.getenv("FTW_CACHE_DIR") or (Path.home() / ".cache" / "ftw_global"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ftw_read_groups(part: str, f, groups: List[int]):
    """Row groups as one DataFrame, each cached on disk after first read.
    A row group is ~65k polygons (~30 s over HTTP); villages in the same area
    reuse the same few groups, so the cache turns repeat runs into disk reads."""
    import pandas as pd
    import pyarrow.parquet as pq

    frames = []
    cache = _ftw_cache_dir()
    for g in groups:
        fp = cache / f"{part}_rg{g:05d}.parquet"
        if fp.exists():
            frames.append(pq.read_table(fp).to_pandas())
            continue
        tb = f.read_row_group(g, columns=FTW_COLUMNS)
        try:
            pq.write_table(tb, fp)
        except Exception:                                  # noqa: BLE001
            pass
        frames.append(tb.to_pandas())
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FTW_COLUMNS)


def delineate_ftw(aoi_geojson: Dict[str, Any], *, min_field_ha: float = DEFAULT_MIN_FIELD_HA,
                  year: Optional[int] = None,
                  min_confidence: float = FTW_MIN_CONFIDENCE) -> DelineationResult:
    """FTW Global polygons intersecting the AOI, via row-group bbox pruning."""
    import shapely

    aoi = _shape(aoi_geojson)
    x0, y0, x1, y1 = aoi.bounds
    parts = [p for p, b in _ftw_state_bboxes().items()
             if b[0] <= x1 and b[2] >= x0 and b[1] <= y1 and b[3] >= y0]
    if not parts:
        raise DelineationError("AOI is outside every FTW India partition")

    polys: List[Tuple[Any, Dict[str, Any]]] = []
    n_rg = 0
    for part in parts:
        f, st = _ftw_parquet(part)
        hit = np.where((st[:, 0] <= x1) & (st[:, 2] >= x0)
                       & (st[:, 1] <= y1) & (st[:, 3] >= y0))[0].tolist()
        n_rg += len(hit)
        if not hit:
            continue
        df = _ftw_read_groups(part, f, hit)
        bb = df["bbox"]
        m = np.array([(b["xmin"] <= x1 and b["xmax"] >= x0 and b["ymin"] <= y1 and b["ymax"] >= y0)
                      for b in bb], dtype=bool)
        df = df[m]
        if year is not None and len(df):
            yrs = df["determination:datetime"].dt.year
            if (yrs == year).any():
                df = df[yrs == year]
            else:
                df = df[yrs == yrs.max()]
        geoms = shapely.from_wkb(df["geometry"].to_numpy())
        for g, c in zip(geoms, df["confidence"].to_numpy()):
            if c is not None and np.isfinite(c) and c < min_confidence:
                continue
            if not g.intersects(aoi):
                continue
            conf = float(c) / 100.0 if c is not None and np.isfinite(c) else 0.5
            polys.append((g, {"source": "ftw", "confidence": conf}))

    # FTW polygons are traced from its own 10 m raster: same staircase.
    fields = clean_fields(polys, aoi, min_field_ha=min_field_ha, regularize_m=REGULARIZE_M)
    return DelineationResult(fields, "ftw", {"partitions": parts, "row_groups": n_rg,
                                             "raw_features": len(polys)})


# =============================================================================
# 3. multi-temporal edge + watershed
# =============================================================================
def _masked_blank(ee, band: str):
    return ee.Image.constant(0).rename(band).float().updateMask(ee.Image.constant(0))


def _boundary_image(ee, aoi, year: int, epsg: int):
    """
    Boundary strength in [0, ~1] plus a cropland probability band.

    A field boundary is a place where the land surface differs *persistently*
    across the year. Per month we take the gradient magnitude of cloud-masked
    S2 median reflectance (RGB, NIR, SWIR1) and NDVI; the monthly maps are
    averaged, so a transient edge (cloud shadow, one irrigation) is diluted
    while a bund or a sowing-date difference that recurs month after month
    accumulates. The AlphaEarth embedding gradient adds a learned whole-year
    discontinuity; the S1 VH texture term adds cloud-free monsoon structure.
    """
    from data_acquisition.extra_sources import (
        CS_BAND, CS_COLLECTION, CS_THRESHOLD, S2_COLLECTION, embedding_image,
    )

    proj = ee.Projection(f"EPSG:{epsg}").atScale(SCALE_M)
    region = aoi.buffer(200)

    def s2_month(m):
        start = ee.Date.fromYMD(year, 1, 1).advance(m, "month")
        coll = (ee.ImageCollection(S2_COLLECTION).filterBounds(region)
                .filterDate(start, start.advance(1, "month"))
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
                .linkCollection(ee.ImageCollection(CS_COLLECTION), [CS_BAND]))

        def mask(i):
            ok = i.select(CS_BAND).gte(CS_THRESHOLD)
            return i.updateMask(ok).select(["B2", "B3", "B4", "B8", "B11"]).divide(10000)
        blank = (ee.Image.constant([0] * 5).rename(["B2", "B3", "B4", "B8", "B11"])
                 .float().updateMask(ee.Image.constant(0)))
        med = ee.ImageCollection([blank]).merge(coll.map(mask)).median()
        ndvi = med.normalizedDifference(["B8", "B4"]).rename("NDVI")
        img = med.addBands(ndvi).reproject(proj)
        # Scale bands to comparable ranges before taking gradients.
        img = img.select(["B2", "B3", "B4", "B8", "B11", "NDVI"]).multiply(
            ee.Image.constant([6, 5, 4, 2.5, 2.5, 1.2]))
        grads = []
        for b in ["B2", "B3", "B4", "B8", "B11", "NDVI"]:
            gr = img.select(b).gradient()
            grads.append(gr.select("x").pow(2).add(gr.select("y").pow(2)))
        mag = ee.Image.cat(grads).reduce(ee.Reducer.sum()).sqrt().multiply(SCALE_M)
        return mag.rename("g").set("n", coll.size())

    months = ee.ImageCollection(ee.List.sequence(0, 11).map(lambda m: s2_month(ee.Number(m))))
    s2_edge = months.mean().unmask(0)

    emb = embedding_image(ee, year).reproject(proj)
    gx = emb.convolve(ee.Kernel.fixed(3, 3, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], -1, -1, False))
    gy = emb.convolve(ee.Kernel.fixed(3, 3, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]], -1, -1, False))
    emb_edge = (gx.pow(2).add(gy.pow(2)).reduce(ee.Reducer.sum()).sqrt()
                .divide(8).rename("e").unmask(0))

    s1 = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region)
          .filterDate(f"{year}-06-01", f"{year}-11-01")
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
          .select("VH"))
    s1 = ee.ImageCollection([_masked_blank(ee, "VH")]).merge(s1)
    s1_med = s1.median().reproject(proj).focal_median(1, "square", "pixels")
    s1g = s1_med.gradient()
    s1_edge = (s1g.select("x").pow(2).add(s1g.select("y").pow(2)).sqrt()
               .multiply(SCALE_M).divide(3).rename("s").unmask(0))


    # Cropland probability from Dynamic World (annual mean of the crops
    # probability), used to drop non-farm segments, never as a boundary.
    dw = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1").filterBounds(region)
          .filterDate(f"{year}-01-01", f"{year + 1}-01-01").select("crops"))
    dw = ee.ImageCollection([_masked_blank(ee, "crops")]).merge(dw)
    crop = dw.mean().reproject(proj).unmask(0).rename("crop")
    return ee.Image.cat([s2_edge.rename("s2"), emb_edge.rename("emb"),
                         s1_edge.rename("s1"), crop]).toFloat()


_TRANSIENT_EE_MARKERS = ("too many", "rate limit", "quota", "timeout", "timed out",
                         "deadline", "internal error", "unavailable", "try again",
                         "429", "500", "502", "503", "504")


def _is_transient_ee_error(exc: Exception) -> bool:
    """Network failures and EE load/quota errors are worth retrying; an
    evaluation error (missing band, bad geometry) fails the same way every time."""
    if type(exc).__name__ != "EEException":
        return True
    msg = str(exc).lower()
    return any(m in msg for m in _TRANSIENT_EE_MARKERS)


def _download_grid(ee, image, bounds_utm: Tuple[float, float, float, float], epsg: int,
                   n_bands: Optional[int] = None) -> np.ndarray:
    """computePixels over a UTM box, tiled to stay under request limits.
    Returns an (H, W, bands) float32 array, north-up."""
    x0, y0, x1, y1 = bounds_utm
    W = int(math.ceil((x1 - x0) / SCALE_M))
    H = int(math.ceil((y1 - y0) / SCALE_M))
    nb = int(n_bands) if n_bands else len(image.bandNames().getInfo())
    tile = int(math.sqrt(MAX_PIXELS_PER_REQUEST / max(nb, 1)))
    tile = max(256, min(tile, 1500))
    out = np.zeros((H, W, nb), dtype=np.float32)
    for r0 in range(0, H, tile):
        for c0 in range(0, W, tile):
            h = min(tile, H - r0)
            w = min(tile, W - c0)
            req = {
                "expression": image,
                "fileFormat": "NUMPY_NDARRAY",
                "grid": {
                    "dimensions": {"width": w, "height": h},
                    "affineTransform": {
                        "scaleX": SCALE_M, "shearX": 0, "translateX": x0 + c0 * SCALE_M,
                        "shearY": 0, "scaleY": -SCALE_M, "translateY": y1 - r0 * SCALE_M,
                    },
                    "crsCode": f"EPSG:{epsg}",
                },
            }
            for attempt in range(4):
                try:
                    arr = ee.data.computePixels(req)
                    break
                except Exception as exc:                   # noqa: BLE001
                    if not _is_transient_ee_error(exc) or attempt == 3:
                        raise DelineationError(f"computePixels failed: {str(exc)[:200]}")
                    time.sleep(3 * 2 ** attempt)
            block = np.stack([arr[n].astype(np.float32) for n in arr.dtype.names], axis=-1)
            out[r0:r0 + h, c0:c0 + w, :] = block
    return out


def watershed_segments(edge: np.ndarray, *, min_pixels: int, marker_mode: str = "minima",
                       minima_size: int = 3, marker_quantile: float = 0.6,
                       smooth_sigma: float = 0.0, merge_ratio: float = 0.4) -> np.ndarray:
    """
    Marker-controlled watershed on a boundary-strength map, then merge.

    Markers — one seed per candidate field:
      minima     local minima of the smoothed edge map (a pixel that is the
                 minimum of its `minima_size` window and below the
                 `marker_quantile` of edge strength). At 10 m a 0.25 ha field
                 is ~5x5 px, and the edges between neighbouring small fields
                 are faint and broken, so thresholded low-edge *regions* leak
                 into each other and yield a handful of seeds per village.
                 Local minima give one seed per basin regardless.
      threshold  connected low-edge regions (the first design; kept for the
                 benchmark record — it under-segments badly on Indian fields).

    The flood assigns every pixel to a seed along the edge relief, so borders
    settle on ridges. Regions smaller than `min_pixels` are then merged into
    the neighbour across their weakest boundary, and neighbours whose shared
    boundary is weak relative to the image's strong edges are merged too.
    """
    from scipy import ndimage as ndi

    e = np.nan_to_num(edge.astype(np.float64), nan=0.0)
    if smooth_sigma:
        e = ndi.gaussian_filter(e, smooth_sigma)
    hi = np.quantile(e, 0.98) if e.size else 1.0
    e = np.clip(e / max(hi, 1e-9), 0, 1)
    thr = np.quantile(e, marker_quantile)

    if marker_mode == "minima":
        seeds = (e <= ndi.minimum_filter(e, size=minima_size)) & (e <= thr)
    else:
        seeds = ndi.binary_erosion(e <= thr, structure=np.ones((3, 3)), iterations=1)
    markers, n = ndi.label(seeds, structure=np.ones((3, 3)))
    if n == 0:
        return np.ones_like(e, dtype=np.int32)
    labels = _priority_flood(e, markers)
    return _merge_regions(labels, e, min_pixels=min_pixels, merge_ratio=merge_ratio)


def _priority_flood(relief: np.ndarray, markers: np.ndarray) -> np.ndarray:
    """
    Marker-controlled watershed by priority flooding (Meyer's algorithm).

    NOT scipy.ndimage.watershed_ift: measured on a synthetic grid of bunded
    fields it lets regions cross one-pixel bunds (6 of 16 fields leaked at
    every dtype and connectivity tried), and at full uint16 range it leaves
    ~99% of pixels unlabelled. scikit-image's watershed is used when installed;
    otherwise this heapq flood, which is exact and fast enough for village-
    scale grids (~1 s per 250k px).
    """
    try:
        from skimage.segmentation import watershed as sk_ws  # type: ignore
        return sk_ws(relief, markers, connectivity=1).astype(np.int32)
    except ImportError:
        pass
    import heapq

    H, W = relief.shape
    lab = markers.astype(np.int32).copy()
    flat_r = relief.ravel()
    flat_l = lab.ravel()
    heap: List[Tuple[float, int, int]] = []
    counter = 0
    seeds = np.flatnonzero(flat_l)
    for idx in seeds:
        heap.append((float(flat_r[idx]), counter, int(idx)))
        counter += 1
    heapq.heapify(heap)
    queued = flat_l > 0
    while heap:
        _, _, idx = heapq.heappop(heap)
        r, c = divmod(idx, W)
        lv = flat_l[idx]
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < H and 0 <= nc < W:
                j = nr * W + nc
                if not queued[j]:
                    queued[j] = True
                    flat_l[j] = lv
                    heapq.heappush(heap, (float(flat_r[j]), counter, j))
                    counter += 1
    return lab


def _merge_regions(labels: np.ndarray, e: np.ndarray, *, min_pixels: int,
                   merge_ratio: float) -> np.ndarray:
    """Region-adjacency merging with union-find over boundary statistics."""
    lab = labels.astype(np.int64)
    H, W = lab.shape
    # Pair up horizontally and vertically adjacent pixels on different labels.
    pairs = []
    vals = []
    for a, b, ea, eb in (
        (lab[:, :-1], lab[:, 1:], e[:, :-1], e[:, 1:]),
        (lab[:-1, :], lab[1:, :], e[:-1, :], e[1:, :]),
    ):
        m = a != b
        lo = np.minimum(a[m], b[m])
        hi_ = np.maximum(a[m], b[m])
        pairs.append(np.stack([lo, hi_], 1))
        vals.append(np.maximum(ea[m], eb[m]))
    if not pairs or sum(len(p) for p in pairs) == 0:
        return labels
    P = np.concatenate(pairs)
    V = np.concatenate(vals)
    key = P[:, 0] * (lab.max() + 1) + P[:, 1]
    order = np.argsort(key)
    key, P, V = key[order], P[order], V[order]
    uniq, start = np.unique(key, return_index=True)
    edges = []
    for i, s in enumerate(start):
        end = start[i + 1] if i + 1 < len(start) else len(key)
        edges.append((float(np.median(V[s:end])), int(P[s, 0]), int(P[s, 1]), end - s))

    sizes = np.bincount(lab.ravel())
    parent = np.arange(len(sizes))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    size = sizes.astype(np.int64).copy()
    strong = float(np.quantile([t[0] for t in edges], 0.75)) if edges else 1.0
    # Weakest boundaries first.
    for w, a, b, blen in sorted(edges):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        small = min(size[ra], size[rb]) < min_pixels
        weak = w < merge_ratio * strong
        if small or weak:
            parent[rb] = ra
            size[ra] += size[rb]
    # Second pass: anything still under min_pixels joins its weakest neighbour.
    for w, a, b, blen in sorted(edges):
        ra, rb = find(a), find(b)
        if ra != rb and min(size[ra], size[rb]) < min_pixels:
            parent[rb] = ra
            size[ra] += size[rb]
    root = np.array([find(i) for i in range(len(parent))])
    return root[lab].astype(np.int32)


# Relative weight of each boundary cue, from src/tune_delineation.py over 30
# India-10k sites (reports/delineation_tuning.json), re-run after the flood
# fix. Best mean IoU by cue set: s2 0.394 · s2+emb 0.382 · all 0.376. The
# annual AlphaEarth embedding and monsoon S1 texture do NOT sharpen boundaries
# at 0.24 ha field size, so they are off by default (still computable, for
# regions with larger fields where they may earn a place).
EDGE_WEIGHTS = {"s2": 1.0, "emb": 0.0, "s1": 0.0}
BOUNDARY_BANDS = ("s2", "emb", "s1", "crop")


@dataclass
class BoundaryArrays:
    bands: np.ndarray            # (H, W, 4) — BOUNDARY_BANDS
    x0: float
    y1: float
    epsg: int
    year: int

    def band(self, name: str) -> np.ndarray:
        return self.bands[..., BOUNDARY_BANDS.index(name)]


def fetch_boundary_arrays(aoi_geojson: Dict[str, Any], *, year: int,
                          ee_module=None) -> BoundaryArrays:
    ee = ee_module
    if ee is None:
        import ee  # noqa: PLC0415
    aoi = _shape(aoi_geojson)
    c = aoi.centroid
    epsg = _utm_epsg(c.x, c.y)
    au = _to_utm(aoi, epsg)
    x0, y0, x1, y1 = au.buffer(50).bounds
    x0, y0 = math.floor(x0 / SCALE_M) * SCALE_M, math.floor(y0 / SCALE_M) * SCALE_M
    x1, y1 = math.ceil(x1 / SCALE_M) * SCALE_M, math.ceil(y1 / SCALE_M) * SCALE_M
    img = _boundary_image(ee, ee.Geometry(aoi_geojson, None, False), year, epsg)
    arr = _download_grid(ee, img, (x0, y0, x1, y1), epsg, n_bands=len(BOUNDARY_BANDS))
    return BoundaryArrays(arr, x0, y1, epsg, year)


def combined_edge(ba: BoundaryArrays, weights: Optional[Dict[str, float]] = None) -> np.ndarray:
    """Weighted sum of the per-cue edge maps, each scaled by its own robust
    maximum first so a weight means the same thing in every scene."""
    w = weights or EDGE_WEIGHTS
    out = np.zeros(ba.bands.shape[:2], dtype=np.float64)
    for k, wk in w.items():
        if not wk:
            continue
        e = np.nan_to_num(ba.band(k).astype(np.float64))
        hi = np.quantile(e, 0.98) if e.size else 1.0
        out += wk * np.clip(e / max(hi, 1e-9), 0, 1.5)
    return out


def segment_arrays(ba: BoundaryArrays, aoi_geojson: Dict[str, Any], *,
                   min_field_ha: float = DEFAULT_MIN_FIELD_HA, min_cropland: float = 0.25,
                   weights: Optional[Dict[str, float]] = None, upsample: int = 2,
                   **ws_kwargs) -> DelineationResult:
    import rasterio.features
    from affine import Affine
    from shapely.geometry import shape

    aoi = _shape(aoi_geojson)
    epsg = ba.epsg
    au = _to_utm(aoi, epsg)
    edge = combined_edge(ba, weights)
    crop = ba.band("crop")
    # Upsampling the boundary map (bilinear) does not add information, but it
    # gives a 5-px-wide field an interior for the flood and lets borders fall
    # between 10 m pixel centres instead of on the pixel grid.
    if upsample and upsample > 1:
        from scipy import ndimage as ndi
        edge = ndi.zoom(edge, upsample, order=1)
        crop = ndi.zoom(crop, upsample, order=1)
    px = SCALE_M / (upsample or 1)
    min_px = max(3, int(round(min_field_ha * 10_000 / px ** 2)))
    labels = watershed_segments(edge, min_pixels=min_px, **ws_kwargs)

    tf = Affine(px, 0, ba.x0, 0, -px, ba.y1)
    inside = rasterio.features.geometry_mask([au], out_shape=labels.shape,
                                             transform=tf, invert=True)
    idx = labels.ravel()
    sums = np.bincount(idx, weights=crop.ravel().astype(np.float64))
    cnts = np.bincount(idx)
    crop_frac = np.divide(sums, np.maximum(cnts, 1))
    polys: List[Tuple[Any, Dict[str, Any]]] = []
    for geom, val in rasterio.features.shapes(labels.astype(np.int32), mask=inside, transform=tf):
        cf = float(crop_frac[int(val)])
        if cf < min_cropland:
            continue
        polys.append((_to_wgs(shape(geom), epsg), {"source": "watershed",
                                                   "confidence": round(0.3 + 0.4 * cf, 3),
                                                   "cropland_prob": round(cf, 3)}))
    fields = clean_fields(polys, aoi, min_field_ha=min_field_ha, epsg=epsg,
                          regularize_m=REGULARIZE_M)
    return DelineationResult(fields, "watershed", {
        "grid": list(labels.shape), "segments": int(len(np.unique(labels[inside]))),
        "epsg": epsg, "year": ba.year,
    })


def delineate_watershed(aoi_geojson: Dict[str, Any], *, year: int,
                        min_field_ha: float = DEFAULT_MIN_FIELD_HA,
                        min_cropland: float = 0.25,
                        ee_module=None, **ws_kwargs) -> DelineationResult:
    ba = fetch_boundary_arrays(aoi_geojson, year=year, ee_module=ee_module)
    return segment_arrays(ba, aoi_geojson, min_field_ha=min_field_ha,
                          min_cropland=min_cropland, **ws_kwargs)


# =============================================================================
# fusion
# =============================================================================
def fuse_fields(primary: Sequence[Dict[str, Any]], fill: Sequence[Dict[str, Any]],
                aoi_geojson: Dict[str, Any], *, min_field_ha: float,
                min_fill_fraction: float = 0.6) -> List[Dict[str, Any]]:
    """
    Keep every `primary` polygon; add `fill` segments only where the primary
    source has nothing.

    FTW Global is precise where it fires but conservative on smallholder
    fields — it simply has no polygon for many of them. A local segmenter
    covers everything but splits and merges more. So FTW wins wherever it has
    an opinion, and a fill segment survives only if at least
    `min_fill_fraction` of it lies outside all primary polygons (otherwise it
    is a partial duplicate that would just leave a sliver).
    """
    from shapely.geometry import shape
    from shapely.ops import unary_union

    aoi = _shape(aoi_geojson)
    prim = [(shape(f["geometry"]), {**(f.get("properties") or {}), "confidence": 1.0})
            for f in primary]
    covered = unary_union([g for g, _ in prim]) if prim else None
    extra = []
    for f in fill:
        g = shape(f["geometry"])
        if g.is_empty:
            continue
        if covered is not None and not covered.is_empty:
            free = g.difference(covered)
            if free.area < min_fill_fraction * g.area:
                continue
            g = free
        props = {**(f.get("properties") or {}), "confidence": 0.5}
        extra.append((g, props))
    return clean_fields(prim + extra, aoi, min_field_ha=min_field_ha,
                        regularize_m=REGULARIZE_M)


def delineate_hybrid(aoi_geojson: Dict[str, Any], *, year: int,
                     min_field_ha: float = DEFAULT_MIN_FIELD_HA) -> DelineationResult:
    """FTW Global where it has a polygon, watershed segments everywhere else.
    If FTW is unreachable this degrades to plain watershed rather than failing."""
    ws = delineate_watershed(aoi_geojson, year=year, min_field_ha=min_field_ha)
    try:
        ftw = delineate_ftw(aoi_geojson, min_field_ha=min_field_ha, year=year)
    except Exception as exc:                               # noqa: BLE001
        logger.warning("hybrid: FTW unavailable (%s); watershed only", str(exc)[:160])
        ws.diagnostics["ftw_error"] = str(exc)[:200]
        return ws
    fields = fuse_fields(ftw.fields, ws.fields, aoi_geojson, min_field_ha=min_field_ha)
    n_ftw = sum(1 for f in fields if f["properties"].get("source") == "ftw")
    return DelineationResult(fields, "hybrid", {
        "ftw_fields": n_ftw, "watershed_fields": len(fields) - n_ftw,
        "ftw": ftw.diagnostics, "watershed": ws.diagnostics,
    })


# =============================================================================
# chain
# =============================================================================
METHODS = ("auto", "alu", "hybrid", "ftw", "watershed", "snic")


def available_methods() -> List[str]:
    out = []
    if os.getenv(ALU_KEY_ENV):
        out.append("alu")
    out += ["hybrid", "ftw", "watershed", "snic"]
    return out


def delineate(aoi_geojson: Dict[str, Any], *, year: int, method: str = "auto",
              min_field_ha: float = DEFAULT_MIN_FIELD_HA,
              progress: Optional[Callable[[str], None]] = None) -> DelineationResult:
    """
    Run the requested provider, or the chain for `auto`. 'snic' is not run
    here: it is implemented inside area_classifier and signalled by raising, so
    the caller falls back to its own path.
    """
    method = (method or "auto").lower()
    if method not in METHODS:
        raise DelineationError(f"unknown delineation method {method!r}")
    # auto: ALU (sub-metre) when a key is configured, else our watershed —
    # measured best of the 10 m sources on India-10k (median IoU 0.388 vs FTW
    # 0.169, FTW+watershed 0.317, SNIC 0.292; reports/delineation_benchmark_*.json).
    # FTW then SNIC remain as fallbacks if Earth Engine pixels are unavailable.
    order = {"auto": [m for m in ("alu", "watershed", "ftw") if m in available_methods()],
             "alu": ["alu"], "hybrid": ["hybrid"], "ftw": ["ftw"],
             "watershed": ["watershed"], "snic": []}[method]
    errors = {}
    for m in order:
        t0 = time.time()
        logger.info("delineation provider %s: starting", m)
        try:
            if progress:
                progress(m)
            if m == "alu":
                res = delineate_alu(aoi_geojson, min_field_ha=min_field_ha)
            elif m == "ftw":
                res = delineate_ftw(aoi_geojson, min_field_ha=min_field_ha, year=year)
            elif m == "hybrid":
                res = delineate_hybrid(aoi_geojson, year=year, min_field_ha=min_field_ha)
            else:
                res = delineate_watershed(aoi_geojson, year=year, min_field_ha=min_field_ha)
            if res.fields:
                logger.info("delineation provider %s: %d fields in %.0f s",
                            m, len(res.fields), time.time() - t0)
                res.diagnostics["errors_before"] = errors
                return res
            logger.warning("delineation provider %s returned no fields (%.0f s)",
                           m, time.time() - t0)
            errors[m] = "no fields returned"
        except Exception as exc:                           # noqa: BLE001
            logger.warning("delineation provider %s failed after %.0f s: %s",
                           m, time.time() - t0, str(exc)[:200])
            errors[m] = str(exc)[:200]
    raise DelineationError(f"all delineation providers failed: {errors}")
