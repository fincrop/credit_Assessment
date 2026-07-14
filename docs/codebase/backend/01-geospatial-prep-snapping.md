# Backend Stage 01 — Geospatial Prep & Snapping (Updated Deep-Dive)

## Purpose & Role

This is the **contract stage** of the whole pipeline. Every downstream stage — satellite fetch (02), cycle detection (03), classification (04), weather (05), performance (06), credit (07) — inherits its spatial footprint (bbox/centroid/area) and temporal footprint (start/end dates, season anchors) from what happens here. There is no retry-and-fix-later: if the bbox is wrong or the window is mis-snapped, every downstream NDVI point, every weather day, every cycle boundary is silently wrong, and it will look like a "model problem" three stages later when it is actually a Stage 01 problem. Treat this stage as the source of truth for "where" and "when," not as boilerplate.

## Present Condition — How It Actually Works Today

### Spatial resolution path
1. `assess_farmer_from_db` loads `farm_info` (lat/lon, `field_area_ha`, optional polygon geometry, LGD codes, optional crop/sowing hints, benefits flags).
2. `_collect_continuous_approach` in `satellite_collector.py` branches:
   - **Polygon present** → `GeometryUtils.calculate_bbox_from_geometry` gives bbox; `calculate_centroid_from_geometry` / `calculate_area_from_geometry` give centroid + geometry-derived hectares.
   - **Point-only** → `calculate_bbox_from_point(lat, lon, field_area_ha or 1.0)`, with a hard floor `MIN_FIELD_BUFFER_KM = 0.5` — no bbox is ever thinner than ~500 m even for a 0.1 ha plot.
3. **Area duality is deliberate:** the registered `field_area_ha` (what the farmer/LGD record says) is what feeds Stage 07's credit-limit multiplier. The geometry-derived area is kept alongside as `field_area_ha_geometry` for diagnostics but does **not** override the registered figure. This protects the credit calculation from bad or stale polygon digitization, at the cost of not catching cases where the polygon is actually more accurate than the registry.

### Temporal resolution path
4. `_snap_to_season_start(today, lookback_years=3)` anchors the start date to the most recent **June 15 (Kharif)** or **October 15 (Rabi)** on or before `today - 3 years`; end date is simply `today`.
5. This snap is used twice — once to build the actual download window, once inside `main._build_satellite_cache_key` — so cache keys and live windows stay in lockstep as long as both call sites keep using the same anchor function.
6. **Known internal inconsistency:** `PipelineConfig.SEASONS` (config.py L34–58) encodes May 15–Oct 15 / Oct 15–May 15, header comments elsewhere say Jun 1–Nov 30, and the actual snap anchors are Jun 15/Oct 15. Three different "seasons" exist in the codebase simultaneously. Only the snap-anchor version is live; the other two are documentation/legacy debt that will mislead anyone reading `config.py` first.

### Eco-context attachment
7. `infer_agro_ecoregion(lat, lon, state_lgd_code)` attaches an agro-ecoregion key once location is known. LGD-code-based hinting is implemented for exactly **one state (UP, code "9")** — everywhere else falls back to lat/lon bbox matching only.
8. This eco-context, along with any `sowing_date_hint` / `crop_hint`, is passed into Stage 03's `detect_cycles` as `agro_profile` — and then **discarded there** (`_ = (...)`, a no-op). So today Stage 01 computes context that Stage 03 receives but never uses. This is not a bug per se — it's a half-finished feature — but it means any assumption that "cycle detection is eco-region-aware" is currently false.

## Ground Reality — What This Means Operationally

