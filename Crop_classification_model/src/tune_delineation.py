"""
Fast offline tuner for the watershed delineator.

Scores label rasters directly against rasterised ground-truth fields (pixel
IoU via bincount), skipping polygonisation and clean-up — ~1000x faster than
eval_delineation's polygon scorer, so a few-hundred-point grid runs in
minutes. Uses the boundary arrays eval_delineation cached in data/delin_cache.
The chosen setting must then be confirmed with the polygon scorer.

    python -m src.tune_delineation --sites 60
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys

import numpy as np
import pandas as pd

from ._bootstrap import REPORTS, setup_logging
from .eval_delineation import CACHE, YEAR, _load_gt, _sites

log = setup_logging("tune_delineation")


def _gt_raster(site, ba, up: int):
    import rasterio.features
    from affine import Affine
    from crop_analysis.field_delineation import SCALE_M, _to_utm

    px = SCALE_M / up
    H, W = ba.bands.shape[0] * up, ba.bands.shape[1] * up
    tf = Affine(px, 0, ba.x0, 0, -px, ba.y1)
    shapes = [(_to_utm(g, ba.epsg), i + 1) for i, g in enumerate(site.geometry)]
    return rasterio.features.rasterize(shapes, out_shape=(H, W), transform=tf, fill=0,
                                       dtype="int32")


def score_labels(labels: np.ndarray, gt: np.ndarray):
    n_gt = int(gt.max())
    lab = labels.astype(np.int64)
    L = lab.max() + 1
    joint = np.bincount(gt.ravel().astype(np.int64) * L + lab.ravel(),
                        minlength=(n_gt + 1) * L).reshape(n_gt + 1, L)
    lab_sz = joint.sum(0)
    out = []
    for g in range(1, n_gt + 1):
        row = joint[g]
        gsz = row.sum()
        if gsz == 0:
            continue
        iou = row / (gsz + lab_sz - row)
        out.append(float(iou.max()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=int, default=30)
    ap.add_argument("--min-field-ha", type=float, default=0.03)
    a = ap.parse_args()

    from crop_analysis.field_delineation import (
        BoundaryArrays, SCALE_M, combined_edge, watershed_segments,
    )
    from scipy import ndimage as ndi

    sites = _sites(_load_gt(), a.sites, "test")
    data = []
    for s in sites:
        fp = CACHE / f"site_{int(s['site'].iloc[0])}.npz"
        if fp.exists():
            z = np.load(fp)
            data.append((BoundaryArrays(z["bands"], float(z["x0"]), float(z["y1"]),
                                        int(z["epsg"]), YEAR), s))
    log.info("cached sites: %d", len(data))
    gts = {(i, up): _gt_raster(s, ba, up) for i, (ba, s) in enumerate(data) for up in (1, 2, 3)}

    grid = {
        "w": [("s2", {"s2": 1, "emb": 0, "s1": 0}),
              ("s2+emb", {"s2": 0.5, "emb": 0.5, "s1": 0}),
              ("all", {"s2": 0.5, "emb": 0.35, "s1": 0.15})],
        "upsample": [1, 2],
        "minima_size": [3],
        "marker_quantile": [0.6, 0.9],
        "merge_ratio": [0.2, 0.4, 0.6],
        "smooth_sigma": [0.0],
        "min_scale": [0.5],
    }
    keys = list(grid)
    rows = []
    edges = {}
    for combo in itertools.product(*grid.values()):
        kw = dict(zip(keys, combo))
        wn, w = kw.pop("w")
        up = kw.pop("upsample")
        ms = kw.pop("min_scale")
        px = SCALE_M / up
        min_px = max(3, int(round(ms * a.min_field_ha * 10_000 / px ** 2)))
        ious = []
        for i, (ba, s) in enumerate(data):
            k = (i, wn, up)
            if k not in edges:
                e = combined_edge(ba, w)
                edges[k] = ndi.zoom(e, up, order=1) if up > 1 else e
            lab = watershed_segments(edges[k], min_pixels=min_px, **kw)
            ious += score_labels(lab, gts[(i, up)])
        v = np.array(ious)
        rows.append({"weights": wn, "upsample": up, "min_scale": ms, **kw,
                     "median_iou": round(float(np.median(v)), 4),
                     "mean_iou": round(float(v.mean()), 4),
                     "iou50": round(float((v >= 0.5).mean()), 4)})
    df = pd.DataFrame(rows).sort_values("mean_iou", ascending=False)
    log.info("\n%s", df.head(15).to_string(index=False))
    for col in ("weights", "upsample", "marker_quantile", "merge_ratio", "smooth_sigma",
                "minima_size", "min_scale"):
        log.info("best by %s:\n%s", col, df.groupby(col)["mean_iou"].max().to_string())
    out = REPORTS / "delineation_tuning.json"
    out.write_text(json.dumps(df.head(40).to_dict("records"), indent=1))
    log.info("wrote %s (%d settings)", out, len(df))
    return 0


if __name__ == "__main__":
    sys.exit(main())
