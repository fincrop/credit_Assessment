"""
Offline tests for crop_analysis/field_delineation.py: the watershed segmenter
on synthetic boundary maps, and the shared polygon clean-up. The network
providers (ALU, FTW, Earth Engine) are benchmarked by
Crop_classification_model/src/eval_delineation.py instead.
"""
from __future__ import annotations

import numpy as np
from shapely.geometry import box, mapping, shape

from crop_analysis.field_delineation import (
    BOUNDARY_BANDS,
    BoundaryArrays,
    _to_utm,
    _utm_epsg,
    clean_fields,
    segment_arrays,
    watershed_segments,
)


def _grid_edges(n_fields: int = 4, size: int = 8, noise: float = 0.05, seed: int = 0):
    """n x n fields of `size` px separated by one-pixel bunds."""
    rng = np.random.default_rng(seed)
    H = W = n_fields * size
    e = rng.uniform(0, noise, (H, W))
    for k in range(1, n_fields):
        e[k * size, :] = 1.0
        e[:, k * size] = 1.0
    return e


def test_watershed_recovers_bunded_grid():
    labels = watershed_segments(_grid_edges(), min_pixels=10)
    n = len(np.unique(labels))
    assert 12 <= n <= 20, n            # 16 fields, some tolerance


def test_watershed_does_not_leave_unlabelled_pixels():
    # Regression: full-range uint16 relief made scipy's watershed_ift leave
    # ~99% of pixels at label 0, which the merge then turned into one region.
    labels = watershed_segments(_grid_edges(n_fields=6), min_pixels=5)
    assert (labels > 0).all()
    assert len(np.unique(labels)) > 20


def test_faint_interior_ridge_does_not_split_a_field():
    """A moisture or canopy streak inside one farm is not a bund.

    Outer lines are full strength; the line through the middle is a quarter
    of that. The two halves must come back as one field.
    """
    e = np.full((24, 24), 0.04)
    e[0, :] = e[-1, :] = e[:, 0] = e[:, -1] = 1.0
    e[12, :] = 0.25
    labels = watershed_segments(e, min_pixels=8)
    assert len(np.unique(labels)) == 1, len(np.unique(labels))


def test_min_pixels_merges_specks():
    fine = watershed_segments(_grid_edges(n_fields=8, size=4), min_pixels=3)
    coarse = watershed_segments(_grid_edges(n_fields=8, size=4), min_pixels=40)
    assert len(np.unique(coarse)) < len(np.unique(fine))


def test_clean_fields_removes_overlap_and_slivers():
    aoi = box(76.680, 18.320, 76.690, 18.330)
    a = box(76.681, 18.321, 76.684, 18.324)
    b = box(76.683, 18.321, 76.686, 18.324)          # overlaps a
    sliver = box(76.6881, 18.3281, 76.6882, 18.3282)  # ~0.01 ha
    out = clean_fields([(a, {"confidence": 0.9}), (b, {"confidence": 0.5}),
                        (sliver, {"confidence": 0.9})], aoi, min_field_ha=0.05)
    assert len(out) == 2
    epsg = _utm_epsg(76.685, 18.325)
    g0, g1 = (_to_utm(shape(f["geometry"]), epsg) for f in out)
    assert g0.intersection(g1).area < 1.0            # m²
    # the higher-confidence polygon keeps the contested strip
    assert out[0]["properties"]["confidence"] == 0.9


def test_segment_arrays_end_to_end_synthetic():
    lon0, lat0 = 76.68, 18.32
    epsg = _utm_epsg(lon0, lat0)
    from crop_analysis.field_delineation import _to_utm as tu
    from shapely.geometry import Point
    p = tu(Point(lon0, lat0), epsg)
    e = _grid_edges(n_fields=4, size=10)
    H, W = e.shape
    bands = np.zeros((H, W, len(BOUNDARY_BANDS)), dtype=np.float32)
    bands[..., BOUNDARY_BANDS.index("s2")] = e
    bands[..., BOUNDARY_BANDS.index("emb")] = e
    bands[..., BOUNDARY_BANDS.index("crop")] = 0.8
    x0 = p.x - p.x % 10
    y1 = p.y - p.y % 10 + H * 10
    ba = BoundaryArrays(bands, x0, y1, epsg, 2024)
    from crop_analysis.field_delineation import _to_wgs
    aoi = _to_wgs(box(x0 + 5, y1 - H * 10 + 5, x0 + W * 10 - 5, y1 - 5), epsg)
    res = segment_arrays(ba, mapping(aoi), min_field_ha=0.3)
    areas = sorted(f["properties"]["area_ha"] for f in res.fields)
    assert 10 <= len(areas) <= 20, len(areas)
    assert np.median(areas) > 0.6                     # fields are 1 ha


def test_regularize_straightens_staircase_and_keeps_neighbours_glued():
    """Two fields meeting along a diagonal, traced as 5 m pixel staircases.
    After regularisation the shared edge is one straight line used by both
    polygons: no gap, no overlap, far fewer vertices."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    from crop_analysis.field_delineation import regularize_partition

    step, n = 5.0, 20
    stair = [(i * step, i * step) for i in range(n + 1)]
    zig = []
    for i in range(n):
        zig += [(i * step, i * step), ((i + 1) * step, i * step)]
    zig.append((n * step, n * step))
    lower = Polygon([(0, 0)] + zig[1:] + [(n * step, 0)])
    upper = Polygon(zig + [(0, n * step)])
    assert lower.intersection(upper).area < 1e-6
    aoi = box(-10, -10, n * step + 10, n * step + 10)
    out = regularize_partition([[lower, {"i": 0}], [upper, {"i": 1}]], aoi,
                               tolerance_m=7.0, fill_gap_m2=500, min_area_m2=10)
    assert len(out) == 2
    a, b = out[0][0], out[1][0]
    assert a.intersection(b).area < 1.0                       # no overlap
    assert abs(unary_union([a, b]).area - (n * step) ** 2) < 5.0   # no gap
    before = max(len(lower.exterior.coords), len(upper.exterior.coords))
    assert max(len(a.exterior.coords), len(b.exterior.coords)) <= 8 < before  # straightened
    assert {out[0][1]["i"], out[1][1]["i"]} == {0, 1}          # props kept


def test_regularize_absorbs_small_gap_into_neighbour():
    from shapely.geometry import box as bx
    from crop_analysis.field_delineation import regularize_partition

    left, right = bx(0, 0, 50, 100), bx(52, 0, 100, 100)     # 2 m sliver between
    out = regularize_partition([[left, {}], [right, {}]], bx(0, 0, 100, 100),
                               tolerance_m=1.0, fill_gap_m2=500, min_area_m2=10)
    total = sum(p.area for p, _ in out)
    assert abs(total - 100 * 100) < 1.0
