"""Raster monitoring engine (accuracy plan Track C, §5).

Real 10 m pixel stacks per village tile replace the 1–4 sample points per farm
of the point engine. Every field number is computed from the same pixels the
published maps show:

    grid        village tile grid and per-field interior / edge masks
    fetch       Earth Engine pulls: Sentinel-2, Landsat 8/9, Sentinel-1 per scene
    indices     spectral indices; Landsat cross-calibrated to Sentinel-2
    smooth      weighted Whittaker smoothing with an upper envelope
    phenology   bare-soil-first green-up, peak, end of season
    reference   crop reference curves and curve-fit crop checks
    sowing      monsoon onset prior + optical + radar posterior
    stress      sowing-cohort anomalies, persistence, type, waterlogging
    yield_index district-anchored relative yield
    products    COG rasters, PNG overlays, product index
    engine      one village run -> field records + raster products
"""

import src._bootstrap  # noqa: F401  (sys.path, and PROJ/GDAL data before rasterio loads)
