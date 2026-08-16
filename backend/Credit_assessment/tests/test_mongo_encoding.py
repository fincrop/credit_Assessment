"""
Tests for utils/mongo_encoding.py

These lock in the behaviour that matters for data integrity:
NaN must not become 0, numpy must not reach PyMongo, and datetimes must be
comparable with each other.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from utils.mongo_encoding import as_utc, count_nulls, to_mongo, utc_now


# ── NaN / Inf: absent, never zero ─────────────────────────────────────────

def test_nan_and_inf_become_none_not_zero():
    out = to_mongo({"a": float("nan"), "b": float("inf"), "c": float("-inf"), "d": 0.0})
    assert out["a"] is None, "NaN must encode as None (missing), not 0.0"
    assert out["b"] is None
    assert out["c"] is None
    assert out["d"] == 0.0, "a real zero must survive as a zero"


def test_numpy_nan_in_series_becomes_none():
    series = np.array([0.61, np.nan, 0.58, np.inf])
    out = to_mongo(series)
    assert out == [0.61, None, 0.58, None]


def test_nan_never_encodes_to_a_falsy_number():
    for v in (float("nan"), np.float64("nan"), np.float32("nan")):
        assert to_mongo(v) is None


# ── numpy scalars ─────────────────────────────────────────────────────────

def test_numpy_scalars_are_converted():
    out = to_mongo({
        "f64": np.float64(1.5),
        "f32": np.float32(2.5),
        "i64": np.int64(7),
        "i32": np.int32(8),
        "bool": np.bool_(True),
    })
    assert out == {"f64": 1.5, "f32": 2.5, "i64": 7, "i32": 8, "bool": True}
    for key, expected in (("f64", float), ("i64", int), ("bool", bool)):
        assert type(out[key]) is expected, f"{key} must be a builtin {expected}"


def test_nested_numpy_array_is_flattened_to_lists():
    out = to_mongo({"grid": np.array([[1.0, 2.0], [3.0, np.nan]])})
    assert out["grid"] == [[1.0, 2.0], [3.0, None]]


def test_numpy_bool_is_not_mistaken_for_int():
    assert to_mongo(np.bool_(False)) is False
    assert to_mongo(False) is False
    # Python bool is a subclass of int; make sure ordering in to_mongo holds.
    assert type(to_mongo(True)) is bool


# ── datetimes ─────────────────────────────────────────────────────────────

def test_naive_datetime_becomes_utc_aware():
    out = to_mongo(datetime(2026, 8, 15, 12, 30))
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(0)


def test_aware_datetime_is_preserved():
    src = datetime(2026, 8, 15, 12, 30, tzinfo=timezone.utc)
    assert to_mongo(src) == src


def test_date_becomes_datetime():
    out = to_mongo(date(2026, 8, 15))
    assert isinstance(out, datetime) and out.tzinfo is not None


def test_naive_and_aware_become_comparable():
    """The whole point: two write paths used different conventions."""
    a = to_mongo(datetime(2026, 8, 15, 12, 0))                       # was naive
    b = to_mongo(datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc))  # was aware
    assert b > a, "encoded datetimes from both write paths must be comparable"


def test_iso_strings_parse_including_z_suffix():
    assert as_utc("2026-08-15T12:00:00Z").hour == 12
    assert as_utc("2026-08-15T12:00:00+00:00").hour == 12
    assert as_utc("not a date") is None
    assert as_utc("") is None
    assert as_utc(None) is None


def test_utc_now_is_aware():
    assert utc_now().tzinfo is not None


# ── keys ──────────────────────────────────────────────────────────────────

def test_illegal_bson_keys_are_sanitised():
    out = to_mongo({"$set": 1, "a.b": 2, 3: "int key"})
    assert "_set" in out and "$set" not in out
    assert "a_b" in out and "a.b" not in out
    assert "3" in out


# ── failure modes ─────────────────────────────────────────────────────────

def test_unknown_type_raises_rather_than_stringifying():
    class Opaque:
        pass

    with pytest.raises(TypeError):
        to_mongo({"x": Opaque()})


def test_deep_nesting_raises():
    deep: dict = {}
    node = deep
    for _ in range(80):
        node["n"] = {}
        node = node["n"]
    with pytest.raises(ValueError):
        to_mongo(deep)


# ── realistic payload ─────────────────────────────────────────────────────

def test_realistic_satellite_series_encodes_cleanly():
    """A bin series with cloud gaps — the shape that actually reaches Mongo."""
    payload = {
        "dates": ["2023-06-22", "2023-07-02"],
        "ndvi": np.array([0.61, np.nan]),
        "bin_quality": [np.float64(0.82), np.float64(0.0)],
        "missing": [np.bool_(False), np.bool_(True)],
        "signal_source": ["optical", "imputed"],
        "n_valid": np.int64(1),
        "created_at": datetime(2026, 8, 15, 9, 0),
    }
    out = to_mongo(payload)

    assert out["ndvi"] == [0.61, None]
    assert out["missing"] == [False, True]
    assert out["n_valid"] == 1 and type(out["n_valid"]) is int
    assert out["created_at"].tzinfo is not None
    # One null, from the cloud-gapped bin.
    assert count_nulls(out["ndvi"]) == 1


def test_encoded_payload_contains_no_numpy_or_nan():
    """Belt-and-braces sweep over a nested structure."""
    payload = {
        "series": {"ndvi": np.array([0.1, np.nan]), "flag": np.bool_(True)},
        "cycles": [{"peak": np.float32(0.7), "n": np.int16(3)}],
    }
    out = to_mongo(payload)

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        else:
            assert not isinstance(v, np.generic), f"numpy leaked: {v!r}"
            assert not isinstance(v, np.ndarray), f"ndarray leaked: {v!r}"
            if isinstance(v, float):
                assert math.isfinite(v), f"non-finite float leaked: {v!r}"

    walk(out)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
