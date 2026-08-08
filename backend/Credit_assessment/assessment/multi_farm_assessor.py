"""
Multi-Farm Assessor (orchestrator)
==================================
Runs the EXISTING single-farm pipeline once per owned plot, then aggregates the
per-farm results into one farmer-level agronomic risk index via FarmerAggregator.

Per-plot execution is sequential (do not parallelize GEE — concurrency limits +
stable stream order). Optional on_plot_done fires once per completed plot for
job progress.partial_result (farm_assessments only — never farmer_level).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from .farmer_aggregator import FarmerAggregator, INDEX_VERSION

logger = logging.getLogger(__name__)

try:
    from shapely.geometry import shape as _shp_shape
    from shapely.geometry.base import BaseGeometry as _ShapelyBase
    import shapely.wkt as _shp_wkt

    _SHAPELY_OK = True
except Exception:  # pragma: no cover
    _shp_shape = None
    _ShapelyBase = ()
    _shp_wkt = None
    _SHAPELY_OK = False


def assign_plot_keys(farms: List[Dict]) -> List[Dict]:
    """
    Guarantee a unique plot_key on each farm dict (mutates copies).
    Key = farm_id || survey_sub || plot_{i}, with _{i} suffix on collisions.
    """
    seen: Dict[str, int] = {}
    out: List[Dict] = []
    for i, farm in enumerate(farms):
        f = dict(farm)
        raw = (
            str(f.get("farm_id") or "").strip()
            or "_".join(
                p
                for p in (
                    str(f.get("survey_number") or "").strip(),
                    str(f.get("sub_survey_number") or "").strip(),
                )
                if p
            )
            or f"plot_{i}"
        )
        key = raw
        if key in seen:
            key = f"{raw}_{i}"
        seen[key] = 1
        f["plot_key"] = key
        if not f.get("farm_id"):
            f["farm_id"] = key
        out.append(f)
    return out


def counters_from_farm_assessments(rows: List[Dict]) -> Dict[str, int]:
    """Single-source counters from slim farm_assessments rows."""
    n_total = len(rows)
    n_scored = 0
    n_failed = 0
    n_skipped = 0
    for r in rows:
        reason = str(r.get("skipped_reason") or "")
        if reason.startswith("error:"):
            n_failed += 1
        elif not r.get("included", True) or reason:
            n_skipped += 1
        elif r.get("index_score") is not None:
            n_scored += 1
        else:
            n_skipped += 1
    return {
        "n_plots_total": n_total,
        "n_plots_scored": n_scored,
        "n_plots_failed": n_failed,
        "n_plots_skipped": n_skipped,
        "n_plots_done": n_scored + n_skipped + n_failed,
    }


class MultiFarmAssessor:
    """Compose the single-farm pipeline across a farmer's plots."""

    def __init__(
        self,
        pipeline: Any,
        aggregator: Optional[FarmerAggregator] = None,
        max_plots: int = 12,
        keep_full_per_farm: bool = False,
    ):
        self.pipeline = pipeline
        self.aggregator = aggregator or FarmerAggregator()
        self.max_plots = int(max_plots)
        self.keep_full = bool(keep_full_per_farm)

    def assess_farmer_multi(
        self,
        farm_info: Dict,
        save_to_db: bool = True,
        on_plot_done: Optional[Callable[[List[Dict]], None]] = None,
    ) -> Dict:
        farmer_id = farm_info.get("farmer_id")
        farms = list(farm_info.get("farms") or [])
        farmer_benefits = farm_info.get("farmer_benefits")

        if not farms:
            farms = [
                {
                    "farm_id": farm_info.get("farm_id") or f"{farmer_id}_main",
                    "geometry": farm_info.get("geometry"),
                    "area_ha": farm_info.get("field_area_ha"),
                    "primary_crop": farm_info.get("crop"),
                    "sowing_date": farm_info.get("sowing_date"),
                    "centroid": {
                        "lat": farm_info.get("latitude"),
                        "lng": farm_info.get("longitude"),
                    },
                    "district_lgd_code": farm_info.get("district_lgd_code"),
                    "included_in_assessment": True,
                    "is_ror_owner": True,
                }
            ]

        # Sequential loop only — do not parallelize per-plot GEE calls.
        farms = assign_plot_keys(farms)
        per_farm: List[Dict] = []
        scored_count = 0

        def _emit() -> None:
            if on_plot_done is None:
                return
            try:
                on_plot_done(list(per_farm))
            except Exception as e:
                logger.debug("on_plot_done failed: %s", e)

        for farm in farms:
            tf = FarmerAggregator.tenure_factor(farm)
            included = bool(farm.get("included_in_assessment", True))

            if not included or tf <= 0.0:
                per_farm.append(self._skipped_record(farm, tf, "excluded_or_no_tenure"))
                _emit()
                continue
            if scored_count >= self.max_plots:
                per_farm.append(self._skipped_record(farm, tf, "over_plot_cap"))
                _emit()
                continue
            if not farm.get("geometry") and not (farm.get("centroid") or {}).get("lat"):
                per_farm.append(self._skipped_record(farm, tf, "no_geometry"))
                _emit()
                continue

            try:
                assessment = self._assess_one(farmer_id, farm, farmer_benefits)
                status = str(assessment.get("status", "")).upper()
                if status and status != "SUCCESS":
                    err = assessment.get("error") or (
                        (assessment.get("errors") or ["pipeline_non_success"])[0]
                    )
                    per_farm.append(
                        self._skipped_record(farm, tf, f"error:{str(err)[:80]}")
                    )
                    _emit()
                    continue
                per_farm.append(self._slim_farm_result(assessment, farm, tf))
                scored_count += 1
                _emit()
            except Exception as e:
                logger.warning(
                    "Plot %s failed: %s",
                    farm.get("plot_key") or farm.get("farm_id"),
                    str(e)[:160],
                )
                per_farm.append(self._skipped_record(farm, tf, f"error:{str(e)[:80]}"))
                _emit()

        farmer_result = self.aggregator.aggregate(per_farm, farm_info)
        farmer_result["farm_assessments"] = per_farm
        farmer_result["assessment_type"] = "multi_farm"
        farmer_result["index_version"] = INDEX_VERSION

        counts = counters_from_farm_assessments(per_farm)
        farmer_result.update(counts)
        n_total = counts["n_plots_total"]
        n_failed = counts["n_plots_failed"]
        n_skipped = counts["n_plots_skipped"]
        scored_count = counts["n_plots_scored"]

        fl = farmer_result.get("farmer_level") or {}
        score = fl.get("index_score")
        category = fl.get("risk_category")
        warnings: List[str] = []
        if n_failed > 0 and scored_count > 0:
            warnings.append(f"scored {scored_count}/{n_total}, {n_failed} failed")
        if score is None or category == "INSUFFICIENT_DATA":
            farmer_result["status"] = "FAILED"
            plot_errs = [
                str(r.get("skipped_reason") or "").removeprefix("error:").strip()
                for r in per_farm
                if str(r.get("skipped_reason") or "").startswith("error:")
            ]
            if plot_errs and scored_count == 0:
                farmer_result["error"] = (
                    f"All plots failed analysis: {plot_errs[0]}"
                    + (f" (+{len(plot_errs) - 1} more)" if len(plot_errs) > 1 else "")
                )
            else:
                farmer_result["error"] = (
                    fl.get("reason_codes") or [{}]
                )[0].get("message") or "No scorable plots"
        else:
            farmer_result["status"] = "SUCCESS"
        if warnings:
            farmer_result["warnings"] = warnings

        # One AI enrichment on the farmer aggregate (per-plot runs skip STEP 8).
        try:
            from ai_integration.enrichment import enrich_assessment_with_ai

            if enrich_assessment_with_ai(farmer_result):
                stages = list(farmer_result.get("pipeline_stages") or [])
                if "10_ai" not in stages:
                    stages.append("10_ai")
                farmer_result["pipeline_stages"] = stages
        except Exception as e:
            logger.debug("farmer-level AI enrichment skipped: %s", e)

        if save_to_db:
            self._persist(farmer_id, farmer_result)

        logger.info(
            "Multi-farm: farmer=%s plots=%d scored=%d skipped=%d failed=%d index=%s",
            farmer_id,
            n_total,
            scored_count,
            n_skipped,
            n_failed,
            score,
        )
        return farmer_result

    def assess_farmer_multi_from_db(self, farmer_id: str, save_to_db: bool = True) -> Dict:
        mongo = getattr(self.pipeline, "db", None) or getattr(
            self.pipeline, "mongo_helper", None
        )
        farm_info = None
        if mongo is not None and hasattr(mongo, "get_farm_by_id"):
            farm_info = mongo.get_farm_by_id(farmer_id)
        if not farm_info:
            raise ValueError(f"No farm_info found for farmer_id={farmer_id}")
        return self.assess_farmer_multi(farm_info, save_to_db=save_to_db)

    @staticmethod
    def _to_shapely(geometry):
        """Normalize plot geometry to Shapely for assess_farmer."""
        if geometry is None:
            return None
        if _SHAPELY_OK and isinstance(geometry, _ShapelyBase):
            return geometry if not geometry.is_empty else None
        if not _SHAPELY_OK:
            return geometry
        if isinstance(geometry, dict):
            geom = (
                geometry.get("geometry", geometry)
                if "geometry" in geometry and "type" not in geometry
                else geometry
            )
            if isinstance(geometry, dict) and geometry.get("type") == "Feature":
                geom = geometry.get("geometry")
            try:
                g = _shp_shape(geom)
                return g if (g is not None and not g.is_empty) else None
            except Exception:
                return None
        if isinstance(geometry, str):
            s = geometry.strip()
            try:
                g = _shp_wkt.loads(s)
                if g is not None and not g.is_empty:
                    return g
            except Exception:
                pass
            try:
                g = _shp_shape(json.loads(s))
                return g if (g is not None and not g.is_empty) else None
            except Exception:
                return None
        return None

    def _assess_one(
        self, farmer_id: str, farm: Dict, farmer_benefits: Optional[Dict]
    ) -> Dict:
        centroid = farm.get("centroid") or {}
        raw_geom = farm.get("geometry")
        conv = getattr(self.pipeline, "_convert_geometry_from_db", None)
        geom = None
        if conv and isinstance(raw_geom, (dict, list, str)):
            try:
                geom = conv(raw_geom)
            except Exception:
                geom = None
        if geom is None:
            geom = self._to_shapely(raw_geom)

        return self.pipeline.assess_farmer(
            farmer_id=farmer_id,
            geometry=geom,
            field_area_ha=farm.get("area_ha"),
            latitude=centroid.get("lat"),
            longitude=centroid.get("lng"),
            crop_hint=farm.get("primary_crop"),
            sowing_date=farm.get("sowing_date"),
            farmer_benefits=farmer_benefits,
            farm_metadata={
                "state_lgd_code": farm.get("state_lgd_code"),
                "district_lgd_code": farm.get("district_lgd_code"),
                "farm_id": farm.get("farm_id"),
                "plot_key": farm.get("plot_key"),
            },
            save_to_db=False,
            enable_crop_classification=False,
            skip_ai_enrichment=True,
        )

    @staticmethod
    def _sub_scalars(assessment: Dict) -> Dict[str, float]:
        ra = assessment.get("risk_assessment") or {}
        subs = ra.get("sub_indices") or {}
        if subs:
            return {
                k: float(v.get("score"))
                for k, v in subs.items()
                if isinstance(v, dict) and v.get("score") is not None
            }
        cr = assessment.get("credit_assessment") or {}
        cs = cr.get("component_scores") or {}
        out = {}
        for k, v in cs.items():
            if isinstance(v, dict):
                v = v.get("score")
            if isinstance(v, (int, float)):
                out[k] = float(v)
        return out

    def _slim_farm_result(self, assessment: Dict, farm: Dict, tenure_factor: float) -> Dict:
        ra = assessment.get("risk_assessment") or {}
        cr = assessment.get("credit_assessment") or {}
        cycles = (assessment.get("crop_cycles") or {}).get("cycles") or []
        season_types = [c.get("season_type") for c in cycles if c.get("season_type")]
        rec = {
            "plot_key": farm.get("plot_key") or farm.get("farm_id"),
            "farm_id": farm.get("farm_id"),
            "area_ha": float(farm.get("area_ha") or assessment.get("field_area_ha") or 0.0),
            "tenure_factor": round(float(tenure_factor), 3),
            "included": True,
            "is_ror_owner": farm.get("is_ror_owner"),
            "crop": farm.get("primary_crop") or assessment.get("crop_hint"),
            "district": farm.get("district_lgd_code"),
            "season_types": season_types,
            "index_score": ra.get("index_score", cr.get("credit_score")),
            "raw_index": ra.get("raw_index"),
            "risk_category": ra.get("risk_category", cr.get("risk_category")),
            "confidence_gate": ra.get("confidence_gate"),
            "sub_indices": self._sub_scalars(assessment),
            "reason_codes": (ra.get("reason_codes") or [])[:8],
        }
        detail = self._plot_detail_for_ui(assessment)
        if detail:
            rec["detail"] = detail
        if self.keep_full:
            rec["full_assessment"] = assessment
        return rec

    @staticmethod
    def _plot_detail_for_ui(assessment: Dict) -> Dict:
        """
        Compact per-plot analysis blocks for the farm detail UI.
        Omits bulky satellite scene arrays; keeps cropping / weather / cycles / performance.
        """
        detail: Dict[str, Any] = {}

        ca = assessment.get("cropping_analysis")
        if isinstance(ca, dict) and ca:
            ca2 = dict(ca)
            seasons = ca2.get("season_results")
            if isinstance(seasons, list):
                trimmed = []
                for s in seasons[:16]:
                    if not isinstance(s, dict):
                        continue
                    s2 = {k: v for k, v in s.items() if k != "interval_indices"}
                    intervals = s.get("interval_indices")
                    if isinstance(intervals, list) and intervals:
                        step = max(1, len(intervals) // 36)
                        s2["interval_indices"] = intervals[::step][:36]
                    trimmed.append(s2)
                ca2["season_results"] = trimmed
            detail["cropping_analysis"] = ca2

        for key in (
            "performance_analysis",
            "weather_analysis",
            "crop_cycles",
            "continuous_data_stats",
            "location",
            "weather_intervals",
        ):
            val = assessment.get(key)
            if val is not None and val != {} and val != []:
                detail[key] = val

        # Plot-level AI block when present (often only on farmer aggregate)
        ai = assessment.get("ai_enrichment")
        if isinstance(ai, dict) and ai:
            detail["ai_enrichment"] = ai

        return detail

    @staticmethod
    def _skipped_record(farm: Dict, tenure_factor: float, reason: str) -> Dict:
        return {
            "plot_key": farm.get("plot_key") or farm.get("farm_id"),
            "farm_id": farm.get("farm_id"),
            "area_ha": float(farm.get("area_ha") or 0.0),
            "tenure_factor": round(float(tenure_factor), 3),
            "included": False,
            "is_ror_owner": farm.get("is_ror_owner"),
            "crop": farm.get("primary_crop"),
            "district": farm.get("district_lgd_code"),
            "sub_indices": {},
            "skipped_reason": reason,
        }

    def _persist(self, farmer_id: str, farmer_result: Dict) -> None:
        mongo = getattr(self.pipeline, "db", None) or getattr(
            self.pipeline, "mongo_helper", None
        )
        if mongo is not None and hasattr(mongo, "save_multi_farm_assessment"):
            try:
                mongo.save_multi_farm_assessment(farmer_id, farmer_result)
            except Exception as e:
                logger.warning("save_multi_farm_assessment failed: %s", e)


__all__ = ["MultiFarmAssessor", "assign_plot_keys", "counters_from_farm_assessments"]
