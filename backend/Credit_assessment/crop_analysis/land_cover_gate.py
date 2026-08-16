"""
Land cover gate
===============
Decides whether a parcel is agricultural land before any credit-relevant
analysis runs.

WHY THIS EXISTS
───────────────
Before this module the pipeline had NO land-cover logic whatsoever. It would
accept any valid polygon drawn anywhere on Earth and emit a risk index for it.
Traced end to end, a parking lot produced index 41 / HIGH / SUCCESS, and dense
forest scored BETTER than a genuine fallow farm — because "stability" rewards
the absence of stress anomalies, and nothing stressful happens to a forest.

A lender cannot use a score that might have been computed over a lake.

APPROACH
────────
Three independent evidence streams, combined:

  1. SPECTRAL  — published index thresholds on RAW, un-normalised values.
     Uses only established, citable indices (NDVI, MNDWI/NDWI, NDBI, BSI, NIRv).
     No threshold here was tuned against our own data, because we have no
     ground truth to tune against; each is documented with the reasoning and
     the surface it corresponds to.

  2. TEMPORAL  — does this parcel behave like farmland over time? Cropland
     greens up and senesces; water, rock and rooftops do not. This stream needs
     no external validation at all, because it is a statement about the
     parcel's own signal. It is also the only stream that separates a FALLOW
     FARM (greens up in some seasons) from PERMANENTLY BARREN ground.

  3. EXTERNAL LULC — a third-party land-cover product (ESA WorldCover, Google
     Dynamic World). Not enabled here: see LANDCOVER_USE_EXTERNAL_LULC. Turning
     it on requires first recording the published per-class accuracy for
     cropland over South Asia, because 10 m global products are weakest exactly
     where our parcels live (sub-hectare fields). Claiming a validated source
     without having read its validation would defeat the point of using one.

Agreement across streams -> act with confidence. Disagreement -> say so.
Encoding the disagreement is the honest output; forcing a verdict is not.

OUTCOMES (decision D-1, confidence-banded)
──────────────────────────────────────────
    reject          high-confidence non-farmland; no score is produced
    flag            borderline; score, but carry the evidence and discount the
                    data-confidence gate
    pass            behaves like farmland

PLANTATION (decision D-6)
─────────────────────────
Orchards, banana and sugarcane are fundable products. They are evergreen, so
they look spectrally like forest and never trough — which is also why the cycle
detector currently returns zero cycles for them and the risk engine scores them
as abandoned land. PLANTATION is therefore a DISTINCT, NON-REJECTING verdict and
must never be collapsed into FOREST or BARREN. Until the perennial cycle model
lands, plantation parcels are held (flagged) rather than scored as fallow.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from config import PipelineConfig

logger = logging.getLogger(__name__)

GATE_VERSION = "land_cover_gate_v1"

# Verdict classes.
CROPLAND = "CROPLAND"
WATER = "WATER"
BUILTUP = "BUILTUP"
BARREN = "BARREN"
FOREST = "FOREST"
PLANTATION = "PLANTATION"
UNKNOWN = "UNKNOWN"

# Outcomes.
PASS = "pass"
FLAG = "flag"
REJECT = "reject"

# Classes that are never scored as cropland, but are not all treated alike.
_NON_AGRICULTURAL = (WATER, BUILTUP, BARREN, FOREST)


def _finite(values: Any) -> np.ndarray:
    """Coerce to a float array containing only finite values."""
    arr = np.asarray(list(values or []), dtype=object)
    out: List[float] = []
    for v in arr:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(f):
            out.append(f)
    return np.array(out, dtype=float)


def _series(continuous: Dict, key: str) -> np.ndarray:
    return _finite(continuous.get(key))


def _pct(arr: np.ndarray, q: float) -> Optional[float]:
    return float(np.percentile(arr, q)) if arr.size else None


def _r(v: Optional[float], nd: int = 4) -> Optional[float]:
    return None if v is None else round(float(v), nd)


# ─────────────────────────────────────────────────────────────────────────────
# Evidence extraction
# ─────────────────────────────────────────────────────────────────────────────

def _build_evidence(continuous: Dict) -> Dict:
    """
    Summarise the RAW index series into the statistics the gate reasons over.

    Deliberately uses raw values, never the normalised composite: the composite
    is built for cycle detection and (under signal_v1) was rescaled per parcel,
    which is precisely what made land-cover discrimination impossible.
    """
    ndvi = _series(continuous, "ndvi_values")
    mndwi = _series(continuous, "mndwi_values")
    ndwi = _series(continuous, "ndwi_values")
    ndbi = _series(continuous, "ndbi_values")
    bsi = _series(continuous, "bsi_values")
    nirv = _series(continuous, "nirv_values")

    # Water index: MNDWI preferred (fewer built-up false positives); NDWI is the
    # fallback for records collected before MNDWI existed.
    water_idx, water_src = (mndwi, "mndwi") if mndwi.size else (ndwi, "ndwi")

    ev: Dict[str, Any] = {
        "n_obs": int(ndvi.size),
        "ndvi_p10": _r(_pct(ndvi, 10)),
        "ndvi_p50": _r(_pct(ndvi, 50)),
        "ndvi_p90": _r(_pct(ndvi, 90)),
        "ndvi_max": _r(float(ndvi.max()) if ndvi.size else None),
        "ndvi_amplitude": _r(
            (float(np.percentile(ndvi, 90)) - float(np.percentile(ndvi, 10)))
            if ndvi.size else None
        ),
        "water_index_source": water_src if water_idx.size else None,
        "water_p50": _r(_pct(water_idx, 50)),
        "water_frac_positive": _r(
            float(np.mean(water_idx > 0)) if water_idx.size else None
        ),
        "ndbi_p50": _r(_pct(ndbi, 50)),
        "ndbi_frac_positive": _r(float(np.mean(ndbi > 0)) if ndbi.size else None),
        "bsi_p50": _r(_pct(bsi, 50)),
        "nirv_p50": _r(_pct(nirv, 50)),
        "nirv_p90": _r(_pct(nirv, 90)),
    }

    # Fraction of the record spent at or below bare-soil NDVI. A cropped field
    # returns to bare ground between cycles; a forest never does; a rooftop
    # never leaves it.
    bare = float(getattr(PipelineConfig, "LANDCOVER_NDVI_BARE_SOIL", 0.20))
    veg = float(getattr(PipelineConfig, "LANDCOVER_NDVI_VEGETATED", 0.45))
    ev["frac_below_bare"] = _r(float(np.mean(ndvi <= bare)) if ndvi.size else None)
    ev["frac_above_vegetated"] = _r(float(np.mean(ndvi >= veg)) if ndvi.size else None)
    return ev


# ─────────────────────────────────────────────────────────────────────────────
# Stream 1 — spectral
# ─────────────────────────────────────────────────────────────────────────────

def _spectral_stream(ev: Dict) -> Tuple[Optional[str], float, List[str]]:
    """
    Classify from published index thresholds. Returns (class, confidence, notes).

    Threshold provenance — each corresponds to a documented surface behaviour,
    not to anything fitted on our data:
      * MNDWI/NDWI > 0 over open water (water absorbs SWIR/NIR far more than it
        reflects green); a majority-positive record means standing water.
      * NDBI > 0 over impervious surfaces (SWIR reflectance exceeds NIR).
      * NDVI persistently below ~0.20 indicates no meaningful canopy at any
        point in the record.
      * NDVI persistently above ~0.55 with little variation indicates permanent
        woody cover rather than a crop, which must green up and senesce.
    """
    notes: List[str] = []
    P = PipelineConfig

    water_frac = ev.get("water_frac_positive")
    water_p50 = ev.get("water_p50")
    ndbi_frac = ev.get("ndbi_frac_positive")
    ndvi_p90 = ev.get("ndvi_p90")
    ndvi_p50 = ev.get("ndvi_p50")
    ndvi_p10 = ev.get("ndvi_p10")
    amp = ev.get("ndvi_amplitude")

    water_dom = float(getattr(P, "LANDCOVER_WATER_FRACTION", 0.60))
    builtup_dom = float(getattr(P, "LANDCOVER_BUILTUP_FRACTION", 0.60))
    bare = float(getattr(P, "LANDCOVER_NDVI_BARE_SOIL", 0.20))
    evergreen = float(getattr(P, "LANDCOVER_NDVI_EVERGREEN", 0.55))
    min_amp = float(getattr(P, "LANDCOVER_MIN_NDVI_AMPLITUDE", 0.18))

    # Water — the strongest single signal we have.
    if water_frac is not None and water_frac >= water_dom and (ndvi_p90 or 0) < bare:
        notes.append(
            f"water index positive in {water_frac:.0%} of observations "
            f"and NDVI never exceeded {bare:.2f}"
        )
        return WATER, min(0.95, 0.55 + water_frac * 0.4), notes

    # Built-up.
    if ndbi_frac is not None and ndbi_frac >= builtup_dom and (ndvi_p90 or 0) < 0.30:
        notes.append(
            f"NDBI positive in {ndbi_frac:.0%} of observations with NDVI peaking "
            f"at only {ndvi_p90:.2f}"
        )
        return BUILTUP, min(0.90, 0.50 + ndbi_frac * 0.4), notes

    # Exposed soil / rock. BSI separates natural bare ground from impervious
    # surfaces, though only loosely — and it does not matter much, because both
    # are rejected. We do not claim finer discrimination than the evidence
    # supports.
    bsi_p50 = ev.get("bsi_p50")
    bare_bsi = float(getattr(P, "LANDCOVER_BSI_BARE", 0.35))
    if (
        bsi_p50 is not None and bsi_p50 >= bare_bsi
        and ndvi_p90 is not None and ndvi_p90 < 0.30
    ):
        notes.append(
            f"strongly positive bare-soil index ({bsi_p50:.2f}) with NDVI "
            f"peaking at {ndvi_p90:.2f}"
        )
        return BARREN, 0.85, notes

    # Never vegetated at any point in a multi-year record.
    if ndvi_p90 is not None and ndvi_p90 < bare:
        notes.append(f"NDVI never exceeded {ndvi_p90:.2f} across the record")
        return BARREN, 0.85, notes

    # Permanently green with little seasonal variation: woody cover. Whether
    # that is natural forest or a plantation is decided by the temporal stream —
    # the spectral evidence alone cannot separate them, and guessing would
    # reject exactly the perennial growers we want to fund.
    if (
        ndvi_p10 is not None and ndvi_p10 >= evergreen
        and amp is not None and amp < min_amp
    ):
        notes.append(
            f"canopy persistently high (NDVI p10 {ndvi_p10:.2f}) with little "
            f"seasonal variation (amplitude {amp:.2f})"
        )
        return FOREST, 0.70, notes

    if ndvi_p50 is not None and ndvi_p50 >= bare:
        notes.append("spectral profile consistent with vegetated land")
        return CROPLAND, 0.60, notes

    notes.append("spectral evidence inconclusive")
    return None, 0.0, notes


# ─────────────────────────────────────────────────────────────────────────────
# Stream 2 — temporal
# ─────────────────────────────────────────────────────────────────────────────

def _temporal_stream(ev: Dict, n_cycles: Optional[int]) -> Tuple[Optional[str], float, List[str]]:
    """
    Does this parcel behave like farmland over time?

    Needs no external validation: it is a statement about the parcel's own
    signal. It is also what separates a fallow farm (which greens up in some
    seasons) from permanently barren ground (which never does) — a distinction
    no single-date spectral test can make.
    """
    notes: List[str] = []
    P = PipelineConfig

    amp = ev.get("ndvi_amplitude")
    frac_veg = ev.get("frac_above_vegetated")
    frac_bare = ev.get("frac_below_bare")

    min_amp = float(getattr(P, "LANDCOVER_MIN_NDVI_AMPLITUDE", 0.18))
    evergreen_bare_max = float(getattr(P, "LANDCOVER_EVERGREEN_BARE_FRACTION", 0.05))

    if amp is None:
        notes.append("no usable series for temporal analysis")
        return None, 0.0, notes

    # Detected cycles are direct evidence of cultivation.
    if n_cycles:
        notes.append(f"{n_cycles} crop cycle(s) detected")
        return CROPLAND, 0.85, notes

    # Seasonal swing without a completed cycle: still cropping behaviour
    # (possibly fallow years, or a cycle the detector could not close).
    if amp >= min_amp and (frac_veg or 0) > 0.05:
        notes.append(
            f"seasonal NDVI swing of {amp:.2f} with vegetated periods — "
            f"consistent with cultivation"
        )
        return CROPLAND, 0.65, notes

    # Persistently green and never bare: perennial woody cover. Distinguishing
    # a plantation from natural forest needs the external LULC stream or a
    # registry crop hint; PLANTATION is the non-rejecting reading, chosen
    # deliberately so an orchard is held for review rather than refused.
    if amp < min_amp and (frac_bare or 0) <= evergreen_bare_max and (frac_veg or 0) > 0.5:
        notes.append(
            f"permanently vegetated (never bare) with amplitude {amp:.2f} — "
            f"perennial woody cover"
        )
        return PLANTATION, 0.55, notes

    # Flat and never vegetated: nothing grows here.
    if amp < min_amp and (frac_veg or 0) < 0.05:
        notes.append(
            f"no seasonal variation (amplitude {amp:.2f}) and never vegetated"
        )
        return BARREN, 0.80, notes

    notes.append("temporal behaviour inconclusive")
    return None, 0.0, notes


# ─────────────────────────────────────────────────────────────────────────────
# Stream 3 — external LULC (interface only; see module docstring)
# ─────────────────────────────────────────────────────────────────────────────

def _external_lulc_stream(external: Optional[Dict]) -> Tuple[Optional[str], float, List[str]]:
    """
    Read a third-party land-cover verdict, if one was supplied.

    Disabled by default. This function does not fetch anything — the caller
    supplies the verdict, so this module stays free of Earth Engine.

    Before enabling, record the product's published per-class accuracy for
    cropland over South Asia in config (rule P-6). Using a "validated source"
    without having read its validation is not evidence.
    """
    if not isinstance(external, dict) or not external.get("class"):
        return None, 0.0, []
    cls = str(external["class"]).upper()
    conf = float(external.get("confidence") or 0.0)
    src = external.get("source", "external_lulc")
    return cls, conf, [f"{src} reports {cls} (confidence {conf:.2f})"]


# ─────────────────────────────────────────────────────────────────────────────
# Combination
# ─────────────────────────────────────────────────────────────────────────────

def _combine(streams: Dict[str, Tuple[Optional[str], float, List[str]]]) -> Tuple[str, float, bool]:
    """
    Combine stream verdicts into (class, confidence, agreement).

    Confidence-weighted vote. Agreement between independent streams raises
    confidence; disagreement lowers it, which is what routes a parcel into the
    FLAG band rather than forcing a reject/pass it cannot support.
    """
    votes: Dict[str, float] = {}
    for _, (cls, conf, _notes) in streams.items():
        if cls is None:
            continue
        votes[cls] = votes.get(cls, 0.0) + conf

    if not votes:
        return UNKNOWN, 0.0, False

    best = max(votes, key=votes.get)
    voting = [c for c, _, _ in streams.values() if c is not None]
    agreement = len(set(voting)) == 1 and len(voting) > 1

    total = sum(votes.values()) or 1.0
    share = votes[best] / total
    strongest = max(
        (conf for cls, conf, _ in streams.values() if cls == best), default=0.0
    )

    confidence = strongest * (0.75 + 0.25 * share)
    if agreement:
        confidence = min(0.98, confidence + 0.10)
    return best, float(np.clip(confidence, 0.0, 1.0)), agreement


def classify_land_cover(
    continuous_data: Optional[Dict],
    *,
    n_cycles: Optional[int] = None,
    field_area_ha: Optional[float] = None,
    location: Optional[Dict] = None,
    agro_profile: Optional[Dict] = None,
    external_lulc: Optional[Dict] = None,
    registry_crop: Optional[str] = None,
) -> Dict:
    """
    Decide whether this parcel is agricultural land.

    Returns a verdict dict which is ALWAYS stamped onto the assessment — on a
    pass as much as a rejection — so a lender can see the evidence either way.

    Keys:
        outcome            'pass' | 'flag' | 'reject'
        class              CROPLAND | WATER | BUILTUP | BARREN | FOREST | PLANTATION | UNKNOWN
        is_cultivable      bool (False only when the outcome is 'reject')
        confidence         0..1
        reason             one human-readable sentence
        evidence           raw index statistics behind the decision
        streams            per-stream verdicts, for audit
        gate_version
    """
    P = PipelineConfig
    continuous_data = continuous_data or {}
    evidence = _build_evidence(continuous_data)

    min_obs = int(getattr(P, "LANDCOVER_MIN_OBSERVATIONS", 8))
    if evidence["n_obs"] < min_obs:
        # Rule P-1: not enough evidence is not the same as evidence of a
        # problem. Do not reject a parcel we could not actually look at.
        return {
            "gate_version": GATE_VERSION,
            "outcome": FLAG,
            "class": UNKNOWN,
            "is_cultivable": True,
            "confidence": 0.0,
            "reason": (
                f"Only {evidence['n_obs']} usable observations "
                f"(minimum {min_obs}) — land cover could not be assessed."
            ),
            "evidence": evidence,
            "streams": {},
            "insufficient_data": True,
        }

    streams = {
        "spectral": _spectral_stream(evidence),
        "temporal": _temporal_stream(evidence, n_cycles),
        "external_lulc": _external_lulc_stream(external_lulc),
    }
    cls, confidence, agreement = _combine(streams)

    # FOREST vs PLANTATION is the one distinction the spectral stream openly
    # cannot make — both are simply "persistently green". Only the temporal
    # stream can offer anything, and even that is weak.
    #
    # The costs of the two errors are not symmetric. Calling an orchard FOREST
    # rejects a farmer we want to fund (decision D-6). Calling a woodlot
    # PLANTATION merely flags it for review, and it will not score well anyway
    # once the perennial model exists. So where the two streams split this way,
    # the non-rejecting reading wins — deliberately, not by accident of weights.
    temporal_cls = streams["temporal"][0]
    if cls == FOREST and temporal_cls == PLANTATION:
        cls = PLANTATION
        confidence = max(confidence, streams["temporal"][1])

    # A registry-declared perennial crop is corroborating context for the
    # plantation reading. It is self-reported and unverified, so it may only
    # ever move a verdict AWAY from rejection, never toward one.
    long_duration = {k.title() for k in (getattr(P, "LONG_DURATION_CROPS", {}) or {})}
    if (
        cls in (FOREST, BARREN)
        and registry_crop
        and registry_crop.strip().title() in long_duration
    ):
        cls = PLANTATION
        confidence = max(confidence, 0.6)
        streams["registry_hint"] = (
            PLANTATION, 0.6,
            [f"registry declares long-duration crop '{registry_crop}'"],
        )

    reject_at = float(getattr(P, "LANDCOVER_REJECT_CONFIDENCE", 0.75))
    flag_at = float(getattr(P, "LANDCOVER_FLAG_CONFIDENCE", 0.45))

    notes: List[str] = []
    for name, (scls, sconf, snotes) in streams.items():
        for note in snotes:
            notes.append(f"{name}: {note}")

    if cls == CROPLAND:
        outcome = PASS if confidence >= flag_at else FLAG
        reason = "Parcel behaves like cultivated farmland."
        if outcome == FLAG:
            reason = "Parcel appears cultivated, but the evidence is weak."
    elif cls == PLANTATION:
        # D-6: fundable, and now scoreable — the detector emits annual
        # production cycles for perennials and the risk engine scores them on
        # canopy persistence rather than cycles-per-year. Still FLAG rather than
        # PASS: the spectral evidence cannot fully separate a managed planting
        # from natural woody cover, and that residual uncertainty should be
        # visible to a lender rather than hidden behind a clean pass.
        outcome = FLAG
        reason = (
            "Parcel appears to be a perennial planting (orchard / plantation). "
            "Scored on canopy persistence and inter-annual stability rather "
            "than cropping cycles."
        )
    elif cls in _NON_AGRICULTURAL:
        outcome = REJECT if confidence >= reject_at else FLAG
        label = {
            WATER: "a water body",
            BUILTUP: "built-up / impervious surface",
            BARREN: "permanently barren land",
            FOREST: "permanent woody cover, not cropland",
        }[cls]
        reason = (
            f"Parcel appears to be {label}."
            if outcome == REJECT
            else f"Parcel may be {label}, but the evidence is not conclusive."
        )
    else:
        outcome = FLAG
        reason = "Land cover could not be determined from the available evidence."

    verdict = {
        "gate_version": GATE_VERSION,
        "outcome": outcome,
        "class": cls,
        "is_cultivable": outcome != REJECT,
        "confidence": round(float(confidence), 3),
        "agreement": agreement,
        "reason": reason,
        "notes": notes,
        "evidence": evidence,
        "streams": {
            k: {"class": v[0], "confidence": round(float(v[1]), 3)}
            for k, v in streams.items()
        },
        "thresholds": {
            "reject_confidence": reject_at,
            "flag_confidence": flag_at,
        },
        "field_area_ha": field_area_ha,
        "location": location,
        "external_lulc_used": bool(external_lulc),
    }

    if outcome == REJECT:
        logger.warning(
            "Land-cover gate REJECTED parcel as %s (confidence %.2f): %s",
            cls, confidence, reason,
        )
    elif outcome == FLAG:
        logger.info(
            "Land-cover gate FLAGGED parcel as %s (confidence %.2f)", cls, confidence
        )
    return verdict


def confidence_gate_penalty(verdict: Optional[Dict]) -> float:
    """
    Multiplier applied to the data-confidence gate for a flagged parcel.

    A parcel we are unsure about must not score as if it were clean cropland.
    Returns 1.0 for a clean pass.
    """
    if not isinstance(verdict, dict) or verdict.get("outcome") != FLAG:
        return 1.0
    return float(getattr(PipelineConfig, "LANDCOVER_FLAG_GATE_PENALTY", 0.85))


__all__ = [
    "classify_land_cover",
    "confidence_gate_penalty",
    "GATE_VERSION",
    "CROPLAND", "WATER", "BUILTUP", "BARREN", "FOREST", "PLANTATION", "UNKNOWN",
    "PASS", "FLAG", "REJECT",
]
