# Crop Monitoring — Full System

**Organisation:** Elai  
**System:** Enhanced Crop Monitoring Pipeline (stages 0–9)  
**What this document is:** A complete account of what the system performs, what it processes, the methods behind each result, and the solutions it publishes for a farm.  
**Source of truth:** The live pipeline (`main.py` and the analysis modules) and the crop library in `config.py`. Stage-by-stage technical notes live alongside this file in `documents/`.

---

## 1. What we do

We monitor one farm boundary, one crop, and one sowing date from establishment through harvest. Every few days a new satellite observation is compared with a configured reference for that crop, region, season, and growth stage. From that comparison the system publishes crop condition, stress, a yield projection, nutrient status, biomass, and a ranked pest and disease risk list.

The engine is a rule-based reference library, not a model trained on five-day crop-condition labels. Those labels are scarce, and a model trained in one geography does not carry to a new region until the same labelled record exists there. The library already holds the expected canopy, stage timing, and tolerances, so each new observation can be checked against it on the same cycle the satellite arrives.

A farm is assessed on the satellite cycle, about every five to six days, from sowing through harvest. Results are written to a central store so dashboards and field tools can read them by farm, coordinator, and region.

**What a completed run provides**

| Solution | What the field team receives |
|----------|------------------------------|
| Sowing date | The date the cycle is counted from, with a source and a confidence |
| Growth stage | The stage the crop is in on each observation, in days after sowing |
| Harvest date | A detected harvest date when the canopy crashes, or a projected harvest window from the crop calendar when it has not |
| Stress | Healthy, nutrient deficit, crop water deficit, tissue damage, or sub-optimal growth, with the share of the field that is stressed |
| Yield | A tons-per-hectare projection for the interval, with a low–high band |
| Nutrients | Nitrogen, phosphorus, and potassium scores, plus soil proxies for salinity, organic carbon, and pH |
| Biomass and dry matter | Accumulated above-ground biomass and the harvestable fraction |
| Pest and disease | A ranked list of probable agents, each with a probability, a confidence tier, and a recommended action |
| Weather context | Heat, rainfall, humidity, growing degree days, and vapour-pressure deficit on the same dates |
| Maps | Optional field maps for stress, yield, nutrients, and biomass |

Each of these is an advisory product. Stress is not a laboratory diagnosis. Yield is a canopy-based projection, not a weighbridge result. Nutrient scores are canopy proxies, not a soil test. Pest and disease output is a scouting list, not a confirmed pathogen.

---

## 2. What we process

Every run starts from a farm record and builds a stack of observations for that polygon.

### Farm identity

The farm API supplies the boundary, crop, country, sowing date, establishment method, and season. The boundary is treated as one crop and one sowing date. A polygon that mixes crops or sowing dates will be scored as if it were uniform, and the mixed canopy will be reported as stress.

Country is the satellite region (India, Slovakia, Egypt, Philippines). An Indian state, when present, is a sub-region used for cycle length and some yield envelopes. Establishment is normalised to direct-sown, transplanted, planted, or, for sugarcane, ratoon.

### Satellite observations

| Source | What it contributes | Resolution and cadence |
|--------|---------------------|------------------------|
| Sentinel-2 optical | Vegetation and stress indices computed on the farm | 10 m, about every 5–6 days when the scene is clear |
| Sentinel-1 radar | VV, VH, and radar vegetation index through cloud and at night | All-weather, paired to the same intervals |
| Open-Meteo | Temperature, rainfall, humidity, reference evapotranspiration, growing degree days, vapour-pressure deficit | One point at the farm centroid, historical and forecast |
| Landsat thermal | Land-surface temperature for heat context, or an air-temperature proxy when the thermal scene is missing | Field-level heat flag, not a pixel diagnosis |

