"""Raster-engine monitoring exports: record CSV, legend keying, farm report card."""
from __future__ import annotations

import csv
import io

from api.monitoring_export import RECORD_COLUMNS, monitoring_csv, monitoring_report_card


def _square(x, y, d=0.001):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


RECORD = {
    "field_id": "7", "crop": "Cotton", "status": "provisional", "area_ha": 0.42,
    "pixel_basis": "inner", "n_interior_pixels": 3, "low_resolution": True, "n_clear_looks": 21,
    "last_clear_observation": "2026-09-28", "phenology_status": "ok",
    "sowing": {"status": "estimated", "date": "2026-06-18", "p10": "2026-06-12", "p90": "2026-06-25",
               "sources": ["optical_fit", "radar"], "regime": "rainfed", "onset": "2026-06-11"},
    "das": 106, "stage": "flowering",
    "harvest": {"observed": False, "date": None,
                "window": {"kind": "multi_pick", "start": "2026-11-05", "end": "2027-01-31"}},
    "stress": {"status": "scored", "latest_date": "2026-09-28", "latest_class": "mild",
               "latest_type": "Water stress", "latest_confirmed": True, "looks_stressed": 2,
               "reference": "cohort"},
    "yield": {"basis": "index_only", "yield_index": 0.93, "index_p10": 0.8, "index_p90": 1.06,
              "yield_t_ha": None, "label": "Estimate anchored to district average; not field-calibrated."},
    "phenology_check": {"checked": False, "disagrees": False, "ratio": 0.9},
    "qa_flags": ["few_clear_looks"],
}
RESULT = {
    "engine": "raster_v1", "name": "Test village", "season": "kharif", "as_of": "2026-10-02",
    "farm_count": 1, "records": [RECORD], "onset": {"date": "2026-06-11"},
    "limits": ["Weather is village-level."],
    "fields": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _square(76.85, 18.80),
         "properties": {"field_id": "7", "crop": "Cotton", "stress": "Water stress", "color": "#0072B2"}}]},
    "zones": [],
}


def test_record_csv_has_one_row_per_field_and_the_contract_columns():
    rows = list(csv.reader(io.StringIO(monitoring_csv(RESULT))))
    assert tuple(rows[0]) == RECORD_COLUMNS
    row = dict(zip(rows[0], rows[1]))
    assert row["sowing_date"] == "2026-06-18" and row["sowing_p90"] == "2026-06-25"
    assert row["harvest_window_end"] == "2027-01-31"
    assert row["stress_latest_type"] == "Water stress"
    assert row["yield_t_ha"] == "" and row["yield_index"] == "0.93"
    assert "monsoon_onset" in monitoring_csv(RESULT)


def test_report_card_renders_without_rasters():
    png = monitoring_report_card(RESULT, "7", None)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10_000


def test_report_card_rejects_unknown_field():
    import pytest
    with pytest.raises(ValueError):
        monitoring_report_card(RESULT, "999", None)
