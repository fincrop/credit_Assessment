#!/usr/bin/env python3
"""
Script to clear satellite data cache for a specific farmer or all farmers
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mongodb_helper import MongoDBHelper

def clear_satellite_cache(farmer_id=None):
    """Clear satellite cache for specific farmer or all farmers"""
    
    # Initialize MongoDB connection
    db = MongoDBHelper()
    
    if farmer_id:
        print(f"Clearing satellite cache for farmer {farmer_id}...")
        try:
            result = db.delete_satellite_stats_cache(farmer_id)
            print(f"Deleted {result.deleted_count} cache entries")
        except Exception as e:
            print(f"Error clearing cache for farmer {farmer_id}: {e}")
            # Try manual deletion
            try:
                result = db.satellite_stats_cache.delete_many({'farmer_id': farmer_id})
                print(f"Manually deleted {result.deleted_count} cache entries")
            except Exception as e2:
                print(f"Manual deletion also failed: {e2}")
    else:
        print("Clearing all satellite cache...")
        try:
            result = db.delete_satellite_stats_cache()
            print(f"Deleted {result.deleted_count} cache entries")
        except Exception as e:
            print(f"Error clearing all cache: {e}")
            # Try manual deletion
            try:
                result = db.satellite_stats_cache.delete_many({})
                print(f"Manually deleted {result.deleted_count} cache entries")
            except Exception as e2:
                print(f"Manual deletion also failed: {e2}")
    
    print("Cache clearing completed!")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        farmer_id = sys.argv[1]
        clear_satellite_cache(farmer_id)
    else:
        clear_satellite_cache()
