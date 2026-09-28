"""
Tests for area-wide crop classification.

Everything here is the non-GEE half: geometry maths, season windows, scene
assembly, result aggregation and export. The GEE stages need live credentials
and minutes of round-trips, so they are exercised by running a real job rather
than in the unit suite -- but the logic that decides what a result *means* is
all testable offline, and that is where the arithmetic errors would hide.
"""
from __future__ import annotations

from datetime import date
import os

import numpy as np
import pytest

from crop_analysis.crop_detector import EXTRACTOR_VERSION
from crop_analysis.area_classifier import (
    ClassifyInputs,
    NON_CROP_CLASSES,
    SNIC_COMPACTNESS,
    SNIC_NEIGHBORHOOD,
    SNIC_SEED_SPACING,
    WORLDCOVER_ASSET,
    WORLDCOVER_CROPLAND,
    _apply_cropland_mask,
    _bins_for,
    _cycle_date,
    _pick_cycle,
    _representative_latlon,
    _scenes_from_series,
    _worldcover_image,
    build_result,
    class_color,
    classify_objects,
    geometry_area_ha,
    geometry_centroid,
    merge_field_objects,
    season_window,
)
from api.classification_export import result_to_csv, result_to_geotiff, result_to_png


def square(x: float, y: float, d: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]],
    }


# =============================================================================
# geometry
# =============================================================================
def test_area_of_known_square():
    # 0.01 deg at 23N is ~1.023 km E-W and ~1.107 km N-S -> ~113 ha.
    ha = geometry_area_ha(square(77.0, 23.0, 0.01))
    assert 100 < ha < 125


def test_area_subtracts_holes():
    outer = square(77.0, 23.0, 0.01)["coordinates"][0]
    inner = square(77.002, 23.002, 0.004)["coordinates"][0]
    solid = geometry_area_ha({"type": "Polygon", "coordinates": [outer]})
    holed = geometry_area_ha({"type": "Polygon", "coordinates": [outer, inner]})
    assert holed < solid
    assert holed == pytest.approx(solid - geometry_area_ha(square(77.002, 23.002, 0.004)), rel=0.02)


def test_multipolygon_area_sums_parts():
    a = square(77.0, 23.0, 0.01)
    b = square(78.0, 23.0, 0.01)
    multi = {"type": "MultiPolygon", "coordinates": [a["coordinates"], b["coordinates"]]}
    assert geometry_area_ha(multi) == pytest.approx(
        geometry_area_ha(a) + geometry_area_ha(b), rel=1e-6
    )


def test_centroid_is_inside_the_square():
    c = geometry_centroid(square(77.0, 23.0, 0.01))
    assert 77.0 <= c["lng"] <= 77.01
    assert 23.0 <= c["lat"] <= 23.01


def test_degenerate_geometry_is_zero_not_an_error():
    assert geometry_area_ha({"type": "Point", "coordinates": [77.0, 23.0]}) == 0.0
    assert geometry_area_ha({}) == 0.0


# =============================================================================
# season windows
# =============================================================================
@pytest.mark.parametrize("season", ["kharif", "rabi", "zaid", "whole_year"])
def test_window_is_ordered_and_long_enough_to_hold_a_cycle(season):
    d0, d1 = season_window(season, 2024)
    assert d0 < d1
    # The shortest reference crop is Bajra at 65 days; a window that cannot
    # contain one plus its shoulders cannot produce a cycle at all.
    assert (d1 - d0).days >= 120


def test_rabi_window_crosses_the_year_boundary():
    d0, d1 = season_window("rabi", 2024)
    assert d0.year == 2024 and d1.year == 2025


def test_unknown_season_falls_back_to_whole_year():
    assert season_window("nonsense", 2024) == season_window("whole_year", 2024)


def test_bins_are_ten_days_apart():
    bins = _bins_for(date(2024, 6, 1), date(2024, 7, 1))
    assert bins[0] == date(2024, 6, 1)
    assert all((bins[i + 1] - bins[i]).days == 10 for i in range(len(bins) - 1))


