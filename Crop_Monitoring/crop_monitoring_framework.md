# Crop Monitoring Framework

**Status:** final design for implementation  
**Builds on:** classified parcels from `Crop_classification_model` and the product list in `crop_monitoring_full.md`  
**Scope:** present-season monitoring for one farm in India, using Earth Engine plus the weather sources already wired in this repo  
**Out of this version:** pest and disease ranking, soil-test products (phosphorus, potassium, salinity, organic carbon, pH)

The wireframe says what a finished run should publish. This file says how to produce it when clouds, mixed sowing, coarse weather grids, and thin local history are the normal case.

---

## 1. What a run publishes

Monitoring starts from a classified parcel and follows the open season until harvest. Each update publishes:

| Product | What it is |
|---|---|
| Zones | One or more management zones inside the boundary, each with its own sowing cohort |
| Sowing | A date distribution (early, most likely, late), the sensors that supported it, and a confidence |
| Progress | Phenology progress from emergence to harvest, plus a stage name |
| Harvest | A detected crash date, or a remaining window while the canopy is still up |
| Stress | Type and share of the zone in mild, moderate, or severe stress, scored inside that zone |
| Canopy state | Cover, water status, and biomass, each with an uncertainty that grows through cloud |
| Yield | Tons per hectare as an ensemble, with a band that reflects disagreement and missing optical views |
| Nitrogen status | A canopy red-edge score used to adjust yield. It is a satellite proxy |

Credit scoring is unchanged. `CropPerformanceAnalyzer` stays the historical vigor proxy for lending. This framework does not replace it.

Typed products (stage table, yield envelope, nitrogen curve) require a confident crop label and a region the classifier is allowed to score. Below that gate the run still publishes zones, canopy state, and weather, and it withholds the crop-specific yield envelope.

---

## 2. Handoff from classification

| Input | Where it comes from | Role |
|---|---|---|
| Field polygon | Delineation or the cadastral boundary, with the −10 m core inset | Pixel work happens on the core |
| Crop and confidence | Tier-2 classifier | Selects the crop prior |
| Agro-ecoregion | Existing region boxes | One input to the climate-analog pool, not the only reference |
| Season | `crop_calendar.py` | Which cycle is the present season, and the sowing prior window |
| Cycle hint | `CropCycleDetector` | A hint for sowing and harvest, checked against the multi-sensor estimate |
| Duration prior | `CropGrowthCurves.CROP_DURATIONS` min / typical / max | A soft check on observed length. It is not the clock |

The present season is the cycle that contains today and whose green peak falls in that crop’s calendar window. Perennial crops (banana, sugarcane, grapes) use green-up to green-up, or the previous harvest for sugarcane ratoon.

---

## 3. Data sources

Field products use sensors that can see a parcel of about 0.5–2 ha. Coarse sensors are used for timing, weather spread, and priors. They never become a fake 10 m map.

### Field sensors

| Source | Use |
|---|---|
| `COPERNICUS/S2_SR_HARMONIZED` | Canopy, water, senescence, nitrogen. Mask: QA60 + SCL + `GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED` at `cs_cdf ≥ 0.60` |
| `COPERNICUS/S1_GRD` | Tillage, emergence, and canopy structure through cloud. IW, dual-pol VV/VH. Ascending and descending are separate series |
| `LANDSAT/LC08/C02/T1_L2` and `LC09` | Extra clear looks when Sentinel-2 is cloudy. `ST_B10` land-surface temperature where the field supports 30 m pixels (about 1 ha and larger) |
| `GOOGLE/DYNAMICWORLD/V1` | Mask trees, water, built-up, and bare non-crop inside the boundary |

Indices, using the formulas already in `satellite_collector.py`:

| Family | Indices |
|---|---|
| Cover | NDVI, EVI, MSAVI2, kNDVI, NIRv |
| Chlorophyll | NDRE, GCVI, CIre = (B7 / B5) − 1 |
| Water | NDMI, LSWI, NDWI, MNDWI |
| Senescence and soil | PSRI, BSI, NBR = (B8 − B12) / (B8 + B12) |
| Radar | VV, VH, RVI = 4 · VH / (VV + VH), per orbit |

### Weather and land surface

These are reduced over a buffer, not read at the centroid alone. Section 7 is the method.

