"""
Serialisable model wrappers for the crop classifier bundle.

WHY THIS FILE LIVES HERE AND NOT IN THE TRAINING PROJECT
────────────────────────────────────────────────────────
A joblib/pickle stores the *import path* of every custom class, not its code. The
first exported bundle defined these classes in the trainer, which runs as
`python -m src.train`, so they were pickled as `__main__.CalibratedBundle` — and
even with the module name fixed, `Crop_classification_model/src/` does not exist
on the production server at all. Loading raised:

    AttributeError: Can't get attribute 'CalibratedBundle' on <module '__main__'>

So any class that is part of a bundle must be importable from the DEPLOYED
package. The trainer imports these from here; the pickle then records
`crop_analysis.model_bundle.*`, which production can resolve.

Nothing here depends on the training project. `ContiguousLabelXGB` imports
xgboost lazily, inside `fit`, so loading a bundle for inference needs only the
already-pinned runtime.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np

__all__ = ["TemperatureScaler", "ContiguousLabelXGB", "CalibratedBundle"]


class TemperatureScaler:
    """
    Single-parameter multiclass calibration on log-probabilities.

    Chosen over per-class isotonic deliberately: 18 monotone fits on ~1k
    calibration rows would overfit, and temperature scaling cannot reorder
    classes, so accuracy is untouched while confidences become meaningful.

    Calibration matters here because `performance_analyzer` forks on
    `crop_confidence >= 0.25` to unlock ICAR-curve crop-specific scoring, where
    a wrong crop name swings up to 45% of the risk index.
    """

    def __init__(self) -> None:
        self.temperature: float = 1.0

    def fit(self, proba: np.ndarray, y: np.ndarray) -> "TemperatureScaler":
        eps = 1e-12
        logp = np.log(np.clip(proba, eps, 1.0))
        idx = np.arange(len(y))

        best_t, best_nll = 1.0, np.inf
        # Coarse-to-fine scan: derivative-free, robust, and a few hundred
        # closed-form NLL evaluations cost nothing next to fitting the model.
        for lo, hi, step in ((0.20, 6.0, 0.05), (None, None, 0.005)):
            if lo is None:
                lo, hi = max(0.05, best_t - 0.10), best_t + 0.10
            for t in np.arange(lo, hi + 1e-9, step):
                z = logp / t
                z -= z.max(axis=1, keepdims=True)
                p = np.exp(z)
                p /= p.sum(axis=1, keepdims=True)
                nll = -np.mean(np.log(np.clip(p[idx, y], eps, 1.0)))
                if nll < best_nll:
                    best_nll, best_t = nll, float(t)
        self.temperature = best_t
        return self

    def transform(self, proba: np.ndarray) -> np.ndarray:
        eps = 1e-12
        z = np.log(np.clip(proba, eps, 1.0)) / self.temperature
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        return p / p.sum(axis=1, keepdims=True)


class ContiguousLabelXGB:
    """
    XGBClassifier that tolerates a fold missing a class.

    XGBoost 2.0.3 requires y to be exactly [0..k-1] and raises otherwise:

        Invalid classes inferred from unique values of `y`.
        Expected: [0 1 2 3], got [0 1 3 5]

    That is the normal case under spatially-blocked CV — Chilli occupies 6
    spatial blocks and Tobacco 7, so some fold's training set will be missing a
    class. Labels are remapped to a contiguous range for fitting, and `classes_`
    reports the ORIGINAL labels in `predict_proba` column order so callers can
    re-embed into the full class space.
    """

    def __init__(self, **params: Any) -> None:
        self.params: Dict[str, Any] = dict(params)
        self.est = None
        self.classes_: Optional[np.ndarray] = None

    def fit(self, X, y, sample_weight=None) -> "ContiguousLabelXGB":
        from xgboost import XGBClassifier

        y = np.asarray(y)
        self.classes_ = np.unique(y)
        remap = {c: i for i, c in enumerate(self.classes_)}
        y_contig = np.fromiter((remap[v] for v in y), dtype=int, count=len(y))

        self.est = XGBClassifier(**self.params)
        self.est.fit(X, y_contig, sample_weight=sample_weight)
        return self

    def predict_proba(self, X):
        # Columns align with self.classes_.
        return self.est.predict_proba(X)

    def predict(self, X):
        return np.asarray(self.classes_)[np.argmax(self.predict_proba(X), axis=1)]

    @property
    def feature_importances_(self):
        return self.est.feature_importances_


class CalibratedBundle:
    """
    Estimator + temperature, exposing exactly the surface CropDetector uses.

    CropDetector calls `.predict()` and `.predict_proba()` and nothing else.
    """

    def __init__(self, estimator, scaler: TemperatureScaler, classes) -> None:
        self.estimator = estimator
        self.scaler = scaler
        self.classes_ = np.asarray(classes)

    def predict_proba(self, X):
        """
        Full-width calibrated probabilities, in `classes_` order.

        CropDetector zips these columns against `crop_names`, so the width must
        be the full class count even if the estimator never saw a class — an
        absent class gets probability 0 rather than shifting every column after
        it, which would mislabel every probability in the output.
        """
        p = self.scaler.transform(self.estimator.predict_proba(X))
        seen = np.asarray(getattr(self.estimator, "classes_", self.classes_))
        if len(seen) == len(self.classes_):
            return p
        full = np.zeros((p.shape[0], len(self.classes_)))
        full[:, np.searchsorted(self.classes_, seen)] = p
        return full

    def predict(self, X):
        return np.asarray(self.classes_)[np.argmax(self.predict_proba(X), axis=1)]
