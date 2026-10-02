"""Run one classified parcel through zones, sowing, state, stress, and yield."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Optional

from src.library import TYPED_CONFIDENCE, get_prior, harvest_in_season, harvest_month_window, resolve_window
from src.models import Cue, MonitorRequest, ObservationStack, WeatherDay
from src.nitrogen import score_date as score_nitrogen
from src.progress import measure
from src.sowing import estimate_sowing
from src.state import simulate
from src.stress import score_date as score_stress
from src.weather import apply_irrigation
from src.yield_model import estimate as estimate_yield
from src.yield_model import pixel_uneven
from src.zones import split_harvest, split_zones, zone_index_series, zone_ndmi_series

MODELS = {
    "sowing": "sowing_v1_multisensor",
    "state": "state_v1_weather_radar_optical",
    "stress": "stress_v1_intrazone",
    "yield": "yield_v1_ensemble_tau",
    "nitrogen": "nitrogen_v1_rededge",
}


def run_monitoring(
    request: MonitorRequest,
    stack: Optional[ObservationStack] = None,
    reuse_through: Optional[date] = None,
    previous: Optional[dict] = None,
) -> dict:
    """Score the present season. Pass ``stack`` to skip Earth Engine."""
    as_of = request.as_of or date.today()
    prior = get_prior(request.crop)
    typed = request.confidence >= TYPED_CONFIDENCE and request.crop not in ("", "Unknown", "Others")
    window = resolve_window(
        request.crop, as_of, request.season, prior, request.start, request.end or as_of,
    )
    if stack is None:
        if not request.geometry:
            raise ValueError("A GeoJSON geometry or a pre-built observation stack is required.")
        from src.observe import fetch_stack
        stack = fetch_stack(request.geometry, window.monitor_start, window.monitor_end, request.crop)

    hint = None
    if request.sowing_hint is not None:
        family = "provided" if request.sowing_hint_source == "provided" else "cycle_hint"
        sigma = 3.0 if family == "provided" else 12.0
        weight = 3.0 if family == "provided" else 0.6
        hint = Cue(family, request.sowing_hint, sigma, weight)

    raw_zones = split_zones(stack.pixels, prior)
    sowing_cohorts = sum(1 for zone in raw_zones if zone.kind == "crop")
    zones = []
    for zone in raw_zones:
        if zone.kind != "crop":
            zones.append(zone)
            continue
        zones.extend(split_harvest(zone, prior))
    number = 1
    for zone in zones:
        if zone.kind == "crop":
            zone.zone_id = f"z{number}"
            number += 1
    crop_zones = [z for z in zones if z.kind == "crop"]
    crop_pixels = sum(z.pixel_count for z in crop_zones) or 1
    previous_by_greenup = _index_previous(previous)

    published = []
    for zone in zones:
        if zone.kind != "crop":
            published.append({
                "zone_id": zone.zone_id,
                "kind": "non_crop",
                "pixel_count": zone.pixel_count,
                "area_share": None,
                "note": "Trees, built-up, water, or bare ground. Cut out of the crop boundary and not scored.",
            })
            continue

        local_weather = [replace(w, irrigation=False) for w in stack.weather]
        apply_irrigation(local_weather, zone_ndmi_series(zone), stack.neighborhood_ndmi)
        # One farm-recorded sowing date is used when the field is a single cohort.
        # Staggered zones keep their own sensor posterior.
        use_hint = hint if sowing_cohorts == 1 else None
        sowing = estimate_sowing(zone, local_weather, prior, window, use_hint)

        rows = simulate(
            zone, local_weather, sowing, prior, window.monitor_start, window.monitor_end,
            stack.coarse_ndvi,
        )
        progress = measure(rows, sowing, prior)
        reject = _reject_reason(progress, rows, prior, as_of, window.sow_end)
        if reject:
            published.append({
                "zone_id": zone.zone_id,
                "kind": "excluded",
                "crop": request.crop,
                "pixel_count": zone.pixel_count,
                "area_share": round(zone.pixel_count / crop_pixels, 3),
                "split_reason": "anomaly",
                "note": reject,
            })
            continue
        zone_crop = request.crop
        zone_typed = typed and sowing.known

        intervals = _intervals(
            zone, rows, progress, prior, local_weather, zone_typed,
        )
        if reuse_through is not None:
            intervals = _reuse(zone, intervals, previous_by_greenup, reuse_through)

        uneven = _uneven_on_peak(zone, progress)
        heat_fraction = _heat_fraction(rows, local_weather, prior)
        n_penalty = 0.0
        for item in intervals:
            n = item.get("nitrogen")
            if n:
                n_penalty = max(n_penalty, float(n.get("penalty") or 0))

        span = 21
        if sowing.known and sowing.early and sowing.late:
            span = (sowing.late - sowing.early).days
        uncertainty = rows[-1].uncertainty if rows else 1.0
        yield_doc = None
        if zone_typed:
            yield_doc = estimate_yield(
                prior, progress, rows,
                request.peer_integrals if zone.zone_id == "z1" else None,
                request.district_yield_t_ha,
                n_penalty, heat_fraction, uncertainty, span, uneven,
            )

        published.append({
            "zone_id": zone.zone_id,
            "kind": "crop" if zone_crop == request.crop else "unspecified",
            "crop": zone_crop,
            "pixel_count": zone.pixel_count,
            "area_share": round(zone.pixel_count / crop_pixels, 3),
            "greenup": _iso(zone.greenup),
            "split_reason": zone.split_reason,
            "bbox": _bbox(zone),
            "sowing": _sowing_doc(sowing),
            "harvest": _harvest_doc(progress, prior, window),
            "phenology": _phenology_doc(prior, window),
            "progress": _progress_doc(progress),
            "indices": zone_index_series(zone),
            "irrigation_dates": [w.date.isoformat() for w in local_weather if w.irrigation],
            "uneven_rain_days": sum(1 for w in local_weather if w.uneven_rain),
            "rain_mm": round(sum(w.rain_p50 for w in local_weather), 1),
            "yield": yield_doc,
            "intervals": intervals,
        })

    reference_pool = None
    for zone_doc in published:
        if zone_doc.get("yield"):
            reference_pool = zone_doc["yield"]["reference_pool"]
            break

    from src.layers import build_fields

    by_id = {z.zone_id: z for z in zones}
    fields = build_fields(
        request.geometry,
        [(by_id[doc["zone_id"]], doc) for doc in published if doc.get("zone_id") in by_id],
    )

    for doc in published:
        feature = next(
            (item for item in fields["features"] if (item.get("properties") or {}).get("zone_id") == doc.get("zone_id")),
            None,
        )
        if feature:
            doc["geometry"] = feature["geometry"]

    kept = [doc for doc in published if doc.get("kind") == "crop"]
    accepted = bool(kept)
    exclusion_reason = None
    if not accepted:
        notes = [doc.get("note") for doc in published if doc.get("note")]
        exclusion_reason = notes[0] if notes else (
            f"No {request.crop} canopy remained after bare ground and mismatched pixels were removed."
        )

    return {
        "crop": request.crop,
        "confidence": request.confidence,
        "typed": typed,
        "season": window.season,
        "ecoregion": request.ecoregion,
        "as_of": as_of.isoformat(),
        "lineage": {
            "cluster_id": request.cluster_id,
            "village": request.village,
            "classification_job_id": request.classification_job_id,
            "source_field_id": request.source_field_id,
        },
        "phenology": _phenology_doc(prior, window),
        "window": {
            "sow_start": window.sow_start.isoformat(),
            "sow_end": window.sow_end.isoformat(),
            "monitor_start": window.monitor_start.isoformat(),
            "monitor_end": window.monitor_end.isoformat(),
        },
        "reference_pool": reference_pool,
        "models": dict(MODELS),
        "zone_count": len(crop_zones),
        "accepted": accepted,
        "exclusion_reason": exclusion_reason,
        "cropland_fraction": stack.cropland_fraction,
        "coarse_ndvi_days": len(stack.coarse_ndvi or {}),
        "non_crop_pixels": sum(z.pixel_count for z in zones if z.kind == "non_crop"),
        "zones": published,
        "fields": fields,
        "limits": _limits(typed, prior.name),
    }


def _intervals(zone, rows, progress, prior, weather, zone_typed) -> list[dict]:
    by_wx = {w.date: w for w in weather}
    out = []
    last = rows[-1].date if rows else None
    for i, row in enumerate(rows):
        publish = row.kind in ("optical", "radar") or row.date == last or (row.kind == "inferred" and i % 6 == 0)
        if not publish:
            continue
        if row.kind == "inferred" and row.uncertainty > 0.72 and row.date != last:
            # Keep the gap visible, but do not emit a condition call.
            out.append(_interval_doc(row, None, None, False))
            continue
        stress = None
        nitrogen = None
        if row.kind == "optical":
            stress = score_stress(zone, row.date, progress, prior, by_wx.get(row.date), row)
            if zone_typed:
                nitrogen = score_nitrogen(zone, row.date, row, prior)
        out.append(_interval_doc(row, stress, nitrogen, True))
    return out


def _interval_doc(row, stress, nitrogen, condition_ok: bool) -> dict:
    return {
        "date": row.date.isoformat(),
        "kind": row.kind,
        "cover": round(row.cover, 3),
        "water": round(row.water, 3),
        "biomass_kg_ha": round(row.biomass, 1),
        "uncertainty": round(row.uncertainty, 3),
        "tau": round(row.tau, 3),
        "stage": row.stage,
        "condition_available": condition_ok and row.uncertainty <= 0.72,
        "stress": stress,
        "nitrogen": nitrogen,
    }


def _harvest_doc(progress, prior, window) -> dict:
    months = harvest_month_window(window.sow_start, window.sow_end, prior)
    in_season = harvest_in_season(progress.harvest, months)
    return {
        "observed": progress.harvest is not None,
        "date": _iso(progress.harvest),
        "early": _iso(progress.harvest_early),
        "late": _iso(progress.harvest_late),
        "picks": [day.isoformat() for day in progress.picks],
        "in_season": in_season,
        "note": progress.note,
    }


def _phenology_doc(prior, window) -> dict:
    months = harvest_month_window(window.sow_start, window.sow_end, prior)
    return {
        "crop": prior.name,
        "min_days": prior.min_days,
        "typical_days": prior.typical_days,
        "max_days": prior.max_days,
        "harvest_months": list(months) if months else None,
        "stages": [name for _, _, name in prior.stages],
    }


def _sowing_doc(sowing) -> dict:
    return {
        "known": sowing.known,
        "date": _iso(sowing.date),
        "early": _iso(sowing.early),
        "late": _iso(sowing.late),
        "confidence": sowing.confidence,
        "sources": list(sowing.sources),
        "note": sowing.note,
    }


def _progress_doc(progress) -> dict:
    return {
        "emergence": _iso(progress.emergence),
        "peak": _iso(progress.peak),
        "senescence": _iso(progress.senescence),
        "harvest": _iso(progress.harvest),
        "harvest_window": [_iso(progress.harvest_early), _iso(progress.harvest_late)],
        "tau": round(progress.tau, 3),
        "stage": progress.stage,
        "duration_days": progress.duration_days,
        "duration_outlier": progress.duration_outlier,
        "days_remaining": progress.days_remaining,
        "picks": [d.isoformat() for d in progress.picks],
        "note": progress.note,
    }


def _shape_matches(progress, prior) -> bool:
    """Observed length has to sit inside this crop's own range."""
    if progress.duration_outlier:
        return False
    return True


