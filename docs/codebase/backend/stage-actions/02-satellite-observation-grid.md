# Stage 02 — Action plan (Issue → Solution)

**Status:** Awaiting your decisions (approve / change / defer each item)  
**Scope:** Satellite observation grid (Sentinel-2 fetch, binning, indices, cache)  
**Depends on Stage 01:** A1/A2 change bbox quality feeding this stage; implement Stage 01 first or in parallel only for docs  
**Related code:** `data_acquisition/satellite_collector.py`, `main.py` (cache key / validation), `config.py` (`MAX_CLOUD_COVER_*`, `CONTINUOUS_SCENE_INTERVAL_DAYS`), Mongo `satellite_stats_cache`

---

## Already done — no code change (docs only)

| ID | What docs still say | Reality | Action |
|----|---------------------|---------|--------|
| D1 | `force_fresh_download = True` hardcoded | Env-gated `SATELLITE_FORCE_FRESH` (default off → cache reads work) | Update `02-…md` Present Condition §6 |
| D2 | Cloud caps live only as instance attrs / single `MAX_CLOUD_COVER=60` | `MAX_CLOUD_COVER_KHARIF/RABI/CONTINUOUS` in `PipelineConfig`; collector reads them | Update Present Condition §2 |
| D3 | ~1200 lines of commented seasonal collector | Removed in cleanup | Drop that recommendation from stage doc |

---

## Proposed changes — decide per item

### B1 — Stamp `indices_available` + provider on the assessment

**Issue**  
Schema looks like six indices (NDVI/EVI/NDMI/PSRI/NDRE/NDWI), but the **GEE path** mainly fills NDVI/EVI/NDMI; PSRI/NDRE/NDWI are often NaN. STAC may fill more. Downstream (06/08) cannot tell “missing index” from “zero signal.”

**Why change**  
Silent provider fallback (GEE → STAC) changes input richness mid-batch with no flag → unexplained score variance and future features (e.g. NDRE stress) fail quietly on GEE farms.

**Solution**  
After collect, compute which index means are finite across the series (or per-scene majority), and attach:

```json
{
  "satellite_provider": "gee",
  "indices_available": ["NDVI", "EVI", "NDMI"],
  "indices_sparse": ["PSRI", "NDRE", "NDWI"]
}
```

Downstream may ignore sparse indices until implemented; no scoring change required in this item.

**How (concrete)**  
1. Helper on continuous result in `satellite_collector` or `main` after STEP 1.  
2. Persist on assessment / satellite block.  
3. Optional log warning when provider is GEE and secondary indices are empty.

**Decision:** [ ] Approve  [ ] Change  [ ] Defer  

---

### B2 — Per-request force-fresh (not only global env)

**Issue**  
Cache bypass is only via process env `SATELLITE_FORCE_FRESH`. Operators/appeal flows cannot refresh one farm without affecting the whole process.

**Why change**  
Repeat assessments should hit cache (cheap); appeals / post-season refresh need one-off fresh pull.

**Solution**  
Keep env as global override. Add optional job/API flag `force_fresh_satellite: true` (or query param) that skips cache **for that job only**.

**How (concrete)**  
1. Plumb flag through `jobs` → `job_runner` → `assess_farmer*` / cache read branch in `main`.  
2. Document in `.env.example` / API: env = process-wide; flag = per job.  
3. Default remains cache-on.

**Decision:** [ ] Approve  [ ] Change  [ ] Defer  

---

### B3 — Pixel-level cloud/shadow masking (beyond scene % gate)

**Issue**  
Acceptance is mostly scene-level cloud % (60/70/80). Kharif scenes can pass the cap but still include contaminated pixels in the AOI mean.

**Why change**  
“Numerically clean” NDVI that is agronomically dirty → false cycle peaks/drops and performance anomalies.

**Solution**  
Strengthen masking already partially present on GEE (QA60/SCL):

- Prefer SCL / s2cloudless-style pixel masks for means inside the bbox  
- STAC path: apply SCL (or equivalent) when reading COGs, not only `eo:cloud_cover` filter  
- Keep seasonal % caps as a first gate; pixel mask as second gate  

