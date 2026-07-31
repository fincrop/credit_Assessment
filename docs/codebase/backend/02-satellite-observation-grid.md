# Backend Stage 02 — Satellite Observation Grid

## Purpose

Most expensive stage (GEE/STAC). Builds a regular 10-day observation grid with explicit missing bins for Stage 03+.

## Present condition (after 2026-07 enhancement)

1. Provider: `SATELLITE_PROVIDER` default `gee`; GEE failure → STAC.
2. Cloud caps from `PipelineConfig.MAX_CLOUD_COVER_KHARIF/RABI/CONTINUOUS` (80 / 60 / 70).
3. Fixed 10-day bins; lowest-cloud scene wins; empty bins → `missing: true`.
4. Assessment stamps `satellite_provider`, `indices_available`, `indices_sparse`, `cloud_mask_version` (`scl_qa60_v1`).
5. Cache **reads on** by default; skip via env `SATELLITE_FORCE_FRESH` or per-job `force_fresh_satellite`.
6. Cache **writes** store slim index grid (no fat blobs).
7. Hard fail if fewer than 12 valid bins; warn under 20.

## Done

| Item | Notes |
|------|--------|
| Env + per-job force-fresh | Global env and job/API flag |
| `indices_available` / provider | On assessment + collector result |
| Slim satellite cache | Index series only |
| Cloud-mask version stamp | Audit; GEE path uses SCL/QA60 |

## Missing / next

| Priority | Item |
|----------|------|
| Medium | Deeper STAC/GEE pixel-mask parity tests |
| Medium | Optional GEE parity for PSRI/NDRE/NDWI (only if features need them) |
| Major | Sentinel-1 SAR blend for Kharif cloud gaps |
| Deferred | PlanetScope-class high-res paid tier |

## Interfaces

Upstream: Stage 01 bbox + dates · Downstream: Stages 03–06 · Mongo: `satellite_stats_cache`

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
