import pandas as pd
import pytest

from evaluation import sowing_checks as sc


def _df():
    return pd.DataFrame({
        "field_id": ["1", "2", "3", "4", "5"],
        "sowing_date": ["2026-06-10", "2026-07-20", "2026-06-01", "2026-05-20", None],
        "survey_date": ["2026-07-01", "2026-07-15", "2026-07-01", "2026-07-01", "2026-07-01"],
        "irrigated": ["false", "false", "false", "true", "false"],
        "sowing_optical": ["2026-06-10", "2026-07-20", "2026-06-01", "2026-05-20", "2026-06-05"],
        "sowing_radar": ["2026-06-15", "2026-07-05", "2026-06-11", None, "2026-06-06"],
        "sowing_ours": ["2026-06-10", "2026-07-20", None, None, None],
        "sowing_client": ["2026-06-13", "2026-07-01", "2026-06-01", None, None],
    })


def test_upper_bound():
    r = sc.upper_bound_violations(_df())
    assert r["violation_rate"]["k"] == 1 and r["violation_rate"]["n"] == 4
    assert r["violation_rate"]["value"] == pytest.approx(0.25)
    assert r["violating_field_ids"] == ["2"]
    assert r["n_skipped_missing"] == 1


def test_onset_lower_bound_excludes_irrigated():
    r = sc.onset_lower_bound(_df(), onset="2026-06-08")
    # rainfed with dates: 1 (06-10), 2 (07-20), 3 (06-01 < 06-03 -> early); 4 irrigated excluded
    assert r["early_rate"]["n"] == 3 and r["early_rate"]["k"] == 1
    assert r["n_irrigated_excluded"] == 1
    assert sc.onset_lower_bound(_df())["status"] == "not_available"


def test_optical_radar_agreement():
    r = sc.optical_radar_agreement(_df())
    # diffs: 5, 15, 10, -, 1 -> within 10: 3 of 4
    assert r["agreement_rate"]["k"] == 3 and r["agreement_rate"]["n"] == 4
    assert r["median_abs_diff_days"] == pytest.approx(7.5)
    assert sc.optical_radar_agreement(_df().drop(columns="sowing_radar"))["status"] == "not_available"


def test_client_errors():
    r = sc.client_date_errors(_df())
    # errors: -3, +19
    assert r["n"] == 2 and r["median_abs_error_days"] == pytest.approx(11.0)
    assert r["p90_abs_error_days"] == pytest.approx(3 + 0.9 * 16)
    assert sc.client_date_errors(_df().drop(columns="sowing_client"))["status"] == "not_available"


def test_run_all():
    r = sc.run_all(_df(), onset="2026-06-08")
    assert r["kind"] == "sowing_checks" and r["upper_bound"]["violation_rate"]["n"] == 4
    assert r["optical_radar"]["status"] == "measured" and r["client"]["status"] == "measured"
