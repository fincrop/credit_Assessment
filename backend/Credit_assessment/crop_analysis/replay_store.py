"""Disk copies of Earth Engine downloads, so a later model or method reads files.

The monitoring scene grids live in their own grid folder (Crop_Monitoring
cache). Delineation rasters and classification series live here, under the
same cache root, because they are keyed by the outline and the field
geometries rather than by the field bounding box.

  delineation/<outline>/boundary_<recipe>_<year>_<months>.npz
  delineation/<outline>/profiles_<recipe>_<year>_<months>_<grid>.npz
  series/<geometry hash>.json

A file is written only after a successful download, via a temporary name, so a
killed run cannot leave a half file that the next run would trust.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def root() -> Path:
    env = os.getenv("VILLAGE_REPLAY_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "Crop_Monitoring" / "cache"


def store(cache_root: Path | None) -> Path:
    return Path(cache_root) if cache_root is not None else root()


def outline_key(geojson: Any) -> str:
    blob = json.dumps(geojson, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def series_key(geometry: Any, lo: str, hi: str, recipe: str) -> str:
    blob = json.dumps(
        {"geometry": geometry, "lo": lo, "hi": hi, "recipe": recipe},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def atomic_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)


def atomic_npz(path: Path, **arrays: Any) -> None:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".partial.npz")
    np.savez_compressed(tmp, **arrays)
    os.replace(tmp, path)
