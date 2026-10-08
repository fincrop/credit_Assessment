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
from crop_analysis import field_delineation as fd  # noqa: E402

GT_URL = ("https://s3.us-west-2.amazonaws.com/us-west-2.opendata.source.coop/"
          "ftw/india-10k-ml/india_10k_ml.parquet")
GT_PATH = DATA / "india_10k_ml.parquet"
CACHE = DATA / "delin_cache"
YEAR = 2024
SEED = 7


MH_PARCELS = DATA / "00_parcels_mh_new.parquet"
MH_SPLIT = DATA / "splits" / "mh2023_split.json"


def _load_mh() -> gpd.GeoDataFrame:
    """Marathwada cotton/soybean 2023 parcels (accuracy plan C1.1).

    Labels are partial: only cotton and soybean parcels are drawn, so the
    per-GT-field scores (best IoU, fragmentation, area ratio) are valid, but
    over-segmentation of unlabelled neighbours is not measured. Parcels in the
    frozen mh2023 test blocks form split "test"; the rest are "train" (for
    --sweep tuning only).
    """
    g = gpd.read_parquet(MH_PARCELS)
    held = set(json.loads(MH_SPLIT.read_text())["excluded_from_training"]) if MH_SPLIT.exists() else set()
    g["split"] = np.where(g["geom_hash"].isin(held), "test", "train")
    g["metrics:area"] = g.to_crs(32643).geometry.area
    return g


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


def _cached(key: str, fetch):
    from crop_analysis.field_delineation import BoundaryArrays
    fp = CACHE / f"{key}.npz"
    if fp.exists():
        z = np.load(fp)
        return BoundaryArrays(z["bands"], float(z["x0"]), float(z["y1"]), int(z["epsg"]), YEAR)
    ba = fetch()
    np.savez_compressed(fp, bands=ba.bands, x0=ba.x0, y1=ba.y1, epsg=ba.epsg)
    return ba


def _cached_prof(key: str, fetch):
    fp = CACHE / f"{key}.npz"
    if fp.exists():
        return np.load(fp)["profiles"]
    arr = fetch()
    np.savez_compressed(fp, profiles=arr)
    return arr


def _ftw_polys(key: str, aoi):
    """FTW Global polygons for a site, cached as WKB (the row groups are also
    cached by the backend reader)."""
    import pickle

    import shapely

    fp = CACHE / f"{key}_ftw.pkl"
    if fp.exists():
        raw = pickle.loads(fp.read_bytes())
        return [(shapely.from_wkb(w), p) for w, p in raw]
    polys, _ = fd.ftw_polygons(aoi, year=YEAR)
    fp.write_bytes(pickle.dumps([(shapely.to_wkb(g), p) for g, p in polys]))
    return polys


def _ftw_model_edge(key: str, aoi, ba):
    """FTW U-Net boundary probability on the site's own-year S2 (cached)."""
    from crop_analysis import ftw_model as fm

    fp = CACHE / f"{key}_ftwmodel.npz"
    if fp.exists():
        return np.load(fp)["p"]
    p = fm.boundary_prob(fm.fetch_input(ba, aoi, YEAR))
    np.savez_compressed(fp, p=p)
    return p