# =============================================================================
# scene assembly
# =============================================================================
def test_missing_bins_become_nan_not_zero():
    """A cloud gap read as 0.0 would look like bare soil to the model."""
    bins = ["2024-06-01", "2024-06-11", "2024-06-21"]
    series = {"2024-06-01": {"NDVI_mean": 0.5, "EVI_mean": 0.3}}
    scenes, n_real = _scenes_from_series(series, bins)

    assert n_real == 1
    assert len(scenes) == 3
    assert scenes[0]["missing"] is False
    assert scenes[1]["missing"] is True
    assert np.isnan(scenes[1]["indices"]["NDVI_mean"])


def test_non_finite_values_are_treated_as_missing():
    bins = ["2024-06-01"]
    scenes, n_real = _scenes_from_series({"2024-06-01": {"NDVI_mean": None}}, bins)
    assert n_real == 0
    assert scenes[0]["missing"] is True


def test_scene_order_follows_the_bin_grid():
    bins = ["2024-06-01", "2024-06-11", "2024-06-21"]
    series = {b: {"NDVI_mean": 0.4} for b in bins}
    scenes, _ = _scenes_from_series(series, bins)
    assert [s["date"] for s in scenes] == bins


# =============================================================================
# cycle selection
# =============================================================================
def test_picks_the_cycle_overlapping_the_season_not_the_longest():
    """A perennial spanning the whole year must not outrank the season's own
    cycle just by being longer."""
    season_cycle = {"start_date": "2024-06-01", "end_date": "2024-10-01"}
    perennial = {"start_date": "2023-01-01", "end_date": "2024-05-01"}
    picked = _pick_cycle([perennial, season_cycle], date(2024, 5, 1), date(2024, 12, 15))
    assert picked is season_cycle


def test_no_overlapping_cycle_returns_none():
    far_off = {"start_date": "2020-01-01", "end_date": "2020-06-01"}
    assert _pick_cycle([far_off], date(2024, 5, 1), date(2024, 12, 15)) is None


def test_unparseable_cycle_dates_are_skipped():
    assert _pick_cycle([{"start_date": "", "end_date": None}],
                       date(2024, 5, 1), date(2024, 12, 15)) is None


def test_cycle_date_reads_sowing_and_harvest_aliases():
    """CropCycle objects expose sowing_date/harvest_date, not start_date/end_date."""
    from datetime import datetime

    class _Cycle:
        sowing_date = datetime(2024, 6, 1)
        harvest_date = datetime(2024, 10, 1)

    assert _cycle_date(_Cycle(), "start_date") == date(2024, 6, 1)
    assert _cycle_date(_Cycle(), "end_date") == date(2024, 10, 1)
    picked = _pick_cycle([_Cycle()], date(2024, 5, 1), date(2024, 12, 15))
    assert picked is not None


# =============================================================================
# result aggregation
# =============================================================================
def _objects():
    return [
        {"field_id": 1, "geometry": square(77.00, 23.0, 0.003), "area_ha": 10.0,
         "centroid": {"lat": 23.0, "lng": 77.00}, "crop": "Rice", "confidence": 0.82},
        {"field_id": 2, "geometry": square(77.01, 23.0, 0.003), "area_ha": 6.0,
         "centroid": {"lat": 23.0, "lng": 77.01}, "crop": "Wheat", "confidence": 0.61},
        {"field_id": 3, "geometry": square(77.02, 23.0, 0.002), "area_ha": 4.0,
         "centroid": {"lat": 23.0, "lng": 77.02}, "crop": "Abstained",
         "confidence": 0.18, "note": "below threshold"},
    ]


def _result():
    aoi = [{"name": "Test village", "boundary": square(77.0, 23.0, 0.03)}]
    return build_result(_objects(), aoi, ClassifyInputs(season="kharif", year=2024),
                        ["2024-06-01", "2024-06-11"], "tier1_v1")


def test_abstentions_are_excluded_from_classified_area():
    r = _result()
    assert r["classified_area_ha"] == 10.0 + 6.0
    assert r["unclassified_area_ha"] == 4.0


def test_shares_are_over_mapped_area_and_sum_to_one():
    r = _result()
    assert sum(s["area_share"] for s in r["stats"]) == pytest.approx(1.0, abs=1e-6)


def test_mean_confidence_ignores_abstentions():
    """Averaging in a 0.18 abstention would understate how sure the model is
    about the fields it actually named."""
    r = _result()
    assert r["mean_confidence"] == pytest.approx((0.82 + 0.61) / 2, abs=1e-4)


def test_stats_are_ordered_by_area_descending():
    areas = [s["area_ha"] for s in _result()["stats"]]
    assert areas == sorted(areas, reverse=True)


