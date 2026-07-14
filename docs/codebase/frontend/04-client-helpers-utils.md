# Frontend Stage 04 — Client Helpers & Utils (Updated Deep-Dive)

## Purpose & Role

The shared utility layer underneath everything else in the frontend — assessment orchestration, parcel geometry clustering (which directly determines the `field_area_ha`/geometry that feeds backend Stage 01), Mongo connectivity, JWT signing, and display formatting. Bugs or weak defaults here don't stay contained to one page; `farmerParcelCluster`'s output flows straight into the backend pipeline's spatial envelope, and `jwt.ts`'s secret handling governs every authenticated session in the app.

## Present Condition — How It Actually Works Today

1. **`assessmentClient.ts`:** `runAssessmentJob` (enqueue), `pollJobStatus` (status GET), `ingestFarmerData` (raw ingest POST) — the browser's only interface to the BFF for assessment workflows.
2. **`farmerParcelCluster.ts`:** Haversine-based clustering of nearby parcels from registry payloads, estimating centroid and total hectares, producing geometry suitable for `farm_info`.
3. **`mongodb.ts`:** singleton `MongoClient` cached on `global` to survive Next.js HMR; DB name defaults to `agristack`; non-production may set `tlsAllowInvalidCertificates`.
4. **`jwt.ts`:** HS256 signing/verification via `jose`; secret resolution order is `AUTH_SECRET` → `NEXTAUTH_SECRET` → **the literal string `'fallback-secret'`**.
5. **`format.ts`:** display helpers (`formatRupees`, `formatPct`, `riskColor`, etc.) used across dashboard sections.

## Ground Reality — What This Means Operationally

- **The `'fallback-secret'` hardcoded JWT fallback is the most urgent single item in this entire frontend deep-dive, full stop.** If any deployment environment — a staging server, a demo instance, a rushed production rollout — fails to set `AUTH_SECRET`/`NEXTAUTH_SECRET`, every session token in that deployment is signed with a secret that is publicly visible in the source code. For a system gating access to farmer financial and personal data, this isn't a "known limitation," it's a live authentication-bypass risk sitting in the codebase today, and it's exactly the kind of thing that gets missed precisely because the app *appears* to work correctly with the fallback in place (login succeeds, sessions persist) — there's no visible symptom of the misconfiguration until someone deliberately checks env vars or an attacker forges a token.
- **`farmerParcelCluster`'s Haversine clustering thresholds being hardcoded constants, undocumented per-state, is a real accuracy risk feeding directly into backend Stage 01's geometry handling.** India's parcel density varies enormously — a clustering distance tuned for sparse holdings in one state could incorrectly merge genuinely separate small adjacent parcels in a densely subdivided smallholder region (common in much of India), or conversely fail to cluster parcels that legitimately belong to one farmer's fragmented holding (also extremely common — Indian landholding fragmentation across multiple non-contiguous plots under one owner is well documented in agricultural census data). Since backend Stage 01 already flags geometry-quality as a real risk (feeding bbox sizing and area duality decisions), this clustering step is an upstream contributor to that risk that's worth tuning deliberately rather than leaving as an unexamined constant.
- **`tlsAllowInvalidCertificates` in non-production Mongo connections is a reasonable dev-convenience default but worth explicit guarding** — a misconfigured `NODE_ENV` or an environment variable typo that lands a "non-production" TLS posture in an actual production deployment would silently weaken transport security for a database holding farmer financial records, and this class of misconfiguration is exactly as invisible in normal operation as the JWT fallback-secret issue above.
- **Client-side error surfacing depending on `res.json()` shapes from the BFF** means `assessmentClient` has an implicit, untyped contract with the API routes (03) — any BFF response-shape change that isn't mirrored here fails silently or with a confusing error rather than a clear typed compile-time signal, which is the same schema-drift risk flagged from the other direction in frontend Stage 03.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Fail boot (not just log a warning) if `AUTH_SECRET`/`NEXTAUTH_SECRET` is missing in production** — already flagged as a quick win in the original doc, and it deserves to be treated as the single highest-priority item across the whole frontend review; a hard startup failure (throwing during Next.js server initialization, or a build-time env check) is standard practice for any secret this consequential, and removes any possibility of the fallback silently shipping to a real deployment.
2. **Document and regionally tune `farmerParcelCluster`'s distance thresholds**, ideally informed by the same agro-climatic-zone or state-level parcel-density data referenced in backend Stage 01's LGD/ecoregion recommendations — since both problems (buffer sizing in Stage 01, clustering distance here) are really the same underlying "India's parcel geometry varies regionally" issue seen from two different code layers, solving them with a shared regional-parameters source would be more coherent than tuning each independently.
3. **Add Jest/Vitest fixtures for `farmerParcelCluster`** (already flagged as medium) — given how directly this function's output feeds the backend's spatial envelope, deterministic fixture tests (known input parcel sets → expected cluster/centroid/area output) are worth prioritizing before regional tuning work, so tuning changes can be validated against known-good behavior rather than tested only by eye.
4. **Explicitly guard `tlsAllowInvalidCertificates` behind an unambiguous, separately-named flag** (not just implicit `NODE_ENV` inference) so a misconfigured environment variable can't silently weaken transport security for financial data.
5. **Share assessment types with FastAPI's OpenAPI schema** (already flagged as medium, echoed from frontend Stage 03) — resolving the untyped `res.json()` contract risk from this side as well, ideally via the same generated-types approach so both fixes reinforce each other rather than being solved twice independently.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters |
|---|---|---|
| Urgent/Quick win | Fail boot in production if `AUTH_SECRET`/`NEXTAUTH_SECRET` is unset — remove the `'fallback-secret'` silent fallback | Currently a live authentication-bypass risk with no visible symptom until actively checked or exploited |
| Quick win | Explicitly, separately guard `tlsAllowInvalidCertificates` rather than implicit `NODE_ENV` inference | Prevents silent transport-security weakening for financial data on environment misconfiguration |
| Medium | Jest/Vitest fixture tests for `farmerParcelCluster` | Protects the function whose output most directly feeds backend Stage 01's spatial envelope |
| Medium | Regionally tune (and document) Haversine clustering distance thresholds, ideally from the same regional-parameters source recommended for backend Stage 01 | Addresses the same "India parcel geometry varies regionally" issue from the frontend side; currently an unexamined hardcoded constant |
| Medium | Share assessment types generated from FastAPI's OpenAPI schema | Removes the untyped `res.json()` contract risk between frontend and backend |

## Interfaces to Other Stages (unchanged, restated for continuity)

Used by pages (01), API routes (03), dashboard (05), webhooks save path (06).

---
*This document supersedes the original `04-client-helpers-utils.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
