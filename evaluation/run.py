"""Command-line entry point for the Track D evaluation harness.

    python -m evaluation.run <subcommand> [options]

Subcommands: sample, kappa, area, client, official, metrics, sowing, gates,
templates. See evaluation/README.md for examples.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import area as area_mod
from . import d1_sampling, d3_client, d4_official, gates, metrics, sowing_checks

ALLOWED_LABELS = ["Cotton", "Soyabean", "Tur", "Jowar", "Bajra", "Sugarcane", "Maize",
                  "Other", "Fallow", "Non-crop", "Unclear"]


def _dump(obj, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    return path


def _say(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
def cmd_sample(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    flagged = None
    if a.flagged_ids:
        flagged = [l.strip() for l in Path(a.flagged_ids).read_text().splitlines() if l.strip()]
    sample, strata = d1_sampling.draw_sample(a.results, n_total=a.n, strata_min=a.min,
                                             seed=a.seed, selection=a.selection,
                                             flagged_ids=flagged)
    sample.to_csv(out / "sample.csv", index=False)
    strata.to_csv(out / "strata.csv", index=False)
    d1_sampling.map_class_areas(a.results, flagged).to_csv(out / "map_areas.csv", index=False)
    sheet, key = d1_sampling.interpretation_sheet(sample, seed=a.sheet_seed, chip_root="chips")
    for tag in ("A", "B"):
        sheet.to_csv(out / f"interpretation_sheet_{tag}.csv", index=False)
    pd.DataFrame(columns=["sample_id", "label", "confidence", "notes"]).to_csv(
        out / "adjudication_sheet_C.csv", index=False)
    key.to_csv(out / "KEY_do_not_share_with_interpreters.csv", index=False)
    (out / "labels_allowed.txt").write_text("\n".join(ALLOWED_LABELS) + "\n", encoding="utf-8")
    _say(f"sampled {len(sample)} fields into {out}")
    _say(strata.to_string(index=False))
    if a.chips:
        chip_in = key.merge(sheet[["sample_id", "lat", "lon"]], on="sample_id")
        if a.limit_chips:
            chip_in = chip_in.head(a.limit_chips)
        rec = d1_sampling.make_chips(chip_in, out / "chips", a.season_start, a.season_end)
        rec.to_csv(out / "chips" / "chip_log.csv", index=False)
        _say(f"chips: {rec['status'].value_counts().to_dict()}")
    return 0


def cmd_kappa(a) -> int:
    out = Path(a.out)
    summary, dis, ref = d1_sampling.import_labels(a.a, a.b, a.key, a.c, min_kappa=a.min_kappa)
    _dump(summary, out / "d1_agreement.json")
    dis.to_csv(out / "disagreements.csv", index=False)
    ref.to_csv(out / "reference.csv", index=False)
    k = summary["kappa"]
    _say(f"kappa {k['kappa']:.3f} ({k['interpretation']}, n={k['n']}); "
         f"usable_for_gating={summary['usable_for_gating']}; "
         f"{summary['n_disagreements']} disagreements, {summary['n_unresolved']} unresolved")
    return 0


def cmd_area(a) -> int:
    out = Path(a.out)
    ref = pd.read_csv(a.reference, dtype=str, keep_default_na=False)
    strata = pd.read_csv(a.strata)
    s_areas = dict(zip(strata["stratum"].astype(str), strata["area_ha"].astype(float)))
    m_areas = None
    if a.map_areas:
        m = pd.read_csv(a.map_areas)
        m_areas = dict(zip(m["map_class"].astype(str), m["area_ha"].astype(float)))
    usable = ref[ref["reference_label"].astype(str).str.strip() != ""]
    dropped = len(ref) - len(usable)
    res = area_mod.stratified_estimate(usable, s_areas, map_col="map_class",
                                       ref_col="reference_label", stratum_col="stratum",
                                       map_areas=m_areas)
    res["n_dropped_no_reference"] = int(dropped)
    if dropped:
        res["warning"] = (f"{dropped} sampled units had no final reference label and were "
                          "excluded; if they are not missing at random the estimate is biased")
    _dump(res, out / "d1_area.json")
    area_mod.summary_table(res).to_csv(out / "d1_area_table.csv", index=False)
    _say(area_mod.summary_table(res).to_string(index=False))
    return 0


def cmd_client(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    clean, errors = d3_client.validate_records(d3_client.load_records(a.records))
    pd.DataFrame(errors, columns=["row", "record_id", "column", "message"]).to_csv(
        out / "client_validation_errors.csv", index=False)
    comp, log = d3_client.match_records(clean, a.results, min_iou=a.min_iou)
    comp.to_csv(out / "client_comparison.csv", index=False)
    log.to_csv(out / "client_disagreements.csv", index=False)
    summary = d3_client.summarise(comp)
    summary["n_rejected"] = len({e["row"] for e in errors})
    _dump(summary, out / "client_summary.json")
    _say(f"{len(clean)} valid, {summary['n_rejected']} rejected, "
         f"{summary['n_matched']} matched, {len(log)} disagreement entries")
    return 0


def cmd_official(a) -> int:
    if a.config:
        cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    else:
        cfg = {}
        if a.level and a.name and a.season and a.year:
            cfg["crop_shares"] = {"level": a.level, "name": a.name, "season": a.season,
                                  "year": a.year}
        if a.district and a.crop and a.year:
            cfg["yields"] = [{"district": a.district, "crop": c, "year": a.year} for c in a.crop]
        if a.state and a.crop:
            cfg["sowing"] = [{"state": a.state, "district": a.district, "crop": c,
                              "year": a.year} for c in a.crop]
    res = d4_official.run_all(a.results, cfg, ref_dir=a.ref_dir)
    _dump(res, Path(a.out) / "official_checks.json")
    _say(f"crop shares: {res['crop_shares']['status']}; "
         f"yields: {[y['status'] for y in res['yields']]}; "
         f"sowing: {[s['status'] for s in res['sowing']]}")
    return 0


def cmd_metrics(a) -> int:
    df = pd.read_csv(a.csv, dtype=str, keep_default_na=False)
    conf = df[a.conf_col].astype(float).tolist() if a.conf_col in df.columns else None
    rep = metrics.classification_report(df[a.true_col].tolist(), df[a.pred_col].tolist(),
                                        confidence=conf, threshold=a.threshold, source=a.source)
    _dump(rep, Path(a.out))
    _say(f"n={rep['n']} OA={rep['overall_accuracy']['value']:.3f} "
         f"macroF1={rep['macro_f1']:.3f} ECE={rep.get('ece', float('nan')):.3f}")
    return 0


def cmd_sowing(a) -> int:
    df = pd.read_csv(a.csv, dtype=str, keep_default_na=False).replace("", pd.NA)
    res = sowing_checks.run_all(df, onset=a.onset)
    res["source"] = a.source
    _dump(res, Path(a.out))
    _say(json.dumps({k: v for k, v in res.items() if k in ("upper_bound", "optical_radar")},
                    default=str)[:600])
    return 0


def cmd_gates(a) -> int:
    inp = gates.load_inputs(a.inputs)
    if a.results:
        ab = gates.abstain_rate(a.results)
        ab["source"] = Path(a.results).name
        inp["abstain"] = ab
    rep = gates.evaluate(inp)
    jp, mp = gates.write_report(rep, a.out, a.stem)
    _say(f"{rep['counts']} -> {jp}, {mp}")
    return 0


def cmd_templates(a) -> int:
    d4_official.write_templates(a.ref_dir)
    ref = Path(a.ref_dir)
    if not (ref / "client_records_template.csv").exists():
        d3_client.write_templates(ref)
    _say(f"templates present in {ref} (existing files were not overwritten)")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evaluation.run",
                                description="Track D validation harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="D1: draw sample, blind sheets, optional chips")
    s.add_argument("--results", required=True, help="classification FeatureCollection (.geojson)")
    s.add_argument("--out", required=True)
    s.add_argument("--n", type=int, default=300)
    s.add_argument("--min", type=int, default=30, help="minimum per stratum")
    s.add_argument("--seed", type=int, default=20261102)
    s.add_argument("--sheet-seed", type=int, default=7)
    s.add_argument("--selection", choices=["area", "uniform"], default="area")
    s.add_argument("--flagged-ids", help="text file of monitoring-flagged field ids")
    s.add_argument("--chips", action="store_true", help="fetch Sentinel-2 chips (Earth Engine)")
    s.add_argument("--limit-chips", type=int, default=0)
    s.add_argument("--season-start", default="2026-05-01")
    s.add_argument("--season-end", default="2026-10-31")
    s.set_defaults(fn=cmd_sample)

    k = sub.add_parser("kappa", help="D1: import two interpreters' sheets")
    k.add_argument("--a", required=True)
    k.add_argument("--b", required=True)
    k.add_argument("--key", required=True)
    k.add_argument("--c", help="adjudication sheet (optional)")
    k.add_argument("--min-kappa", type=float, default=0.70)
    k.add_argument("--out", required=True)
    k.set_defaults(fn=cmd_kappa)

    ar = sub.add_parser("area", help="Olofsson area + accuracy from D1 reference")
    ar.add_argument("--reference", required=True, help="reference.csv from 'kappa'")
    ar.add_argument("--strata", required=True, help="strata.csv from 'sample'")
    ar.add_argument("--map-areas", help="map_areas.csv from 'sample'")
    ar.add_argument("--out", required=True)
    ar.set_defaults(fn=cmd_area)

    c = sub.add_parser("client", help="D3: validate + match client records")
    c.add_argument("--records", required=True)
    c.add_argument("--results", required=True)
    c.add_argument("--min-iou", type=float, default=0.3)
    c.add_argument("--out", required=True)
    c.set_defaults(fn=cmd_client)

    o = sub.add_parser("official", help="D4: official-statistics checks")
    o.add_argument("--results", required=True)
    o.add_argument("--config", help="JSON with crop_shares / yields / sowing sections")
    o.add_argument("--level")
    o.add_argument("--name")
    o.add_argument("--season")
    o.add_argument("--year", type=int)
    o.add_argument("--state")
    o.add_argument("--district")
    o.add_argument("--crop", nargs="*")
    o.add_argument("--ref-dir", default=str(d4_official.REFERENCE_DIR))
    o.add_argument("--out", required=True)
    o.set_defaults(fn=cmd_official)

    m = sub.add_parser("metrics", help="classification metrics from a y_true/y_pred CSV")
    m.add_argument("--csv", required=True)
    m.add_argument("--true-col", default="y_true")
    m.add_argument("--pred-col", default="y_pred")
    m.add_argument("--conf-col", default="p_top1")
    m.add_argument("--threshold", type=float)
    m.add_argument("--source", default="mh2023_heldout")
    m.add_argument("--out", required=True, help="output JSON path")
    m.set_defaults(fn=cmd_metrics)

    sw = sub.add_parser("sowing", help="sowing checks from a field table CSV")
    sw.add_argument("--csv", required=True)
    sw.add_argument("--onset")
    sw.add_argument("--source", required=True)
    sw.add_argument("--out", required=True, help="output JSON path")
    sw.set_defaults(fn=cmd_sowing)

    g = sub.add_parser("gates", help="assemble the section 8 gate report")
    g.add_argument("inputs", nargs="*", help="metric JSON files")
    g.add_argument("--results", help="classification re-run (for the abstain-rate gate)")
    g.add_argument("--out", required=True)
    g.add_argument("--stem", default="gate_report")
    g.set_defaults(fn=cmd_gates)

    t = sub.add_parser("templates", help="create header-only reference templates")
    t.add_argument("--ref-dir", default=str(d4_official.REFERENCE_DIR))
    t.set_defaults(fn=cmd_templates)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
