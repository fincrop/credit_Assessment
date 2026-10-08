"""
Cadastral co-registration: move survey-number plots onto the field lines the
satellite sees, then use them as a constraint on delineated segments
(accuracy plan C1.3).

Survey-number maps in the pilot villages sit 50-100 m from the real field
lines: 5-10 Sentinel-2 pixels, more than the width of many fields. Snapping
without alignment would assign plots to the wrong field.

    1. Reference: the 10 m multi-month edge map delineation already builds
       (field_delineation.combined_edge), turned into a distance-to-edge map.
    2. Global fit: rotation x scale grid; for each, the translation that best
       overlays the plot lines on the edges, found by FFT cross-correlation
       (+-MAX_SHIFT_M), then refined at 1 m.
    3. Local correction: the village is cut into blocks; each block's own small
       shift (a tie point, +-LOCAL_SHIFT_M) is estimated the same way; a
       smoothed thin-plate spline through the tie points removes residual warp
       from old map sheets.
    4. Quality gate per village: held-out tie-point residual (leave-one-out
       through the spline) <= MAX_RESIDUAL_M and share of plot-line length
       within EDGE_TOL_M of an image edge >= MIN_EDGE_AGREEMENT. A village that
       fails keeps image-delineated boundaries.

Plot lines are a constraint, not ground truth: `constrain_segments` splits a
delineated segment along an aligned plot line, but never removes a segment
edge the image shows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

PIXEL_M = 10.0
MAX_SHIFT_M = 150.0
ROTATIONS_DEG = tuple(np.arange(-3.0, 3.01, 0.5))
SCALES = (0.98, 0.99, 1.0, 1.01, 1.02)
LOCAL_SHIFT_M = 25.0
MIN_TIE_SPREAD = 0.25       # orientation spread a block needs to give a tie shift
MIN_TIE_GAIN_M = 1.0        # mean edge distance the local shift must remove
BLOCK_M = 400.0
MIN_BLOCK_POINTS = 60
EDGE_QUANTILE = 0.80
SOFT_EDGE_M = 15.0
EDGE_TOL_M = 10.0
MAX_RESIDUAL_M = 10.0
MIN_EDGE_AGREEMENT = 0.60
TPS_SMOOTHING = 50.0
POINT_SPACING_M = 5.0


@dataclass
class EdgeGrid:
    """Edge strength on a north-up UTM grid: pixel (r, c) centre is
    (x0 + (c + .5) * res, y1 - (r + .5) * res)."""
    edge: np.ndarray
    x0: float
    y1: float
    epsg: int
    res: float = PIXEL_M

    def distance_m(self) -> np.ndarray:
        from scipy import ndimage as ndi
        e = np.nan_to_num(self.edge)
        strong = e >= np.quantile(e, EDGE_QUANTILE)
        return ndi.distance_transform_edt(~strong) * self.res

    def to_rc(self, xy: np.ndarray) -> np.ndarray:
        c = (xy[:, 0] - self.x0) / self.res - 0.5
        r = (self.y1 - xy[:, 1]) / self.res - 0.5
        return np.c_[r, c]


@dataclass
class Alignment:
    rotation_deg: float
    scale: float
    shift: Tuple[float, float]
    center: Tuple[float, float]
    tie_points: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    tie_shifts: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    residual_m: Optional[float] = None
    edge_agreement_before: Optional[float] = None
    edge_agreement: Optional[float] = None
    passed: bool = False
    reason: str = ""
    _tps: Any = None

    def apply_global(self, xy: np.ndarray) -> np.ndarray:
        th = np.radians(self.rotation_deg)
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        c = np.asarray(self.center)
        return c + self.scale * (xy - c) @ R.T + np.asarray(self.shift)

    def apply(self, xy: np.ndarray) -> np.ndarray:
        g = self.apply_global(xy)
        if self._tps is not None:
            g = g + self._tps(g)
        return g

    def report(self) -> Dict[str, Any]:
        return {
            "rotation_deg": round(self.rotation_deg, 2), "scale": round(self.scale, 4),
            "shift_m": [round(self.shift[0], 1), round(self.shift[1], 1)],
            "tie_points": int(len(self.tie_points)),
            "residual_m": None if self.residual_m is None else round(self.residual_m, 1),
            "edge_agreement_before": None if self.edge_agreement_before is None
            else round(self.edge_agreement_before, 3),
            "edge_agreement": None if self.edge_agreement is None else round(self.edge_agreement, 3),
            "passed": self.passed, "reason": self.reason,
            "gate": {"max_residual_m": MAX_RESIDUAL_M, "min_edge_agreement": MIN_EDGE_AGREEMENT},
        }


# ── geometry helpers ─────────────────────────────────────────────────────────
def line_points(polygons_utm: Sequence[Any], spacing: float = POINT_SPACING_M,
                with_directions: bool = False):
    """Points every `spacing` m along the union of plot boundaries (shared
    edges counted once), optionally with the unit line direction at each."""
    from shapely.ops import unary_union

    lines = unary_union([p.boundary for p in polygons_utm])
    geoms = getattr(lines, "geoms", [lines])
    pts, dirs = [], []
    for g in geoms:
        n = max(int(g.length // spacing), 1)
        for i in range(n + 1):
            p = g.interpolate(i * spacing)
            q = g.interpolate(min(i * spacing + 1.0, g.length))
            r = g.interpolate(max(i * spacing - 1.0, 0.0))
            d = np.array([q.x - r.x, q.y - r.y])
            dirs.append(d / (np.linalg.norm(d) or 1.0))
            pts.append((p.x, p.y))
    pts = np.asarray(pts, float)
    return (pts, np.asarray(dirs, float)) if with_directions else pts


def orientation_spread(dirs: np.ndarray) -> float:
    """Smallest / largest eigenvalue of the line-orientation tensor (0 = all
    lines parallel, 1 = no dominant direction). A block of parallel lines can
    slide along them unnoticed (the aperture problem), so its shift is not a
    usable tie point."""
    if len(dirs) < 3:
        return 0.0
    T = dirs.T @ dirs / len(dirs)
    ev = np.linalg.eigvalsh(T)
    return float(ev[0] / max(ev[-1], 1e-9))


def _sample(dist: np.ndarray, grid: EdgeGrid, xy: np.ndarray, cap: float = 50.0) -> np.ndarray:
    rc = np.rint(grid.to_rc(xy)).astype(int)
    h, w = dist.shape
    ok = (rc[:, 0] >= 0) & (rc[:, 0] < h) & (rc[:, 1] >= 0) & (rc[:, 1] < w)
    out = np.full(len(xy), cap)
    out[ok] = np.minimum(dist[rc[ok, 0], rc[ok, 1]], cap)
    return out


def _best_shift(soft: np.ndarray, grid: EdgeGrid, xy: np.ndarray, max_m: float) -> Tuple[float, float, float]:
    """Translation (dx, dy) in metres maximising sum(soft) under the points, by FFT."""
    pad = int(np.ceil(max_m / grid.res)) + 2
    h, w = soft.shape
    S = np.zeros((h + 2 * pad, w + 2 * pad))
    S[pad:pad + h, pad:pad + w] = soft
    P = np.zeros_like(S)
    rc = np.rint(grid.to_rc(xy)).astype(int) + pad
    ok = (rc[:, 0] >= 0) & (rc[:, 0] < S.shape[0]) & (rc[:, 1] >= 0) & (rc[:, 1] < S.shape[1])
    np.add.at(P, (rc[ok, 0], rc[ok, 1]), 1.0)
    corr = np.real(np.fft.ifft2(np.fft.fft2(S) * np.conj(np.fft.fft2(P))))
    k = int(np.ceil(max_m / grid.res))
    best, arg = -np.inf, (0, 0)
    for dr in range(-k, k + 1):
        for dc in range(-k, k + 1):
            v = corr[dr % corr.shape[0], dc % corr.shape[1]]
            if v > best:
                best, arg = v, (dr, dc)
    dr, dc = arg
    return dc * grid.res, -dr * grid.res, float(best / max(ok.sum(), 1))


def _refine(dist: np.ndarray, grid: EdgeGrid, xy: np.ndarray, dx: float, dy: float,
            radius: float = 8.0, step: float = 1.0) -> Tuple[float, float]:
    best, arg = np.inf, (dx, dy)
    for ex in np.arange(-radius, radius + step / 2, step):
        for ey in np.arange(-radius, radius + step / 2, step):
            v = float(np.mean(_sample(dist, grid, xy + (dx + ex, dy + ey), cap=30.0)))
            if v < best:
                best, arg = v, (dx + ex, dy + ey)
    return arg


def edge_agreement(dist: np.ndarray, grid: EdgeGrid, xy: np.ndarray) -> float:
    return float(np.mean(_sample(dist, grid, xy) <= EDGE_TOL_M))


# ── alignment ────────────────────────────────────────────────────────────────
def align(plots_utm: Sequence[Any], grid: EdgeGrid) -> Alignment:
    xy, dirs = line_points(plots_utm, with_directions=True)
    if len(xy) < 50:
        return Alignment(0.0, 1.0, (0.0, 0.0), (0.0, 0.0), passed=False,
                         reason="too few plot lines to align")
    dist = grid.distance_m()
    soft = np.exp(-dist / SOFT_EDGE_M)
    center = tuple(xy.mean(axis=0))
    before = edge_agreement(dist, grid, xy)

    best = None
    for rot in ROTATIONS_DEG:
        for sc in SCALES:
            a = Alignment(float(rot), float(sc), (0.0, 0.0), center)
            g = a.apply_global(xy)
            dx, dy, score = _best_shift(soft, grid, g, MAX_SHIFT_M)
            if best is None or score > best[0]:
                best = (score, rot, sc, dx, dy)
    _, rot, sc, dx, dy = best
    a = Alignment(float(rot), float(sc), (0.0, 0.0), center)
    dx, dy = _refine(dist, grid, a.apply_global(xy), dx, dy)
    a.shift = (float(dx), float(dy))
    a.edge_agreement_before = before

    # Tie points: each block's own small shift after the global fit.
    g = a.apply_global(xy)
    bx = np.floor((g[:, 0] - g[:, 0].min()) / BLOCK_M).astype(int)
    by = np.floor((g[:, 1] - g[:, 1].min()) / BLOCK_M).astype(int)
    ties, shifts = [], []
    for key in set(zip(bx.tolist(), by.tolist())):
        m = (bx == key[0]) & (by == key[1])
        if m.sum() < MIN_BLOCK_POINTS:
            continue
        sx, sy, _ = _best_shift(soft, grid, g[m], LOCAL_SHIFT_M)
        sx, sy = _refine(dist, grid, g[m], sx, sy, radius=6.0)
        c0 = float(np.mean(_sample(dist, grid, g[m], cap=30.0)))
        c1 = float(np.mean(_sample(dist, grid, g[m] + (sx, sy), cap=30.0)))
        # Keep a local shift only when the block constrains it in both
        # directions and it really improves the fit; otherwise the block
        # anchors the spline at zero (trust the global fit there).
        if orientation_spread(dirs[m]) < MIN_TIE_SPREAD or (c0 - c1) < MIN_TIE_GAIN_M:
            sx, sy = 0.0, 0.0
        ties.append(g[m].mean(axis=0))
        shifts.append((sx, sy))
    a.tie_points, a.tie_shifts = np.asarray(ties, float).reshape(-1, 2), np.asarray(shifts, float).reshape(-1, 2)

    if len(a.tie_points) >= 4:
        from scipy.interpolate import RBFInterpolator
        a._tps = RBFInterpolator(a.tie_points, a.tie_shifts, kernel="thin_plate_spline",
                                 smoothing=TPS_SMOOTHING)
        # Held-out residual: predict each tie from the others.
        res = []
        for i in range(len(a.tie_points)):
            keep = np.arange(len(a.tie_points)) != i
            f = RBFInterpolator(a.tie_points[keep], a.tie_shifts[keep],
                                kernel="thin_plate_spline", smoothing=TPS_SMOOTHING)
            res.append(float(np.hypot(*(f(a.tie_points[i:i + 1])[0] - a.tie_shifts[i]))))
        a.residual_m = float(np.median(res))
    elif len(a.tie_points):
        a.residual_m = float(np.median(np.hypot(a.tie_shifts[:, 0], a.tie_shifts[:, 1])))

    a.edge_agreement = edge_agreement(dist, grid, a.apply(xy))
    reasons = []
    if a.residual_m is None or a.residual_m > MAX_RESIDUAL_M:
        reasons.append(f"held-out residual {a.residual_m if a.residual_m is not None else 'n/a'} m "
                       f"> {MAX_RESIDUAL_M:.0f} m")
    if a.edge_agreement < MIN_EDGE_AGREEMENT:
        reasons.append(f"only {a.edge_agreement:.0%} of plot lines within {EDGE_TOL_M:.0f} m of an "
                       f"image edge (< {MIN_EDGE_AGREEMENT:.0%})")
    a.passed = not reasons
    a.reason = "; ".join(reasons) or "passed"
    return a


def transform_polygons(polys_utm: Sequence[Any], a: Alignment) -> List[Any]:
    from shapely.geometry import MultiPolygon, Polygon

    def ring(coords):
        arr = np.asarray(coords, float)[:, :2]
        return a.apply(arr)

    out = []
    for p in polys_utm:
        parts = list(p.geoms) if isinstance(p, MultiPolygon) else [p]
        moved = [Polygon(ring(q.exterior.coords), [ring(h.coords) for h in q.interiors]) for q in parts]
        out.append(moved[0] if len(moved) == 1 else MultiPolygon(moved))
    return out


# ── GeoJSON entry points ─────────────────────────────────────────────────────
def align_plots(plots_fc: Dict[str, Any], grid: EdgeGrid) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Plot FeatureCollection (WGS84) -> (aligned FeatureCollection, report)."""
    from shapely.geometry import mapping, shape

    from crop_analysis.field_delineation import _to_utm, _to_wgs

    feats = [f for f in plots_fc.get("features") or [] if f.get("geometry")]
    utm = [_to_utm(shape(f["geometry"]), grid.epsg) for f in feats]
    a = align(utm, grid)
    moved = transform_polygons(utm, a) if a.passed else utm
    out = []
    for f, g in zip(feats, moved):
        props = dict(f.get("properties") or {})
        props["boundary_source"] = "aligned_cadastral" if a.passed else "cadastral_unaligned"
        props["alignment_residual_m"] = a.residual_m
        out.append({"type": "Feature", "geometry": mapping(_to_wgs(g, grid.epsg)), "properties": props})
    return {"type": "FeatureCollection", "features": out}, a.report()


