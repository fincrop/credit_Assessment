#!/usr/bin/env python3
"""
Build peer cohorts from accumulated feature snapshots.

PeerBenchmark is a correct percentile engine that has never fired:
upsert_cohort_stat had no caller, so cohort_stats stayed empty and every parcel
fell through to the internal fallback. The raw material has been accumulating
all along — save_feature_snapshot writes nirv_auc_mean_by_cycle and cohort_key
on every assessment. Nothing aggregated them. This does.

Needs volume, not field data: a percentile among parcels we assessed is a real
self-referential fact. But a cohort below the minimum size is an artefact of who
happened to be onboarded first, so under-sized cohorts are reported and NOT
stored.

Run this periodically (a nightly cron is the intended home) and after any batch
of new assessments.

USAGE
-----
  python scripts/devtools/build_cohorts.py            # report only, no writes
  python scripts/devtools/build_cohorts.py --write

Run from the package root (backend/Credit_assessment).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:  # pragma: no cover
    pass

from assessment.cohort_builder import build_cohorts, summarise  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="persist the warm cohorts (default is report only)")
    ap.add_argument("--min-n", type=int, default=None,
                    help="override the minimum cohort size")
    args = ap.parse_args()

    from mongodb_helper import MongoDBHelper

    db = MongoDBHelper()
    try:
        if db.feature_store is None:
            print("No feature_store collection.")
            return 1

        snapshots = list(db.feature_store.find({}, {"_id": 0}))
        print(f"Feature snapshots read : {len(snapshots)}")
        if not snapshots:
            print("Nothing to aggregate. Run some assessments first.")
            return 0

        cohorts = build_cohorts(snapshots, min_cohort_n=args.min_n)
        stats = summarise(snapshots, cohorts, min_cohort_n=args.min_n)

        print(f"Minimum cohort size    : {stats['min_cohort_n']} farmers")
        print(f"Distinct cohort keys   : {stats['n_cohort_keys_seen']}")
        print(f"Warm (usable)          : {stats['n_cohorts_warm']}")
        print(f"Still short            : {stats['n_cohorts_short']}")
        print()

        if cohorts:
            print("Warm cohorts:")
            for key, doc in cohorts.items():
                m = doc["metrics"]["nirv_auc_mean"]
                print(f"  {key:32s} n={m['n']:4d} farmers  "
                      f"median={m['percentiles']['p50']:.4f}")
            print()

        if stats["short_by_key"]:
            print("Short of the minimum (not stored):")
            for key, info in stats["short_by_key"].items():
                print(f"  {key:32s} {info['farmers']:4d} farmers "
                      f"(needs {info['needs']} more)")
            print()

        if not cohorts:
            print("No cohort reached the minimum, so peer benchmarking stays")
            print("inactive and vigour remains an absolute score. That is the")
            print("correct behaviour — a percentile over a handful of parcels")
            print("would be an artefact of onboarding order, not a comparison.")

        if args.write and cohorts:
            written = 0
            for key, doc in cohorts.items():
                if db.upsert_cohort_stat(key, doc["metrics"]):
                    written += 1
            print(f"Wrote {written} cohort(s) to cohort_stats.")
        elif cohorts:
            print("Report only — pass --write to persist.")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
