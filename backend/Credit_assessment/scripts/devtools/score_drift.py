#!/usr/bin/env python3
"""
Score drift report — old pipeline vs current pipeline.

Phases 1, 2 and 4 changed scores deliberately (physical signal scale,
re-derived cycle thresholds, perennial detection, sub-indices that no longer
reward the absence of evidence). Before any of those numbers reach a lender we
need to say concretely how much moved, for whom, and driven by what.

The old code cannot be re-run, but it does not need to be. Its outputs are
already in `credit_assessments`. So:

    baseline = the most recent stored assessment per farmer
    current  = a fresh run of the pipeline as it stands now

USAGE
-----
  # Dry run: how many farmers have a usable baseline? Pulls no imagery.
  python scripts/devtools/score_drift.py --plan

  # Re-assess a sample and compare (satellite pulls; slow).
  python scripts/devtools/score_drift.py --limit 25

  # Everything with a baseline, writing artifacts.
  python scripts/devtools/score_drift.py --all --out drift_report

  # Compare without re-running: use assessments already written by the new code.
  python scripts/devtools/score_drift.py --no-rerun

NOTES
-----
* Re-running (the default) calls the real pipeline, so it costs satellite quota
  and takes minutes per farmer. Start with --plan, then a small --limit.
* Baselines are identified by NOT carrying the current signal version. A farmer
  whose only assessment is already from the new code is reported as having no
  baseline rather than being compared against itself.
* Nothing here writes to credit_assessments unless --persist is passed.

Run from the package root (backend/Credit_assessment).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The report contains non-ASCII (em dashes, arrows) and Windows consoles
# default to cp1252, which raises UnicodeEncodeError mid-write. Degrade
# unmappable characters instead of losing the report.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover - older/odd streams
        pass

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from assessment.drift_analysis import (  # noqa: E402
    compare_many, describe_document, format_report, summarise,
)

logger = logging.getLogger("score_drift")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _signal_version_of(doc: dict) -> str:
    """Signal version a stored assessment was produced with ('' if pre-versioning)."""
    for path in (
        ("signal_quality_summary", "signal_version"),
        ("risk_assessment", "signal_version"),
    ):
        node = doc
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            return node
    return ""


def load_baselines(db, current_signal_version: str, limit: int | None) -> dict:
    """
    Most recent PRE-CUTOVER assessment per farmer.

    A farmer whose only assessment already carries the current signal version
    has no baseline — comparing it against itself would manufacture a
    reassuring zero drift.
    """
    baselines: dict = {}
    skipped_already_new = 0

    cursor = (
        db.assessments.find({"status": {"$in": ["SUCCESS", None]}})
        .sort("assessment_date", -1)
    )
    for doc in cursor:
        fid = doc.get("farmer_id")
        if not fid or fid in baselines:
            continue
        if _signal_version_of(doc) == current_signal_version:
            skipped_already_new += 1
            continue
        doc.pop("_id", None)
        baselines[fid] = doc
        if limit and len(baselines) >= limit:
            break

    if skipped_already_new:
        logger.info(
            "Skipped %d assessment(s) already produced by the current signal "
            "version — they are not a baseline.", skipped_already_new,
        )
    return baselines


def build_pipeline():
    """
    Construct the pipeline the same way worker.py does, so the drift report
    measures what production actually runs.
    """
    from config import DEFAULT_CROP_MODEL_PATH, resolve_package_path
    from main import SatelliteBasedCreditPipeline

    model_path = str(
        resolve_package_path(
            os.environ.get("CROP_MODEL_PATH", DEFAULT_CROP_MODEL_PATH)
        )
    )
    return SatelliteBasedCreditPipeline(
        crop_model_path=model_path,
        ml_mode=os.environ.get("ML_MODE", "rule_based"),
        verbose=False,
        use_mongodb=True,
    )


def rerun(pipeline, farmer_ids: list, persist: bool = False) -> dict:
    """
    Re-assess each farmer with the current pipeline.

    persist defaults to False: a diagnostic must not write into the collection
    it is measuring against, or a re-run becomes its own baseline next time.
    """
    out: dict = {}
    total = len(farmer_ids)
    for i, fid in enumerate(farmer_ids, 1):
        logger.info("[%d/%d] re-assessing %s", i, total, fid)
        try:
            out[fid] = pipeline.assess_farmer_from_db(fid, save_to_db=persist)
        except Exception as e:                      # noqa: BLE001
            logger.error("  %s failed: %s", fid, e)
            out[fid] = {"status": "FAILED", "error": str(e)[:300]}
    return out


def load_current_from_db(db, farmer_ids: list, current_signal_version: str) -> dict:
    """Latest assessment already written by the current code (no re-run)."""
    out: dict = {}
    for fid in farmer_ids:
        for doc in db.assessments.find({"farmer_id": fid}).sort("assessment_date", -1):
            if _signal_version_of(doc) == current_signal_version:
                doc.pop("_id", None)
                out[fid] = doc
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=10,
                    help="max farmers to compare (default 10)")
    ap.add_argument("--all", action="store_true", help="every farmer with a baseline")
    ap.add_argument("--plan", action="store_true",
                    help="report what WOULD be compared and exit; pulls no imagery")
    ap.add_argument("--no-rerun", action="store_true",
                    help="compare against assessments already written by the new code")
    ap.add_argument("--farmers", nargs="*", help="specific farmer ids")
    ap.add_argument("--out", help="write <out>.txt and <out>.json")
    ap.add_argument("--persist", action="store_true",
                    help="also SAVE the re-runs to credit_assessments "
                         "(off by default: a diagnostic should not write into "
                         "the collection it is measuring)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    _configure_logging(not args.quiet)

    from config import PipelineConfig
    from mongodb_helper import MongoDBHelper

    current_signal_version = str(getattr(PipelineConfig, "SIGNAL_VERSION", ""))
    limit = None if (args.all or args.farmers) else args.limit

    db = MongoDBHelper()
    try:
        baselines = load_baselines(db, current_signal_version, limit)
        if args.farmers:
            baselines = {k: v for k, v in baselines.items() if k in set(args.farmers)}

        if not baselines:
            print("No pre-cutover assessments found — there is no baseline to")
            print("compare against. Drift cannot be measured from this database.")
            return 1

        farmer_ids = sorted(baselines)
        print(f"Baseline assessments found : {len(farmer_ids)}")
        print(f"Current signal version     : {current_signal_version}")

        if args.plan:
            print("\n--plan: no assessments run. Farmers that would be compared:\n")
            print(f"  {'farmer':28s} {'shape':20s} {'score':>8s} {'band':10s} date")
            usable = 0
            shapes: dict = {}
            for fid in farmer_ids:
                d = describe_document(baselines[fid])
                shapes[d["shape"]] = shapes.get(d["shape"], 0) + 1
                if d["score"] is not None:
                    usable += 1
                score_txt = "—" if d["score"] is None else f"{d['score']:.1f}"
                date = str(baselines[fid].get("assessment_date"))[:19]
                print(f"  {fid:28s} {d['shape']:20s} {score_txt:>8s} "
                      f"{str(d['band'] or '—'):10s} {date}")

            print(f"\n  Document shapes: "
                  + ", ".join(f"{k}={v}" for k, v in sorted(shapes.items())))
            print(f"  Baselines with a recoverable score: {usable}/{len(farmer_ids)}")

            if usable == 0:
                print("\n  !! NONE of these baselines carries a usable score, so drift")
                print("     cannot be measured against them. Inspect one with:")
                print("       python scripts/devtools/inspect_assessment.py <farmer_id>")
                return 1
            if usable < len(farmer_ids):
                print(f"\n  {len(farmer_ids) - usable} baseline(s) have no recoverable")
                print("  score and will be reported as 'no_baseline' rather than")
                print("  compared — they are not counted as drift.")

            print(f"\nRe-running these would make {len(farmer_ids)} satellite-backed "
                  f"assessment(s). Use --limit to sample first.")
            return 0

        if args.no_rerun:
            currents = load_current_from_db(db, farmer_ids, current_signal_version)
            if not currents:
                print("\nNo assessments from the current code found either.")
                print("Run without --no-rerun to generate them.")
                return 1
        else:
            print(f"\nRe-assessing {len(farmer_ids)} farmer(s). This pulls imagery "
                  f"and will take a while...")
            print(f"Writing results to credit_assessments: "
                  f"{'YES (--persist)' if args.persist else 'no'}\n")
            currents = rerun(build_pipeline(), farmer_ids, persist=args.persist)

        comparisons = compare_many(baselines, currents)
        summary = summarise(comparisons)
        report = format_report(comparisons, summary)

        print()
        print(report)

        if args.out:
            stamp = datetime.now(timezone.utc).isoformat()
            with open(f"{args.out}.txt", "w", encoding="utf-8") as fh:
                fh.write(f"# generated {stamp}\n\n{report}\n")
            with open(f"{args.out}.json", "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "generated_at": stamp,
                        "signal_version": current_signal_version,
                        "mode": "no_rerun" if args.no_rerun else "rerun",
                        "summary": summary,
                        "comparisons": comparisons,
                    },
                    fh, indent=2, default=str,
                )
            print(f"\nWrote {args.out}.txt and {args.out}.json")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
