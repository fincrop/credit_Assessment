"""
Cloud-robust kharif classifier at inference (bundle from
Crop_classification_model/src/train_fused.py).

Every field gets an answer from Sentinel-1 + optical fused on one season
calendar (crop_analysis.fused_features), with no optical cycle detection, so
monsoon cloud lowers confidence instead of producing "Insufficient data".
"""
from __future__ import annotations

import json
import logging
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

FETCH_CHUNK = 200
# Bump when the 10-day Sentinel-1 / reflectance reduction changes. A new model
# or a new Others rule reads the saved series and does not bump this.
SERIES_RECIPE = "s1_refl_10d_v1"
# Below this NDVI peak, with enough clear looks, the field stayed bare.
FALLOW_MAX_NDVI = 0.30
FALLOW_MIN_OPTICAL = 3


@lru_cache(maxsize=2)
def load_bundle(path: str) -> Dict[str, Any]:
    import joblib

    b = joblib.load(path)
    if not isinstance(b, dict) or b.get("tier") != "fused":
        raise ValueError(f"{path} is not a fused classifier bundle")
    return b


def is_fused_bundle(path: Any) -> bool:
    try:
        load_bundle(str(path))
        return True
    except Exception:  # noqa: BLE001
        return False


class FusedClassifier:
    def __init__(self, path: Any):
        self.path = Path(str(path))
        self.b = load_bundle(str(self.path))
        from crop_analysis.fused_features import FEATURE_VERSION
        if self.b.get("feature_version") != FEATURE_VERSION:
            raise ValueError(f"{self.path.name} was trained on features "
                             f"{self.b.get('feature_version')}, this build computes {FEATURE_VERSION}")
        self.names: List[str] = list(self.b["feature_names"])
        self.classes: List[str] = list(self.b["classes"])
        self.imputer = self.b.get("imputer")
        self.T = float(self.b.get("temperature") or 1.0)
        # Regional prior correction fitted on out-of-fold Marathwada rows (v2+).
        self.bias = np.asarray(self.b.get("class_bias") or np.zeros(len(self.classes)), float)
        self.rule = dict(self.b.get("abstain_rule") or {"p_min": 0.35, "gap_min": 0.10})

    @property
    def crop_names(self) -> List[str]:
        return self.classes

    def features(self, s1, refl, year: int, as_of: Optional[date]):
        from crop_analysis.fused_features import build
        return build(s1, refl, year, self.imputer, as_of)

    def predict(self, feats: Mapping[str, float]) -> Dict[str, Any]:
        """Same answer shape as CropDetector._classify_crop_chronological."""
        X = np.array([[float(feats.get(n, np.nan)) for n in self.names]], float)
        model = self.b["model"]
        raw = np.clip(model.predict_proba(X)[0], 1e-9, 1.0)
        lg = np.full(len(self.classes), np.log(1e-9))
        lg[np.asarray(model.classes_, int)] = np.log(raw)
        T, bias = self.T, self.bias
        seen = float(feats.get("season_seen_steps") or 0.0)
        for g in self.b.get("calibration") or []:       # stage-specific (v2+), ascending
            if seen >= float(g["min_seen_steps"]):
                T, bias = float(g["temperature"]), np.asarray(g["class_bias"], float)
        z = lg / T + bias
        z -= z.max()
        p = np.exp(z) / np.exp(z).sum()
        order = np.argsort(p)[::-1]
        p1, p2 = float(p[order[0]]), float(p[order[1]]) if len(p) > 1 else 0.0
        reason = None
        if p1 < float(self.rule.get("p_min", 0.0)):
            reason = f"p_max {p1:.2f} < {self.rule['p_min']}"
        elif p1 - p2 < float(self.rule.get("gap_min", 0.0)):
            reason = f"top2_gap {p1 - p2:.2f} < {self.rule['gap_min']}"
        return {
            "all_probabilities": {c: float(v) for c, v in zip(self.classes, p)},
            "abstained": reason is not None, "abstain_reason": reason,
            "raw_confidence": p1, "top2_gap": p1 - p2,
        }

    def provenance(self) -> Dict[str, Any]:
        import hashlib
        h = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return {"name": self.path.stem, "path": self.path.name, "sha256": h,
                "extractor_version": self.b.get("feature_version"), "classes": self.classes,
                "tier": "fused", "imputer_r2": (self.b.get("imputer_info") or {}).get("test_r2")}


def _series_path(geometry: Any, lo: date, hi: date, cache_root) -> Path:
    from crop_analysis.replay_store import series_key, store

    key = series_key(geometry, lo.isoformat(), hi.isoformat(), SERIES_RECIPE)
    return store(cache_root) / "series" / f"{key}.json"


def _load_series(path: Path) -> Optional[Dict[str, list]]:
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("series cache unreadable (%s); downloading again", str(exc)[:160])
        return None
    if not isinstance(doc, dict) or "s1" not in doc or "refl" not in doc:
        return None
    return doc


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.floating):
        v = float(value)
        return None if not np.isfinite(v) else v
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def fetch_series(ee, objects: Sequence[Mapping[str, Any]], lo: date, hi: date, progress=None,
                 cache_root=None) -> Dict[str, Dict[str, list]]:
    """S1 + reflectance 10-day series for every object (batched reduceRegions).

    Each field is stored under a hash of its geometry and the date window.
    A rerun downloads only fields that are not already on disk, and a failed
    chunk is not written, so the next run retries just that chunk.
    """
    from crop_analysis.replay_store import atomic_json

    out: Dict[str, Dict[str, list]] = {}
    missing: List[Mapping[str, Any]] = []
    for obj in objects:
        hit = _load_series(_series_path(obj["geometry"], lo, hi, cache_root))
        if hit is None:
            missing.append(obj)
        else:
            out[str(obj["field_id"])] = hit
    if not missing:
        logger.info("classification series: %d fields from disk", len(out))
        if progress:
            progress(len(objects), len(objects))
        return out

    from data_acquisition.extra_sources import fetch_time_series

    logger.info("classification series: %d fields on disk, %d to download", len(out), len(missing))
    done = len(out)
    for k in range(0, len(missing), FETCH_CHUNK):
        chunk = missing[k:k + FETCH_CHUNK]
        done += len(chunk)
        if progress:
            progress(done, len(objects))
        try:
            res = fetch_time_series(ee, [(str(o["field_id"]), o["geometry"]) for o in chunk],
                                    lo, hi, ["s1", "refl"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("fused series chunk failed: %s", str(exc)[:160])
            res = {}
        for obj in chunk:
            fid = str(obj["field_id"])
            if fid not in res:
                continue
            series = _jsonable(res[fid])
            atomic_json(_series_path(obj["geometry"], lo, hi, cache_root), series)
            out[fid] = series
    return out


def is_fallow(feats: Mapping[str, float]) -> bool:
    mx = feats.get("ndvi_max")
    return (mx is not None and np.isfinite(mx) and mx < FALLOW_MAX_NDVI
            and (feats.get("n_opt") or 0) >= FALLOW_MIN_OPTICAL)
