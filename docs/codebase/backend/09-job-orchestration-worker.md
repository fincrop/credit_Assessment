# Backend Stage 09 — Job Orchestration & Worker

## Purpose

Operational envelope: enqueue assessments, run pipeline, expose status for the dashboard.

## Present condition (after 2026-07 enhancement)

1. Consumers: `worker.py` poll **or** FastAPI `BackgroundTasks` after `POST /v1/jobs/assess`.
2. Job fields include benefits, `require_classification`, optional **`force_fresh_satellite`**, status, result/error, **`progress`** (`pipeline_stages` / current stage).
3. **Reaper:** RUNNING past `JOB_RUNNING_TIMEOUT_MINUTES` (default 45) → FAILED (`timed_out_reaped`); runs from worker loop and `/health`.
4. **`GET /v1/jobs/health`:** queue depth, running count, oldest QUEUED age, reaper stats.
5. Dashboard polls Mongo via Next status route (now returns `progress`).
6. Still one heavy run at a time per process (`asyncio.Lock`) in inline FastAPI mode.

## Done

| Item | Notes |
|------|--------|
| Stuck-job reaper | Stops infinite RUNNING |
| Queue health endpoint | Catches “no worker” stalls |
| Progress on job + UI | Stage-aware loading message |
| Per-job force-fresh | Plumbed through job_runner |

## Missing / next

| Priority | Item |
|----------|------|
| Medium | Bounded concurrency pool (2–4) instead of single global lock |
| Medium | Authorization scoping on job status (farmer/account ownership) |
| Major | Distributed queue (Celery/RQ/SQS) + retries + DLQ |

## Interfaces

Upstream: frontend enqueue + `farm_info` · Downstream: full Stages 01–08 · Shared: Mongo `jobs`

---
*Backlog: [10-comparison-and-enhancements.md](10-comparison-and-enhancements.md)*