| Source | Use | Grid |
|---|---|---|
| `UCSB-CHG/CHIRPS/DAILY` | Seasonal rainfall amount | ~5 km |
| `NASA/GPM_L3/IMERG_V07` | Storm timing and spatial spread. Summed to daily | ~10 km |
| IMD 0.25° rain and 1° temperature via `imdlib`, when `WEATHER_IMD_DATA_DIR` is present | India gauge-analysis rain and temperature | 0.25° / 1° |
| `ECMWF/ERA5_LAND/DAILY_AGGR` | Temperature, dewpoint, vapour-pressure deficit, shortwave radiation, potential evaporation, soil moisture 0–7 cm, soil temperature, skin temperature | ~9 km |
| NASA POWER | Fill when ERA5 or CHIRPS is empty. Already used by classification | Point, ~50 km native |
| Open-Meteo or IMD forecast | Next 14 days, used only to extend the harvest window and the biomass step | Point |

### Landscape timing (priors only)

| Source | Use |
|---|---|
| `MODIS/061/MOD11A1` daytime LST, or VIIRS LST | When the surrounding cropland cools or wets, as a sowing corroboration during weeks with no clear 10 m scene |
| `MODIS/061/MOD09GQ` red and NIR | A 250 m greenness prior for the process model in long cloud gaps. It does not enter field maps |

A 250 m or 1 km pixel is larger than a typical parcel. It can move the timing prior. It cannot label a pixel inside the farm.

---

## 4. Pipeline

```text
Classified parcel
        │
        ▼
0  Priors        crop, season window, duration range, climate-analog pool
1  Zones         split mixed cover and mixed sowing dates
2  Observe       optical, radar-by-orbit, buffer weather, land-surface timing
3  Sowing        fuse rain, radar, optical, and land surface into a date distribution
4  State         daily canopy / water / biomass; satellites correct it when they see the field
5  Progress      emergence, peak, senescence on the fused state; stage from progress
6  Stress        inside each zone, and only when uncertainty allows
7  Yield         three estimators on phenology progress; duration is checked, not assumed
8  Publish       zones, distributions, uncertainty; skip dates already stored
```

Pest ranking is not a stage in this version. It needs an agent library and field confirmation, and it waits until canopy, weather, and stage are stable.

---

## 5. Zones: mixed boundaries and staggered sowing

A polygon is not scored as one canopy. Mixed crops and two sowing dates in one boundary are split first. Stress, stage, and yield then run per zone. A harvested patch next to a flowering patch is two results, not one “nutrient deficit”.

### 5.1 Remove pixels that are not this season’s crop

On the core polygon, mask pixels that are:

- Dynamic World built, water, trees, or flooded vegetation outside a rice stage that expects water
- Green all year (NDVI stays high through the pre-sowing window): bund trees, orchard edge, sugarcane left in an annual field
- Permanently bare (BSI high and NDVI flat across the whole window)

Remaining pixels are the crop mask.

### 5.2 Split sowing cohorts

For every crop pixel, estimate a green-up date:

- Sentinel-1 RVI rise on a single orbit (this exists in the monsoon)
- Sentinel-2 NDVI rise on clear dates, which replaces the radar date when both exist

Build a histogram of green-up dates. Split when both are true:

- Two modes are at least 21 days apart
- Each mode holds at least 15% of crop pixels and at least 12 pixels at 10 m

The split is spatial. Pixels take the mode they fall in, then small islands are absorbed into the neighbouring cohort. A field can also split on peak date with the same rule, which catches two crops that emerged together and peaked apart.

Each zone keeps its own sowing distribution, progress, stress, and yield. The classifier’s crop name is applied to the dominant zone. Other zones are cohorts: same crop at another date, or an unspecified second crop. They are not forced onto the dominant stage table. If a secondary zone’s shape is far from the declared crop, it is labelled `unspecified` and scored with the relative method only.

### 5.3 Field rollup

The farm document lists zones with area shares. Field-level stress is the area-weighted stress of zones that are in the same progress band. Zones in different bands are reported separately. The rollup never averages them into one stress type.

---

## 6. Sowing without a clear optical scene

Kharif sowing sits inside the monsoon. A method that waits for Sentinel-2 or Landsat will miss the date in the weeks that matter. Sowing is a fusion of every independent cue, and the published result is a distribution.

### 6.1 Cues

Each cue emits a candidate date and a width.