def test_every_feature_carries_a_colour_and_the_crop():
    r = _result()
    for f in r["fields"]["features"]:
        p = f["properties"]
        assert p["color"].startswith("#")
        assert p["crop"]
        assert "area_ha" in p and "confidence" in p


def test_notes_survive_onto_the_feature():
    r = _result()
    abstained = [f for f in r["fields"]["features"]
                 if f["properties"]["crop"] == "Abstained"][0]
    assert abstained["properties"]["note"] == "below threshold"


def test_field_count_covers_unclassified_objects_too():
    assert _result()["field_count"] == 3


def test_empty_objects_do_not_divide_by_zero():
    aoi = [{"name": "Empty", "boundary": square(77.0, 23.0, 0.01)}]
    r = build_result([], aoi, ClassifyInputs(), ["2024-06-01"], None)
    assert r["field_count"] == 0
    assert r["mean_confidence"] == 0.0
    assert r["stats"] == []


# =============================================================================
# colours and classes
# =============================================================================
def test_colour_is_stable_per_crop():
    """Two runs must colour Rice identically or their maps cannot be compared."""
    assert class_color("Rice") == class_color("Rice")
    assert class_color("Rice") != class_color("Wheat")


def test_unknown_class_gets_a_fallback_colour():
    assert class_color("Sunflower").startswith("#")


def test_abstained_counts_as_non_crop():
    assert "Abstained" in NON_CROP_CLASSES
    assert "Rice" not in NON_CROP_CLASSES


# =============================================================================
# inputs
# =============================================================================
def test_inputs_defaults_survive_an_empty_dict():
    i = ClassifyInputs.from_dict({})
    assert i.season == "kharif"
    assert i.confidence_threshold == 0.25
    assert i.apply_region_guard is True


def test_inputs_keep_explicit_false():
    """`or`-style coercion would silently re-enable a guard the user turned off."""
    i = ClassifyInputs.from_dict({"apply_region_guard": False, "apply_season_mask": False})
    assert i.apply_region_guard is False
    assert i.apply_season_mask is False


def test_region_name_is_the_result_label():
    """History and downloads key off aoi_name; a typed region must win over
    the drawn-polygon default ('Drawn area 1')."""
    aoi = [{"name": "Drawn area 1", "boundary": square(77.0, 23.0, 0.03)}]
    r = build_result(
        _objects(), aoi,
        ClassifyInputs(season="kharif", year=2024, region_name="  Kheda village  "),
        ["2024-06-01"], "tier1_v1",
    )
    assert r["aoi_name"] == "Kheda village"


def test_blank_region_name_falls_back_to_the_area_name():
    aoi = [{"name": "Drawn area 1", "boundary": square(77.0, 23.0, 0.03)}]
    r = build_result(_objects(), aoi, ClassifyInputs(season="kharif", year=2024),
                     ["2024-06-01"], "tier1_v1")
    assert r["aoi_name"] == "Drawn area 1"


# =============================================================================
# export
# =============================================================================
def test_csv_holds_a_row_per_field_and_the_summary():
    csv_text = result_to_csv(_result())
    lines = csv_text.splitlines()
    assert lines[0].startswith("field_id,")
    assert any(line.startswith("1,Rice,") for line in lines)
    assert "# Summary" in csv_text
    assert "# Provenance" in csv_text


def test_csv_escapes_nothing_it_should_not_and_stays_parseable():
    import csv as _csv
    import io as _io

    rows = list(_csv.reader(_io.StringIO(result_to_csv(_result()))))
    header = rows[0]
    assert header == ["field_id", "crop", "area_ha", "confidence", "lon", "lat", "note"]
    # first three data rows are the fields
    assert rows[1][1] == "Rice"
    assert rows[3][1] == "Abstained"


def test_geotiff_is_a_paletted_classified_raster():
    from rasterio.io import MemoryFile

    blob = result_to_geotiff(_result())
    assert blob[:4] in (b"II*\x00", b"MM\x00*")  # little/big-endian TIFF
    with MemoryFile(blob) as mem:
        with mem.open() as ds:
            assert ds.count == 1
            assert ds.crs is not None
            data = ds.read(1)
            tags = ds.tags()
            id_of = {v: int(k.split("_", 1)[1]) for k, v in tags.items() if k.startswith("CLASS_")}
            assert id_of["Rice"] != id_of["Wheat"]
            assert id_of["Rice"] in data
            assert id_of["Wheat"] in data
            # Background (outside any field) stays nodata.
            assert 0 in data
            cmap = ds.colormap(1)
            assert cmap[id_of["Rice"]][:3] == (0xC9, 0xA2, 0x27)


