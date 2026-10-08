# Earth Engine service

How this pipeline spends Earth Engine, how that spend is measured, and what the 4 October 2026 pilot implies for the Beed villages.

The history pages do not read the local output folders. A village shows up on the classification or monitoring page only after a completed row is written to MongoDB for the logged-in account (`admin@agristack.gov.in`). The same run also keeps the downloaded arrays and the result files on disk, so a later model or method change does not call Earth Engine again.

## What an EECU is

An EECU is Earth Engine's unit of compute. The bill is **how long that compute ran**, not how many villages or folders were named.

| Unit | Meaning |
|---|---|
| EECU-second | One EECU working for one second |
| EECU-hour | 3,600 EECU-seconds |

The Cloud Monitoring metric is **Earth Engine Cloud Project → Completed EECU-seconds**, aggregation **Sum**. Each bar is the EECU-seconds of requests that finished in that time bucket. The APIs & Services traffic chart (requests per second, errors, latency) is not this number.

The noncommercial quota is **150 EECU-hours per month**, which is **540,000 EECU-seconds**. It resets at midnight Pacific Time on the 1st. For October 2026 that is 12:30 PM IST on 1 October. Past the cap the account slows down. It does not hard-stop, and a slowed call still counts.

## What a village run actually asks Earth Engine to do

Cost follows **pixels × scenes** inside a rectangle. A revenue circle is only the queue and the cluster report. One box around a whole circle would also read the land between the villages, so each village is its own rectangle.

The dissolved village outline is the only geometry that enters delineation. Beed survey plots are dissolved away before this step. Sheet hectares are the insured area. Outline hectares are what Earth Engine reads.

1. **Delineation.** `fetch_boundary_arrays` in `crop_analysis/field_delineation.py` calls `ee.data.computePixels` on a UTM box around the outline (bounds plus 50 m) at 10 m. Watershed then cuts that array into fields. Profiles are off. A second run of the same outline reads `Crop_Monitoring/cache/delineation/<outline>/boundary_v1_<year>_all.npz`.

2. **Classification.** The fused v3 model needs a 10-day Sentinel-1 VV/VH series and a Sentinel-2 reflectance series from 1 May through the as-of date. `fetch_series` reduces those with `reduceRegions`, 200 fields at a time. The series is stored under `Crop_Monitoring/cache/series/`. A changed field polygon is a new key and is the only field downloaded again. Fallow is `ndvi_max < 0.30` with at least 3 optical looks. Crops other than the requested one stay in Others.

3. **Monitoring.** `Crop_Monitoring/src/raster/engine.py` builds a 10 m grid around the field polygons plus 100 m, not around the village file by itself. It downloads Sentinel-2, Landsat 8/9, and Sentinel-1 scenes into `Crop_Monitoring/cache/<grid>/`, plus village weather (CHIRPS, ERA5-Land, IMERG, MODIS LST, NASA POWER) into `weather.json`. A complete date index plus the scene files on disk means a rerun does not list dates and does not download those scenes.

Earth Engine is called again only when the outline changes, the monitoring grid key changes, the as-of date moves past the stored scenes, or a field polygon itself changes. A new model, watershed threshold, or stress formula reads the files already on disk.

## Pilot, 4 October 2026

As-of date 2026-10-04, kharif, watershed, fused v3, Cotton. Villages ran one after the other.

| Village | Outline | Sheet cotton | Fields | Cotton found | EECU |
|---|---:|---:|---:|---:|---|
| Bhat Antarwali (Umapur, Beed) | 501 ha | 95.5 ha | 943 | 69.2 ha | shared with the row below |
| Dhaswadi (Khandali, Latur) | 1,179 ha | 63.1 ha | 1,999 | 70.6 ha | shared with the row above |
| **Both** | **1,680 ha** | | | | **about 3 EECU-seconds** |

The 3 EECU-seconds is the afternoon spike on the Completed EECU-seconds chart (about 2:55–3:20 PM IST). The daily chart that ends at 9:33 AM the same day does not include it. October before that spike was about 0.8 EECU-seconds (2 Oct ~0.55, 3 Oct ~0.26, 4 Oct morning 0.009). The 28 Sep bar is the previous quota month.

```
1,680 ha  →  3 EECU-seconds
1,000 ha  →  3 × 1000 / 1680  ≈  1.8 EECU-seconds  ≈  0.0005 EECU-hours
```

## Beed at that rate

Outline hectares, not sheet hectares. Bhat Antarwali is already done.

| Circle | Crop | Villages left | Outline |
|---|---|---:|---:|
| Umapur | Cotton | 8 | 11,610 ha |
| Mategaon | Cotton | 9 | 10,466 ha |
| Hoal | Soyabean | 10 | 13,338 ha |
| Wida | Soyabean | 11 | 13,245 ha |
| **Remaining** | | **38** | **48,659 ha** |

```
48,659 ha × 1.8 EECU-seconds / 1,000 ha  ≈  87 EECU-seconds  ≈  0.024 EECU-hours
```

October including the pilot and that batch stays under **0.03 EECU-hours**. The cap is 150. The 2 Oct and 3 Oct bars were read from the axis; only 0.009 was a tooltip. Replacing those two with tooltip values will not move this from "fits" to "does not fit" unless the real bars are thousands of times taller than they look.

Villages still run **one at a time**. Overlapping Earth Engine calls on this account return 429, and the retry is billed again.

## Where a finished village is saved

| Store | What | Who reads it |
|---|---|---|
| `Crop_Monitoring/cache/` | Delineation arrays, classification series, scene grids, weather | The next run, so Earth Engine is skipped |
| `Crop_Monitoring/outputs/pilot_2026/<village>/` | `classification.geojson`, `monitoring.json`, map rasters | Disk and the raster API (`result.raster_dir`) |
| `classification_jobs` | The result the classification page draws, owned by `admin@agristack.gov.in` | Classification history |
| `monitoring_jobs` | The result the monitoring page draws, same owner | Monitoring history |
| `monitoring_parcels` | One row per monitored field | Cluster comparison |

`Crop_Monitoring/run_pilot_village.py` writes the disk files and then the three Mongo collections. `--publish-only` loads a finished folder into Mongo and does not call Earth Engine.

The two pilot villages are already in Mongo:

| Page | Village | Job |
|---|---|---|
| Classification | Bhat Antarwali | `6ac2248fd04bbd1215583561` |
| Classification | Dhaswadi | `6ac2249bd04bbd1215583913` |
| Monitoring | Bhat Antarwali | `6ac22490d04bbd1215583562` |
| Monitoring | Dhaswadi | `6ac2249ed04bbd1215583914` |

Open the history list while logged in as `admin@agristack.gov.in`. An old `?job=` link points at a row that was deleted and will not show these two.
