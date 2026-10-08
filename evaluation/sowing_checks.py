"""Sowing-date validation without farmer dates (plan sections 5.3 and 8).

(a) upper bound   : a field cannot be sown after the date it was seen sown
                    (survey / label date). Gate: violations <= 5%.
(b) onset bound   : rainfed fields are rarely sown before monsoon onset - 5 d.
(c) optical-radar : the two independent estimates agree within 10 days.
                    Gate: >= 80%.
(d) client dates  : median and P90 absolute error when farm dates exist.
                    Gate: <= 7 / <= 15 days.

Every function takes a DataFrame and column names, ignores rows with
missing values, and returns a JSON-ready dict with the count used.
"""

from __future__ import annotations

from typing import Optional, Union

import numpy as np
import pandas as pd

from .metrics import wilson_interval


def _dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


def _frac(k: int, n: int) -> dict:
    lo, hi = wilson_interval(k, n)
    return {"value": (k / n) if n else None, "k": int(k), "n": int(n),
            "ci_low": lo if n else None, "ci_high": hi if n else None}


def upper_bound_violations(df: pd.DataFrame, sowing_col: str = "sowing_date",
                           survey_col: str = "survey_date", tolerance_days: int = 0) -> dict:
    """Fraction of fields whose estimated sowing is after the survey date."""
    s, v = _dates(df[sowing_col]), _dates(df[survey_col])
    ok = s.notna() & v.notna()
    bad = (s[ok] - v[ok]).dt.days > tolerance_days
    ids = df.loc[ok[ok].index[bad.values], "field_id"].astype(str).tolist() if "field_id" in df else []
    return {"check": "sowing_upper_bound", "violation_rate": _frac(int(bad.sum()), int(ok.sum())),
            "tolerance_days": tolerance_days, "violating_field_ids": ids[:200],
            "n_skipped_missing": int((~ok).sum())}


def onset_lower_bound(df: pd.DataFrame, onset: Union[str, pd.Timestamp, None] = None,
                      sowing_col: str = "sowing_date", onset_col: str = "onset_date",
                      irrigated_col: str = "irrigated", margin_days: int = 5) -> dict:
    """Fraction of rainfed fields sown before onset - ``margin_days``.

    ``onset`` may be a single date for the village or a per-field column.
    Fields flagged irrigated are excluded (pre-monsoon sowing is plausible).
    """
    s = _dates(df[sowing_col])
    if onset_col in df.columns:
        o = _dates(df[onset_col])
    elif onset is not None:
        o = pd.Series(pd.Timestamp(onset), index=df.index)
    else:
        return {"check": "sowing_onset_lower_bound", "status": "not_available",
                "message": "no monsoon onset date supplied"}
    irr = df[irrigated_col].map(lambda v: str(v).strip().lower() in ("true", "1", "yes", "y")) \
        if irrigated_col in df.columns else pd.Series(False, index=df.index)
    use = s.notna() & o.notna() & ~irr
    early = (s[use] < o[use] - pd.Timedelta(days=margin_days))
    return {"check": "sowing_onset_lower_bound", "status": "measured",
            "early_rate": _frac(int(early.sum()), int(use.sum())),
            "margin_days": margin_days, "n_irrigated_excluded": int(irr.sum())}


def optical_radar_agreement(df: pd.DataFrame, optical_col: str = "sowing_optical",
                            radar_col: str = "sowing_radar", within_days: int = 10) -> dict:
    """Fraction of fields with both estimates that agree within ``within_days``."""
    if optical_col not in df.columns or radar_col not in df.columns:
        return {"check": "sowing_optical_radar", "status": "not_available",
                "message": f"columns {optical_col}/{radar_col} not present"}
    a, b = _dates(df[optical_col]), _dates(df[radar_col])
    both = a.notna() & b.notna()
    diff = (a[both] - b[both]).dt.days.abs()
    k = int((diff <= within_days).sum())
    return {"check": "sowing_optical_radar", "status": "measured",
            "agreement_rate": _frac(k, int(both.sum())), "within_days": within_days,
            "median_abs_diff_days": float(diff.median()) if len(diff) else None}


def client_date_errors(df: pd.DataFrame, est_col: str = "sowing_ours",
                       client_col: str = "sowing_client") -> dict:
    """Median and P90 absolute error (days) where client sowing dates exist."""
    if est_col not in df.columns or client_col not in df.columns:
        return {"check": "sowing_client_error", "status": "not_available",
                "message": "no client sowing dates"}
    a, b = _dates(df[est_col]), _dates(df[client_col])
    both = a.notna() & b.notna()
    e = (a[both] - b[both]).dt.days
    if e.empty:
        return {"check": "sowing_client_error", "status": "not_available",
                "message": "no client sowing dates matched to estimates", "n": 0}
    ae = e.abs()
    return {"check": "sowing_client_error", "status": "measured", "n": int(len(e)),
            "median_abs_error_days": float(ae.median()),
            "p90_abs_error_days": float(np.percentile(ae, 90)),
            "mean_signed_error_days": float(e.mean())}


def run_all(df: pd.DataFrame, onset: Optional[str] = None, **kw) -> dict:
    out = {"kind": "sowing_checks", "n_fields": int(len(df))}
    out["upper_bound"] = (upper_bound_violations(df) if {"sowing_date", "survey_date"} <= set(df.columns)
                          else {"status": "not_available", "message": "needs sowing_date and survey_date"})
    out["onset"] = (onset_lower_bound(df, onset=onset) if "sowing_date" in df.columns
                    else {"status": "not_available"})
    out["optical_radar"] = optical_radar_agreement(df)
    out["client"] = client_date_errors(df)
    return out