| Cue | What counts as the event | Typical width |
|---|---|---|
| Calendar prior | The sowing window in `crop_calendar.py` for that crop and season | The window itself |
| Rain trigger | Inside the window, the first 3-day rainfall of at least 25 mm after a drier spell, using the buffer rainfall in section 7. ERA5 soil moisture 0–7 cm should rise in the same week | ±7 days |
| Radar tillage | On one orbit, VV departs from the pre-window baseline by more than the orbit’s own noise | ±6 days |
| Radar emergence | On that same orbit, RVI or VH/VV rises and stays up for two consecutive looks. Orbits are never mixed in one test | ± one repeat (about 12 days on that orbit; the other orbit can confirm) |
| Optical emergence | Clear Sentinel-2 or Landsat: NDVI crosses the crop emergence level and BSI falls, on at least 12 core pixels | ±5 days |
| Land surface | Daytime land-surface temperature minus ERA5 air temperature drops versus the bare-soil baseline, from Landsat `ST_B10` on large fields or from MODIS/VIIRS LST as a landscape cue. ERA5 skin temperature and soil temperature level 1 narrowing over the same days supports it | ±8 days |

Radar emergence is then stepped back by the crop’s sowing-to-emergence lag (about 7–14 days, stored per crop). Optical emergence uses the same lag. The rain trigger is already a sowing opportunity, so it is not lagged again.

Rainfed kharif (soybean, cotton, maize, bajra, tur) leans on the rain trigger plus radar. Irrigated rabi (wheat, mustard, gram, potato) leans on radar tillage and optical or land-surface cooling, because sowing often follows irrigation rather than rain. Rice uses standing-water onset (MNDWI or LSWI jump, or a VV drop) as an extra emergence cue for transplanted fields.

### 6.2 Combining cues

The calendar window is the prior. Each cue that fired adds a likelihood around its date. The posterior is that prior times the likelihoods.

- Published sowing date = posterior median
- Early and late = 10th and 90th percentile
- Confidence is high when at least two families agree (for example radar and rain, or optical and radar) and the posterior is narrow
- Confidence is low when only the calendar and a single weak cue fired; the document then leads with the window
- If nothing fires inside the window, sowing stays unknown and crop-specific yield is withheld. The cycle detector’s date may be shown as a hint with source `cycle_hint`

A clear optical emergence later in the season is allowed to tighten the posterior. It does not move sowing onto a cloudy filled date.

### 6.3 Why both radar orbits

Sentinel-1A repeats a given look about every 12 days. Ascending and descending are different incidence angles, so they are stored as two series. Emergence is tested inside one series, and the other series confirms it. Using both looks roughly doubles the chance of a sowing observation during cloud, without pretending the two geometries are the same measurement.

---

## 7. Weather over a buffer, and irrigation

ERA5-Land is about 9 km, CHIRPS about 5 km, IMERG about 10 km, IMD rain 0.25°. The centroid and a small polygon often fall in one cell, and a storm or an irrigation turn will not match that cell. The field uses a spatial sample and a satellite wetness check.

### 7.1 Rain and temperature as a spread

For each day, take every grid cell that intersects the field expanded by a buffer:

| Variable | Buffer | Published value |
|---|---|---|
| CHIRPS and IMD rain | 10 km | Median, plus the 10th and 90th percentile across cells |
| IMERG rain | 10 km | Same spread, used for the day a storm arrived |
| ERA5 temperature, radiation, evaporation, humidity, soil moisture | 15 km | Area-weighted mean, plus the spread |

Vapour-pressure deficit comes from ERA5 temperature and dewpoint on those cells. Growing degree days use the area-weighted mean temperature and the crop base.

The water balance uses the median rain. If the 90th percentile is far above the median, the day is flagged `uneven_rain`. Yield and stress then carry both a wetter and a drier scenario instead of one millimetre value. IMD replaces CHIRPS as the seasonal total when the local IMD files are loaded. IMERG still supplies timing.

### 7.2 Irrigation and rain the grid missed

Satellite wetness is the field observation. The grid is the neighbourhood.

An irrigation (or a local storm the grid missed) is flagged when all of these hold:

- Field NDMI or LSWI rises, or Sentinel-1 VV drops, on a clear or radar date
- Buffer rainfall over the surrounding five days is below 5 mm even at the 90th percentile
- A ring of other cropland from 0.5 km to 2 km around the field, masked with Dynamic World, does not show the same wetness jump

