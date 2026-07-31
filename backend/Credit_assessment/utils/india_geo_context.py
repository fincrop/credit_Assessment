"""
Coarse spatial context for Indian agricultural credit modelling.

Goals:
  - Infer a human-readable eco-region label from lon/lat (and optional LGD codes).
  - Provide mild priors that tune crop-cycle thresholds for cloud-prone plains,
    semi-arid tracts, and hill / western India contrasts — all optional.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# Approximate mainland India AOI (degrees)
_INDIA_BB = {"lat_min": 6.0, "lat_max": 37.5, "lon_min": 67.5, "lon_max": 98.5}


def _inside(lat: Optional[float], lon: Optional[float]) -> bool:
    if lat is None or lon is None:
        return False
    bb = _INDIA_BB
    return bb["lat_min"] <= lat <= bb["lat_max"] and bb["lon_min"] <= lon <= bb["lon_max"]


def _inside_rect(
    lat: float, lon: float, lat0: float, lat1: float, lon0: float, lon1: float
) -> bool:
    return lat0 <= lat <= lat1 and lon0 <= lon <= lon1


def infer_agro_ecoregion(
    latitude: Optional[float],
    longitude: Optional[float],
    state_lgd_code: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Return (label, tuning_profile).

    `tuning_profile` keys (all optional, consumed by CropCycleDetector / CropDetector):
      - ndvi_threshold_delta: additive to NDVI cultivation gate [-0.05, +0.05]
      - cycle_min_peak_floor: lower bound clamp for adaptive min_peak (e.g. 0.13)
      - cycle_baseline_bonus: additive to adaptive min_baseline CVI ceiling
      - cycle_sustained_frac_floor: lowest sustained-growth frac in adaptive pass
      - narrative: short text for dashboards
    """
    lat, lon = latitude, longitude

    hint_from_lgd = _hint_from_state_lgd(state_lgd_code)

    if not _inside(lat, lon):
        meta = {"narrative": "Outside mainland India AOI — default temperate priors"}
        return "EXTRA_INDIA", meta

    # --- bbox-based stacking (later boxes override label only if overlapping) -----
    eco = hint_from_lgd.get("preferred_label") if hint_from_lgd else None

    if _inside_rect(lat, lon, 22.0, 31.8, 74.0, 88.8):
        # Indo-Gangetic & eastern drainage — high humidity, clouds, staggered peaks
        eco = "GANGETIC_AND_EASTERN_PLAINS"

    elif _inside_rect(lat, lon, 15.5, 22.8, 72.8, 78.8):
        # Peninsular Deccan (roughly west-central)
        eco = "DECCAN_PLATEAU"

    elif _inside_rect(lat, lon, 8.0, 20.9, 72.8, 80.8):
        # Peninsula south-central / TN–KA–AP fringe (very coarse)
        eco = "SOUTHERN_PENINSULA"

    elif _inside_rect(lat, lon, 23.5, 32.9, 68.8, 75.9):
        # Arid/semi-arid NW + western Rajasthan belt
        eco = "NORTH_WEST_SEMI_ARID"

    elif _inside_rect(lat, lon, 21.8, 30.9, 75.9, 86.9) and eco is None:
        # Fallback central India wedge
        eco = "CENTRAL_HIGHLAND_MIXED"

    if eco is None:
        eco = "INDIA_UNSPECIFIED_PLAINS"

    profile = dict(_PROFILE_ECO.get(eco, _PROFILE_ECO["INDIA_UNSPECIFIED_PLAINS"]))
    profile["narrative"] = profile.get("narrative", "")
    if hint_from_lgd.get("district_note"):
        profile["lgd_hint"] = hint_from_lgd
    return eco, profile


