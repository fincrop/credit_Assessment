"""
Peer cohort builder
===================
Turns accumulated feature snapshots into the distributions `PeerBenchmark` reads.

WHY THIS EXISTS
───────────────
`PeerBenchmark` is a correct percentile engine that has never once fired.
`upsert_cohort_stat` had no caller anywhere in the codebase, so `cohort_stats`
stayed empty and every parcel fell through to the internal fallback — while the
narrative surface described vigour as "peer-relative". The claim was removed;
this makes it true.

The raw material has been accumulating all along: `save_feature_snapshot` writes
`nirv_auc_mean_by_cycle` and `cohort_key` on every assessment. Nothing
aggregated them.

NO GROUND TRUTH NEEDED
──────────────────────
A percentile among parcels WE assessed is a real, self-referential fact. It
needs volume, not field data. But two honesty constraints follow from that:

  * A cohort must not activate below MIN_COHORT_N. Below that a "percentile" is
    an artefact of who happened to be onboarded first.
  * It is a percentile among ASSESSED PARCELS, not among farms in the region.
    Our set is not a random sample, and anything built on it must say so —
    hence `population` on every stored cohort.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from config import PipelineConfig
from utils.mongo_encoding import to_mongo, utc_now

logger = logging.getLogger(__name__)

COHORT_BUILDER_VERSION = "cohort_builder_v1"

# Percentile breakpoints stored per metric. PeerBenchmark reads these as
# piecewise-linear empirical breakpoints, which beats a normal approximation
# for yield distributions — they are routinely skewed.
_BREAKPOINTS = (5, 10, 25, 50, 75, 90, 95)

__all__ = ["build_cohorts", "COHORT_BUILDER_VERSION"]


def _values_from_snapshot(snap: Dict) -> List[float]:
    """Per-cycle NIRv-AUC values from one feature snapshot."""
    feats = snap.get("features") or {}
    raw = feats.get("nirv_auc_mean_by_cycle") or []
    out: List[float] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f):
            out.append(f)
    return out


def build_cohorts(
    snapshots: Iterable[Dict],
    *,
    min_cohort_n: Optional[int] = None,
) -> Dict[str, Dict]:
    """
    Aggregate feature snapshots into per-cohort metric distributions.

    Pure: takes snapshots, returns cohort documents. The caller does the I/O,
    which keeps this testable without a database.

    Returns {cohort_key: {metrics, n, population, ...}} containing ONLY cohorts
    that reached the minimum size. Under-sized cohorts are reported in the
    caller's summary rather than stored, so a half-populated cohort can never
    be mistaken for a usable one.
    """
    P = PipelineConfig
    if min_cohort_n is None:
        min_cohort_n = int(getattr(P, "PEER_BENCHMARK_MIN_COHORT_N", 20))

    # cohort_key -> {"values": [...], "farmers": set()}
    buckets: Dict[str, Dict[str, Any]] = {}
    for snap in snapshots or []:
        if not isinstance(snap, dict):
            continue
        key = ((snap.get("features") or {}).get("cohort_key")
               or snap.get("cohort_key"))
        if not key:
            continue
        vals = _values_from_snapshot(snap)
        if not vals:
            continue
        b = buckets.setdefault(str(key), {"values": [], "farmers": set()})
        b["values"].extend(vals)
        fid = snap.get("farmer_id")
        if fid:
            b["farmers"].add(str(fid))

    cohorts: Dict[str, Dict] = {}
    for key, b in buckets.items():
        vals = np.array(b["values"], dtype=float)
        # n counts FARMERS, not cycles. One farm with six cycles is one
        # observation of a farm, and counting cycles would let a single parcel
        # manufacture a cohort.
        n_farmers = len(b["farmers"])
        if n_farmers < min_cohort_n:
            continue

        percentiles = {
            f"p{p}": round(float(np.percentile(vals, p)), 6) for p in _BREAKPOINTS
        }
        cohorts[key] = to_mongo({
            "cohort_key": key,
            "builder_version": COHORT_BUILDER_VERSION,
            "updated_at": utc_now(),
            "metrics": {
                "nirv_auc_mean": {
                    "n": n_farmers,
                    "n_values": int(vals.size),
                    "mean": round(float(np.mean(vals)), 6),
                    "std": round(float(np.std(vals)), 6),
                    "percentiles": percentiles,
                },
            },
            # Stated on every cohort so no consumer can quietly present this as
            # a regional statistic. It is a comparison against the parcels we
            # happen to have assessed.
            "population": (
                "parcels assessed by this pipeline in this agro-zone/season; "
                "not a random sample of farms in the region"
            ),
        })

    return cohorts


def summarise(
    snapshots: Iterable[Dict],
    cohorts: Dict[str, Dict],
    *,
    min_cohort_n: Optional[int] = None,
) -> Dict:
    """Counts for the operator: what warmed, what is still short, and by how much."""
    P = PipelineConfig
    if min_cohort_n is None:
        min_cohort_n = int(getattr(P, "PEER_BENCHMARK_MIN_COHORT_N", 20))

    per_key: Dict[str, set] = {}
    for snap in snapshots or []:
        if not isinstance(snap, dict):
            continue
        key = ((snap.get("features") or {}).get("cohort_key")
               or snap.get("cohort_key"))
        fid = snap.get("farmer_id")
        if key and fid:
            per_key.setdefault(str(key), set()).add(str(fid))

    short = {
        k: {"farmers": len(v), "needs": min_cohort_n - len(v)}
        for k, v in per_key.items() if len(v) < min_cohort_n
    }
    return {
        "min_cohort_n": min_cohort_n,
        "n_cohort_keys_seen": len(per_key),
        "n_cohorts_warm": len(cohorts),
        "n_cohorts_short": len(short),
        "short_by_key": dict(sorted(
            short.items(), key=lambda kv: -kv[1]["farmers"]
        )[:20]),
    }
