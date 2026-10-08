"""Cadastral co-registration (accuracy plan C1.3) on synthetic villages."""
from __future__ import annotations

import numpy as np
import pytest
from shapely.affinity import rotate, scale, translate
from shapely.geometry import box

from crop_analysis import cadastral_align as ca

RES = 10.0


def _village(rng, nx=14, ny=12):
    """Irregular plot grid ~ 1.2 x 1.0 km (plots 60-120 m), origin (500000, 2080000)."""
    xs = np.cumsum(np.r_[0, rng.uniform(60, 120, nx)]) + 500_000
    ys = np.cumsum(np.r_[0, rng.uniform(60, 120, ny)]) + 2_080_000
    return [box(xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(nx) for j in range(ny)]


def _edge_grid(plots, rng, noise=0.15, blur=0.8):
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from scipy import ndimage as ndi

    xmin = min(p.bounds[0] for p in plots) - 300
    ymax = max(p.bounds[3] for p in plots) + 300
    xmax = max(p.bounds[2] for p in plots) + 300
    ymin = min(p.bounds[1] for p in plots) - 300
    w, h = int((xmax - xmin) / RES), int((ymax - ymin) / RES)
    lines = rasterize([(p.boundary, 1) for p in plots], out_shape=(h, w),
                      transform=from_origin(xmin, ymax, RES, RES), all_touched=True).astype(float)
    edge = ndi.gaussian_filter(lines, blur) + rng.normal(0, noise, (h, w)).clip(0)
    # Image edges are incomplete: drop 30% of the line pixels.
    edge *= rng.random((h, w)) > 0.3
    return ca.EdgeGrid(edge, xmin, ymax, 32643, RES)


def _displace(plots, dx=70.0, dy=-45.0, rot=1.2, sc=1.01):
    from shapely.ops import unary_union
    c = unary_union(plots).centroid
    return [translate(scale(rotate(p, rot, origin=c), sc, sc, origin=c), dx, dy) for p in plots]


def test_shifted_rotated_survey_map_is_recovered_within_a_pixel():
    rng = np.random.default_rng(11)
    truth = _village(rng)
    grid = _edge_grid(truth, rng)
    survey = _displace(truth)
    a = ca.align(survey, grid)
    assert a.passed, a.reason
    moved = ca.transform_polygons(survey, a)
    err = [np.hypot(*(np.asarray(m.exterior.coords)[:4] - np.asarray(t.exterior.coords)[:4]).T).mean()
           for m, t in zip(moved, truth)]
    assert np.median(err) <= RES
    assert a.edge_agreement > a.edge_agreement_before
    assert a.residual_m is not None and a.residual_m <= ca.MAX_RESIDUAL_M


def test_unrelated_edges_fail_the_gate():
    rng = np.random.default_rng(5)
    truth = _village(rng)
    other = _village(np.random.default_rng(99))
    grid = _edge_grid(other, rng)
    a = ca.align(truth, grid)
    assert not a.passed
    assert "image edge" in a.reason or "residual" in a.reason


def test_constrain_segments_splits_a_segment_on_an_aligned_plot_line():
    from shapely.geometry import mapping

    from crop_analysis.field_delineation import _to_wgs

    epsg = 32643
    left, right = box(500000, 2080000, 500080, 2080100), box(500080, 2080000, 500160, 2080100)
    seg = box(500000, 2080000, 500160, 2080100)          # delineation merged two plots
    plots = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(_to_wgs(left, epsg)), "properties": {"survey_no": "12/1"}},
        {"type": "Feature", "geometry": mapping(_to_wgs(right, epsg)), "properties": {"survey_no": "12/2"}},
    ]}
    out = ca.constrain_segments([{"type": "Feature", "geometry": mapping(_to_wgs(seg, epsg)),
                                  "properties": {"field_id": 1}}], plots, epsg=epsg)
    assert len(out) == 2
    assert {f["properties"]["survey_no"] for f in out} == {"12/1", "12/2"}
    assert all(f["properties"]["boundary_source"] == "aligned_cadastral" for f in out)


def test_classification_applies_aligned_plots_to_delineated_segments(monkeypatch):
    from shapely.geometry import mapping
    from shapely.ops import unary_union

    from crop_analysis import area_classifier as ac
    from crop_analysis import field_delineation as fd
    from crop_analysis.field_delineation import _to_wgs

    rng = np.random.default_rng(3)
    truth = _village(rng, nx=10, ny=8)
    eg = _edge_grid(truth, rng)
    bands = np.zeros((*eg.edge.shape, len(fd.BOUNDARY_BANDS)))
    bands[..., fd.BOUNDARY_BANDS.index("s2")] = eg.edge
    ba = fd.BoundaryArrays(bands, eg.x0, eg.y1, eg.epsg, 2026)
    monkeypatch.setattr(fd, "fetch_boundary_arrays", lambda aoi, year: ba)

    survey = _displace(truth, dx=55, dy=30, rot=0.8, sc=1.0)
    plots = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": mapping(_to_wgs(p, 32643)), "properties": {"survey_no": f"S{i}"}}
        for i, p in enumerate(survey)]}
    # Delineation merged the first two plots of the first column into one segment.
    merged = unary_union([truth[0], truth[1]])
    objs = [{"field_id": 1, "geometry": mapping(_to_wgs(merged, 32643)), "area_ha": merged.area / 1e4,
             "series": {}, "boundary_source": "watershed"}]
    aoi = mapping(_to_wgs(unary_union(truth).envelope, 32643))
    inputs = ac.ClassifyInputs(year=2026, cadastral_plots=plots, min_field_area_ha=0.2)
    out = ac._apply_cadastral([{"boundary": aoi}], inputs, objs, lambda *a, **k: None)
    assert ac._ALIGNMENT_REPORT["passed"], ac._ALIGNMENT_REPORT
    assert len(out) == 2
    assert {o["survey_no"] for o in out} == {"S0", "S1"}
    assert all(o["boundary_source"] == "aligned_cadastral" for o in out)
