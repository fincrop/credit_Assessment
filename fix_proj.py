"""
PROJ Database Fix for Windows
==============================
This must be imported BEFORE any rasterio/GDAL imports
"""

import os
import sys
from pathlib import Path

def configure_proj():
    """Configure PROJ_LIB environment variable"""
    try:
        # Method 1: Use pyproj's data directory
        import pyproj
        proj_data_dir = pyproj.datadir.get_data_dir()
        
        # Verify proj.db exists
        proj_db_path = Path(proj_data_dir) / 'proj.db'
        
        if proj_db_path.exists():
            os.environ['PROJ_LIB'] = str(proj_data_dir)
            os.environ['PROJ_DATA'] = str(proj_data_dir)  # Also set PROJ_DATA
            print(f"[OK] PROJ configured: {proj_data_dir}")
            return True
        else:
            print(f"⚠️  proj.db not found at: {proj_db_path}")
            
    except Exception as e:
        print(f"⚠️  Could not configure PROJ: {e}")
    
    # Method 2: Search in venv
    venv_paths = [
        Path(sys.prefix) / 'Lib' / 'site-packages' / 'pyproj' / 'proj_dir' / 'share' / 'proj',
        Path(sys.prefix) / 'share' / 'proj',
        Path(sys.prefix) / 'Library' / 'share' / 'proj',
    ]
    
    for proj_path in venv_paths:
        proj_db = proj_path / 'proj.db'
        if proj_db.exists():
            os.environ['PROJ_LIB'] = str(proj_path)
            os.environ['PROJ_DATA'] = str(proj_path)
            print(f"[OK] PROJ configured (fallback): {proj_path}")
            return True
    
    print("❌ Could not find proj.db - reinstallation needed")
    return False

def configure_gdal():
    """Configure GDAL to reduce warnings"""
    os.environ['CPL_LOG'] = 'OFF'
    os.environ['CPL_DEBUG'] = 'OFF'
    os.environ['GDAL_DATA'] = os.path.join(sys.prefix, 'Lib', 'site-packages', 'rasterio', 'gdal_data')

def setup_environment():
    """Setup complete geo environment"""
    print("Configuring geospatial environment...")
    
    # Configure PROJ first
    proj_ok = configure_proj()
    
    # Configure GDAL
    configure_gdal()
    
    # Suppress Python warnings
    import warnings
    warnings.filterwarnings('ignore', category=RuntimeWarning, module='rasterio')
    
    if proj_ok:
        print("[OK] Environment ready\n")
    else:
        print("⚠️  Environment configured with warnings\n")
    
    return proj_ok

if __name__ == "__main__":
    setup_environment()