Optical indices used across the pipeline include NDVI, NDRE, EVI, GNDVI, MSAVI, GCI, IRECI, NDMI, NDWI, MTCI, PSRI, NBR, BSI, S2REP, TCARI/OSAVI, and the radar pair VV/VH with RVI. Each family answers a different question: greenness and canopy mass, red-edge chlorophyll, leaf and canopy water, senescence and bare soil, and structure that remains visible when optical data is cloudy.

### Time window

Monitoring runs from a short buffer before sowing through the crop’s harvest-search budget. Intervals are chained to the actual acquisition date: if a clear scene lands on day 7 instead of day 6, the next window starts from day 7. Previous-season catch-up uses fixed six-day buckets. A run that finds no new interval stops; weather, analysis, and upload are not repeated for dates already stored.

### Crops in the library

Nineteen crops are configured. A configured crop has a stress library, a nutrient library, soil-property priors, a yield envelope, growth stages, and regional durations.

| Crop | Typical establishment | Notes |
|------|----------------------|--------|
| Cotton | Direct-sown | Explicit multi-picking harvest |
| Maize | Direct-sown | |
| Rice | Transplanted or direct-sown | Separate stage tables |
| Wheat | Direct-sown | Calendar phenology preferred in long cool seasons (for example Slovakia) |
| Barley | Direct-sown | |
| Potato | Direct-sown | Season branches: India rabi/kharif; Egypt nili, winter, summer |
| Soyabean | Direct-sown | |
| Groundnut | Direct-sown or transplanted | |
| Mustard | Direct-sown | |
| Gram | Direct-sown | |
| Onion, White Onion, Red Onion, Sanghar White Onion | Transplanted or direct-sown | Separate crops: stage length and yield envelope differ |
| Sugarcane | Direct-sown, transplanted, or ratoon | Cycle counted from planting, or from the previous harvest for ratoon |
| Banana | Planted | Long cycle |
| Sunflower | Direct-sown | |
| Chilli | Transplanted or direct-sown | |
| Tomato | Transplanted or direct-sown | |

Calibration against local harvest records is not uniform. White Onion is marked calibrated. Cotton, maize, wheat, Sanghar White Onion, and sugarcane are partial. The remaining crops are uncalibrated: the same methods run, and the yield uncertainty band is widened.

---

## 3. How a farm is processed

The pipeline is ten stages. Biomass and dry matter run inside analysis. Pest and disease ranking runs at the end of stress. Nutrient letters on a stress label are attached after nutrient scores exist.

```text
0 Config     crop, region, stage, and tolerance library
1 Farm       boundary, sowing, crop, season, download window
2 Download   Sentinel-2 indices and Sentinel-1 radar, cloud flags
3 Weather    temperature, rain, GDD, VPD, land-surface temperature, stage name
4 Imputation fill cloudy optical pixels, or mark the interval unobserved
5 Analysis   field means, smoothing, sowing and harvest, growth timeline, biomass
6 Stress     stress type and stressed area; pest and disease ranking
7 Yield      tons per hectare for the interval
8 Nutrient   N, P, K and soil proxies; then N/P/K on the stress label
9 Upload     one farm document for dashboards
```

Stages 6, 7, and 8 all read the analysis summary. Upload assembles them. It does not recompute the science.

When cloud imputation is off, a cloudy interval is stored as unobserved. No filled maps or means are invented. Stress for that date is the stub “Data Not Fetched”. The next clear date is scored on its own.

---

## 4. Sowing dates

### What we publish

A sowing date, a confidence from 0 to 1, and a source. Days after sowing (DAS) for every later product are counted from this date. Day 0 is sowing for a direct-sown crop, the transplanting date for a transplanted crop, the planting date for a planted crop, and the previous harvest for sugarcane ratoon.

### Method

Production uses the sowing date on the farm record. That path has confidence 1.0 and source `provided`.

When the record has no usable date, or when a run is asked to estimate sowing, the detector looks for emergence and then steps backward.

