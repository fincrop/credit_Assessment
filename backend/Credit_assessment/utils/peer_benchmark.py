"""
Peer Benchmarking (Pillar 3)
============================
Converts a raw agronomic metric (e.g. NIRv-AUC yield-potential, peak vigor)
into a **peer-relative percentile** (0-100) against a cohort defined by
agro-climatic zone x season x crop-family.

Why peer-relative: "this parcel is in the 72nd percentile of yield-potential
for its zone/season/crop-family" is more robust and more explainable than an
absolute index, and it naturally absorbs regional/seasonal differences.

Cold-start design (important for the phased index -> calibrated scorecard path):
- When a cohort has enough observed samples (>= MIN_COHORT_N) the percentile is
  computed against the ACCUMULATED distribution (from MongoDB `cohort_stats`).
- Otherwise `percentile()` returns source="insufficient" and the caller keeps
  its internal self-calibrated score. Optionally a configurable PRIOR can be
  used instead (source="prior"). Either way the SAME interface later reads real
  cohort stats, so scoring transitions smoothly from cold-start to data-driven.

Persistence is intentionally injected (a dict now, a Mongo-backed loader later)
so this module has no hard database dependency.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple, List

# Minimum observed cohort samples before the cohort distribution is trusted.
MIN_COHORT_N = 20

# Optional cold-start priors (used only when use_priors=True). Values are on the
# metric's own scale. Kept deliberately wide so a prior-based percentile is soft.
DEFAULT_PRIORS: Dict[str, Dict[str, float]] = {
    # mean NIRv across a cycle (NIR*NDVI averaged over bins), typical Indian field
    "nirv_auc_mean": {"mean": 0.115, "std": 0.045},
    # cycle peak composite vigor (0-1)
    "vigor_peak":    {"mean": 0.62,  "std": 0.14},
    "_default":      {"mean": 0.50,  "std": 0.20},
}


def _norm_cdf(z: float) -> float:
    """Standard-normal CDF via erf (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class PeerBenchmark:
    """Cohort percentile engine with graceful cold-start."""

    def __init__(
        self,
        cohort_stats: Optional[Dict[str, Dict[str, Any]]] = None,
        priors: Optional[Dict[str, Dict[str, float]]] = None,
        use_priors: bool = False,
        min_cohort_n: int = MIN_COHORT_N,
    ):
        """
        Args:
            cohort_stats: {cohort_key: {metric: {"mean","std","n", "p10..p90"?}}}
                          (e.g. loaded from Mongo `cohort_stats`). Empty = cold start.
            priors:       optional metric priors for cold-start percentiles.
            use_priors:   if True, cold-start returns a prior-based percentile
                          (source="prior"); if False, returns source="insufficient"
                          so the caller can fall back to its internal score.
            min_cohort_n: samples required before a cohort distribution is trusted.
        """
        self.cohort_stats = cohort_stats or {}
        self.priors = priors or DEFAULT_PRIORS
        self.use_priors = bool(use_priors)
        self.min_cohort_n = int(min_cohort_n)

    # ------------------------------------------------------------------ #
    @staticmethod
    def cohort_key(
        agro_zone: Optional[str],
        season: Optional[str],
        crop_family: Optional[str],
    ) -> str:
        """Canonical cohort key: ZONE|SEASON|CROPFAMILY (upper-cased)."""
        def _norm(x: Optional[str]) -> str:
            return (str(x).strip() or "NA").upper() if x not in (None, "") else "NA"
        return "|".join([_norm(agro_zone), _norm(season), _norm(crop_family)])

    # ------------------------------------------------------------------ #
    def percentile(
        self,
        metric: str,
        value: float,
        cohort_key: Optional[str] = None,
    ) -> Tuple[Optional[float], Dict[str, Any]]:
        """
        Return (percentile_0_100 | None, meta).

        - Uses the cohort distribution when it has >= min_cohort_n samples.
        - Else, if use_priors: uses the metric prior (source="prior").
        - Else: returns (None, {"cohort_source": "insufficient", ...}) so the
          caller keeps its own internal score.
        """
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None, {"cohort_source": "invalid_value"}

        dist = None
        if cohort_key:
            dist = (self.cohort_stats.get(cohort_key) or {}).get(metric)

        if dist and int(dist.get("n", 0)) >= self.min_cohort_n:
            # Prefer empirical percentiles when provided, else normal approx.
            emp = dist.get("percentiles")  # optional: {"p10":..,"p50":..,"p90":..}
            if isinstance(emp, dict) and emp:
                pct = self._empirical_percentile(value, emp)
                meta = {"cohort_source": "cohort_empirical", "n": int(dist.get("n", 0)),
                        "cohort_key": cohort_key}
                return round(float(pct), 1), meta
            mean = float(dist.get("mean", 0.0))
            std = float(dist.get("std", 0.0))
            if std <= 0:
                std = 1e-6
            z = (value - mean) / std
            pct = 100.0 * _norm_cdf(z)
            return round(float(min(100.0, max(0.0, pct))), 1), {
                "cohort_source": "cohort_normal", "n": int(dist.get("n", 0)),
                "z": round(z, 3), "mean": round(mean, 4), "std": round(std, 4),
                "cohort_key": cohort_key,
            }

        if self.use_priors:
            p = self.priors.get(metric) or self.priors.get("_default")
            mean, std = float(p["mean"]), float(p["std"]) or 1e-6
            z = (value - mean) / std
            pct = 100.0 * _norm_cdf(z)
            return round(float(min(100.0, max(0.0, pct))), 1), {
                "cohort_source": "prior", "z": round(z, 3),
                "mean": mean, "std": std, "cohort_key": cohort_key,
            }

        return None, {"cohort_source": "insufficient", "cohort_key": cohort_key}

    # ------------------------------------------------------------------ #
    @staticmethod
    def _empirical_percentile(value: float, emp: Dict[str, float]) -> float:
        """Piecewise-linear percentile from stored empirical breakpoints."""
        # Accept keys like p5,p10,...,p95; sort by numeric percentile.
        pts: List[Tuple[float, float]] = []
        for k, v in emp.items():
            try:
                p = float(str(k).lstrip("pP"))
                pts.append((p, float(v)))
            except (TypeError, ValueError):
                continue
        if not pts:
            return 50.0
        pts.sort(key=lambda t: t[1])  # sort by metric value
        # Below/above range
        if value <= pts[0][1]:
            return pts[0][0]
        if value >= pts[-1][1]:
            return pts[-1][0]
        for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
            if v0 <= value <= v1 and v1 > v0:
                frac = (value - v0) / (v1 - v0)
                return p0 + frac * (p1 - p0)
        return 50.0

    # ------------------------------------------------------------------ #
    @classmethod
    def from_mongo(cls, mongo_helper: Any, **kwargs) -> "PeerBenchmark":
        """
        Build from accumulated cohort stats in Mongo (`cohort_stats` collection).
        Hook for the future data-driven path; returns a cold-start instance if
        the collection/loader is unavailable.
        """
        stats: Dict[str, Dict[str, Any]] = {}
        try:
            if mongo_helper is not None and hasattr(mongo_helper, "get_cohort_stats"):
                stats = mongo_helper.get_cohort_stats() or {}
        except Exception:
            stats = {}
        return cls(cohort_stats=stats, **kwargs)


__all__ = ["PeerBenchmark", "MIN_COHORT_N", "DEFAULT_PRIORS"]
