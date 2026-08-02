"""
Farmer-Level Aggregation (Multi-Farm)
=====================================
Combines the per-FARM risk-index results (each produced by the UNCHANGED
single-farm pipeline) into a single FARMER-level agronomic risk index.

Design principles (why this is not just an average):
  * Aggregate PER SUB-INDEX, because they combine differently:
      - vigor / stability / weather / data_confidence -> tenure+area-weighted mean
      - landuse (Capacity) -> weighted mean + a bounded PORTFOLIO bonus
        (total holdings & number of worked plots = more productive capacity)
  * TENURE WEIGHTING (parcel != farmer): a plot the farmer jointly owns or
    cultivates-but-does-not-own must not carry full weight for THIS borrower.
    weight = area_ha * tenure_factor(ownership).
  * DIVERSIFICATION is a NEW positive farmer-level signal that only exists once
    you aggregate: multiple crops / seasons / locations spread risk -> small bonus.
  * The Data-Confidence GATE is re-derived at the farmer level and still gates.

No pipeline internals are touched; this consumes per-farm `risk_assessment`
outputs and farm metadata only.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from config import PipelineConfig
except Exception:  # pragma: no cover - allow standalone import in tests
    class PipelineConfig:  # minimal fallback
        pass

logger = logging.getLogger(__name__)

INDEX_VERSION = "index_v5"
_SUBSTANTIVE = ("landuse", "vigor", "stability", "weather")
_DEFAULT_WEIGHTS = {"landuse": 30.0, "vigor": 25.0, "stability": 20.0, "weather": 25.0}


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


def _wmean(vals: List[float], weights: List[float], default: float = 50.0) -> float:
    num = sum(v * w for v, w in zip(vals, weights))
    den = sum(weights)
    return float(num / den) if den > 0 else default


class FarmerAggregator:
    """Aggregate per-farm risk indices into one farmer-level index."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        cfg_w = getattr(PipelineConfig, "SUBINDEX_WEIGHTS", None)
        w = dict(weights or cfg_w or _DEFAULT_WEIGHTS)
        s = sum(w.values()) or 1.0
        self.weights = {k: round(v * 100.0 / s, 3) for k, v in w.items()}

    # ------------------------------------------------------------------ #
    # Tenure
    # ------------------------------------------------------------------ #
    @staticmethod
    def tenure_factor(farm: Dict) -> float:
        """
        Ownership weight in [0,1] for THIS borrower:
          - explicit ownership_share -> that share
          - joint_owners present, no share -> 1/(1+n_joint)
          - sole owner -> 1.0
          - cultivator but NOT the ROR owner (is_ror_owner is False) -> * lease factor
          - not included_in_assessment -> 0 (stored, not scored)
        """
        if not farm.get("included_in_assessment", True):
            return 0.0
        share = farm.get("ownership_share")
        joint = farm.get("joint_owners") or []
        if share is not None:
            base = float(share)
        elif joint:
            base = 1.0 / (1.0 + len(joint))
        else:
            base = 1.0
        if farm.get("is_ror_owner") is False:
            base *= float(getattr(PipelineConfig, "TENURE_LEASE_FACTOR", 0.35))
        return float(max(0.0, min(1.0, base)))

    # ------------------------------------------------------------------ #
    # Main
    # ------------------------------------------------------------------ #
    def aggregate(self, farm_results: List[Dict], farmer_doc: Dict) -> Dict:
        """
        Args:
            farm_results: per-farm slim results, each with at least:
                farm_id, area_ha, tenure_factor, included (bool),
                sub_indices {name: score}, index_score, risk_category,
                crop (opt), district (opt), season_types (opt list)
            farmer_doc: farm_info doc (farmer_benefits, farms metadata, etc.)
        """
        scored = [
            f for f in farm_results
            if f.get("included") and float(f.get("tenure_factor", 0)) > 0 and f.get("sub_indices")
        ]
        n_total = len(farm_results)
        n_scored = len(scored)

        if n_scored == 0:
            return self._degraded(farm_results, farmer_doc, reason="no_scorable_plots")

        weights = [float(f["area_ha"]) * float(f["tenure_factor"]) for f in scored]

        # --- per-sub-index aggregation ---
        sub: Dict[str, float] = {}
        for k in ("vigor", "stability", "weather", "data_confidence"):
            vals = [float(f["sub_indices"].get(k, 50.0)) for f in scored]
            sub[k] = round(_wmean(vals, weights), 1)

        # Land-use: weighted-mean base + bounded portfolio (capacity) bonus.
        lu_vals = [float(f["sub_indices"].get("landuse", 50.0)) for f in scored]
        lu_base = _wmean(lu_vals, weights)
        total_area = sum(float(f["area_ha"]) * float(f["tenure_factor"]) for f in scored)
        portfolio_bonus = self._portfolio_bonus(n_scored, total_area)
        sub["landuse"] = round(_clip(lu_base + portfolio_bonus), 1)

        # --- compose the 4 substantive sub-indices ---
        additive = sum(sub[k] * self.weights.get(k, 0) / 100.0 for k in _SUBSTANTIVE)

        # --- diversification (positive) ---
        diversification = self._diversification(scored)

        # --- tri-state benefits (positive-only) ---
        benefits = self._benefits_bonus(farmer_doc.get("farmer_benefits"))

        raw_index = _clip(additive + diversification["bonus"] + benefits["bonus"])

        # --- farmer data-confidence gate ---
        gate_min = float(getattr(PipelineConfig, "CONFIDENCE_GATE_MIN", 0.60))
        gate = round(max(gate_min, min(1.0, gate_min + (1.0 - gate_min) * (sub["data_confidence"] / 100.0))), 3)
        index_score = round(_clip(raw_index * gate), 1)
        risk_category = self._classify(index_score)

        weak = [k for k in _SUBSTANTIVE if sub[k] < 50.0]
        reason_codes = self._reason_codes(sub, scored, farm_results, diversification, benefits, gate, weak)

        owned_area = round(sum(float(f["area_ha"]) for f in scored), 3)
        n_owned = sum(1 for f in farm_results if float(f.get("tenure_factor", 0)) > 0 and f.get("included"))
        districts = {f.get("district") for f in scored if f.get("district")}

        return {
            "farmer_id": farmer_doc.get("farmer_id"),
            "index_version": INDEX_VERSION,
            "method": "multi_farm_aggregate_v5",
            "positioning": "agronomic_risk_index",
            "no_repayment_calibration": True,
            "farmer_level": {
                "index_score": index_score,
                "raw_index": round(raw_index, 1),
                "risk_category": risk_category,
                "confidence_gate": gate,
                "sub_indices": sub,
                "weights": self.weights,
                "weak_sub_indices": weak,
                "diversification": diversification,
                "benefits": benefits,
                "portfolio_bonus": round(portfolio_bonus, 2),
                "reason_codes": reason_codes,
                "n_plots_total": n_total,
                "n_plots_scored": n_scored,
                "n_plots_owned": n_owned,
                "total_scored_area_ha": owned_area,
                "weather_shared": len(districts) <= 1,
                "aggregation": {
                    "vigor_stability_weather_dc": "tenure_area_weighted_mean",
                    "landuse": "weighted_mean + portfolio_bonus",
                    "tenure_weighting": True,
                },
            },
            "calibration": {
                "index_version": INDEX_VERSION,
                "weights": self.weights,
                "confidence_gate": gate,
                "per_farm": [
                    {"farm_id": f.get("farm_id"), "area_ha": f.get("area_ha"),
                     "tenure_factor": f.get("tenure_factor"),
                     "index_score": f.get("index_score"),
                     "sub_indices": f.get("sub_indices")}
                    for f in scored
                ],
                "outcome_label": None,
                "pd_estimate": None,
                "calibration_version": None,
            },
            "assessment_date": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _portfolio_bonus(n_scored: int, total_area: float) -> float:
        """
        Small capacity bonus for a larger worked portfolio (more plots / area),
        with diminishing returns. Bounded by PORTFOLIO_BONUS_MAX.
        """
        cap = float(getattr(PipelineConfig, "PORTFOLIO_BONUS_MAX", 8.0))
        # Multi-plot is the genuinely NEW farmer-level capacity signal. A single
        # plot's area is already captured by its own land-use sub-index, so the
        # portfolio bonus is 0 for one plot (no double-counting) and grows with
        # the NUMBER of worked plots, modulated by total worked area.
        plot_f = min(1.0, max(0.0, (n_scored - 1) / 4.0))  # 0 at 1 plot, saturates ~5
        if plot_f <= 0.0:
            return 0.0
        area_f = min(1.0, math.sqrt(max(total_area, 0.0)) / 2.5)
        return cap * plot_f * (0.6 + 0.4 * area_f)

    @staticmethod
    def _diversification(scored: List[Dict]) -> Dict:
        """Crop / geographic / count diversity -> 0-100 score + bounded bonus."""
        crops = {str(f.get("crop")).strip().lower() for f in scored
                 if f.get("crop") and str(f.get("crop")).lower() not in ("", "unclassified", "unknown", "none")}
        districts = {f.get("district") for f in scored if f.get("district")}
        seasons = set()
        for f in scored:
            for s in (f.get("season_types") or []):
                if s:
                    seasons.add(str(s).lower())

        crop_div = min(1.0, max(0.0, (len(crops) - 1) / 2.0))
        geo_div = min(1.0, max(0.0, (len(districts) - 1) / 2.0))
        season_div = min(1.0, max(0.0, (len(seasons) - 1) / 2.0))
        count_f = min(1.0, max(0.0, (len(scored) - 1) / 4.0))

        score = 100.0 * (0.35 * crop_div + 0.25 * geo_div + 0.20 * season_div + 0.20 * count_f)
        cap = float(getattr(PipelineConfig, "DIVERSIFICATION_BONUS_MAX", 5.0))
        bonus = round(cap * (score / 100.0), 2)
        return {
            "score": round(score, 1),
            "bonus": bonus,
            "n_crops": len(crops),
            "n_districts": len(districts),
            "n_seasons": len(seasons),
            "n_plots": len(scored),
            "note": "multi-plot crop/geographic/season spread lowers portfolio risk",
        }

    @staticmethod
    def _benefits_bonus(raw_fb: Any) -> Dict:
        per = float(getattr(PipelineConfig, "BENEFITS_BONUS_PER_FLAG", 2.0))
        cap = float(getattr(PipelineConfig, "BENEFITS_BONUS_MAX", 4.0))
        fb = raw_fb if isinstance(raw_fb, dict) else {}
        pm = fb.get("pm_kisan_enrolled")
        ins = fb.get("has_crop_insurance")
        bonus = 0.0
        conferred = []
        if pm is True:
            bonus += per; conferred.append("PM_KISAN")
        if ins is True:
            bonus += per; conferred.append("CROP_INSURANCE")
        return {"bonus": round(min(cap, bonus), 2), "conferred": conferred,
                "pm_kisan": pm, "has_crop_insurance": ins,
                "note": "positive-only; unknown/absent never penalises"}

    @staticmethod
    def _classify(score: float) -> str:
        def _cut(v, d):
            try:
                if isinstance(v, (tuple, list)) and v:
                    return float(min(v))
                return float(v)
            except (TypeError, ValueError):
                return float(d)
        rt = getattr(PipelineConfig, "RISK_THRESHOLDS", None)
        if isinstance(rt, dict) and rt:
            low, med, high = _cut(rt.get("LOW"), 70), _cut(rt.get("MEDIUM"), 50), _cut(rt.get("HIGH"), 30)
        else:
            low, med, high = 70.0, 50.0, 30.0
        if score >= low: return "LOW"
        if score >= med: return "MEDIUM"
        if score >= high: return "HIGH"
        return "VERY_HIGH"

    def _reason_codes(self, sub, scored, all_results, diversification, benefits, gate, weak) -> List[Dict]:
        codes: List[Dict] = []
        def add(code, msg, pol): codes.append({"code": code, "message": msg, "polarity": pol})

        n = len(scored)
        add("PORTFOLIO_SIZE", f"{n} owned plot(s) scored across the holding.", "neutral")

        # outlier plots (high-risk minority)
        high_risk = [f for f in scored if f.get("risk_category") in ("HIGH", "VERY_HIGH")]
        if high_risk and len(high_risk) < n:
            add("PLOT_RISK_MIX",
                f"{len(high_risk)} of {n} plots carry elevated agronomic risk; "
                "offset by stronger plots in the portfolio.", "caveat")
        elif high_risk and len(high_risk) == n:
            add("ALL_PLOTS_ELEVATED", "All scored plots carry elevated agronomic risk.", "negative")

        if diversification["score"] >= 50:
            add("DIVERSIFIED",
                f"Diversified holding ({diversification['n_crops']} crop(s), "
                f"{diversification['n_districts']} location(s)) spreads risk.", "positive")

        # tenure note
        leased = [f for f in all_results if f.get("is_ror_owner") is False]
        if leased:
            add("TENURE_DISCOUNT",
                f"{len(leased)} non-owned/leased plot(s) down-weighted for this borrower.", "caveat")

        for k in _SUBSTANTIVE:
            if sub[k] >= 70:
                add(f"{k.upper()}_STRONG", f"Portfolio {k.replace('_',' ')} is strong.", "positive")
            elif sub[k] < 50:
                add(f"{k.upper()}_WEAK", f"Portfolio {k.replace('_',' ')} is a weak area.", "negative")

        if benefits["conferred"]:
            add("BENEFITS_PRESENT", f"Govt schemes: {', '.join(benefits['conferred'])}.", "positive")
        if gate < 0.85:
            add("CONFIDENCE_REDUCED", f"Farmer index gated by data confidence (x{gate}).", "caveat")
        return codes

    def _degraded(self, farm_results, farmer_doc, reason: str) -> Dict:
        return {
            "farmer_id": farmer_doc.get("farmer_id"),
            "index_version": INDEX_VERSION,
            "method": "multi_farm_aggregate_v5",
            "farmer_level": {
                "index_score": None,
                "risk_category": "INSUFFICIENT_DATA",
                "confidence_gate": None,
                "sub_indices": {},
                "weights": self.weights,
                "n_plots_total": len(farm_results),
                "n_plots_scored": 0,
                "reason_codes": [{"code": "NO_SCORABLE_PLOTS",
                                  "message": f"No owned/analysable plots ({reason}).",
                                  "polarity": "negative"}],
            },
            "assessment_date": datetime.now().isoformat(),
        }


__all__ = ["FarmerAggregator", "INDEX_VERSION"]
