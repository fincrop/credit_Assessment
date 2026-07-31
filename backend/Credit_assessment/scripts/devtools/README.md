# Devtools / one-off scripts

These scripts are **not** part of the FastAPI or Next.js production path.

| Script | Role |
|--------|------|
| `example_usage.py` | Local CLI examples for the pipeline |
| `clear_satellite_cache.py` | Flush Mongo satellite cache |
| `debug_crop_cycles.py` / `test_crop_cycle_fix.py` | Cycle detector debugging |
| `test_satellite_fix.py` / `analyze_real_satellite_data.py` | Satellite download checks |
| `check_env_fix.py` / `fix_proj.py` / `install_env.bat` / `clean_env.bat` | Windows/Conda env helpers |
| `step1_database_setup.py` / `update_env.py` | DB / env utilities |

Run from `backend/Credit_assessment` so imports resolve, e.g.:

```bash
cd backend/Credit_assessment
python scripts/devtools/example_usage.py
```

`check_env_fix.py` / `update_env.py` locate `frontend/.env.local` via the monorepo root (two levels above this package).
