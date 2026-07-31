# Stage 02 — Action plan (Issue → Solution)

**Status:** Approved 2026-07-14 (recommended set)  
**Decisions:** B1–B3, B5 Approve · B4 Defer · B6 Defer · B7 Drop · D1–D3 docs  
**Implement order:** D1–D3 → B1 → B2 → B5 → B3  
**Scope:** Satellite observation grid  
**Related code:** `satellite_collector.py`, `main.py`, `config.py`, Mongo `satellite_stats_cache`

See prior revision history in git for full Issue/Why/How text. Summary of approved work:

| ID | Work |
|----|------|
| B1 | Stamp `indices_available` + `satellite_provider` on assessment |
| B2 | Per-job `force_fresh_satellite` flag (env remains global override) |
| B3 | Pixel-level SCL/QA cloud mask parity GEE+STAC; stamp `cloud_mask_version` |
| B5 | Slim cache payloads (index series only) |
| B4/B6 | Deferred | B7 | Dropped |
