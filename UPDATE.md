# Update: AgriStack India connectivity via AWS Lambda (Mumbai)

**Date:** 2 August 2026  
**Status:** Outbound Seek + inbound webhook → Mongo proven on Lambda (`ap-south-1`)  
**Function URL:** `https://e2luibgn3cnl42vb4dp6ddcxe40jmbpc.lambda-url.ap-south-1.on.aws/`

---

## 1. Context — what problem we were solving

Our credit pipeline has two layers:

1. **Frontend (Next.js)** — AgriStack sandbox UI, auth, farmer ingest, dashboard, job enqueue  
2. **Backend (FastAPI on Render / local)** — satellite → crop cycles → risk/credit scoring  

AgriStack sandbox integration needs:

| Direction | What happens | Why it failed on free cloud hosts |
|-----------|----------------|-------------------------------------|
| **Outbound** | Our app calls AgriStack Token + Seek APIs | Server IPs outside India (e.g. Render / typical Vercel regions) are often blocked or return HTML instead of JSON |
| **Inbound** | AgriStack POSTs farmer data to our `sender_uri` webhook | Webhook URL hosted outside India may be rejected by geo / security policy |

We confirmed in code that Seek is **not** called from the browser. The flow is:

```text
Browser → Next.js API route (/api/token, /api/agristack, /api/krishi-dss-seek)
       → sandbox.agristack.gov.in
```

So the **IP AgriStack sees is whichever machine runs those Next.js routes** (localhost, Render, Vercel, etc.) — not the user’s browser.

We also have a domain (`zucarto.com`) and an AWS account with **$100 credit**. Full Lightsail frontend in Mumbai remains valid, but we tested a **cheaper, narrower** approach first: only move the AgriStack surface to **AWS Lambda in Mumbai (`ap-south-1`)**.

---

## 2. What we decided (architecture choice)

### Chosen approach (validated)

```text
Browser / Next.js UI  (local today; Vercel or Lightsail later)
        │
        │  (next: proxy these calls)
        ▼
AWS Lambda Function URL  (Mumbai / ap-south-1)
        ├── POST action=token|seek|token_and_seek  → AgriStack outbound
        └── POST /webhook/.../on-seek              → MongoDB Atlas
                │
                ▼
        Same collections the UI already reads
        (webhook_farmers_responses, webhook_responses, webhook_kdss_responses)
                │
                ▼
Save to Platform → farm_info → FastAPI pipeline (Render / local)
```

### Why Lambda + Mongo (not webhook-only Lambda)

- **Webhook-only Lambda is not enough.** If Token/Seek still run on Vercel/Render, outbound geo blocks remain.  
- **Saving webhooks to Mongo is better than Lambda-only storage**, because:
  - Frontend already lists webhooks via `/api/webhook-responses`
  - Ingest / “Save to Platform” already looks up `webhook_*` collections by `correlation_id`
  - Same Atlas DB as the rest of the product — no second data store

### Why not “full frontend on Lightsail” yet

Lightsail Mumbai for the whole Next app still works and is simpler operationally long-term. Lambda was chosen as a **cost-efficient proof** that Mumbai egress + ingress satisfies AgriStack, while keeping UI deploy options open (local / Vercel / later Lightsail).

---

## 3. What we did (step by step)

### 3.1 Created Mumbai Lambda Function URL

- Region: **Asia Pacific (Mumbai) `ap-south-1`**
- Public HTTPS Function URL (no ALB — keeps cost low)
- Initial hello handler confirmed the URL was reachable

### 3.2 Fixed runtime packaging issues

| Issue | Cause | Fix |
|-------|--------|-----|
| `502` / Init Error | File was `index.mjs` (ESM) but code used `exports.handler` (CommonJS) | Switched to `export const handler = ...` |
| AgriStack not reached during 502s | Handler never started | Not a geo failure — deployment bug |

### 3.3 Proved outbound Token from Lambda

- Dummy credentials → AgriStack returned **JSON** `400` (“username or password incorrect”), **not** an HTML WAF/block page → **geo OK**
- Real sandbox credentials (registry_sandbox user) → **`access_token` received** (`200`)

### 3.4 Proved outbound Seek from Lambda

- Extended Lambda with `action=token_and_seek`
- Tested `farmer_id=11960881396`
- AgriStack returned **`ack_status: ACK`** with a `correlation_id`

### 3.5 Added webhook receiver that writes Mongo

Code lives in the repo:

