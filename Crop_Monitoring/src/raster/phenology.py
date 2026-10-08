"""Season milestones from smoothed NDVI, with a bare-soil precondition.

The Dhaswadi audit traced the April sowing dates to one rule: a pixel already
green in the first image was taken as "emerged" on that date (554 of 1,090
zones greened up on the first composite). Here a green-up only counts after
the pixel was SEEN bare: a real clear observation below BARE_NDVI before the
rise. Monitoring starts 1 March so the dry pre-season state is always in view.

Milestones follow the amplitude-fraction convention (TIMESAT, Jonsson & Eklundh
2004): start of season where the curve first reaches 20% of its seasonal
amplitude above the pre-season base, end of season where it falls back to 50%
after the peak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import date, timedelta

import numpy as np

BARE_NDVI = 0.22          # a clear look below this is bare soil / stubble
MIN_AMPLITUDE = 0.20      # smaller seasonal rise is not a crop cycle
SOS_FRACTION = 0.20
EOS_FRACTION = 0.50
HARVEST_FRACTION = 0.20   # back near the base: canopy removed
PEAK_CONFIRM_DROP = 0.05  # curve must fall this much after the peak to call it final
PEAK_CONFIRM_DAYS = 10
BARE_LOOKBACK = 45        # days before the base a bare look still counts

STATUS_OK = 0
STATUS_GREEN_AT_START = 1   # never seen bare before the rise: not a datable green-up
STATUS_NO_CYCLE = 2         # no seasonal rise of crop size
STATUS_NO_DATA = 3          # too few clear looks


@dataclass
class Phenology:
    """Per-pixel (or per-field) milestones as day indices into the daily axis; -1 = none."""
    days: list[date]
    status: np.ndarray        # (N,) int8
    base_i: np.ndarray
    sos_i: np.ndarray
    peak_i: np.ndarray
    eos_i: np.ndarray
    harvest_i: np.ndarray
    base: np.ndarray          # NDVI at base
    peak: np.ndarray          # NDVI at peak
    peak_final: np.ndarray    # bool: the curve has turned down after the peak
    bare_seen: np.ndarray     # bool
    # Days between the clear looks either side of the start of season. In a
    # monsoon cloud gap the smoothed curve is a straight line across the gap and
    # the crossing date is only known to within this many days.
    sos_gap: np.ndarray = None

    def date_of(self, idx: np.ndarray) -> list[date | None]:
        return [self.days[i] if i >= 0 else None for i in np.asarray(idx).tolist()]

    @property
    def amplitude(self) -> np.ndarray:
        return self.peak - self.base


def detect(
    days: list[date],
    z: np.ndarray,
    raw_dates: list[date],
    raw_ndvi: np.ndarray,
    raw_weight: np.ndarray,
    peak_search_start: date,
    min_obs: int = 6,
    radar_dates: Optional[list[date]] = None,
    radar_bare: Optional[np.ndarray] = None,
) -> Phenology:
    """Milestones for each column of the daily smoothed curve z (D, N).

    raw_* are the real observations (T, N) behind z, used for the bare-soil
    check: the smoothed curve can dip below BARE_NDVI by interpolation alone.
    """
    D, N = z.shape
    di = np.arange(D)
    start_k = max(0, (peak_search_start - days[0]).days)
    nan_cols = ~np.isfinite(z).all(axis=0)
    zz = np.where(np.isfinite(z), z, -np.inf)

    n_obs = (raw_weight > 0).sum(axis=0)
    in_window = di[:, None] >= start_k
    peak_i = np.where(in_window, zz, -np.inf).argmax(axis=0)
    peak = zz[peak_i, np.arange(N)]

    # Base: the lowest point before the peak (the bare pre-season state).
    before = di[:, None] <= peak_i[None, :]
    zb = np.where(before, np.where(np.isfinite(z), z, np.inf), np.inf)
    base_i = zb.argmin(axis=0)
    base = zb[base_i, np.arange(N)]
    amp = peak - base

    sos_level = base + SOS_FRACTION * amp
    after_base = (di[:, None] > base_i[None, :]) & (di[:, None] <= peak_i[None, :])
    cross = after_base & (zz >= sos_level[None, :])
    sos_i = np.where(cross.any(axis=0), cross.argmax(axis=0), -1)

    # Bare seen: a real clear observation below BARE_NDVI in THIS season's
    # pre-sowing state -- from BARE_LOOKBACK days before the base up to the
    # green-up. A bare look in March before a summer crop does not date a
    # kharif green-up.
    raw_k = np.array([(d - days[0]).days for d in raw_dates]) if raw_dates else np.zeros(0, int)
    bare_seen = np.zeros(N, bool)
    if len(raw_k):
        is_bare = (raw_weight > 0) & np.isfinite(raw_ndvi) & (raw_ndvi < BARE_NDVI)
        upto = np.where(sos_i >= 0, sos_i, peak_i)
        window = (raw_k[:, None] >= (base_i - BARE_LOOKBACK)[None, :]) & (raw_k[:, None] <= upto[None, :])
        bare_seen = (is_bare & window).any(axis=0)
    # Radar sees bare soil through cloud: a bare Sentinel-1 look (absolute
    # signature, sowing.load_sar_reference) in the same pre-sowing window counts.
    if radar_dates and radar_bare is not None and len(radar_dates):
        rk = np.array([(d - days[0]).days for d in radar_dates])
        upto = np.where(sos_i >= 0, sos_i, peak_i)
        rwin = (rk[:, None] >= (base_i - BARE_LOOKBACK)[None, :]) & (rk[:, None] <= upto[None, :])
        bare_seen = bare_seen | (radar_bare & rwin).any(axis=0)

    after_peak = di[:, None] > peak_i[None, :]
    eos_cross = after_peak & (zz <= (base + EOS_FRACTION * amp)[None, :])
    eos_i = np.where(eos_cross.any(axis=0), eos_cross.argmax(axis=0), -1)
    hv_cross = after_peak & (zz <= (base + HARVEST_FRACTION * amp)[None, :])
    harvest_i = np.where(hv_cross.any(axis=0), hv_cross.argmax(axis=0), -1)

    tail_drop = peak - zz[-1]
    peak_final = (peak_i <= D - 1 - PEAK_CONFIRM_DAYS) & (tail_drop >= PEAK_CONFIRM_DROP)

    status = np.full(N, STATUS_OK, np.int8)
    status[amp < MIN_AMPLITUDE] = STATUS_NO_CYCLE
    status[(status == STATUS_OK) & ~bare_seen] = STATUS_GREEN_AT_START
    status[(n_obs < min_obs) | nan_cols] = STATUS_NO_DATA
    bad = status != STATUS_OK
    for arr in (sos_i, eos_i, harvest_i):
        arr[bad] = -1

    sos_gap = np.full(N, -1, np.int32)
    if len(raw_k):
        seen = (raw_weight > 0) & np.isfinite(raw_ndvi)
        kk = raw_k[:, None].astype(float)
        prev = np.where(seen & (kk <= sos_i[None, :]), kk, -np.inf).max(axis=0)
        nxt = np.where(seen & (kk >= sos_i[None, :]), kk, np.inf).min(axis=0)
        gap = nxt - prev
        sos_gap = np.where((sos_i >= 0) & np.isfinite(gap), gap, -1).astype(np.int32)

    return Phenology(days, status, base_i, sos_i, peak_i, eos_i, harvest_i,
                     base.astype(np.float32), peak.astype(np.float32), peak_final, bare_seen,
                     sos_gap)


def gdd(tmean_by_day: dict[date, float], start: date, end: date, base_temp: float,
        ceiling: float = 40.0) -> float:
    """Growing degree days from start to end, capped at a ceiling."""
    total = 0.0
    d = start
    while d <= end:
        t = tmean_by_day.get(d)
        if t is not None:
            total += max(0.0, min(t, ceiling) - base_temp)
        d += timedelta(days=1)
    return total