1. **Optical emergence.** NDVI (and supporting greenness indices) crossing the crop’s emergence threshold marks the date the canopy becomes visible.
2. **Radar emergence.** A rise in radar vegetation index, or a structural change in VV/VH, corroborates emergence when cloud hides the optical signal.
3. **Lag to sowing.** The detected emergence date is moved back by the crop’s sowing-to-emergence lag (commonly about 10 days, crop-specific in the library).
4. **Season window check.** Where a monsoon onset is known, the estimated date is checked against the sowing windows for that crop. If emergence is not found and a monsoon onset exists, onset is used as a low-confidence proxy (about 0.4). If neither exists, sowing detection fails and the cycle is not invented.

### What this is for

Stage timing, stress expectations, yield applicability, and harvest search all depend on DAS. A wrong sowing date shifts every stage window. Mixed sowing inside one boundary cannot be represented: the detector returns one date for the polygon.

---

## 5. Growth stages

### What we publish

For each interval, the phenological stage and the days after sowing. The weather stage writes the calendar stage name from the library. Analysis also stores a fused timeline that can note where the canopy is ahead of or behind that calendar. Downstream stress, yield, and nutrient scoring use the calendar stage from days after sowing, not a stage renamed by a single cloudy or imputed index.

### Method

Each crop has a day-based template: a start and end day for every stage, measured from day 0. Examples of the raw template, before a region stretches it:

| Crop | Stages (raw template) |
|------|------------------------|
| Cotton | Germination, vegetative, squaring and flowering, boll development, maturity, harvest |
| Rice, transplanted | Transplanting establishment, tillering, panicle initiation and heading, grain fill, maturity, harvest |
| Rice, direct-sown | Germination, then the same reproductive sequence on a longer calendar |
| Wheat | Germination, tillering, jointing and heading, grain fill, maturity, harvest |
| Maize | Germination, vegetative, tasseling and silking, grain fill, maturity, harvest |
| Potato | Emergence, vegetative, tuber initiation, tuber bulking, maturity, harvest |
| Groundnut | Germination, vegetative, flowering and pegging, pod fill, maturity, harvest |
| Soyabean | Germination, vegetative, flowering, pod fill, maturity, harvest |
| Onion (Sanghar, transplanted) | Transplanting establishment, vegetative, bulbing, maturation, harvest |
| Sugarcane | Germination and establishment, tillering, grand growth, maturation, harvest-ready |

Two adjustments sit on the raw template.

**Establishment and season.** Transplanted crops skip germination and use a shorter establishment (about 10 days versus about 20 days for direct sowing). Potato uses a different block for each season. Rice, onion, groundnut, chilli, and tomato branch by establishment method.

**Regional length.** The template is stretched or compressed to the region’s actual season length, and further when a farm or sub-region has a known cycle length. A long cool-season wheat calendar is not the same map as a short tropical one. The stretch changes stage dates. It does not replace the crop’s stress or nutrient tables with another crop’s tables.

**Heat time.** Growing degree days accumulate from sowing using a crop base temperature, an optimum, and a maximum. Wheat and barley use a base near 0 °C. Cotton uses about 15.6 °C. Sugarcane and banana accumulate several thousand degree-days over a long cycle. GDD flags whether development is ahead of or behind the day calendar. It does not replace the day-based stage name used for scoring.

Where the template has no explicit harvest stage, one is placed after maturity. That window is field operations (typically about 10–25 days), not another green growth stage. Cotton is the exception: three pickings are modelled, the first around 150–165 days after sowing, then every 15–20 days, with an expected greenness drop at each picking.

---

## 6. Harvest dates

### What we publish

A harvest date when the sensors show the crop has been taken or has crashed into senescence, plus a harvest window. If the crash has not happened yet, a projected ready-to-harvest date and window are still stored so the season is not left without a harvest field. Confidence and source travel with the date. Sources include optical, radar, both sensors, a weather-informed estimate, a cotton multi-pick detection, and a calendar fallback.

