# Stage 01 — Action plan (Issue → Solution)

**Status:** Approved 2026-07-13 (recommended set)  
**Decisions:** A1–A3 Approve · A4 Approve with Stage 03 · A5–A6 Defer · D1–D3 Approve (docs)  
**Implement order:** D1–D3 → A3 → A2 → A1 → A4 (with Stage 03) · A5/A6 later  
**Scope:** Geospatial prep & season snapping only  
**Related code:** `main.py` (assess entry), `data_acquisition/satellite_collector.py`, `utils/geometry_utils.py`, `utils/india_geo_context.py`, `config.py` (`MIN_FIELD_BUFFER_KM`, `SEASON_SNAP_ANCHORS`, `SEASONS`)

---

## Already done — no code change (docs only)

| ID | What docs still say | Reality | Action |
|----|---------------------|---------|--------|
| D1 | Three conflicting season definitions | `SEASONS` (calendar windows) + `SEASON_SNAP_ANCHORS` (Jun 15 / Oct 15) are intentional and documented in `config.py` | Update `01-geospatial-prep-snapping.md` Present Condition §6 |
| D2 | LGD→eco is UP-only | Major state LGD codes mapped in `india_geo_context.py` | Update Present Condition §7 |
| D3 | `agro_profile` discarded via `_ = (...)` | Hints are **logged**; `hints_applied: false` — still not used in math | Soften wording; real gap is “not applied” (see A4) |

---

## Proposed changes — decide per item

### A1 — Adaptive buffer for small point-only farms

**Issue**  
Point-only farms always get at least a **500 m** buffer (`MIN_FIELD_BUFFER_KM = 0.5`), even for 0.1–0.5 ha plots.

**Why change**  
Most Indian holdings are small. A 500 m box pulls in neighbor fields → contaminated NDVI → wrong cycles (03), performance (06), credit (07). This is the biggest silent accuracy risk for smallholders.

**Solution**  
Keep polygon path as-is for now. For **point-only**, compute buffer from declared area instead of a flat 500 m floor:

- Ideal radius ≈ half-side of a square with area = `field_area_ha`, plus a small padding  
- Floor ≈ **100–150 m** (≈ 10–15 Sentinel-2 pixels), not 500 m  
- Cap still allowed for very large declared areas  

**How (concrete)**  
1. Add `PipelineConfig.MIN_FIELD_BUFFER_KM` lower default (e.g. `0.15`) **or** replace floor with `adaptive_buffer_km(field_area_ha)`.  
2. Change `GeometryUtils.calculate_bbox_from_point` (or its caller in `satellite_collector`) to use that helper.  
3. Log `buffer_km_used` on the assessment for audit.  
4. Optional: keep old 500 m behind env `FIELD_BUFFER_LEGACY=1` for A/B.

**Decision:** [x] Approve  [ ] Change (notes below)  [ ] Defer  

---

### A2 — Geometry QA before trusting a polygon

**Issue**  
If a polygon exists, we trust it for bbox/centroid with **no** check against registered `field_area_ha` or geometry validity.

**Why change**  
Bad digitization (10× too large, self-intersecting, wrong place) silently breaks Stage 02 satellite pull and can mis-locate weather (05). Credit still uses registry area (good), but satellite signal can be garbage.

**Solution**  
Before using polygon bbox:

1. `shapely` validity check (`is_valid` / `make_valid` if safe)  
2. Compare geometry-derived ha vs `field_area_ha` — if ratio outside **0.5×–2.0×**, **do not** trust polygon for satellite bbox  
3. Fallback: point + adaptive buffer (A1)  
4. Stamp `geometry_source`: `polygon` | `polygon_rejected_fallback_point` | `point`

**How (concrete)**  
1. Small helper in `geometry_utils.py`: `validate_farm_geometry(geom, field_area_ha) -> (ok, reason, area_ha)`.  
2. Call from continuous collect path before bbox.  
3. Persist `geometry_qa` on assessment / satellite meta.

**Decision:** [x] Approve  [ ] Change  [ ] Defer  

---

### A3 — Stamp snap + geometry provenance on the assessment

**Issue**  
Start date and bbox choice are not written clearly onto the saved assessment. Later you cannot tell which rule produced a historical score.

