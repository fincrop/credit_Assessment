"""Section 8 release-gate report.

Takes whatever metric outputs exist (JSON dicts from the other modules) and
evaluates every gate in the plan's section 8 table. A gate whose inputs are
missing, or whose reference is not usable (e.g. D1 kappa < 0.70), is
``NOT MEASURED`` - never ``PASS``.

Recognised inputs (dict keyed by role, or a list of dicts with ``kind``):

=====================  ==============================================  ===========================
role                   produced by                                     kind
=====================  ==============================================  ===========================
heldout                ``run.py metrics --source mh2023_heldout``      classification_metrics
d1_agreement           ``run.py kappa``                                d1_agreement
d1_area                ``run.py area``                                 area_estimate
abstain                ``run.py gates --results <geojson>``            abstain_rate
official               ``run.py official``                             official_checks
sowing (any number)    ``run.py sowing --source ...``                  sowing_checks
client                 ``run.py client``                               client_comparison
external               hand-written / other tracks                     gate_values
=====================  ==============================================  ===========================
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

PASS, FAIL, NM = "PASS", "FAIL", "NOT MEASURED"

NON_CROP = {"Fallow", "Abstained", "Other/Abstained", "Non-crop", "Unclear", "Monitoring-flagged"}

GATES = [
    {"id": "classification_precision_recall", "component": "Classification",
     "metric": "Recall and precision, Cotton and Soyabean (Tur reported)",
     "threshold": ">= 0.85 each (95% CI lower bound >= 0.80)",
     "measured_on": "MH 2023 held-out blocks and D1 interpretation",
     "later": "+ client records", "status_if_not_met": "Not released"},
    {"id": "classification_calibration", "component": "Classification",
     "metric": "Calibration error (ECE, 10 bins)", "threshold": "<= 0.05",
     "measured_on": "MH 2023 held-out", "later": "-", "status_if_not_met": "Not released"},
    {"id": "classification_abstain_rate", "component": "Classification",
     "metric": "Abstain rate", "threshold": "<= 20%", "measured_on": "Dhaswadi re-run",
     "later": "-", "status_if_not_met": "Released; abstentions shown"},
    {"id": "reference_kappa", "component": "Reference quality",
     "metric": "Interpreter agreement (Cohen's kappa)", "threshold": ">= 0.70",
     "measured_on": "D1", "later": "-", "status_if_not_met": "D1 not used for gating"},
    {"id": "area_vs_d1", "component": "Area",
     "metric": "Village crop area vs D1 area estimate", "threshold": "within +-10%",
     "measured_on": "D1", "later": "-", "status_if_not_met": "Not released"},
    {"id": "area_vs_official", "component": "Area",
     "metric": "Village crop shares vs official shares", "threshold": "within +-15 pts",
     "measured_on": "D4", "later": "-", "status_if_not_met": "Not released"},
    {"id": "delineation_iou", "component": "Delineation",
     "metric": "Median matched IoU", "threshold": ">= 0.60, or aligned cadastral passing C1.3",
     "measured_on": "MH adjacent-parcel benchmark", "later": "-",
     "status_if_not_met": "Shown with boundary_confidence"},
    {"id": "sowing_upper_bound", "component": "Sowing",
     "metric": "Upper-bound violations", "threshold": "<= 5%",
     "measured_on": "MH 2023 cotton; Dhaswadi", "later": "-",
     "status_if_not_met": "Shown as window, not date"},
    {"id": "sowing_optical_radar", "component": "Sowing",
     "metric": "Optical-radar agreement within 10 days", "threshold": ">= 80%",
     "measured_on": "MH 2023 cotton; Dhaswadi", "later": "-",
     "status_if_not_met": "Shown as window, not date"},
    {"id": "sowing_abs_error", "component": "Sowing",
     "metric": "Median / 90th-percentile absolute error", "threshold": "<= 7 / <= 15 days",
     "measured_on": "-", "later": "Client records",
     "status_if_not_met": "Labelled \"not yet validated against farm records\""},
    {"id": "stage_d1_agreement", "component": "Stage",
     "metric": "Agreement with D1 harvest-timing evidence", "threshold": ">= 80%",
     "measured_on": "D1 second pass", "later": "Client records",
     "status_if_not_met": "Labelled provisional"},
    {"id": "yield_error", "component": "Yield",
     "metric": "Field / village error", "threshold": "<= 25% / <= 10% (>= 30 records per crop)",
     "measured_on": "-", "later": "Client records (>= 30 per crop)",
     "status_if_not_met": "Published only as a district-anchored band with label (5.6)"},
    {"id": "stress_plausibility", "component": "Stress",
     "metric": "Stress-type plausibility vs D1 evidence and weather; persistence rule applied",
     "threshold": ">= 80% plausible; 100% rule compliance", "measured_on": "D1 + audit",
     "later": "Client records", "status_if_not_met": "Shown as anomaly only, type withheld"},
    {"id": "raster_integrity", "component": "Raster",
     "metric": "Field summary equals interior-pixel statistics; no filled pixels; fixed scales",
     "threshold": "100%", "measured_on": "Automated tests", "later": "-",
     "status_if_not_met": "Not released"},
    {"id": "insufficient_evidence", "component": "All",
     "metric": "insufficient_evidence used wherever applicable", "threshold": "100% (nothing invented)",
     "measured_on": "Audit", "later": "-", "status_if_not_met": "Not released"},
]

KIND_TO_ROLE = {
    "d1_agreement": "d1_agreement", "area_estimate": "d1_area", "abstain_rate": "abstain",
    "official_checks": "official", "client_comparison": "client", "gate_values": "external",
    "sowing_checks": "sowing",
}


# ---------------------------------------------------------------------------
# Input assembly
# ---------------------------------------------------------------------------
def load_inputs(items: Iterable[Union[str, Path, Mapping]]) -> Dict[str, Any]:
    """Load JSON files / dicts and key them by role. ``sowing`` and
    ``external`` accumulate lists; classification metrics go to ``heldout``
    unless their ``source`` mentions d1."""
    out: Dict[str, Any] = {"sowing": [], "external": []}
    for it in items:
        if isinstance(it, (str, Path)):
            src_file = str(it)
            it = json.loads(Path(it).read_text(encoding="utf-8"))
        else:
            src_file = None
        role = it.get("role")
        kind = it.get("kind")
        if role is None:
            if kind == "classification_metrics":
                role = "d1_metrics" if "d1" in str(it.get("source", "")).lower() else "heldout"
            else:
                role = KIND_TO_ROLE.get(kind)
        if role is None:
            raise ValueError(f"cannot tell what {src_file or 'input'} is (kind={kind!r})")
        it = dict(it)
        it.setdefault("_file", src_file)
        if role in ("sowing", "external"):
            out[role].append(it)
        else:
            out[role] = it
    return out


def abstain_rate(fc) -> dict:
    """Share of fields (and of area) the classifier abstained on."""
    if isinstance(fc, (str, Path)):
        fc = json.loads(Path(fc).read_text(encoding="utf-8"))
    feats = fc["features"] if isinstance(fc, dict) else fc
    n = len(feats)
    k, a_tot, a_abs = 0, 0.0, 0.0
    for f in feats:
        p = f.get("properties", f)
        status = str(p.get("status") or "").lower()
        crop = str(p.get("crop") or "")
        ab = status in ("abstained", "out_of_support", "insufficient_evidence") or crop in ("Abstained", "")
        area = float(p.get("area_ha") or 0.0)
        a_tot += area
        if ab:
            k += 1
            a_abs += area
    return {"kind": "abstain_rate", "value": k / n if n else None, "n": n, "k": k,
            "area_weighted": a_abs / a_tot if a_tot else None}


# ---------------------------------------------------------------------------
# Gate evaluators
# ---------------------------------------------------------------------------
def _fmt(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _nm(reason: str, **kw):
    return {"result": NM, "value": None, "value_text": "not measured", "source": "-",
            "reason": reason, **kw}


def _d1_usable(inp) -> tuple:
    ag = inp.get("d1_agreement")
    if not ag:
        return False, "no D1 interpreter agreement result"
    if not ag.get("usable_for_gating"):
        return False, f"D1 not usable for gating ({ag.get('reason', 'kappa below 0.70')})"
    return True, ""


def _pr_from_heldout(m, cls):
    pc = m.get("per_class", {}).get(cls)
    if not pc or not pc["precision"]["n"] or not pc["recall"]["n"]:
        return None
    return {"precision": pc["precision"]["value"], "precision_low": pc["precision"]["ci_low"],
            "recall": pc["recall"]["value"], "recall_low": pc["recall"]["ci_low"],
            "n_support": pc["support"]}


def _pr_from_area(a, cls):
    pc = a.get("per_class", {}).get(cls)
    if not pc or not pc["n_mapped_in_sample"] or not pc["n_reference_in_sample"]:
        return None
    ua, pa = pc["users_accuracy"], pc["producers_accuracy"]
    if any(math.isnan(x) for x in (ua["value"], ua["ci_low"], pa["value"], pa["ci_low"])):
        return None
    return {"precision": ua["value"], "precision_low": ua["ci_low"],
            "recall": pa["value"], "recall_low": pa["ci_low"],
            "n_support": pc["n_reference_in_sample"]}


def g_classification_pr(inp, required=("Cotton", "Soyabean"), reported=("Tur",),
                        point=0.85, low=0.80):
    sources = {}
    if inp.get("heldout"):
        sources["mh2023_heldout"] = lambda c: _pr_from_heldout(inp["heldout"], c)
    else:
        sources["mh2023_heldout"] = None
    ok, why = _d1_usable(inp)
    if ok and inp.get("d1_area"):
        sources["d1_interpretation"] = lambda c: _pr_from_area(inp["d1_area"], c)
    else:
        sources["d1_interpretation"] = None
        d1_reason = why if not ok else "no D1 area/accuracy estimate"
    details, any_fail, all_measured, texts = {}, False, True, []
    for s, fn in sources.items():
        if fn is None:
            details[s] = {"status": NM, "reason": d1_reason if s.startswith("d1") else "no held-out metrics"}
            all_measured = False
            texts.append(f"{s}: not measured")
            continue
        details[s] = {}
        parts = []
        for c in list(required) + list(reported):
            v = fn(c)
            if v is None:
                details[s][c] = {"status": NM}
                if c in required:
                    all_measured = False
                parts.append(f"{c} n/a")
                continue
            passed = (v["precision"] >= point and v["recall"] >= point
                      and v["precision_low"] >= low and v["recall_low"] >= low)
            v["status"] = (PASS if passed else FAIL) if c in required else "reported"
            details[s][c] = v
            if c in required and not passed:
                any_fail = True
            parts.append(f"{c} P {_fmt(v['precision'], 2)} (lo {_fmt(v['precision_low'], 2)}) "
                         f"R {_fmt(v['recall'], 2)} (lo {_fmt(v['recall_low'], 2)})")
        texts.append(f"{s}: " + "; ".join(parts))
    res = FAIL if any_fail else (PASS if all_measured else NM)
    used = [s for s, f in sources.items() if f is not None]
    return {"result": res, "value": details, "value_text": " | ".join(texts),
            "source": ", ".join(used) or "-",
            "reason": "" if res == PASS else ("a required class fails" if res == FAIL
                                              else "gate needs both held-out and usable D1 evidence")}


def g_calibration(inp, thr=0.05):
    m = inp.get("heldout")
    if not m or m.get("ece") is None:
        return _nm("no held-out calibration result")
    e = float(m["ece"])
    return {"result": PASS if e <= thr else FAIL, "value": e, "value_text": _fmt(e),
            "source": m.get("source", "mh2023_heldout"), "reason": ""}


def g_abstain(inp, thr=0.20):
    a = inp.get("abstain")
    if not a or a.get("value") is None:
        return _nm("no classification re-run supplied")
    v = float(a["value"])
    return {"result": PASS if v <= thr else FAIL, "value": v,
            "value_text": f"{100 * v:.1f}% of fields ({_fmt(100 * a['area_weighted'], 1) if a.get('area_weighted') is not None else 'n/a'}% of area)",
            "source": a.get("source") or a.get("_file") or "classification results", "reason": ""}


def g_kappa(inp, thr=0.70):
    ag = inp.get("d1_agreement")
    if not ag or not ag.get("kappa") or not ag["kappa"].get("n"):
        return _nm("no D1 double-interpreted labels")
    k = ag["kappa"]["kappa"]
    if k is None or (isinstance(k, float) and math.isnan(k)):
        return _nm("kappa undefined")
    return {"result": PASS if k >= thr else FAIL, "value": k,
            "value_text": f"{k:.3f} ({ag['kappa'].get('interpretation')}, n={ag['kappa']['n']})",
            "source": "D1 interpreters", "reason": ""}


def g_area_d1(inp, tol_pct=10.0, min_share=0.05):
    ok, why = _d1_usable(inp)
    if not ok:
        return _nm(why)
    a = inp.get("d1_area")
    if not a:
        return _nm("no D1 area estimate")
    tot = a.get("total_area") or 0
    rows, fail, texts = {}, False, []
    for c, pc in a["per_class"].items():
        if c in NON_CROP:
            continue
        ma, est = pc["area"].get("map_area"), pc["area"]["value"]
        if ma is None or tot <= 0:
            continue
        if max(ma, est) / tot < min_share:
            continue
        d = pc["area"].get("map_minus_estimate_pct")
        if d is None:
            rows[c] = {"status": NM}
            continue
        passed = abs(d) <= tol_pct
        fail |= not passed
        rows[c] = {"map_area": ma, "estimated_area": est, "ci_low": pc["area"]["ci_low"],
                   "ci_high": pc["area"]["ci_high"], "diff_pct": d,
                   "status": PASS if passed else FAIL}
        texts.append(f"{c} {d:+.1f}%")
    if not rows or all(r.get("status") == NM for r in rows.values()):
        return _nm("no crop class with map area to compare")
    res = FAIL if fail else (PASS if all(r["status"] == PASS for r in rows.values()) else NM)
    return {"result": res, "value": rows, "value_text": "; ".join(texts),
            "source": "D1 stratified estimate (Olofsson 2014)", "reason": ""}


def g_area_official(inp, tol=15.0):
    o = inp.get("official")
    cs = (o or {}).get("crop_shares")
    if not cs or cs.get("status") not in ("consistent", "review"):
        return _nm((cs or {}).get("message", "no official crop-share check"))
    v = cs["max_abs_diff_pts_major"]
    return {"result": PASS if v <= tol else FAIL, "value": v,
            "value_text": f"max |diff| {v:.1f} pts (major crops)",
            "source": ", ".join(c["source"] for c in cs["comparisons"]), "reason": ""}


def _external(inp, key):
    for e in inp.get("external", []):
        vals = e.get("values", {})
        if key in vals:
            v = vals[key]
            if isinstance(v, Mapping):
                return v.get("value"), v.get("source", e.get("_file") or "external")
            return v, e.get("source", e.get("_file") or "external")
    return None, None


def g_delineation(inp, thr=0.60):
    v, src = _external(inp, "delineation_median_iou")
    c13, src2 = _external(inp, "cadastral_c13_pass")
    if v is None and c13 is None:
        return _nm("no delineation benchmark result")
    passed = (v is not None and float(v) >= thr) or c13 is True
    return {"result": PASS if passed else FAIL, "value": {"median_iou": v, "cadastral_c13_pass": c13},
            "value_text": f"median IoU {_fmt(v)}; C1.3 {c13}", "source": src or src2, "reason": ""}


def _sowing_inputs(inp, section, key):
    vals = []
    for s in inp.get("sowing", []):
        sec = s.get(section) or {}
        r = sec.get(key)
        if isinstance(r, Mapping) and r.get("n"):
            vals.append((s.get("source", s.get("_file") or "sowing"), r))
    return vals


def g_sowing_upper(inp, thr=0.05):
    vals = _sowing_inputs(inp, "upper_bound", "violation_rate")
    if not vals:
        return _nm("no upper-bound test (needs sowing estimates and survey dates)")
    fail = any(r["value"] > thr for _, r in vals)
    return {"result": FAIL if fail else PASS, "value": {s: r for s, r in vals},
            "value_text": "; ".join(f"{s}: {100 * r['value']:.1f}% (n={r['n']})" for s, r in vals),
            "source": ", ".join(s for s, _ in vals), "reason": ""}


def g_sowing_or(inp, thr=0.80):
    vals = _sowing_inputs(inp, "optical_radar", "agreement_rate")
    if not vals:
        return _nm("no fields with both optical and radar sowing estimates")
    fail = any(r["value"] < thr for _, r in vals)
    return {"result": FAIL if fail else PASS, "value": {s: r for s, r in vals},
            "value_text": "; ".join(f"{s}: {100 * r['value']:.1f}% (n={r['n']})" for s, r in vals),
            "source": ", ".join(s for s, _ in vals), "reason": ""}


def g_sowing_err(inp, med=7.0, p90=15.0):
    cands = []
    c = inp.get("client")
    if c and (c.get("sowing") or {}).get("n"):
        cands.append(("client records", c["sowing"]["n"], c["sowing"]["median_abs_error_days"],
                      c["sowing"]["p90_abs_error_days"]))
    for s in inp.get("sowing", []):
        cl = s.get("client") or {}
        if cl.get("status") == "measured" and cl.get("n"):
            cands.append((s.get("source", "sowing"), cl["n"], cl["median_abs_error_days"],
                          cl["p90_abs_error_days"]))
    if not cands:
        return _nm("no client sowing dates yet")
    fail = any(m > med or p > p90 for _, _, m, p in cands)
    return {"result": FAIL if fail else PASS,
            "value": [{"source": s, "n": n, "median": m, "p90": p} for s, n, m, p in cands],
            "value_text": "; ".join(f"{s}: median {m:.1f} d, P90 {p:.1f} d (n={n})" for s, n, m, p in cands),
            "source": ", ".join(s for s, *_ in cands), "reason": ""}


def g_stage(inp, thr=0.80):
    v, src = _external(inp, "stage_d1_agreement")
    if v is None:
        return _nm("no D1 second-pass harvest-timing comparison")
    ok, why = _d1_usable(inp)
    if not ok and "client" not in str(src).lower():
        return _nm(why)
    v = float(v)
    return {"result": PASS if v >= thr else FAIL, "value": v, "value_text": f"{100 * v:.1f}%",
            "source": src, "reason": ""}


def g_yield(inp, field_thr=25.0, village_thr=10.0):
    c = inp.get("client")
    ybc = (c or {}).get("yield_by_crop") or {}
    enough = {k: v for k, v in ybc.items() if v.get("enough_records")}
    if not enough:
        have = ", ".join(f"{k} n={v['n']}" for k, v in ybc.items()) or "none"
        return _nm(f"needs >= 30 client yield records per crop (have: {have})")
    fail = False
    texts = []
    for k, v in enough.items():
        agg = v.get("aggregate_pct_error")
        p = v["field_median_abs_pct_error"] <= field_thr and agg is not None and abs(agg) <= village_thr
        v["status"] = PASS if p else FAIL
        fail |= not p
        texts.append(f"{k}: field {v['field_median_abs_pct_error']:.1f}%, village {_fmt(agg, 1)}% (n={v['n']})")
    return {"result": FAIL if fail else PASS, "value": enough, "value_text": "; ".join(texts),
            "source": "client records", "reason": ""}


def g_stress(inp, thr=0.80):
    v, src = _external(inp, "stress_plausible_rate")
    r, src2 = _external(inp, "stress_rule_compliance")
    if v is None or r is None:
        return _nm("needs stress plausibility (D1 + weather) and persistence-rule audit")
    passed = float(v) >= thr and float(r) >= 1.0
    return {"result": PASS if passed else FAIL, "value": {"plausible": v, "rule_compliance": r},
            "value_text": f"plausible {100 * float(v):.1f}%, rule {100 * float(r):.1f}%",
            "source": f"{src}; {src2}", "reason": ""}


def _hundred(inp, key, what):
    v, src = _external(inp, key)
    if v is None:
        return _nm(f"no {what} result")
    v = float(v)
    return {"result": PASS if v >= 1.0 else FAIL, "value": v, "value_text": f"{100 * v:.1f}%",
            "source": src, "reason": ""}


EVALUATORS = {
    "classification_precision_recall": g_classification_pr,
    "classification_calibration": g_calibration,
    "classification_abstain_rate": g_abstain,
    "reference_kappa": g_kappa,
    "area_vs_d1": g_area_d1,
    "area_vs_official": g_area_official,
    "delineation_iou": g_delineation,
    "sowing_upper_bound": g_sowing_upper,
    "sowing_optical_radar": g_sowing_or,
    "sowing_abs_error": g_sowing_err,
    "stage_d1_agreement": g_stage,
    "yield_error": g_yield,
    "stress_plausibility": g_stress,
    "raster_integrity": lambda i: _hundred(i, "raster_checks_pass_rate", "raster integrity test"),
    "insufficient_evidence": lambda i: _hundred(i, "insufficient_evidence_compliance", "audit"),
}


def evaluate(inputs: Union[Mapping, Iterable]) -> dict:
    inp = dict(inputs) if isinstance(inputs, Mapping) else load_inputs(inputs)
    inp.setdefault("sowing", [])
    inp.setdefault("external", [])
    rows = []
    for g in GATES:
        try:
            r = EVALUATORS[g["id"]](inp)
        except Exception as exc:  # noqa: BLE001  never let a bad input become a PASS
            r = _nm(f"could not evaluate: {exc}")
        if r["result"] not in (PASS, FAIL, NM):
            r["result"] = NM
        row = {**g, **r}
        row["consequence"] = "Meets gate" if r["result"] == PASS else g["status_if_not_met"]
        rows.append(row)
    counts = {k: sum(1 for r in rows if r["result"] == k) for k in (PASS, FAIL, NM)}
    not_released = sorted({r["component"] for r in rows
                           if r["result"] != PASS and r["status_if_not_met"] == "Not released"})
    return {"kind": "gate_report", "created": dt.datetime.now().isoformat(timespec="seconds"),
            "counts": counts, "gates": rows,
            "components_not_releasable": not_released,
            "inputs": {k: ([x.get("_file") for x in v] if isinstance(v, list) else
                           (v.get("_file") if isinstance(v, Mapping) else None))
                       for k, v in inp.items()}}


def to_markdown(report: dict) -> str:
    def cell(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# Release gate report (plan section 8)", "",
        f"Generated {report['created']}. "
        f"PASS {report['counts'][PASS]}, FAIL {report['counts'][FAIL]}, "
        f"NOT MEASURED {report['counts'][NM]}.", "",
        "NOT MEASURED means the evidence does not exist yet; it is never counted as a pass.", "",
        "| Component | Metric | Gate | Value | Result | Data source | Status if not met |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["gates"]:
        val = r.get("value_text") or "-"
        if r["result"] == NM and r.get("reason"):
            val = f"not measured: {r['reason']}"
        lines.append("| " + " | ".join(cell(x) for x in (
            r["component"], r["metric"], r["threshold"], val, f"**{r['result']}**",
            r.get("source") or r["measured_on"], r["status_if_not_met"])) + " |")
    lines += ["", "Components that cannot be released until their gates pass: "
              + (", ".join(report["components_not_releasable"]) or "none"), ""]
    return "\n".join(lines)


def write_report(report: dict, out_dir: Union[str, Path], stem: str = "gate_report") -> tuple:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / f"{stem}.json", out / f"{stem}.md"
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    mp.write_text(to_markdown(report), encoding="utf-8")
    return jp, mp
