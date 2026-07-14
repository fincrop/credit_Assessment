# Deployment Stage 04 — CI/CD Pipeline

## Purpose & Role

Automate test, lint, build, and deploy. **Not yet implemented** in this repository.

## Core Files & Key Functions/Classes

| Path | Status |
|------|--------|
| `.github/workflows/*` | **Absent** (confirmed; also listed in `.dockerignore`) |
| `render.yaml` | Deploy-on-push via Render Git integration only (platform-side), not repo CI |
| `frontend` scripts | `lint` / `build` exist but are not gated by CI |

## Detailed Methodology

Current delivery path (inferred): push to GitHub → Render auto-build per service `buildFilter`. No PR checks, no required status checks, no image scanning.

## Inputs & Outputs

N/A today.

## Configuration & Dependencies

Would need GitHub Actions (or Render native PR previews) + secrets in GitHub/Render.

## Current Implementation Notes

- `.dockerignore` excludes `.github` from API image (fine once workflows exist).
- No pytest/frontend test workflow discovered.

## Known Limitations & Issues

- Regressions (e.g. shap `ci` NameError, scorer weight edits) can reach production unchecked.
- Docker builds are slow; without CI caching strategy, feedback is only post-merge on Render.

## Strengths

- Render `buildFilter` partially substitutes for path-aware builds.

## Enhancement Recommendations

**Starting setup (recommended):**

1. GitHub Actions on PR:
   - `frontend`: `npm ci` → `npm run lint` → `npm run build`
   - Python: `ruff`/`flake8` optional; `pytest` on pure units (cycle impute, credit limit math) without GEE
2. On `main`: optional `docker build` (scheduled or manual) to catch Dockerfile breaks
3. Block merge without green checks
4. Later: deploy previews for frontend; staging Render blueprint

## Interfaces to Other Stages

Gates deploy quality for 02–03; should run secret scanning related to 06.