def _hint_from_state_lgd(code: Optional[str]) -> Dict[str, Any]:
    """Agristack / LGD state-code hints → preferred eco-region label."""
    if code is None or str(code).strip() == "":
        return {}

    sc = str(code).strip()

    # Official LGD state codes → coarse eco labels used by _PROFILE_ECO.
    # Bbox inference still wins when lat/lon fall in a more specific rect.
    _STATE_LGD_TO_ECO: Dict[str, Tuple[str, str]] = {
        # (preferred_label, district_note)
        "2":  ("NORTH_WEST_SEMI_ARID", "Himachal Pradesh"),
        "3":  ("GANGETIC_AND_EASTERN_PLAINS", "Punjab"),
        "5":  ("GANGETIC_AND_EASTERN_PLAINS", "Uttarakhand"),
        "6":  ("GANGETIC_AND_EASTERN_PLAINS", "Haryana"),
        "7":  ("GANGETIC_AND_EASTERN_PLAINS", "Delhi"),
        "8":  ("NORTH_WEST_SEMI_ARID", "Rajasthan"),
        "9":  ("GANGETIC_AND_EASTERN_PLAINS", "Uttar Pradesh"),
        "10": ("GANGETIC_AND_EASTERN_PLAINS", "Bihar"),
        "18": ("GANGETIC_AND_EASTERN_PLAINS", "Assam"),
        "19": ("GANGETIC_AND_EASTERN_PLAINS", "West Bengal"),
        "20": ("GANGETIC_AND_EASTERN_PLAINS", "Jharkhand"),
        "21": ("GANGETIC_AND_EASTERN_PLAINS", "Odisha"),
        "22": ("CENTRAL_HIGHLAND_MIXED", "Chhattisgarh"),
        "23": ("CENTRAL_HIGHLAND_MIXED", "Madhya Pradesh"),
        "24": ("NORTH_WEST_SEMI_ARID", "Gujarat"),
        "27": ("DECCAN_PLATEAU", "Maharashtra"),
        "28": ("SOUTHERN_PENINSULA", "Andhra Pradesh"),
        "29": ("SOUTHERN_PENINSULA", "Karnataka"),
        "32": ("SOUTHERN_PENINSULA", "Kerala"),
        "33": ("SOUTHERN_PENINSULA", "Tamil Nadu"),
        "36": ("SOUTHERN_PENINSULA", "Telangana"),
    }

    mapped = _STATE_LGD_TO_ECO.get(sc)
    if mapped:
        label, note = mapped
        return {
            "state_code": sc,
            "district_note": f"LGD {note} hint",
            "preferred_label": label,
        }

    return {"state_code": sc}


_PROFILE_ECO: Dict[str, Dict[str, Any]] = {
    "GANGETIC_AND_EASTERN_PLAINS": {
        "ndvi_threshold_delta": -0.025,
        "cycle_min_peak_floor": 0.14,
        "cycle_baseline_bonus": 0.06,
        "cycle_sustained_frac_floor": 0.42,
        "narrative": (
            "Humid Indo-Gangetic / eastern plains: cloudy Kharif, mild cycle threshold relaxation"
        ),
    },
    "DECCAN_PLATEAU": {
        "ndvi_threshold_delta": 0.0,
        "cycle_min_peak_floor": 0.15,
        "cycle_baseline_bonus": 0.04,
        "cycle_sustained_frac_floor": 0.45,
        "narrative": "Semi-humid Deccan — bimodal rain, moderate CVI variability",
    },
    "SOUTHERN_PENINSULA": {
        "ndvi_threshold_delta": 0.015,
        "cycle_min_peak_floor": 0.16,
        "cycle_baseline_bonus": 0.03,
        "cycle_sustained_frac_floor": 0.46,
        "narrative": "Southern peninsula — tighter winter peaks, peri-urban mixed pixels common",
    },
    "NORTH_WEST_SEMI_ARID": {
        "ndvi_threshold_delta": 0.02,
        "cycle_min_peak_floor": 0.16,
        "cycle_baseline_bonus": 0.02,
        "cycle_sustained_frac_floor": 0.48,
        "narrative": "Semi-arid NW — sharper seasonal contrast, weaker monsoon trough",
    },
    "CENTRAL_HIGHLAND_MIXED": {
        "ndvi_threshold_delta": 0.0,
        "cycle_min_peak_floor": 0.15,
        "cycle_baseline_bonus": 0.04,
        "cycle_sustained_frac_floor": 0.45,
        "narrative": "Mixed central tract — soybean/cotton/pearl-millet rotations",
    },
    "INDIA_UNSPECIFIED_PLAINS": {
        "ndvi_threshold_delta": -0.01,
        "cycle_min_peak_floor": 0.15,
        "cycle_baseline_bonus": 0.04,
        "cycle_sustained_frac_floor": 0.46,
        "narrative": "Indian mainland fallback — blended priors",
    },
    "EXTRA_INDIA": {
        "ndvi_threshold_delta": 0.0,
        "cycle_min_peak_floor": 0.17,
        "cycle_baseline_bonus": 0.02,
        "cycle_sustained_frac_floor": 0.48,
        "narrative": "Non-Indian coordinates — conservative defaults",
    },
}


def detector_ndvi_threshold(
    base_ndvi: float, latitude: Optional[float], longitude: Optional[float]
) -> float:
    """Apply small regional delta to CropDetector cultivation gate."""
    eco, profile = infer_agro_ecoregion(latitude, longitude)
    delta = float(profile.get("ndvi_threshold_delta", 0.0) or 0.0)
    return float(min(0.45, max(0.07, base_ndvi + delta)))


__all__ = ["infer_agro_ecoregion", "detector_ndvi_threshold"]
