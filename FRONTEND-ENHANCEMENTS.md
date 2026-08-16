# Frontend Enhancements — v6

**Status:** All eleven items complete. Three decisions still open (§15.2); two caveats a ✅ hides (§0.3).
**Date:** 2026-08-16
**Scope:** `frontend/` only. Backend v6 is complete (`BACKEND-ENHANCEMENTS.md`); this pass makes its output legible.
**Design reference:** `enhancements/Farmer Assessment Report (standalone).html` — adopted for its *visual language*, not its content. Several of its panels are fabrications the backend explicitly refuses to emit (§9.3).

---

## 0. Status

### 0.1 The plan, in plain language

Ordered so each item is shippable on its own and nothing depends on a later one.

| | Item | Status |
|---|---|---|
| 1 | One colour, one meaning | ✅ Done |
| 2 | Make the design system real | ✅ Done |
| 3 | Show what we refused to score | ✅ Done |
| 4 | Wire up the report data | ✅ Done |
| 5 | Show *why* the score is that number | ✅ Done |
| 6 | Show the field we actually saw | ✅ Done |
| 7 | Make the map a remote-sensing map | ✅ Done — one item descoped, see below |
| 8 | The report | ✅ Done — header slot open on FD-6 |
| 9 | Rebuild the dashboard around its reader | ✅ Done — IA yes, file split partial |
| 10 | The remaining panels | ✅ Done |
| 11 | Make it usable for everyone | ✅ Done |

### 0.2 What each item means

**① One colour, one meaning.** ✅ **Done.** There turned out to be *four* competing ramps, not three — `riskBgClass` in `lib/format.ts` was a fourth, colouring the risk pill independently of the gauge. All four are now one function.

- `lib/kbsScore.ts` rewritten as the single source. Bands re-stepped to the validated set (§5.2); each band gained `ink` (text-safe step, ≥ 5:1 on cream), `surface`, `border`, `indexMin/Max`, and `riskCategory`.
- `scoreColor(index)` is now the only score→colour entry point. `subScoreBarColor`'s 65/55 thresholds and `SummaryHero`'s inline 70/45 ternary are gone.
- `bandForRiskCategory()` maps the backend `LOW…VERY_HIGH` enum onto the same four bands, so a risk pill and the gauge can no longer disagree. `riskBgClass` and `riskPillClass` are deleted; `bandChipStyle()` replaces both.
- **Nine places painted text with the mark colour.** Fair (`#E5A614`) measures 1.92:1 on cream — it was unreadable as text and nobody had checked. Those now use `ink`.
- Gauge band labels enlarged (7–9px → 8–10px) and darkened off `stone-400`. These labels are the *mandatory* relief for Fair's contrast warning, so they are load-bearing, not decoration.
- `riskColor` and `formatRupees` deleted — both dead.

