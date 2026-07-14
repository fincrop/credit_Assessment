# Backend Stage 05 — Weather Analysis (Updated Deep-Dive)

## Purpose & Role

Converts a point-based daily meteorology feed into a stage-aware risk score that feeds `weather_safety` (8 of 100 credit points) and, more importantly for narrative purposes, drives much of Stage 08's explainability content (extreme-event narratives, thresholds used). Because it operates per-cycle and is stage-aware (vegetative/flowering/grain-fill/ripening), it's one of the more agronomically literate parts of the pipeline — but it inherits Stage 03/04's cycle boundaries and crop labels wholesale, so its accuracy ceiling is capped by those upstream stages.

## Present Condition — How It Actually Works Today

1. **Source:** NASA POWER daily API — free, no API key, point-based at farm centroid; parameters T2M/T2M_MAX/T2M_MIN/PRECTOTCORR/RH2M/WS2M.
2. **Per-cycle fetch:** for each cultivated cycle in `season_results`, pulls the series for `[start_date, end_date]`.
3. **Growth-stage mapping:** generic fractional stages by default, or crop-specific stage timing from `CropGrowthCurves` when a named crop exists (i.e., benefits from Stage 04 classification when it's on).
4. **Dynamic thresholds** (need ≥21 days of observations): heat ~91st percentile of Tmax (floor 36°C, cap 44°C, ≥3-day spell), cold ~10th percentile of Tmin (floor 4°C, cap 10°C, ≥3-day spell), heavy rain and drought similarly percentile-based with floors and an interval-scaling factor (1.2) tied to Stage 02's `interval_days`.
5. **Static fallbacks** when dynamic thresholds aren't viable (short series): heat 40°C, cold 10°C, drought 30 days at ≤2mm/day.
6. **Risk scoring:** weighted blend — event frequency/severity 40%, critical-stage events 30%, rain deficit/excess 15%, drought fraction 10%, temp extremity 5% — aggregated into `weather_risk_score`, inverted for credit's `weather_safety`.

## Ground Reality — What This Means Operationally

- **NASA POWER is a genuinely solid choice for a free, no-key, globally consistent data source, but it is a ~0.5° gridded reanalysis-derived product, not true point observation.** For India, this means real local microclimate effects — rain-shadow zones, coastal vs. inland gradients within a single district, elevation effects in hill agriculture — get smoothed out. For most plains agriculture this is an acceptable approximation; for farms in topographically complex or coastal-transition regions, POWER's spatial coarseness can materially understate or overstate localized extreme events (a farm that experienced a genuine local hailstorm or cloudburst may show unremarkable POWER-derived weather that day).
- **Percentile-based dynamic thresholds are a real strength relative to flat national thresholds** (this is explicitly called out as better-calibrated than "40°C everywhere" in the original doc, and that's accurate) — but the "≥21 observations" requirement means very short or newly-registered cycles fall back to static thresholds that are calibrated for "somewhere in India" rather than the specific farm's actual climate, right at the point in a farmer's history where accurate risk assessment probably matters most (first-time borrowers, new land).
- **Point-based sampling at centroid ignores multi-parcel or elongated field geometry** — already flagged as a known limitation, but worth restating: a farm whose polygon spans a meaningful lat/lon range (common for larger holdings or non-contiguous parcels under one farmer ID) gets a single weather value representing only its centroid, understating spatial risk heterogeneity.
- **Network dependency on a single external API with no described local caching (unlike Stage 02's satellite cache)** means a POWER outage or rate-limit event directly fails or partially fails the weather block for whatever assessments are in flight at that moment — for a lending pipeline, an unplanned dependency outage silently degrading credit scores (rather than failing loudly and consistently) is worth treating as an operational risk, not just a technical inconvenience.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Blend in IMD gridded data (0.25° rainfall, temperature grids) as a secondary/higher-resolution source for India specifically**, since IMD's gridded products are calibrated against India's own dense station network and are already flagged as a "major" recommendation in the original doc — this is the most India-appropriate accuracy upgrade available, better suited to the domestic climate service than a global reanalysis product like POWER.
2. **Consider ERA5-Land (0.1° resolution, hourly) as a middle-ground blend or fallback** — meaningfully finer spatial resolution than POWER's ~0.5° grid, and widely used in agricultural risk modeling internationally; blending POWER (reliable uptime, simple) with ERA5-Land (finer grid) for cross-validation on flagged extreme events would catch cases where POWER's coarseness likely missed something real.
3. **Cache POWER responses by (lat, lon, date-range) in Mongo, mirroring Stage 02's satellite cache pattern** — already flagged as a "quick win" in the original doc, and it remains one, since it both reduces external dependency risk and speeds up repeat/appeal assessments the same way fixing Stage 02's cache-read bypass would.
4. **Add a circuit-breaker/retry-with-backoff and an explicit "weather block degraded" flag on the assessment** rather than allowing partial silent failure, so Stage 08's narrative and Stage 07's credit consumers can distinguish "genuinely low weather risk" from "weather data was unavailable, treat this component cautiously."
5. **Parcel-weighted weather sampling for large or multi-geometry farms** (already flagged as medium) — for a farm with real polygon extent, sampling a few representative points across the polygon (not just centroid) and averaging would reduce the point-sampling blind spot for larger holdings.
6. **Publish the exact `_calculate_cycle_risk` formula alongside unit tests on synthetic weather series** (already flagged as a quick win) — this is as much a trust/audit item as an engineering one: a lender or regulator asking "how exactly does a heatwave reduce this farmer's score" deserves a documented, tested formula rather than reverse-engineering it from code.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Cache POWER responses by (lat, lon, date range) in Mongo | Reduces external dependency risk and repeat-assessment latency, same rationale as Stage 02's cache |
| Quick win | Document exact risk formula next to `_calculate_cycle_risk` with synthetic-series unit tests | Supports audit/regulatory explainability requests that Stage 08 narratives currently can't fully back up |
| Medium | Add explicit "weather data degraded/unavailable" flag instead of silent partial failure | Lets Stage 07/08 consumers distinguish genuinely-safe weather from missing-data risk |
| Medium | Parcel-weighted sampling (multiple points, not just centroid) for larger/multi-geometry farms | Reduces spatial blind spot for exactly the farms where centroid-only sampling is weakest |
| Major | Blend IMD gridded data as a higher-resolution India-specific source | POWER's ~0.5° grid is coarser than what India's own domestic climate data can offer |
| Major | Blend/cross-validate with ERA5-Land (0.1° resolution) for flagged extreme events | Finer grid catches localized extremes POWER's coarseness may miss |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Stages 01 (location), 04 (cycle windows/crops) |
| Downstream | 07 `weather_safety`; 08 SHAP/LLM context |
| Shared | `interval_days` coupling to Stage 02 grid |

---
*This document supersedes the original `05-weather-analysis.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
