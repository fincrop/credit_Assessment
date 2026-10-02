"""Stress inside one zone, from that zone's own pixels."""

from __future__ import annotations

from datetime import date
from typing import Optional

from src.library import CropPrior
from src.models import WeatherDay
from src.progress import Progress
from src.state import StateDay
from src.zones import Zone


def _percentile(values: list[float], p: float) -> float:
    xs = sorted(values)
    if not xs:
        raise ValueError("empty")
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _pixels_on(zone: Zone, day: date) -> list[dict]:
    found = []
    for track in zone.pixels:
        for row in track.records:
            if row.date == day and row.sensor in ("s2", "landsat") and row.ndvi is not None:
                found.append({
                    "ndvi": row.ndvi,
                    "ndre": row.ndre,
                    "ndmi": row.ndmi,
                    "bsi": row.bsi,
                    "psri": row.psri,
                    "mndwi": row.mndwi,
                })
                break
    return found


def _bin(deviation: float) -> str:
    if deviation < 0.20:
        return "healthy"
    if deviation < 0.40:
        return "mild"
    if deviation < 0.60:
        return "moderate"
    return "severe"


def score_date(
    zone: Zone,
    day: date,
    progress: Progress,
    prior: CropPrior,
    weather: Optional[WeatherDay],
    state: Optional[StateDay],
) -> dict | None:
    """Return a stress record, or None when the day has no clear pixels."""
    pixels = _pixels_on(zone, day)
    if len(pixels) < 4:
        return None
    if state is not None and state.uncertainty > 0.72 and state.kind != "optical":
        return None

    ndvi = [p["ndvi"] for p in pixels]
    healthy_ref = _percentile(ndvi, 75)
    classes = []
    stressed_px = []
    healthy_px = []
    for p in pixels:
        if healthy_ref <= 0.05:
            dev = 0.0
        else:
            dev = max(0.0, (healthy_ref - p["ndvi"]) / healthy_ref)
        label = _bin(dev)
        classes.append(label)
        if label == "healthy":
            healthy_px.append(p)
        else:
            stressed_px.append(p)

    n = len(classes)
    share = {
        "mild": classes.count("mild") / n,
        "moderate": classes.count("moderate") / n,
        "severe": classes.count("severe") / n,
    }
    stressed = share["mild"] + share["moderate"] + share["severe"]
    tau = state.tau if state is not None else progress.tau

    if healthy_ref < 0.25 and tau < 0.18:
        stress_type = "Healthy"
        stressed = min(stressed, 0.0)
        share = {"mild": 0.0, "moderate": 0.0, "severe": 0.0}
    elif stressed < 0.01:
        stress_type = "Healthy"
    else:
        stress_type = _type(healthy_px, stressed_px, prior, tau, weather)

    if prior.name == "Rice" and tau < 0.55 and stress_type == "Crop Water Deficit":
        stress_type = "Sub-optimal Growth" if stressed >= 0.01 else "Healthy"

    if tau >= 0.85:
        # Senescence is expected. Keep the type, damp the share that drives yield.
        share = {k: v * 0.5 for k, v in share.items()}
        stressed *= 0.5

    heat = False
    if weather is not None and prior.tau_peak * 0.7 <= tau <= 0.9:
        if weather.tmax >= prior.heat_tmax and weather.vpd >= 1.6:
            heat = True

    return {
        "date": day.isoformat(),
        "type": stress_type,
        "stressed_fraction": round(stressed, 4),
        "mild": round(share["mild"], 4),
        "moderate": round(share["moderate"], 4),
        "severe": round(share["severe"], 4),
        "weather_stress": "heat" if heat else None,
        "pixel_count": n,
        "healthy_ndvi": round(healthy_ref, 3),
    }


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _type(
    healthy: list[dict],
    stressed: list[dict],
    prior: CropPrior,
    tau: float,
    weather: Optional[WeatherDay],
) -> str:
    if not stressed:
        return "Healthy"
    h_ndre, s_ndre = _mean(healthy, "ndre"), _mean(stressed, "ndre")
    h_ndmi, s_ndmi = _mean(healthy, "ndmi"), _mean(stressed, "ndmi")
    h_bsi, s_bsi = _mean(healthy, "bsi"), _mean(stressed, "bsi")
    h_psri, s_psri = _mean(healthy, "psri"), _mean(stressed, "psri")

    ndre_drop = (h_ndre - s_ndre) if h_ndre is not None and s_ndre is not None else 0.0
    ndmi_drop = (h_ndmi - s_ndmi) if h_ndmi is not None and s_ndmi is not None else 0.0
    bsi_rise = (s_bsi - h_bsi) if h_bsi is not None and s_bsi is not None else 0.0
    psri_rise = (s_psri - h_psri) if h_psri is not None and s_psri is not None else 0.0

    irrigated = bool(weather and weather.irrigation)
    water_ok = ndmi_drop >= 0.08 and not irrigated and not (prior.name == "Rice" and tau < 0.55)
    if tau < 0.82 and (bsi_rise >= 0.08 or psri_rise >= 0.06):
        return "Tissue Damage"
    if water_ok and ndmi_drop > ndre_drop + 0.02:
        return "Crop Water Deficit"
    if ndre_drop >= 0.05 and ndre_drop >= ndmi_drop:
        return "Nutrient Deficit"
    if tau < 0.25 and (_mean(stressed, "ndvi") or 1) < prior.emergence_ndvi + 0.05:
        return "Sub-optimal Growth"
    return "Sub-optimal Growth"
