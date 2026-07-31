# Frontend Stage 03 — API Routes & Integration (Updated Deep-Dive)

## Purpose & Role

This is the BFF (backend-for-frontend) layer keeping AgriStack credentials, Mongo credentials, and the pipeline API off the browser — with one deliberate, documented exception (the Agristack sandbox page's browser-direct calls for IP-allowlist reasons, see frontend Stage 02). It's also the layer that decides, per-deployment, whether assessments actually get processed at all (the FastAPI-vs-legacy-Mongo-insert enqueue fork), which makes it a quieter but real point of operational risk.

## Present Condition — How It Actually Works Today

1. **Auth routes:** `/api/login` (bcrypt against `users`, sets 7-day `auth-token` cookie), `/api/logout`, `/api/me`.
2. **AgriStack proxies:** `/api/token`, `/api/agristack`, `/api/krishi-dss-seek` — all server-side proxies keeping credentials off the browser for these specific calls.
3. **Ingest:** `/api/ingest-farmer` parses AgriStack-shaped JSON, clusters parcels via `farmerParcelCluster`, upserts `farm_info`.
4. **Enqueue dual path:** if `PIPELINE_API_URL`/`ASSESSMENT_API_URL` is set, POST to FastAPI's `/v1/jobs/assess` on the Python service under `backend/Credit_assessment` (optionally with an `X-API-Key`); otherwise insert directly into Mongo `jobs` as QUEUED and log that `worker.py` (run from that package) is required to ever process it.
5. **Status:** `/api/assess/status/[id]` always reads Mongo directly — never calls FastAPI — meaning Mongo is the sole source of truth for job state regardless of which enqueue path was used.
6. **Cookies:** `httpOnly`, `sameSite: 'lax'`, `secure` only in production.

## Ground Reality — What This Means Operationally

- **The legacy enqueue fallback (direct Mongo insert when `PIPELINE_API_URL` is unset) is the same "silent stall" risk flagged in backend Stage 09, but it originates here** — this route is the one deciding, per-deployment-configuration, whether a submitted assessment has any actual guarantee of being processed. A misconfigured or newly-spun-up environment where someone forgot to set `PIPELINE_API_URL` and isn't running `worker.py` will accept farmer assessment requests indefinitely with no functional backend consuming them — and because the enqueue call itself succeeds (the Mongo insert works fine), there's no error surfaced to the user or operator at the point of submission. This is a two-file problem (this route plus backend Stage 09) but the actual point of failure prevention (validating a consumer exists before accepting the job) most naturally belongs here, at intake.
- **No rate limiting on enqueue** (already flagged) is a real operational risk specifically because each successful enqueue eventually triggers the most expensive part of the entire system (backend Stages 02/05/08's external API calls) — a script, bug, or even an over-eager double-click UI issue that fires enqueue repeatedly could generate a meaningful and unnecessary GEE/STAC/NASA POWER/Groq cost spike with nothing at this layer to slow it down.
- **Sample credentials potentially present in `config/endpoints.ts`** (already flagged as a secret-hygiene issue) is worth treating with real urgency in a system handling financial/lending data — even "sandbox" credentials checked into a repo are a common source of accidental production exposure if that file is ever copy-pasted into a real config without review.
- **No idempotency key on ingest** means a webhook-triggered or manually retried "Save to Platform" action could, depending on how `farmer_id`/parcel matching works in `farmerParcelCluster`, create duplicate or conflicting `farm_info` records rather than cleanly updating an existing one — worth explicit verification given how central `farm_info` is to every downstream backend stage.
- **Job ownership isn't checked on the status GET beyond generic login** — the same concern flagged in backend Stage 09 shows up here as well, since this route is the actual point where an authenticated-but-unauthorized user could potentially read another farmer's/officer's assessment status by guessing or being handed an ObjectId.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Validate a consumer exists (or explicitly warn) before accepting an enqueue request when using the legacy Mongo-insert fallback** — e.g., a lightweight periodic worker-heartbeat check (a worker writes a `last_seen` timestamp to Mongo; enqueue checks it's recent before accepting, or at minimum surfaces a warning banner in the UI) rather than accepting jobs into a queue with no confirmed consumer.
2. **Add rate limiting on the enqueue route** — a standard token-bucket or fixed-window limiter (many hosting platforms and edge/proxy layers offer this natively, e.g., Vercel Edge Config + middleware, or a simple per-IP/per-session counter in Mongo/Redis) is a well-established, low-effort mitigation for exactly this "expensive downstream operation triggered by an unauthenticated-cost-wise-cheap endpoint" pattern.
3. **Move all sample/sandbox credentials out of `config/endpoints.ts` and into env-only samples** (already flagged as quick win) — treat this as security hygiene work with real urgency given the financial-lending context, not routine cleanup.
4. **Add idempotency keys to the ingest route** — accept an optional client-generated idempotency key (or derive one from AgriStack's own correlation ID, since Stage 06 already tracks these) so retried "Save to Platform" actions cleanly no-op or update rather than risk duplicate `farm_info` records.
5. **Add ownership/authorization scoping on job-status GET** (already flagged as medium, and echoed from backend Stage 09) — confirm the requesting session is actually authorized to view the specific farmer/job in question, not just that they're logged in generally.
6. **Consider exposing a shared OpenAPI-derived type contract with FastAPI** (already flagged as medium in the original doc) — since this BFF layer and the Python pipeline both need to agree on job/assessment shapes, generating TypeScript types from FastAPI's OpenAPI schema (a common pattern via tools like `openapi-typescript`) would reduce the risk of silent schema drift between the two codebases as either evolves independently.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Quick win | Move sandbox/sample credentials out of `config/endpoints.ts` into env-only samples | Real secret-hygiene urgency in a financial-lending context |
| Quick win | Rate limit the enqueue route | Prevents cost spikes on the most expensive backend operations (satellite/weather/LLM calls) from a cheap frontend trigger |
| Medium | Worker-heartbeat check (or UI warning) before accepting jobs via the legacy Mongo-insert enqueue fallback | Prevents silent, indefinite job stalls when `PIPELINE_API_URL` is misconfigured — the frontend-side half of a backend Stage 09 risk |
| Medium | Idempotency keys on farmer ingest | Prevents duplicate/conflicting `farm_info` records from retried "Save to Platform" actions |
| Medium | Ownership/authorization scoping on job-status GET | Standard multi-tenant safeguard for financial/personal data, echoed from backend Stage 09 |
| Medium | Shared OpenAPI-derived TypeScript types with FastAPI | Reduces silent schema drift risk between the two independently-evolving codebases |

## Interfaces to Other Stages (unchanged, restated for continuity)

Dashboard client (04/05); webhooks (06); backend job runner (backend/09).

---
*This document supersedes the original `03-api-routes-integration.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
