# Frontend Stage 01 — Routing & Pages (Updated Deep-Dive)

## Purpose & Role

This stage is the entire user-facing surface of the system: three pages (login, AgriStack sandbox/ingest, dashboard) and a single `proxy.ts` gate deciding who gets in. For a lending-facing tool, this is where a loan officer or field agent actually experiences everything the nine backend stages computed — if this layer is slow, confusing, or fails silently, the sophistication of the scoring pipeline behind it is irrelevant to the person using it.

## Present Condition — How It Actually Works Today

1. **Three real pages:** `/login` (credential form), `/agristack` (sandbox: endpoints, editors, webhook tab, Save to Platform), `/dashboard` (farmer search → enqueue → poll → tabbed report). Root `/` redirects to `/agristack`.
2. **Auth gate (`proxy.ts`):** public prefixes are `/login`, `/api/login`, `/api/logout`, and anything under `/webhook`; everything else requires a valid `auth-token` cookie verified via `verifyJWT`, else redirect to `/login?callbackUrl=…`.
3. **Dashboard behavior:** form submit → `runAssessmentJob` → `pollJobStatus` every 3 seconds until SUCCESS/FAILED → render tabbed sections (overview / cropPerf / weather / cycles / ai).
4. **No dedicated farmer list/picker** — the operator must already know the `farmer_id` to run an assessment.
5. **Agristack page can call sandbox APIs directly from the browser** (an IP-allowlist workaround), and dynamically rewrites `sender_uri` from `window.location` / `NEXT_PUBLIC_APP_DOMAIN`.

## Ground Reality — What This Means Operationally

- **Requiring the operator to already know a `farmer_id` is a real usability gap for the actual field context this product targets.** Loan officers and field agents working with smallholder farmers typically think in terms of farmer name, village, or a physical land-record reference — not an internal database ID. Without a searchable farmer list/picker, this system currently assumes a workflow step (looking up the ID elsewhere) that adds real friction for exactly the non-technical field staff who are likely to be the primary users.
- **A 3-second poll loop against a backend pipeline that can genuinely take minutes** (satellite download, weather API calls, optional LLM narrative — see backend Stages 02, 05, 08) means the dashboard is issuing a meaningful number of HTTP requests over what could be a multi-minute wait, with only status text as feedback. In rural/semi-urban Indian network conditions (variable 4G, occasional connectivity drops common in the field), a bare "processing…" indicator with no stage-level context is a worse experience than it would be on reliable urban broadband — a loan officer waiting several minutes with no sense of *what* is happening (satellite fetch vs. weather vs. scoring) is more likely to assume the system has hung and retry or abandon.
- **The dashboard assumes a job+poll architecture exclusively**, which is consistent with backend Stage 09's async design, but it does mean there's no offline-tolerant or resumable pattern — if a field agent's connection drops mid-poll (plausible in rural coverage areas), there's no described mechanism to resume watching an in-flight job other than reloading and re-searching, which depends on them remembering the job was already running rather than accidentally re-triggering a duplicate assessment.
- **The browser-direct sandbox-API-call pattern on the Agristack page (bypassing the Next.js BFF for IP-allowlist reasons) is a reasonable pragmatic workaround**, but it does mean sandbox credentials/behavior are partly exposed to whatever the browser can see and do — worth keeping in mind as a slightly different trust boundary than the rest of the app, which otherwise routes everything through the BFF specifically to keep secrets server-side.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Add a searchable farmer picker backed by `farm_info`** (already flagged as medium in the original doc) — even a simple name/village/LGD-code search-as-you-type would remove the single biggest usability gap for the actual field-staff persona this product serves; this is a comparatively small frontend + one BFF endpoint effort relative to its impact on real usage.
2. **Surface backend `pipeline_stages` progress during polling** (already flagged in both the frontend Stage 05 doc and backend Stage 09) — since the backend already emits stage labels like `2_satellite`, `4_cycles`, `6_weather`, showing "Fetching satellite imagery…" → "Analyzing crop cycles…" → "Scoring creditworthiness…" instead of an opaque spinner is a well-established pattern for long-running async operations (progressive disclosure of pipeline stage), and directly addresses the "did this hang?" anxiety that matters more on flaky rural connections than on reliable office broadband.
3. **Consider a lightweight resumable-job pattern** — persisting the last-known `job_id` for a farmer search in local component state (not localStorage per the artifact constraints, but this is a real Next.js app, not an artifact, so browser storage is legitimate here) so that a reload or reconnect can rejoin an in-flight job's polling rather than defaulting to "search again," reducing duplicate-assessment risk from confused re-submission.
4. **Consider a lightweight service-worker or connectivity-aware retry/backoff on the poll loop** rather than a fixed 3-second interval regardless of network state — on flaky connections, a fixed aggressive poll interval can itself contribute to perceived unresponsiveness (failed requests queuing up) rather than helping; exponential backoff on failure with a "reconnecting…" state would be more honest UX.
5. **Replace the create-next-app boilerplate still sitting in the root README** (already flagged as a quick win) — a small thing, but worth doing alongside any onboarding-documentation pass, since new contributors' first impression of the repo currently starts with irrelevant scaffolding text.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given the actual field-user persona |
|---|---|---|
| Quick win | Replace root/frontend README boilerplate with project-specific docs | First-impression clarity for new contributors |
| Medium | Searchable farmer picker (name/village/LGD) instead of requiring a known `farmer_id` | Removes the single largest usability gap for field-staff/loan-officer users who don't think in internal IDs |
| Medium | Surface `pipeline_stages` progress text during polling instead of opaque status | Reduces "did this hang?" anxiety on variable rural connectivity during multi-minute waits |
| Medium | Resumable job-watching after reload/reconnect | Reduces duplicate-assessment risk when field connectivity drops mid-wait |
| Medium | Connectivity-aware backoff on the poll loop instead of a fixed 3s interval regardless of network state | More honest UX under flaky connections common in rural field use |

## Interfaces to Other Stages (unchanged, restated for continuity)

Feeds Stage 03 API routes and Stage 05 dashboard data; webhooks → Stage 06.

---
*This document supersedes the original `01-routing-pages.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
