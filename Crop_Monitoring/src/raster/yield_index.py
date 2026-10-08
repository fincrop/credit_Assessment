"""District-anchored relative yield (plan §5.6, Stage 1).

No field yield records exist yet, so nothing here pretends to be calibrated:

    yield_index = field reproductive-window canopy integral
                  / median of same-crop fields in the village
    yield_t_ha  = official district average yield x yield_index   (only when a
                  district figure is supplied; otherwise the index alone)

The P10-P90 band combines the year-to-year spread of the district average with
the index uncertainty. Every output says "Estimate anchored to district
average; not field-calibrated". The old 0.45 t/ha lint constant, the peers
ranking within one run, and the median-of-three rule are gone.

Stage 2 (once >= 30 client records per crop): `calibrate` fits
yield = a + b * integral + c * stress severity, and the label changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np

# Days after sowing over which canopy most decides yield.
REPRODUCTIVE_WINDOW = {
    "Cotton": (60, 150),       # squaring, flowering, boll development
    "Soyabean": (40, 90),      # flowering to pod fill
    "Tur": (90, 170),
    "Maize": (45, 95),
    "Jowar": (50, 100),
    "default": (40, 110),
}
MIN_PEERS = 8
INDEX_CV_FLOOR = 0.10
DEFAULT_DISTRICT_CV = 0.25    # used only when the district series has < 3 years
STRESS_PENALTY_PER_SEVERITY = 0.02
MAX_STRESS_PENALTY = 0.35
LABEL_STAGE1 = "Estimate anchored to district average; not field-calibrated."


def window_for(crop: str) -> tuple[int, int]:
    return REPRODUCTIVE_WINDOW.get(crop, REPRODUCTIVE_WINDOW["default"])


def canopy_integral(days: list[date], ndvi: np.ndarray, sowing: date, crop: str,
                    as_of: date, reference_value=None) -> tuple[float, float]:
    """(integral over the window, fraction of the window already observed).

    The part of the window still in the future is filled from the crop
    reference (cohort median) if given, so an in-season figure is a forecast
    whose observed share is reported with it.
    """
    lo, hi = window_for(crop)
    total, seen = 0.0, 0
    for das in range(lo, hi + 1):
        d = sowing + timedelta(days=das)
        k = (d - days[0]).days
        if d <= as_of and 0 <= k < len(ndvi) and np.isfinite(ndvi[k]):
            total += float(ndvi[k])
            seen += 1
        elif reference_value is not None:
            v = reference_value(das)
            if np.isfinite(v):
                total += float(v)
    return total, seen / float(hi - lo + 1)


@dataclass
class DistrictYield:
    crop: str
    mean_t_ha: float
    cv: float
    years: list[int]
    source: str


def district_yield(rows: list[dict], crop: str) -> Optional[DistrictYield]:
    """From official rows {crop, year, yield_t_ha, source}; last 5 years."""
    mine = sorted((r for r in rows if r.get("crop") == crop and r.get("yield_t_ha")),
                  key=lambda r: r["year"])[-5:]
    if not mine:
        return None
    vals = np.array([float(r["yield_t_ha"]) for r in mine])
    cv = float(vals.std() / vals.mean()) if len(vals) >= 3 and vals.mean() > 0 else DEFAULT_DISTRICT_CV
    return DistrictYield(crop, float(vals.mean()), cv, [int(r["year"]) for r in mine],
                         "; ".join(sorted({str(r.get("source", "")) for r in mine})))


def estimate(integral: float, observed_share: float, peer_integrals: list[float],
             severity: float, district: Optional[DistrictYield]) -> dict:
    peers = [p for p in peer_integrals if np.isfinite(p) and p > 0]
    if len(peers) < MIN_PEERS or not np.isfinite(integral):
        return {"basis": "insufficient_peers", "yield_index": None, "yield_t_ha": None,
                "note": f"Fewer than {MIN_PEERS} same-crop fields with a canopy integral; no index."}
    med = float(np.median(peers))
    raw_index = integral / med if med > 0 else np.nan
    penalty = min(MAX_STRESS_PENALTY, STRESS_PENALTY_PER_SEVERITY * max(severity, 0.0))
    index = raw_index * (1.0 - penalty)
    peer_cv = float(np.median(np.abs(np.array(peers) - med)) * 1.4826 / med) if med > 0 else 0.3
    # Unobserved window share widens the band: it is forecast from the cohort.
    index_cv = max(INDEX_CV_FLOOR, peer_cv * 0.5) * (1.0 + (1.0 - observed_share))
    out = {
        "basis": "district_anchored" if district else "index_only",
        "yield_index": round(float(index), 3),
        "index_p10": round(float(index * (1 - 1.2816 * index_cv)), 3),
        "index_p90": round(float(index * (1 + 1.2816 * index_cv)), 3),
        "observed_window_share": round(float(observed_share), 2),
        "stress_penalty": round(float(penalty), 3),
        "peers": len(peers),
        "label": LABEL_STAGE1,
    }
    if district:
        cv = float(np.hypot(district.cv, index_cv))
        point = district.mean_t_ha * index
        out.update({
            "yield_t_ha": round(point, 3),
            "yield_p10": round(max(0.0, point * (1 - 1.2816 * cv)), 3),
            "yield_p90": round(point * (1 + 1.2816 * cv), 3),
            "baseline_t_ha": round(district.mean_t_ha, 3),
            "baseline_source": district.source,
            "baseline_years": district.years,
        })
    else:
        out.update({"yield_t_ha": None, "note": "No official district yield supplied; index only."})
    return out


def calibrate(records: list[dict], min_records: int = 30) -> Optional[dict]:
    """Stage 2: ordinary least squares on client records
    {integral, severity, yield_t_ha}. None until min_records exist."""
    rows = [r for r in records if all(r.get(k) is not None for k in ("integral", "severity", "yield_t_ha"))]
    if len(rows) < min_records:
        return None
    X = np.column_stack([np.ones(len(rows)), [r["integral"] for r in rows], [r["severity"] for r in rows]])
    y = np.array([r["yield_t_ha"] for r in rows], float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    return {"coef": coef.tolist(), "residual_sd": float(resid.std(ddof=3)), "n": len(rows)}
