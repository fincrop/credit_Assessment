"""Stratified area and accuracy estimation (Olofsson et al. 2014).

The implementation is the general stratified estimator of Stehman (2014):
strata do not have to equal map classes (for example a "monitoring-flagged"
stratum that cuts across map classes). When strata *are* the map classes,
it reduces exactly to the Olofsson et al. (2014) formulas:

* error matrix in area proportions  ``p_ij = W_i n_ij / n_i.``
* user's accuracy   ``U_i = p_ii / p_i.``,  ``V(U_i) = U_i (1-U_i) / (n_i. - 1)``
* producer's accuracy ``P_j = p_jj / p_.j`` with the ratio variance (eq. 7)
* overall accuracy  ``O = sum_j p_jj``, ``V(O) = sum_i W_i^2 U_i (1-U_i)/(n_i.-1)``
* area proportion   ``p_.k = sum_i W_i n_ik / n_i.``,
  ``S(p_.k) = sqrt(sum_i W_i^2 (n_ik/n_i.)(1 - n_ik/n_i.)/(n_i. - 1))``
* bias-adjusted area ``A_k = A_tot * p_.k`` with 95% CI ``+- 1.96 A_tot S(p_.k)``

Sampling units are treated as points (each sampled unit represents an equal
share of its stratum's area). ``d1_sampling.draw_sample`` therefore selects
fields with probability proportional to area by default, which mimics
"random points within strata" from the plan (section 6, D1).

References
----------
Olofsson P., Foody G.M., Herold M., Stehman S.V., Woodcock C.E., Wulder M.A.
(2014) Good practices for estimating area and assessing accuracy of land
change. Remote Sensing of Environment 148, 42-57.
Stehman S.V. (2014) Estimating area and map accuracy for stratified random
sampling when the strata are different from the map classes. IJRS 35, 4923-4939.
"""

from __future__ import annotations

import math
from typing import Dict, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

Z95 = 1.959963984540054


def _ci(est: float, se: float, z: float, lo_clip: Optional[float] = None,
        hi_clip: Optional[float] = None) -> dict:
    lo = est - z * se if not math.isnan(se) else float("nan")
    hi = est + z * se if not math.isnan(se) else float("nan")
    if lo_clip is not None and not math.isnan(lo):
        lo = max(lo, lo_clip)
    if hi_clip is not None and not math.isnan(hi):
        hi = min(hi, hi_clip)
    return {"value": float(est), "se": float(se), "ci_low": float(lo),
            "ci_high": float(hi), "half_width": float(z * se) if not math.isnan(se) else float("nan")}


def _strat_stats(groups, weights, y_fn, x_fn=None, fpc=None):
    """Return (estimate, se) for a stratified mean (x_fn None) or a combined
    ratio estimator ``sum W ybar / sum W xbar``."""
    Y = 0.0
    X = 0.0
    parts = []
    for h, df in groups.items():
        W = weights[h]
        n = len(df)
        y = y_fn(df).astype(float)
        x = x_fn(df).astype(float) if x_fn is not None else None
        Y += W * y.mean()
        if x is not None:
            X += W * x.mean()
        parts.append((h, W, n, y, x))
    if x_fn is None:
        est = Y
    else:
        est = Y / X if X > 0 else float("nan")
    var = 0.0
    for h, W, n, y, x in parts:
        if n < 2:
            return est, float("nan")
        f = 1.0 - (n / fpc[h]) if fpc is not None and h in fpc and fpc[h] else 1.0
        if x is None:
            s2 = y.var(ddof=1)
        else:
            if math.isnan(est):
                return est, float("nan")
            s2 = y.var(ddof=1) + est * est * x.var(ddof=1) - 2 * est * np.cov(x, y, ddof=1)[0, 1]
        var += W * W * f * s2 / n
    if x_fn is not None:
        if X <= 0:
            return est, float("nan")
        var /= X * X
    return est, math.sqrt(max(var, 0.0))


