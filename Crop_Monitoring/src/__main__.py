"""Run crop monitoring from the command line.

Demo (no Earth Engine):

    python -m src --demo

One classified parcel:

    python -m src --geometry field.geojson --crop Soyabean --confidence 0.8 --season kharif --as-of 2024-09-20
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path


def _parse_day(text: str | None) -> date | None:
    if not text:
        return None
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Present-season crop monitoring")
    parser.add_argument("--demo", action="store_true", help="Run the synthetic soybean season")
    parser.add_argument("--geometry", help="GeoJSON file containing the field polygon")
    parser.add_argument("--crop", default="Soyabean")
    parser.add_argument("--confidence", type=float, default=0.8)
    parser.add_argument("--season", default=None)
    parser.add_argument("--ecoregion", default=None)
    parser.add_argument("--as-of", dest="as_of", default=None)
    parser.add_argument("--sowing", default=None, help="YYYY-MM-DD farm record, if known")
    parser.add_argument("--district-yield", dest="district_yield", type=float, default=None)
    parser.add_argument("--out", default=None, help="Write the farm document JSON here")
    args = parser.parse_args(argv)

    from src.models import MonitorRequest
    from src.pipeline import run_monitoring

    if args.demo:
        from src.synthetic import soybean_mixed
        request, stack = soybean_mixed()
        document = run_monitoring(request, stack)
    else:
        if not args.geometry:
            parser.error("--geometry is required unless --demo is set")
        geometry = json.loads(Path(args.geometry).read_text(encoding="utf-8"))
        if geometry.get("type") == "Feature":
            geometry = geometry["geometry"]
        elif geometry.get("type") == "FeatureCollection":
            geometry = geometry["features"][0]["geometry"]
        request = MonitorRequest(
            crop=args.crop,
            geometry=geometry,
            confidence=args.confidence,
            season=args.season,
            ecoregion=args.ecoregion,
            as_of=_parse_day(args.as_of),
            sowing_hint=_parse_day(args.sowing),
            sowing_hint_source="provided",
            district_yield_t_ha=args.district_yield,
        )
        document = run_monitoring(request)

    text = json.dumps(document, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return document


if __name__ == "__main__":
    main()
