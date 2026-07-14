# Backend Stage 02 — Satellite Observation Grid (Updated Deep-Dive)

## Purpose & Role

This is the most expensive stage in wall-clock and dollar terms (GEE compute or STAC/COG network I/O), and it is the stage that converts "a bbox and a date range" into the actual signal every later stage reasons over. Its job is to produce a *regular* calendar grid — explicit missing bins rather than irregular scene dates — so Stage 03's cycle detector doesn't have to also solve "what does a gap mean." Any noise or bias introduced here (cloud contamination, coarse index approximation, wrong resampling) propagates through cycle detection, weather-window alignment, performance scoring, and ultimately the credit score, without any later stage having a way to detect or correct it.

## Present Condition — How It Actually Works Today

1. **Provider selection:** `SATELLITE_PROVIDER` env defaults to `gee`; GEE init failure falls back to STAC (Microsoft Planetary Computer, `sentinel-2-l2a`).
2. **Cloud filtering is not one number.** Instance attributes (not the single `PipelineConfig.MAX_CLOUD_COVER=60`) actually gate the query: Kharif months (Jun–Oct) allow up to 80% cloud cover in the STAC day filter, Rabi/other months cap at 60%, and the continuous/GEE filter uses 70%. This tri-modal cap exists because Kharif (monsoon) scenes are cloud-heavy by nature — a flat 60% cap would starve the Kharif window of usable scenes entirely.
3. **Binning:** fixed 10-day bins (`CONTINUOUS_SCENE_INTERVAL_DAYS`), half-open, lowest-cloud scene per bin wins; empty bins get an explicit `missing: true` placeholder with NaN indices rather than being dropped.
4. **Index computation:** NDVI/EVI/NDMI/PSRI/NDRE/NDWI formulas are implemented, but the **GEE path materializes mainly NDVI/EVI/NDMI**; PSRI/NDRE/NDWI are largely NaN placeholders unless the STAC/COG path is used. This means the "6-index" story in the schema is really a "3-index-reliable, 3-index-best-effort" story depending on which provider actually served a given farm.
5. **Validation gate in `main`:** fewer than 12 valid (non-missing) bins → hard `ValueError`, assessment fails outright; fewer than 20 → soft warning but proceeds.
6. **`force_fresh_download = True`** is hardcoded — every assessment re-downloads and reprocesses the full multi-year Sentinel-2 series even if an identical cache key exists from an hour ago. Cache writes still happen (so the cache table grows), but reads are dead code. This was marked "temporary for testing" but is live in the current codebase.

## Ground Reality — What This Means Operationally

