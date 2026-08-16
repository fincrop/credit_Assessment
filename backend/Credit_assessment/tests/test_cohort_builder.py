"""
Tests for assessment/cohort_builder.py

PeerBenchmark has never fired: upsert_cohort_stat had no caller, so cohort_stats
stayed empty while the narrative surface described vigour as "peer-relative".
The claim was removed; this makes it true.

The honesty constraints matter as much as the arithmetic — a percentile over a
handful of parcels is an artefact of onboarding order, and a percentile among
parcels WE assessed is not a regional statistic.
"""

from __future__ import annotations

import pytest

from assessment.cohort_builder import build_cohorts, summarise

KEY = "DECCAN|KHARIF|NA"


def _snap(farmer_id, values, cohort_key=KEY):
    return {
        "farmer_id": farmer_id,
        "features": {"cohort_key": cohort_key,
                     "nirv_auc_mean_by_cycle": list(values)},
    }


def _cohort_of(n_farmers, base=0.30, key=KEY):
    return [_snap(f"F{i:03d}", [base + i * 0.01], key) for i in range(n_farmers)]


# ── the size floor ────────────────────────────────────────────────────────

def test_an_undersized_cohort_is_not_stored():
    """
    Below the minimum a 'percentile' reflects who was onboarded first, not how
    a farm compares. Reporting one would be worse than reporting none.
    """
    cohorts = build_cohorts(_cohort_of(5), min_cohort_n=20)
    assert cohorts == {}


def test_a_cohort_activates_once_it_reaches_the_minimum():
    cohorts = build_cohorts(_cohort_of(20), min_cohort_n=20)
    assert KEY in cohorts
    assert cohorts[KEY]["metrics"]["nirv_auc_mean"]["n"] == 20


def test_n_counts_farmers_not_cycles():
    """
    One farm with six cycles is one observation of a farm. Counting cycles
    would let a single parcel manufacture a cohort on its own.
    """
    snaps = [_snap("F1", [0.3, 0.31, 0.32, 0.33, 0.34, 0.35])] * 1
    cohorts = build_cohorts(snaps, min_cohort_n=5)
    assert cohorts == {}, "one farmer's six cycles must not form a cohort"


def test_repeat_snapshots_from_one_farmer_do_not_inflate_n():
    snaps = _cohort_of(20) + _cohort_of(20)  # same farmer ids twice
    cohorts = build_cohorts(snaps, min_cohort_n=20)
    assert cohorts[KEY]["metrics"]["nirv_auc_mean"]["n"] == 20


# ── the distribution ──────────────────────────────────────────────────────

def test_empirical_breakpoints_are_stored_for_percentile_lookup():
    m = build_cohorts(_cohort_of(25), min_cohort_n=20)[KEY]["metrics"]["nirv_auc_mean"]
    pct = m["percentiles"]
    for k in ("p5", "p10", "p25", "p50", "p75", "p90", "p95"):
        assert k in pct
    # Monotonic, or the piecewise-linear lookup in PeerBenchmark is meaningless.
    vals = [pct[k] for k in ("p5", "p10", "p25", "p50", "p75", "p90", "p95")]
    assert vals == sorted(vals)


def test_mean_and_std_are_present_for_the_normal_fallback():
    m = build_cohorts(_cohort_of(25), min_cohort_n=20)[KEY]["metrics"]["nirv_auc_mean"]
    assert m["mean"] > 0
    assert m["std"] >= 0


def test_the_output_is_the_shape_peerbenchmark_reads():
    """Contract check against the consumer, not just internal consistency."""
    from utils.peer_benchmark import PeerBenchmark

    cohorts = build_cohorts(_cohort_of(25), min_cohort_n=20)
    stats = {k: v["metrics"] for k, v in cohorts.items()}
    pb = PeerBenchmark(cohort_stats=stats, min_cohort_n=20)
    pct, meta = pb.percentile("nirv_auc_mean", 0.40, KEY)
    assert pct is not None, f"PeerBenchmark could not read the cohort: {meta}"
    assert 0 <= pct <= 100


# ── honesty about what the cohort is ──────────────────────────────────────

def test_every_cohort_states_its_population():
    """
    It is a comparison against parcels we happen to have assessed, not a random
    sample of farms in the region. Nothing downstream may quietly present it as
    the latter.
    """
    doc = build_cohorts(_cohort_of(20), min_cohort_n=20)[KEY]
    assert "not a random sample" in doc["population"]


def test_cohorts_are_kept_separate_by_key():
    snaps = _cohort_of(20, key="A|KHARIF|NA") + _cohort_of(20, base=0.9, key="B|RABI|NA")
    cohorts = build_cohorts(snaps, min_cohort_n=20)
    assert set(cohorts) == {"A|KHARIF|NA", "B|RABI|NA"}
    a = cohorts["A|KHARIF|NA"]["metrics"]["nirv_auc_mean"]["mean"]
    b = cohorts["B|RABI|NA"]["metrics"]["nirv_auc_mean"]["mean"]
    assert b > a, "distinct cohorts were pooled"


# ── operator summary ──────────────────────────────────────────────────────

def test_summary_reports_how_far_short_each_cohort_is():
    snaps = _cohort_of(7)
    stats = summarise(snaps, build_cohorts(snaps, min_cohort_n=20), min_cohort_n=20)
    assert stats["n_cohorts_warm"] == 0
    assert stats["n_cohorts_short"] == 1
    assert stats["short_by_key"][KEY]["needs"] == 13


def test_summary_counts_warm_cohorts():
    snaps = _cohort_of(25)
    stats = summarise(snaps, build_cohorts(snaps, min_cohort_n=20), min_cohort_n=20)
    assert stats["n_cohorts_warm"] == 1


# ── degenerate input ──────────────────────────────────────────────────────

def test_no_snapshots_yields_no_cohorts():
    assert build_cohorts([], min_cohort_n=20) == {}
    assert build_cohorts(None, min_cohort_n=20) == {}


def test_snapshots_without_values_or_keys_are_skipped():
    snaps = [{"farmer_id": "F1"}, {"features": {"cohort_key": KEY}}, None, "junk"]
    assert build_cohorts(snaps, min_cohort_n=1) == {}


def test_non_finite_values_are_dropped():
    snaps = [_snap(f"F{i}", [float("nan"), 0.3]) for i in range(20)]
    m = build_cohorts(snaps, min_cohort_n=20)[KEY]["metrics"]["nirv_auc_mean"]
    assert m["n_values"] == 20  # the NaNs, not the farmers, were dropped


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