### Method

Search opens near the start of maturity (or the harvest stage, whichever is earlier) and runs through the phenology harvest end plus a short buffer. The download budget (`harvest_days`) sizes how far the search may look. It is not itself the harvest date.

Inside that window the detector looks for complementary evidence.

1. **Optical crash.** NDVI must have reached a real peak earlier in the season. Harvest is the later drop: a fall through a crash threshold (default about 0.35, crop-specific), or a relative drop from the peak (default about 18 percent), confirmed by senescence or bare-soil indices (PSRI rise, BSI rise). A canopy that is still green is not called harvested on a relative dip alone.
2. **Radar harvest.** A structural drop in backscatter or radar vegetation index in the same window corroborates or, when optical is missing, stands alone at lower confidence.
3. **Cotton pickings.** Successive NDVI drops of about 12 percent inside the picking calendar, rather than a single end-of-season crash.
4. **Re-anchor to a clear crash.** If a first estimate sits on a cloudy or imputed date, and a clear observation shows the real greenness collapse, the date moves to that clear crash. A late farm date must not push the optical floor past a crash that is already visible.
5. **Calendar fallback.** If optical and radar do not find a harvest, the date and window are taken from the phenology harvest stage (regionally stretched for a long or short season). This is a projection. It is replaced when a later interval shows a real crash. It applies mid-season as well, so a farm that is not yet mature still has a planned harvest window.
6. **Canopy extension.** If green canopy clearly continues past the template harvest, the timeline can be extended and the harvest date moves with that evidence.
7. **Post-harvest check.** A greenness rebound after the reported harvest is flagged as an anomaly (regrowth, a missed crash, or a wrong date).

Stress and yield stop treating the field as an active crop once the harvest window has passed. They freeze or skip rather than scoring bare soil as nutrient stress.

### What this is for

Harvest readiness, the end of stress and yield scoring, and season duration. Duration is harvest date minus sowing date when both exist.

---

## 7. Stress analysis

### What we publish

For each interval that has a usable observation:

| Field | Meaning |
|-------|---------|
| Stress type | Healthy, Nutrient Deficit, Crop Water Deficit, Tissue Damage, Sub-optimal Growth, or Data Not Fetched |
| Stressed percentage | Share of valid farm pixels in mild, moderate, or severe stress |
| Overall stress score | Field score from 0 to 1 after uncertainty adjustments |
| Weather stress | Heat, vapour-pressure deficit, or land-surface temperature, reported beside the biological type |
| Nutrient letter | After nutrient scoring: Nutrient Deficit (N), (P), or (K) |

### Method

The profile for the interval comes from the stage: pre-sowing, establishment, standard growth, a vulnerable or critical stage, maturity, or harvest. Critical stages in the crop library (flowering, grain fill, bulbing, tuber bulking, boll development, and crop-specific stages such as groundnut pod fill) use tighter expectations.

Expected index levels are interpolated inside the stage and can blend across a stage boundary. The field is then compared in two ways.

**Area.** Once a real canopy is present, stressed area is intra-field: each pixel is compared with the healthy pixels of this same farm, not with a national peak NDVI. Bins are healthy at or below 20 percent deviation, mild from 20 to 40, moderate from 40 to 60, and severe above 60. Stressed percentage is mild plus moderate plus severe. Below 1 percent the field stays Healthy. At or above 1 percent it cannot stay Healthy.

**Type.** The label comes from which index family departed:

| Type | Evidence | What it means in the field |
|------|----------|----------------------------|
| Nutrient deficit | Red-edge and chlorophyll indices (NDRE and related), not a biomass index alone | Canopy nutrition is behind the stage reference |
| Crop water deficit | Moisture indices (NDMI, NDWI) | Leaf and canopy water are down; uncorroborated water stress is capped so a single dry index does not dominate |
| Tissue damage | Senescence and burn indices (PSRI, NBR, BSI), with radar used for a lodging-like VV jump | Canopy structure or pigment breakdown, including physical damage |
| Sub-optimal growth | Bare or weak canopy late in establishment, or a general shortfall that does not lock to one type | Establishment failure or a diffuse lag |
| Heat (weather track) | Land-surface temperature corroborated by air maximum temperature, plus vapour-pressure deficit | Raises the overall score and fills weather stress. It does not rename the biological type to “heat” |

