"""A complete scene folder and a saved weather file are a monitoring rerun.
Neither call may touch Earth Engine."""

from __future__ import annotations

import json
from datetime import date

import numpy as np

from src.models import WeatherDay
from src.raster import fetch
from src.raster.grid import Grid

START = date(2026, 5, 1)
END = date(2026, 10, 1)
GRID = Grid(32643, 500000.0, 2_000_000.0, 2, 2, 10.0)


def _scene(bands):
    return {b: np.ones((2, 2), np.float32) for b in bands}


def test_complete_sentinel2_index_does_not_call_earth_engine(tmp_path, monkeypatch):
    day = date(2026, 6, 1)
    bands = fetch.S2_BANDS + ["cs_cdf"]
    np.savez_compressed(tmp_path / f"s2_{day.isoformat()}.npz", **_scene(bands))
    (tmp_path / "s2_index.json").write_text(json.dumps({
        "recipe": fetch.S2_RECIPE, "start": START.isoformat(), "end": END.isoformat(),
        "items": [{"date": day.isoformat()}],
    }), encoding="utf-8")

    def boom():
        raise AssertionError("Earth Engine was called")

    monkeypatch.setattr(fetch, "_ee", boom)
    stack = fetch.fetch_s2(GRID, START, END, tmp_path)
    assert stack.dates == [day]
    assert stack.bands["B8"].shape == (1, 2, 2)


def test_complete_sentinel1_index_does_not_call_earth_engine(tmp_path, monkeypatch):
    day = date(2026, 6, 9)
    np.savez_compressed(tmp_path / f"s1_{day.isoformat()}_DES63.npz", **_scene(fetch.S1_BANDS))
    (tmp_path / "s1_index.json").write_text(json.dumps({
        "recipe": fetch.S1_RECIPE, "start": START.isoformat(), "end": END.isoformat(),
        "items": [{"date": day.isoformat(), "pass": "DESCENDING", "orbit": 63}],
    }), encoding="utf-8")
    def boom():
        raise AssertionError("Earth Engine was called")

    monkeypatch.setattr(fetch, "_ee", boom)
    stack = fetch.fetch_s1(GRID, START, END, tmp_path)
    assert stack.dates == [day]
    assert stack.meta[0]["relative_orbit"] == 63
    assert stack.meta[0]["pass"] == "DESCENDING"


def test_weather_is_reused_and_a_later_date_fetches_only_the_tail(tmp_path, monkeypatch):
    calls = []

    def fake(grid, start, end):
        calls.append((start, end))
        return [WeatherDay(date=start, rain_p50=float((start - START).days))]

    monkeypatch.setattr(fetch, "_pull_weather", fake)
    monkeypatch.setattr(fetch, "_ee", lambda: (_ for _ in ()).throw(AssertionError("Earth Engine was called")))

    first = fetch.fetch_weather(GRID, START, date(2026, 6, 1), tmp_path)
    second = fetch.fetch_weather(GRID, START, date(2026, 6, 1), tmp_path)
    later = fetch.fetch_weather(GRID, START, date(2026, 7, 1), tmp_path)
    again = fetch.fetch_weather(GRID, START, date(2026, 7, 1), tmp_path)

    assert calls == [(START, date(2026, 6, 1)), (date(2026, 6, 2), date(2026, 7, 1))]
    assert [d.date for d in first] == [START]
    assert [d.date for d in second] == [START]
    assert [d.date for d in later] == [START, date(2026, 6, 2)]
    assert [d.date for d in again] == [d.date for d in later]


def test_empty_weather_tail_does_not_advance_the_cache(tmp_path, monkeypatch):
    def fake(grid, start, end):
        if start == START:
            return [WeatherDay(date=START, rain_p50=3.0)]
        return []

    monkeypatch.setattr(fetch, "_pull_weather", fake)
    fetch.fetch_weather(GRID, START, date(2026, 6, 1), tmp_path)
    short = fetch.fetch_weather(GRID, START, date(2026, 7, 1), tmp_path)
    assert [d.date for d in short] == [START]
    doc = json.loads((tmp_path / "weather.json").read_text(encoding="utf-8"))
    assert doc["end"] == "2026-06-01"