def _reject_reason(progress, rows, prior, as_of: date, sow_end: date) -> str | None:
    """Drop a zone from the classified crop when the canopy is not that crop.

    A season that is still growing is kept. A bare field, or a flush whose
    length cannot be this crop, is removed and the boundary is redrawn without it.
    """
    if progress.duration_outlier:
        return (
            f"The green season is outside the {prior.min_days}–{prior.max_days} day range "
            f"for {prior.name}, so this part is not kept as {prior.name}."
        )
    cover = max((row.cover for row in rows), default=0.0)
    if progress.emergence is None and cover < prior.emergence_ndvi and as_of > sow_end:
        return f"No {prior.name} canopy. This part stays bare or never greens up like {prior.name}."
    return None


def _uneven_on_peak(zone, progress) -> float:
    if progress.peak is None:
        return 0.0
    values = []
    for track in zone.pixels:
        for row in track.records:
            if row.date == progress.peak and row.ndvi is not None and row.sensor in ("s2", "landsat"):
                values.append(row.ndvi)
    return pixel_uneven(values)


def _heat_fraction(rows, weather, prior) -> float:
    by = {w.date: w for w in weather}
    critical = [r for r in rows if prior.tau_peak * 0.7 <= r.tau <= 0.9]
    if not critical:
        return 0.0
    hot = 0
    for row in critical:
        wx = by.get(row.date)
        if wx is not None and wx.tmax >= prior.heat_tmax and wx.vpd >= 1.6:
            hot += 1
    return hot / len(critical)


