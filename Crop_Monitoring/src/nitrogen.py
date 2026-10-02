"""Canopy nitrogen from red-edge indices. It adjusts yield. It is not a soil test."""

from __future__ import annotations

from datetime import date

from src.library import CropPrior
from src.state import StateDay
from src.zones import Zone


def _median(values: list[float]) -> float:
    xs = sorted(values)
    n = len(xs)
    mid = n // 2
    if n % 2:
        return xs[mid]
    return 0.5 * (xs[mid - 1] + xs[mid])


def score_date(zone: Zone, day: date, state: StateDay, prior: CropPrior) -> dict | None:
    if state.kind != "optical" or state.tau < 0.08 or state.tau > 0.92:
        return None
    ndre = []
    cire = []
    for track in zone.pixels:
        for row in track.records:
            if row.date != day or row.sensor not in ("s2", "landsat"):
                continue
            if row.ndre is not None:
                ndre.append(row.ndre)
            if row.cire is not None:
                cire.append(row.cire)
    if len(ndre) < 4 and len(cire) < 4:
        return None
    # Expected red-edge rises with cover. Bare soil early in the season is clamped.
    expected_ndre = 0.08 + 0.38 * max(state.cover, 0.0)
    expected_ndre = max(expected_ndre, 0.12)
    actual = _median(ndre) if ndre else None
    if actual is None and cire:
        # CIre is a ratio-like chlorophyll index; map a typical 0–1.5 range onto NDRE.
        actual = max(0.0, min(0.6, _median(cire) * 0.25))
    if actual is None:
        return None
    if state.tau < 0.15:
        actual = max(actual, expected_ndre * 0.75)
    ratio = actual / expected_ndre if expected_ndre else 1.0
    score = max(0.0, min(100.0, 100.0 * min(ratio, 1.25) / 1.25))
    if score < 50:
        band = "low"
    elif score < 75:
        band = "medium"
    else:
        band = "high"
    # Penalty consumed by yield retention. A medium score does not cut yield.
    penalty = max(0.0, (55.0 - score) / 220.0) if score < 55 else 0.0
    return {
        "date": day.isoformat(),
        "score": round(score, 1),
        "band": band,
        "ndre": round(actual, 3),
        "expected_ndre": round(expected_ndre, 3),
        "penalty": round(penalty, 3),
        "note": "Red-edge canopy status for this progress. It is not a fertiliser rate.",
    }
