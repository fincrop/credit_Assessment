"""Fused classifier: stage-specific calibration and the abstain rule."""
from __future__ import annotations

import joblib
import numpy as np

from crop_analysis.fused_classifier import FusedClassifier


class _Fixed:
    classes_ = np.array([0, 1])

    def predict_proba(self, X):
        return np.array([[0.7, 0.3]] * len(X))


def _bundle(tmp_path, **extra):
    from crop_analysis.fused_features import FEATURE_VERSION
    b = {"tier": "fused", "feature_version": FEATURE_VERSION, "feature_names": ["season_seen_steps"],
         "classes": ["Cotton", "Soyabean"], "model": _Fixed(), "temperature": 1.0, "abstain_rule": {"p_min": 0.35, "gap_min": 0.10},
         **extra}
    p = tmp_path / f"fused_{len(extra)}.joblib"
    joblib.dump(b, p)
    return FusedClassifier(p)


def test_late_season_calibration_is_used_from_its_step(tmp_path):
    clf = _bundle(tmp_path, calibration=[
        {"min_seen_steps": 0, "temperature": 3.0, "class_bias": [0.0, 0.0]},     # soft in season
        {"min_seen_steps": 19, "temperature": 0.5, "class_bias": [0.0, 0.0]},    # sharp late
    ])
    early = clf.predict({"season_seen_steps": 16})["all_probabilities"]["Cotton"]
    late = clf.predict({"season_seen_steps": 25})["all_probabilities"]["Cotton"]
    assert 0.5 < early < 0.7 < late


def test_bundle_from_other_feature_version_is_refused(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        _bundle(tmp_path, feature_version="fused_v1")


def test_bundle_without_calibration_keeps_single_temperature(tmp_path):
    clf = _bundle(tmp_path)
    p = clf.predict({"season_seen_steps": 25})
    assert abs(p["all_probabilities"]["Cotton"] - 0.7) < 1e-6
    assert not p["abstained"]