def _bbox(zone) -> list[float] | None:
    lons = [p.lon for p in zone.pixels]
    lats = [p.lat for p in zone.pixels]
    if not lons:
        return None
    return [min(lons), min(lats), max(lons), max(lats)]


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _index_previous(previous: Optional[dict]) -> dict:
    out = {}
    if not previous:
        return out
    for zone in previous.get("zones") or []:
        key = zone.get("greenup") or zone.get("zone_id")
        out[key] = {item["date"]: item for item in zone.get("intervals") or []}
    return out


def _reuse(zone, intervals, previous_by_greenup, reuse_through: date) -> list[dict]:
    key = zone.greenup.isoformat() if zone.greenup else zone.zone_id
    old = previous_by_greenup.get(key) or {}
    merged = []
    for item in intervals:
        day = date.fromisoformat(item["date"])
        if day <= reuse_through and item["date"] in old:
            merged.append(old[item["date"]])
        else:
            merged.append(item)
    return merged


def _limits(typed: bool, crop: str) -> list[str]:
    lines = [
        "Stress is scored inside a sowing zone, against that zone's own pixels.",
        "Cloudy days advance a weather-driven state. They are not filled NDVI maps.",
        "Rain is the spread across a buffer. An irrigation flag is a wet-up the grid missed.",
        "Yield is the median of biomass, peak canopy, and district peers on observed progress.",
        "Nitrogen is a red-edge canopy status used to adjust yield.",
    ]
    if not typed:
        lines.append(
            "Crop confidence is below the typed-monitoring gate, so the yield envelope was withheld."
        )
    if crop:
        lines.append("Pest and disease ranking is not part of this version.")
    return lines


