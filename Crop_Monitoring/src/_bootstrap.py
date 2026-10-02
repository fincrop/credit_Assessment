"""Put the credit-assessment package and the crop calendar on sys.path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "Credit_assessment"
CALENDAR_SRC = ROOT / "Crop_classification_model" / "src"
PACKAGE = ROOT / "Crop_Monitoring"

for _p in (str(BACKEND), str(CALENDAR_SRC), str(PACKAGE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _register_conda_dlls() -> None:
    if sys.platform != "win32":
        return
    prefix = Path(sys.prefix)
    for sub in ("Library/bin", "Library/mingw-w64/bin", "Library/usr/bin", "DLLs"):
        d = prefix / sub
        if not d.is_dir():
            continue
        try:
            os.add_dll_directory(str(d))
        except (AttributeError, OSError):
            pass
        if str(d) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


_register_conda_dlls()


def load_env(path: Optional[Path] = None) -> dict:
    env_path = path or (BACKEND / ".env")
    found = {}
    if not env_path.exists():
        return found
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        found[key] = val
        os.environ.setdefault(key, val)
    return found


def init_ee() -> str:
    """Initialise Earth Engine with the same service account as classification."""
    import ee

    load_env()
    project = os.environ.get("GEE_PROJECT")
    key_path = os.environ.get("GEE_SA_KEY_PATH")
    if not project or not key_path:
        raise RuntimeError(
            "GEE_PROJECT / GEE_SA_KEY_PATH missing. Expected them in "
            f"{BACKEND / '.env'}"
        )
    kp = Path(key_path)
    if not kp.is_absolute():
        kp = BACKEND / kp
    if not kp.exists():
        raise RuntimeError(f"GEE service account key not found: {kp}")
    os.environ["GEE_SA_KEY_PATH"] = str(kp)
    sa_email = json.loads(kp.read_text(encoding="utf-8"))["client_email"]
    ee.Initialize(ee.ServiceAccountCredentials(sa_email, str(kp)), project=project)
    return project