- `infra/agristack-lambda/index.mjs`
- `infra/agristack-lambda/package.json`
- Deployable `infra/agristack-lambda/function.zip` (includes `mongodb` driver)

Webhook paths mirror the Next.js routes:

| Lambda path | Mongo collection | `source` field |
|-------------|------------------|----------------|
| `/webhook/farmers/on-seek` | `webhook_farmers_responses` | `agristack-farmers` |
| `/webhook/on-seek` | `webhook_responses` | `agristack` |
| `/webhook/kdss/on-seek` | `webhook_kdss_responses` | `agristack-kdss` |

Document shape matches existing frontend webhooks:

- `receivedAt`, `headers`, `body`, `rawBody`, plus `via: "lambda-ap-south-1"` for traceability  

Lambda env vars used:

- `MONGODB_URI`
- `MONGODB_DATABASE=agristack`
- `LAMBDA_PUBLIC_BASE_URL` (plain Function URL base, no markdown)
- Optional: `WEBHOOK_SECRET` (same idea as frontend; unset = allow in sandbox)

### 3.6 End-to-end verification

| Test | Result |
|------|--------|
| Synthetic POST to `/webhook/farmers/on-seek` | Saved in Mongo (`via: lambda-ap-south-1`) |
| Seek with clean `sender_uri` ending in `/on-seek` | **ACK** |
| Real AgriStack callback for Seek correlation | Stored in Mongo under matching collection |
| UI path | Operator can open Webhook Responses → Save to Platform (existing flow) |

### 3.7 Bug we hit and fixed (sender_uri)

AgriStack error:

```text
sender_uri.invalid — Must end with '/on-seek'
```

**Root cause:** `LAMBDA_PUBLIC_BASE_URL` had been pasted as a **markdown link**  
`[https://...](https://...)`, so the composed `sender_uri` was malformed.

**Fixes:**

1. Set env to a **plain** URL:  
   `https://e2luibgn3cnl42vb4dp6ddcxe40jmbpc.lambda-url.ap-south-1.on.aws`
2. Code now **sanitizes** accidental markdown in `LAMBDA_PUBLIC_BASE_URL` before building webhook URLs

Default Seek `sender_uri` should be:

```text
https://e2luibgn3cnl42vb4dp6ddcxe40jmbpc.lambda-url.ap-south-1.on.aws/webhook/farmers/on-seek
```

---

## 4. Impact — what this changes for the product

### Solved

1. **India outbound connectivity** for AgriStack Token + Seek without hosting the entire frontend in Mumbai yet  
2. **India inbound webhooks** landing in the **same Mongo collections** the app already uses  
3. **Cost-efficient use of AWS credit** — Function URL + small Lambda, no ALB/NAT/RDS  
4. Clear separation: **AgriStack edge in Mumbai Lambda**; **scoring API can stay on Render/local**

### Not yet solved (still next work)

1. Local/Vercel Next.js routes still call AgriStack **directly** unless we rewire them to Lambda  
2. Production frontend hosting choice (Vercel vs Lightsail `agri.zucarto.com`) not finalized  
3. Custom domain (`zucarto.com`) not yet pointed at Lambda (Function URL works; domain is nicer for ops)  
4. Webhook auth hardening (`WEBHOOK_SECRET`) optional for sandbox; needed for stricter production  

### What did *not* need to move

- FastAPI credit pipeline (Render / local) — no AgriStack geo requirement for scoring  
- MongoDB Atlas — already shared; Lambda writes into it  
- Dashboard / farm assessment UX — unchanged once webhooks are in Mongo  

---

## 5. How the system works now (current truth)

### Working today

```text
[Proven]
You → Lambda (Mumbai) → AgriStack Token/Seek → ACK
AgriStack → Lambda /webhook/farmers/on-seek → Mongo webhook_* collections
Frontend (local) → reads Mongo → Save to Platform → farm_info
Frontend → PIPELINE_API_URL → FastAPI → credit assessment
```

### Still the old path for UI “Run” in sandbox (until we wire it)

```text
[Not yet rewired]
Browser → Next /api/agristack → AgriStack
  ↑
  Fails or is fragile if that Next server is outside India
```

So: **infrastructure proof is done**; **app wiring to always use Lambda** is the main follow-up.

---

## 6. Repo artifacts

