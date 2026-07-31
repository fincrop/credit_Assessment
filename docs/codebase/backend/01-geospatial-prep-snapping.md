# Backend Stage 01 — Geospatial Prep & Snapping (Updated Deep-Dive)

## Purpose & Role

This is the **contract stage** of the whole pipeline. Every downstream stage inherits its spatial footprint (bbox/centroid/area) and temporal footprint (start/end dates, season anchors) from what happens here.

## Present Condition — How It Actually Works Today

### Spatial resolution path
1. `assess_farmer_from_db` loads `farm_info` (lat/lon, `field_area_ha`, optional polygon geometry, LGD codes, optional crop/sowing hints, benefits flags).
2. `_collect_continuous_approach` in `satellite_collector.py` branches:
   - **Polygon present** → geometry QA (validity + area ratio vs registered ha); on pass, bbox from geometry; on fail, **point + adaptive buffer** fallback.
   - **Point-only** → `calculate_bbox_from_point` with **adaptive buffer** (floor `MIN_FIELD_BUFFER_KM` ≈ 0.15 km unless `FIELD_BUFFER_LEGACY=1`).
3. **Area duality remains:** registered `field_area_ha` feeds Stage 07 credit limit; geometry-derived area is diagnostic.

### Temporal resolution path
4. `_snap_to_season_start` uses `PipelineConfig.SEASON_SNAP_ANCHORS` (Jun 15 / Oct 15). Calendar windows in `SEASONS` are separate (May/Oct) and documented as such.
5. Snap is stamped onto `assessment.geospatial_prep` (`season_anchor_used`, `snap_logic_version`, window dates, geometry source, buffer).

### Eco-context attachment
6. `infer_agro_ecoregion` uses lat/lon boxes plus **major-state LGD hints**.
7. Eco context + sowing/crop hints are passed into Stage 03 and **soft-applied** (peak-CVI nudge, sow bias); see `hints_applied` / `applied_knobs` on cycle meta.

## Ground Reality (still valid)

Small Indian holdings, inconsistent digitization, and monsoon-onset variability remain structural constraints. Adaptive buffering and geometry QA mitigate neighbor contamination and bad polygons; dynamic monsoon snap and national agro-climatic shapefile joins remain **deferred**.

## Enhancement status

| Item | Status |
|------|--------|
| Adaptive / smaller point buffer | **Done** |
| Geometry QA + fallback | **Done** |
| geospatial_prep provenance stamp | **Done** |
| Soft wire agro_profile / sowing hint → Stage 03 | **Done** |
| Dynamic monsoon onset snap | Deferred |
| ICAR/NARP spatial join | Deferred |

## Interfaces

| Direction | Coupling |
|-----------|----------|
| Upstream | Mongo `farm_info` / direct `assess_farmer` args |
| Downstream | Stage 02 bbox + dates; Stage 03 soft priors; Stage 07 `field_area_ha` |
| Shared | `PipelineConfig`, Mongo cache key schema |

---
*Action plan: [stage-actions/01-geospatial-prep.md](stage-actions/01-geospatial-prep.md) · Status: [IMPLEMENTATION-STATUS.md](stage-actions/IMPLEMENTATION-STATUS.md)*
