"""Monitoring runner helpers for the raster engine."""
from __future__ import annotations

from api import monitoring_runner as mr


def test_engine_defaults_to_raster(monkeypatch):
    monkeypatch.delenv("MONITORING_ENGINE", raising=False)
    assert mr._engine() == "raster"
    monkeypatch.setenv("MONITORING_ENGINE", "point")
    assert mr._engine() == "point"


def test_large_documents_drop_intervals_and_say_so(monkeypatch):
    monkeypatch.setattr(mr, "MAX_RESULT_BYTES", 2_000)
    doc = {"zones": [{"zone_id": f"{i}-z1", "intervals": [{"date": "2026-09-01", "x": "y" * 50}] * 5}
                     for i in range(20)]}
    out = mr._fit_document(doc)
    assert all("intervals" not in z for z in out["zones"])
    assert "local monitoring file" in out["trimmed"]["reason"]


def test_small_documents_are_untouched():
    doc = {"zones": [{"zone_id": "1-z1", "intervals": [{"date": "2026-09-01"}]}]}
    assert mr._fit_document(doc) is doc and "trimmed" not in doc


def test_typed_district_yield_is_used_only_for_a_single_crop_without_a_table(monkeypatch, tmp_path):
    rows = mr._district_rows({"district_yield_t_ha": "1.4"}, ["Cotton"])
    # The shipped evaluation/reference/district_yields.csv is a header-only
    # template, so the typed figure is the only source.
    assert rows == [{"crop": "Cotton", "year": 0, "yield_t_ha": 1.4,
                     "source": "entered on the monitoring request"}]
    assert mr._district_rows({"district_yield_t_ha": "1.4"}, ["Cotton", "Soyabean"]) == []


def test_jsonable_turns_dates_into_strings():
    from datetime import date, datetime
    out = mr._jsonable({"d": date(2026, 6, 20), "t": datetime(2026, 6, 20, 5), "l": [date(2026, 7, 1)]})
    assert out["d"] == "2026-06-20" and out["l"] == ["2026-07-01"]
    assert isinstance(out["t"], datetime)