def test_geotiff_refuses_an_empty_result():
    aoi = [{"name": "Empty", "boundary": square(77.0, 23.0, 0.01)}]
    empty = build_result([], aoi, ClassifyInputs(), ["2024-06-01"], None)
    with pytest.raises(ValueError, match="No fields"):
        result_to_geotiff(empty)


def test_png_is_a_png_with_a_legend_sized_payload():
    import io as _io
    from PIL import Image

    blob = result_to_png(_result())
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(_io.BytesIO(blob))
    assert img.format == "PNG"
    assert img.size[0] >= 400
    assert img.size[1] >= 100


# =============================================================================
# WorldCover loading
# =============================================================================
class _FakeMap:
    def __init__(self, asset):
        self.asset = asset
        self.selected = None

    def mosaic(self):
        self.mosaicked = True
        return self

    def select(self, band):
        self.selected = band
        return self

    def eq(self, value):
        self.compared_to = value
        return self


class _FakeEE:
    def __init__(self):
        self.image_ids = []
        self.collection_ids = []

    def Image(self, asset):
        self.image_ids.append(asset)
        raise AssertionError(
            f"WorldCover must not be loaded as ee.Image({asset!r}); "
            "it is an ImageCollection"
        )

    def ImageCollection(self, asset):
        self.collection_ids.append(asset)
        return _FakeMap(asset)


def test_worldcover_loads_as_collection_mosaic_not_an_image():
    """Regression: ee.Image('ESA/WorldCover/v200') fails at getInfo with
    'Asset ... is not an Image' because v200 is an ImageCollection."""
    ee = _FakeEE()
    img = _worldcover_image(ee)
    assert ee.collection_ids == [WORLDCOVER_ASSET]
    assert ee.image_ids == []
    assert img.mosaicked is True
    assert img.selected == "Map"


def test_cropland_mask_uses_worldcover_class_40():
    ee = _FakeEE()

    class _Clusters:
        def updateMask(self, mask):
            self.mask = mask
            return self

    clusters = _Clusters()
    masked, used = _apply_cropland_mask(ee, clusters)
    assert used is True
    assert masked is clusters
    assert clusters.mask.compared_to == WORLDCOVER_CROPLAND


def test_cropland_mask_falls_back_when_worldcover_cannot_be_built():
    class _BrokenEE:
        def ImageCollection(self, asset):
            raise RuntimeError("no credentials")

    clusters = object()
    masked, used = _apply_cropland_mask(_BrokenEE(), clusters)
    assert used is False
    assert masked is clusters


# =============================================================================
# CropDetector wiring
# =============================================================================
def test_representative_latlon_is_the_median_centroid():
    lat, lon = _representative_latlon([
        {"centroid": {"lat": 20.0, "lng": 77.0}},
        {"centroid": {"lat": 22.0, "lng": 79.0}},
        {"centroid": {"lat": 21.0, "lng": 78.0}},
    ])
    assert lat == pytest.approx(21.0)
    assert lon == pytest.approx(78.0)


def test_representative_latlon_empty_objects_is_none():
    assert _representative_latlon([]) == (None, None)
    assert _representative_latlon([{"centroid": {}}]) == (None, None)


