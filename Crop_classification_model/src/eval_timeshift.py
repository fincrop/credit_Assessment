"""
Inter-annual robustness check for the fused kharif classifier.

Nearly all cotton and all soybean labels are from 2023, a late-monsoon year.
A model can then learn the calendar ("green by mid-July means Onion") instead
of the crop. The frozen mh2023 test is the same year, so it cannot see this.

This replays the frozen test parcels with every observation date moved by
`--shifts` days (negative = an earlier monsoon, as in 2026 at Dhaswadi) and
reports per-class recall and how the predicted class mix moves. A robust
model keeps Cotton / Soyabean recall within a few points across shifts.

    python -m src.eval_timeshift --model models/crop_classifier_fused_v2.joblib
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ._bootstrap import DATA, REPORTS, setup_logging

log = setup_logging("eval_timeshift")
SERIES = DATA / "06_fused_series.parquet"
SPLIT = DATA / "splits" / "mh2023_split.json"
FOCUS = ("Cotton", "Soyabean", "Tur")


def shift_records(js: str, days: int) -> list:
    out = []
    for r in json.loads(js or "[]"):
        d = date.fromisoformat(r["date"]) + timedelta(days=days)
        out.append({**r, "date": d.isoformat()})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True)
    ap.add_argument("--shifts", default="-20,0,20")
    ap.add_argument("--cutoffs", default="10-01,full")
    a = ap.parse_args()
    from crop_analysis.fused_classifier import FusedClassifier

    clf = FusedClassifier(Path(a.model).resolve())
    test = set(json.loads(SPLIT.read_text())["test_geom_hashes"])
    df = pd.read_parquet(SERIES)
    df = df[df.geom_hash.isin(test)].reset_index(drop=True)
    report = {"model": Path(a.model).name, "n": len(df), "results": {}}
    for co in a.cutoffs.split(","):
        for s in (int(x) for x in a.shifts.split(",")):
            preds = []
            for r in df.itertuples():
                y = int(r.season_year)
                as_of = None if co == "full" else date(y, *map(int, co.split("-")))
                f, _ = clf.features(shift_records(r.s1_series, s), shift_records(r.refl_series, s), y, as_of)
                p = clf.predict(f)["all_probabilities"]
                preds.append(max(p, key=p.get))
            truth = df.Crop_Name.tolist()
            rec = {c: round(float(np.mean([p == c for p, t in zip(preds, truth) if t == c])), 3)
                   for c in FOCUS if c in truth}
            mix = Counter(preds).most_common(6)
            report["results"][f"{co} shift {s:+d}d"] = {"recall": rec, "predicted_mix": mix}
            log.info("%-6s shift %+3dd  recall %s  mix %s", co, s, rec, mix)
    out = REPORTS / f"timeshift_{Path(a.model).stem}.json"
    out.write_text(json.dumps(report, indent=1))
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