- **India's land parcels are small and irregular.** NSSO/Agriculture Census data shows average operational holding size is roughly 1.08 ha nationally, with a large share of holdings under 1 ha (marginal farmers). A 500 m minimum buffer against a 0.1–0.5 ha plot means the bbox is dominated by neighboring parcels, not the farmer's own field — Sentinel-2's 10 m pixel size makes this worse, not better, because you're averaging in 2–5 neighboring plots' worth of pixels into "this farmer's" NDVI signal. This is arguably the single biggest silent accuracy risk in the whole pipeline for smallholders, and it originates entirely in Stage 01.
- **Geometry data quality in India is inconsistent.** Farm polygons sourced from farmer self-declaration apps, land records (Bhu-lekh/Bhu-Naksha style digitization), or GPS walk-arounds vary wildly in accuracy — some off by tens of meters, some genuinely precise. Treating "polygon present" as automatically trustworthy (feeding it straight into bbox/centroid without a sanity check against registered area) is a real production risk: a badly digitized polygon can silently produce a bbox that's 10x too large or oriented wrong.
- **Season calendar drift is a real agronomic risk, not just a doc-hygiene issue.** Monsoon onset (which anchors Kharif) has been trending variable/delayed in parts of India in recent years (IMD long-period-average monitoring shows increasing onset variability). A hardcoded Jun 15 anchor assumes a "normal" monsoon year; in a genuinely early or late monsoon year, the anchor can clip the actual sowing window by 2–4 weeks, which cascades into Stage 03 mis-detecting cycle start and Stage 05 mis-aligning growth-stage weather windows.
- **LGD-to-ecoregion coverage of one state (UP only)** means the system's "regional calibration" story is mostly aspirational outside UP today. If lending decisions are being made pan-India, this is a gap between what's documented as a capability and what's actually running.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Parcel-aware buffering instead of a flat 500 m floor.** Best practice in ag-tech remote sensing (used by platforms like EOSDA Crop Monitor, Regrow, or India's own SatSure) is to buffer relative to the *actual* field boundary shape (an inward negative buffer / erosion, e.g. -10 m to -20 m from the polygon edge) rather than a fixed outward radius from a point. For point-only farms, a smaller adaptive buffer scaled to declared area (e.g. buffer = f(√area)) with a much lower floor (~100–150 m, matching 10–20 Sentinel-2 pixels) would reduce neighbor-plot contamination for the majority-marginal-holding population this system is scoring.
2. **Automated geometry QA gate.** Before a polygon is trusted, cross-check geometry-derived area against registered `field_area_ha` (e.g., flag if ratio is outside 0.5x–2x) and check for self-intersecting/invalid geometries (Shapely `is_valid`). Route flagged farms either to point-buffer fallback or to a manual-review queue rather than silently trusting whichever number comes back.
3. **Dynamic season anchors instead of hardcoded Jun 15/Oct 15.** IMD publishes monsoon onset dates annually; a lightweight approach is to pull a public onset-date feed (or maintain a small lookup table updated once a season) and let the anchor shift ±1–2 weeks around the traditional date rather than being frozen. This is a moderate-effort, high-value change because it directly improves Stage 03/05 alignment without touching those stages' code.
4. **Extend LGD→ecoregion mapping nationally**, or better, replace the hand-maintained LGD lookup with a spatial join against a published agro-climatic zone shapefile (ICAR's 127 agro-climatic zones, or the simpler 15 NARP zones) using the farm's lat/lon directly — this sidesteps the LGD-coverage problem entirely and is a one-time GIS join rather than an ever-growing manual map.
5. **Actually wire `agro_profile` into Stage 03**, since Stage 01 is already computing it. Even a modest use — e.g., nudging the peak-CVI floor or expected-cycles-per-year by ecoregion — would let two stages of "we compute this but don't use it" collapse into one coherent feature rather than dead code sitting in two files.
6. **Version and validate the temporal snap itself.** Since Stage 01's date choice is silently inherited by a cache key (Stage 02) and by Stage 05's weather window, consider stamping `season_anchor_used` and `snap_logic_version` onto the assessment record so that later audits/re-scoring can tell which anchor rule produced a given historical assessment — important for lender audit trails and for safely changing the anchor logic later without breaking comparability.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Single source of truth for season anchors — export from `PipelineConfig`, delete the two stale `SEASONS`/comment variants | Prevents future contributors from "fixing" Stage 05/07 against the wrong calendar |
| Quick win | Extend LGD→ecoregion beyond UP, or switch to a lat/lon agro-climatic-zone join | Removes a pan-India accuracy gap that's invisible unless you check state-by-state |
| Medium | Geometry QA gate (area cross-check + validity check) before trusting a polygon | Protects Stage 02 bbox and Stage 07 credit-limit area input from bad digitization |
| Medium | Adaptive/smaller buffer for small point-only parcels | Directly reduces neighbor-plot NDVI contamination that Stage 03/06 currently absorb as "signal" |
| Medium | Stamp `season_anchor_used` / geometry-source metadata onto the assessment | Enables audit and safe iteration without breaking historical comparability |
| Major | Wire `agro_profile`/hints into Stage 03 `detect_cycles` (currently discarded) | Closes the loop between eco-context Stage 01 computes and cycle logic Stage 03 runs |
| Major | Monsoon-onset-aware dynamic snapping (replace fixed Jun 15/Oct 15) | Improves cycle/weather window alignment in early/late monsoon years, a recurring real-world condition |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Direction | Coupling |
|-----------|----------|
| Upstream | Mongo `farm_info` / direct `assess_farmer` args |
| Downstream | Stage 02 uses bbox + dates; Stage 03 receives series and (currently unused) `agro_profile`; Stage 07 uses `field_area_ha` for credit limit |
| Shared | `PipelineConfig`, `SATELLITE_PROVIDER`, Mongo cache key schema |

---
*This document supersedes the original `01-geospatial-prep-snapping.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