**How (concrete)**  
1. Audit GEE `_mask` / reduceRegion path vs STAC COG path for parity.  
2. Document which mask recipe is live (`cloud_mask_version` on assessment — pairs with Stage 01 A3).  
3. Unit/smoke test: cloudy fixture → mean uses unmasked pixels only.

**Decision:** [ ] Approve  [ ] Change  [ ] Defer  

---

### B4 — GEE index parity for PSRI / NDRE / NDWI (optional)

**Issue**  
Formulas exist; GEE reduce often leaves secondary indices empty while STAC fills them.

**Why change**  
Only needed if we plan features that consume those indices soon. Otherwise B1 is enough.

**Solution**  
Either implement band math on GEE path to match STAC, **or** explicitly document “primary trio only on GEE” and skip parity work.

**How**  
Non-trivial GEE expression work + validation vs STAC on same AOI. Prefer **Defer** unless you need NDRE/PSRI this quarter.

**Decision:** [ ] Approve now  [ ] Defer (recommended if B1 approved)  [ ] Drop  

---

### B5 — Slim cache payloads (index series only)

**Issue**  
Cache may store heavier scene blobs than needed for replay.

**Why change**  
Smaller Mongo docs → cheaper reads once cache is primary path; less bloat as volume grows.

**Solution**  
Cache only what Stage 03+ need: dates, missing flags, index means (and cloud %), not full rasters/band dumps.

**How**  
1. Inspect current `satellite_stats_cache` document shape.  
2. Write slim schema; keep read compatibility for old fat docs if needed.  
3. No change to live scoring if slim fields are complete.

**Decision:** [ ] Approve  [ ] Change  [ ] Defer  

---

### B6 — Sentinel-1 SAR blend for monsoon gaps

**Issue**  
Optical-only grid leaves many Kharif bins `missing` or cloudy; higher cloud % only tolerates the problem.

**Why change**  
Structural India monsoon gap; SAR can fill when optical cannot.

**Solution**  
Major: add S1 VV/VH (or ratio) series aligned to same 10-day bins; use as gap-fill / auxiliary signal for Stage 03 — not a config tweak.

**How**  
New collector path, bin alignment, detection API changes. **Defer** until B1–B3 land and product wants monsoon investment.

**Decision:** [ ] Approve now  [ ] Defer (recommended)  [ ] Drop  

---

### B7 — Commercial high-res fallback (PlanetScope-class) for tiny plots

**Issue**  
10 m Sentinel-2 is coarse for sub-~0.3 ha plots even with Stage 01 adaptive buffer.

**Why change**  
Optional paid tier for high-value/smallholder edge cases.

**Solution**  
Out of scope for core open pipeline unless you have a vendor contract and budget.

**Decision:** [ ] Approve now  [ ] Defer  [x] Drop (unless product mandates)  

*(Default recommendation: Drop / out of scope — change if you disagree.)*

---

## Recommended order for Stage 02

| Step | ID | Effort | Behavior change? |
|------|-----|--------|------------------|
| 0 | D1–D3 | Docs only | No |
| 1 | B1 | Small | No (metadata) |
| 2 | B2 | Small | Yes (per-job refresh) |
| 3 | B5 | Small–medium | No if schema-compatible |
| 4 | B3 | Medium | Yes (cleaner pixel means) |
| 5 | B4 | Medium | Only if needed |
| 6 | B6 | Major | Later |
| 7 | B7 | Major / commercial | Drop unless mandated |

---

## Coupling notes

- Stage 01 **A1/A2** improve *what* AOI is scored; Stage 02 improves *how clean* the pixels inside that AOI are.  
- Do not re-enable hardcoded force-fresh; cache-on is already correct.  
- SAR (B6) should wait until adaptive buffer + pixel masks exist — otherwise SAR fills gaps on a still-contaminated neighbor-heavy bbox.

---

## Your call

Reply with decisions, e.g.:

```text
B1 Approve
B2 Approve
B3 Approve
B4 Defer
B5 Approve
B6 Defer
B7 Drop
D1–D3 Approve
```

Or rewrite any solution/how. After Stage 02 is settled → **Stage 03** next.
