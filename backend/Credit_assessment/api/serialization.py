"""
Trim pipeline dicts for HTTP responses (avoid multi‑MB JSON from satellite grids).
"""

from __future__ import annotations

import copy
from typing import Any, Dict


def slim_assessment_for_api(
    assessment: Dict[str, Any],
    *,
    include_heavy: bool = False,
) -> Dict[str, Any]:
    """
    Return a copy safe to JSON-encode for browsers.

    Always drops raw continuous scene arrays. If ``include_heavy`` is False,
    removes ``satellite_data`` entirely (keeps ``continuous_data_stats`` on the
    assessment root when present).

    Top-level ``risk_assessment``, ``credit_assessment`` (shim),
    ``ai_enrichment``, and ``signal_quality_summary`` are preserved for API consumers.
    """
    out = copy.deepcopy(assessment)
    # Explicit keep-list documentation: do not strip these keys.
    _ = (
        out.get("risk_assessment"),
        out.get("credit_assessment"),
        out.get("ai_enrichment"),
        out.get("signal_quality_summary"),
    )
    sd = out.get("satellite_data")
    if isinstance(sd, dict):
        cd = sd.get("continuous_data")
        if isinstance(cd, dict):
            slim_cd = {
                k: v
                for k, v in cd.items()
                if k not in ("scenes", "dates", "ndvi_series", "raw")
            }
            for keep in ("start_date", "end_date", "interval_days", "total_days"):
                if keep in cd:
                    slim_cd[keep] = cd[keep]
            sd["continuous_data"] = slim_cd
        out["satellite_data"] = sd

    if not include_heavy:
        out.pop("satellite_data", None)

    if out.get("status") != "SUCCESS" and not _truthy_env("EXPOSE_INTERNAL_ERRORS"):
        out.pop("traceback", None)

    return out


def _truthy_env(name: str) -> bool:
    import os

    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")