Early establishment on imputed or cloudy data prefers Healthy and caps the stressed share. Clear bare land late in establishment is allowed to become sub-optimal or a typed stress. Maturity dampens the score (about half) because senescence is expected. Imputed intervals are scored with a quality penalty so a filled pixel cannot outrank a clear one. Rice waterlogging can be suppressed where standing water is normal for the stage.

Intervals with no optical observation are not scored. They are marked Data Not Fetched. Carry-forward of the last clear stress reading is off.

### What this is for

A five-day condition call: on the expected path, or under a named stress, with how much of the field is affected. It is the trigger for scouting. It is not a prescription by itself. The nutrient letter is added only after the nutrient stage has a score, so a water-stress interval is not mislabelled as nitrogen.

---

## 8. Yield analysis

### What we publish

| Field | Meaning |
|-------|---------|
| Projected yield | Tons per hectare for this interval |
| Low and high | Uncertainty band; wider when the crop is uncalibrated or the interval was imputed |
| Retention | Fraction of potential left after penalties, from 0 to 1 |
| Potential percentage | Projection relative to the crop’s configured maximum |
| Applicability | Active, establishment pending, pre-sowing, or harvest window |

The model version is `yield_v3_potential_adjusted`. It is a heuristic. It is not a process model such as APSIM or DSSAT. It is for tracking whether the season is holding, gaining, or losing yield potential. It should be read against harvest records before it is treated as a production forecast.

### Method

The crop library holds a base yield and a maximum yield in tons per hectare. A sub-region overlay can change that envelope (for example a state rice maximum that differs from another state’s). A country multiplier then applies a light adjustment: cool-season regions slightly lower peak density, arid regions slightly lower maximum, with India left at 1.0 for the common case.

Applicability gates the number. Pre-sowing and germination do not project a harvestable yield. Harvest and post-harvest freeze the maturity snapshot instead of scoring bare soil.

For an active interval:

```text
composite health = weighted blend of NDVI, NDRE, radar vegetation index, and NDMI
                   inside the ranges expected for this stage

retention = 1
            − stress penalty
            − field-heterogeneity penalty
            − heat penalty
            − vapour-pressure-deficit penalty
            − waterlogging penalty
            − uneven-rainfall penalty
            − nutrient penalty (off until nutrient scores exist; cap is zero by default)
            + recovery credit when stress improved since the last interval

stage potential = maximum yield × stage fraction
health factor   = floor + (1 − floor) × composite health
projected yield = stage potential × health factor × retention
                  clipped to the crop maximum
```

Stage fraction is about 70 percent of maximum at vegetative or tillering onset, and about 85 percent at fill and peak stages (grain fill, boll development, bulbing, tuber bulking, pod fill, grand growth). Health nudges around that seed. Retention still applies, so a stressed fill stage does not keep 85 percent.

Penalty sources:

| Penalty | What reduces yield |
|---------|-------------------|
| Stress | Stressed area from the stress stage, stronger in critical windows |
| Heterogeneity | Uneven NDVI across the field versus the crop’s normal variation |
| Heat | Reproductive heat, and only when land-surface temperature is backed by air temperature; a still-healthy canopy scales the weather penalty down |
| Vapour-pressure deficit | Dry-air days in grain fill, boll, tuber, or maturity |
| Waterlogging | Radar wetness in stages where standing water hurts the crop |
| Rainfall | Uneven rain relative to the crop’s yield modifiers |
| Recovery | A credit when the stress score improved |

