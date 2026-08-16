#!/usr/bin/env python3
"""
Inspect a stored assessment document.

Answers "what shape is this record, and where did its score go?" — which is
exactly the question when a baseline shows no recoverable score in the drift
report. Prints the shape, the recovered score, and where it was found, then a
pruned view of the document.

USAGE
-----
  python scripts/devtools/inspect_assessment.py <farmer_id>
  python scripts/devtools/inspect_assessment.py <farmer_id> --all
  python scripts/devtools/inspect_assessment.py --list

Run from the package root (backend/Credit_assessment).
"""

from __future__ import annotations

import argparse
import json
import os
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

from assessment.drift_analysis import describe_document  # noqa: E402

# Paths compare_one/_score_of tries, in order. Reported so a missing score can
# be traced to a specific structural difference rather than guessed at.
SCORE_PATHS = (
    ("index_score",),
    ("credit_score",),
    ("risk_assessment", "index_score"),
    ("farmer_level", "index_score"),
)

# Large blobs pruned from the printed view.
_BULKY = ("seasonal_ndvi", "weather_intervals", "farm_assessments",
          "satellite_data", "ai_enrichment", "calibration")


def _dig(doc, path):
    node = doc
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def _prune(doc, depth=0):
    """Readable view: drop bulk arrays, summarise their size instead."""
    if not isinstance(doc, dict):
        return doc
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if k in _BULKY:
            if isinstance(v, list):
                out[k] = f"<list of {len(v)} items, omitted>"
            elif isinstance(v, dict):
                out[k] = f"<dict with {len(v)} keys, omitted>"
            else:
                out[k] = "<omitted>"
        elif isinstance(v, dict) and depth < 2:
            out[k] = _prune(v, depth + 1)
        elif isinstance(v, list) and len(v) > 6:
            out[k] = f"<list of {len(v)} items>"
        else:
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("farmer_id", nargs="?", help="farmer to inspect")
    ap.add_argument("--all", action="store_true",
                    help="show every assessment for this farmer, not just the latest")
    ap.add_argument("--list", action="store_true",
                    help="list farmers that have assessments")
    ap.add_argument("--full", action="store_true", help="print the whole document")
    args = ap.parse_args()

    from mongodb_helper import MongoDBHelper

    db = MongoDBHelper()
    try:
        if args.list or not args.farmer_id:
            print("Farmers with stored assessments:\n")
            seen = set()
            for doc in db.assessments.find({}, {"farmer_id": 1, "assessment_date": 1,
                                                "assessment_type": 1}).sort(
                                                    "assessment_date", -1).limit(200):
                fid = doc.get("farmer_id")
                if fid and fid not in seen:
                    seen.add(fid)
                    print(f"  {fid:28s} {str(doc.get('assessment_date'))[:19]}  "
                          f"{doc.get('assessment_type', 'single_farm')}")
            print(f"\n{len(seen)} farmer(s).")
            return 0

        query = {"farmer_id": args.farmer_id}
        docs = list(db.assessments.find(query).sort("assessment_date", -1))
        if not docs:
            print(f"No assessments found for {args.farmer_id!r}.")
            return 1

        print(f"{len(docs)} assessment(s) for {args.farmer_id}\n")
        for i, doc in enumerate(docs if args.all else docs[:1], 1):
            d = describe_document(doc)
            print("=" * 70)
            print(f"#{i}  {str(doc.get('assessment_date'))[:19]}")
            print("=" * 70)
            print(f"  shape          : {d['shape']}")
            print(f"  status         : {d['status']}")
            print(f"  index_version  : {d['index_version']}")
            print(f"  recovered score: {d['score']}   band: {d['band']}")
            print(f"  n_plots_scored : {d['n_plots_scored']}")
            print("\n  Score lookup, path by path:")
            for path in SCORE_PATHS:
                val = _dig(doc, path)
                mark = "FOUND" if val is not None else "  -  "
                print(f"    [{mark}] {'.'.join(path):32s} = {val!r}")
            print("\n  Top-level keys:")
            for k in sorted(k for k in doc if k != "_id"):
                print(f"    {k}")
            print("\n  Document:")
            body = doc if args.full else _prune(doc)
            body.pop("_id", None)
            print(json.dumps(body, indent=2, default=str)[:6000])
            print()
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
