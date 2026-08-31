"""
Shared bootstrap: put the production pipeline on sys.path, load its .env, and
initialise Earth Engine with the same service account the pipeline uses.

Every stage imports from here rather than re-deriving paths, so the training
code and the serving code read the same config module and the same credentials.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# Crop_classification_model/src/_bootstrap.py -> repo root
ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend" / "Credit_assessment"
PROJECT = ROOT / "Crop_classification_model"
DATA = PROJECT / "data"
MODELS = PROJECT / "models"
REPORTS = PROJECT / "reports"

for _d in (DATA, MODELS, REPORTS):
    _d.mkdir(parents=True, exist_ok=True)

# The pipeline uses flat imports (`from config import PipelineConfig`), so its
# own directory must be on sys.path — not the repo root.
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

def _register_conda_dlls() -> None:
    """
    Put the interpreter's own native DLL directories on the search path.

    Running `.conda/python.exe` WITHOUT activating the environment leaves
    Library/bin off the DLL search path. `import scipy` still succeeds (numpy
    bundles its own DLLs), but the first LAPACK call — scipy.optimize.curve_fit
    inside CropCycleDetector._fit_double_logistic — dies with Windows fatal
    exception 0xc06d007f, a delay-load failure. That is a NATIVE crash: no
    traceback, exit code 127, and no try/except can catch it, so a whole
    extraction run would vanish mid-way with nothing to debug.

    Harmless on Linux/macOS and on non-conda interpreters.
    """
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

ACRES_TO_HA = 0.40468564224
BLOCK_DEG = 0.25          # ~25 km spatial CV blocks
EQUAL_AREA_CRS = "EPSG:6933"


def load_env(path: Optional[Path] = None) -> dict:
    """Minimal .env reader — avoids a python-dotenv dependency for one file."""
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
    """
    Initialise Earth Engine from GEE_PROJECT + GEE_SA_KEY_PATH.

    Returns the project id. Raises with an actionable message rather than
    letting a downstream getInfo() fail with an opaque auth error mid-extraction.
    """
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

    # The production collector re-initialises GEE itself and opens
    # GEE_SA_KEY_PATH RELATIVE TO CWD. Running from anywhere but
    # backend/Credit_assessment made it fail over to STAC silently, which would
    # have compared our GEE numbers against a different provider entirely.
    # Re-export the resolved absolute path so both paths use one credential.
    os.environ["GEE_SA_KEY_PATH"] = str(kp)

    sa_email = json.loads(kp.read_text(encoding="utf-8"))["client_email"]
    ee.Initialize(ee.ServiceAccountCredentials(sa_email, str(kp)), project=project)
    return project


def setup_logging(
    name: str,
    level: int = logging.INFO,
    quiet_pipeline: bool = True,
) -> logging.Logger:
    # The pipeline logs Unicode arrows and box characters. On a cp1252 Windows
    # console that raises UnicodeEncodeError inside logging itself, which looks
    # like a crash in whatever stage happened to be running. Force UTF-8 on the
    # stream instead of stripping the pipeline's log messages.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("rasterio", "urllib3", "googleapiclient", "fiona", "pyogrio",
                  "google", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if quiet_pipeline:
        # Production modules are verbose per-parcel at INFO. Over thousands of
        # parcels that buries our own progress entirely.
        for mod in ("crop_analysis", "data_acquisition", "utils", "config",
                    "crop_analysis.crop_cycle_detector",
                    "crop_analysis.crop_detector",
                    "crop_analysis.land_cover_gate",
                    "data_acquisition.satellite_collector"):
            logging.getLogger(mod).setLevel(logging.WARNING)

    return logging.getLogger(name)


def block_id(lat, lon, deg: float = BLOCK_DEG):
    """Spatial CV group key. Vectorised over numpy/pandas input."""
    import numpy as np

    la = np.floor(np.asarray(lat, dtype=float) / deg).astype(int)
    lo = np.floor(np.asarray(lon, dtype=float) / deg).astype(int)
    return np.char.add(np.char.add(la.astype(str), "_"), lo.astype(str))