An optional blend with biomass and harvestable dry matter can pull the index-based number toward the growth model. That blend is skipped at maturity except for storage-organ crops (onion, potato, groundnut), where late biomass still informs the harvestable organ. A drop guard stops one imputed interval from collapsing the season estimate.

Cloudy unobserved intervals produce no yield number. A cloudy carry of the last clear yield is off by default.

### What this is for

A per-interval production trajectory: plot comparison, harvest logistics, and a season estimate that can be checked against reported tons per hectare. Variety and region comparisons are valid where the same method and the same envelope rules were used. An uncalibrated crop’s band is intentionally wider (`UNCALIBRATED_BAND_MULTIPLIER` 1.4).

---

## 9. Nutrient and soil status

### What we publish

Per interval, while the crop is in an active growth stage:

| Product | Meaning |
|---------|---------|
| Nitrogen, phosphorus, potassium | Scores against this crop’s stage reference curve |
| Status band | Low below 50, Medium from 50 to 74, High at 75 and above |
| Electrical conductivity | Salinity proxy from stage-normalised vigour and radar |
| Organic carbon | Season baseline, not a weekly soil test |
| pH | Season baseline; Indian field crops use a slightly alkaline prior |
| Recommendation | Stage-timed note tied to the limiting score |

Model version: `nutrient_v4_scientific`. These are satellite proxies. They do not replace soil sampling, and they are not a national kilogram-per-hectare recommendation table.

### Method

Scoring is raster-first: each pixel is compared with the stage reference, and the field value is the median. A scalar path remains as fallback and for recommendation text.

Nitrogen uses red-edge chlorophyll (NDRE and related ratios) against a dilution curve, with a limited blend from the nitrogen nutrition index. The nutrition index can support a medium nitrogen score. It cannot force a low score when the optical nitrogen signal is medium. Early in the season the score is clamped so bare soil is not called a severe deficiency. Phosphorus and potassium use their own index weights by stage. Germination is deferred. Harvest is excluded.

Soil properties are season baselines with a light country adjustment: cooler regions lower salinity and higher organic carbon; arid regions the reverse and a slightly higher pH. India multipliers are 1.0. Sub-region does not pick a different nutrient table.

After this stage finishes, stress rows that were typed as nutrient deficit receive a single letter, N or P or K, from the limiting score.

### What this is for

Which nutrient is behind, at which stage, so an input decision can be timed. The map shows where in the field the score is low. Confirmation is still a soil or tissue test.

---

## 10. Biomass and dry matter

### What we publish

| Product | Meaning |
|---------|---------|
| Accumulated biomass | Above-ground dry biomass, kilograms per hectare, as a season running total |
| Daily growth rate | Interval-average gain |
| Biomass stress ratio | Actual accumulation divided by the amount expected at this DAS |
| fAPAR | Fraction of photosynthetically active radiation the canopy absorbs |
| LAI | Leaf area estimated from fAPAR and an extinction coefficient |
| Nitrogen nutrition index | From NDRE and the crop dilution curve |
| Dry matter | Harvestable dry matter: above-ground biomass times a crop-and-stage partition, which becomes the harvest index at maturity |

Biomass model: `biomass_v1_rue_accumulator`. Dry matter model: `dry_matter_v2_partitioned_hi`. Both are computed inside analysis, not as separate pipeline stages.

### Method

Growth follows radiation-use efficiency:

```text
biomass gain = radiation-use efficiency
               × photosynthetically active radiation
               × fAPAR
               × temperature and water-stress scalars
```

fAPAR comes from canopy indices. Weather supplies radiation, temperature, and vapour-pressure deficit. The crop cycle supplies DAS and stage, which select the efficiency and the partition fraction. Unobserved cloudy intervals are skipped. The result is model-derived. It is not a weighed biomass sample.

Biomass corroborates stress (a growth stall), can blend into yield, supplies fAPAR and nutrition-index maps to the nutrient stage, and flags sudden growth anomalies for pest and disease ranking.

