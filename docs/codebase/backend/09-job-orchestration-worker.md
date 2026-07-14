# Backend Stage 09 — Job Orchestration & Worker (Updated Deep-Dive)

## Purpose & Role

This stage is the operational envelope around everything Stages 01–08 compute — it decides whether the pipeline can actually be run reliably at scale (multiple farmers, concurrent requests, retries after failure) or whether it's fundamentally a single-farmer-at-a-time batch tool wearing a web-API costume. Given every upstream stage in this system involves genuinely slow, expensive I/O (satellite downloads, weather API calls, optional LLM calls), this stage's concurrency and reliability model directly determines the real-world throughput and user experience of the whole product — a farmer waiting on a loan decision experiences Stage 09's design choices as directly as they experience the credit score itself.

## Present Condition — How It Actually Works Today

1. **Two equivalent consumers** share `process_assessment_job`: a dedicated `worker.py` polling Mongo `jobs` every 2.0s, and FastAPI's inline `BackgroundTasks` after `POST /v1/jobs/assess`.
2. **Job document** carries `farmer_id`, benefits overrides, `status` (QUEUED/RUNNING/SUCCESS/FAILED), timestamps, `result`/`error`, optional `require_classification`.
3. **Concurrency control:** a single `asyncio.Lock` (`_pipeline_job_lock`) serializes heavy pipeline runs within one FastAPI process — meaning, in the inline mode, **only one assessment actually runs at a time per process**, regardless of how many are queued.
4. **Dashboard polling** always reads Mongo directly (not the FastAPI layer), which is a sound decoupling choice — the UI doesn't need the pipeline process to be responsive to know job status.
5. **Legacy fallback:** if `PIPELINE_API_URL` is unset, Next.js inserts the QUEUED job itself and logs that a separate `worker.py` process is required to ever pick it up — i.e., there's a configuration state where jobs can be created but silently never processed unless someone remembers to run the worker.
6. **No dead-letter queue, retry count, or heartbeat** — a worker or process crash mid-RUNNING leaves a job permanently stuck in RUNNING with no automatic recovery.

## Ground Reality — What This Means Operationally

- **A single-process `asyncio.Lock` serializing all heavy runs is a hard throughput ceiling that will become the visible bottleneck the moment usage grows past a pilot.** If this system is meant to serve a lending institution processing dozens-to-hundreds of assessments per day (a realistic scale for even a modest microfinance/NBFC deployment), one-at-a-time processing means queue depth and wait times grow linearly with volume with no relief valve short of restructuring this stage — this is worth surfacing to whoever owns the product roadmap as a scaling constraint, not just a code note, since it affects a very concrete question ("how many loan officers can this system serve concurrently").
- **The "jobs silently never processed if `worker.py` isn't running and `PIPELINE_API_URL` is unset" state is a genuinely dangerous failure mode for a lending workflow** — a farmer or loan officer sees a QUEUED job that will simply never progress, with no automatic alert distinguishing "processing normally, please wait" from "this will never complete." In a financial-decision context, silent stalling is worse than a fast, visible failure, because it erodes trust and potentially delays a farmer's access to credit indefinitely without anyone being notified.
- **No heartbeat/reaper for stuck RUNNING jobs compounds the above** — a worker crash (OOM from a memory-heavy satellite fetch, an unhandled exception outside the per-block try/excepts elsewhere in the pipeline, a container restart) leaves that specific farmer's assessment stuck indefinitely, again with no automatic signal to operators or the affected user.
- **The dual worker/FastAPI-inline model, while giving deployment flexibility (small deployments can skip running a separate worker process), doubles the surface area for concurrency bugs** — any fix to `process_assessment_job`'s behavior needs validation against both call sites, and it's easy to imagine a scenario where a fix is tested against one path and not the other.
- **No described authorization check beyond app-login-cookie trust on the status route** — for a system handling financial and agricultural data tied to specific identifiable farmers, verifying that the caller is actually authorized to view a specific job's result (not just logged in generally) is a standard multi-tenant security expectation, and its absence (or at least its not being clearly documented) is worth an explicit security review.

