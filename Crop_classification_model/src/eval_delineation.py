"""
Field-delineation benchmark on manually drawn Indian fields.

Ground truth: "10,000 Crop Field Boundaries across India" (Wang, Waldner &
Lobell; CC-BY-4.0, via source.coop/ftw/india-10k-ml). Fields come in sites of
~5 adjacent parcels; median field 0.24 ha — genuinely smallholder.

For each sampled site the AOI is the site's bounding box + 150 m. Every method
delineates that AOI and is scored per ground-truth field:

  best_iou     max IoU of the GT field with any predicted polygon
  iou50        share of GT fields matched at IoU >= 0.5
  frag         predicted polygons that cover >= 10% of the GT field
               (1 = clean, >1 = over-segmented)
  area_ratio   area of the best-matching polygon / GT area
               (>>1 = under-segmented, neighbours merged in)

Methods: snic (the previous production segmenter), ftw (FTW Global 2024),
watershed (field_delineation), and alu when AG_UNDERSTANDING_API_KEY is set.

Watershed boundary arrays are cached per site (data/delin_cache/), so
`--sweep` re-tunes the segmenter offline in seconds.

    python -m src.eval_delineation --sites 40
    python -m src.eval_delineation --sites 40 --methods watershed --sweep
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import geopandas as gpd
import numpy as np
import pandas as pd

from ._bootstrap import DATA, REPORTS, init_ee, setup_logging

log = setup_logging("eval_delineation")

GT_URL = ("https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/"
          "ftw/india-10k-ml/india_10k_ml.parquet")
GT_PATH = DATA / "india_10k_ml.parquet"
CACHE = DATA / "delin_cache"
YEAR = 2024
SEED = 7


def _load_gt() -> gpd.GeoDataFrame:
    if not GT_PATH.exists():
        import requests
        log.info("downloading India 10k field labels")
        GT_PATH.write_bytes(requests.get(GT_URL, timeout=300).content)
    return gpd.read_parquet(GT_PATH)


def _sites(gt: gpd.GeoDataFrame, n: int, split: str) -> List[gpd.GeoDataFrame]:
    import scipy.sparse as sp
    import scipy.sparse.csgraph as cg
    from scipy.spatial import cKDTree

    g = gt[gt["split"] == split].copy()
    gu = g.to_crs(32644)
    xy = np.c_[gu.geometry.centroid.x, gu.geometry.centroid.y]
    pairs = np.array(sorted(cKDTree(xy).query_pairs(300)))
    A = sp.coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(g),) * 2)
    _, lab = cg.connected_components(A, directed=False)
    g["site"] = lab
    full = g.groupby("site").filter(lambda d: len(d) >= 4)
    ids = full["site"].unique()
    rng = np.random.default_rng(SEED)
    pick = rng.choice(ids, size=min(n, len(ids)), replace=False)
    return [full[full["site"] == s] for s in pick]


def _aoi(site: gpd.GeoDataFrame) -> Dict[str, Any]:
    c = site.unary_union.centroid
    from crop_analysis.field_delineation import _to_utm, _to_wgs, _utm_epsg
    from shapely.geometry import box, mapping
    epsg = _utm_epsg(c.x, c.y)
    b = _to_utm(site.unary_union, epsg).bounds
    return mapping(_to_wgs(box(*b).buffer(150, join_style=2), epsg))


def score(site: gpd.GeoDataFrame, fields: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    from crop_analysis.field_delineation import _to_utm, _utm_epsg
    from shapely.geometry import shape

    c = site.unary_union.centroid
    epsg = _utm_epsg(c.x, c.y)
    preds = [_to_utm(shape(f["geometry"]), epsg) for f in fields]
    out = []
    for g in site.geometry:
        gu = _to_utm(g, epsg)
        best, best_area, frag = 0.0, np.nan, 0
        for p in preds:
            if not p.intersects(gu):
                continue
            inter = p.intersection(gu).area
            if inter >= 0.10 * gu.area:
                frag += 1
            iou = inter / p.union(gu).area
            if iou > best:
                best, best_area = iou, p.area / gu.area
        out.append({"best_iou": best, "frag": frag, "area_ratio": best_area,
                    "gt_ha": gu.area / 1e4})
    return out


def _summ(rows: List[Dict[str, float]]) -> Dict[str, float]:
    d = pd.DataFrame(rows)
    if d.empty:
        return {}
    return {
        "n_fields": int(len(d)),
        "median_iou": round(float(d.best_iou.median()), 4),
        "mean_iou": round(float(d.best_iou.mean()), 4),
        "iou50": round(float((d.best_iou >= 0.5).mean()), 4),
        "iou30": round(float((d.best_iou >= 0.3).mean()), 4),
        "median_frag": float(d.frag.median()),
        "median_area_ratio": round(float(d.area_ratio.median()), 3),
    }


# ---------------------------------------------------------------- methods ---
def run_snic(aoi: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The previous production path: area_classifier's SNIC segmentation."""
    import ee
    from crop_analysis import area_classifier as ac

    g = ee.Geometry(aoi, None, False)
    d0, d1 = ac.season_window("kharif", YEAR)
    images, _ = ac._composite_collection(ee, g, d0, d1, 70.0)
    seg = ac._segmentation_image(ee, images, g)
    snic = ee.Algorithms.Image.Segmentation.SNIC(
        image=seg, size=ac.SNIC_SEED_SPACING, compactness=ac.SNIC_COMPACTNESS,
        connectivity=ac.SNIC_CONNECTIVITY, neighborhoodSize=ac.SNIC_NEIGHBORHOOD)
    cl = ac._absorb_specks(snic.select("clusters").clip(g), ac.MIN_OBJECT_PIXELS)
    fc = ac._vectorise_clusters(ee, cl, g, 0)
    return [{"type": "Feature", "geometry": f["geometry"], "properties": {}}
            for f in fc.get("features", [])]


