# Frontend Stage 02 — State Management (Updated Deep-Dive)

## Purpose & Role

Two genuinely different auth/state concepts coexist here: the app's own session (httpOnly JWT cookie, protecting the lending dashboard itself) and an AgriStack OAuth access token (Redux, used only for sandbox calls to India's AgriStack farmer-data gateway). Confusing these two — assuming Redux governs app access, or assuming the cookie governs AgriStack calls — is the main risk this stage's design has to guard against, and largely does, but the split is subtle enough to warrant explicit reinforcement for anyone new to the codebase.

## Present Condition — How It Actually Works Today

1. `AuthProvider` calls `GET /api/me` on app load to establish `user`/`loading` state from the JWT cookie.
2. AgriStack sandbox flow: user hits the token endpoint → response stored via `setToken` in Redux (`accessToken`, `tokenType`, `expiresIn`, `refreshToken`) → subsequent AgriStack proxy calls attach the Bearer token from the store.
3. Logout clears the app cookie server-side; Redux token clearing is a UI-level responsibility on logout paths, not automatic.
4. Redux persists only in memory — a page refresh loses the AgriStack token entirely and requires re-fetching, while the app session (cookie) survives a refresh normally.

## Ground Reality — What This Means Operationally

- **In-memory-only Redux token storage means any page refresh during an active AgriStack sandbox session forces a full re-auth against AgriStack**, which is a genuinely different (and worse) experience than the app's own cookie-backed session surviving refreshes transparently. For field staff who may refresh a page reflexively when a UI feels slow (a plausible reaction on a flaky connection — see frontend Stage 01), this asymmetry is likely to be a recurring, mildly confusing friction point ("why did I get logged out of AgriStack but not the app?").
- **AgriStack token refresh isn't automated beyond storing a `refreshToken` field** — meaning if a session runs long enough for the access token to expire mid-workflow (e.g., a loan officer mid-way through ingesting several farmer payloads via the sandbox), the practical failure mode is likely an AgriStack call failing partway through a multi-step task, not a graceful silent refresh. Worth confirming this explicitly, since silent token expiry mid-task is a common source of confusing "it worked a minute ago" support tickets.
- **The dual-model split existing without much runtime enforcement (relying on developer discipline / documentation, per the original doc's honest "dual auth models confuse new contributors" note) is a maintainability risk more than a security one** — the actual security boundaries (httpOnly cookie for admin session, Redux for a third-party API token) are individually reasonable choices; the risk is a future contributor building a new feature that reads the wrong token for the wrong purpose, not that either mechanism is unsound in isolation.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Implement automatic AgriStack token refresh using the stored `refreshToken`** (already flagged as medium) — a small axios/fetch interceptor pattern (detect 401 from an AgriStack-proxied call, attempt refresh, retry once) is standard practice and would remove the silent mid-task failure mode described above.
2. **Persist the AgriStack token server-side (encrypted) if UX requires fewer re-auths** (already flagged as medium) — an alternative to client-refresh is treating the AgriStack token like the app's own session: store it server-side keyed to the app session, so a page refresh doesn't lose sandbox context at all; this is a larger change but removes the refresh-asymmetry friction entirely rather than just automating around it.
3. **Unify logout to always call `clearToken()`** (already flagged as medium/quick) — ensuring every logout path, not just the "intended" ones, clears the Redux token consistently closes a small but real gap where a stale AgriStack token could persist in memory past an intended full logout within the same tab session.
4. **Add a lightweight runtime type/namespace guard** — e.g., naming the Redux slice something unambiguous like `agristackToken` rather than a generic `token`, and correspondingly naming cookie-session helpers distinctly, so a contributor reading either in isolation gets an immediate naming signal about which system they're touching (this is a naming discipline fix, not an architecture change, but directly targets the "confuses new contributors" risk called out in the original doc).

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters |
|---|---|---|
| Quick win | Unify logout to always call `clearToken()` on every logout path | Closes a gap where a stale AgriStack token could persist past an intended logout |
| Quick win | Rename the Redux slice/reducer key from generic `token` to `agristackToken` (or similar) | Reduces "which system am I touching" ambiguity for new contributors, directly addressing the documented confusion risk |
| Medium | Automatic AgriStack token refresh via stored `refreshToken` on 401 | Prevents silent mid-task failures when a long sandbox session outlives the access token |
| Medium | Server-side encrypted persistence of the AgriStack token, keyed to app session | Removes the refresh-page-loses-sandbox-context asymmetry entirely, better UX than client-side refresh alone |

## Interfaces to Other Stages (unchanged, restated for continuity)

Sandbox pages (01) and API token/agristack routes (03); independent of assessment Redux (assessment state is local React state in dashboard).

---
*This document supersedes the original `02-state-management.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
