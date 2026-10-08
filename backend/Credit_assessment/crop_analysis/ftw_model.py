"""
Fields of The World boundary model on this season's Sentinel-2.

FTW Global (field_delineation.ftw_polygons) is a 2024/2025 snapshot. Field
lines move: plots are split between heirs, merged on lease, re-bunded. This
runs the FTW baseline U-Net (CC-BY checkpoint `3_Class_CCBY_FTW_Pretrained`,
EfficientNet-B3 encoder, 3 classes: background / field / field boundary) on
our own two-date Sentinel-2 composite for the requested year, and returns the
boundary probability on the delineation grid. It is blended into the
watershed's edge map; it never produces fields on its own.

Input as the FTW trainers use it: L2A digital numbers / 3000, bands
B4, B3, B2, B8 of window A then window B. Window A is the rabi season
(15 Jan - 15 Mar, mostly cloud-free in Maharashtra); window B the late
kharif (1 Sep - 31 Oct, clipped to the as-of date). Two seasons on one
plot show a boundary wherever the two halves were managed differently.

Checkpoint path: env FTW_MODEL_PATH, else ~/.cache/ftw_models/. When torch,
segmentation_models_pytorch or the checkpoint is missing, `boundary_prob`
returns None and delineation runs on satellite edges alone.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

CKPT_NAME = "3_Class_CCBY_FTW_Pretrained.ckpt"
BOUNDARY_CLASS = 2
NORM = 3000.0
TILE = 512
OVERLAP = 64


def checkpoint_path() -> Path:
    env = os.getenv("FTW_MODEL_PATH")
    return Path(env) if env else Path.home() / ".cache" / "ftw_models" / CKPT_NAME


@lru_cache(maxsize=1)
def _model():
    import segmentation_models_pytorch as smp
    import torch

    ck = torch.load(str(checkpoint_path()), map_location="cpu", weights_only=False)
    hp = ck.get("hyper_parameters") or {}
    m = smp.Unet(encoder_name=hp.get("backbone", "efficientnet-b3"), encoder_weights=None,
                 in_channels=int(hp.get("in_channels", 8)), classes=int(hp.get("num_classes", 3)))
    sd = {k[len("model."):]: v for k, v in ck["state_dict"].items() if k.startswith("model.")}
    m.load_state_dict(sd, strict=True)
    m.eval()
    return m


def available() -> bool:
    if not checkpoint_path().exists():
        return False
    try:
        import segmentation_models_pytorch  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def windows(year: int, as_of: Optional[date] = None):
    """((a0, a1), (b0, b1)) ISO date pairs. Before 15 Sep of `year` the late-
    kharif window is not yet observed, so last year's is used."""
    as_of = as_of or date(year, 12, 31)
    a = (date(year, 1, 15), date(year, 3, 15))
    if as_of >= date(year, 9, 15):
        b = (date(year, 9, 1), min(date(year, 10, 31), as_of))
    else:
        b = (date(year - 1, 9, 1), date(year - 1, 10, 31))
    return a, b


def _composite(ee, region, d0: date, d1: date):
    from data_acquisition.extra_sources import CS_BAND, CS_COLLECTION, CS_THRESHOLD, S2_COLLECTION

    coll = (ee.ImageCollection(S2_COLLECTION).filterBounds(region)
            .filterDate(d0.isoformat(), d1.isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
            .linkCollection(ee.ImageCollection(CS_COLLECTION), [CS_BAND]))
    bands = ["B4", "B3", "B2", "B8"]
    return coll.map(lambda i: i.updateMask(i.select(CS_BAND).gte(CS_THRESHOLD)).select(bands)) \
               .median().select(bands).unmask(0).toFloat()


def fetch_input(ba, aoi_geojson, year: int, as_of: Optional[date] = None, ee_module=None) -> np.ndarray:
    """(8, H, W) float32 model input on the BoundaryArrays 10 m grid."""
    from crop_analysis.field_delineation import SCALE_M, _download_grid

    ee = ee_module
    if ee is None:
        import ee  # noqa: PLC0415
    region = ee.Geometry(aoi_geojson, None, False).buffer(300)
    (a0, a1), (b0, b1) = windows(year, as_of)
    img = _composite(ee, region, a0, a1).addBands(_composite(ee, region, b0, b1))
    H, W = ba.bands.shape[:2]
    bounds = (ba.x0, ba.y1 - H * SCALE_M, ba.x0 + W * SCALE_M, ba.y1)
    arr = _download_grid(ee, img, bounds, ba.epsg, n_bands=8)
    return np.moveaxis(arr, -1, 0)


def boundary_prob(x: np.ndarray) -> Optional[np.ndarray]:
    """Boundary-class softmax (H, W) for an (8, H, W) input, tiled with overlap."""
    if not available():
        return None
    import torch

    m = _model()
    x = np.nan_to_num(x.astype(np.float32) / NORM, nan=0.0)
    C, H, W = x.shape
    ph, pw = (-H) % 32, (-W) % 32
    xp = np.pad(x, ((0, 0), (0, ph), (0, pw)), mode="reflect" if min(H, W) > 32 else "constant")
    HH, WW = xp.shape[1:]
    acc = np.zeros((HH, WW), np.float32)
    wts = np.zeros((HH, WW), np.float32)
    step = TILE - OVERLAP
    with torch.no_grad():
        for r in range(0, max(HH - OVERLAP, 1), step):
            for c in range(0, max(WW - OVERLAP, 1), step):
                r1, c1 = min(r + TILE, HH), min(c + TILE, WW)
                r0, c0 = max(r1 - TILE, 0), max(c1 - TILE, 0)
                t = torch.from_numpy(xp[:, r0:r1, c0:c1][None])
                p = torch.softmax(m(t), dim=1)[0, BOUNDARY_CLASS].numpy()
                acc[r0:r1, c0:c1] += p
                wts[r0:r1, c0:c1] += 1
    return (acc / np.maximum(wts, 1))[:H, :W]


def boundary_on_grid(ba, aoi_geojson, year: int, as_of: Optional[date] = None,
                     upsample: int = 2, ee_module=None) -> Optional[np.ndarray]:
    """Boundary probability on the (upsampled) delineation grid, or None."""
    if not available():
        return None
    try:
        p = boundary_prob(fetch_input(ba, aoi_geojson, year, as_of, ee_module))
    except Exception as exc:                               # noqa: BLE001
        logger.warning("FTW model boundary unavailable: %s", str(exc)[:160])
        return None
    if p is None:
        return None
    if upsample and upsample > 1:
        from scipy import ndimage as ndi
        p = ndi.zoom(p, upsample, order=1)
    return p.astype(np.float32)