- **Monsoon cloud cover is the defining constraint for Indian ag remote sensing, not an edge case.** Kharif (Jun–Oct) is precisely when farmers most need signal (sowing, vegetative growth, flowering under monsoon rainfall) and precisely when optical satellites see the least. The 80% Kharif cloud allowance is a pragmatic necessity, but it means many "valid" Kharif scenes still have meaningful cloud contamination (shadow, thin cirrus not always flagged by the SCL/QA60 mask) baked into a per-pixel index value that looks clean numerically but isn't agronomically clean. This is a known, structural limitation of Sentinel-2-only pipelines in monsoon-dependent geography — not something a threshold tweak fully solves.
- **`force_fresh_download=True` is a real production cost problem, not a cosmetic flag.** GEE compute has usage quotas and STAC/COG reads have real egress and latency cost; a 3-year, 10-day-binned, multi-index series re-pulled on every single assessment (including repeat assessments of the same farm within days, which happens routinely for re-scoring/appeals) is the single largest avoidable cost and latency driver in the whole pipeline, and it directly determines whether Stage 09's job queue can throughput more than one assessment at a time on a given deployment tier.
- **PSRI/NDRE/NDWI being provider-dependent is invisible unless someone diffs GEE vs STAC output.** If a deployment silently falls back from GEE to STAC mid-operation (e.g., after a GEE auth/quota hiccup), some farms in a batch get 6 usable indices and others get 3, with no signal to Stage 03/06 that the input richness differs. Any future feature (e.g., NDRE-based nitrogen-stress proxy) built assuming full-index availability will quietly degrade for the GEE-served majority.
- **10 m native resolution vs. small Indian holdings compounds the Stage 01 buffer issue.** Even with a well-buffered bbox, a 10 m pixel on a sub-hectare, irregularly shaped plot still mixes bunds, field edges, and sometimes adjacent crops into the aggregate index mean used per scene — this is a sensor-physics limit that no downstream stage can fully undo, and it's worth remembering when interpreting confidence scores in Stage 03/06 as more precise than the input actually supports.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Fix the cache-read bypass first — it's the highest-leverage, lowest-risk change available.** Re-enabling cache reads (gated by `SATELLITE_CACHE_TTL_DAYS`, already 30 days) with a targeted "force refresh" flag exposed per-request (not global) would cut cost/latency dramatically for the common case of repeat/appeal assessments while still allowing an explicit fresh pull when genuinely needed (e.g., after a season boundary passes).
2. **Move toward a proper cloud/shadow probability mask rather than a scene-level percentage threshold.** Sentinel-2's `s2cloudless` probability layer (already used inside GEE's own cloud-masking recipes) or the STAC-side SCL band lets you mask at the *pixel* level within an otherwise-accepted scene, instead of accepting/rejecting whole scenes on an aggregate cloud percentage. This directly reduces the "numerically clean but agronomically contaminated" problem above, and is a well-established industry pattern (used in Sentinel Hub's and Google's own recommended NDVI compositing workflows).
3. **Blend in Sentinel-1 SAR during monsoon gaps.** SAR penetrates cloud cover, and India-specific ag-monitoring literature (and operational systems like the ISRO/Bhuvan crop-monitoring stack) routinely blend Sentinel-1 backscatter (VV/VH) with Sentinel-2 optical to fill exactly the Kharif cloud gaps this pipeline is currently just tolerating with a higher cloud-cover cap. This would be a genuinely major addition, not a config tweak, but it directly targets the biggest structural gap in the current design.
4. **Normalize index availability across providers**, either by implementing PSRI/NDRE/NDWI on the GEE path to parity with STAC, or by explicitly flagging `indices_available: [...]` per assessment so Stage 06/08 can gracefully degrade rather than silently operating on NaNs.
5. **Consider a higher-resolution commercial fallback for very small/high-value plots** (e.g., PlanetScope's ~3 m daily imagery) as an optional paid tier for cases where 10 m Sentinel-2 is known to be inadequate (e.g., plots under ~0.3 ha) — this is how several ag-fintech platforms handle the smallholder resolution gap without abandoning the free Sentinel-2 backbone for everyone else.
6. **Persist slim index series only**, not full scene blobs, in the cache — reduces Mongo document size and makes the eventual cache-read fix (item 1) cheaper to serve.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Env-gate or remove `force_fresh_download=True`; re-enable cache reads | Directly cuts per-assessment cost/latency; unblocks Stage 09 throughput |
| Quick win | Delete/archive the ~1200 commented legacy seasonal collector lines | Reduces noise for anyone maintaining the active continuous path |
| Quick win | Unify the three cloud-cover numbers (60/70/80) into one documented `PipelineConfig` block instead of instance attrs vs. config constant | Prevents future contributors from "fixing" cloud thresholds in the wrong place |
| Medium | Expose `indices_available` per assessment so downstream stages degrade gracefully instead of silently consuming NaNs | Protects Stage 06/08 from unexplained score variance across providers |
| Medium | Pixel-level cloud/shadow masking (s2cloudless / SCL) instead of scene-level percentage gating | Reduces contaminated-but-accepted Kharif scenes reaching Stage 03/06 |
| Major | Sentinel-1 SAR blending for monsoon cloud gaps | Addresses the structural Kharif visibility gap rather than tolerating it via higher cloud caps |
| Major | Optional high-res (PlanetScope-class) fallback for sub-0.3 ha plots | Targets the resolution mismatch for the smallholder majority this system is meant to score |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stage 01 geometry + snapped dates |
| Downstream | Stage 03 `detect_cycles`; Stage 04 scene windows; Stage 05 `interval_days` |
| Shared Mongo | `satellite_stats_cache` keyed by SHA-256 from `main._build_satellite_cache_key` |

---
*This document supersedes the original `02-satellite-observation-grid.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
