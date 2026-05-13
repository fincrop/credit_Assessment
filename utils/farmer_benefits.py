"""
Normalize PM-KISAN / crop-insurance flags from Mongo, job payloads, or Agristack JSON.
Avoids scorer/UI drift when booleans arrive as ints, strings, or alternate key names.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def truthy_benefit_flag(val: Any) -> bool:
    """Interpret loose JSON / BSON values as a boolean enrollment flag."""
    if val is True:
        return True
    if val is False or val is None:
        return False
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "y", "enrolled")
    if isinstance(val, dict):
        return len(val) > 0
    if isinstance(val, (list, tuple, set)):
        return len(val) > 0
    return bool(val)


def normalize_farmer_benefits(src: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """
    Produce canonical keys pm_kisan_enrolled, has_crop_insurance.

    Handles camelCase aliases and structured insurance payloads from ingest.
    """
    if not isinstance(src, dict) or not src:
        return {"pm_kisan_enrolled": False, "has_crop_insurance": False}

    pm = src.get("pm_kisan_enrolled")
    if pm is None:
        pm = src.get("pmKisanEnrolled")

    insurance = src.get("has_crop_insurance")
    if insurance is None:
        insurance = src.get("hasCropInsurance")
    if insurance is None:
        insurance = src.get("crop_insurance")
    if insurance is None:
        insurance = src.get("cropInsuranceDetails")
    if insurance is None:
        insurance = src.get("crop_insurance_details")

    return {
        "pm_kisan_enrolled": truthy_benefit_flag(pm),
        "has_crop_insurance": truthy_benefit_flag(insurance),
    }


def merge_farmer_benefits(
    from_farm_document: Optional[Dict[str, Any]],
    override: Optional[Dict[str, Any]],
) -> Dict[str, bool]:
    """
    Start from the farm record, then apply only keys that are present on the override
    (so partial payloads do not zero out unrelated benefits).
    """
    base = normalize_farmer_benefits(from_farm_document or {})
    if not isinstance(override, dict) or not override:
        return base

    out = dict(base)

    if "pm_kisan_enrolled" in override or "pmKisanEnrolled" in override:
        raw_pm = override.get("pm_kisan_enrolled")
        if raw_pm is None:
            raw_pm = override.get("pmKisanEnrolled")
        out["pm_kisan_enrolled"] = truthy_benefit_flag(raw_pm)

    if (
        "has_crop_insurance" in override
        or "hasCropInsurance" in override
        or "crop_insurance" in override
        or "crop_insurance_details" in override
        or "cropInsuranceDetails" in override
    ):
        raw_ins = override.get("has_crop_insurance")
        if raw_ins is None:
            raw_ins = override.get("hasCropInsurance")
        if raw_ins is None:
            raw_ins = override.get("crop_insurance")
        if raw_ins is None:
            raw_ins = override.get("crop_insurance_details") or override.get("cropInsuranceDetails")
        out["has_crop_insurance"] = truthy_benefit_flag(raw_ins)

    return out
