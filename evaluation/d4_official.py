"""D4: official-statistics consistency checks (plan section 6, D4).

The official tables are supplied by the user in header-only CSV templates
under ``evaluation/reference/``. Nothing here contains or invents official
numbers. When a table is empty or has no rows for the requested place /
crop / year the check returns ``status: "not_available"`` instead of failing.

Checks
------
* crop shares: mapped area share by crop vs official share, flag
  ``|diff| > 15`` percentage points for major crops;
* yields: village mean estimated yield vs the district's 5-year range;
* sowing progress: village sowing-date CDF vs weekly cumulative sowing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

import numpy as np
import pandas as pd

from .d3_client import load_results
from .labels import is_blank, normalise_label

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"

TEMPLATES = {
    "official_crop_shares": ["level", "name", "state", "crop", "area_ha", "season", "year", "source"],
    "district_yields": ["district", "crop", "year", "yield_t_ha", "source"],
    "sowing_progress": ["state", "district", "crop", "week_ending", "cumulative_sown_pct", "source"],
}

NON_CROP_CLASSES = {"Fallow", "Abstained", "Other/Abstained", "Non-crop", "Unclear",
                    "Unknown", "Insufficient_evidence", "Out_of_support"}

NA = "not_available"


def write_templates(ref_dir: Union[str, Path] = REFERENCE_DIR, overwrite: bool = False) -> None:
    """Create header-only templates (never overwrites a filled table)."""
    ref = Path(ref_dir)
    ref.mkdir(parents=True, exist_ok=True)
    for name, cols in TEMPLATES.items():
        p = ref / f"{name}.csv"
        if overwrite or not p.exists():
            p.write_text(",".join(cols) + "\n", encoding="utf-8")


def load_table(name: str, ref_dir: Union[str, Path, None] = None,
               table: Optional[pd.DataFrame] = None) -> tuple:
    """Return ``(df, status, message)`` with status available/not_available/invalid."""
    cols = TEMPLATES[name]
    if table is None:
        p = Path(ref_dir or REFERENCE_DIR) / f"{name}.csv"
        if not p.exists():
            return pd.DataFrame(columns=cols), NA, f"{p} not found"
        table = pd.read_csv(p, dtype=str, keep_default_na=False)
    df = table.copy()
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        return df, "invalid", f"{name}: missing columns {missing}"
    df = df[~df.apply(lambda r: all(is_blank(v) for v in r), axis=1)]
    if df.empty:
        return df, NA, f"{name}: template has no rows yet"
    return df.reset_index(drop=True), "available", ""


def _norm(s) -> str:
    return "" if is_blank(s) else " ".join(str(s).strip().lower().split())


def _results_frame(results) -> pd.DataFrame:
    if isinstance(results, pd.DataFrame):
        df = results.copy()
        if "crop" in df:
            df["crop"] = df["crop"].map(normalise_label)
        return df
    return load_results(results)


# ---------------------------------------------------------------------------
# Crop shares
# ---------------------------------------------------------------------------
def mapped_crop_shares(results, exclude: Iterable[str] = NON_CROP_CLASSES) -> dict:
    df = _results_frame(results)
    df = df[df["crop"].notna()].copy()
    df["area_ha"] = pd.to_numeric(df["area_ha"], errors="coerce").fillna(0.0)
    total = float(df["area_ha"].sum())
    excl = set(exclude)
    crop = df[~df["crop"].isin(excl)]
    by = crop.groupby("crop")["area_ha"].sum()
    crop_total = float(by.sum())
    return {
        "total_area_ha": total,
        "crop_area_ha": crop_total,
        "excluded_area_ha": {k: float(v) for k, v in
                             df[df["crop"].isin(excl)].groupby("crop")["area_ha"].sum().items()},
        "unclassified_share_pct": 100.0 * (total - crop_total) / total if total > 0 else None,
        "shares_pct": {k: 100.0 * v / crop_total for k, v in by.items()} if crop_total > 0 else {},
        "area_ha": {k: float(v) for k, v in by.items()},
    }


def compare_crop_shares(results, level: str, name: str, season: str, year: int,
                        official: Optional[pd.DataFrame] = None,
                        ref_dir: Union[str, Path, None] = None,
                        threshold_pts: float = 15.0, major_share_pct: float = 10.0,
                        major_crops: Optional[Iterable[str]] = None) -> dict:
    """Mapped vs official crop shares for one village / taluka / district.

    Shares are computed over cropped area only (fallow, abstained and
    non-crop classes excluded from the mapped denominator; the excluded share
    is reported). A crop is "major" if listed in ``major_crops`` or if either
    share is at least ``major_share_pct``. Crops present on one side only
    count as 0% on the other side.
    """
    df, status, msg = load_table("official_crop_shares", ref_dir, official)
    base = {"check": "crop_shares", "level": level, "name": name, "season": season,
            "year": int(year), "threshold_pts": threshold_pts}
    if status != "available":
        return {**base, "status": status, "message": msg}
    sel = df[(df["level"].map(_norm) == _norm(level)) & (df["name"].map(_norm) == _norm(name))
             & (df["season"].map(_norm) == _norm(season))
             & (pd.to_numeric(df["year"], errors="coerce") == int(year))]
    if sel.empty:
        return {**base, "status": NA,
                "message": f"no official rows for {level} {name} {season} {year}"}
    mapped = mapped_crop_shares(results)
    sel = sel.copy()
    sel["crop_n"] = sel["crop"].map(normalise_label)
    sel["area"] = pd.to_numeric(sel["area_ha"], errors="coerce")
    majors = {normalise_label(c) for c in (major_crops or [])}
    comparisons = []
    for src, g in sel.groupby("source"):
        off = g.groupby("crop_n")["area"].sum()
        off = off[~off.index.isin(NON_CROP_CLASSES)]
        ot = float(off.sum())
        if ot <= 0:
            continue
        off_pct = {k: 100.0 * v / ot for k, v in off.items()}
        rows = []
        for crop in sorted(set(off_pct) | set(mapped["shares_pct"])):
            mp = mapped["shares_pct"].get(crop, 0.0)
            op = off_pct.get(crop, 0.0)
            major = crop in majors or mp >= major_share_pct or op >= major_share_pct
            diff = mp - op
            rows.append({"crop": crop, "mapped_share_pct": mp, "official_share_pct": op,
                         "diff_pts": diff, "major": bool(major),
                         "flag": bool(major and abs(diff) > threshold_pts)})
        comparisons.append({"source": src, "official_total_area_ha": ot, "rows": rows,
                            "n_flags": int(sum(r["flag"] for r in rows)),
                            "max_abs_diff_pts_major": max([abs(r["diff_pts"]) for r in rows if r["major"]] or [0.0])})
    if not comparisons:
        return {**base, "status": NA, "message": "official rows have no usable area"}
    worst = max(c["max_abs_diff_pts_major"] for c in comparisons)
    return {**base, "status": "review" if any(c["n_flags"] for c in comparisons) else "consistent",
            "mapped": mapped, "comparisons": comparisons, "max_abs_diff_pts_major": worst,
            "message": ("differences beyond +-%.0f pts trigger review before release" % threshold_pts)}


# ---------------------------------------------------------------------------
# Yields
# ---------------------------------------------------------------------------
def village_yield_vs_district(results, district: str, crop: str, year: int,
                              n_years: int = 5, min_years: int = 3,
                              yields: Optional[pd.DataFrame] = None,
                              ref_dir: Union[str, Path, None] = None,
                              yield_basis: Optional[str] = None) -> dict:
    """Area-weighted village mean yield vs the district's range over the
    ``n_years`` most recent years before ``year``."""
    crop_n = normalise_label(crop)
    base = {"check": "yield_vs_district", "district": district, "crop": crop_n, "year": int(year)}
    df, status, msg = load_table("district_yields", ref_dir, yields)
    if status != "available":
        return {**base, "status": status, "message": msg}
    d = df[(df["district"].map(_norm) == _norm(district)) & (df["crop"].map(normalise_label) == crop_n)].copy()
    d["year_n"] = pd.to_numeric(d["year"], errors="coerce")
    d["y"] = pd.to_numeric(d["yield_t_ha"], errors="coerce")
    d = d[d["year_n"].notna() & d["y"].notna() & (d["year_n"] < int(year))]
    d = d.sort_values("year_n").groupby("year_n", as_index=False)["y"].mean().tail(n_years)
    if len(d) < min_years:
        return {**base, "status": NA,
                "message": f"only {len(d)} prior years for {district} {crop_n}; need {min_years}"}
    r = _results_frame(results)
    r = r[r["crop"] == crop_n].copy()
    r["yield_t_ha"] = pd.to_numeric(r.get("yield_t_ha"), errors="coerce")
    r["area_ha"] = pd.to_numeric(r.get("area_ha"), errors="coerce")
    r = r[r["yield_t_ha"].notna()]
    if r.empty:
        return {**base, "status": NA, "message": f"no village yield estimates for {crop_n}"}
    w = r["area_ha"].fillna(0)
    vmean = float(np.average(r["yield_t_ha"], weights=w) if w.sum() > 0 else r["yield_t_ha"].mean())
    lo, hi, mean = float(d["y"].min()), float(d["y"].max()), float(d["y"].mean())
    st = "within_range" if lo <= vmean <= hi else ("below_range" if vmean < lo else "above_range")
    out = {**base, "status": st, "village_mean_t_ha": vmean, "n_fields": int(len(r)),
           "district_years": [int(x) for x in d["year_n"]], "district_min_t_ha": lo,
           "district_max_t_ha": hi, "district_mean_t_ha": mean,
           "pct_vs_district_mean": 100.0 * (vmean - mean) / mean if mean > 0 else None}
    if yield_basis == "district_anchored":
        out["caveat"] = ("yields are district-anchored, so agreement with the district "
                         "figure is partly by construction (weak check)")
    return out


# ---------------------------------------------------------------------------
# Sowing progress
# ---------------------------------------------------------------------------
def sowing_vs_progress(results, state: str, crop: str, year: Optional[int] = None,
                       district: Optional[str] = None, threshold_pts: float = 20.0,
                       normalise_to_final: bool = True,
                       progress: Optional[pd.DataFrame] = None,
                       ref_dir: Union[str, Path, None] = None) -> dict:
    """Village sowing-date CDF vs official weekly cumulative sowing.

    ``cumulative_sown_pct`` is often reported as % of normal area; with
    ``normalise_to_final`` the series is divided by its last value so both
    curves end at 100%.
    """
    crop_n = normalise_label(crop)
    base = {"check": "sowing_progress", "state": state, "district": district, "crop": crop_n,
            "threshold_pts": threshold_pts}
    df, status, msg = load_table("sowing_progress", ref_dir, progress)
    if status != "available":
        return {**base, "status": status, "message": msg}
    d = df[(df["state"].map(_norm) == _norm(state)) & (df["crop"].map(normalise_label) == crop_n)].copy()
    if district and (d["district"].map(_norm) == _norm(district)).any():
        d = d[d["district"].map(_norm) == _norm(district)]
        base["official_level"] = "district"
    else:
        d = d[d["district"].map(is_blank)]
        base["official_level"] = "state"
    d["week"] = pd.to_datetime(d["week_ending"], errors="coerce")
    d["pct"] = pd.to_numeric(d["cumulative_sown_pct"], errors="coerce")
    d = d[d["week"].notna() & d["pct"].notna()]
    if year is not None:
        d = d[d["week"].dt.year == int(year)]
    if d.empty:
        return {**base, "status": NA, "message": "no official sowing-progress rows for this selection"}
    d = d.sort_values("week").groupby("week", as_index=False)["pct"].mean()
    if normalise_to_final and d["pct"].iloc[-1] > 0:
        d["pct"] = 100.0 * d["pct"] / d["pct"].iloc[-1]
    r = _results_frame(results)
    r = r[r["crop"] == crop_n]
    sd = pd.to_datetime(r["sowing_date"], errors="coerce").dropna()
    if sd.empty:
        return {**base, "status": NA, "message": f"no village sowing dates for {crop_n}"}
    weeks = []
    for _, w in d.iterrows():
        v = 100.0 * float((sd <= w["week"]).mean())
        weeks.append({"week_ending": w["week"].date().isoformat(), "village_cdf_pct": v,
                      "official_pct": float(w["pct"]), "diff_pts": v - float(w["pct"])})
    mx = max(abs(w["diff_pts"]) for w in weeks)
    before = float((sd < d["week"].iloc[0] - pd.Timedelta(days=7)).mean())
    return {**base, "status": "review" if mx > threshold_pts else "consistent",
            "n_fields": int(len(sd)), "weeks": weeks, "max_abs_diff_pts": mx,
            "share_sown_before_first_report_week_pct": 100.0 * before}


def run_all(results, config: Mapping, ref_dir: Union[str, Path, None] = None) -> dict:
    """Run every D4 check described in ``config`` (see README)."""
    out = {"kind": "official_checks", "created": dt.datetime.now().isoformat(timespec="seconds")}
    cs = config.get("crop_shares")
    out["crop_shares"] = (compare_crop_shares(results, ref_dir=ref_dir, **cs) if cs
                          else {"status": NA, "message": "not configured"})
    out["yields"] = [village_yield_vs_district(results, ref_dir=ref_dir, **y)
                     for y in config.get("yields", [])] or [{"status": NA, "message": "not configured"}]
    out["sowing"] = [sowing_vs_progress(results, ref_dir=ref_dir, **s)
                     for s in config.get("sowing", [])] or [{"status": NA, "message": "not configured"}]
    return out