def _ws_arrays(key: str, aoi):
    from crop_analysis.field_delineation import BoundaryArrays, fetch_boundary_arrays
    fp = CACHE / f"{key}.npz"
    if fp.exists():
        z = np.load(fp)
        return BoundaryArrays(z["bands"], float(z["x0"]), float(z["y1"]), int(z["epsg"]), YEAR)
    ba = fetch_boundary_arrays(aoi, year=YEAR)
    np.savez_compressed(fp, bands=ba.bands, x0=ba.x0, y1=ba.y1, epsg=ba.epsg)
    return ba


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sites", type=int, default=40)
    ap.add_argument("--split", default="test")
    ap.add_argument("--methods", default="snic,ftw,watershed")
    ap.add_argument("--sweep", action="store_true", help="grid-search watershed params")
    ap.add_argument("--min-field-ha", type=float, default=0.03)
    ap.add_argument("--tag", default="", help="suffix for the report file")
    a = ap.parse_args()

    CACHE.mkdir(exist_ok=True)
    init_ee()
    from crop_analysis import field_delineation as fd

    methods = [m.strip() for m in a.methods.split(",") if m.strip()]
    if "alu" not in methods and fd.os.getenv(fd.ALU_KEY_ENV):
        methods.append("alu")
    gt = _load_gt()
    sites = _sites(gt, a.sites, a.split)
    log.info("sites: %d  (%d GT fields, median %.3f ha)", len(sites),
             sum(len(s) for s in sites),
             float(pd.concat(sites)["metrics:area"].median()) / 1e4)

    rows: Dict[str, List[Dict[str, float]]] = {m: [] for m in methods}
    arrays = {}
    timing: Dict[str, float] = {m: 0.0 for m in methods}
    for i, site in enumerate(sites, 1):
        key = f"site_{int(site['site'].iloc[0])}"
        aoi = _aoi(site)
        for m in methods:
            t = time.time()
            try:
                if m == "snic":
                    fields = run_snic(aoi)
                elif m == "ftw":
                    fields = fd.delineate_ftw(aoi, min_field_ha=a.min_field_ha, year=YEAR).fields
                elif m in ("hybrid_ws", "hybrid_snic"):
                    base = fd.delineate_ftw(aoi, min_field_ha=a.min_field_ha, year=YEAR).fields
                    if m == "hybrid_ws":
                        fill = fd.segment_arrays(_ws_arrays(key, aoi), aoi,
                                                 min_field_ha=a.min_field_ha,
                                                 min_cropland=0.0).fields
                    else:
                        fill = run_snic(aoi)
                    fields = fd.fuse_fields(base, fill, aoi, min_field_ha=a.min_field_ha)
                elif m == "alu":
                    fields = fd.delineate_alu(aoi, min_field_ha=a.min_field_ha).fields
                else:
                    ba = _ws_arrays(key, aoi)
                    arrays[key] = (ba, aoi, site)
                    fields = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha,
                                               min_cropland=0.0).fields
            except Exception as exc:                     # noqa: BLE001
                log.warning("site %s %s failed: %s", key, m, str(exc)[:160])
                fields = []
            timing[m] += time.time() - t
            rows[m] += score(site, fields)
        if i % 5 == 0 or i == len(sites):
            log.info("  %d/%d  %s", i, len(sites),
                     "  ".join(f"{m}={_summ(rows[m]).get('median_iou', 0):.3f}" for m in methods))

    report = {"year": YEAR, "sites": len(sites), "split": a.split,
              "methods": {m: {**_summ(rows[m]), "sec_per_site": round(timing[m] / len(sites), 1)}
                          for m in methods}}

    if a.sweep and arrays:
        log.info("sweeping watershed parameters over %d cached sites", len(arrays))
        grid = {
            "marker_mode": ["minima"],
            "minima_size": [3, 5],
            "marker_quantile": [0.4, 0.6, 0.8],
            "merge_ratio": [0.2, 0.35, 0.5],
            "smooth_sigma": [0.0, 0.8],
            "upsample": [1, 2],
            "w": [("s2", {"s2": 1, "emb": 0, "s1": 0}),
                  ("s2+emb", {"s2": 0.5, "emb": 0.5, "s1": 0}),
                  ("all", {"s2": 0.5, "emb": 0.35, "s1": 0.15})],
        }
        sweep = []
        keys = list(grid)
        for combo in itertools.product(*grid.values()):
            kw = dict(zip(keys, combo))
            wn, w = kw.pop("w")
            ups = kw.pop("upsample")
            rr = []
            for ba, aoi, site in arrays.values():
                f = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                      weights=w, upsample=ups, **kw).fields
                rr += score(site, f)
            sweep.append({**kw, "upsample": ups, "weights": wn, **_summ(rr)})
        sw = pd.DataFrame(sweep).sort_values("mean_iou", ascending=False)
        log.info("\n%s", sw.head(12).to_string(index=False))
        report["sweep_top"] = sw.head(20).to_dict("records")

    out = REPORTS / f"delineation_benchmark{('_' + a.tag) if a.tag else ''}.json"
    out.write_text(json.dumps(report, indent=1))
    log.info("\n%s", pd.DataFrame(report["methods"]).T.to_string())
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