def test_classify_objects_loads_detector_with_crop_model_path(monkeypatch):
    """Regression: CropDetector takes crop_model_path, not model_path."""
    captured: dict = {}

    class FakeCropDetector:
        def __init__(self, crop_model_path, latitude=None, longitude=None,
                     verbose=True, **kwargs):
            captured["init"] = {
                "crop_model_path": crop_model_path,
                "latitude": latitude,
                "longitude": longitude,
                "verbose": verbose,
                "kwargs": kwargs,
            }

        def _classify_crop_chronological(self, scenes, cycle=None):
            return {
                "all_probabilities": {"Rice": 0.85, "Wheat": 0.15},
                "abstained": False,
                "crop": "Rice",
                "confidence": 0.85,
            }

    class FakeCycleDetector:
        def detect_cycles(self, **kwargs):
            return [{"start_date": "2024-06-01", "end_date": "2024-10-01",
                     "duration_days": 122}]

    monkeypatch.setattr("crop_analysis.crop_detector.CropDetector", FakeCropDetector)
    monkeypatch.setattr(
        "crop_analysis.crop_cycle_detector.CropCycleDetector", FakeCycleDetector
    )

    bins = ["2024-06-01", "2024-06-11", "2024-06-21",
            "2024-07-01", "2024-07-11", "2024-07-21"]
    rec = {"NDVI_mean": 0.5, "EVI_mean": 0.4, "NDMI_mean": 0.2}
    objects = [{
        "field_id": 1,
        "geometry": square(77.0, 23.0, 0.003),
        "area_ha": 10.0,
        "centroid": {"lat": 23.1, "lng": 77.2},
        "series": {b: rec for b in bins},
    }]

    out = classify_objects(
        objects, bins, ClassifyInputs(season="kharif", year=2024),
        lambda *a, **k: None,
    )

    assert captured["init"]["kwargs"] == {}
    assert str(captured["init"]["crop_model_path"]).endswith(
        "crop_classifier_tier1_v1.joblib"
    )
    assert captured["init"]["latitude"] == pytest.approx(23.1)
    assert captured["init"]["longitude"] == pytest.approx(77.2)
    assert captured["init"]["verbose"] is False
    assert objects[0]["crop"] == "Rice"
    assert out["model_version"] == EXTRACTOR_VERSION
    assert os.environ.get("PHENO_FIT_DISABLE") == "1"


# =============================================================================
# field-boundary SNIC
# =============================================================================
def test_snic_follows_edges_not_a_square_grid():
    """Compactness 0.5 on a seed lattice produced chessboard blocks the size
    of the seed spacing. Spectral compactness lets clusters stop at bunds."""
    assert SNIC_COMPACTNESS <= 0.15
    assert SNIC_NEIGHBORHOOD == 2 * SNIC_SEED_SPACING


# =============================================================================
# field-polygon cleanup
# =============================================================================
def _field(fid: int, geom: dict, crop: str, ha: float, conf: float = 0.8) -> dict:
    c = geometry_centroid(geom)
    return {
        "field_id": fid,
        "geometry": geom,
        "area_ha": ha,
        "centroid": c,
        "crop": crop,
        "confidence": conf,
    }


def test_adjacent_same_crop_merges_into_one_field():
    """Two SNIC pieces of Rice that share an edge are one farm, not two."""
    a = _field(1, square(77.00, 23.0, 0.003), "Rice", 10.0, 0.8)
    b = _field(2, square(77.003, 23.0, 0.003), "Rice", 10.0, 0.6)
    out = merge_field_objects([a, b], min_area_ha=0.2)
    rice = [o for o in out if o["crop"] == "Rice"]
    assert len(rice) == 1
    assert rice[0]["confidence"] == pytest.approx(0.7, abs=0.05)
    assert rice[0]["area_ha"] > 15.0


def test_disconnected_same_crop_stays_two_fields():
    a = _field(1, square(77.00, 23.0, 0.003), "Rice", 10.0)
    b = _field(2, square(77.05, 23.0, 0.003), "Rice", 10.0)
    out = merge_field_objects([a, b], min_area_ha=0.2)
    assert len([o for o in out if o["crop"] == "Rice"]) == 2


def test_adjacent_different_crops_stay_separate():
    a = _field(1, square(77.00, 23.0, 0.003), "Rice", 10.0)
    b = _field(2, square(77.003, 23.0, 0.003), "Wheat", 10.0)
    out = merge_field_objects([a, b], min_area_ha=0.2)
    crops = {o["crop"] for o in out}
    assert crops == {"Rice", "Wheat"}
    assert len(out) == 2


def test_tiny_speck_is_absorbed_into_neighbour_even_if_crop_differs():
    """A leftover SNIC sliver on a farm edge should not become its own field."""
    host = _field(1, square(77.00, 23.0, 0.01), "Rice", 100.0)
    speck = _field(2, square(77.01, 23.0, 0.0001), "Wheat", 0.02, 0.4)
    out = merge_field_objects([host, speck], min_area_ha=0.2)
    assert len(out) == 1
    assert out[0]["crop"] == "Rice"
    wheat = [o for o in out if o["crop"] == "Wheat"]
    assert wheat == []