def stratified_estimate(sample: pd.DataFrame, stratum_areas: Mapping[str, float],
                        map_col: str = "map_label", ref_col: str = "reference_label",
                        stratum_col: Optional[str] = "stratum",
                        map_areas: Optional[Mapping[str, float]] = None,
                        classes: Optional[Sequence[str]] = None,
                        stratum_unit_counts: Optional[Mapping[str, float]] = None,
                        z: float = Z95, area_unit: str = "ha") -> dict:
    """Error matrix, accuracies and bias-adjusted areas with 95% CIs.

    Parameters
    ----------
    sample : one row per sampled unit with map label, reference label and
        stratum. If ``stratum_col`` is None or missing, stratum = map label.
    stratum_areas : total area of each stratum (all strata, including any
        with no sample, which raises).
    map_areas : mapped area per map class. Defaults to ``stratum_areas`` when
        strata are the map classes; required otherwise for the map-vs-estimate
        comparison (left as None if not given).
    stratum_unit_counts : population size per stratum for a finite
        population correction. Omit (default) to match Olofsson et al.
    """
    df = sample.copy()
    if stratum_col is None or stratum_col not in df.columns:
        df["_stratum"] = df[map_col]
    else:
        df["_stratum"] = df[stratum_col].where(df[stratum_col].notna(), df[map_col])
    df = df[df[ref_col].notna() & (df[ref_col].astype(str).str.strip() != "")]
    df["_stratum"] = df["_stratum"].astype(str)
    df[map_col] = df[map_col].astype(str)
    df[ref_col] = df[ref_col].astype(str)

    strata_area = {str(k): float(v) for k, v in stratum_areas.items()}
    A = sum(strata_area.values())
    if A <= 0:
        raise ValueError("stratum areas must sum to > 0")
    missing = [h for h, a in strata_area.items() if a > 0 and h not in set(df["_stratum"])]
    if missing:
        raise ValueError(f"strata with area but no reference sample: {missing}")
    unknown = sorted(set(df["_stratum"]) - set(strata_area))
    if unknown:
        raise ValueError(f"sample strata not in stratum_areas: {unknown}")
    W = {h: a / A for h, a in strata_area.items()}
    groups = {h: g for h, g in df.groupby("_stratum")}
    strata_are_map = bool((df["_stratum"] == df[map_col]).all())

    if classes is None:
        cls = sorted(set(df[map_col]) | set(df[ref_col]) |
                     (set(strata_area) if strata_are_map else set()))
    else:
        cls = [str(c) for c in classes]
    if map_areas is None and strata_are_map:
        map_areas = strata_area
    map_areas = {str(k): float(v) for k, v in (map_areas or {}).items()}

    # Error matrix in area proportions (rows = map, cols = reference)
    pm = pd.DataFrame(0.0, index=cls, columns=cls)
    counts = pd.DataFrame(0, index=cls, columns=cls)
    for h, g in groups.items():
        n = len(g)
        for (m, r), c in g.groupby([map_col, ref_col]).size().items():
            if m in pm.index and r in pm.columns:
                pm.loc[m, r] += W[h] * c / n
                counts.loc[m, r] += int(c)
    pm.index.name = "map"
    pm.columns.name = "reference"

    fpc = {str(k): float(v) for k, v in stratum_unit_counts.items()} if stratum_unit_counts else None

    per_class = {}
    for k in cls:
        ua = _strat_stats(groups, W,
                          lambda d, k=k: ((d[map_col] == k) & (d[ref_col] == k)).values,
                          lambda d, k=k: (d[map_col] == k).values, fpc)
        pa = _strat_stats(groups, W,
                          lambda d, k=k: ((d[map_col] == k) & (d[ref_col] == k)).values,
                          lambda d, k=k: (d[ref_col] == k).values, fpc)
        ap = _strat_stats(groups, W, lambda d, k=k: (d[ref_col] == k).values, None, fpc)
        area = _ci(ap[0] * A, ap[1] * A, z, lo_clip=0.0)
        ma = map_areas.get(k)
        area["map_area"] = ma
        if ma is not None and area["value"] > 0:
            area["map_minus_estimate_pct"] = 100.0 * (ma - area["value"]) / area["value"]
        else:
            area["map_minus_estimate_pct"] = None
        per_class[k] = {
            "users_accuracy": _ci(ua[0], ua[1], z, 0.0, 1.0),
            "producers_accuracy": _ci(pa[0], pa[1], z, 0.0, 1.0),
            "area_proportion": _ci(ap[0], ap[1], z, 0.0, 1.0),
            "area": area,
            "n_mapped_in_sample": int((df[map_col] == k).sum()),
            "n_reference_in_sample": int((df[ref_col] == k).sum()),
        }
    oa = _strat_stats(groups, W, lambda d: (d[map_col] == d[ref_col]).values, None, fpc)

    return {
        "kind": "area_estimate",
        "method": "Olofsson et al. 2014 / Stehman 2014 stratified estimator",
        "area_unit": area_unit,
        "total_area": A,
        "n_sample": int(len(df)),
        "strata_equal_map_classes": strata_are_map,
        "strata": {h: {"area": strata_area[h], "weight": W[h],
                       "n": int(len(groups.get(h, [])))} for h in strata_area},
        "classes": cls,
        "error_matrix_counts": counts.to_dict(orient="index"),
        "error_matrix_proportions": pm.to_dict(orient="index"),
        "overall_accuracy": _ci(oa[0], oa[1], z, 0.0, 1.0),
        "per_class": per_class,
    }


def from_counts(count_matrix: pd.DataFrame, map_areas: Mapping[str, float],
                **kw) -> dict:
    """Convenience wrapper: build the per-unit sample from an error matrix
    of counts (rows = map class = stratum, columns = reference class)."""
    rows = []
    for m in count_matrix.index:
        for r in count_matrix.columns:
            c = int(count_matrix.loc[m, r])
            rows.extend([{"map_label": str(m), "reference_label": str(r),
                          "stratum": str(m)}] * c)
    return stratified_estimate(pd.DataFrame(rows), map_areas,
                               classes=[str(c) for c in count_matrix.index], **kw)


def summary_table(result: dict) -> pd.DataFrame:
    """Flat per-class table for CSV / Markdown."""
    rows = []
    for k, v in result["per_class"].items():
        rows.append({
            "class": k,
            "map_area": v["area"]["map_area"],
            "estimated_area": v["area"]["value"],
            "area_ci_low": v["area"]["ci_low"],
            "area_ci_high": v["area"]["ci_high"],
            "map_minus_estimate_pct": v["area"]["map_minus_estimate_pct"],
            "users_accuracy": v["users_accuracy"]["value"],
            "ua_ci_halfwidth": v["users_accuracy"]["half_width"],
            "producers_accuracy": v["producers_accuracy"]["value"],
            "pa_ci_halfwidth": v["producers_accuracy"]["half_width"],
        })
    return pd.DataFrame(rows)
