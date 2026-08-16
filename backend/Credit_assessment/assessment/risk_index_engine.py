"""
Risk Index Engine (Pillar 5)
============================
Composes the pipeline's agronomic signals into an explainable, expert-weighted
AGRONOMIC RISK INDEX (0-100). This REPLACES the loan-amount credit scorer:

  * NO rupee limit / repayment calibration (that is a later phase; hooks reserved).
  * FIVE decoupled sub-indices (so each can be validated/recalibrated alone):
        1. Land-Use & Activity     (Capacity)
        2. Vigor & Yield-Potential (Capacity / Character)
        3. Stability & Stress      (Character)
        4. Weather                 (Conditions)
        5. Data-Confidence         (meta -> multiplicative GATE, not additive)
  * AHP-style additive weights over sub-indices 1-4; Data-Confidence gates the
    result in [gate_min, 1.0] so a cloud-blind / thin-history parcel is never
    silently scored as if it were fully observed.
  * Tri-state government benefits: a POSITIVE-only bonus. Unknown NEVER penalises
    (fixes the worker-path unknown->False collapse from a scoring standpoint).
  * Deterministic reason codes + a calibration/versioning block for the future
    validation & performance-feedback layer.

Input contract: the accumulated `assessment` dict produced by the pipeline
(cropping_analysis, performance_analysis, weather_analysis, farmer_benefits,
field_area_ha, and the Pillar-1 satellite signal-quality summary). All reads are
defensive with safe defaults, so partial assessments degrade gracefully.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from config import PipelineConfig

try:
    from utils.farmer_benefits import normalize_farmer_benefits, truthy_benefit_flag
    _BENEFITS_HELPERS = True
except Exception:  # pragma: no cover
    _BENEFITS_HELPERS = False

logger = logging.getLogger(__name__)

INDEX_VERSION = "index_v5"

# Default AHP-style sub-index weights (sum to 100 over the 4 substantive
# sub-indices; Data-Confidence is a multiplier, not a weight). Overridable via
# PipelineConfig.SUBINDEX_WEIGHTS after expert AHP elicitation.
_DEFAULT_WEIGHTS = {"landuse": 30.0, "vigor": 25.0, "stability": 20.0, "weather": 25.0}


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


class RiskIndexEngine:
    """Expert-weighted, explainable agronomic risk index."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        peer_benchmark: Any = None,
        verbose: bool = True,
    ):
        cfg_w = getattr(PipelineConfig, "SUBINDEX_WEIGHTS", None)
        self.weights = dict(weights or cfg_w or _DEFAULT_WEIGHTS)
        # Renormalise defensively to sum 100.
        s = sum(self.weights.values()) or 1.0
        self.weights = {k: round(v * 100.0 / s, 3) for k, v in self.weights.items()}
        self.peer = peer_benchmark
        self.verbose = verbose

    # ------------------------------------------------------------------ #
    # PUBLIC API
    # ------------------------------------------------------------------ #
    def score(self, assessment: Dict, cohort_key: Optional[str] = None) -> Dict:
        ca = assessment.get("cropping_analysis", {}) or {}
        pa = assessment.get("performance_analysis", {}) or {}
        wa = assessment.get("weather_analysis", {}) or {}

        lu = self._sub_landuse(ca, pa)
        vg = self._sub_vigor(pa)
        st = self._sub_stability(pa)
        wx = self._sub_weather(wa)
        dc = self._sub_data_confidence(assessment, ca, pa, wa)

        # Additive weighted composite over the 4 substantive sub-indices.
        w = self.weights
        additive = (
            lu["score"] * w.get("landuse", 0) / 100.0
            + vg["score"] * w.get("vigor", 0) / 100.0
            + st["score"] * w.get("stability", 0) / 100.0
            + wx["score"] * w.get("weather", 0) / 100.0
        )

        # Tri-state benefits: positive-only bonus (unknown never penalises).
        benefits = self._benefits_bonus(assessment.get("farmer_benefits"))
        raw_index = _clip(additive + benefits["bonus"])

        # Data-Confidence GATE (multiplicative).
        gate = dc["gate"]

        # Footprint provenance.
        #
        # When a polygon fails geometry QA the collector silently substitutes a
        # circular buffer around the centroid, so the whole assessment may
        # describe land NEAR the parcel rather than the parcel. Observed on
        # farmer 14322905350: QA failed on an area ratio of 3.21 and a 0.15 km
        # buffer was used instead — roughly 7 ha of surrounding fields standing
        # in for a 0.45 ha holding.
        #
        # The land-cover gate already refuses to REJECT on substituted geometry,
        # but a SCORE built on one carried no mark at all. A number measured
        # over the wrong footprint must not read as confidently as one measured
        # over the right one.
        geo_prep = assessment.get("geospatial_prep") or {}
        geometry_source = str(geo_prep.get("geometry_source") or "")
        geometry_substituted = bool(
            geometry_source and geometry_source != "polygon"
        )
        if geometry_substituted:
            penalty = float(
                getattr(PipelineConfig, "GEOMETRY_SUBSTITUTED_GATE_PENALTY", 0.85)
            )
            gate = round(gate * penalty, 3)
            logger.warning(
                "Score computed over a SUBSTITUTED footprint (%s) — confidence "
                "gate discounted to %.3f. The boundary should be re-drawn "
                "before this score is relied on.", geometry_source, gate,
            )

        index_score = round(_clip(raw_index * gate), 1)

        risk_category = self._classify(index_score)
        sub = {"landuse": lu, "vigor": vg, "stability": st,
               "weather": wx, "data_confidence": dc}
        weak = [k for k in ("landuse", "vigor", "stability", "weather")
                if sub[k]["score"] < 50.0]
        reason_codes = self._reason_codes(sub, benefits, gate)

        if self.verbose:
            logger.info("=" * 60)
            logger.info("AGRONOMIC RISK INDEX (%s)", INDEX_VERSION)
            for k in ("landuse", "vigor", "stability", "weather"):
                logger.info("  %-10s %5.1f/100 @ %s%%", k, sub[k]["score"], w.get(k))
            logger.info("  data_confidence %.1f -> gate x%.3f", dc["score"], gate)
            logger.info("  benefits bonus +%.1f", benefits["bonus"])
            logger.info("  >> raw %.1f  x gate %.3f = INDEX %.1f (%s)",
                        raw_index, gate, index_score, risk_category)

        return {
            "index_version": INDEX_VERSION,
            "method": "risk_index_v5_rule_based",
            "index_score": index_score,          # 0-100 agronomic risk index
            "raw_index": round(raw_index, 1),     # before confidence gate
            "risk_category": risk_category,
            "sub_indices": sub,
            "weights": w,
            "confidence_gate": round(gate, 3),
            # What footprint this score was measured over. A consumer must be
            # able to tell a score about the farmer's parcel from a score about
            # the land around it.
            "footprint": {
                "geometry_source": geometry_source or None,
                "geometry_substituted": geometry_substituted,
                "note": (
                    "Measured over a substituted footprint, not the supplied "
                    "boundary — re-draw the boundary before relying on this score."
                    if geometry_substituted else None
                ),
            },
            "benefits": benefits,
            "weak_sub_indices": weak,
            "reason_codes": reason_codes,
            # Explicitly NOT a loan amount / repayment-calibrated score.
            "positioning": "agronomic_risk_index",
            "no_repayment_calibration": True,
            "calibration": {
                "index_version": INDEX_VERSION,
                "weights": w,
                "confidence_gate": round(gate, 3),
                "cohort_key": cohort_key,
                "subindex_inputs": {k: sub[k].get("inputs", {}) for k in sub},
                # Reserved for the future validation / feedback layer (Phase 2):
                "outcome_label": None,
                "pd_estimate": None,
                "calibration_version": None,
            },
            "assessment_date": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------ #
    # SUB-INDEX 1 — LAND-USE & ACTIVITY  (Capacity)
    # ------------------------------------------------------------------ #
    def _sub_landuse(self, ca: Dict, pa: Dict) -> Dict:
        sp = pa.get("seasonal_performance", []) or []
        n_complete = int(pa.get("n_complete_cycles", 0) or
                         len([p for p in sp if not p.get("is_active_cycle")]))
        years = float(ca.get("lookback_years", ca.get("years_analyzed", 3)) or 3)
        cpi = n_complete / max(years, 1e-6)   # cycles per year

        # Perennial branch. For an orchard or plantation "cycles per year" is
        # not a productivity measure — it is ~1 by construction, and the annual
        # intensity ladder below would cap such a parcel at 60/100 no matter how
        # well it is managed. The credit-relevant signals for a perennial are
        # that the canopy is MAINTAINED year-round and stable between years.
        if str(ca.get("cycle_kind", "annual")).lower() == "perennial":
            return self._sub_landuse_perennial(ca, pa, years)

        # Cropping intensity (0-100)
        if cpi >= 2.0:
            intensity = 100.0
        elif cpi >= 1.5:
            intensity = 80.0 + (cpi - 1.5) / 0.5 * 20.0
        elif cpi >= 1.0:
            intensity = 60.0 + (cpi - 1.0) / 0.5 * 20.0
        elif cpi >= 0.5:
            intensity = 35.0 + (cpi - 0.5) / 0.5 * 25.0
        else:
            intensity = _clip(cpi / 0.5 * 35.0)

        # Season coverage / continuity
        total_seasons = max(float(ca.get("total_seasons_analyzed", 0) or 0), 0)
        seasons_with = float(ca.get("seasons_with_crops", 0) or 0)
        seasons_with = max(seasons_with, float(len(sp)))
        coverage = _clip((seasons_with / total_seasons) * 100.0) if total_seasons > 0 else _clip(min(1.0, cpi / 1.5) * 100.0)

        # Fallow penalty.
        #
        # fallow_fraction is only populated when land-utilization ran, and that
        # only runs when at least one cycle was detected. So a parcel with ZERO
        # cycles previously fell through to the 0.0 default and took NO fallow
        # penalty at all — while a genuine farm with two cycles and long gaps
        # was penalised. Dead ground scored better than working land.
        #
        # Zero cycles over the whole lookback IS total fallow; treat it as such
        # rather than as missing data.
        fallow_raw = ca.get("fallow_fraction")
        if fallow_raw is None and n_complete == 0:
            fallow_frac = 1.0
            fallow_basis = "inferred_no_cycles"
        else:
            fallow_frac = float(fallow_raw or 0.0)
            fallow_basis = "measured" if fallow_raw is not None else "default"
        fallow_penalty = _clip(fallow_frac * 100.0, 0, 40)

        score = _clip(0.55 * intensity + 0.30 * coverage + 0.15 * (100.0 - fallow_penalty))
        return {
            "score": round(score, 1),
            "inputs": {"n_complete_cycles": n_complete, "years": years,
                       "cycles_per_year": round(cpi, 2),
                       "season_coverage": round(coverage, 1),
                       "fallow_fraction": round(fallow_frac, 3),
                       "fallow_basis": fallow_basis},
            "drivers": {"intensity": round(intensity, 1), "coverage": round(coverage, 1)},
        }

    def _sub_landuse_perennial(self, ca: Dict, pa: Dict, years: float) -> Dict:
        """
        Land-use sub-index for an orchard / plantation.

        Scored on what actually indicates a well-run perennial:
          * canopy persistence — the planting is maintained, not abandoned;
          * production continuity — a production year observed for each year of
            the lookback, rather than a gap suggesting removal or neglect;
          * inter-annual stability — consistent output between years.

        Deliberately NOT scored on cycles-per-year, which for a perennial is ~1
        by construction and would cap even an exemplary orchard at 60/100.
        """
        sp = pa.get("seasonal_performance", []) or []
        n_years_observed = len(sp)

        # Continuity: did we see a production year for each year of the record?
        expected_years = max(1.0, round(years))
        continuity = _clip((n_years_observed / expected_years) * 100.0)

        # Persistence: how well the canopy is held up.
        peaks = []
        for p in sp:
            pk = (p.get("yield_detail") or {}).get("peak_cvi")
            if pk is not None:
                peaks.append(float(pk))
        persistence = (
            _clip(float(np.mean(peaks)) / 0.75 * 100.0) if peaks else 50.0
        )

        # Inter-annual stability of production.
        yields = [float(p["yield_potential_score"]) for p in sp
                  if p.get("yield_potential_score") is not None]
        if len(yields) >= 2 and np.mean(yields) > 0:
            cv = float(np.std(yields) / np.mean(yields))
            stability = _clip(100.0 - cv * 200.0)
        else:
            # Rule P-1: one observed year is not evidence of stability. Neutral,
            # not good.
            stability = 55.0

        score = _clip(0.40 * persistence + 0.35 * continuity + 0.25 * stability)
        return {
            "score": round(score, 1),
            "inputs": {
                "cycle_kind": "perennial",
                "n_production_years": n_years_observed,
                "years": years,
                "mean_peak_cvi": round(float(np.mean(peaks)), 3) if peaks else None,
                # Not applicable to a perennial; recorded so a consumer that
                # expects it does not silently read a misleading zero.
                "cycles_per_year": None,
                "fallow_fraction": None,
                "fallow_basis": "not_applicable_perennial",
            },
            "drivers": {
                "canopy_persistence": round(persistence, 1),
                "production_continuity": round(continuity, 1),
                "inter_annual_stability": round(stability, 1),
            },
        }

    # ------------------------------------------------------------------ #
    # SUB-INDEX 2 — VIGOR & YIELD-POTENTIAL  (Capacity / Character)
    # ------------------------------------------------------------------ #
    def _sub_vigor(self, pa: Dict) -> Dict:
        sp = pa.get("seasonal_performance", []) or []
        yields = [float(p.get("yield_potential_score")) for p in sp
                  if p.get("yield_potential_score") is not None]
        peaks = []
        n_peer_scored = 0
        for p in sp:
            yd = p.get("yield_detail", {}) or {}
            pk = yd.get("peak_cvi")
            if pk is not None:
                peaks.append(float(pk))
            # Did this cycle's yield score actually come from a peer cohort
            # comparison, or from the internal self-referential fallback? The
            # cohort is cold until cohort_stats is populated, so in practice
            # this is currently 0 — and no output may claim otherwise.
            if str(yd.get("yield_index_basis") or "") == "peer_nirv":
                n_peer_scored += 1
        # With no scored cycles there is no yield evidence. Defaulting to a
        # neutral 50 treated "we saw nothing grow" as an average farm.
        if yields:
            mean_yield = float(np.mean(yields))
        elif pa.get("average_yield_score") is not None:
            mean_yield = float(pa["average_yield_score"])
        else:
            mean_yield = float(getattr(PipelineConfig, "VIGOR_NO_EVIDENCE_SCORE", 25.0))

        # Cap the peak term. peak_cvi/0.75 saturates at 100 for any persistently
        # green surface, so dense forest maxed this out — scoring better on
        # vigor than a real crop. Vegetation denser than a crop canopy is not
        # evidence of a better crop.
        peak_score = _clip(float(np.mean(peaks)) / 0.75 * 100.0) if peaks else mean_yield
        score = _clip(0.70 * mean_yield + 0.30 * peak_score)
        return {
            "score": round(score, 1),
            "inputs": {"mean_yield_potential": round(mean_yield, 1),
                       "mean_peak_cvi": round(float(np.mean(peaks)), 3) if peaks else None,
                       "n_cycles_scored": len(yields),
                       "n_cycles_peer_scored": n_peer_scored,
                       # True only when at least one cycle was scored against a
                       # real peer cohort. Drives the wording of VIGOR_STRONG.
                       "peer_relative": n_peer_scored > 0},
            "drivers": {"yield_potential": round(mean_yield, 1), "peak_quality": round(peak_score, 1)},
        }

    # ------------------------------------------------------------------ #
    # SUB-INDEX 3 — STABILITY & STRESS  (Character)
    # ------------------------------------------------------------------ #
    def _sub_stability(self, pa: Dict) -> Dict:
        sp = pa.get("seasonal_performance", []) or []
        P = PipelineConfig
        ph = float(getattr(P, "CREDIT_ANOMALY_PENALTY_HIGH", 1.8))
        pm = float(getattr(P, "CREDIT_ANOMALY_PENALTY_MEDIUM", 0.55))
        pl = float(getattr(P, "CREDIT_ANOMALY_PENALTY_LOW", 0.15))
        pcap = float(getattr(P, "CREDIT_ANOMALY_PENALTY_MAX", 16.0))

        seen = set()
        n_h = n_m = n_l = 0
        for p in sp:
            season = p.get("season", "")
            n_scenes = int(p.get("n_scenes", 0) or 0)
            for e in p.get("anomaly_events", []) or []:
                key = (season, e.get("type"), e.get("date"), e.get("scene_index"))
                if key in seen:
                    continue
                seen.add(key)
                imp = e.get("impact", "LOW")
                if n_scenes < 6 and imp == "LOW":
                    continue
                if imp == "HIGH":
                    n_h += 1
                elif imp == "MEDIUM":
                    n_m += 1
                else:
                    n_l += 1
        n_seasons = max(1, len(sp))
        raw_pen = n_h * ph + n_m * pm + n_l * pl
        penalty = min(pcap, raw_pen / np.sqrt(n_seasons))
        anomaly_free = _clip(100.0 - penalty)

        # Inter-year variability of vigor (lower CV = more stable).
        vig = [float(p.get("yield_potential_score")) for p in sp
               if p.get("yield_potential_score") is not None]
        if len(vig) >= 2 and np.mean(vig) > 0:
            cv = float(np.std(vig) / np.mean(vig))
            stability_cv = _clip(100.0 - cv * 200.0)  # cv 0->100, cv 0.5->0
        else:
            stability_cv = 60.0  # neutral when too few cycles

        score = _clip(0.6 * anomaly_free + 0.4 * stability_cv)

        # No cycles at all means no evidence of stable cultivation — not
        # evidence of stability.
        #
        # This sub-index rewards the ABSENCE of stress anomalies, and nothing
        # stressful ever happens to a parking lot. With zero cycles the anomaly
        # count is zero and the CV term falls to its neutral 60, so barren land
        # scored 84/100 here — its single strongest pillar. Absence of evidence
        # must not read as evidence of good behaviour (rule P-1).
        no_evidence = len(sp) == 0
        if no_evidence:
            score = float(getattr(PipelineConfig, "STABILITY_NO_EVIDENCE_SCORE", 25.0))

        return {
            "score": round(score, 1),
            "inputs": {"anomalies_high": n_h, "anomalies_medium": n_m, "anomalies_low": n_l,
                       "anomaly_penalty": round(penalty, 2),
                       "n_seasons_observed": len(sp),
                       "no_cultivation_evidence": no_evidence,
                       "vigor_cv": round(cv, 3) if len(vig) >= 2 and np.mean(vig) > 0 else None},
            "drivers": {"anomaly_free": round(anomaly_free, 1), "consistency": round(stability_cv, 1)},
        }

    # ------------------------------------------------------------------ #
    # SUB-INDEX 4 — WEATHER  (Conditions; two-directional)
    # ------------------------------------------------------------------ #
    def _sub_weather(self, wa: Dict) -> Dict:
        risk = float(wa.get("weather_risk_score", 50.0))
        risk_safety = _clip(100.0 - risk)

        exp = (wa.get("forward_exposure") or {}).get("exposure_score")
        exposure_safety = _clip(100.0 - float(exp)) if exp is not None else None

        br = wa.get("backward_resilience") or {}
        resilience = br.get("mean_resilience_score")

        if resilience is not None and exposure_safety is not None:
            score = 0.40 * float(resilience) + 0.30 * exposure_safety + 0.30 * risk_safety
            basis = "resilience+exposure+risk"
        elif exposure_safety is not None:
            score = 0.5 * exposure_safety + 0.5 * risk_safety
            basis = "exposure+risk"
        else:
            score = risk_safety
            basis = "risk_only"
        return {
            "score": round(_clip(score), 1),
            "inputs": {"weather_risk_score": round(risk, 1),
                       "forward_exposure": exp,
                       "backward_resilience": resilience,
                       "basis": basis},
            "drivers": {"resilience": resilience, "exposure_safety": exposure_safety,
                        "risk_safety": round(risk_safety, 1)},
        }

    # ------------------------------------------------------------------ #
    # SUB-INDEX 5 — DATA-CONFIDENCE  (meta -> multiplicative gate)
    # ------------------------------------------------------------------ #
    def _sub_data_confidence(self, assessment: Dict, ca: Dict, pa: Dict, wa: Dict) -> Dict:
        sq = self._signal_quality(assessment)
        valid_frac = float(sq.get("valid_fraction", 0.8))
        mean_q = float(sq.get("mean_bin_quality", valid_frac))
        sar_fallback = float(sq.get("sar_fallback_fraction", 0.0))

        sp = pa.get("seasonal_performance", []) or []
        n_cycles = len(sp)
        history_conf = _clip(min(1.0, n_cycles / 4.0) * 100.0)  # ~4 cycles = full

        weather_ok = 100.0 if wa.get("weather_indicators_present") or wa.get("seasonal_weather") else 60.0

        # SAR fallback is *good* (it filled gaps) up to a point; very heavy
        # reliance means less optical certainty -> mild discount.
        sar_discount = _clip(max(0.0, sar_fallback - 0.5) * 60.0, 0, 30)

        obs_conf = _clip(100.0 * (0.6 * valid_frac + 0.4 * mean_q) - sar_discount)
        score = _clip(0.55 * obs_conf + 0.30 * history_conf + 0.15 * weather_ok)

        gate_min = float(getattr(PipelineConfig, "CONFIDENCE_GATE_MIN", 0.60))
        gate = round(gate_min + (1.0 - gate_min) * (score / 100.0), 3)
        return {
            "score": round(score, 1),
            "gate": float(max(gate_min, min(1.0, gate))),
            "inputs": {"valid_fraction": round(valid_frac, 3),
                       "mean_bin_quality": round(mean_q, 3),
                       "sar_fallback_fraction": round(sar_fallback, 3),
                       "n_cycles": n_cycles},
            "drivers": {"observation": round(obs_conf, 1), "history": round(history_conf, 1)},
        }

    @staticmethod
    def _signal_quality(assessment: Dict) -> Dict:
        """Find the Pillar-1 signal_quality_summary wherever it was stashed."""
        for path in (
            ("satellite_data", "continuous_data", "signal_quality_summary"),
            ("satellite_data", "signal_quality_summary"),
            ("continuous_data", "signal_quality_summary"),
            ("signal_quality_summary",),
        ):
            node: Any = assessment
            ok = True
            for k in path:
                if isinstance(node, dict) and k in node:
                    node = node[k]
                else:
                    ok = False
                    break
            if ok and isinstance(node, dict):
                return node
        return {}

    # ------------------------------------------------------------------ #
    # Tri-state benefits (positive-only bonus)
    # ------------------------------------------------------------------ #
    def _benefits_bonus(self, raw_fb: Any) -> Dict:
        per = float(getattr(PipelineConfig, "BENEFITS_BONUS_PER_FLAG", 2.0))
        cap = float(getattr(PipelineConfig, "BENEFITS_BONUS_MAX", 4.0))
        # Read the RAW tri-state directly — do NOT run normalize_farmer_benefits
        # here, since that coerces unknown (None) -> False and destroys the
        # tri-state distinction we are preserving.
        fb = raw_fb if isinstance(raw_fb, dict) else {}

        def _is_true(v) -> bool:
            if _BENEFITS_HELPERS:
                try:
                    return bool(truthy_benefit_flag(v))
                except Exception:
                    pass
            return v is True

        pm = fb.get("pm_kisan_enrolled")
        ins = fb.get("has_crop_insurance")
        bonus = 0.0
        conferred = []
        if _is_true(pm):
            bonus += per
            conferred.append("PM_KISAN")
        if _is_true(ins):
            bonus += per
            conferred.append("CROP_INSURANCE")
        bonus = min(cap, bonus)
        return {
            "bonus": round(bonus, 2),
            "conferred": conferred,
            "pm_kisan": pm,            # tri-state preserved (True/False/None)
            "has_crop_insurance": ins,
            "note": "positive-only; unknown/absent never penalises the index",
        }

    # ------------------------------------------------------------------ #
    # Classification + reason codes
    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify(score: float) -> str:
        """
        Map score -> risk category (higher score = lower risk). Tolerant of
        config RISK_THRESHOLDS being either scalar cutoffs ({"LOW":70,...}) or
        range tuples ({"LOW":(70,100),...}); falls back to a fixed ladder.
        """
        def _cut(v, default):
            try:
                if isinstance(v, (tuple, list)) and v:
                    return float(min(v))   # lower bound of the band
                return float(v)
            except (TypeError, ValueError):
                return float(default)

        rt = getattr(PipelineConfig, "RISK_THRESHOLDS", None)
        if isinstance(rt, dict) and rt:
            low = _cut(rt.get("LOW"), 70)
            med = _cut(rt.get("MEDIUM"), 50)
            high = _cut(rt.get("HIGH"), 30)
        else:
            low, med, high = 70.0, 50.0, 30.0
        if score >= low:
            return "LOW"
        if score >= med:
            return "MEDIUM"
        if score >= high:
            return "HIGH"
        return "VERY_HIGH"

    def _reason_codes(self, sub: Dict, benefits: Dict, gate: float) -> List[Dict]:
        codes: List[Dict] = []

        def add(code: str, msg: str, polarity: str):
            codes.append({"code": code, "message": msg, "polarity": polarity})

        lu, vg, st, wx, dc = (sub["landuse"], sub["vigor"], sub["stability"],
                              sub["weather"], sub["data_confidence"])

        cpi = lu["inputs"].get("cycles_per_year", 0)
        if lu["score"] >= 70:
            add("LANDUSE_HIGH_INTENSITY", f"Consistent multi-cycle cultivation (~{cpi}/yr).", "positive")
        elif lu["score"] < 50:
            add("LANDUSE_LOW_ACTIVITY", f"Sparse/irregular cultivation (~{cpi}/yr).", "negative")

        if vg["score"] >= 70:
            # Only claim a peer comparison when one actually happened. The peer
            # cohort is cold until cohort_stats is populated, and until then the
            # vigor sub-index is a self-calibrated absolute score — saying
            # "above peers" would overstate the evidence to a lender.
            if vg["inputs"].get("peer_relative"):
                add("VIGOR_STRONG",
                    "Vegetation vigor / yield-potential above cohort peers.", "positive")
            else:
                add("VIGOR_STRONG",
                    "Strong vegetation vigor / yield-potential "
                    "(absolute; no peer cohort available).", "positive")
        elif vg["score"] < 50:
            add("VIGOR_WEAK", "Below-par vigor / yield-potential.", "negative")

        if st["score"] < 50:
            add("STABILITY_STRESS", "Elevated stress anomalies / year-to-year variability.", "negative")
        elif st["score"] >= 75:
            add("STABILITY_STRONG", "Low stress load and consistent performance.", "positive")

        res = wx["inputs"].get("backward_resilience")
        if res is not None and res >= 70:
            add("WEATHER_RESILIENT", "Vegetation held up under past adverse weather.", "positive")
        if wx["score"] < 50:
            add("WEATHER_EXPOSED", "High weather risk / exposure for the location.", "negative")

        if gate < 0.85:
            add("CONFIDENCE_REDUCED",
                f"Score gated by data confidence (x{gate}) — cloud gaps / short history.",
                "caveat")

        if benefits["conferred"]:
            add("BENEFITS_PRESENT", f"Govt-scheme enrolment: {', '.join(benefits['conferred'])}.", "positive")

        return codes


__all__ = ["RiskIndexEngine", "INDEX_VERSION"]
