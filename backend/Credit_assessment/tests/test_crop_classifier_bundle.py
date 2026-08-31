"""
The exported crop-classifier bundle must load in PRODUCTION, not just in the
trainer's process.

This is a regression test for a real failure. The first exported bundle pickled
its wrapper classes from `src/train.py`, run as `python -m src.train`, so joblib
recorded them as `__main__.CalibratedBundle`. Production would have raised

    AttributeError: Can't get attribute 'CalibratedBundle' on <module '__main__'>

on the first inference — not at build time, and not anywhere a build check would
have looked. Worse, that module lives in `Crop_classification_model/`, which is
not deployed at all, so even the corrected module name would not have resolved.

These tests import ONLY from the deployed package, exactly as the API and worker
do. They skip when no bundle is present so CI stays green on a clean checkout.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

BUNDLE_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "models" / "crop_classifier_tier1_v1.joblib",
    Path(__file__).resolve().parents[3] / "Crop_classification_model" / "models"
    / "crop_classifier_tier1_v1.joblib",
]


def _bundle_path():
    for p in BUNDLE_CANDIDATES:
        if p.exists():
            return p
    return None


pytestmark = pytest.mark.skipif(
    _bundle_path() is None,
    reason="no exported crop_classifier_tier1_v1.joblib to verify",
)


@pytest.fixture(scope="module")
def bundle():
    import joblib

    return joblib.load(_bundle_path())


def test_wrapper_classes_resolve_from_the_deployed_package():
    """The classes a bundle contains must be importable from the backend."""
    from crop_analysis.model_bundle import (
        CalibratedBundle, ContiguousLabelXGB, TemperatureScaler,
    )

    for cls in (CalibratedBundle, ContiguousLabelXGB, TemperatureScaler):
        assert cls.__module__ == "crop_analysis.model_bundle", (
            f"{cls.__name__} is pickled under {cls.__module__!r}; a bundle "
            "referencing that path will not load in production"
        )


def test_bundle_has_every_contract_key(bundle):
    """CropDetector.__init__ reads these four and nothing else is optional."""
    for key in ("model", "label_encoder", "feature_names", "crop_names"):
        assert key in bundle, f"bundle is missing required key {key!r}"


def test_pickled_model_is_not_from_main(bundle):
    """The exact failure this file exists for."""
    mod = type(bundle["model"]).__module__
    assert mod != "__main__", (
        "model was pickled from __main__ — it will not unpickle anywhere else"
    )
    assert not mod.startswith("src."), (
        f"model was pickled from {mod!r}, which is not deployed"
    )


def test_crop_names_match_label_encoder_order(bundle):
    """CropDetector zips crop_names against predict_proba columns."""
    classes = list(bundle["label_encoder"].classes_)
    assert list(bundle["crop_names"]) == classes


def test_every_class_is_known_to_the_reference_curves(bundle):
    """A predicted crop must resolve in CropGrowthCurves or Stage 6 cannot use it."""
    from config import CropGrowthCurves

    for crop in bundle["crop_names"]:
        resolved = CropGrowthCurves._normalize_crop_name(crop)
        assert resolved in CropGrowthCurves.CROP_DURATIONS, (
            f"predicted crop {crop!r} has no reference curve"
        )


def test_detector_loads_it_and_scores_a_cycle(bundle):
    """End to end through the real consumer, with a real-shaped cycle."""
    from crop_analysis.crop_detector import CropDetector

    det = CropDetector(
        crop_model_path=str(_bundle_path()),
        latitude=20.0, longitude=76.0, verbose=False,
    )

    n_feat = len(bundle["feature_names"])
    assert len(det.crop_names) == len(bundle["crop_names"])

    scenes = [
        {
            "date": f"2023-{m:02d}-{d:02d}",
            "indices": {
                k: v for k, v in (
                    ("NDVI_mean", 0.2 + 0.05 * i), ("EVI_mean", 0.18 + 0.04 * i),
                    ("NDMI_mean", 0.10 + 0.02 * i), ("NDRE_mean", 0.15),
                    ("PSRI_mean", 0.02), ("kNDVI_mean", 0.1),
                    ("LSWI_mean", 0.05), ("GCVI_mean", 1.2), ("NDVI_std", 0.04),
                )
            },
        }
        for i, (m, d) in enumerate(
            [(1, 5), (1, 15), (1, 25), (2, 5), (2, 15), (2, 25),
             (3, 5), (3, 15), (3, 25), (4, 5), (4, 15), (4, 25)]
        )
    ]

    cycle = {
        "duration_days": 120, "peak_ndvi": 0.75, "baseline_ndvi": 0.18,
        "ndvi_rise": 0.57, "integral_ndvi_days": 55.0, "peak_evi": 0.68,
        "peak_ndmi": 0.30, "confidence": 0.8, "peak_observed": True,
        "observed_fraction": 0.9,
    }

    out = det._classify_crop_chronological(scenes, cycle=cycle)

    assert out["abstain_reason"] != "cycle_metadata_unavailable", (
        f"a {n_feat}-feature bundle could not be scored with a cycle supplied"
    )
    # Whether it abstains on confidence is fine; the probability vector is not.
    if not out["abstained"]:
        assert out["crop"] in bundle["crop_names"]
    probs = out["all_probabilities"]
    assert set(probs) == set(bundle["crop_names"])
    assert abs(sum(probs.values()) - 1.0) < 1e-5


def test_cycle_dependent_bundle_abstains_without_a_cycle(bundle):
    """
    Never zero-fill. A tier-1 bundle scored without a cycle would silently get
    0.0 for duration and every phenology scalar and still return a confident
    answer; it must refuse instead.
    """
    from crop_analysis.crop_detector import TIER1_SCALAR_NAMES, CropDetector

    needs_cycle = any(f in TIER1_SCALAR_NAMES for f in bundle["feature_names"])
    if not needs_cycle:
        pytest.skip("bundle is tier-0; it does not need cycle metadata")

    det = CropDetector(
        crop_model_path=str(_bundle_path()),
        latitude=20.0, longitude=76.0, verbose=False,
    )
    assert det.requires_cycle

    scenes = [
        {"date": f"2023-01-{d:02d}",
         "indices": {"NDVI_mean": 0.4, "EVI_mean": 0.35, "NDMI_mean": 0.2}}
        for d in (1, 6, 11, 16, 21, 26)
    ] + [
        {"date": f"2023-02-{d:02d}",
         "indices": {"NDVI_mean": 0.5, "EVI_mean": 0.45, "NDMI_mean": 0.25}}
        for d in (1, 6, 11, 16, 21, 26)
    ]

    out = det._classify_crop_chronological(scenes)
    assert out["abstained"] is True
    assert out["crop"] is None
    assert out["abstain_reason"] == "cycle_metadata_unavailable"


def test_declared_metrics_are_the_blocked_ones(bundle):
    """
    The bundle must carry the honest number, not a random-split one.

    A random split on this dataset scores ~0.13 higher because 233 of 280
    spatial blocks hold a single crop; recording that figure would misrepresent
    the model to anyone reading the bundle.
    """
    assert "GroupKFold" in str(bundle.get("cv_protocol", "")), (
        "cv_protocol must record the spatially-blocked protocol"
    )
    metrics = bundle.get("metrics") or {}
    if metrics:
        assert 0.0 < metrics.get("balanced_accuracy", 0) < 1.0
    assert bundle.get("leakage_gap") is not None, (
        "leakage_gap must be recorded so the blocked/random difference is visible"
    )