| Path | Purpose |
|------|---------|
| `infra/agristack-lambda/index.mjs` | Token, Seek, webhook → Mongo handler (ESM) |
| `infra/agristack-lambda/package.json` | `mongodb` dependency, `"type": "module"` |
| `infra/agristack-lambda/function.zip` | Upload package for AWS Lambda |
| `infra/agristack-lambda/check_webhooks.mjs` | Dev helper to inspect recent webhook docs |

Existing frontend webhook routes remain valid for local/Cloudflare-tunnel demos; Lambda is the **India-stable** path.

---

## 7. Next steps and updates (recommended order)

### Step A — Wire frontend to Lambda (highest priority)

**Why:** So the AgriStack sandbox page always uses Mumbai IPs, even if the UI is on Vercel or a non-India host.

**What to change:**

1. Add env, e.g.  
   `AGRISTACK_PROXY_URL=https://e2luibgn3cnl42vb4dp6ddcxe40jmbpc.lambda-url.ap-south-1.on.aws`
2. Update Next routes:
   - `/api/token` → forward to Lambda `action=token` (or dedicated path)
   - `/api/agristack` → forward Seek body + Bearer token to Lambda `action=seek` (or proxy raw Seek)
   - `/api/krishi-dss-seek` → same pattern for KDSS (extend Lambda if needed)
3. Set Seek `sender_uri` / `NEXT_PUBLIC_APP_DOMAIN` equivalent for AgriStack to the Lambda farmers webhook URL (not localhost, not Render, not a foreign Vercel URL)

**Acceptance:** Clicking Seek in the UI produces ACK and a new Mongo webhook row with `via: lambda-ap-south-1`.

### Step B — Optional: custom domain on Lambda / API Gateway

**Why:** Stable branded URL, e.g. `https://agri-hooks.zucarto.com/webhook/farmers/on-seek`.

**What:** Cloudflare DNS `CNAME`/`A` → Lambda Function URL custom domain or API Gateway HTTP API in `ap-south-1`.

### Step C — Frontend hosting decision

| Option | When to use |
|--------|-------------|
| **Keep UI local + Lambda for AgriStack** | Active development / demos |
| **Vercel UI + Lambda AgriStack** | Nice DX; geo handled by Lambda |
| **Lightsail Mumbai full Next app** | Simpler single India host; less proxy glue |

Recommendation after Step A: **Vercel (or any host) for UI + Mumbai Lambda for AgriStack** is enough; Lightsail only if you want everything under one India VM.

### Step D — Production hardening

- Set `WEBHOOK_SECRET` if AgriStack (or a gateway) can send a shared header  
- Restrict Function URL auth later (IAM / secret) without breaking AgriStack callbacks  
- Rotate any credentials that were shared in chat/logs  
- CloudWatch alarms on Lambda errors / Mongo failures  
- Increase timeout/memory only if Seek+Mongo cold starts need it (currently fine at ~30s / 256MB)

### Step E — KDSS parity

Extend Lambda with Krishi DSS Seek proxy + ensure `/webhook/kdss/on-seek` is used as `sender_uri` for that endpoint (path already implemented for receive).

### Step F — Docs / ops

- Document Lambda env vars in `.env.example` (frontend) as `AGRISTACK_PROXY_URL`  
- Note in README that AgriStack must use Mumbai Lambda, not Render IP  
- Keep this file updated when Step A lands  

---

## 8. Immediate checklist for the team

- [x] AWS Mumbai Lambda Function URL created  
- [x] Token from Lambda works  
- [x] Seek from Lambda returns ACK  
- [x] Webhook receiver saves to Mongo (same collections as UI)  
- [x] Real AgriStack callback observed in Mongo  
- [ ] Fix/confirm `LAMBDA_PUBLIC_BASE_URL` is **plain URL** (no markdown)  
- [ ] Re-upload latest `function.zip` if sanitization fix not yet on AWS  
- [ ] Wire Next.js `/api/token` + `/api/agristack` to Lambda  
- [ ] Point UI Seek `sender_uri` at Lambda `/webhook/farmers/on-seek`  
- [ ] Decide Vercel vs Lightsail for long-term frontend  
- [ ] Optional: attach `zucarto.com` subdomain to Lambda webhooks  

---

## 9. One-line summary

**We proved that a small Mumbai Lambda can both call AgriStack and receive its webhooks into our existing MongoDB, solving the out-of-country hosting block without moving the whole credit pipeline to AWS — next we must route the Next.js AgriStack API proxies through that Lambda so the product UI uses this path by default.**