The flag is an event, not a millimetre amount. The water state is reset toward “recently wetted” and the dry-down restarts. Rice ponding is read directly from MNDWI and LSWI and is the water status for that zone while the stage expects standing water.

This stops a dry centroid from being read as drought on a field that was irrigated, and it stops a single wet cell from being read as rain on a field that stayed dry.

---

## 8. Continuity through cloud

Cloudy weeks will often have radar and weather only. The framework keeps a daily state and lets whichever sensor is available correct it. It does not invent an NDVI map for a cloudy day.

### 8.1 The state

Per zone, every day:

| State | Meaning |
|---|---|
| Cover | Fraction of the zone with green canopy, 0 to 1 |
| Water | Canopy and surface wetness relative to the stage |
| Biomass | Above-ground dry biomass, kg/ha, accumulated |
| Uncertainty | Grows every day without an optical view, shrinks when a sensor updates |

### 8.2 Daily step from weather

Between images the state moves with weather:

```text
biomass gain = RUE × PAR × fAPAR(cover) × temperature scalar × water scalar
cover        = cover + growth from that gain, limited by the crop’s maximum cover
water        = water − evaporation + rain scenario + irrigation event
```

PAR is about half of ERA5 shortwave over the buffer. The temperature scalar uses the crop base and an optimum. RUE is a crop constant (maize higher, cotton and legumes lower, sugarcane on a long cycle). Unobserved days still advance biomass, and uncertainty rises.

### 8.3 Corrections

| Observation | What it updates |
|---|---|
| Clear Sentinel-2 | Cover, chlorophyll, water. Strong correction. Uncertainty drops |
| Clear Landsat with enough 30 m pixels | Cover and water. Medium correction |
| Sentinel-1, either orbit | Cover and structure. Weak on chlorophyll. Medium drop in uncertainty |
| Buffer weather only | The daily step. Uncertainty keeps rising |
| MODIS/VIIRS greenness | A landscape prior: if the whole 250 m neighbourhood is still rising, the process model is allowed to keep growing cover. It does not overwrite the zone value |

Chlorophyll (NDRE, CIre) is updated only from clear Sentinel-2, because red-edge is 20 m and Landsat does not carry it. Nitrogen status is withheld while uncertainty is high.

### 8.4 What gets published on a cloudy date

A weather-propagated day is stored as `inferred`, with the uncertainty. Stress and the yield point estimate are published on that day only when uncertainty is still inside the crop’s limit (a run of cloudy days on the order of two weeks, tightened if radar also disagreed with the process model). Past that limit the document keeps the state and the band, and it marks condition as unavailable until the next radar or optical correction.

Filled 10 m index maps are not written for inferred days.

---

## 9. Progress, duration, and harvest

Stage and yield follow observed progress. A fixed duration from `CROP_DURATIONS` does not decide the stage.

### 9.1 Milestones on the fused cover curve

| Milestone | Rule |
|---|---|
| Emergence | Cover stays above the crop emergence level for two updates (radar or optical) |
| Peak | Maximum of smoothed cover after emergence |
| Senescence | After the peak, cover falls about 15% and, when a clear scene exists, PSRI or BSI rises. Radar RVI decline is enough when optical is absent |
| Harvest | A later crash through the crop’s residue level, or the end of senescence |

Progress τ is 0 at emergence and 1 at harvest. Peak greenness sits at a crop-specific τ (about 0.65 for cereals, earlier for crops whose yield organ fills after maximum greenness, such as cotton bolls, potato tubers, and onion bulbs). Between milestones, τ is interpolated. The stage name is looked up from τ.

Days after sowing are still reported, from the sowing median. They are context. A late sowing does not push a peaked canopy back into “vegetative” because a template said day 40.

### 9.2 Duration is a check

Observed length is senescence minus emergence, or harvest minus sowing when both ends exist. Compare it with min and max days for that crop.

- Inside the range: normal
- Outside the range: flag `duration_outlier`, widen the yield band, and review the zone for a wrong crop or a mixed remnant

The template is not stretched until the outlier fits. A long-duration prior is not applied to a short canopy, and a short-duration prior is not stretched to cover a long one. The crop name on the parcel is the one classification assigned.

Each classified crop keeps its own prior: duration, stage names, and the harvest months that follow from its sowing window plus that duration. Two crops in one cluster are never scored on one calendar. When the classified crop is cotton, successive cover drops after boll set are stored as picks. For every other annual crop, harvest is the canopy crash, or a remaining window while the canopy is still up. An observed harvest outside that crop's own months is stored and flagged. The date is not moved to fit the calendar.

