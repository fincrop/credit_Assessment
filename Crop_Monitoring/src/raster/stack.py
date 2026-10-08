"""Per-scene pixel stacks. One array per band, NaN where the pixel is not clear.

Dates are real acquisition dates. Nothing here composites: an 11-day median
put every field on one shared date grid and hid which day a value came from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from src.raster.grid import Grid

# Weight a Landsat look carries relative to a Sentinel-2 look in smoothing:
# a 30 m pixel covers most of a 0.27 ha field plus its neighbours, and the
# cross-calibration (S2 ~ 1.1-1.5 x Landsat on Dhaswadi) leaves a residual.
LANDSAT_WEIGHT = 0.4


@dataclass
class SceneStack:
    sensor: str                          # s2 | landsat | s1
    grid: Grid
    dates: list[date]
    bands: dict[str, np.ndarray]         # name -> (T, H, W) float32, NaN = not clear
    meta: list[dict] = field(default_factory=list)   # per scene: orbit, clear_fraction, ...

    @property
    def n(self) -> int:
        return len(self.dates)

    def band(self, name: str) -> np.ndarray:
        return self.bands[name]

    def clear(self) -> np.ndarray:
        """(T, H, W) bool: pixel had a usable observation."""
        first = next(iter(self.bands.values()))
        return np.isfinite(first)

    def subset(self, keep: list[int]) -> "SceneStack":
        return SceneStack(
            self.sensor, self.grid, [self.dates[i] for i in keep],
            {k: v[keep] for k, v in self.bands.items()},
            [self.meta[i] for i in keep] if self.meta else [],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path, **{f"band_{k}": v.astype(np.float32) for k, v in self.bands.items()},
            dates=np.array([d.isoformat() for d in self.dates]),
            meta=np.array(json.dumps(self.meta)),
            grid=np.array(json.dumps(self.grid.__dict__)),
            sensor=np.array(self.sensor),
        )

    @staticmethod
    def load(path: Path) -> "SceneStack":
        z = np.load(path, allow_pickle=False)
        grid = Grid(**json.loads(str(z["grid"])))
        bands = {k[5:]: z[k] for k in z.files if k.startswith("band_")}
        dates = [date.fromisoformat(str(d)) for d in z["dates"]]
        return SceneStack(str(z["sensor"]), grid, dates, bands, json.loads(str(z["meta"])))


def merge_by_date(stacks: list[SceneStack]) -> SceneStack:
    """Stack scenes of one sensor from several tiles or requests into one, by date."""
    if not stacks:
        raise ValueError("nothing to merge")
    dates, meta, bands = [], [], {k: [] for k in stacks[0].bands}
    for s in stacks:
        dates.extend(s.dates)
        meta.extend(s.meta or [{} for _ in s.dates])
        for k in bands:
            bands[k].append(s.bands[k])
    idx = sorted(range(len(dates)), key=lambda i: dates[i])
    return SceneStack(
        stacks[0].sensor, stacks[0].grid, [dates[i] for i in idx],
        {k: np.concatenate(v)[idx] for k, v in bands.items()}, [meta[i] for i in idx],
    )


@dataclass
class OpticalSeries:
    """NDVI-family observations from every optical sensor, for one set of pixels.

    `values` (T, N) holds one row per real acquisition; a Sentinel-2 and a
    Landsat look on the same day are two rows, never one overwritten key.
    """
    dates: list[date]
    sensors: list[str]
    values: dict[str, np.ndarray]    # index -> (T, N)
    weights: np.ndarray              # (T, N), 0 where not clear

    @property
    def n_obs(self) -> np.ndarray:
        return (self.weights > 0).sum(axis=0)
