#!/usr/bin/env python3
"""Delete chosen classification or monitoring runs from MongoDB.

The pages read these collections live, so a deleted row leaves the UI on refresh:

  classification_jobs     classification history, including failed runs
  monitoring_jobs         monitoring history
  monitoring_parcels      field rows for a monitoring run

Each row has its own number. Two runs can share a name (both called Dhaswadi);
the number, time, and zone count tell them apart. Deleting a monitoring number
leaves classifications in place. Deleting a classification number leaves
monitoring in place.

Farmers, logins, and credit assessments are not touched.

Examples (from the repo root):

  python Crop_classification_model/delete.py
      List every row and ask which numbers to delete.

  python Crop_classification_model/delete.py --monitoring 2 --yes
      Delete monitoring row m2 only.

  python Crop_classification_model/delete.py --classification 6 --yes
      Delete classification row c6 only.

  python Crop_classification_model/delete.py --classification failed --yes
      Delete every failed classification. Monitoring stays.

  python Crop_classification_model/delete.py --monitoring all --yes
      Delete every monitoring run. Classifications stay.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENV = ROOT / "backend" / "Credit_assessment" / ".env"
FRONTEND_ENV = ROOT / "frontend" / ".env.local"
RASTER_ROOT = ROOT / "Crop_Monitoring" / "outputs"

CLASSIFICATION = "classification_jobs"
MONITORING = "monitoring_jobs"
PARCELS = "monitoring_parcels"

CLASSIFICATION_FIELDS = {
    "stage": 1,
    "error": 1,
    "created_at": 1,
    "finished_at": 1,
    "updated_at": 1,
    "total_area_ha": 1,
    "inputs.region_name": 1,
    "inputs.season": 1,
    "inputs.year": 1,
    "inputs.name": 1,
    "areas.name": 1,
    "areas.area_ha": 1,
    "result.aoi_name": 1,
    "result.season": 1,
    "result.year": 1,
    "result.field_count": 1,
    "result.total_area_ha": 1,
    "result.stats.crop": 1,
    "result.stats.area_share": 1,
}

MONITORING_FIELDS = {
    "stage": 1,
    "error": 1,
    "created_at": 1,
    "finished_at": 1,
    "total_area_ha": 1,
    "inputs.name": 1,
    "inputs.crop": 1,
    "inputs.season": 1,
    "inputs.classification_job_id": 1,
    "inputs.village": 1,
    "result.name": 1,
    "result.crop": 1,
    "result.season": 1,
    "result.raster_dir": 1,
    "result.zone_count": 1,
}

_CODE = re.compile(r"^([cm])(\d+)(?:-(\d+))?$")
_CODE_WORD = re.compile(r"^([cm])-(all|failed)$")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_HEX_ID = re.compile(r"^[0-9a-f]{6,24}$")


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        _parse_env_file(path)
        return
    load_dotenv(path, override=False)


def _parse_env_file(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_connection() -> tuple[str, str]:
    _load_env_file(BACKEND_ENV)
    _load_env_file(FRONTEND_ENV)
    uri = (os.environ.get("MONGODB_URI") or "").strip()
    database = (
        os.environ.get("MONGODB_DATABASE")
        or os.environ.get("MONGODB_DB")
        or "agristack"
    ).strip()
    if not uri:
        sys.exit(
            "MONGODB_URI is not set. Put it in backend/Credit_assessment/.env "
            "(the same file the app uses) and run this script again."
        )
    return uri, database


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone().strftime("%b %d, %Y, %I:%M %p")
    if isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return moment.astimezone().strftime("%b %d, %Y, %I:%M %p")
    return ""


def classification_name(doc: dict) -> str:
    inputs = doc.get("inputs") or {}
    result = doc.get("result") or {}
    areas = doc.get("areas") or []
    area_name = ""
    if areas and isinstance(areas[0], dict):
        area_name = _text(areas[0].get("name"))
    return (
        _text(inputs.get("region_name"))
        or _text(result.get("aoi_name"))
        or area_name
        or "Area of interest"
    )


def monitoring_name(doc: dict) -> str:
    inputs = doc.get("inputs") or {}
    result = doc.get("result") or {}
    return (
        _text(result.get("name"))
        or _text(inputs.get("name"))
        or _text(inputs.get("village"))
        or "Field"
    )


def _top_crop(doc: dict) -> str:
    stats = (doc.get("result") or {}).get("stats") or []
    best = ""
    best_share = -1.0
    for row in stats:
        if not isinstance(row, dict):
            continue
        share = float(row.get("area_share") or 0)
        crop = _text(row.get("crop"))
        if share > best_share and crop:
            best_share = share
            best = crop
    return best


def _area_ha(doc: dict) -> str:
    result = doc.get("result") or {}
    areas = doc.get("areas") or []
    total = result.get("total_area_ha")
    if total is None:
        total = doc.get("total_area_ha")
    if total is None and areas:
        total = sum(float(a.get("area_ha") or 0) for a in areas if isinstance(a, dict))
    if total is None:
        return ""
    number = float(total)
    return f"{number:.0f} ha" if number >= 10 else f"{number:.1f} ha"


def _short_error(doc: dict) -> str:
    text = _text(doc.get("error"))
    if len(text) > 140:
        return text[:137] + "..."
    return text


def classification_detail(doc: dict) -> str:
    result = doc.get("result") or {}
    inputs = doc.get("inputs") or {}
    season = _text(result.get("season")) or _text(inputs.get("season")) or "kharif"
    year = result.get("year") or inputs.get("year") or ""
    fields = result.get("field_count")
    bits = [season.capitalize(), str(year), _area_ha(doc)]
    if isinstance(fields, (int, float)):
        bits.append(f"{int(fields)} fields")
    crop = _top_crop(doc)
    if crop:
        bits.append(crop)
    return " | ".join(bit for bit in bits if bit)


def monitoring_detail(doc: dict) -> str:
    result = doc.get("result") or {}
    inputs = doc.get("inputs") or {}
    bits = [
        _text(result.get("season")) or _text(inputs.get("season")),
        _text(result.get("crop")) or _text(inputs.get("crop")),
        _area_ha(doc),
    ]
    zones = result.get("zone_count")
    if isinstance(zones, (int, float)):
        bits.append(f"{int(zones)} zones")
    return " | ".join(bit for bit in bits if bit)


def _print_row(code: str, name: str, doc: dict, detail: str) -> None:
    stage = str(doc.get("stage") or "queued")
    when = _when(doc.get("finished_at") or doc.get("created_at"))
    print(f"  {code:<5} {stage:<10} {name}")
    line = " | ".join(part for part in (when, detail) if part)
    print(f"        {line}")
    print(f"        id {doc['_id']}")
    if stage == "failed" and _short_error(doc):
        print(f"        {_short_error(doc)}")


def print_catalog(classifications: list[dict], monitoring: list[dict]) -> None:
    print(f"CLASSIFICATIONS  ({len(classifications)})  pick with c1, c2, ...")
    if not classifications:
        print("  (none)")
    for index, doc in enumerate(classifications, start=1):
        _print_row(f"c{index}", classification_name(doc), doc, classification_detail(doc))
        print()
    _print_duplicates(classifications, classification_name, "c")

    print(f"MONITORING  ({len(monitoring)})  pick with m1, m2, ...")
    if not monitoring:
        print("  (none)")
    for index, doc in enumerate(monitoring, start=1):
        _print_row(f"m{index}", monitoring_name(doc), doc, monitoring_detail(doc))
        print()
    _print_duplicates(monitoring, monitoring_name, "m")


def _print_duplicates(docs: list[dict], name_of, prefix: str) -> None:
    grouped: dict[str, list[int]] = {}
    for index, doc in enumerate(docs, start=1):
        grouped.setdefault(name_of(doc), []).append(index)
    for name, indexes in grouped.items():
        if len(indexes) < 2:
            continue
        codes = ", ".join(f"{prefix}{index}" for index in indexes)
        print(f"  {name} is on more than one row: {codes}.")
        print("  Pick the number. The time on each row is what separates them.")
        print()


def _add_range(chosen: set[int], start: int, end: int, count: int, label: str) -> None:
    if start > end:
        start, end = end, start
    if start < 1 or end > count:
        raise ValueError(f"{label}{start}-{label}{end} is outside 1..{count}.")
    chosen.update(range(start - 1, end))


def _add_one(chosen: set[int], number: int, count: int, label: str) -> None:
    if number < 1 or number > count:
        span = f"{label}1" if count == 1 else f"{label}1..{label}{count}"
        raise ValueError(f"{label}{number} is not in the list. Rows are {span}.")
    chosen.add(number - 1)


def _add_failed(chosen: set[int], docs: list[dict]) -> None:
    chosen.update(index for index, doc in enumerate(docs) if doc.get("stage") == "failed")


def _add_id(token: str, classifications: list[dict], monitoring: list[dict],
            class_idx: set[int], mon_idx: set[int]) -> None:
    class_hits = [i for i, doc in enumerate(classifications) if str(doc["_id"]).startswith(token)]
    mon_hits = [i for i, doc in enumerate(monitoring) if str(doc["_id"]).startswith(token)]
    hits = len(class_hits) + len(mon_hits)
    if hits == 0:
        raise ValueError(f"No row has an id starting with {token}.")
    if hits > 1:
        raise ValueError(
            f"Id {token} matches more than one row. Use the c or m number instead."
        )
    if class_hits:
        class_idx.add(class_hits[0])
    else:
        mon_idx.add(mon_hits[0])


def parse_selection(
    raw: str,
    classifications: list[dict],
    monitoring: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Turn 'm2', 'c6', 'c1,m2', 'm all', or 'c failed' into the chosen rows."""
    tokens = raw.replace(",", " ").lower().split()
    if not tokens or tokens == ["q"] or tokens == ["quit"]:
        raise ValueError("Cancelled.")

    class_idx: set[int] = set()
    mon_idx: set[int] = set()
    kind: str | None = None
    n_class = len(classifications)
    n_mon = len(monitoring)

    for token in tokens:
        if token in {"c", "classification", "classifications"}:
            kind = "c"
            continue
        if token in {"m", "monitoring"}:
            kind = "m"
            continue
        if token in {"q", "quit", "exit"}:
            raise ValueError("Cancelled.")

        word = _CODE_WORD.fullmatch(token)
        if word:
            side, which = word.group(1), word.group(2)
            target = class_idx if side == "c" else mon_idx
            docs = classifications if side == "c" else monitoring
            if which == "all":
                target.update(range(len(docs)))
            else:
                _add_failed(target, docs)
            kind = side
            continue

        if token == "all":
            if kind == "c":
                class_idx.update(range(n_class))
            elif kind == "m":
                mon_idx.update(range(n_mon))
            else:
                class_idx.update(range(n_class))
                mon_idx.update(range(n_mon))
            continue

        if token == "failed":
            if kind == "c":
                _add_failed(class_idx, classifications)
            elif kind == "m":
                _add_failed(mon_idx, monitoring)
            else:
                raise ValueError("Write 'c failed' or 'm failed'.")
            continue

        coded = _CODE.fullmatch(token)
        if coded:
            side, start_s, end_s = coded.group(1), coded.group(2), coded.group(3)
            target = class_idx if side == "c" else mon_idx
            count = n_class if side == "c" else n_mon
            if end_s:
                _add_range(target, int(start_s), int(end_s), count, side)
            else:
                _add_one(target, int(start_s), count, side)
            kind = side
            continue

        span = _RANGE.fullmatch(token)
        if span:
            if kind not in {"c", "m"}:
                raise ValueError(f"Write c{span.group(1)}-c{span.group(2)} or m{span.group(1)}-m{span.group(2)}.")
            target = class_idx if kind == "c" else mon_idx
            count = n_class if kind == "c" else n_mon
            _add_range(target, int(span.group(1)), int(span.group(2)), count, kind)
            continue

        if token.isdigit():
            if kind not in {"c", "m"}:
                raise ValueError(f"Use c{token} for a classification or m{token} for monitoring.")
            target = class_idx if kind == "c" else mon_idx
            count = n_class if kind == "c" else n_mon
            _add_one(target, int(token), count, kind)
            continue

        if _HEX_ID.fullmatch(token):
            _add_id(token, classifications, monitoring, class_idx, mon_idx)
            kind = None
            continue

        raise ValueError(
            f"Do not understand '{token}'. Examples: m2, c6, c1,m2, m all, c failed."
        )

    if not class_idx and not mon_idx:
        raise ValueError("No rows selected.")
    chosen_class = [classifications[i] for i in sorted(class_idx)]
    chosen_mon = [monitoring[i] for i in sorted(mon_idx)]
    return chosen_class, chosen_mon


