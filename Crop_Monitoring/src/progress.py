"""Phenology progress from the fused cover curve. Duration is a check."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from src.library import CropPrior, stage_name
from src.sowing import SowingEstimate
from src.state import StateDay


@dataclass
class Progress:
    emergence: date | None
    peak: date | None
    senescence: date | None
    harvest: date | None
    harvest_early: date | None
    harvest_late: date | None
    tau: float
    stage: str
    duration_days: int | None
    duration_outlier: bool
    days_remaining: int | None
    picks: list[date]
    note: str


def _smooth(values: list[float], width: int = 5) -> list[float]:
    out = []
    for i in range(len(values)):
        lo = max(0, i - width // 2)
        hi = min(len(values), i + width // 2 + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / len(chunk))
    return out


def _tau_on(day: date, emergence: date | None, peak: date | None, harvest: date | None, tau_peak: float) -> float:
    if emergence is None or day < emergence:
        return 0.0
    if harvest is not None and day >= harvest:
        return 1.0
    if peak is None or day <= peak:
        span = max((peak - emergence).days, 1) if peak else 1
        return tau_peak * min(1.0, (day - emergence).days / span)
    if harvest is None:
        # Past the peak but still green: move toward maturity without declaring harvest.
        span = max((day - peak).days, 1)
        return min(0.97, tau_peak + (1.0 - tau_peak) * min(1.0, span / 45.0))
    span = max((harvest - peak).days, 1)
    frac = min(1.0, (day - peak).days / span)
    return tau_peak + (1.0 - tau_peak) * frac


def measure(rows: list[StateDay], sowing: SowingEstimate, prior: CropPrior) -> Progress:
    if not rows:
        return Progress(None, None, None, None, None, None, 0.0, "pre-emergence",
                        None, False, None, [], "No state to read.")

    cover = _smooth([r.cover for r in rows])
    start_i = 0
    if sowing.date is not None:
        for i, row in enumerate(rows):
            if row.date >= sowing.date:
                start_i = i
                break

    emergence_i = None
    level = 0.22 if not prior.perennial else 0.35
    for i in range(start_i, len(cover) - 1):
        if cover[i] >= level and cover[i + 1] >= level:
            emergence_i = i
            break
    if emergence_i is None:
        for i in range(start_i, len(cover)):
            if cover[i] >= level:
                emergence_i = i
                break

    peak_i = None
    senescence_i = None
    harvest_i = None
    if emergence_i is not None:
        peak_i = max(range(emergence_i, len(cover)), key=lambda i: cover[i])
        peak_val = cover[peak_i]
        for i in range(peak_i + 1, len(cover)):
            if cover[i] <= peak_val * 0.85 and (rows[i].date - rows[peak_i].date).days >= 10:
                senescence_i = i
                break
        residue = prior.residue_cover
        search_from = senescence_i if senescence_i is not None else peak_i + 1
        for i in range(search_from, len(cover)):
            if cover[i] <= residue or (peak_val > 0.3 and cover[i] <= peak_val * 0.45):
                harvest_i = i
                break

    # Cotton: successive drops after boll set, recorded as picks. The last one
    # is the harvest crash when the cover actually falls to residue.
    picks: list[date] = []
    if prior.name == "Cotton" and peak_i is not None:
        local = cover[peak_i]
        for i in range(peak_i + 1, len(cover)):
            if local <= 0:
                break
            if cover[i] <= local * 0.88 and (rows[i].date - rows[peak_i].date).days >= 12:
                picks.append(rows[i].date)
                local = cover[i]
                if len(picks) == 3:
                    break

    emergence = rows[emergence_i].date if emergence_i is not None else None
    peak = rows[peak_i].date if peak_i is not None else None
    senescence = rows[senescence_i].date if senescence_i is not None else None
    harvest = rows[harvest_i].date if harvest_i is not None else None
    if prior.perennial and harvest is not None and cover[harvest_i] > 0.45:
        # A perennial dip is not an annual harvest.
        harvest = None

    as_of = rows[-1].date
    tau = _tau_on(as_of, emergence, peak, harvest, prior.tau_peak)
    for row in rows:
        row.tau = _tau_on(row.date, emergence, peak, harvest, prior.tau_peak)
        row.stage = stage_name(prior, row.tau)

    duration = None
    outlier = False
    if emergence is not None and senescence is not None and not prior.perennial:
        duration = (senescence - emergence).days
        if duration < prior.min_days * 0.55 or duration > prior.max_days * 1.25:
            outlier = True
    elif emergence is not None and harvest is not None and not prior.perennial:
        duration = (harvest - emergence).days
        if duration < prior.min_days * 0.55 or duration > prior.max_days * 1.25:
            outlier = True

    days_remaining = None
    harvest_early = harvest
    harvest_late = harvest
    if harvest is None and emergence is not None and tau < 0.98:
        elapsed = max((as_of - emergence).days, 1)
        rate = max(tau / elapsed, 1.0 / prior.max_days)
        days_remaining = int(round((1.0 - tau) / rate))
        days_remaining = max(7, min(days_remaining, prior.max_days))
        harvest_early = as_of + timedelta(days=int(days_remaining * 0.75))
        harvest_late = as_of + timedelta(days=int(days_remaining * 1.25))
        # Keep the window inside what the crop can still do.
        latest = emergence + timedelta(days=int(prior.max_days * 1.15))
        earliest = emergence + timedelta(days=max(prior.min_days // 2, elapsed))
        if harvest_early < earliest:
            harvest_early = earliest
        if harvest_late > latest:
            harvest_late = latest
        if harvest_late < harvest_early:
            harvest_late = harvest_early

    if harvest is not None:
        note = "Harvest is the observed canopy crash. Yield freezes after this date."
    elif emergence is None:
        note = "Emergence is not on the fused cover curve yet."
    else:
        note = "Harvest is still a window. Progress follows the observed peak, not a fixed duration."
    if outlier:
        note += " Observed length is outside the crop range, so the yield band is wider."

    return Progress(
        emergence, peak, senescence, harvest, harvest_early, harvest_late,
        tau, stage_name(prior, tau), duration, outlier, days_remaining, picks, note,
    )