### 9.3 Harvest splits the boundary the same way sowing does

Sowing cohorts are split first. Inside one cohort, pixels can still be picked or cut on different days. Each pixel gets a crash date: the first clear look after its own peak where cover falls to residue. The same 21-day, 12-pixel, 15% rule then cuts the cohort.

- One crash, or no crash yet: the classified boundary stays one parcel. Harvest is a date or a window.
- Two crash dates far enough apart: the boundary is replaced by one polygon per harvest. Each keeps the shared sowing date and gets its own harvest, stress, indices, and yield.
- A standing patch next to an already harvested patch is its own parcel. Bare soil after the crash is not scored as stress on the standing crop.

The published geometry is the updated parcel, not only the polygon that came from classification. `split_reason` is `boundary`, `sowing`, or `harvest`.

### 9.4 Harvest as a window

When a crash is observed, that date is the harvest and yield freezes. Bare soil after that date is not scored.

When the crash is not yet observed, the harvest window is the set of dates on which τ can reach the harvest milestone given:

- current cover and biomass
- the temperature range from the 14-day forecast plus the local climatology after that
- the crop’s min and max remaining days

The window shrinks when a new clear or radar scene arrives. Yield before harvest is the ensemble projected across that window, not a single calendar day.

Perennials do not use an annual crash. Banana is scored on cover stability and water. Sugarcane ratoon counts from the previous harvest. Grapes count from the pruning green-up.

---

## 10. Stress inside the zone

Stress uses pixels of one zone on one date. The healthy reference is the upper part of that zone’s own cover distribution on that date, once a real canopy exists.

| Deviation from the zone’s healthy pixels | Class |
|---|---|
| Within 20% | Healthy |
| 20–40% | Mild |
| 40–60% | Moderate |
| Above 60% | Severe |

Stressed area is mild + moderate + severe. Below about 1% the zone stays Healthy.

The type comes from which family moved against that zone’s own recent clear scenes and against the peer band in section 12:

| Type | Evidence |
|---|---|
| Nutrient deficit | NDRE or CIre low while NDMI stays in band, on a clear Sentinel-2 date |
| Water deficit | NDMI and LSWI low, and the irrigation test in section 7 did not fire |
| Tissue damage | PSRI, NBR, or BSI move toward senescence or bare soil before the senescence milestone, optionally with a sharp same-orbit VV change |
| Sub-optimal growth | Cover still near bare soil late in establishment, or a shortfall that does not lock to one family |
| Heat, beside the biological type | Buffer maximum temperature and vapour-pressure deficit together. Landsat surface temperature is added on large fields when it agrees with air temperature |

Rice tillering with high MNDWI stays out of the water-deficit class. Maturity damps the score, because senescence is expected. Inferred days past the uncertainty limit do not receive a stress type.

A second sowing cohort is not stress in the first cohort. That case was removed in section 5.

---

## 11. Yield

Yield does not wait for a soil test, and it does not wait for a single harvest date. Nitrogen is a modifier. Phosphorus, potassium, and soil chemical priors are omitted in this version.

### 11.1 Three estimators

All three run on τ, on the fused state, and on the sowing distribution.

**Peer yield.** Compare this zone’s cover integral from emergence through the current τ with other zones of the same crop and season in the same district (section 12). The zone’s percentile in that peer set is mapped onto the district’s published yield distribution (Directorate of Economics and Statistics, stored per crop and district). This is the estimator that still works when the local training set is thin.

**Biomass × harvest index.** Biomass is the accumulated state. Harvest index is a crop value that rises with τ and reaches the crop harvest index at τ = 1. Projecting to harvest integrates biomass forward across the harvest window in section 9.3, then multiplies by harvest index.

**Peak canopy.** Peak NIRv (or peak cover, if the peak fell in cloud and only radar saw it) is mapped through a crop curve whose level has been scaled to this district’s recent clear scenes. The curve shape is crop-wide. The level is local.

### 11.2 The number that is published

```text
point  = median of the three estimators
band   = the spread between them
         + extra width from the sowing early/late range
         + extra width from days since the last optical correction
         + extra width when duration_outlier is set
```

Before the peak, the point is a potential and the band is wide. After the peak, the band tightens. At a detected harvest, the value freezes. A later bare-soil scene does not pull it down.

