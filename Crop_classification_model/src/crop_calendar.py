"""
Crop calendars. The single copy lives in the production package at
`backend/Credit_assessment/crop_analysis/crop_calendar.py`; this module
re-exports it so training, inference and monitoring cannot drift apart.

Imported both as `src.crop_calendar` (training) and as top-level
`crop_calendar` (Crop_Monitoring puts this directory on sys.path).
"""
from __future__ import annotations

try:
    from . import _bootstrap  # noqa: F401  (puts the backend on sys.path)
except ImportError:  # imported as a top-level module; caller set sys.path
    pass

from crop_analysis.crop_calendar import *  # noqa: F401,F403
from crop_analysis.crop_calendar import (  # noqa: F401  (names * skips)
    _SEASON_ALIASES,
    _month_end,
    _month_start,
    _span,
)
