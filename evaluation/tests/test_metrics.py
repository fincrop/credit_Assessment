import math

import numpy as np
import pytest

from evaluation import metrics as m

Z = 1.96


def test_wilson_hand_values():
    lo, hi = m.wilson_interval(8, 10, z=Z)
    # p=.8, z^2/n=.38416: centre=.99208/1.38416, half=1.96*sqrt(.016+.009604)/1.38416
    assert lo == pytest.approx(0.49016, abs=1e-4)
    assert hi == pytest.approx(0.94332, abs=1e-4)
    lo, hi = m.wilson_interval(0, 10, z=Z)
    assert lo == pytest.approx(0.0, abs=1e-12)
    assert hi == pytest.approx(0.27753, abs=1e-4)
    assert all(math.isnan(x) for x in m.wilson_interval(0, 0))


YT = ["A", "A", "A", "B", "B", "C"]
YP = ["A", "A", "B", "B", "C", "C"]


def test_confusion_matrix_rows_are_reference():
    cm = m.confusion_matrix(YT, YP)
    assert list(cm.index) == ["A", "B", "C"]
    assert cm.loc["A", "A"] == 2 and cm.loc["A", "B"] == 1
    assert cm.loc["B", "C"] == 1 and cm.values.sum() == 6


def test_per_class_precision_recall_f1():
    pc = m.per_class_metrics(YT, YP)
    assert pc["A"]["precision"]["value"] == 1.0
    assert pc["A"]["recall"]["value"] == pytest.approx(2 / 3)
    assert pc["A"]["f1"] == pytest.approx(0.8)
    assert pc["B"]["precision"]["value"] == 0.5 and pc["B"]["recall"]["value"] == 0.5
    assert pc["B"]["f1"] == pytest.approx(0.5)
    assert pc["C"]["precision"]["value"] == 0.5 and pc["C"]["recall"]["value"] == 1.0
    assert pc["C"]["f1"] == pytest.approx(2 / 3)
    lo, hi = m.wilson_interval(2, 3)
    assert pc["A"]["recall"]["ci_low"] == pytest.approx(lo)
    assert pc["A"]["support"] == 3 and pc["A"]["predicted"] == 2


def test_balanced_accuracy_and_macro_f1():
    assert m.balanced_accuracy(YT, YP) == pytest.approx((2 / 3 + 0.5 + 1.0) / 3)
    assert m.macro_f1(YT, YP) == pytest.approx((0.8 + 0.5 + 2 / 3) / 3)
    assert m.overall_accuracy(YT, YP)["value"] == pytest.approx(4 / 6)


def test_ece_hand_value_and_bin_edges():
    conf = [0.95, 0.95, 0.85, 0.85, 0.35, 0.30]
    corr = [1, 0, 1, 1, 0, 1]
    r = m.expected_calibration_error(conf, corr)
    # bin9 |.5-.95|*2/6 + bin8 |1-.85|*2/6 + bin3 |.5-.325|*2/6
    assert r["ece"] == pytest.approx(0.15 + 0.05 + 0.175 / 3)
    assert r["bins"][3]["n"] == 2  # 0.30 belongs to [0.3, 0.4)
    r2 = m.expected_calibration_error([0.0, 1.0], [0, 1])
    assert r2["bins"][0]["n"] == 1 and r2["bins"][9]["n"] == 1
    assert r2["ece"] == pytest.approx(0.0)
    with pytest.raises(ValueError):
        m.expected_calibration_error([1.2], [1])


def test_coverage_at_threshold():
    conf = [0.95, 0.95, 0.85, 0.85, 0.35, 0.30]
    corr = [1, 0, 1, 1, 0, 1]
    r = m.coverage_at_threshold(conf, corr, 0.8)
    assert r["coverage"]["value"] == pytest.approx(4 / 6)
    assert r["abstain_rate"] == pytest.approx(2 / 6)
    assert r["precision"]["value"] == pytest.approx(0.75)


def _kappa_case():
    # 2x2: [[20, 5], [10, 15]]  -> po=.7, pe=.5, kappa=.4
    a = ["Y"] * 25 + ["N"] * 25
    b = ["Y"] * 20 + ["N"] * 5 + ["Y"] * 10 + ["N"] * 15
    return a, b


def test_cohen_kappa_hand_value():
    a, b = _kappa_case()
    k = m.cohen_kappa(a, b)
    assert k["po"] == pytest.approx(0.7)
    assert k["pe"] == pytest.approx(0.5)
    assert k["kappa"] == pytest.approx(0.4)
    assert k["se"] == pytest.approx(math.sqrt(0.7 * 0.3 / (50 * 0.25)))
    assert k["interpretation"] == "fair"


def test_kappa_perfect_and_per_class():
    assert m.cohen_kappa(["a", "b", "a"], ["a", "b", "a"])["kappa"] == pytest.approx(1.0)
    a, b = _kappa_case()
    pc = m.per_class_kappa(a, b)
    assert pc["Y"]["kappa"] == pytest.approx(0.4) and pc["N"]["kappa"] == pytest.approx(0.4)


@pytest.mark.parametrize("k,label", [(-0.1, "poor"), (0.1, "slight"), (0.3, "fair"),
                                     (0.5, "moderate"), (0.7, "substantial"),
                                     (0.9, "almost perfect")])
def test_interpret_kappa(k, label):
    assert m.interpret_kappa(k) == label


def test_classification_report_bundle():
    rep = m.classification_report(YT, YP, confidence=[.9, .8, .7, .6, .5, .4], threshold=0.6,
                                  source="mh2023_heldout")
    assert rep["kind"] == "classification_metrics" and rep["n"] == 6
    assert "ece" in rep and rep["coverage"]["coverage"]["k"] == 4
    assert rep["confusion_matrix"]["A"]["B"] == 1
