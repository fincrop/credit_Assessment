# Multi-Farm Assessment — Implementation Plan (index_v5)

**Approach:** a thin **orchestrator + aggregation layer** over the *unchanged*
single-farm pipeline. Each plot is scored individually by the existing pipeline;
a new aggregator combines the per-farm indices into a farmer-level index. Nothing
in the analysis stages is modified. One job per farmer; the assessment *document*
grows.

```
farm_info.farms[]  ──▶  MultiFarmAssessor           (new, orchestrator)
                          for plot in farms[]:
                             assess_farmer(geometry=plot, …)   ← EXISTING pipeline, untouched
                          FarmerAggregator.aggregate(per_farm) ← new, the methodology
                        ──▶ { farmer_level, farm_assessments[] }
                        ──▶ save_multi_farm_assessment(farmer_id, …)
```

## 1. New / changed files

| File | Place at | Action | Tested |
|---|---|---|---|
| `farmer_aggregator.py` | `assessment/farmer_aggregator.py` | **new** — aggregation methodology | ✅ full |
| `multi_farm_assessor.py` | `assessment/multi_farm_assessor.py` | **new** — orchestrator | ✅ (mock pipeline) |
| `mongodb_helper.py` | (existing) | **+`save_multi_farm_assessment`** | ✅ compile |
| `config_multifarm_additions.py` | merge into `PipelineConfig` | **new** merge block | ✅ |
| `api/job_runner.py` | (existing) | **1 branch** — route to multi-farm when `farms[]` present | — |
| `main.py` | (existing) | **none** — `assess_farmer` already takes a boundary | — |

## 2. `farm_info` schema (one document per farmer_id)

Your proposed shape is right. The aggregator needs a few explicit fields per plot
(marked **[agg]**) — make sure the frontend normalizer populates them:

```jsonc
{
  "farmer_id": "…",                       // unique index (unchanged)
  "name": "…", "mobile": "…",
  // ── assessment envelope (kept for backward-compat / legacy single-farm) ──
  "latitude": .., "longitude": .., "geometry": {…}, "field_area_ha": ..,
  "state_lgd_code": "..", "district_lgd_code": "..", "crop": "..", "sowing_date": "..",
  "farmer_benefits": { "pm_kisan_enrolled": true|false|null,      // TRI-STATE
                       "has_crop_insurance": true|false|null },
  "source": "agristack|journey", "status": "..", "parcel_ingest_stats": {…},

  // ── full multi-plot inventory (NEW) ──
  "farms": [{
    "farm_id": "..", "farm_name": "..",
    "survey_number": "..", "sub_survey_number": "..",
    "village_lgd_code": "..", "district_lgd_code": "..", "sub_district_lgd_code": "..",
    "area_ha": .., "area_unit": "ha",
    "geometry": {…},                        // GeoJSON Polygon | MultiPolygon
    "centroid": { "lat": .., "lng": .. },   // [agg] used if geometry pull needs a point
    "primary_crop": "..",                   // [agg] crop hint + diversification
    "sowing_date": "..",
    "included_in_assessment": true,         // [agg] false ⇒ stored, not scored
    "is_ror_owner": true|false,             // [agg] false ⇒ leased/cultivator ⇒ down-weighted
    "ownership_share": 1.0,                  // [agg] optional; else derived from joint_owners
    "joint_owners": [ "…name…" ],           // [agg] share = 1/(1+len) when no explicit share
    "owner_name_ror": "..", "land_usage_type": "..",
    "source_plot": {…}                      // optional slim raw
  }],
  "farmer_profile": { "gender": "..", "dob": "..", "farmer_category": "..", … }
}
```

**Normalizer mapping (frontend `farmInfoSchema.ts`):**
- `is_ror_owner`: true when the farmer is the ROR owner (e.g. `owner_name_ror` matches
  the farmer identity), false when they cultivate but aren't the recorded owner.
- `ownership_share`: explicit if AgriStack provides it; else omit and let the aggregator
  derive `1/(1+len(joint_owners))`.
- `district_lgd_code` per plot: **required for diversification + per-plot cohort**.
- `included_in_assessment`: from your existing `buildClusteredFarmFields` (kept), but note
  per-farm scoring no longer *needs* the distance cluster — it's only used to mark plots
  you deliberately exclude. Prefer including all owned plots.

`get_farm_by_id(farmer_id)` is unchanged — it returns this whole doc; the orchestrator
reads `farms[]` from it.

## 3. Backend integration (the only wiring)

In **`api/job_runner.py`**, route based on whether the doc has plots:

```python
from assessment.multi_farm_assessor import MultiFarmAssessor

farm_info = mongo.get_farm_by_id(farmer_id)
if getattr(PipelineConfig, "MULTI_FARM_ENABLED", True) and (farm_info.get("farms") or []):
    result = MultiFarmAssessor(pipeline).assess_farmer_multi(farm_info, save_to_db=True)
else:
    result = pipeline.assess_farmer_from_db(farmer_id, ...)   # unchanged single-farm path
```

- `pipeline` is the existing `SatelliteBasedCreditPipeline` instance.
- `assess_farmer_multi` loops `farms[]`, calls the **unchanged** `pipeline.assess_farmer(...)`
  per owned plot (`save_to_db=False`), aggregates, and persists the farmer-level doc.
