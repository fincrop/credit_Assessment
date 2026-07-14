# Devtools / one-off scripts

These scripts are **not** part of the FastAPI or Next.js production path. They were moved out of the repo root during technical-debt cleanup.

| Script | Role |
|--------|------|
| `example_usage.py` | Local CLI examples for the pipeline |
| `clear_satellite_cache.py` | Flush Mongo satellite cache |
| `debug_crop_cycles.py` / `test_crop_cycle_fix.py` | Cycle detector debugging |
| `test_satellite_fix.py` / `analyze_real_satellite_data.py` | Satellite download checks |
| `check_env_fix.py` / `fix_proj.py` / `install_env.bat` / `clean_env.bat` | Windows/Conda env helpers |
| `step1_database_setup.py` / `update_env.py` | DB / env utilities |

Run from repo root so imports resolve, e.g.:

```bash
python scripts/devtools/example_usage.py
```