**Why change**  
Needed for lender audit, re-scoring after rule changes, and debugging “why did this farm get this window.”

**Solution**  
Add a small `geospatial_prep` (or `stage_01`) block on the assessment, e.g.:

```json
{
  "snap_logic_version": "v1_fixed_anchors",
  "season_anchor_used": "kharif_jun15",
  "window_start": "2023-06-15",
  "window_end": "2026-07-13",
  "geometry_source": "point",
  "buffer_km_used": 0.15,
  "field_area_ha_registered": 0.4,
  "field_area_ha_geometry": null,
  "eco_region": "..."
}
```

**How (concrete)**  
1. Build dict in `main` / collector after snap + bbox.  
2. Attach to assessment payload before Mongo save.  
3. No change to scoring math.

**Decision:** [x] Approve  [ ] Change  [ ] Defer  

---

### A4 — Use `agro_profile` / hints in Stage 03 (Stage 01 already produces them)

**Issue**  
Stage 01 computes eco-region + passes `sowing_date_hint` / `crop_hint` / `agro_profile` into `detect_cycles`, but Stage 03 only logs them (`hints_applied: false`).

**Why change**  
National CVI thresholds miss low-canopy / regional crops → under-detected cycles → lower credit. Plumbing already exists; last mile is missing.

**Solution (minimal, Stage 01+03)**  
Do **not** hard-override cycle dates. Soft use only:

- From `agro_profile`: nudge peak-CVI floor / expected cycles-per-year by eco band (small lookup table)  
- From `sowing_date_hint`: bias sow-walk toward nearby dates (±N days preference), not force  
- Set `hints_applied: true` + which knobs moved  

**How (concrete)**  
Most code lands in Stage 03 (`crop_cycle_detector.py`). Stage 01 change is only: ensure eco profile shape is stable and documented. Implement fully when we do Stage 03 actions — list here so you see the coupling.

**Decision:** [x] Approve (do with Stage 03)  [ ] Change  [ ] Defer  

---

### A5 — Dynamic monsoon-onset snap (replace fixed Jun 15)

**Issue**  
Lookback always snaps to fixed **Jun 15 / Oct 15**, regardless of early/late monsoon year.

**Why change**  
Late/early monsoon can clip real sowing by 2–4 weeks → bad cycle starts and weather windows.

**Solution**  
Later: shift Kharif anchor ±1–2 weeks from a small onset table or IMD feed; keep Rabi Oct 15 unless product wants otherwise. Stamp which onset rule was used (A3).

**How**  
Major / research-ish. Needs data source choice + product buy-in. **Defer** until A1–A3 land unless you prioritize monsoon years now.

**Decision:** [ ] Approve now  [x] Defer (recommended)  [ ] Drop  

---

### A6 — ICAR/NARP spatial join instead of hand LGD map

**Issue**  
Eco labels still rely on lat/lon boxes + LGD state hints, not a full agro-climatic shapefile join.

**Why change**  
More accurate regional calibration for A4 thresholds.

**Solution**  
One-time GIS join to published zone shapefile; replace or back `infer_agro_ecoregion`.

**How**  
Major (data asset + packaging in Docker). **Defer** until A4 proves eco keys are consumed.

**Decision:** [ ] Approve now  [x] Defer (recommended)  [ ] Drop  

---

## Recommended order for Stage 01 (approved)

| Step | ID | Effort | Behavior change? |
|------|-----|--------|------------------|
| 0 | D1–D3 | Docs only | No |
| 1 | A3 | Small | No (metadata only) — safest first |
| 2 | A2 | Medium | Yes (bad polygons fall back to point) |
| 3 | A1 | Medium | Yes (tighter bboxes for small point farms) |
| 4 | A4 | With Stage 03 | Yes (cycle thresholds) |
| 5 | A5, A6 | Later | Yes |

---

## Your call

Reply with decisions, e.g.:

```text
A1 Approve
A2 Approve
A3 Approve
A4 Approve with Stage 03
A5 Defer
A6 Defer
D1–D3 Approve (doc fix)
```

Or rewrite any solution/how. When Stage 01 is settled, we do the same **Issue → Solution → How** sheet for **Stage 02**.