## Possibilities & Best-in-Class Approaches (Current Technical Landscape)

1. **Move to a proper distributed task queue (Celery with Redis/RabbitMQ broker, or a managed equivalent like AWS SQS + workers, or RQ for a lighter-weight Python-native option) once volume justifies it** — this is standard practice for exactly this profile of workload (slow, I/O-heavy, need-for-retry background jobs), and would natively solve horizontal scaling (multiple workers pulling from a shared queue), retry-with-backoff, and dead-letter handling that currently don't exist. This is explicitly already flagged as the "major" recommendation and remains the correct long-term direction.
2. **In the interim, before a full queue migration, implement a reaper** (already flagged as quick win) — a scheduled check for jobs stuck in RUNNING beyond a reasonable timeout (calibrated against realistic pipeline duration, likely several minutes given satellite/weather I/O), automatically transitioning them to FAILED (with a distinct "timed out" reason) so they can be retried rather than silently stuck forever. This is a genuinely fast, high-value fix relative to the full queue migration.
3. **Replace the single global `asyncio.Lock` with a small worker pool (bounded concurrency, e.g., 2–4 concurrent heavy runs) sized to whatever the host's memory/network can actually sustain**, as a lighter-weight intermediate step between "one at a time" and "full distributed queue" — this alone could meaningfully improve throughput without the operational complexity of introducing a new broker dependency.
4. **Surface job-processing health as a first-class status, not just per-job status** — e.g., an operator-facing endpoint or alert showing "queue depth," "oldest QUEUED job age," and "is a worker actually consuming jobs right now" would directly catch the silent-stall failure mode (worker not running / `PIPELINE_API_URL` misconfigured) before it becomes a farmer-facing complaint.
5. **Add authorization scoping on the job-status route** so a caller can only retrieve jobs tied to farmers/accounts they're authorized to see — worth confirming this explicitly with the security/compliance owner given the financial and personal-data sensitivity of the payload.
6. **Add a progress field keyed to `pipeline_stages`** (already flagged as medium) — since the underlying pipeline already emits stage labels (`2_satellite`, `4_cycles`, etc.), surfacing "currently on weather analysis" rather than an opaque RUNNING status is a low-cost UX improvement that also gives operators a much finer-grained signal for where a stuck job actually stalled, which directly helps debug the reaper/stuck-job scenario above.

## Enhancement Recommendations (Prioritized)

| Priority | Recommendation | Why it matters given downstream stages |
|---|---|---|
| Urgent/Quick win | Reaper for RUNNING jobs stuck beyond a timeout → FAILED with retry path | Prevents indefinite silent stalls, the most farmer-visible failure mode currently possible |
| Quick win | Health/status endpoint surfacing queue depth + "is a worker actually consuming jobs" | Catches the silent misconfiguration state (no worker running, `PIPELINE_API_URL` unset) before it becomes a support incident |
| Quick win | Progress field keyed to existing `pipeline_stages` labels | Cheap UX and debugging win using data the pipeline already emits |
| Medium | Bounded worker pool (2–4 concurrent) instead of a single global lock | Meaningful throughput improvement without introducing a new broker dependency |
| Medium | Authorization scoping on job-status route beyond generic login-cookie trust | Standard multi-tenant security expectation for financial/personal data |
| Major | Migrate to a distributed task queue (Celery/Redis, SQS, or RQ) with retry + dead-letter support | Structural fix for horizontal scale, retry, and dead-letter handling that the current single-process model cannot provide |

## Interfaces to Other Stages (unchanged, restated for continuity)

| Upstream | Frontend enqueue; farm must exist in `farm_info` |
| Downstream | Full pipeline stages 01–08; persists `credit_assessments` when `save_to_db` |
| Shared | Mongo `jobs` collection contract with Next.js status API |

---
*This document supersedes the original `09-job-orchestration-worker.md` with expanded ground-reality analysis and forward-looking enhancement guidance. Original core-file/methodology tables retained; content is additive, not a rewrite of verified facts.*
