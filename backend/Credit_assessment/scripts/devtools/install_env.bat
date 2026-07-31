@echo off
echo ========================================
echo STEP 2: Installing Fresh Environment
echo ========================================
echo.
echo This will create a new conda environment with all dependencies
echo Installation time: 5-15 minutes depending on internet speed
echo.
echo Press any key to continue or Ctrl+C to cancel...
pause > nul

cd /d "%~dp0..\.."

echo.
echo ========================================
echo Step 1/5: Creating environment from environment.yml...
echo ========================================
conda env create -f environment.yml -p .\.conda

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to create environment!
    echo Try running: conda clean --all -y
    echo Then run this script again
    pause
    exit /b 1
)

echo.
echo ========================================
echo Step 2/5: Activating environment...
echo ========================================
call conda activate .\.conda

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Failed to activate environment!
    pause
    exit /b 1
)

echo.
echo ========================================
echo Step 3/5: Verifying core packages...
echo ========================================
echo.
echo Checking NumPy (must be 1.24.x)...
python -c "import numpy; print(f'  ✓ NumPy: {numpy.__version__}')" || goto error

echo Checking SciPy...
python -c "import scipy; print('  ✓ SciPy: OK')" || goto error

echo Checking Pandas...
python -c "import pandas; print('  ✓ Pandas: OK')" || goto error

echo Checking Scikit-learn...
python -c "import sklearn; print('  ✓ Scikit-learn: OK')" || goto error

echo.
echo ========================================
echo Step 4/5: Verifying geospatial packages...
echo ========================================
echo.
echo Checking Rasterio...
python -c "import rasterio; print('  ✓ Rasterio: OK')" || goto error

echo Checking Shapely...
python -c "import shapely; print('  ✓ Shapely: OK')" || goto error

echo Checking GeoPandas...
python -c "import geopandas; print('  ✓ GeoPandas: OK')" || goto error

echo Checking GDAL...
python -c "import osgeo.gdal; print('  ✓ GDAL: OK')" || goto error

echo.
echo ========================================
echo Step 5/5: Verifying ML and database packages...
echo ========================================
echo.
echo Checking XGBoost...
python -c "import xgboost; print('  ✓ XGBoost: OK')" || goto error

echo Checking PyMongo...
python -c "import pymongo; print('  ✓ PyMongo: OK')" || goto error

echo Checking pystac-client...
python -c "import pystac_client; print('  ✓ pystac-client: OK')" || goto error

echo Checking planetary-computer...
python -c "import planetary_computer; print('  ✓ planetary-computer: OK')" || goto error

echo.
echo ========================================
echo Testing Pipeline Components...
echo ========================================
echo.
echo Testing Satellite Collector...
python -c "from data_acquisition.satellite_collector import SatelliteDataCollector; print('  ✓ Satellite Collector: OK')" || goto error

echo Testing Weather Analyzer...
python -c "from data_acquisition.weather_analyzer import WeatherAnalyzer; print('  ✓ Weather Analyzer: OK')" || goto error

echo Testing Crop Detector...
python -c "from crop_analysis.crop_detector import CropDetector; print('  ✓ Crop Detector: OK')" || goto error

echo Testing Credit Scorer...
python -c "from assessment.credit_scorer import CreditScorer; print('  ✓ Credit Scorer: OK')" || goto error

echo.
echo ========================================
echo SUCCESS! Installation Complete!
echo ========================================
echo.
echo Environment created at: .\.conda
echo.
echo To activate the environment:
echo     conda activate .\.conda
echo.
echo To test the full pipeline:
echo     python main.py --farmer-id potato_05 --mode ENHANCED
echo.
echo To deactivate:
echo     conda deactivate
echo.
pause
exit /b 0

:error
echo.
echo ========================================
echo ERROR: Installation verification failed!
echo ========================================
echo.
echo One or more packages failed to import.
echo Check the error messages above.
echo.
echo Troubleshooting:
echo 1. Make sure you have a stable internet connection
echo 2. Try: conda clean --all -y
echo 3. Delete .conda folder and re-run clean_env.bat
echo 4. Re-run install_env.bat
echo.
pause
exit /b 1
