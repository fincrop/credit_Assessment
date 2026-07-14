# Frontend Stage 06 — Webhook Integration (Updated Deep-Dive)

## Purpose & Role

AgriStack's Seek/On-Seek pattern is asynchronous by design (ACK now, payload later via webhook), and this stage is the bridge that makes that usable: capturing whatever AgriStack eventually POSTs, letting an operator review it, and manually promoting it into `farm_info` for the actual credit pipeline. Because the webhook routes are deliberately public (unauthenticated, by necessity — AgriStack needs to reach them), this is also the one place in the frontend where the trust model is intentionally different from everything else, and that needs to be handled with corresponding care.

## Present Condition — How It Actually Works Today

1. **Three parallel webhook routes** (`on-seek`, `farmers/on-seek`, `kdss/on-seek`) storing raw payloads plus receive metadata into three separate Mongo collections.
2. **`proxy.ts` leaves `/webhook/*` unauthenticated** so AgriStack can reach them without needing to authenticate as an app user.
3. **UI flow:** operator lists incoming payloads via `/api/webhook-responses` (paginated), selects one, and the "Save to Platform" action calls `ingest-farmer` to cluster parcels and upsert `farm_info`.
4. **No shared-secret/HMAC validation** on incoming webhook payloads — anyone who can reach the public URL can insert data into these collections.
5. **Local dev workaround:** `cloudflared` tunnel exposes localhost to AgriStack for webhook delivery during development.

## Ground Reality — What This Means Operationally

- **Unauthenticated public webhooks with no signature validation is the most significant security gap identified across the entire frontend review, and it deserves to be treated with real urgency given what flows through it.** Anyone who discovers or guesses one of these three webhook URLs can POST arbitrary JSON that gets stored and, if an operator doesn't scrutinize it carefully before clicking "Save to Platform," potentially gets promoted into `farm_info` — which is the record that seeds the *entire* backend credit-scoring pipeline (Stage 01 onward) for whatever `farmer_id` the junk payload claims to represent. This isn't just a data-quality nuisance; it's a potential vector for someone to inject a fabricated farm record that then receives a real, backend-computed credit assessment, or to pollute genuine records if `farmer_id`/parcel matching isn't airtight.
- **The manual "Save to Platform" step is currently the only safeguard between an unauthenticated public endpoint and the production credit pipeline**, and it depends entirely on an operator's judgment and attentiveness — a reasonable interim control, but a thin one, especially as ingest volume grows and review becomes more perfunctory over time (a well-documented pattern in any manual-review-of-high-volume-queue workflow: review quality degrades as volume and repetition increase).
- **Raw body retention for debugging AgriStack schema drift is a genuinely good practice** (already flagged as a strength) — but combined with no input validation, it also means whatever arbitrary payload someone sends gets durably stored, which has its own data-hygiene and storage-growth implications absent the already-flagged TTL indexes.
- **Correlation-ID matching UX depends on fields the original doc flags as needing manual verification "when debugging"** — meaning even legitimate AgriStack callbacks can currently fail to visibly join with their originating seek request in a way that's fully trustworthy, which compounds the review-fatigue risk above (an operator who's used to correlation matching being imperfect may become more inclined to eyeball-approve payloads rather than rigorously verify them).

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Implement HMAC/shared-secret signature validation on all three webhook routes immediately** (already flagged as a quick win, and it deserves elevation to urgent given the direct path into `farm_info` and the credit pipeline) — if AgriStack's webhook delivery supports a signing secret or a shared header value (common in webhook design generally, e.g., Stripe/GitHub-style HMAC-SHA256 signatures), validating it before storing (or at minimum before allowing "Save to Platform" promotion) closes the most consequential gap identified in this entire review. If AgriStack's specific integration doesn't support signing, an IP-allowlist fallback (if AgriStack publishes sending IP ranges) or a shared-secret query parameter would be a reasonable interim mitigation.
2. **Add automated schema/sanity validation before allowing promotion to `farm_info`** — even without cryptographic signing, validating that a payload has the expected AgriStack shape (required fields present, plausible geographic bounds for India, plausible area values) before an operator can click "Save to Platform" would catch obviously-malformed or malicious payloads without requiring a human to catch it by eye every time.
3. **Auto-ingest on verified correlation-ID match with an operator confirmation toggle** (already flagged as medium) — this is a good middle ground: once signature validation (item 1) and correlation matching are both trustworthy, reducing manual friction for the common legitimate case while keeping a confirmation step as a safety net, rather than the current fully-manual review of every single payload regardless of confidence.
4. **Add TTL indexes on the webhook response collections** (already flagged as medium) — controls storage growth from exactly the kind of unvalidated-payload accumulation described above, and is good practice independent of the security fixes since even legitimate raw-body retention doesn't need to be kept indefinitely.
5. **Consider surfacing a trust/confidence indicator in the UI list** (`WebhookResponses.tsx`) once validation exists — e.g., a badge distinguishing "signature verified" from "unverified" payloads, so that even during a transition period where not all sources are validated, an operator has an explicit signal rather than treating all listed payloads as equally trustworthy by default.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters |
|---|---|---|
| Urgent/Quick win | HMAC/shared-secret validation on all three webhook routes | Currently the most significant security gap in the frontend — the direct, unauthenticated path into `farm_info` and the entire backend credit pipeline |
| Quick win | TTL indexes on webhook response collections | Controls storage growth from raw, currently-unvalidated payload retention |
| Medium | Automated schema/sanity validation gate before "Save to Platform" promotion | Catches malformed/malicious payloads without relying solely on manual operator review |
| Medium | Auto-ingest on verified correlation match with confirmation toggle | Reduces review fatigue for the common legitimate case once validation is trustworthy, without removing the safety net |
| Medium | Trust/confidence badge in `WebhookResponses.tsx` UI list | Gives operators an explicit signal during any transition period before all sources are fully validated |

## Interfaces to Other Stages (unchanged, restated for continuity)

Sandbox page (01); ingest API (03); backend Stage 0/01 farm load.

---
*This document supersedes the original `06-webhook-integration.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
