#!/usr/bin/env python3
"""
Clear the satellite stats cache for one farmer, or for all farmers.

Usage:
    python scripts/devtools/clear_satellite_cache.py            # all entries
    python scripts/devtools/clear_satellite_cache.py FARMER_001 # one farmer

Run from the package root (backend/Credit_assessment).
"""

import os
import sys
from pathlib import Path

# Package root is three levels up from scripts/devtools/
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# Load .env, as the other devtools do. Without this the script died with
# "MONGODB_URI is not configured" — and because it was chained with `;` before
# the next command, the failure scrolled past and the cache was NOT cleared,
# so a subsequent re-run silently reused the stale satellite series it was
# supposed to invalidate.
try:
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
except Exception:  # pragma: no cover
    pass

from mongodb_helper import MongoDBHelper  # noqa: E402


def clear_satellite_cache(farmer_id=None):
    """Clear satellite cache for a specific farmer, or all farmers."""
    target = farmer_id or "ALL farmers"
    print(f"Clearing satellite cache for {target}...")

    db = MongoDBHelper()
    try:
        deleted = db.delete_satellite_stats_cache(farmer_id)
        print(f"Deleted {deleted} cache entries.")
        if farmer_id and deleted == 0:
            print(
                f"  (No entries found for '{farmer_id}'. The cache is keyed by "
                f"an opaque hash and stores the farmer id under "
                f"metadata.farmer_id — check the id is exact.)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    clear_satellite_cache(sys.argv[1] if len(sys.argv) > 1 else None)
