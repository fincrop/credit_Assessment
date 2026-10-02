"""Yield ensemble on observed progress. A fixed duration does not set the number."""

from __future__ import annotations

from statistics import median

from src.library import CropPrior, default_yield
from src.progress import Progress
from src.state import StateDay


def _rank(value: float, peers: list[float]) -> float:
    if not peers:
        return 0.5
    below = sum(1 for p in peers if p <= value)
    return below / len(peers)


def retention(
    rows: list[StateDay],
    prior: CropPrior,
    uneven: float,
    nitrogen_penalty: float,
    heat_fraction: float = 0.0,
) -> tuple[float, dict]:
    critical = [r for r in rows if prior.tau_peak * 0.75 <= r.tau <= 0.9]
    pool = critical or rows
    if not pool:
        return 1.0, {}
    dry = sum(1 for r in pool if r.water < 0.35) / len(pool)
    pen = (
        0.40 * dry
        + 0.25 * max(0.0, min(heat_fraction, 1.0))
        + 0.20 * max(0.0, min(uneven, 1.0))
        + nitrogen_penalty
    )
    value = max(0.35, min(1.0, 1.0 - pen))
    return value, {
        "water_fraction": round(dry, 3),
        "uneven": round(uneven, 3),
        "heat_fraction": round(heat_fraction, 3),
        "nitrogen_penalty": round(nitrogen_penalty, 3),
        "retention": round(value, 3),
    }


def pixel_uneven(ndvi_values: list[float]) -> float:
    if len(ndvi_values) < 4:
        return 0.0
    mean = sum(ndvi_values) / len(ndvi_values)
    if mean <= 0.05:
        return 0.0
    var = sum((v - mean) ** 2 for v in ndvi_values) / len(ndvi_values)
    cv = (var ** 0.5) / mean
    return max(0.0, min(1.0, (cv - 0.08) / 0.40))


def estimate(
    prior: CropPrior,
    progress: Progress,
    rows: list[StateDay],
    peer_integrals: list[float] | None,
    district_yield: float | None,
    nitrogen_penalty: float,
    heat_fraction: float,
    uncertainty: float,
    sowing_span_days: int,
    uneven: float,
) -> dict | None:
    if progress.emergence is None or progress.tau <= 0.05:
        return None

    base = district_yield if district_yield and district_yield > 0 else default_yield(prior.name)
    pool = "district_statistic" if district_yield else "crop_shape"
    peers = list(peer_integrals or [])
    if len(peers) >= 15:
        pool = "district_peers"

    keep, parts = retention(rows, prior, uneven, nitrogen_penalty, heat_fraction)
    current = rows[-1]
    # Freeze once harvest is observed: do not keep adding bare-soil days.
    if progress.harvest is not None:
        frozen = [r for r in rows if r.date <= progress.harvest]
        current = frozen[-1] if frozen else current

    integral = sum(r.cover for r in rows if progress.emergence and r.date >= progress.emergence and r.date <= current.date)

    days_left = 0 if progress.harvest else int(progress.days_remaining or 0)
    recent = [r.gain for r in rows[-7:]] or [0.0]
    recent_gain = sum(recent) / len(recent)
    biomass_final = current.biomass + recent_gain * days_left * 0.85
    y_bio = (biomass_final * prior.hi) / 1000.0 * keep

    peak_cover = max((r.cover for r in rows), default=0.0)
    ref = max(prior.cover_max * 0.85, 0.2)
    y_peak = base * (peak_cover / ref) * keep

    estimators = {
        "biomass_hi": y_bio,
        "peak_canopy": y_peak,
    }
    if pool == "district_peers":
        estimators["peers"] = base * (0.55 + 0.90 * _rank(integral, peers)) * keep

    vals = [v for v in estimators.values() if v == v]
    point = median(vals)
    spread = (max(vals) - min(vals)) if len(vals) > 1 else point * 0.22
    extra = spread / 2.0
    extra += point * 0.10 * (max(sowing_span_days, 0) / 14.0)
    extra += point * 0.22 * max(0.0, min(uncertainty, 1.0))
    if progress.duration_outlier:
        extra += point * 0.30
    if pool == "crop_shape":
        extra += point * 0.12
    # Before the peak the season can still move. Keep the band honest.
    if progress.peak is None or (progress.peak and current.date <= progress.peak):
        extra += point * 0.10
    low = max(0.0, point - extra)
    high = point + extra
    before_peak = progress.peak is None or current.date <= progress.peak
    return {
        "t_ha": round(point, 3),
        "low": round(low, 3),
        "high": round(high, 3),
        "retention": parts.get("retention", round(keep, 3)),
        "retention_parts": parts,
        "estimators": {k: round(v, 3) for k, v in estimators.items()},
        "reference_pool": pool,
        "baseline_t_ha": round(base, 3),
        "cover_integral": round(integral, 3),
        "keep": round(keep, 3),
        "band_inputs": {
            "uncertainty": uncertainty,
            "span": sowing_span_days,
            "outlier": bool(progress.duration_outlier),
            "before_peak": before_peak,
        },
        "frozen": progress.harvest is not None,
        "note": (
            "Median of the biomass, peak-canopy, and peer estimators on observed progress. "
            "The band widens when they disagree, when sowing is uncertain, or when optical views are old."
        ),
    }


def with_village_peers(yield_doc: dict | None, peers: list[float]) -> dict | None:
    """Re-rank yield against the other farms in this run. Needs 15 integrals."""
    if not yield_doc or len(peers) < 15:
        return yield_doc
    integral = yield_doc.get("cover_integral")
    keep = yield_doc.get("keep")
    base = yield_doc.get("baseline_t_ha")
    if integral is None or keep is None or not base:
        return yield_doc
    info = yield_doc.get("band_inputs") or {}
    estimators = {k: float(v) for k, v in (yield_doc.get("estimators") or {}).items()}
    estimators["peers"] = float(base) * (0.55 + 0.90 * _rank(float(integral), peers)) * float(keep)
    vals = [v for v in estimators.values() if v == v]
    point = median(vals)
    spread = (max(vals) - min(vals)) if len(vals) > 1 else point * 0.22
    extra = spread / 2.0
    extra += point * 0.10 * (max(int(info.get("span") or 0), 0) / 14.0)
    extra += point * 0.22 * max(0.0, min(float(info.get("uncertainty") or 0), 1.0))
    if info.get("outlier"):
        extra += point * 0.30
    if info.get("before_peak"):
        extra += point * 0.10
    updated = dict(yield_doc)
    updated.update({
        "t_ha": round(point, 3),
        "low": round(max(0.0, point - extra), 3),
        "high": round(point + extra, 3),
        "estimators": {k: round(v, 3) for k, v in estimators.items()},
        "reference_pool": "village_peers",
        "note": "Median of biomass, peak canopy, and the other farms scored in this run.",
    })
    updated.pop("band_inputs", None)
    return updated


def strip_yield_internals(document: dict) -> None:
    for zone in document.get("zones") or []:
        yielded = zone.get("yield")
        if isinstance(yielded, dict):
            yielded.pop("band_inputs", None)