Retention adjusts the biomass and peak-canopy estimators before the median:

```text
retention = 1 − water-stress integral − heat during the critical τ band
              − uneven cover inside the zone − nitrogen shortfall
```

Nitrogen shortfall comes from NDRE and CIre versus the peer band at that τ, on clear dates only. It can reduce retention. It is not published as a fertiliser rate.

If the three estimators disagree by a wide margin, the document shows the median and the band, and confidence is low. Agreement is what makes the band narrow. There is no fourth source of truth inside the season.

### 11.3 District baseline

Absolute tons per hectare equal the peer percentile placed on the district yield distribution for that crop and season. Where the district statistic is missing, use the state statistic and add width. The number is a monitoring projection scaled to official yields. Harvest weights, when they exist later, are a calibration set, not a prerequisite for publishing the projection.

---

## 12. References when the crop–region cell is thin

A borrowed NDVI curve from another agro-ecoregion is the wrong fix. Humid and dry districts do not share an absolute NDVI level. The framework uses a hierarchy and keeps absolute levels local.

### 12.1 Order of evidence

For a zone of a given crop and season, build the expected cover, NDRE, and NDMI band in this order. Stop adding weaker levels once the stronger level has enough samples.

| Order | Pool | What it is allowed to set |
|---|---|---|
| 1 | This field, earlier seasons of the same crop | Level and timing |
| 2 | Other zones in the same district and season, classified as this crop at high confidence, including farms classified this year | Level and timing. This is the operational peer set, and it is much larger than the 9,000 training parcels |
| 3 | Climate analogs: districts with a similar seasonal rainfall, rainy-day count, mean temperature, and elevation from ERA5 and CHIRPS | Timing and shape. Levels are rescaled to pool 2 or to this season’s own bare-soil and peak |
| 4 | Crop-wide India shape from all training parcels | Shape only: where peak τ sits, how steep emergence is. The NDVI level is taken from this field’s bare soil and from the first clear peak |

Pool 2 is the default. The training GeoPackage is how the classifier was built. It is not the only fields monitoring is allowed to see.

### 12.2 Climate analogs

Analogs are nearest neighbours in climate space, not the next box on the agro-ecoregion map. A sparse Deccan crop borrows from districts with the same monsoon length and temperature, which may sit outside that box, and it rescales the level. The document records which pool was used.

### 12.3 Self-scale in a new district

The first clear scenes set bare-soil NDVI and, later, the peak. Stress in section 10 is intra-zone, so it does not need a historical curve at all. Peer yield needs pool 2. Until about 15 peer zones exist in the district this season, yield uses pool 3 for shape, the state yield statistic for the level, and a wider band. The band is an honest width on a real estimator, not a copied foreign curve.

### 12.4 What “enough” means

| Pool | Minimum before it can set the level |
|---|---|
| This field, prior seasons | 1 completed season of the same crop |
| District peers this season | 15 zones |
| Climate analogs | 30 zones after rescaling |

Below those counts the next row takes over. Every published yield carries `reference_pool` so a thin district is visible.

---

## 13. Nitrogen

On clear Sentinel-2 dates, after emergence and before senescence, the zone median of NDRE and CIre is compared with the peer band at the same τ. The score is a status against that band. Early bare soil is clamped so it is not read as a severe deficiency.

The score adjusts yield retention. It is also stored on the interval so a nutrient-deficit stress label can be told apart from water deficit. Maps are 20 m, matching the red-edge bands.

Phosphorus, potassium, electrical conductivity, organic carbon, and pH are not produced. They need soil information this version does not use.

---

## 14. Document

One document per farm per run.

- Identity, area, crop, confidence, season, ecoregion, reference pool
- Zones: geometry, area share, cohort, crop name or `unspecified`
- Per zone: sowing median and early/late, sources, confidence
- Per zone: τ, stage, harvest date or window, duration check
- Per update: observation kind (`optical`, `radar`, `inferred`), uncertainty, cover, water, biomass
- Stress type and stressed area when uncertainty allows
- Yield point and band, the three component values, retention
- Nitrogen status on clear dates
- Weather spread for the day: rain median and tails, temperature, vapour-pressure deficit, irrigation flag, uneven-rain flag
- The index series for that parcel (NDVI, NDRE, NDMI, PSRI, RVI, and the rest), one row per satellite date
- Lineage back to the cluster, the village, and the classification field the crop came from