FTW_VARIANTS = {                      # method -> (edge weight, merge)
    "watershed_ftwedge": (fd.FTW_EDGE_WEIGHT, False),
    "watershed_ftwmerge": (0.0, True),
    "watershed_ftw": (fd.FTW_EDGE_WEIGHT, True),
}


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
    ap.add_argument("--sweep-ftw", action="store_true",
                    help="grid-search FTW edge weight and same-field merge")
    ap.add_argument("--sweep-profile", action="store_true",
                    help="grid-search profile-merge thresholds (needs watershed_profile)")
    ap.add_argument("--min-field-ha", type=float, default=0.03)
    ap.add_argument("--tag", default="", help="suffix for the report file")
    ap.add_argument("--gt", default="india10k", choices=["india10k", "mh2023"],
                    help="ground truth: India 10k (2024) or Marathwada cotton/soybean (2023)")
    ap.add_argument("--year", type=int, default=None, help="imagery year (default: by --gt)")
    a = ap.parse_args()
    global YEAR
    YEAR = a.year or (2023 if a.gt == "mh2023" else YEAR)

    CACHE.mkdir(exist_ok=True)
    init_ee()
    from crop_analysis import field_delineation as fd

    methods = [m.strip() for m in a.methods.split(",") if m.strip()]
    if "alu" not in methods and fd.os.getenv(fd.ALU_KEY_ENV):
        methods.append("alu")
    gt = _load_mh() if a.gt == "mh2023" else _load_gt()
    sites = _sites(gt, a.sites, a.split)
    log.info("sites: %d  (%d GT fields, median %.3f ha)", len(sites),
             sum(len(s) for s in sites),
             float(pd.concat(sites)["metrics:area"].median()) / 1e4)

    rows: Dict[str, List[Dict[str, float]]] = {m: [] for m in methods}
    arrays = {}
    prof_sets = {}
    ftw_sets = {}
    model_sets = {}
    timing: Dict[str, float] = {m: 0.0 for m in methods}
    for i, site in enumerate(sites, 1):
        key = (f"{a.gt}_" if a.gt != "india10k" else "") + f"site_{int(site['site'].iloc[0])}"
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
                elif m in ("watershed_season", "watershed_profile", "watershed_profile12"):
                    months = fd.kharif_months(YEAR)
                    # Kharif-month profiles sit on the same AOI grid as either edge map.
                    if m == "watershed_profile12":
                        ba_e = _ws_arrays(key, aoi)
                    else:
                        ba_e = _cached(f"{key}_k", lambda: fd.fetch_boundary_arrays(aoi, year=YEAR, months=months))
                    prof = None
                    if m != "watershed_season":
                        prof = _cached_prof(f"{key}_prof", lambda: fd.fetch_profile_arrays(
                            aoi, ba_e, year=YEAR, months=months))
                        prof_sets[(m, key)] = (ba_e, prof, aoi, site)
                    fields = fd.segment_arrays(ba_e, aoi, min_field_ha=a.min_field_ha,
                                               min_cropland=0.0, profiles=prof).fields
                elif m in ("watershed_ftwmodel", "watershed_ftwall"):
                    from scipy import ndimage as ndi
                    ba = _ws_arrays(key, aoi)
                    me = ndi.zoom(_ftw_model_edge(key, aoi, ba), 2, order=1)
                    grid = fd.ftw_on_grid(_ftw_polys(key, aoi), ba, 2) if m == "watershed_ftwall" else None
                    model_sets[key] = (ba, me, grid, aoi, site)
                    fields = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                               ftw=grid, model_edge=me).fields
                elif m in FTW_VARIANTS:
                    ba = _ws_arrays(key, aoi)
                    grid = fd.ftw_on_grid(_ftw_polys(key, aoi), ba, 2)
                    ftw_sets[key] = (ba, grid, aoi, site)
                    w, merge = FTW_VARIANTS[m]
                    fields = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                               ftw=grid, ftw_edge_weight=w, ftw_merge=merge).fields
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

    report = {"year": YEAR, "sites": len(sites), "split": a.split, "ground_truth": a.gt,
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

    if a.sweep_profile and prof_sets:
        log.info("sweeping profile-merge thresholds over %d cached sites", len(prof_sets))
        sweep = []
        variants = sorted({k[0] for k in prof_sets})
        for variant in variants:
            sets = [v for k, v in prof_sets.items() if k[0] == variant]
            for d_merge in (0.3, 0.5, 0.7, 1.0, 1.5):
                for w_max in (0.15, 0.25, 0.35, 0.5):
                    rr = []
                    for ba_e, prof, aoi, site in sets:
                        f = fd.segment_arrays(ba_e, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                              profiles=prof, profile_d_merge=d_merge,
                                              profile_w_max=w_max).fields
                        rr += score(site, f)
                    sweep.append({"variant": variant, "d_merge": d_merge, "w_max": w_max, **_summ(rr)})
        sw = pd.DataFrame(sweep).sort_values("median_iou", ascending=False)
        log.info("%s", sw.to_string(index=False))
        report["profile_sweep"] = sw.to_dict("records")

    if a.sweep_ftw and ftw_sets:
        log.info("sweeping FTW evidence over %d cached sites", len(ftw_sets))
        sweep = []
        for w in (0.0, 0.2, 0.35, 0.5, 0.7):
            for merge, cover, w_max in ((False, 0, 0), (True, 0.6, 0.6), (True, 0.6, 0.8),
                                        (True, 0.75, 0.8), (True, 0.6, 1.0)):
                rr = []
                for ba, grid, aoi, site in ftw_sets.values():
                    f = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                          ftw=grid, ftw_edge_weight=w, ftw_merge=merge,
                                          ftw_min_cover=cover or fd.FTW_MIN_COVER,
                                          ftw_w_max=w_max or fd.FTW_W_MAX).fields
                    rr += score(site, f)
                sweep.append({"edge_weight": w, "merge": merge, "min_cover": cover,
                              "w_max": w_max, **_summ(rr)})
                log.info("  ftw w=%.2f merge=%s cover=%.2f w_max=%.1f  median_iou=%.3f  area_ratio=%s",
                         w, merge, cover, w_max, sweep[-1].get("median_iou", 0),
                         sweep[-1].get("median_area_ratio"))
        report["ftw_sweep"] = sorted(sweep, key=lambda r: -r.get("median_iou", 0))

    if model_sets:
        log.info("sweeping FTW-model edge weight over %d cached sites", len(model_sets))
        sweep = []
        for mw in (0.1, 0.2, 0.35, 0.5):
            for gw in (0.0, 0.35, 0.5):
                rr = []
                for ba, me, grid, aoi, site in model_sets.values():
                    if gw and grid is None:
                        continue
                    f = fd.segment_arrays(ba, aoi, min_field_ha=a.min_field_ha, min_cropland=0.0,
                                          ftw=grid if gw else None, ftw_edge_weight=gw,
                                          model_edge=me, model_edge_weight=mw).fields
                    rr += score(site, f)
                if rr:
                    sweep.append({"model_weight": mw, "global_weight": gw, **_summ(rr)})
                    log.info("  model w=%.2f global w=%.2f  median_iou=%.3f", mw, gw,
                             sweep[-1].get("median_iou", 0))
        report["ftw_model_sweep"] = sorted(sweep, key=lambda r: -r.get("median_iou", 0))

    out = REPORTS / f"delineation_benchmark{('_' + a.tag) if a.tag else ''}.json"
    out.write_text(json.dumps(report, indent=1))
    log.info("\n%s", pd.DataFrame(report["methods"]).T.to_string())
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
