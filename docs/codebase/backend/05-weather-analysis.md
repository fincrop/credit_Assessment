# Backend Stage 05 — Weather Analysis

## Purpose

Cycle-aligned NASA POWER weather → risk score → Stage 07 `weather_safety` and Stage 08 narratives.

## Present condition (after 2026-07 enhancement)

1. Source: NASA POWER daily at farm centroid (T2M*, PRECTOTCORR, RH2M, WS2M).
2. Per cultivated cycle; crop-specific stage fractions when a named crop exists.
3. Dynamic percentile thresholds when enough days; static fallbacks otherwise.
4. **POWER responses cached** (Mongo `weather_power_cache` when available + in-memory), key ≈ lat/lon (3 dp) + date range.
5. Result flags: `weather_degraded`, `weather_data_status` ∈ `{ok, degraded, unavailable}`.

## Done

| Item | Notes |
|------|--------|
| POWER cache | Reduces repeat latency / dependency thrash |
| Degraded / unavailable flag | Consumers can distinguish missing data vs low risk |

## Missing / next

| Priority | Item |
|----------|------|
| Medium | Unit tests for `_calculate_cycle_risk` on synthetic series |
| Deferred | Parcel-weighted multi-point sampling |
| Major | IMD gridded and/or ERA5-Land blend |

## Interfaces

Upstream: 01 location, 04 cycle windows/crops · Downstream: 07 `weather_safety`, 08 narratives

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
