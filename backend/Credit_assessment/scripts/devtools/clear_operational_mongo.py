"""
Clear agri-credit operational Mongo data for a clean multi-farm baseline.

PRESERVES (do not wipe):
  - users              (login accounts)
  - lgd_villages       (location reference)
  - lgd_talukas        (location reference)
  - index_versions     (index_v5 weight provenance)

DELETES (all documents in each collection):
  - farm_info
  - farmer_farms
  - credit_assessments
  - jobs
  - satellite_stats_cache
  - weather_power_cache
  - feature_store
  - cohort_stats
  - webhook_responses
  - webhook_farmers_responses
  - webhook_kdss_responses
  - login_rate_limits

Usage (from backend/Credit_assessment):

  # dry-run (counts only)
  python scripts/devtools/clear_operational_mongo.py

  # actually delete
  python scripts/devtools/clear_operational_mongo.py --confirm

Loads MONGODB_URI / MONGODB_DATABASE from backend .env or environment.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow running from Credit_assessment root
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:
    pass


DELETE_COLLECTIONS = [
    "farm_info",
    "farmer_farms",
    "credit_assessments",
    "jobs",
    "satellite_stats_cache",
    "weather_power_cache",
    "feature_store",
    "cohort_stats",
    "webhook_responses",
    "webhook_farmers_responses",
    "webhook_kdss_responses",
    "login_rate_limits",
]

PRESERVE_COLLECTIONS = [
    "users",
    "lgd_villages",
    "lgd_talukas",
    "index_versions",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear operational Mongo collections")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually delete documents (without this flag: dry-run counts only)",
    )
    args = parser.parse_args()

    uri = (os.environ.get("MONGODB_URI") or "").strip()
    db_name = (
        os.environ.get("MONGODB_DATABASE")
        or os.environ.get("MONGODB_DB")
        or "agristack"
    ).strip()

    if not uri:
        print("ERROR: MONGODB_URI is not set (check backend/Credit_assessment/.env)")
        return 1

    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[db_name]

    print(f"Database: {db_name}")
    print(f"Mode: {'DELETE' if args.confirm else 'DRY-RUN (pass --confirm to delete)'}")
    print("-" * 60)

    total = 0
    for name in DELETE_COLLECTIONS:
        col = db[name]
        n = col.count_documents({})
        total += n
        if args.confirm and n > 0:
            result = col.delete_many({})
            print(f"  deleted {result.deleted_count:6d}  {name}")
        else:
            print(f"  {'would delete' if n else 'empty      '}: {n:6d}  {name}")

    print("-" * 60)
    print(f"Total docs {'deleted' if args.confirm else 'to delete'}: {total}")
    print()
    print("Preserved:")
    for name in PRESERVE_COLLECTIONS:
        n = db[name].count_documents({})
        print(f"  {n:6d}  {name}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