Each updated parcel is also written to `monitoring_parcels`, one row per parcel, so a later comparison can query two clusters by crop, season, sowing date, and harvest date without unpacking the job. The crop on that row is the classified crop, not a fixed pair.

Maps (stress, cover, nitrogen) are written for optical dates. Radar dates can carry a cover map from RVI at 10 m. Inferred dates carry numbers and uncertainty, not a filled index raster.

An update that does not change any zone is not rewritten.

---

## 15. How to build it

Implement under `Crop_Monitoring/`, calling the existing collector, weather fetchers, calendar, and duration table. Do not fork a second copy of the index formulas.

| Phase | Work | Finished when |
|---|---|---|
| 1 | Zone split on green-up date, using Sentinel-1 orbits and clear Sentinel-2, plus the non-crop mask | A two-date field becomes two zones, and a uniform field stays one |
| 2 | Buffer weather: CHIRPS, IMERG, ERA5 including radiation and soil moisture, IMD when files exist. Irrigation flag against the cropland ring | A known irrigated week with no grid rain is flagged, and a storm is stored as a spread |
| 3 | Sowing posterior from rain, radar, optical, and land surface | On monsoon parcels, the median falls in the calendar window even when the emergence week has no clear Sentinel-2 scene |
| 4 | Daily state, optical and radar corrections, uncertainty | A three-week cloud gap advances biomass and widens the band, and the next clear scene pulls the state back |
| 5 | Milestones, τ, duration check, harvest window | Stage follows the observed peak, and an outlier-length zone is flagged rather than forced onto the template |
| 6 | Intra-zone stress | Staggered sowing no longer appears as stress on the early cohort |
| 7 | Peer pools, district yield table, three yield estimators | A crop with few training parcels in that ecoregion still returns a peer-based yield marked with its pool |
| 8 | Nitrogen modifier and the farm document | Nitrogen moves retention on clear dates and is absent on inferred dates |
| 9 | Job entry next to the existing API, reading classified parcels | A new scene updates open zones and leaves stored dates fixed |

Phases 1–6 are the monitoring core. Phases 7–8 are the yield solution. Phase 9 is delivery.

Tune first on rice, wheat, soybean, cotton, mustard, and gram. Maize, onion, chilli, and tobacco need their season branch in the calendar before their peer pools are shared across seasons. Banana, sugarcane, and grapes stay on the perennial path.

---

## 16. Limits that remain

- A zone smaller than 12 pixels is merged away. Very thin strips will not get their own sowing date.
- Radar emergence is about as sharp as the 12-day orbit. The early/late range is the precision, and a single day is published only when optical confirms it.
- Buffer weather still cannot place a storm inside a 1 ha field. The spread and the irrigation flag are the record of that ambiguity.
- The 250 m and 1 km land-surface cues affect timing only.
- Yield is an ensemble scaled to district statistics. It becomes tighter after the peak and after an optical correction. It is still a projection.
- Nitrogen is a red-edge status. It is not a soil test.
- Pest and disease ranking is deferred until an agent library exists.
- A wrong crop name on the dominant zone still selects the wrong prior. The confidence gate and the `duration_outlier` flag are what limit the damage. Secondary zones are not given that prior unless their shape matches.

---

## 17. Relationship to the wireframe

`crop_monitoring_full.md` remains the product description. This framework keeps sowing, stage, harvest, stress, yield, biomass, nitrogen, weather, and maps. It changes the methods where the wireframe would fail on this geography:

| Wireframe behaviour | Framework behaviour |
|---|---|
| Sowing from optical emergence, radar as support | Sowing from rain, both radar orbits, optical, and land-surface cooling, as a distribution |
| One boundary, one sowing date, mixed pixels called stress | Zones split first; stress is inside a zone |
| Unobserved optical day, or a filled index | A weather-driven state corrected by radar and by the next clear scene, with uncertainty |
| Weather at the centroid | Buffer percentiles, plus an irrigation flag from field wetness versus the neighbourhood |
| Yield from a template stage fraction and a harvest date | Yield from three estimators on observed progress, projected across a harvest window |
| Nutrient scores including soil priors | Nitrogen canopy status only |
| Pest list on every interval | Deferred |
| A regional library curve, widened when uncalibrated | District peers, then climate analogs, then a crop-wide shape with a local level |