def _raster_dir(doc: dict) -> Path | None:
    raw = _text((doc.get("result") or {}).get("raster_dir"))
    if not raw:
        return None
    path = Path(raw).resolve()
    root = RASTER_ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if path == root or not path.is_dir():
        return None
    return path


def delete_raster_dirs(docs: list[dict]) -> int:
    removed = 0
    for doc in docs:
        path = _raster_dir(doc)
        if path is None:
            continue
        shutil.rmtree(path)
        removed += 1
        print(f"  removed raster folder {path}")
    return removed


def _selection_from_args(args: argparse.Namespace) -> str:
    parts: list[str] = []
    if args.classification:
        parts.append(f"c {args.classification}")
    if args.monitoring:
        parts.append(f"m {args.monitoring}")
    return " ".join(parts)


def _prompt(classifications: list[dict], monitoring: list[dict]) -> str:
    print("Classifications and monitoring are separate lists.")
    print("  m2           that monitoring row only")
    print("  c6           that classification row only")
    print("  c1,m2        one of each")
    print("  m all        every monitoring row")
    print("  c all        every classification")
    print("  c failed     every failed classification")
    print("  all          both lists")
    print("  q            quit")
    print()
    while True:
        try:
            raw = input("Selection: ").strip()
        except EOFError:
            return ""
        if not raw:
            print("Type a row, such as m2 or c6, or q to quit.")
            continue
        try:
            parse_selection(raw, classifications, monitoring)
        except ValueError as exc:
            if str(exc) == "Cancelled.":
                return ""
            print(exc)
            continue
        return raw


