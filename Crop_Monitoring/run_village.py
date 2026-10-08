"""Run the raster engine on a classification result from the command line.

    python run_village.py <classification.geojson> --year 2026 --as-of 2026-10-02 \
        --out outputs/dhaswadi_2026 [--crops Cotton,Soyabean] [--district-yields file.csv]

Writes <out>/monitoring.json (field records, farms/zones/fields shapes) and the
raster products under <out>/cog, <out>/png, <out>/products.json.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src._bootstrap  # noqa: F401,E402
from src.raster import engine  # noqa: E402

NON_CROP = {"", "Unclassified", "Abstained", "Fallow", "Non-agricultural", "Water",
            "Not requested", "Others", "Insufficient data"}
UNKNOWN_OK = {"Unclassified", "Abstained", "Not requested", "Others", "Insufficient data"}


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
    except ImportError:
        pass
    raise TypeError(type(o))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("classification")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--season", default="kharif")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crops", default="")
    ap.add_argument("--district-yields", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--village-normal", action="store_true")
    ap.add_argument("--include-unknown", action="store_true",
                    help="monitor Unclassified / Abstained / Insufficient data / Not requested "
                         "fields as crop 'Unknown' (crop group, sowing, stage, condition)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    fc = json.loads(Path(a.classification).read_text(encoding="utf-8"))
    want = {c for c in a.crops.split(",") if c}
    fields = []
    for f in fc["features"]:
        p = f.get("properties") or {}
        crop = str(p.get("crop") or "")
        if a.include_unknown and crop in UNKNOWN_OK:
            crop = "Unknown"
        elif crop in NON_CROP or (want and crop not in want) or not f.get("geometry"):
            continue
        if not f.get("geometry"):
            continue
        fields.append({
            "field_id": str(p.get("field_id")), "crop": crop, "geometry": f["geometry"],
            "area_ha": p.get("area_ha"), "confidence": p.get("p_top1", p.get("confidence")),
            "classification": {k: p.get(k) for k in ("status", "model_top_crop", "top2_crop",
                                                     "p_top1", "margin", "cycle_complete")
                               if p.get(k) is not None},
        })
    yields = []
    if a.district_yields:
        with open(a.district_yields, newline="", encoding="utf-8") as fh:
            yields = [r for r in csv.DictReader(fh) if r.get("yield_t_ha")]
    out = Path(a.out)
    req = engine.VillageRequest(fields=fields, year=a.year, as_of=date.fromisoformat(a.as_of),
                                season=a.season, out_dir=out, name=a.name or Path(a.classification).stem,
                                district_yields=yields, village_normal=a.village_normal)
    t = time.time()
    doc = engine.run(req, progress=lambda m: logging.info(m))
    out.mkdir(parents=True, exist_ok=True)
    (out / "monitoring.json").write_text(json.dumps(doc, default=_json_default), encoding="utf-8")
    logging.info("done: %d fields in %.0f s -> %s", len(fields), time.time() - t, out / "monitoring.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
