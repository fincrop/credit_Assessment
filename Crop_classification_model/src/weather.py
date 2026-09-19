"""
Weather layer for the crop classifier -- as a *normaliser*, not a feature dump.

The tempting move is to append rainfall and temperature to the feature vector.
That would make the cross-region problem worse, not better. Adversarial
validation already recovers ecoregion from the shipped features at 0.5323
against 0.1667 chance, and absolute climate is close to a region ID: a model
handed mean seasonal rainfall learns "this is the Deccan" and reads the crop off
the region prior. It would gain on blocked CV and lose on the LOEO number that
actually governs village mapping.

So weather enters two ways, both of which *remove* geography rather than add it:

1. **Thermal time.** Cycle duration in days is climate-dependent -- the same
   wheat variety runs ~120 d in Punjab and rather faster in a warmer season.
   Accumulated growing degree days is the agronomically invariant version of
   "how long did this crop take", and `duration_days` / `log_duration` are both
   top-ten features by gain today. This is the single most promising weather
   quantity for the maize/sugarcane and wheat/mustard duration confusions.

2. **Anomalies, never absolutes.** Rainfall enters only as a departure from the
   grid cell's own multi-year climatology for the same calendar window. "Wetter
   than this place normally is" transfers between regions; "800 mm" does not.

MEASURED -- and rule 2 is not fully honoured by rule 1. Adding this block:

    metric                      baseline   + weather + shape
    LOEO balanced accuracy      0.1444     0.2026  (0.2165 with the season mask)
    blocked balanced accuracy   0.7492     0.8081
    ecoregion adversarial       0.7890     0.8993   <-- WORSE

The intent was that nothing here encodes location. `gdd_total` does. A Deccan
cycle banks heat faster than a Gangetic one, so an absolute degree-day sum is
partly a latitude reading, and the adversarial score rose 11 points. The net is
still strongly positive -- cross-region accuracy is up 50% and in-region up 5.9
points -- so thermal time's crop signal clearly outweighs the geography it
smuggles in, and the block stays. But it is a trade, not the free lunch the
paragraph above claimed.

That fix was proposed, tested, and REFUTED. Dropping `gdd_total` /
`log_gdd_total` in favour of `gdd_anomaly_ratio`:

    metric                      + weather + shape   ...minus absolute GDD
    LOEO balanced accuracy      0.2026              0.1999
    blocked balanced accuracy   0.8081              0.8045
    ecoregion adversarial       0.8993              0.8995   <-- unmoved

The adversarial score did not budge by 0.0003, so the absolute heat sum is not
what carries the location. Reading the remaining columns properly, the leak is
almost certainly `gdd_per_day` -- total degree days over duration is *mean daily
temperature*, which is a latitude reading with extra steps -- joined by
`dry_spell_max_days` and `rain_days_fraction`, which describe a climatic regime
rather than a crop. The two columns removed were the ones carrying real
agronomy: a crop's heat budget to maturity is a property of the plant, and rice
needing 1.8x wheat's accumulated warmth is exactly the signal wanted.

So the absolute sums stay, and the untested candidate for removal is
`gdd_per_day` + the two rainfall-regime columns. Not yet measured.

The wider lesson for this block: "is it an anomaly or an absolute?" turned out
to be the wrong question. `gdd_per_day` is a ratio and still encodes place;
`gdd_total` is an absolute and encodes the crop. The question that separates
them is whether the quantity is a property of the *plant* or of the *location*,
and only the adversarial score answers it -- intuition about the formula did
not, twice.

Fetching is on a 0.25 deg grid (253 cells for the whole training set), one
request per cell for the entire 2021-2025 span, cached to Parquet. Per-cycle
features are then derived locally, so re-running costs nothing.

    python -m src.weather --fetch          # one-time, ~253 POWER requests
    python -m src.weather --build          # -> data/04_weather_cycle.parquet
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from . import _bootstrap  # noqa: F401

log = logging.getLogger("weather")

DATA = Path(__file__).resolve().parent.parent / "data"
DAILY_PATH = DATA / "04_weather_daily.parquet"
CYCLE_PATH = DATA / "04_weather_cycle.parquet"

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAMS = ["T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M"]
COMMUNITY = "AG"

GRID_DEG = 0.25
# Base temperature for degree-day accumulation. Deliberately a single generic
# value rather than a per-crop base: the crop is the label, and a crop-specific
# base would leak it straight into the feature.
GDD_BASE_C = 10.0
GDD_CAP_C = 35.0          # upper cutoff -- heat above this does not add growth
PAD_DAYS = 15             # matches CYCLE_PADDING_DAYS at feature time


def _cell(lat: float, lon: float) -> Tuple[float, float]:
    return (round(round(lat / GRID_DEG) * GRID_DEG, 2),
            round(round(lon / GRID_DEG) * GRID_DEG, 2))


# =============================================================================
# fetch
# =============================================================================
def fetch_daily(cycles: pd.DataFrame, sleep: float = 0.4) -> pd.DataFrame:
    """One POWER request per 0.25 deg cell, covering the full training span.

    Requesting the whole span at once rather than per cycle turns ~7,900
    requests into ~253 and makes the climatology in `_anomaly` free: the same
    frame already holds every year the cell has.
    """
    import requests

    cells = sorted({_cell(r.lat, r.lon) for r in cycles.itertuples()})
    start = (pd.to_datetime(cycles.sowing_date).min()
             - pd.Timedelta(days=PAD_DAYS + 400)).strftime("%Y%m%d")
    end = (pd.to_datetime(cycles.harvest_date).max()
           + pd.Timedelta(days=PAD_DAYS)).strftime("%Y%m%d")

    existing: Dict[Tuple[float, float], pd.DataFrame] = {}
    if DAILY_PATH.exists():
        prev = pd.read_parquet(DAILY_PATH)
        for (la, lo), g in prev.groupby(["cell_lat", "cell_lon"]):
            existing[(la, lo)] = g
        log.info("resuming: %d cells already cached", len(existing))

    out = list(existing.values())
    todo = [c for c in cells if c not in existing]
    log.info("fetching %d of %d cells  span %s..%s", len(todo), len(cells), start, end)

    for i, (la, lo) in enumerate(todo, 1):
        params = {
            "parameters": ",".join(POWER_PARAMS), "community": COMMUNITY,
            "latitude": la, "longitude": lo, "start": start, "end": end,
            "format": "JSON",
        }
        df = pd.DataFrame()
        for attempt in range(3):
            try:
                r = requests.get(POWER_URL, params=params, timeout=120)
                r.raise_for_status()
                props = r.json().get("properties", {}).get("parameter", {})
                if not props:
                    raise ValueError("empty parameter block")
                df = pd.DataFrame(props)
                df.index = pd.to_datetime(df.index, format="%Y%m%d")
                df = df.replace(-999.0, np.nan)
                break
            except Exception as e:                      # noqa: BLE001
                if attempt == 2:
                    log.warning("cell %.2f,%.2f failed: %s", la, lo, str(e)[:100])
                else:
                    time.sleep(2 ** attempt)
        if df.empty:
            continue
        df = df.reset_index(names="date")
        df["cell_lat"], df["cell_lon"] = la, lo
        out.append(df)

        if i % 25 == 0 or i == len(todo):
            pd.concat(out, ignore_index=True).to_parquet(DAILY_PATH, index=False)
            log.info("  %d/%d cells", i, len(todo))
        time.sleep(sleep)

    if not out:
        raise SystemExit("no weather fetched")
    daily = pd.concat(out, ignore_index=True)
    daily.to_parquet(DAILY_PATH, index=False)
    log.info("wrote %s  (%d rows, %d cells)", DAILY_PATH, len(daily),
             daily.groupby(["cell_lat", "cell_lon"]).ngroups)
    return daily


# =============================================================================
# per-cycle features
# =============================================================================
def _gdd(tmax: np.ndarray, tmin: np.ndarray) -> np.ndarray:
    """Capped-mean growing degree days. Both tails are clipped before the mean,
    which is the standard agronomic form and stops a single hot day from
    inflating the accumulation."""
    hi = np.clip(tmax, GDD_BASE_C, GDD_CAP_C)
    lo = np.clip(tmin, GDD_BASE_C, GDD_CAP_C)
    return np.maximum((hi + lo) / 2.0 - GDD_BASE_C, 0.0)


def build_cycle_features(cycles: pd.DataFrame,
                         daily: pd.DataFrame) -> pd.DataFrame:
    """Derive the per-cycle weather block.

    Every emitted column is either a thermal-time quantity or an anomaly. No
    absolute rainfall or temperature total reaches the feature vector -- see the
    module docstring for why that restraint is the whole point.
    """
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["gdd"] = _gdd(daily["T2M_MAX"].to_numpy(), daily["T2M_MIN"].to_numpy())
    daily["doy"] = daily["date"].dt.dayofyear

    # Per-cell day-of-year climatology, from every year the cell holds. This is
    # what turns "600 mm fell" into "40% wetter than normal here".
    clim = (daily.groupby(["cell_lat", "cell_lon", "doy"])
                 .agg(rain_norm=("PRECTOTCORR", "mean"),
                      gdd_norm=("gdd", "mean"),
                      t2m_norm=("T2M", "mean"))
                 .reset_index())
    daily = daily.merge(clim, on=["cell_lat", "cell_lon", "doy"], how="left")

    by_cell = {k: g.sort_values("date") for k, g in
               daily.groupby(["cell_lat", "cell_lon"])}

    rows = []
    for c in cycles.itertuples():
        g = by_cell.get(_cell(c.lat, c.lon))
        if g is None:
            rows.append({"geom_hash": c.geom_hash, "cycle_index": c.cycle_index})
            continue
        s = pd.Timestamp(c.sowing_date)
        h = pd.Timestamp(c.harvest_date)
        w = g[(g["date"] >= s) & (g["date"] <= h)]
        if len(w) < 10:
            rows.append({"geom_hash": c.geom_hash, "cycle_index": c.cycle_index})
            continue

        gdd = w["gdd"].to_numpy()
        cum = np.cumsum(gdd)
        total = float(cum[-1])
        rain = w["PRECTOTCORR"].to_numpy()
        rain_n = np.nansum(w["rain_norm"].to_numpy())

        # Thermal-time position of the NDVI peak: how far through the crop's
        # *heat* budget it peaked, not how far through the calendar.
        peak_frac = np.nan
        if isinstance(c.peak_date, str) or pd.notna(c.peak_date):
            pk = pd.Timestamp(c.peak_date)
            if s <= pk <= h:
                upto = w["date"] <= pk
                peak_frac = float(gdd[upto.to_numpy()].sum() / total) if total > 0 else np.nan

        dry = (rain < 1.0).astype(int)
        # longest run of dry days
        best = cur = 0
        for d in dry:
            cur = cur + 1 if d else 0
            best = max(best, cur)

        rows.append({
            "geom_hash": c.geom_hash,
            "cycle_index": c.cycle_index,
            # --- thermal time: the invariant replacement for duration_days ---
            "gdd_total": round(total, 1),
            "log_gdd_total": round(float(np.log1p(total)), 4),
            "gdd_per_day": round(total / max(len(w), 1), 3),
            "gdd_peak_fraction": round(peak_frac, 4) if peak_frac == peak_frac else np.nan,
            # --- anomalies only ---
            "rain_anomaly_ratio": round(float(np.nansum(rain) / rain_n), 4) if rain_n > 5 else np.nan,
            "gdd_anomaly_ratio": round(float(total / max(np.nansum(w["gdd_norm"]), 1e-6)), 4),
            "t2m_anomaly_c": round(float(np.nanmean(w["T2M"] - w["t2m_norm"])), 3),
            "dry_spell_max_days": int(best),
            "rain_days_fraction": round(float((rain >= 1.0).mean()), 4),
            "rh_mean": round(float(np.nanmean(w["RH2M"])), 2),
            "weather_days": int(len(w)),
        })

    out = pd.DataFrame(rows)
    log.info("built weather features for %d/%d cycles (%.1f%% complete)",
             out["gdd_total"].notna().sum() if "gdd_total" in out else 0,
             len(cycles),
             100.0 * (out["gdd_total"].notna().mean() if "gdd_total" in out else 0))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="download POWER daily grid")
    ap.add_argument("--build", action="store_true", help="derive per-cycle features")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--cycles", default="02_cycles.parquet",
                    help="cycle table to derive features for")
    ap.add_argument("--out", default=None,
                    help="per-cycle weather parquet to write")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s",
                        datefmt="%H:%M:%S")

    global CYCLE_PATH
    if a.out:
        CYCLE_PATH = DATA / a.out
    cycles = pd.read_parquet(DATA / a.cycles)

    if a.fetch:
        fetch_daily(cycles, sleep=a.sleep)
    if a.build:
        if not DAILY_PATH.exists():
            raise SystemExit(f"{DAILY_PATH} missing -- run --fetch first")
        feats = build_cycle_features(cycles, pd.read_parquet(DAILY_PATH))
        feats.to_parquet(CYCLE_PATH, index=False)
        log.info("wrote %s  (%d rows, %d cols)", CYCLE_PATH, len(feats),
                 len(feats.columns))
    if not (a.fetch or a.build):
        ap.error("pass --fetch and/or --build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