def constrain_segments(segments: List[Dict[str, Any]], aligned_plots: Dict[str, Any],
                       min_area_ha: float = 0.05, epsg: Optional[int] = None) -> List[Dict[str, Any]]:
    """field = aligned plot ∩ delineated segment.

    A segment crossing an aligned plot line is split along it (survey numbers
    are often subdivided among farmers, so one plot can hold several segments
    and segment edges inside a plot are kept). Pieces smaller than
    `min_area_ha` stay with the segment's largest piece. Segments outside every
    plot are returned unchanged.
    """
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    from crop_analysis.field_delineation import _to_utm, _to_wgs, _utm_epsg

    plots = [shape(f["geometry"]) for f in aligned_plots.get("features") or [] if f.get("geometry")]
    plot_props = [f.get("properties") or {} for f in aligned_plots.get("features") or [] if f.get("geometry")]
    if not plots:
        return segments
    c = unary_union(plots).centroid
    epsg = epsg or _utm_epsg(c.x, c.y)
    plots_u = [_to_utm(p, epsg) for p in plots]
    out: List[Dict[str, Any]] = []
    for seg in segments:
        g = _to_utm(shape(seg["geometry"]), epsg)
        pieces = []
        for p, pp in zip(plots_u, plot_props):
            if not p.intersects(g):
                continue
            inter = g.intersection(p)
            if inter.area > 0:
                pieces.append((inter, pp))
        if not pieces:
            out.append(seg)
            continue
        big = [(q, pp) for q, pp in pieces if q.area >= min_area_ha * 1e4]
        if not big:
            out.append(seg)
            continue
        small = [q for q, _ in pieces if q.area < min_area_ha * 1e4]
        rest = g.difference(unary_union([q for q, _ in pieces]))
        big.sort(key=lambda t: -t[0].area)
        if small or rest.area > 0:
            merged = unary_union([big[0][0], *small, rest]) if rest.area > 0 else unary_union([big[0][0], *small])
            big[0] = (merged, big[0][1])
        for q, pp in big:
            props = dict(seg.get("properties") or {})
            props["boundary_source"] = "aligned_cadastral"
            for k in ("survey_no", "plot_id", "alignment_residual_m"):
                if pp.get(k) is not None:
                    props[k] = pp[k]
            out.append({"type": "Feature", "geometry": mapping(_to_wgs(q, epsg)), "properties": props})
    return out