def print_plan(
    classifications: list[dict],
    monitoring: list[dict],
    chosen_class: list[dict],
    chosen_mon: list[dict],
    parcel_count: int,
) -> None:
    class_codes = {id(doc): f"c{index}" for index, doc in enumerate(classifications, start=1)}
    mon_codes = {id(doc): f"m{index}" for index, doc in enumerate(monitoring, start=1)}
    print("Will delete:")
    if chosen_class:
        print(f"  classifications ({len(chosen_class)})")
        for doc in chosen_class:
            print(f"    {class_codes[id(doc)]}  {classification_name(doc)}  {_when(doc.get('finished_at') or doc.get('created_at'))}")
    else:
        print("  classifications: none (those rows stay on the page)")
    if chosen_mon:
        print(f"  monitoring ({len(chosen_mon)})")
        for doc in chosen_mon:
            when = _when(doc.get("finished_at") or doc.get("created_at"))
            print(f"    {mon_codes[id(doc)]}  {monitoring_name(doc)}  {when}  {monitoring_detail(doc)}")
        print(f"  parcel rows stored for those monitoring runs: {parcel_count}")
    else:
        print("  monitoring: none (those rows stay on the page)")
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pick classification rows (c1, c2, ...) or monitoring rows (m1, m2, ...) "
            "and delete only those from MongoDB."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python Crop_classification_model/delete.py\n"
            "  python Crop_classification_model/delete.py --monitoring 2 --yes\n"
            "  python Crop_classification_model/delete.py --classification 6 --yes\n"
            "  python Crop_classification_model/delete.py --classification failed --yes\n"
            "  python Crop_classification_model/delete.py --monitoring all --yes\n"
        ),
    )
    parser.add_argument(
        "--classification",
        default="",
        metavar="ROWS",
        help="Classification rows to delete: 6 or 1,4 or 1-4 or failed or all.",
    )
    parser.add_argument(
        "--monitoring",
        default="",
        metavar="ROWS",
        help="Monitoring rows to delete: 2 or 1,2 or failed or all. Classifications stay.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Delete without the extra yes/no question.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from pymongo import MongoClient
    except ImportError:
        sys.exit(
            "pymongo is not installed. From backend/Credit_assessment run: "
            "pip install pymongo python-dotenv"
        )

    uri, database = load_connection()
    client = MongoClient(uri, serverSelectionTimeoutMS=15000)
    try:
        client.admin.command("ping")
        db = client[database]
        classifications = list(
            db[CLASSIFICATION].find({}, CLASSIFICATION_FIELDS).sort("created_at", -1)
        )
        monitoring = list(
            db[MONITORING].find({}, MONITORING_FIELDS).sort("created_at", -1)
        )

        print(f"Database: {database}")
        print("A name can appear twice. The row number is the one that is deleted.")
        print()
        print_catalog(classifications, monitoring)

        selection = _selection_from_args(args)
        if not selection:
            if not sys.stdin.isatty():
                print("Pass the rows to delete, for example:")
                print("  python Crop_classification_model/delete.py --monitoring 2 --yes")
                print("  python Crop_classification_model/delete.py --classification 6 --yes")
                return 0
            selection = _prompt(classifications, monitoring)
            if not selection:
                print("Nothing deleted.")
                return 0

        try:
            chosen_class, chosen_mon = parse_selection(selection, classifications, monitoring)
        except ValueError as exc:
            sys.exit(str(exc))

        mon_ids = [str(doc["_id"]) for doc in chosen_mon]
        parcel_count = (
            db[PARCELS].count_documents({"job_id": {"$in": mon_ids}}) if mon_ids else 0
        )
        print_plan(classifications, monitoring, chosen_class, chosen_mon, parcel_count)

        if not args.yes:
            if not sys.stdin.isatty():
                print("Add --yes to delete these rows.")
                return 0
            try:
                answer = input("Type yes to delete, or press Enter to cancel: ").strip().lower()
            except EOFError:
                answer = ""
            if answer != "yes":
                print("Nothing deleted.")
                return 0

        if chosen_class:
            result = db[CLASSIFICATION].delete_many(
                {"_id": {"$in": [doc["_id"] for doc in chosen_class]}}
            )
            print(f"Deleted {result.deleted_count} classification run(s).")
        if chosen_mon:
            result = db[MONITORING].delete_many(
                {"_id": {"$in": [doc["_id"] for doc in chosen_mon]}}
            )
            print(f"Deleted {result.deleted_count} monitoring run(s).")
            removed_dirs = delete_raster_dirs(chosen_mon)
            if removed_dirs:
                print(f"Deleted {removed_dirs} monitoring raster folder(s).")
            result = db[PARCELS].delete_many({"job_id": {"$in": mon_ids}})
            print(f"Deleted {result.deleted_count} monitoring parcel row(s).")
        print()
        print("Done. Refresh the page to see the updated list.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        sys.exit("\nNothing deleted.")
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        if "mongodb" in message.lower() or "timed out" in message.lower():
            sys.exit(f"Could not reach MongoDB: {message}")
        raise
