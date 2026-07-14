# Deployment & frontend (legacy note)

This file is **superseded** by the structured docs under [`docs/codebase/`](docs/codebase/README.md).

| Topic | Go here |
|-------|---------|
| Deploy overview | [docs/codebase/deployment/00-overview.md](docs/codebase/deployment/00-overview.md) |
| Environment variables | [docs/codebase/deployment/01-environment-configuration.md](docs/codebase/deployment/01-environment-configuration.md) |
| Docker / Render | [docs/codebase/deployment/02-containerization-build.md](docs/codebase/deployment/02-containerization-build.md), [03-hosting-infrastructure.md](docs/codebase/deployment/03-hosting-infrastructure.md) |
| Frontend | [docs/codebase/frontend/00-overview.md](docs/codebase/frontend/00-overview.md) |

**Corrections vs older text in this filename’s history:** the UI is **Next.js 16** (not Vite); Mongo default DB is **`agristack`**; dashboard uses **async jobs** (`/v1/jobs/assess` or `worker.py`), not sync-only `POST /v1/assess`.
