"""Earth Engine downloads are written once and read back. A second call must
not invoke the download."""

from __future__ import annotations

from datetime import date

import numpy as np

from crop_analysis import field_delineation as fd
from crop_analysis.field_delineation import BoundaryArrays
from crop_analysis.fused_classifier import fetch_series

AOI = {"type": "Polygon", "coordinates": [[[76.0, 18.0], [76.1, 18.0], [76.1, 18.1], [76.0, 18.0]]]}
GEOM = {"type": "Polygon", "coordinates": [[[76.0, 18.0], [76.01, 18.0], [76.01, 18.01], [76.0, 18.0]]]}


def test_boundary_arrays_second_call_does_not_download(tmp_path, monkeypatch):
    calls = []
    saved = BoundaryArrays(np.arange(4 * 4 * 4, dtype=np.float32).reshape(4, 4, 4), 1000.0, 2000.0, 32643, 2026)

    def fake(ee, aoi, year, months):
        calls.append((year, months))
        return saved

    monkeypatch.setattr(fd, "_download_boundary", fake)
    first = fd.fetch_boundary_arrays(AOI, year=2026, cache_root=tmp_path)
    second = fd.fetch_boundary_arrays(AOI, year=2026, cache_root=tmp_path)
    assert calls == [(2026, None)]
    assert np.array_equal(first.bands, second.bands)
    assert second.x0 == 1000.0 and second.epsg == 32643 and second.year == 2026


def test_profile_arrays_second_call_does_not_download(tmp_path, monkeypatch):
    calls = []
    ba = BoundaryArrays(np.zeros((3, 5, 4), np.float32), 1000.0, 2000.0, 32643, 2026)
    months = [4, 5, 6]
    arr = np.full((3, 5, 9), 0.4, np.float32)
    arr[0, 0, 0] = np.nan

    def fake(ee, aoi, ba, year, months):
        calls.append(list(months))
        return arr.copy()

    monkeypatch.setattr(fd, "_download_profiles", fake)
    first = fd.fetch_profile_arrays(AOI, ba, year=2026, months=months, cache_root=tmp_path)
    second = fd.fetch_profile_arrays(AOI, ba, year=2026, months=months, cache_root=tmp_path)
    assert calls == [months]
    assert np.array_equal(first, second, equal_nan=True)


def test_series_second_call_skips_fields_already_stored(tmp_path, monkeypatch):
    calls = []

    def fake(ee, geoms, lo, hi, blocks):
        calls.append([fid for fid, _ in geoms])
        return {fid: {"s1": [{"date": "2026-06-06", "VV": -10.0, "VH": -16.0}],
                      "refl": [{"date": "2026-06-06", "B8": 0.3, "B4": 0.1}]}
                for fid, _ in geoms}

    monkeypatch.setattr("data_acquisition.extra_sources.fetch_time_series", fake)
    objects = [
        {"field_id": "a", "geometry": GEOM},
        {"field_id": "b", "geometry": {**GEOM, "coordinates": [[[77.0, 18.0], [77.01, 18.0], [77.01, 18.01], [77.0, 18.0]]]}},
    ]
    lo, hi = date(2026, 5, 1), date(2026, 10, 1)
    first = fetch_series(None, objects, lo, hi, cache_root=tmp_path)
    second = fetch_series(None, objects, lo, hi, cache_root=tmp_path)
    third = fetch_series(None, objects[:1], lo, hi, cache_root=tmp_path)
    assert calls == [["a", "b"]]
    assert first["a"]["s1"][0]["VV"] == -10.0
    assert second["b"]["refl"][0]["B8"] == 0.3
    assert third["a"] == first["a"]


def test_failed_series_chunk_is_not_cached(tmp_path, monkeypatch):
    calls = []

    def fake(ee, geoms, lo, hi, blocks):
        calls.append(1)
        raise RuntimeError("429 quota")

    monkeypatch.setattr("data_acquisition.extra_sources.fetch_time_series", fake)
    objects = [{"field_id": "a", "geometry": GEOM}]
    lo, hi = date(2026, 5, 1), date(2026, 10, 1)
    assert fetch_series(None, objects, lo, hi, cache_root=tmp_path) == {}
    assert fetch_series(None, objects, lo, hi, cache_root=tmp_path) == {}
    assert calls == [1, 1]
    assert list(tmp_path.rglob("*.json")) == []