- Per-plot failures are caught and recorded (one bad plot never fails the farmer).
- Tri-state benefits flow through unchanged (`farmer_benefits` on the doc).

**Cost:** 3–10 scalar `reduceRegion` pulls per farmer. The existing weather/satellite
Mongo caches already dedupe co-located plots (same rounded lat/lon → cache hit). A hard
`MULTI_FARM_MAX_PLOTS` cap (default 12) guards outliers. GEE-batching all a farmer's plots
into one call is a **later** optimization, not needed now.

## 4. Aggregation methodology (`farmer_aggregator.py`)

Per-sub-index, because they combine differently:

| Sub-index | Farmer-level rule |
|---|---|
| **vigor / stability / weather / data_confidence** | tenure+area-weighted mean (`weight = area × tenure_factor`) |
| **landuse** (Capacity) | weighted-mean base **+ portfolio bonus** (0 for 1 plot; grows with #plots × area) |

Then: compose the 4 substantive sub-indices with the same `SUBINDEX_WEIGHTS` →
**+ diversification bonus** (crop/geo/season/count spread, ≤5) → **+ benefits bonus**
(tri-state, positive-only) → apply the **farmer data-confidence gate** → 0–100 index +
risk category + reason codes.

**Tenure weighting** (`tenure_factor`): sole owner 1.0; joint `1/(1+n)`; leased/non-owner
× `TENURE_LEASE_FACTOR` (0.35); excluded 0.

**Verified properties:** one bad plot doesn't tank a strong farmer; leasing a weak plot
reduces its drag; a diversified holding scores higher; a single plot gets neither
portfolio nor diversification bonus (no double-count).

## 5. Result document (persisted per farmer)

```jsonc
{
  "farmer_id": "..", "index_version": "index_v5", "method": "multi_farm_aggregate_v5",
  "assessment_type": "multi_farm",
  "farmer_level": {
    "index_score": 77.6, "raw_index": 82.3, "risk_category": "LOW",
    "confidence_gate": 0.94,
    "sub_indices": { "landuse": .., "vigor": .., "stability": .., "weather": .., "data_confidence": .. },
    "weights": {…}, "weak_sub_indices": [..],
    "diversification": { "score": .., "bonus": .., "n_crops": .., "n_districts": .. },
    "benefits": { "bonus": .., "conferred": [..], "pm_kisan": true|false|null, … },
    "portfolio_bonus": ..,
    "reason_codes": [ { "code": "..", "message": "..", "polarity": "positive|negative|caveat|neutral" } ],
    "n_plots_total": .., "n_plots_scored": .., "n_plots_owned": ..,
    "total_scored_area_ha": .., "weather_shared": true|false
  },
  "farm_assessments": [
    { "farm_id": "..", "area_ha": .., "tenure_factor": .., "included": true,
      "crop": "..", "district": "..", "index_score": .., "risk_category": "..",
      "sub_indices": { … scalars … }, "reason_codes": [..] },
    { "farm_id": "..", "included": false, "skipped_reason": "excluded_or_no_tenure" }
  ],
  "calibration": { "index_version": "index_v5", "weights": {…}, "per_farm": [...],
                   "outcome_label": null, "pd_estimate": null }
}
```

`slim_assessment_for_api` passes these top-level keys through (it only strips
`satellite_data`); confirm `farmer_level` + `farm_assessments` aren't dropped.

## 6. Frontend upgrades

Design the index_v5 dashboard redesign with a **per-farm dimension** from the start so
you don't retrofit:

- **`useRiskView(payload)`** selector normalizes to one shape and detects source:
  `farmer_level` (multi-farm) → `risk_assessment` (single-farm) → `credit_assessment` shim.
  Every tab and the print report read this one shape.
- **Overview hero** shows the **farmer-level** index + category + gate, with a chip
  "N plots scored" and the diversification note.
- **New `PerFarmBreakdown` component**: an expandable list — one row per plot with its
  index, risk chip, crop, area, tenure badge (Owned / Joint / Leased), and top reason.
  Excluded/failed plots shown greyed with the skip reason (data-truthfulness).
- **Sub-index bars + gate** render the *farmer-level* sub_indices; clicking a plot swaps
  the bars to that plot's sub_indices (same presentational component).
- **LocationStrip / plots strip**: "6 plots stored · 4 scored · 1 leased · 1 excluded".
- **Print report**: farmer-level one-pager + a compact per-plot table.

TypeScript types are in `assessment.multifarm.d.ts` (below) — add to `assessment.ts`.

## 7. Sequencing

1. Add `farmer_aggregator.py` + `multi_farm_assessor.py` (+ config block + Mongo method).
2. Extend the frontend ingest normalizer to write `farms[]` with the **[agg]** fields
   (this is your AgriStack multi-plot storage plan — ship it first; it's the input).
3. Add the one `api/job_runner.py` branch.
4. Dashboard: `useRiskView` + `PerFarmBreakdown` + hero wired to `farmer_level`.
5. Smoke: enqueue a farmer with `farms[]` → confirm `farmer_level` + `farm_assessments[]`.

## Out of scope (this pass)
- GEE per-farmer batching (later optimization).
- Per-parcel *jobs* (still one job per farmer).
- Cohort accumulation cron.
