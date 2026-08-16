#!/usr/bin/env python3
"""
Audit the land-cover verdict for one parcel.

A rejection that cannot be audited is not usable: it either refuses a real
farmer or hides a broken gate, and there is no way to tell which without the
numbers. This prints every statistic the verdict rested on, what each evidence
stream concluded, and the parcel centroid so you can pull it up on imagery.

USAGE
-----
  python scripts/devtools/inspect_land_cover.py <farmer_id>
  python scripts/devtools/inspect_land_cover.py <farmer_id> --json

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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("farmer_id")
    ap.add_argument("--json", action="store_true", help="dump the raw verdict")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING)

    from config import PipelineConfig, resolve_package_path
    from crop_analysis.land_cover_gate import classify_land_cover
    from main import SatelliteBasedCreditPipeline

    pipeline = SatelliteBasedCreditPipeline(
        crop_model_path=str(resolve_package_path(
            os.environ.get("CROP_MODEL_PATH", "models/crop_classifier_model.joblib"))),
        verbose=False,
        use_mongodb=True,
    )

    farm = pipeline.db.get_farm_by_id(args.farmer_id)
    if not farm:
        print(f"Farmer {args.farmer_id!r} not found.")
        return 1

    lat, lon = farm.get("latitude"), farm.get("longitude")
    print(f"Farmer      : {args.farmer_id}")
    print(f"Declared crop: {farm.get('crop')!r}")
    print(f"Area (ha)   : {farm.get('field_area_ha')}")
    print(f"Centroid    : {lat}, {lon}")
    if lat and lon:
        print(f"  Imagery   : https://www.google.com/maps/@{lat},{lon},600m/data=!3m1!1e3")
    print()

    print("Collecting satellite series (this pulls imagery)...\n")
    geometry = pipeline._convert_geometry_from_db(farm.get("geometry"))
    satellite = pipeline.satellite_collector.collect_historical_data(
        latitude=lat,
        longitude=lon,
        field_area_ha=farm.get("field_area_ha"),
        geometry=geometry,
    )
    continuous = (satellite or {}).get("continuous_data") or {}

    verdict = classify_land_cover(
        continuous,
        n_cycles=None,
        field_area_ha=farm.get("field_area_ha"),
        location={"latitude": lat, "longitude": lon},
        registry_crop=farm.get("crop"),
    )

    if args.json:
        print(json.dumps(verdict, indent=2, default=str))
        return 0

    print("=" * 66)
    print(f"VERDICT: {verdict['class']}  ({verdict['outcome'].upper()})  "
          f"confidence {verdict['confidence']}")
    print("=" * 66)
    print(f"  {verdict['reason']}")
    print(f"  cultivable: {verdict['is_cultivable']}   "
          f"streams agree: {verdict.get('agreement')}")
    print()

    print("-- Evidence (raw index statistics) " + "-" * 30)
    ev = verdict.get("evidence") or {}
    for key in ("n_obs", "ndvi_p10", "ndvi_p50", "ndvi_p90", "ndvi_max",
                "ndvi_amplitude", "frac_below_bare", "frac_above_vegetated",
                "water_index_source", "water_p50", "water_frac_positive",
                "ndbi_p50", "ndbi_frac_positive", "bsi_p50",
                "nirv_p50", "nirv_p90"):
        print(f"  {key:24s} {ev.get(key)}")
    print()

    print("-- What each stream concluded " + "-" * 35)
    for name, s in (verdict.get("streams") or {}).items():
        print(f"  {name:16s} {str(s.get('class')):12s} confidence {s.get('confidence')}")
    print()

    print("-- Reasoning " + "-" * 52)
    for note in verdict.get("notes") or []:
        print(f"  - {note}")
    print()

    print("-- Thresholds in force " + "-" * 42)
    for key in ("LANDCOVER_NDVI_BARE_SOIL", "LANDCOVER_NDVI_VEGETATED",
                "LANDCOVER_NDVI_EVERGREEN", "LANDCOVER_MIN_NDVI_AMPLITUDE",
                "LANDCOVER_WATER_FRACTION", "LANDCOVER_BUILTUP_FRACTION",
                "LANDCOVER_BSI_BARE", "LANDCOVER_REJECT_CONFIDENCE"):
        print(f"  {key:34s} {getattr(PipelineConfig, key, None)}")
    print()

    print("-- Sanity check " + "-" * 49)
    print("  Open the imagery link above. If this parcel is visibly cropped,")
    print("  the rejection is a FALSE POSITIVE and the gate needs adjusting —")
    print("  compare the NDVI percentiles above against what you can see.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
