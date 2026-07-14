# Deployment Stage 05 — Monitoring & Logging

## Purpose & Role

Observe API liveness, pipeline failures, and job outcomes. Today this is mostly **stdout logging** plus a simple health endpoint — no APM/metrics stack is configured in-repo.

## Core Files & Key Functions/Classes

| Path | Role |
|------|------|
| `api/app.py` `GET /health` | `{status, pipeline_loaded, mongodb, jobs_collection}` |
| `main.py` / modules | `logging` INFO with stage STEP banners |
| `worker.py` / `job_runner.py` | Job start/success/fail logs |
| `render.yaml` | `healthCheckPath: /health` |

## Detailed Methodology

- Render hits `/health` for the API service.
- Operators infer job health by Mongo `jobs.status` and dashboard errors.
- No structured JSON logging standard; no correlation id from frontend `job_id` forced into all Python log lines (job_runner does log job id).

## Inputs & Outputs

Health JSON; log lines on the host.

## Configuration & Dependencies

`PIPELINE_VERBOSE` increases pipeline chatter. `EXPOSE_INTERNAL_ERRORS` affects API error bodies (not metrics).

## Current Implementation Notes

- Health can return `pipeline_loaded: false` until first assess warms the pipeline — still `status: ok` if process is up.
- Frontend has no health route documented for Render (Node `npm start` default).

## Known Limitations & Issues

- **Not yet implemented:** Prometheus/OpenTelemetry, error tracking (Sentry), log aggregation, alerting on FAILED job rate, satellite provider SLA dashboards.
- Stuck `RUNNING` jobs lack monitoring/reaper.
- No SLO definitions (e.g. p95 assess time).

## Strengths

- Clear STEP logs help manual debugging of multi-minute runs.
- Job runner distinguishes pipeline FAILED vs exception FAILED.

## Enhancement Recommendations

1. **Quick win:** Include `job_id`/`farmer_id` in a logging filter/adapter; metrics counter of SUCCESS/FAILED.
2. **Quick win:** Frontend `/api/health` pinging Mongo.
3. **Medium:** Sentry (API + Next); alert on FAILED spike.
4. **Major:** Trace spans per pipeline stage with timings stored on the job doc.

## Interfaces to Other Stages

Health used by hosting (03); failures often secret/provider related (06).
