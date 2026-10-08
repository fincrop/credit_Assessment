"""Raster products: Cloud-Optimised GeoTIFFs, map overlays, product index (plan §5.8).

Rules that make a map shareable with a bank:
  1. Fixed colour scales across dates and villages (no per-image stretch).
  2. No-data is hatched, never filled with a neighbour's value.
  3. Every product carries its date, sensor and clear-pixel share.
  4. Colour-blind-safe palettes: Okabe-Ito categories, a sequential ramp for
     indices, an orange-purple diverging ramp for anomalies.
  5. Index maps are written only for real clear observation dates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np

from src.raster.grid import Grid

# Sequential (viridis anchors) for NDVI-family indices.
_VIRIDIS = ["#440154", "#482878", "#3e4989", "#31688e", "#26828e",
            "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"]
# Diverging orange (below the reference: stress) -> white -> purple (above).
_PUOR = ["#7f3b08", "#b35806", "#e08214", "#fdb863", "#fee0b6", "#f7f7f7",
         "#d8daeb", "#b2abd2", "#8073ac", "#542788", "#2d004b"]

SCALES = {
    "ndvi": {"vmin": 0.0, "vmax": 0.9, "ramp": _VIRIDIS, "units": "NDVI"},
    "ndre": {"vmin": 0.0, "vmax": 0.6, "ramp": _VIRIDIS, "units": "NDRE"},
    "ndmi": {"vmin": -0.2, "vmax": 0.6, "ramp": _VIRIDIS, "units": "NDMI"},
    "vh": {"vmin": -25.0, "vmax": -10.0, "ramp": _VIRIDIS, "units": "VH backscatter (dB)"},
    "anomaly": {"vmin": -3.0, "vmax": 3.0, "ramp": _PUOR, "units": "z-score vs same-crop cohort"},
    "sowing_doy": {"vmin": 120, "vmax": 230, "ramp": _VIRIDIS, "units": "sowing day of year"},
}

# Okabe-Ito categorical palette.
STRESS_CLASSES = {
    1: ("healthy", "#009E73"),
    2: ("mild", "#F0E442"),
    3: ("moderate", "#E69F00"),
    4: ("severe", "#D55E00"),
    5: ("establishing", "#BBBBBB"),
}
STAGE_CLASSES = {
    1: ("pre-sowing", "#BBBBBB"), 2: ("emergence", "#F0E442"), 3: ("vegetative", "#009E73"),
    4: ("reproductive", "#0072B2"), 5: ("maturity", "#E69F00"), 6: ("harvested", "#CC79A7"),
}
CLASS_INDEX = {v[0]: k for k, v in STRESS_CLASSES.items()}


def _hex(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _ramp_lut(ramp: list[str], n: int = 256) -> np.ndarray:
    anchors = np.array([_hex(h) for h in ramp], float)
    x = np.linspace(0, 1, len(ramp))
    xi = np.linspace(0, 1, n)
    return np.stack([np.interp(xi, x, anchors[:, c]) for c in range(3)], axis=1).astype(np.uint8)


def _hatch(h: int, w: int) -> np.ndarray:
    """RGBA diagonal hatch for no-data pixels."""
    yy, xx = np.mgrid[0:h, 0:w]
    on = ((xx + yy) % 6) < 2
    out = np.zeros((h, w, 4), np.uint8)
    out[on] = (120, 120, 120, 170)
    out[~on] = (220, 220, 220, 60)
    return out


def colorize_continuous(arr: np.ndarray, scale: dict) -> np.ndarray:
    lut = _ramp_lut(scale["ramp"])
    t = (arr - scale["vmin"]) / (scale["vmax"] - scale["vmin"])
    idx = np.clip(np.nan_to_num(t, nan=0) * 255, 0, 255).astype(int)
    rgba = np.zeros((*arr.shape, 4), np.uint8)
    rgba[..., :3] = lut[idx]
    rgba[..., 3] = 230
    nd = ~np.isfinite(arr)
    rgba[nd] = _hatch(*arr.shape)[nd]
    return rgba


def colorize_classes(arr: np.ndarray, classes: dict) -> np.ndarray:
    rgba = np.zeros((*arr.shape, 4), np.uint8)
    hatch = _hatch(*arr.shape)
    nd = ~np.isfinite(arr) | (arr <= 0)
    for k, (_, col) in classes.items():
        m = arr == k
        rgba[m, :3] = _hex(col)
        rgba[m, 3] = 235
    rgba[nd] = hatch[nd]
    return rgba


def write_cog(path: Path, arr: np.ndarray, grid: Grid, nodata=np.nan, tags: Optional[dict] = None) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    data = arr.astype(np.float32 if np.issubdtype(arr.dtype, np.floating) else arr.dtype)
    profile = {
        "driver": "COG", "width": grid.width, "height": grid.height, "count": 1,
        "dtype": str(data.dtype), "crs": grid.crs, "transform": grid.transform,
        "nodata": nodata, "compress": "DEFLATE",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
        if tags:
            dst.update_tags(**{k: (json.dumps(v) if not isinstance(v, str) else v) for k, v in tags.items()})
    return path


def write_overlay(path: Path, rgba: np.ndarray, grid: Grid) -> list[list[float]]:
    """Reproject RGBA to Web Mercator (what Leaflet draws in) and save a PNG.
    Returns [[south, west], [north, east]] bounds for L.imageOverlay."""
    from PIL import Image
    from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds

    xmin, ymin, xmax, ymax = grid.bounds()
    dst_t, w, h = calculate_default_transform(grid.crs, "EPSG:3857", grid.width, grid.height,
                                              xmin, ymin, xmax, ymax)
    out = np.zeros((4, h, w), np.uint8)
    for b in range(4):
        reproject(rgba[..., b], out[b], src_transform=grid.transform, src_crs=grid.crs,
                  dst_transform=dst_t, dst_crs="EPSG:3857", resampling=Resampling.nearest)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.moveaxis(out, 0, -1), "RGBA").save(path, optimize=True)
    left, top = dst_t.c, dst_t.f
    right, bottom = left + dst_t.a * w, top + dst_t.e * h
    w_, s_, e_, n_ = transform_bounds("EPSG:3857", "EPSG:4326", left, bottom, right, top)
    return [[s_, w_], [n_, e_]]


@dataclass
class ProductIndex:
    root: Path
    items: list[dict] = field(default_factory=list)

    def add(self, product: str, arr: np.ndarray, grid: Grid, *, when: Optional[date] = None,
            sensor: Optional[str] = None, clear_fraction: Optional[float] = None,
            scale: Optional[str] = None, classes: Optional[dict] = None, note: str = "") -> dict:
        stem = product + (f"_{when.isoformat()}" if when else "")
        cog = write_cog(self.root / "cog" / f"{stem}.tif", arr, grid, tags={
            "product": product, "date": when.isoformat() if when else "", "sensor": sensor or "",
            "clear_fraction": clear_fraction if clear_fraction is not None else "",
        })
        if classes:
            rgba = colorize_classes(arr, classes)
            legend = [{"value": k, "label": v[0], "color": v[1]} for k, v in classes.items()]
        else:
            sc = SCALES[scale or product]
            rgba = colorize_continuous(arr, sc)
            legend = {"vmin": sc["vmin"], "vmax": sc["vmax"], "ramp": sc["ramp"], "units": sc["units"]}
        bounds = write_overlay(self.root / "png" / f"{stem}.png", rgba, grid)
        item = {
            "product": product, "date": when.isoformat() if when else None, "sensor": sensor,
            "clear_fraction": None if clear_fraction is None else round(float(clear_fraction), 4),
            "cog": f"cog/{stem}.tif", "png": f"png/{stem}.png", "bounds": bounds,
            "legend": legend, "no_data": "hatched", "note": note,
        }
        self.items.append(item)
        return item

    def save(self) -> Path:
        p = self.root / "products.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"products": self.items}, indent=1), encoding="utf-8")
        return p