Verified: `tsc --noEmit` clean, `next build` succeeds. (`eslint` is broken repo-wide by the `brace-expansion: ^5.0.8` override in `package.json`, which minimatch can't consume — pre-existing, unrelated, worth a separate fix.)

**② Make the design system real.** ✅ **Done.** Tokens moved into Tailwind 4's `@theme`, so they generate real utilities rather than sitting in a `:root` block nothing referenced.

- `globals.css` rewritten: surfaces named by elevation (`paper` / `paper-raised` / `card`), rules (`rule` / `rule-soft` / `rule-strong`), a three-step ink scale, and three warm-tinted shadow levels. The old `:root` names survive as aliases for the plain-CSS rules further down the file.
- **All 242 hex literals gone** from `.tsx`. `border-[#E4DFD4]` → `border-rule` (139), `bg-[#F5F2EB]` → `bg-paper` (63 + 18 opacity variants), and the rest. Verified zero remaining. The only arbitrary hexes left are the AgriStack sandbox's dark JSON viewer — deliberately a dark code surface, internal tooling, out of scope.
- **107 uses of `text-stone-400` retired.** `#A8A29E` measures 2.3:1 on cream and was carrying 10px labels all over the app — a straight WCAG failure on the smallest text in the product. All now `text-ink-muted` (`#78716C`, 4.5:1+).
- **A fifth score ramp surfaced and was killed.** `SubIndexBars.tsx:74` had a fully inline `pct > 65 ? green : pct > 40 ? amber : red` with no named function, which is why item ① 's grep missed it. Now `scoreColor()`. Item ① 's "four ramps" count was four *named* ones; the true count was five.
- `lib/vizPalette.ts` — the validated chart palettes (vegetation sequential + discrete chips, the three-slot categorical, diverging, chart chrome), each annotated with its validator result and its usage rule.
- `lib/mapStyle.ts` — map marks in one place instead of 35 literals across four components, with `parcelStyle()` covering the assessed / warn / focused / **declared** / drawing states. The `declared` state (greyed + dashed) is what item ⑥'s footprint-divergence banner will draw against.
- Page plane got its ~2% radial per §6.2, and `prefers-reduced-motion` now zeroes every animation.

Verified: `tsc --noEmit` clean, `next build` compiles, and the built CSS confirms `--color-paper:#f5f2eb`, `--color-rule:#e4dfd4`, `--color-ink-muted:#78716c` with `.bg-paper` / `.border-rule` / `.text-ink-muted` utilities generated.

**③ Show what we refused to score.** ✅ **Done.** The backend's three terminal states now reach the screen as three distinct findings instead of one amber box.

- `lib/terminalState.ts` — one resolver, `SCORED` / `NOT_FARMLAND` / `UNOBSERVED` / `FAILED` / `PENDING`, for both full payloads and the slim per-plot records. The `skipped_reason` prefix is load-bearing (`not_agricultural:` and `insufficient_observation` are *exclusions*; only `error:` is a failure), and the resolver is now the only place that knows it.
- `RefusalPanel` — a distinct screen per refusal, each carrying its own evidence: observed land-cover class and confidence, or the weeks-observed / clear-coverage / longest-blind-gap / radar-only figures. **No gauge, no band arc, no greyed placeholder** — an empty dial beside a refusal eventually gets read as a zero.
- The insufficiency screen distinguishes its two causes, because they have different fixes: a parcel below the measurable floor needs the *boundary* re-drawn; a cloud-gapped window just needs a later observation. And it says the sentence outright: *"This is a statement about our view of the field, not about the field."*
- `ConfidenceStrip` / `GateBadge` / `FootprintBanner` — the gate is stated as what it did ("Score reduced 8% — limited observation") rather than as a bare `0.92` nobody can interpret. **Footprint substitution is a full-width banner**, not a footnote: when `geometry_substituted` is true, every number on the page describes different ground than the polygon on screen.
- `OmittedPanel` / `ColdStartPanel` — ready for the report route. An omitted panel states the backend's own reason in the slot where the panel would have been, so nobody quietly fills the gap later.
- Plot list exclusions read as findings ("Excluded — observed as water, not farmland") instead of the raw `not_agricultural:WATER` token, which made a legitimate exclusion look like a crash.
- Holding-level: "no plot could be scored" now breaks down by cause and states plainly *"This is not a low score. No number was produced."*

Types extended with the real backend shapes — `land_cover`, `parcel_viability`, `data_sufficiency`, `crop_verification`, `footprint`, `driver_captions` — read off the emitting Python, not guessed.

**`npm run smoke`** added: 33 assertions over the terminal-state resolver and the colour mapping, exiting non-zero on failure. These are the two things that are *silently* wrong when broken. It caught one real bug (the `error:` prefix strip left a leading space). All pass; `tsc` clean; `next build` compiles.

**④ Wire up the report data.** ✅ **Done.** `/v1/report/{farmer_id}` now has a typed client, an authorising proxy, and a contract test that fails the build when the two sides drift.

- `types/report.ts` — `report_payload_v1` mirrored field for field, with the omissions documented where a renderer will look for them.
- `app/api/report/[farmer_id]/route.ts` — proxy, for two reasons: the service key must never reach the browser, and **the pipeline API authenticates the service, not the user**. Ownership is enforced here before the upstream call; without it any authenticated user could read any farmer's report by editing the URL. Returns 404 rather than 403 on a non-owned farmer — a 403 confirms the farmer exists, which is itself a disclosure.
- `lib/reportClient.ts` — never throws; returns four distinguishable empty states (`not-configured` / `not-found` / `unavailable` / `error`), because collapsing them would repeat the mistake item ③ just fixed. Plus `hasSection()` and `trendLabel()`, which enforce the *no fabricated zero* rule at the call site.
- **Ownership extracted to `lib/ownerScope.isFarmerOwnedBy`** and the enqueue route repointed at it. It was inline in one route; a second copy in the report route would have drifted, and the drift is only ever discovered after a month of leaking one lender's farmer to another.

**`npm run check:contract`** — 26 assertions against **real payloads generated by calling `build_report_payload` directly**, not hand-written fixtures. It checks both directions (backend keys we don't type = silently dropped data; typed keys the backend never sends = rendering `undefined`), and pins the things a renderer must not get wrong: the scale is 300–900 not 300–950, `positioning` is `agronomic_risk_index`, the four band names match the gauge, `omitted` still names the three panels we must not build, and **the backend's KBS equals our own index→KBS mapping** — otherwise the gauge and the report would print different numbers for one assessment.

A second sample pins the first-assessment case: `trend` is `null`, not a delta of zero. Writing that test caught a real subtlety — `sections_present` is attached by the *endpoint*, not the payload builder, so a payload can legitimately arrive without it and `hasSection()` has to fall back to the field rather than reporting a blank report. That path is now tested.

`npm run verify` runs smoke + contract + `tsc` in one go.

**⑤ Show *why* the score is that number.** ✅ **Done.** Four disconnected bars and an orphaned `0.92` became one panel showing the arithmetic.

The formula was read off `risk_index_engine.py:88-136` rather than assumed:

```
additive  = Σ (score_i × weight_i / 100)     landuse, vigor, stability, weather
raw_index = clip(additive + benefits.bonus)
index     = clip(raw_index × gate)
```

- **Bars encode contribution, not score.** A sub-index of 42 matters differently at weight 35 than at weight 15. Each track is sized to its *weight* — so the four together span the full 100 points a raw index can reach — and filled to its contribution. **The unfilled part of a track is the recoverable loss**, which is the number a loan officer actually acts on. Four equal-length bars could never show that.
- **The gate is a step in points, not a multiplier in a box.** And when the footprint was substituted, the engine multiplies the gate by 0.85 *in place* — so it is named inside the gate row rather than drawn as a separate step. Splitting it would misstate the formula.
- **The clip is named.** `raw_index` is clipped to 0–100, so the parts need not sum to it. When they don't, a "clipped to range" row appears; silently showing parts that add to a different total looks like an arithmetic error.
- `driver_captions` render under the driver they explain. **The holding level has none** — `farmer_aggregator.py` never calls `build_driver_captions` (zero occurrences), only the per-plot engine does — so none are passed there rather than invented. Worth closing on the backend side.
- `lib/chart.ts` + `ChartFrame` — the shared primitives. `linePath()` **breaks at nulls rather than bridging them**, which item ⑥ depends on: a line drawn through a fortnight with no observation asserts a measurement never taken. `ChartFrame` makes an accessible name, a **table view**, and a real empty state structural rather than remembered.
- `IndexInsightsCard` trimmed to findings only. Its KBS / raw / gate tiles now live in the waterfall; a second copy is how two components end up disagreeing, exactly as the colour ramps did.

Nine assertions added to `npm run smoke` pinning the arithmetic against the engine's, including that four all-100 drivers reach exactly 100 — otherwise the tracks imply unreachable headroom. Writing them caught that my first expected value was wrong (57.4 vs the correct 57.5); the worked example in §7.3 had it right.

**⑥ Show the field we actually saw.** ✅ **Done.**

**A contract bug surfaced first, and it mattered.** `signal_source` is a **per-bin column** (`optical | fused | sar | imputed`), stored parallel to `dates` by `evidence_snapshot.py` — not a single label for the series. Item ④'s type declared it `string`, and the contract test passed only because the fixture I wrote invented a scalar. Fixed, and the fixture regenerated from a realistic evidence block. Had this shipped, the trajectory would have rendered every point as equally observed — losing exactly the information that makes the chart honest.

- `NdviTrajectory` — three rules, each load-bearing:
  - **Gaps stay gaps.** No line across a bin with no observation; the blind span gets a hatched band and a count instead. A line through a cloudy fortnight asserts a measurement that was never taken.
  - **Provenance is texture, not hue.** Optical solid, fused dashed, radar dash-dot, reconstructed dotted. Same quantity, different confidence — a hue change would imply a different measurement. Hue stays reserved for magnitude.
  - **Markers only on directly-observed bins.** A dot asserts "we saw this", so reconstructed values do not get one.
  - No synthesised district median; `comparison_note` is rendered instead.
  - The y-domain is floored at 0 rather than at the series minimum, which would exaggerate small variation into a dramatic curve.
- `ObservationCalendar` — one cell per bin, coloured by provenance, plus the sufficiency figures. **When per-bin provenance is absent it shows the totals and says so**, rather than arranging a plausible-looking grid: "41 of 52 observed" does not tell you *which* eleven were blind, and inventing that in the one panel whose purpose is honesty about absence would be self-defeating.
- `useReport` — evidence lives in the `evidence` collection, not the job result, so it arrives via item ④'s endpoint. `not-found` and `not-configured` are deliberately not surfaced as errors: a farmer with no stored evidence is ordinary, and an unset `PIPELINE_API_URL` is a deployment fact a loan officer cannot act on. Panels degrade to their own empty state rather than blocking the page.

Eighteen assertions added on the chart primitives, aimed at the properties that fail *silently*: a gap must produce a second `M` rather than an `L`, an area must close to the baseline rather than to the previous point, `NaN` must count as no observation. These are one character away from being wrong and look fine on screen when they are. One real bug fixed: `ticks()` accumulated floats and emitted `0.6000000000000001`, which reaches an axis as a label unless every caller remembers to format it.

**⑦ Make the map a remote-sensing map.** ✅ **Done, with one item descoped on evidence.**

**The NDVI raster overlay I planned in §11 does not exist and cannot.** A grep for `getMapId|getThumbURL|tile_url|geotiff|\.tif` across the backend returns nothing — the pipeline emits per-parcel index *values*, never imagery. There is no raster to overlay. I built the honest version instead rather than sourcing tiles from somewhere else and letting them read as our analysis.

- **Declared vs measured footprint** — the P0 from item ③, now drawn rather than asserted. `geospatial_prep.buffer_km_used` gives the real radius of the substituted circular footprint, so the measured area is drawn geometry, not an illustration. When they diverge the declared boundary is restyled greyed-and-dashed, the measured footprint solid, and the legend says *"The score describes the measured area, not the declared one."*
- **NDVI scrubber — parcel mean, and it says so.** The polygon is tinted by its measured NDVI at the selected date, with a caption reading *"One measured value per date, shading the whole parcel — not per-pixel imagery."* Only bins carrying a value are selectable: scrubbing onto a cloud gap and seeing the previous week's colour would present a stale measurement as a current one. Suppressed on the portfolio map (many parcels, one value) and when the footprint was substituted (the polygon is not what was measured).
- **Layer control** — imagery default, cartographic base for orientation. Imagery is the default because this is a remote-sensing product and a road map undersells what the assessment is looking at.
- **Scale bar** — non-negotiable when the output is evidence in a credit file; a reader must be able to judge the size of what they see.
- **Permanent legend**, not hover-revealed. A map carrying marks with no key is an illustration.
- **Copernicus attribution** on both bases — a licence obligation, not a design choice, and it was missing entirely.

Verified: `tsc` (which also checks every Leaflet call against `@types/leaflet`), `verify`, and `next build` all pass. **Not visually confirmed against a live map** — this environment has no database or pipeline credentials to render a real assessment. Worth a manual look before pilot.

**⑧ The report.** ✅ **Done.** `/report/[farmer_id]` — one column, print-first, driven entirely by the report contract and its `sections_present` / `omitted` gates.

- **The score panel is the dashboard's, not a copy.** `riskViewFromReport()` adapts the payload so `ScoreWaterfall` is reused verbatim. Two components rendering the same arithmetic is how the four colour ramps happened, and a report that disagreed with the dashboard about how a score was built would be the worst version of that — it is the artefact that leaves the building.
- **Farmer identity is absent, and the header says so** rather than leaving a gap: *"The masking policy for personal data is not settled, so the assessment service does not emit it and this page does not fetch it."* An empty slot reads as an oversight and invites someone to fill it from `farm_info` before FD-6 is decided.
- Refusals, the footprint banner, and every `omitted{}` entry render inside the report, so a dossier that reaches a credit committee carries the same refusals the screen does.
- Print-first CSS: `@page A4`, `break-inside: avoid` per section (a section split across a page break separates a figure from its qualifier — which is how a number gets quoted without its caveat), and `print-color-adjust: exact` so band tints survive the printer.
- `AssessmentPrintReport` and its print-override CSS are removed. Dashboard "Open report" and the farm-detail header both go to `/report/[farmer_id]` (plot-scoped via `?plot_key=`). Print lives only on that page.

**Two real bugs, both caught by tests rather than by reading:**

1. **The waterfall would have mislabelled a benefits bonus as a clip.** The residual between the parts and `raw_index` has two opposite causes — clipped at 100 (negative) versus something added the scope cannot see (positive, the benefits bonus, which the report payload does not carry). Calling both "clipped to range" states a false reason for a real difference. Now labelled by direction.
2. **My own contract assertions were passing vacuously.** I wrote eight of them against the smoke suite's `(name, got, want)` signature while this file's `check` takes `(name, ok, detail)` — so a non-empty string landed in the `ok` slot and every one passed without comparing anything. `tsx` does not typecheck; `next build` does, and it failed the build. Fixed with an explicit `eq()` helper.

That second one had a process cause worth recording: `npm run verify` *did* run `tsc` and *did* fail, but I grepped its output for pass-strings only, so the failure was invisible. **Check the exit code, not the log.**

**⑨ Rebuild the dashboard around its reader.** ✅ **Done for the IA; the file split is partial and I want to be exact about that.**

The reading order now runs **verdict → evidence → holding → provenance**, marked with zone comments so the next person does not re-shuffle it by accident. Reader ① (the loan officer, FD-5) gets the verdict and its qualifiers above the fold and can stop; reader ② continues into evidence and opens the provenance drawer.

- `ProvenanceFooter` extracted — versions, window, observation counts, gate versions, geometry source, weights. **Collapsed by default**: reader ① never opens it, and provenance nobody can find is the same as provenance that does not exist, the first time a decision is audited.
- Land cover joins the evidence zone at holding level.

**What I did NOT do:** the 796-line page is now 843 lines. The layout is restructured and four components were extracted, but the job orchestration — enqueue, polling, partial-result reconciliation, URL sync — is still inline. That logic is the riskiest code on the page and the least covered by tests; extracting it into a `useAssessmentJob` hook is a refactor that deserves its own change, not a rider on an IA pass. Recorded as debt rather than claimed as done.

**⑩ The remaining panels.** ✅ **Done — and two of my own types were wrong, caught by reading the emitting Python.**

- **`peer_benchmarking` has no percentile and no cohort size.** My type declared `{percentile, n, activated}`; the pipeline actually emits `{cohort_key, engine, n_cycles_peer_scored}`. The cohort's size and the parcel's rank live in a `cohort_stats` collection written by a separate job and are **not on the assessment at all**. So `PeerCohortPanel` cannot say "6 of 20" — that count would be invented. It states the condition instead: peer comparison needs a warm cohort, no zone has one, vigour is scored against an absolute reference meanwhile. FD-7 answered "render the cold state"; this is the honest version of it.
- **Land-cover streams are tuples, not objects** — `[class, confidence, notes[]]`. `LandCoverPanel` shows all three streams (spectral, temporal, external land-use) **separately**, because "the classifier says water" and "all three streams independently say water" are different strengths of evidence, and a lender refusing a parcel deserves to know which they have. No stacked composition bar: the gate emits a class per stream, not per-pixel fractions, so "62% cropland / 38% built-up" would be fabricated.
- `CropVerificationPanel` — observed cycle length against the crop's reference band. Neutral wording throughout: a mismatch is as often a data-entry slip or a change of plan as a misstatement, so it reads as a prompt to ask, not a finding of fact.
- `WeatherAnomalyChart` — SPI/SPEI are already standardised anomalies, which makes them the one weather figure that genuinely belongs on a diverging scale with a **neutral grey midpoint** (the middle means "normal"; a colour there would imply it means something). Replaces the grid of eight stat cards: "SPI −1.4" means nothing to a loan officer; a bar reaching left of centre reads instantly.
- `ScoreTrendSparkline` — renders **nothing** when `trend` is null, which is the whole contract. Direction is carried by an arrow and a signed number, not by colour alone. No two-point line: the payload carries the previous point, not the series, and drawing a trajectory through two assessments would overstate them.

**⑪ Make it usable for everyone.** ✅ **Done.**

| | Before | After |
|---|---|---|
| `aria-*` attributes | 12 | 27 |
| `role=` | ~0 | 5 |
| Chart table fallbacks | 0 | 10 |
| `focus-visible` styles | **0** | app-wide |
| `text-stone-400` on small text | 107 | 0 |
| 9px type | 2 | 0 |
| `prefers-reduced-motion` | none | all animation zeroed |

- **Focus was the worst of it.** Zero focus-visible styles meant the app fell back to a thin browser default that is close to invisible on cream — the product was effectively unusable by keyboard, and a loan officer working twenty files a day is exactly who stops reaching for the mouse. Now a 2px accent ring, `:focus-visible` rather than `:focus` so a mouse click does not leave rings behind.
- **Skip link** as the first tab stop. A dashboard with a long farm list is otherwise ~40 tabs deep before the content starts.
- Every chart carries a **table view** through `ChartFrame`, so no figure is available only as colour and position.
- Print: `@page A4`, `break-inside: avoid` per section, `print-color-adjust: exact` so band tints survive the printer.

Verified in the built CSS, not just in source: `focus-visible`, `skip-link`, `prefers-reduced-motion`, `print-color-adjust` and `@page` all ship (Next splits them across chunks — the first grep I ran sampled one chunk and looked like a failure).

### 0.3 ⚠ What the ticks hide

All eleven are code-complete, typechecked, and building. Four carry a caveat worth stating plainly:

- **Nothing has been visually verified.** This environment has no database or pipeline credentials, so no screen in this document has been rendered against a real assessment. Every claim here is backed by `tsc`, `npm run verify` (98 assertions), and `next build` — not by looking. The map's dual-footprint drawing and the NDVI scrubber are the two most worth a manual pass before pilot.
- **Item ⑦ lost a feature to reality.** The NDVI raster overlay cannot be built: the backend emits no imagery. The parcel-mean tint shipped instead.
- **Item ⑨'s file split is partial.** The IA is restructured; the job-orchestration logic is still inline in an 843-line page. Called out rather than counted as done.
- **`eslint` is broken repo-wide**, pre-existing, by the `brace-expansion: ^5.0.8` override in `package.json` — minimatch cannot consume it. Lint has been guarding nothing this whole time. Worth a separate fix.

### 0.4 ⚠ Two items are blocked on decisions, not on code

- **⑧ The report** needs the PII masking policy settled (backend D-5) before its header can be built. The payload deliberately refuses to read `farm_info` for Aadhaar or mobile.
- **⑩'s peer panel** stays cold until a zone reaches 20 assessed farmers. It renders its own progress state; it never fakes a percentile.

---

## 0. How to read this document

Same structure as the backend doc, deliberately.

- **§1–§3** — what the UI does today, verified against the code, with counts. Where a claim is a measurement, the command that produced it is in the text.
- **§4–§8** — the design system: palette, gradients, typography, form, motion. Every colour decision here was run through a validator, not eyeballed. Failures are reported as failures.
- **§9–§12** — information architecture, component specs, and the visualization catalogue.
- **§13–§15** — build plan, accessibility/print, and the decisions I need from you.

Severity scale, matching the backend doc:

| | Meaning |
|---|---|
| **P0** | A user reads a number and draws the wrong conclusion. Ships wrong information to a lender. |
| **P1** | Backend evidence exists and is invisible, or the UI can't tell "no data" from "we refuse". Fix before pilot. |
| **P2** | Consistency / maintainability debt. Fix in the v6 window. |
| **P3** | Polish. |

---

## 1. Executive summary

### 1.1 The one-line version

**The backend now knows far more than the screen shows, and the screen is styled by hand in 242 places instead of by a system.** Backend v6 added eight capabilities — land-cover classification, parcel viability, data sufficiency, crop verification, evidence series, grounded driver captions, peer cohorts, and a report payload. The frontend renders **zero of them**. Meanwhile the design tokens declared in `globals.css` are referenced **zero times** by any component.

### 1.2 The five systemic problems

**① Every v6 backend field is invisible.** A grep across `frontend/app` for the fields the pipeline now emits returns nothing:

```
land_cover           (0 files)      driver_captions      (0 files)
parcel_viability     (0 files)      footprint            (0 files)
data_sufficiency     (0 files)      evidence / series    (0 files)
crop_verification    (0 files)      score_history        (0 files)
```

The new `GET /v1/report/{farmer_id}` endpoint (`backend/Credit_assessment/api/app.py:560`) has no client. The whole of §3.

**② There are three different colour-to-score mappings for the same number.** For an identical index value, three components disagree on whether it is green:

| Source | Green at | Amber at | Red below |
|---|---|---|---|
| `SummaryHero.tsx:15` | ≥ 70 | ≥ 45 | 45 |
| `kbsScore.ts:91` `subScoreBarColor` | ≥ 65 | ≥ 55 | 55 |
| `KBS_BANDS` (the canonical bands) | ≥ 75 | ≥ 50 | 25 |

An index of 68 is simultaneously amber (hero), green (sub-index bar), and Good (band). **P0** — this is the number the whole product exists to communicate. §2.2.

**③ The risk-band palette fails a colour-vision check that a lending product cannot fail.** The current Good `#639922` and Excellent `#1D9E75` sit at **ΔE 8.8** for *normal* vision — below the 15 floor, meaning full-colour readers struggle to tell the two best bands apart. Under protanopia the Fair↔Good pair collapses to ΔE 7.8. Measured, not asserted (§5.2). **P0**.

**④ A declared design system exists and nothing uses it.** `globals.css:6-17` declares `--bg`, `--border`, `--text`, `--accent`. Component usage:

| | Count |
|---|---|
| `var(--bg…)` referenced in a component | **0** |
| Literal `#E4DFD4` in `.tsx` | **155** |
| Literal `#F5F2EB` in `.tsx` | **87** |
| Distinct `text-stone-N` applications | **483** |
| `bg-[#F5F2EB]` on a page root | **81** |

A palette change today is a 242-site find-and-replace. **P2**, but it blocks everything in §5–§7.

**⑤ Nothing on screen distinguishes "we have no data" from "we refuse to say".** The backend went to real trouble to separate `SUCCESS` / `REJECTED_NOT_AGRICULTURAL` / `INSUFFICIENT_DATA`, and to ship an explicit `omitted{}` block naming panels it will not fill. The UI has one amber box that says "Insufficient data" (`RiskScoreCard.tsx:190`) and no concept of a refusal at all. That throws away the single most defensible thing about this product. **P1**. §10.

### 1.3 What is already good, and should not be rewritten

Being fair to what exists:

- **The cream/paper base is right.** `#F5F2EB` is a genuine differentiator against the default fintech white-or-slate. It reads as a document, which is what a credit file is. Keep it; formalise it.
- **Hand-rolled SVG, no chart library.** `package.json` has no charting dependency, and 11 components draw their own SVG. That is the correct call for this product (§7.1) — do not "fix" it by adding Recharts.
- **The KBS framing copy is honest.** "Field-health index… not a credit score, loan amount, or default probability" appears verbatim on both the hero and the insights card. That sentence is doing real work. It should become a component, not a copy-paste.
- **`useRiskView` is a genuine view-model.** The farmer-level / plot-level / partial-result reconciliation in `lib/useRiskView.ts` is the hard part and it is already isolated. New panels extend it rather than re-deriving.

---

## 2. Current state — verified

### 2.1 The surface, as built

| Route | File | Lines | What it is |
|---|---|---|---|
| `/` | `app/page.tsx` | 264 | Landing |
| `/login` | `app/login/page.tsx` | 265 | Auth |
| `/farmer` | `app/farmer/page.tsx` | 384 | Farmer + plot onboarding, Leaflet/Geoman boundary drawing |
| `/farmer/farms` | `app/farmer/farms/page.tsx` | 274 | Plot list |
| `/dashboard` | `app/dashboard/page.tsx` | **796** | Assessment run + holding-level result |
| `/dashboard/farm/[farm_id]` | `.../page.tsx` | 398 | Per-plot detail, 5 tabs |
| `/agristack` | `app/agristack/page.tsx` | 625 | API sandbox (internal) |

Twenty dashboard components, eleven farmer components. Stack: Next 16, React 19, Tailwind 4, Redux Toolkit, Leaflet, Geist/Geist Mono.

### 2.2 The three-mappings defect, in full

`SummaryHero` computes its own ramp inline:

```tsx
// SummaryHero.tsx:15-21
const scoreColor = insufficient ? '#a8a29e'
  : pct >= 70 ? '#16a34a'
  : pct >= 45 ? '#d97706'
  : '#dc2626';
```

…while `RiskScoreCard` renders the pillar bars through `subScoreBarColor` (thresholds 65/55) and the gauge through `KBS_BANDS` (thresholds at the 25/50/75 quartiles). Three ramps, three sets of hexes, one quantity. Fix in §5.1: one exported function, no inline ternaries, ever.

### 2.3 Typography, measured

141 uses of `text-[10px]`/`text-[11px]`, plus 2 of `text-[9px]`. Nine-pixel type on a screen a loan officer reads for eight hours is not a style choice, it is a defect. §6.

### 2.4 Accessibility, measured

Twelve `aria-*` attributes across the entire app. The gauge has an `aria-label`; almost nothing else does. No chart has a table fallback. §14.

---

## 3. The invisible backend — what v6 emits and nobody renders

This is the section that justifies the work. Each row is data the pipeline computes, persists, and returns today, with no pixel behind it.

| # | Backend field | Where it comes from | What the user loses | Sev |
|---|---|---|---|---|
| 1 | `land_cover` | Item 2, `ddf2cd3b` | *Why* a parcel was refused as non-agricultural. Currently a bare refusal with no evidence. | P1 |
| 2 | `parcel_viability` | `669a9344` | That a plot is below the 0.15 ha fundable floor, and by how much. | P1 |
| 3 | `data_sufficiency` | `b023f600` | Whether we could see the field at all — the difference between a bad farm and a cloudy quarter. | **P0** |
| 4 | `cropping_analysis.crop_verification` | Item 5, `23bfbc1d` | Declared crop vs observed phenology. The fraud/error signal a lender most wants. | P1 |
| 5 | `risk_assessment.driver_captions` | Item 7, `a3237563` | Grounded, per-sub-index explanations. We render bare numbers and generic pill text instead. | P1 |
| 6 | `risk_assessment.footprint` | `588beecf` | That the score was measured over a *substituted* footprint, not the declared boundary. | **P0** |
| 7 | `evidence.series` + `series_completeness` | Item 6, `0ce4bf4f` | The NDVI trajectory and its per-point provenance. The single most persuasive artefact the pipeline produces. | P1 |
| 8 | score history / `trend` | `a3237563` | Movement since the last assessment. | P1 |
| 9 | peer cohort | `a3237563` (cold) | Percentile context — **when a zone warms to n ≥ 20**. Until then the UI must say so, not fake it. | P2 |
| 10 | `omitted{}` | `report_payload.py:269` | The backend explicitly names panels it won't fill. The UI has nowhere to put that. | P1 |

Items 3 and 6 are P0 for the same reason: without them a user reads a low score as *"this farmer's land is poor"* when the true statement is *"we could not see this field"* or *"we measured a different field"*.

---

## 4. Who is looking at the screen

Three audiences, and the current UI is designed for none of them specifically.

**① The loan officer** — the primary user. Twenty files a day, wants a verdict in four seconds and the reason in twenty. Needs: score, band, the one weak pillar, and any refusal, above the fold. Everything else on demand.
*Design consequence:* one hero, one verdict sentence, one weakest-driver callout. No four-column KPI wall.

**② The credit committee / risk reviewer** — reads one file deeply, adversarially. Wants provenance, method version, what we could and could not observe, and whether the number is defensible in an audit. This is the reader the design reference's "score dossier" framing serves, and it is the reader who converts a pilot.
*Design consequence:* an evidence layer, always available, never in the primary flow. Print/PDF is their real output format.

**③ The field agent / farmer-facing operator** — mobile, outdoors, poor connection, explaining the result to the farmer in Marathi or Hindi. Needs the narrative and the map, large, in the local language.
*Design consequence:* the narrative and boundary map must survive a 360px viewport and must not depend on hover.

Right now every screen is built for reader ② at reader ①'s density, in reader ③'s absence.

---

## 5. Colour

### 5.1 Method

Colour was assigned **last**, by the job it does, and every categorical set was run through `dataviz/scripts/validate_palette.js` against **our actual surface** `#F5F2EB` — not the validator's default white. Results below are copied from the run, including the failures.

Four jobs, four rules:

| Job | Encoding | Where it appears |
|---|---|---|
| **Magnitude** (NDVI, health, sufficiency) | one hue, light→dark | trajectory fill, calendar heatmap, choropleth |
| **Identity** (data source, crop, plot) | fixed-order categorical, ≤ 3 slots | provenance lines, plot series |
| **Polarity** (weather anomaly, score delta) | diverging, neutral grey midpoint | rainfall vs normal, trend |
| **State** (risk band, refusal) | reserved status palette + icon + label | gauge bands, refusal cards |

### 5.2 Risk bands — the current set FAILS, and the replacement passes

Current bands (`lib/kbsScore.ts:24`), validated on cream:

```
#E24B4A, #EF9F27, #639922, #1D9E75  → FAILED
  [WARN] CVD separation     worst adjacent #639922↔#EF9F27 ΔE 7.8 (protan)
  [FAIL] Normal-vision floor worst adjacent #1D9E75↔#639922 ΔE 8.8 — below 15
```

Good and Excellent — the two bands that decide whether a farmer is fundable — are 8.8 apart to a reader with *full* colour vision. Replacement, same semantics, re-stepped:

```
#B93A28, #E5A614, #6B9418, #00734F  → ALL CHECKS PASS
  [PASS] Lightness band       all 4 inside L 0.43–0.77
  [PASS] Chroma floor         all 4 >= 0.1
  [PASS] CVD separation       worst adjacent #6B9418↔#E5A614 ΔE 10.2 (protan)
  [PASS] Normal-vision floor  worst adjacent #00734F↔#6B9418 ΔE 15.2
  [WARN] Contrast vs surface  #E5A614 at 1.92:1 — relief required
```

| Band | Index | KBS | New hex | Old hex |
|---|---|---|---|---|
| Poor | 0–25 | 300–450 | `#B93A28` | `#E24B4A` |
| Fair | 25–50 | 450–600 | `#E5A614` | `#EF9F27` |
| Good | 50–75 | 600–750 | `#6B9418` | `#639922` |
| Excellent | 75–100 | 750–900 | `#00734F` | `#1D9E75` |

The contrast WARN on Fair is **not dismissable**: it obliges a visible label. Which leads to the rule that matters more than the hexes —

> **The risk band is never carried by colour alone.** Every band appears as *position on the arc* + *band name in text* + colour. A bare coloured chip with no adjacent word is forbidden anywhere in this app. This is not belt-and-braces: a red/amber/green scale is *structurally* unsafe under protanopia, and no re-stepping fixes that. Position and text do.

### 5.3 Vegetation ramp — sequential, one hue

For NDVI and any continuous field magnitude. Hue spread 19°, lightness monotone:

```
#DDEAC4  #BAD795  #94C267  #6EAB3E  #4C9028  #31741F  #1D5717
   0.1      0.2      0.3      0.4      0.55     0.7      0.85   ← NDVI
```

Two uses, two rules:

- **Continuous fill** (area under the trajectory, choropleth): use all seven. The light end is *allowed* to recede into the cream — near-zero NDVI should look like bare ground.
- **Discrete chips** (legend swatches, calendar cells, category dots): start at step 3. `#94C267` measures 1.85:1 against cream — below the 2:1 ordinal floor — so the discrete set is `#8AB24F #67A03C #468526 #2D6A1E #194E16`, which passes all four ordinal checks.

Green for vegetation is not decoration here: it matches how anyone who has looked at an NDVI raster already reads the image. Do not get clever with viridis.

### 5.4 Categorical — capped at three, hard

For telling *sources* apart (optical vs radar vs modelled), validated all-pairs on cream:

```
#2a78d6 (blue)  #eb6834 (orange)  #1baf7a (aqua)  → ALL CHECKS PASS
  worst all-pairs CVD ΔE 9.2 · normal-vision ΔE 24.0
  [WARN] contrast: #eb6834 2.86 · #1baf7a 2.52 → direct labels required
```

Fixed order, never cycled. A fourth series does not get a fourth hue — it folds into "Other", or the chart becomes small multiples. If plots ever need per-plot colour on one chart, that is a **table**, not more hues (§7.2).

### 5.5 Ink, surfaces, and the paper stack

Formalise what is currently 242 hex literals into one token set. Surfaces are named by elevation, not by colour:

| Token | Hex | Role |
|---|---|---|
| `--paper` | `#F5F2EB` | page plane — the base cream |
| `--paper-raised` | `#FFFEFA` | inset panels, mini-cards |
| `--card` | `#FFFFFF` | the card plane |
| `--rule` | `#E4DFD4` | hairline borders |
| `--rule-soft` | `#EFEBE1` | internal dividers |
| `--ink` | `#1C1917` | primary text |
| `--ink-2` | `#57534E` | secondary text |
| `--ink-muted` | `#78716C` | axis labels, captions — **floor for any text under 13px** |
| `--accent` | `#15803D` | interactive/brand green |
| `--accent-gold` | `#B4842A` | provenance, methodology, the "dossier" accent |

`--ink-muted` at `#78716C` clears 4.5:1 on cream. `text-stone-400` (`#A8A29E`, currently used for 10px labels in `WeatherSection.tsx:10` and elsewhere) does **not** — it measures 2.3:1. That is a straight WCAG failure on the smallest text in the app, and it is everywhere.

---

## 6. Gradients — where they are allowed, and where they lie

You asked specifically about gradients. Here is the position, and it is a strong one.

### 6.1 The rule

> **A gradient may never run along an axis that encodes a value.**

A bar whose length means "68" must be a flat fill. Put a gradient on it and the eye reads the dark end as heavier — the same length now looks like a different number depending on which end you scan from. In a chart that a bank uses to price risk, that is not a style disagreement, it is a measurement error introduced by decoration.

`bandCardSurface()` (`kbsScore.ts:111`) already gets this right — the gradients there are on a *card* whose size means nothing. Keep those. `subScoreBarColor` bars stay flat.

### 6.2 Where gradients are correct, and earn their place

| Surface | Gradient | Why it's legitimate |
|---|---|---|
| **Score hero band wash** | `linear-gradient(160deg, band@6% → band@0%)` over `--card` | Tints the card by band. Encodes nothing; reinforces the label. |
| **Trajectory area fill** | vegetation hue, `18% → 0%` top-to-bottom | Vertical, while the *value* is the line's y-position at the top edge. The fade is depth, not magnitude. |
| **Map raster overlays** | the §5.3 ramp, continuous | This *is* the data. A continuous field deserves a continuous ramp. |
| **Page plane** | `radial-gradient` from `#FAF8F1` at top → `#F5F2EB` | ~2% luminance. Gives the page a light source so cards feel like paper on a desk. Invisible if you look for it, felt if you don't. |
| **Skeleton shimmer** | `--paper → --paper-raised → --paper`, 1.6s | Standard loading affordance. |

Everything else: flat. Specifically **no** gradient on: bars, gauge arcs (each band is one flat colour), buttons, badges, or KPI numerals.

### 6.3 Depth without gradients

The paper metaphor wants shadow discipline, not glow:

```css
--shadow-card:    0 1px 2px rgba(28,25,23,.05), 0 1px 1px rgba(28,25,23,.03);
--shadow-raised:  0 2px 6px rgba(28,25,23,.06), 0 1px 2px rgba(28,25,23,.04);
--shadow-overlay: 0 8px 28px rgba(28,25,23,.12);
```

Three levels, ever. Shadows are warm-tinted (`28,25,23`) not neutral black — a cool grey shadow on cream reads as dirt.

---

## 7. Form — the visualization catalogue

Chart type is chosen by the data's job, before any colour. Several of these replace things that are currently bare numbers.

### 7.1 No charting library. Deliberately.

`package.json` carries no chart dependency and 11 components already hand-roll SVG. Keep it that way:

- Nine charts, all bespoke — none of them is a generic "line chart with a config object".
- Recharts/Nivo pull d3 (≈ 60–90 kB gzipped) and fight React 19 / RSC over refs and `useLayoutEffect`.
- Hand-rolled SVG server-renders, prints correctly, and appears in the PDF without a headless-browser dance.

The investment goes into a tiny shared `lib/chart.ts` — scale helpers, `path()` builders, an axis component, one `<ChartFrame>` with title/legend/table-toggle. Perhaps 200 lines. Not a library.

### 7.2 The catalogue

| # | Panel | Form | Colour job | Replaces | Backend field |
|---|---|---|---|---|---|
| 1 | **Score hero** | hero figure + arc gauge | status (band) | `KbsGauge` (keep, re-step) | `score` |
| 2 | **Score build-up** | horizontal waterfall | sequential + status | 4 flat pillar bars | `sub_indices`, `weights`, `confidence_gate` |
| 3 | **NDVI trajectory** | line + area, provenance-marked | 1 sequential hue | *nothing* | `evidence.series` |
| 4 | **Observation calendar** | week × source heatmap | sequential | *nothing* | `data_sufficiency`, `series_completeness` |
| 5 | **Crop verification** | dual timeline, declared vs observed | categorical (2) | *nothing* | `crop_verification` |
| 6 | **Land cover** | single stacked bar | categorical (3 + Other) | *nothing* | `land_cover` |
| 7 | **Weather anomaly** | diverging column vs normal | diverging | 8 mini stat cards | `weather_analysis` |
| 8 | **Score trend** | sparkline + delta chip | 1 hue + status delta | *nothing* | `trend`, score history |
| 9 | **Peer position** | distribution strip + marker | 1 hue + accent | *nothing* | cohort (**gated on n ≥ 20**) |
| 10 | **Plot portfolio** | sortable table + map | none (table) | `StreamingFarmList` | `farm_assessments` |

Note what is **not** on this list: a pie chart, a radar/spider chart of the four pillars, and a dual-axis anything. Radar in particular is tempting for four sub-indices and is wrong — area scales as the square of the value, so a 10% weakness looks like 20%, and the shape changes if you reorder the axes.

### 7.3 The two that matter most

**② Score build-up (waterfall).** Today the four pillars are four disconnected bars and the confidence gate is a lonely number in a box (`IndexInsightsCard.tsx:68`). Nobody can see how 4 sub-indices and a multiplier produced 612. The waterfall shows it as arithmetic:

```
Landuse    ×0.30  ██████████████            +21.3
Vigour     ×0.35  ████████                  +14.7   ◀ weakest
Stability  ×0.20  ███████████               +13.1
Weather    ×0.15  ██████                     +8.4
                  ─────────────────────────────────
Raw index                                     57.5
Confidence gate   ×0.92                       −4.6   ▼ 41 of 52 observations usable
                  ─────────────────────────────────
Index                                         52.9  →  KBS 617 · Good
```

Bars flat-filled (§6.1), weakest pillar carries a marker and a caption from `driver_captions`, the gate step is the diverging-negative colour. This one panel answers "why this number" better than every existing card combined.

**③ NDVI trajectory with honest provenance.** The single most persuasive thing the pipeline produces, and it is currently not on screen at all. Encoding:

- **Line** — the parcel's NDVI, 2px, vegetation hue at step 5.
- **Area** — vertical fade beneath, 18% → 0%.
- **Provenance by texture, not hue.** Optical = solid. SAR-substituted = dashed. Interpolated = dotted. Same measurement, different confidence — a hue change would falsely imply a different quantity.
- **Gaps are gaps.** No line drawn across a period with no observation; the region gets a hatched band and the count. Interpolating a line through a cloudy fortnight is the exact fabrication the backend spent v6 eliminating.
- **Phenology markers** — SOS / POS / EOS from `crop_cycles[].phenology`, as labelled vertical rules.
- **No district median.** `report_payload.py:124-151` refuses to emit one until a cohort is warm, and states why. The chart renders that refusal as a footnote, not as an empty legend entry.

---

## 8. Motion

Restrained, because this is a document, not a product tour.

| Event | Motion | Duration |
|---|---|---|
| Card enters | fade + 8px rise | 200ms `ease-out` |
| Gauge needle settles | rotate from 0 | 700ms `cubic-bezier(.22,1,.36,1)`, once per result |
| Chart line draws | `stroke-dashoffset` | 500ms, once |
| Value change | no count-up | — |
| Skeleton | shimmer | 1.6s loop |
| Tab / accordion | height + opacity | 160ms |

All of it behind `@media (prefers-reduced-motion: reduce)` → instant final state. **No count-up animation on the score** — a number that spins past 700 on its way to 617 tells the reader something false for 400ms, and it is the number they screenshot.

---

## 9. Information architecture

### 9.1 The dashboard, restructured

`dashboard/page.tsx` is 796 lines holding orchestration, form state, polling, and layout. It splits into three zones with a clear reading order:

```
┌─ VERDICT ─────────────────────────────────────── above the fold ─┐
│  hero gauge + band          │  verdict sentence + weakest driver  │
│  KBS 617 · Good             │  trend chip · refusal banner        │
├─ EVIDENCE ───────────────────────────────────────────────────────┤
│  score build-up waterfall   │  NDVI trajectory                    │
│  observation calendar       │  land cover · crop verification     │
├─ HOLDING ────────────────────────────────────────────────────────┤
│  plot table + boundary map (linked hover/selection)               │
└─ PROVENANCE ────────────────────────── collapsed by default ──────┘
   versions · window · weights · sources · integrity hash
```

Reader ① stops after zone 1. Reader ② reads all four. Reader ③ gets zones 1 and 3 stacked on mobile.

### 9.2 New route: `/report/[farmer_id]`

The `/v1/report/{farmer_id}` endpoint has no client. It gets a dedicated route rendering the dossier — one column, print-first, the design reference's visual language, driven **entirely** by `sections_present` and `omitted`. This replaces `AssessmentPrintReport.tsx` (232 lines of `@media print` overrides), which reformats the dashboard rather than rendering the report contract.

### 9.3 What we do NOT build, and why

The design reference contains panels the backend deliberately refuses to produce. `report_payload.py:7-27` names them. **We do not build UI for them, and we do not quietly leave a blank space where they were** — a blank slot invites someone to fill it later.

| Reference panel | Verdict |
|---|---|
| "Suggested action · defer-30-days · committee threshold 55 · re-assess 11 Oct" | **Not built.** No policy engine, no validated forecast. Fabricating a lending recommendation is the single worst thing this UI could do. |
| District-median NDVI comparison line | **Not built until cohorts warm.** Replaced by a stated reason, from `omitted.peer_comparison`. |
| "Reviewed by / review status" | **Not built.** No review workflow exists. |
| Aadhaar / DOB / mobile on the report header | **Not built.** Masking policy unsettled (backend D-5). The payload deliberately never reaches into `farm_info` for it. |
| KBS scale 300–950, five policy bands | **Not adopted.** Scale is 300–900 over four agronomic bands (backend D-3). The reference's five *credit-policy* bands imply a policy engine we don't have. |

An `<OmittedPanel>` component renders each entry in `omitted{}` as a bordered, explicitly-empty slot with the backend's own reason string. Refusals are a feature; show them.

---

## 10. The honesty layer

This is the part with no equivalent in the current UI, and the part most worth building.

### 10.1 Three terminal states, three distinct screens

Not three shades of one amber box.

| State | Treatment |
|---|---|
| `SUCCESS` | Full result. Gate multiplier shown on the waterfall if < 1.0. |
| `REJECTED_NOT_AGRICULTURAL` | **No score. No gauge. No empty band arc.** A land-cover panel showing the observed classes, the boundary on satellite imagery, and one sentence: *"This parcel is X% built-up / water. It was not scored as farmland."* |
| `INSUFFICIENT_DATA` | **No score.** An observation-calendar panel showing exactly which weeks had usable imagery and which did not, plus the specific cause from `data_sufficiency` — too small, cloud-gapped, or untrustworthy boundary. Then: *"Not enough observation to score this parcel. This is a statement about our view of the field, not about the field."* |

That last sentence is the product. Say it in the UI.

### 10.2 Confidence, everywhere it applies

- **Gate < 1.0** → a visible haircut step on the waterfall, plus a bracket on the gauge showing raw vs gated position. Currently the gate is a number in a box that nobody can interpret.
- **Footprint substituted** (`risk_assessment.footprint`) → a **persistent banner on the map**, not a footnote: *"Scored over a substituted footprint — the declared boundary failed validation."* The user is looking at a polygon; they must know it isn't the one we measured. **P0**.
- **Crop mismatch** (`crop_verification`) → dual timeline, declared vs observed, with the disagreement highlighted. Neutral wording — this is as often a data-entry error as it is misrepresentation.
- **Peer cohort cold** → the peer panel renders its own absence with the count: *"Peer comparison needs 20 assessed parcels in this zone. Currently 6."* A progress state, not an error.

### 10.3 Never render a fabricated zero

`_trend()` returns `None` on a first assessment rather than emitting a delta of 0 (`report_payload.py:97-121`) — because a "0" against a baseline that doesn't exist invents a history. The UI must honour that: when `trend` is null, there is **no trend chip**, not a chip reading "—" or "0". Same rule for every nullable field in the payload. `sections_present` is the switch; use it.

---

## 11. Maps and remote sensing

The map is where "we actually looked at your field" becomes credible, and it is currently a boundary polygon on OSM tiles.

- **Base layers** — satellite imagery default (this is a remote-sensing product; a road map undersells it), with a toggle to a muted cartographic base. Labels only on the cartographic base.
- **Boundary treatment** — 2px accent stroke, 12% fill. **Declared vs measured footprint drawn as two polygons** when they differ: declared dashed and greyed, measured solid and accented. Currently a divergence is invisible.
- ~~**NDVI raster overlay**~~ — **not buildable, and this was a planning error.** A grep for `getMapId|getThumbURL|tile_url|geotiff|\.tif` across the backend returns **nothing**: the pipeline emits per-parcel index *values*, never imagery. There is no raster to overlay and no tile service to serve one. What shipped instead is the honest version of the same idea — the polygon tinted by its **parcel-mean NDVI** at a scrubbed date, labelled as such. Real measurement, no implied within-field detail. Revisit only if the backend gains a tile export.
- **Per-plot choropleth at holding level** — each plot filled by its band colour, always with its plot label rendered (§5.2 rule).
- **Legends are mandatory and permanent**, not hover-revealed. A raster with no legend is an illustration.
- **Scale bar and north arrow.** Non-negotiable in a geospatial product that produces evidence for a credit file.
- **Attribution** — Copernicus/Sentinel-2 attribution is a licence obligation, not a design choice. The design reference already carries it ("contains modified Copernicus Sentinel-2 data"); the app does not.

---

## 12. Component inventory

### 12.1 New

| Component | Purpose |
|---|---|
| `ScoreHero` | Gauge + band + verdict sentence + trend chip. Replaces `SummaryHero` and `RiskScoreCard`'s top half. |
| `ScoreWaterfall` | §7.3 build-up. |
| `NdviTrajectory` | §7.3 chart. |
| `ObservationCalendar` | Week × source sufficiency heatmap. |
| `CropVerificationTimeline` | Declared vs observed phenology. |
| `LandCoverBar` | Stacked composition + non-agri flag. |
| `WeatherAnomalyChart` | Diverging columns vs seasonal normal. |
| `PeerPositionStrip` | Cohort distribution, or its own cold-start state. |
| `ScoreTrendSparkline` | History + delta. |
| `RefusalPanel` | The `REJECTED_*` / `INSUFFICIENT_DATA` screens (§10.1). |
| `OmittedPanel` | Renders one `omitted{}` entry (§9.3). |
| `ConfidenceBadge` | Gate / footprint / provenance chip, consistent everywhere. |
| `ProvenanceFooter` | Versions, window, sources, integrity hash. |
| `ChartFrame` | Title, legend, table toggle, empty state. Every chart wraps in this. |
| `KbsDisclaimer` | The "not a credit score" sentence, once. |

### 12.2 Modified

| Component | Change |
|---|---|
| `KbsGauge` | Re-step to §5.2 hexes; add the raw-vs-gated bracket; keep the geometry. |
| `SubIndexBars` | Absorbed into `ScoreWaterfall`. |
| `IndexInsightsCard` | Reason codes only; metrics move to the waterfall. |
| `WeatherSection` | Mini-card grid → `WeatherAnomalyChart` + a stat row. |
| `PlotBoundaryMapInner` | Layer control, NDVI overlay, dual-footprint, legend, scale bar. |
| `StreamingFarmList` | Sortable table with band chips (chip + label, §5.2). |
| `AssessmentPrintReport` | Retired in favour of `/report/[farmer_id]`. |

### 12.3 Infrastructure

- `lib/designTokens.ts` + rewritten `globals.css` — the §5.5 token set. One source.
- `lib/chart.ts` — scales, path builders, axis, tick formatting (§7.1).
- `lib/reportClient.ts` + `app/api/report/[farmer_id]/route.ts` — proxy to `/v1/report/{farmer_id}`, service-key-side.
- `types/report.ts` — mirror `report_payload_v1`. **Generated from the backend contract, not hand-written**, or it drifts within a sprint.
- `types/assessment.ts` — add `land_cover`, `parcel_viability`, `data_sufficiency`, `crop_verification`, `driver_captions`, `footprint`.

---

## 13. Build plan

Ordered so each phase is shippable and nothing depends on a later phase.

| Phase | Work | Why here | Est. |
|---|---|---|---|
| **F0** | Token system; kill 242 hex literals; **one** score→colour function; re-step the bands | Every later phase paints with these. Doing it after is a rewrite. | 2–3 d |
| **F1** | `types/report.ts`, report client, API proxy, extend `assessment.ts` | Nothing can render v6 data before it is typed and fetched. | 1–2 d |
| **F2** | Honesty layer — `RefusalPanel`, `OmittedPanel`, `ConfidenceBadge`, footprint banner, `sections_present` wiring | Highest value per line. Turns backend rigour into visible product. | 3–4 d |
| **F3** | `ChartFrame` + `lib/chart.ts` + `ScoreWaterfall` | The "why this number" panel. | 3–4 d |
| **F4** | `NdviTrajectory` + `ObservationCalendar` | The evidence pair. Needs F3's chart primitives. | 4–5 d |
| **F5** | Map upgrade — layers, NDVI overlay, dual footprint, legend, scrubber | Heaviest single item; independent of F3/F4. | 4–5 d |
| **F6** | `/report/[farmer_id]` dossier route, print stylesheet, PDF | Needs F2–F4 components to compose. | 3–4 d |
| **F7** | Dashboard IA restructure (§9.1); split the 796-line page | Safe once the components exist. | 2–3 d |
| **F8** | `LandCoverBar`, `CropVerificationTimeline`, `WeatherAnomalyChart`, `PeerPositionStrip`, `ScoreTrendSparkline` | The remaining panels, in value order. | 4–5 d |
| **F9** | Accessibility pass, table fallbacks, reduced-motion, mobile (§14) | Verification, not decoration. | 2–3 d |

**F0 → F2 is the minimum credible increment** (~7 days): consistent colour, and every refusal the backend makes becomes visible. That alone changes what the pilot demonstrates.

---

## 14. Accessibility, print, performance

**Accessibility.** Twelve `aria-*` attributes today. Requirements: every chart carries an accessible name and a table-view toggle; no meaning by colour alone anywhere (§5.2); `--ink-muted` floor for text below 13px, retiring `text-stone-400` on small type; visible focus rings; full keyboard path through tabs, filters, and the map's plot list; live-region announcements for job progress.

**Print.** The report route is designed print-first, not print-adapted: A4, 14mm margins, charts as SVG (they print at full resolution; the current approach loses the gauge), `break-inside: avoid` per section, provenance footer and integrity hash on every page. The status quo — `@media print` overrides on the dashboard — is why the printed output looks like a screenshot of a website.

**Performance.** Charts are SVG and server-renderable; keep them RSC-compatible where they have no interaction. Leaflet stays dynamically imported (already the case). Budget: no route above 200 kB JS gzipped. Skeletons match final layout so nothing reflows on arrival.

**Dark mode: explicitly not in scope.** Every hex in §5 is validated against `#F5F2EB` only. A dark theme is a *second selected palette* — re-stepped and re-validated against the dark surface, not an inversion — and this product's output is a printed credit file. Revisit after pilot. Recorded here so nobody assumes it was forgotten. (Decision **FD-4**.)

---

## 15. Decisions

### 15.1 Settled — 2026-08-16

| # | Decision | Outcome |
|---|---|---|
| **FD-1** | Re-step the risk bands? | **Yes.** Adopted `#B93A28 / #E5A614 / #6B9418 / #00734F`. Shipped in item ①. |
| **FD-3** | Design reference fidelity | **Adopt its visual language, drop the unbacked panels** (§9.3). No policy-action panel, no synthesised district median, no reviewer field. |
| **FD-4** | Dark mode | **Deferred past pilot.** Every hex in §5 is validated against `#F5F2EB` only. |
| **FD-5** | Primary reader | **The loan officer.** Desktop, ~20 files a day, verdict in 4 seconds. The credit committee is served by the report route (item ⑧) as a secondary output; the field agent is a post-pilot concern. Phase order in §13 stands. |
| **FD-8** | Aesthetic direction | **Editorial dossier.** Cream paper, warm shadows, hairline rules, generous type, near-zero imagery — it should read as a credit document and print as one. No stock photography, no decorative hero, no illustrated empty states. Gradients confined to §6.2. |

FD-8 has a consequence worth stating plainly, since it was asked about directly: **this product does not get background images.** Imagery earns its place here only when it *is* evidence — the Sentinel-2 basemap under a parcel boundary, an NDVI raster overlay. A photograph of a wheat field behind a login form is the visual equivalent of a fabricated data point: it implies a richness the screen is not delivering. The texture in this design comes from paper, rule weight, and typography.

### 15.2 Still open

| # | Decision | Options | My recommendation | Blocks |
|---|---|---|---|---|
| **FD-2** | Report route vs dashboard print | (a) dedicated `/report/[farmer_id]` (b) keep improving `AssessmentPrintReport` | **(a).** The payload is a different contract from the dashboard's; rendering it through the dashboard guarantees drift. | item ⑧ |
| **FD-6** | Farmer PII on the report | (a) caller supplies, masked (b) full (c) none | **(a)** — matches the backend's deliberate refusal to read `farm_info` for it. Needs backend D-5 settled. | item ⑧ header |
| **FD-7** | Peer panel before cohorts warm | (a) render the cold-start state with the count (b) hide entirely | **(a).** "6 of 20 parcels in this zone" is a credible progress signal; hiding it makes the feature look absent rather than pending. | item ⑩ |

None of these block items ②–⑦, so building continues.

---

## Appendix A — validator runs

Reproduce from the dataviz skill directory:

```bash
node scripts/validate_palette.js "#E24B4A,#EF9F27,#639922,#1D9E75" --mode light --surface "#F5F2EB"
node scripts/validate_palette.js "#B93A28,#E5A614,#6B9418,#00734F" --mode light --surface "#F5F2EB"
node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a" --mode light --surface "#F5F2EB" --pairs all
node scripts/validate_palette.js "#8AB24F,#67A03C,#468526,#2D6A1E,#194E16" --mode light --surface "#F5F2EB" --ordinal
```

| Palette | Result |
|---|---|
| Risk bands, current | **FAILED** — normal-vision ΔE 8.8 |
| Risk bands, proposed | **ALL PASS** — CVD 10.2, normal 15.2, one contrast WARN (relief: labels) |
| Categorical trio | **ALL PASS** all-pairs — CVD 9.2, normal 24.0, two contrast WARNs |
| Vegetation ordinal chips | **ALL PASS** — monotone L, gaps ≥ 0.06, light end 2.19:1, hue spread 15° |

Every set must be re-run against `#F5F2EB` after any token change. A palette validated on white is not validated for this app.

## Appendix B — measurement commands

```bash
cd frontend/app
grep -ro 'var(--bg'    --include=*.tsx . | wc -l    # 0
grep -ro '#E4DFD4'     --include=*.tsx . | wc -l    # 155
grep -ro '#F5F2EB'     --include=*.tsx . | wc -l    # 87
grep -ro 'text-stone-[0-9]*' --include=*.tsx . | wc -l   # 483
grep -ro 'text-\[1[01]px\]'  --include=*.tsx . | wc -l   # 141
grep -ro 'aria-'       --include=*.tsx . | wc -l    # 12
for k in land_cover parcel_viability data_sufficiency crop_verification \
         driver_captions footprint score_history; do
  echo "$k: $(grep -rl "$k" --include=*.ts --include=*.tsx . | wc -l)"   # all 0
done
```