def _cluster_summary(farms: list[dict], skipped: list[dict], lineage: dict) -> dict:
    """One cluster view of the farms just scored. Two clusters can be compared from these rows."""
    yields = [float(row["yield_t_ha"]) for row in farms if isinstance(row.get("yield_t_ha"), (int, float))]
    sowing = sorted(row["sowing_date"] for row in farms if row.get("sowing_date"))
    stresses: dict[str, int] = {}
    for row in farms:
        label = row.get("stress") or "Unspecified"
        stresses[label] = stresses.get(label, 0) + 1
    fractions = [
        float(row["cropland_fraction"])
        for row in farms
        if isinstance(row.get("cropland_fraction"), (int, float))
    ]
    return {
        "cluster_id": lineage.get("cluster_id"),
        "village": lineage.get("village"),
        "farm_count": len(farms),
        "excluded_count": len(skipped),
        "mean_yield_t_ha": round(sum(yields) / len(yields), 3) if yields else None,
        "sowing_earliest": sowing[0] if sowing else None,
        "sowing_latest": sowing[-1] if sowing else None,
        "stress_counts": stresses,
        "cropland_fraction": round(sum(fractions) / len(fractions), 3) if fractions else None,
    }


def combine_runs(pieces: list[tuple[dict, Optional[dict]]], *, name: str, season: str, as_of: str, lineage: dict) -> dict:
    """One monitoring document for every field in a classification.

    The map and the summary stay on the job. Full index series stay on each
    parcel so a village of farms does not overflow one document.
    """
    zones = []
    features = []
    farms = []
    skipped = []
    crops = []
    for meta, doc in pieces:
        fid = str(meta.get("field_id") or "")
        crop = str(meta.get("crop") or "")
        if crop and crop not in crops:
            crops.append(crop)
        if not doc:
            skipped.append({"field_id": fid, "crop": crop, "note": "No clear pixels in this field."})
            continue
        if doc.get("accepted") is False:
            skipped.append({
                "field_id": fid,
                "crop": crop,
                "note": doc.get("exclusion_reason") or "Removed. The boundary did not match this crop.",
            })
            continue
        farm_yield = None
        farm_stress = None
        farm_sowing = None
        farm_harvest = None
        for zone in doc.get("zones") or []:
            if zone.get("kind") != "crop":
                if zone.get("kind") == "excluded":
                    skipped.append({
                        "field_id": fid,
                        "crop": crop,
                        "note": zone.get("note") or "Part of the boundary was removed.",
                    })
                continue
            tagged = dict(zone)
            tagged["zone_id"] = f"{fid}-{zone.get('zone_id')}"
            tagged["source_field_id"] = fid
            tagged["parent_geometry"] = meta.get("geometry")
            zones.append(tagged)
            if farm_sowing is None and (zone.get("sowing") or {}).get("date"):
                farm_sowing = zone["sowing"]["date"]
            if farm_harvest is None and (zone.get("harvest") or {}).get("date"):
                farm_harvest = zone["harvest"]["date"]
            if farm_yield is None and (zone.get("yield") or {}).get("t_ha") is not None:
                farm_yield = zone["yield"]["t_ha"]
            for item in reversed(zone.get("intervals") or []):
                stress = (item.get("stress") or {}).get("type")
                if stress:
                    farm_stress = stress
                    break
        for feature in (doc.get("fields") or {}).get("features") or []:
            props = dict(feature.get("properties") or {})
            props["zone_id"] = f"{fid}-{props.get('zone_id')}"
            props["field_id"] = props["zone_id"]
            props["source_field_id"] = fid
            props["crop_name"] = crop or props.get("crop_name")
            features.append({"type": "Feature", "geometry": feature.get("geometry"), "properties": props})
        farms.append({
            "field_id": fid,
            "crop": crop,
            "area_ha": meta.get("area_ha"),
            "confidence": meta.get("confidence"),
            "sowing_date": farm_sowing,
            "harvest_date": farm_harvest,
            "yield_t_ha": farm_yield,
            "stress": farm_stress,
            "cropland_fraction": doc.get("cropland_fraction"),
        })

    slim = []
    for zone in zones:
        yielded = zone.get("yield")
        if isinstance(yielded, dict):
            yielded.pop("band_inputs", None)
        slim.append({
            key: value for key, value in zone.items()
            if key not in ("intervals", "indices", "parent_geometry")
        })
    summary = _cluster_summary(farms, skipped, lineage)
    return {
        "name": name,
        "crop": crops[0] if len(crops) == 1 else "multiple",
        "crops": crops,
        "season": season,
        "as_of": as_of,
        "farm_count": len(farms),
        "excluded_count": len(skipped),
        "cluster_summary": summary,
        "zone_count": len(slim),
        "skipped": skipped,
        "farms": farms,
        "zones": slim,
        "fields": {"type": "FeatureCollection", "features": features},
        "lineage": lineage,
        "parcel_zones": zones,
        "limits": [
            "Satellite collections were read once per crop for the whole selection, not once per farm.",
            "Weather is the shared buffer around those fields.",
            "Each farm keeps its classified crop, sowing split, harvest split, and index series.",
        ],
    }