### What this is for

Early warning that a plot is accumulating less than the stage expects, and a harvestable-mass check on the yield projection. For seed and storage crops, biomass is a vigour signal as well as a yield input.

---

## 11. Pest and disease risk

### What we publish

On each scored in-season interval, a ranked list of probable agents (pests and diseases together), typically the top few. Each agent has a common name, a scientific name when known, a probability from 0 to 1, a confidence tier (high, medium-high, medium, low), and a recommended action such as scout, spray, or change irrigation. A season summary aggregates agents that alerted more than once.

A second track runs when the field is under real stress (mild or worse, or a non-healthy type): stress-relevant agents only, with probability capped so a stress interval cannot read as a confirmed outbreak.

Model version: `pest_disease_v3_localized`. A separate forecast tool (`forecast_PD_model.py`) can score forward weather. That forecast is not part of the five-day pipeline.

### Method

The library holds agent rules per crop: stage windows, days-after-sowing ranges, establishment method, and conditions on indices, radar, weather, and stress. An agent scores when its conditions match the interval. Matches that persist across intervals are raised by a duration tracker. Probability is calibrated, then ranked.

The image does not identify a pathogen. The rank is the overlap of canopy signature, weather suitability (humidity, temperature, leaf wetness, rainfall), stage, and spatial patchiness. Germination is suppressed so bare-soil noise is not called a disease. Unobserved intervals are not ranked.

### What this is for

A scouting order before damage is irreversible: which agent is most consistent with this interval, and whether a ground check should precede treatment. Field confirmation remains the decision.

---

## 12. Weather and cloud handling

### Weather method

Weather is attached to every interval, including cloudy ones. It does not fill optical indices.

Growing degree days use the crop base and ceiling from sowing onward. Vapour-pressure deficit counts dry-air stress days. Land-surface temperature is a heat input only when air maximum temperature agrees; a proxy from air temperature is labelled as a proxy. Monsoon onset is detected for India and the Philippines and can adjust sowing windows. Cotton uses an additional rainfall score. The point is the farm centroid, not a weather grid across the field.

### Cloud method

A scene is cloudy when cloud fraction exceeds the threshold, or when the optical means are unusable. Two modes:

| Mode | What happens |
|------|----------------|
| Imputation on (default) | Cloudy optical pixels are filled from radar, the crop phenology curve, and the trend of clear intervals. Fills are estimates. Stress, yield, and nutrients still run, with wider uncertainty and early-season caution |
| Imputation off | The interval is unobserved. No means, no maps, stress stub Data Not Fetched, no yield, no nutrient score, no pest rank. The cursor still advances |

Nineteen optical indices can be filled. India uses the base crop curve. Another country uses a regional curve when one exists (for example maize in Slovakia, potato in Egypt, rice in the Philippines). Radar is not imputed; it is the all-weather observation the fill leans on.

---

## 13. What we publish

Upload builds one crop-monitor document and posts it for dashboards. Small farms go in one request. Large farms are split: scalars first, then maps interval by interval.

The document carries:

- Farm identity, area, crop, season, region, sowing, and harvest
- The growth timeline and days after sowing
- Per-interval index means, weather, and data-quality flags
- Stress, including Data Not Fetched stubs for unobserved dates
- Yield projections and, when maps are enabled, yield rasters
- Nutrient scores and soil baselines
- Biomass, dry matter, and their maps
- The pest and disease ranking
- Optional GeoTIFF layers for stress, yield, nutrients, and biomass

Unobserved intervals are omitted from index means and maps. Their stress stub is kept so the timeline shows the gap instead of a silent skip. Historical intervals already stored are not recomputed when a run only has new dates.

Entry points are the command-line pipeline, a batch runner over many farms, and an HTTP job API. Upload can be skipped when a run is only for local review.

---

## 14. How the solutions fit together

```text
Farm record (one boundary, one crop, one sowing)
        │
        ▼
Satellite + weather on a 5–6 day cycle
        │
        ├── Sowing date ──────────► days after sowing
        │                                │
        │                                ▼
        │                         Growth stage (library calendar,
        │                         stretched to the region)
        │                                │
        ├────────────────────────────────┼──────────────────────────┐
        ▼                                ▼                          ▼
   Stress type and              Yield tons/ha                 Nutrient scores
   stressed area                (health × retention           N / P / K and
        │                        × stage potential)            soil proxies
        │                                ▲                          │
        │                                │                          │
        ▼                                │                          ▼
   Pest and disease rank          Biomass and                 Letter on the
   for scouting                   harvestable dry matter      nutrient-stress label
        │
        ▼
   Harvest date or projected harvest window
        │
        ▼
   Published farm document (numbers, timeline, maps)
```

Reading order for a field user:

1. Confirm sowing date and source. If it is estimated, check confidence before trusting stage dates.
2. Read the stage. Interventions and risk windows are stage-specific.
3. Read stress type and stressed percentage. Healthy with a small weather note is not the same as a typed biological stress.
4. If the type is nutrient, read the N, P, or K letter and the score band.
5. Read yield as a trajectory against the crop maximum, and widen the interpretation when the crop is uncalibrated or the interval was imputed.
6. Use the pest and disease list as a scout order, then confirm in the field.
7. Use the harvest date as detected when the source is optical or radar, and as a plan when the source is the calendar fallback.

---

## 15. Limits that sit on every solution

These are properties of the method, not failures of a single farm.

| Limit | Consequence |
|-------|-------------|
| One boundary, one crop, one sowing date | Mixed crops or staggered sowing inside the polygon are reported as stress |
| Reference library must exist first | A new region, crop, or variety is a reference-building season; deviations are expected until local stage timing and canopy levels have been observed |
| Five-day labels are not used to train a condition model | Condition is the comparison with the configured reference |
| Yield, nutrients, and biomass are models | They support monitoring. Harvest weight, soil tests, and crop cuts remain the measurements |
| Pest and disease is a rank, not an identification | Action follows field confirmation |
| Cloudy intervals | Either a conservative fill or an explicit gap. Neither is a clear satellite view |
| Weather is one point | A storm that misses the centroid, or irrigation the weather station cannot see, will not appear as rain |
| Calibration is uneven | Only crops marked calibrated or partial should be treated as locally checked; others carry a wider yield band |
| Recurrent tuning | Stage windows, expected index levels, and tolerances are per crop, region, season, variety, and duration. A library tuned for one of those does not automatically fit another |

Where references have been observed, validated interval by interval, and tuned, the next season can run on that configuration. Where they have not, the five-day output shows the gap between the configured expectation and the field.

---

## 16. Where the detail lives

| Topic | Document |
|-------|----------|
| Crop library, stage tables, yield envelopes, nutrient curves | `documents/SOLUTION_REFERENCE.md`, `documents/config.md` |
| Farm ingest | `documents/farmprocessing.md` |
| Satellite download | `documents/datadownloading.md` |
| Weather, GDD, monsoon | `documents/weatheranalysis.md` |
| Cloud fill | `documents/cloudimputation.md` |
| Sowing, harvest, timeline, smoothing | `documents/Dataanalysis.md` |
| Biomass and dry matter | `documents/Biomassanalysis.md` |
| Stress | `documents/stressanalysis.md` |
| Yield | `documents/yieldanalysis.md` |
| Nutrients | `documents/soilnutrientanalysis.md` |
| Pest and disease | `documents/pestndiseaseanalysis.md`, `documents/PEST_DISEASE.md` |
| Published document | `documents/uploadmongoanalysis.md` |
| Pipeline map | `documents/CODEBASE_ANALYSIS.md` |
| Delivery constraints of the reference library | `documents/Crop_Monitoring_Model_problems.md` |
