# Satellite data collector — continuous 3-year Sentinel-2 grid (active implementation).
# Legacy seasonal collector was removed in the technical-debt cleanup; see git history if needed.

"""
Satellite Data Collector (continuous 3-year grid)
=================================================
- Window start: agricultural anchors **15 June** (Kharif) / **15 October** (Rabi), ~3 years back.
- Fixed ``CONTINUOUS_SCENE_INTERVAL_DAYS`` bins; lowest-cloud Sentinel-2 L2A per bin.
- Empty bins → placeholders (NaN indices); Stage 4 imputes.
- STAC cloud cap: looser Jun–Oct (Kharif + monsoon tail), stricter Nov–May (Rabi).
"""

import numpy as np
from datetime import datetime, timedelta, date, timezone
from typing import Any, Dict, List, Tuple, Optional
import logging
import gc
import time
import os
import json
import base64
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import pystac_client
    import planetary_computer
    import rasterio
    from rasterio.windows import from_bounds, Window
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    SATELLITE_AVAILABLE = True
except ImportError:
    SATELLITE_AVAILABLE = False
    print("WARNING: Satellite libraries not available")

try:
    import ee
    GEE_AVAILABLE = True
except ImportError:
    ee = None
    GEE_AVAILABLE = False

# Fixed: Proper indentation and added placeholder import
try:
    from config import REGIONAL_CONFIG
    REGIONAL_CONFIG_AVAILABLE = True
except ImportError:
    REGIONAL_CONFIG_AVAILABLE = False

from config import PipelineConfig
from utils.geometry_utils import GeometryUtils
from utils.data_processing import DataProcessor

logger = logging.getLogger(__name__)


class SatelliteDataCollector:
    """
    Sentinel-2 continuous pipeline: ~3-year window from season anchors (15 Jun / 15 Oct),
    fixed N-day bins, one STAC pick per bin (or NaN placeholder). Downstream imputes gaps.
    """

    # Canonical per-scene index keys emitted by _calculate_indices (STAC) and the
    # GEE reducer read-out. This is the single source of truth for the scene
    # `indices` dict shape — placeholders, consumers and persistence all key off it.
    # Keep ordered: NDVI stats first, then the remaining optical indices.
    INDEX_KEYS: Tuple[str, ...] = (
        'NDVI_mean', 'NDVI_std', 'NDVI_p90',
        'EVI_mean',
        'NDMI_mean',
        'PSRI_mean',
        'NDRE_mean',
        'NDWI_mean',
        'MSAVI2_mean',
        'NIRv_mean',
        'kNDVI_mean',
        'LSWI_mean',
        'GCVI_mean',
    )

    def __init__(self, verbose: bool = True):
        if not SATELLITE_AVAILABLE and not GEE_AVAILABLE:
            raise ImportError(
                "Install either STAC deps (pystac-client planetary-computer rasterio) "
                "or GEE deps (earthengine-api)."
            )

        self.provider = os.environ.get("SATELLITE_PROVIDER", "gee").strip().lower()
        self.use_gee = self.provider == "gee" and GEE_AVAILABLE
        self.gee_ready = False

        self.stac_url = PipelineConfig.STAC_API_URL
        self.catalog = None
        if not self.use_gee and SATELLITE_AVAILABLE:
            self.catalog = pystac_client.Client.open(self.stac_url)

        self.verbose = verbose
        self.band_info = {
            band: info['resolution']
            for band, info in PipelineConfig.SENTINEL2_BANDS.items()
        }
        # Cloud thresholds - season-adaptive (PipelineConfig)
        self.max_cloud_kharif = float(
            getattr(PipelineConfig, "MAX_CLOUD_COVER_KHARIF", 80.0)
        )
        self.max_cloud_rabi = float(
            getattr(PipelineConfig, "MAX_CLOUD_COVER_RABI", 60.0)
        )
        self.max_cloud_continuous = float(
            getattr(PipelineConfig, "MAX_CLOUD_COVER_CONTINUOUS", 70.0)
        )

        # GDAL emits TIFF tile noise at INFO during remote COG partial reads; keep our logs readable.
        logging.getLogger("rasterio._env").setLevel(logging.WARNING)
        logging.getLogger("rasterio").setLevel(logging.WARNING)

        if self.use_gee:
            self.gee_ready = self._initialize_gee()
            if not self.gee_ready:
                logger.warning("GEE requested but initialization failed; falling back to STAC.")
                self.use_gee = False

        # If we started with GEE, `self.catalog` was never opened; open STAC after GEE fallback.
        if not self.use_gee and self.catalog is None and SATELLITE_AVAILABLE:
            self.catalog = pystac_client.Client.open(self.stac_url)

        if not self.use_gee and self.catalog is None:
            raise ImportError("No satellite backend available. Install STAC or GEE dependencies.")

        logger.info("SatelliteDataCollector initialized (provider=%s)", "gee" if self.use_gee else "stac")

    # =========================================================================
    # NEW DEFAULT METHOD: Continuous 3-Year Collection
    # =========================================================================

    def _initialize_gee(self) -> bool:
        if not GEE_AVAILABLE:
            logger.error("earthengine-api is not installed.")
            return False
        try:
            project = os.environ.get("GEE_PROJECT", "").strip() or None
            key_path = os.environ.get("GEE_SA_KEY_PATH", "").strip()
            key_payload: Optional[Dict[str, Any]] = None

            # Render / dashboards often paste the full JSON into GEE_SA_KEY_PATH by mistake.
            # That is not a filesystem path; open() raises OSError (e.g. Errno 36 name too long).
            if key_path and key_path.lstrip().startswith("{"):
                try:
                    key_payload = json.loads(key_path)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "GEE_SA_KEY_PATH looks like JSON but is not valid JSON. "
                        "Use GEE_SERVICE_ACCOUNT_JSON or GEE_SERVICE_ACCOUNT_B64 for inline JSON, "
                        "or set GEE_SA_KEY_PATH to a real path to a .json key file."
                    ) from exc
                _fd, _tmp = tempfile.mkstemp(prefix="gee_sa_", suffix=".json", text=True)
                with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                    json.dump(key_payload, _fh)
                key_path = _tmp
                logger.info(
                    "GEE: GEE_SA_KEY_PATH was inline JSON; using a temporary credentials file"
                )

            b64 = os.environ.get("GEE_SERVICE_ACCOUNT_B64", "").strip()
            raw_json = os.environ.get("GEE_SERVICE_ACCOUNT_JSON", "").strip()
            if b64:
                raw_json = base64.b64decode(b64).decode("utf-8")
            if raw_json:
                try:
                    key_payload = json.loads(raw_json)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "GEE_SERVICE_ACCOUNT_JSON (or GEE_SERVICE_ACCOUNT_B64 decoded) is not valid JSON"
                    ) from exc
                fd, tmp_path = tempfile.mkstemp(prefix="gee_sa_", suffix=".json", text=True)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(key_payload, fh)
                key_path = tmp_path
                logger.info("GEE: using credentials from GEE_SERVICE_ACCOUNT_JSON / GEE_SERVICE_ACCOUNT_B64")

            if key_path:
                service_account = os.environ.get("GEE_SERVICE_ACCOUNT", "").strip() or None
                if not service_account:
                    if key_payload is None:
                        with open(key_path, "r", encoding="utf-8") as fh:
                            key_payload = json.load(fh)
                    service_account = (key_payload.get("client_email") or "").strip()
                if not service_account:
                    raise ValueError("GEE service account email missing. Set GEE_SERVICE_ACCOUNT.")
                credentials = ee.ServiceAccountCredentials(service_account, key_path)
                ee.Initialize(credentials=credentials, project=project)
            else:
                ee.Initialize(project=project)
            logger.info("GEE initialized successfully.")
            return True
        except Exception as e:
            logger.error("Failed to initialize GEE: %s", e)
            return False

    def collect_historical_data(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        field_area_ha: Optional[float] = None,
        geometry: Optional[object] = None,
    ) -> Dict:
        """
        Collect ~3 years of Sentinel-2 as a fixed-interval series (see PipelineConfig
        CONTINUOUS_SCENE_INTERVAL_DAYS). Season-aligned start; missing bins are NaN.
        """
        return self._collect_continuous_approach(
            latitude, longitude, field_area_ha, geometry
        )

    def _collect_continuous_approach(
        self,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
        field_area_ha: Optional[float] = None,
        geometry: Optional[object] = None,
    ) -> Dict:
        """
        NEW: Continuous 3-year data collection with parallel downloading.
        This is now the DEFAULT and recommended approach.
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"CONTINUOUS DATA COLLECTION - 3 YEARS")
        logger.info(f"{'='*70}")

        # ── Determine bounding box ─────────────────────────────────────────
        geospatial_prep: Dict = {
            "snap_logic_version": getattr(
                PipelineConfig, "SNAP_LOGIC_VERSION", "v1_fixed_anchors"
            ),
            "geometry_source": "point",
            "buffer_km_used": None,
            "geometry_qa": None,
        }
        registered_ha = field_area_ha

        if geometry is not None:
            qa = GeometryUtils.validate_farm_geometry(geometry, field_area_ha)
            geospatial_prep["geometry_qa"] = {
                "ok": qa.get("ok"),
                "reason": qa.get("reason"),
                "area_ha": qa.get("area_ha"),
                "ratio": qa.get("ratio"),
            }
            if qa.get("ok") and qa.get("geometry") is not None:
                geom_ok = qa["geometry"]
                bbox = GeometryUtils.calculate_bbox_from_geometry(geom_ok)
                centroid_lat, centroid_lon = GeometryUtils.calculate_centroid_from_geometry(
                    geom_ok
                )
                field_area = float(qa.get("area_ha") or 0.0)
                geospatial_prep["geometry_source"] = "polygon"
                logger.info(
                    "Input: geometry polygon | Centroid (%.4f, %.4f)",
                    centroid_lat,
                    centroid_lon,
                )
            else:
                # Rejected polygon → point + adaptive buffer
                if latitude is None or longitude is None:
                    raise ValueError(
                        f"Geometry rejected ({qa.get('reason')}) and no lat/lon fallback"
                    )
                field_area_ha = field_area_ha or 1.0
                buffer_km = GeometryUtils.adaptive_buffer_km(field_area_ha)
                bbox = GeometryUtils.calculate_bbox_from_point(
                    latitude, longitude, field_area_ha
                )
                centroid_lat, centroid_lon = latitude, longitude
                field_area = field_area_ha
                geospatial_prep["geometry_source"] = "polygon_rejected_fallback_point"
                geospatial_prep["buffer_km_used"] = buffer_km
                logger.warning(
                    "Geometry QA failed (%s) — falling back to point buffer %.3f km",
                    qa.get("reason"),
                    buffer_km,
                )
        elif latitude is not None and longitude is not None:
            field_area_ha = field_area_ha or 1.0
            buffer_km = GeometryUtils.adaptive_buffer_km(field_area_ha)
            bbox = GeometryUtils.calculate_bbox_from_point(
                latitude, longitude, field_area_ha
            )
            centroid_lat, centroid_lon = latitude, longitude
            field_area = field_area_ha
            geospatial_prep["geometry_source"] = "point"
            geospatial_prep["buffer_km_used"] = buffer_km
            logger.info(
                "Input: point (%.4f, %.4f) | Area %.2f ha | buffer %.3f km",
                latitude,
                longitude,
                field_area_ha,
                buffer_km,
            )
        else:
            raise ValueError("Must provide (latitude, longitude) or geometry")

        if not GeometryUtils.validate_bbox(bbox):
            raise ValueError(f"Invalid bounding box: {bbox}")

        # ── Rolling window: season anchors (15 Jun / 15 Oct / 15 Feb), season-aware ──
        today = date.today()
        _lookback = int(getattr(PipelineConfig, "CONTINUOUS_LOOKBACK_YEARS", 3))
        snapped_start = self._snap_to_season_start(today, lookback_years=_lookback)
        start_date = snapped_start.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')

        # Anchor used for audit (Jun 15 = kharif, Oct 15 = rabi)
        if snapped_start.month == 6 and snapped_start.day == 15:
            geospatial_prep["season_anchor_used"] = "kharif_jun15"
        elif snapped_start.month == 10 and snapped_start.day == 15:
            geospatial_prep["season_anchor_used"] = "rabi_oct15"
        else:
            geospatial_prep["season_anchor_used"] = (
                f"custom_{snapped_start.isoformat()}"
            )
        geospatial_prep["window_start"] = start_date
        geospatial_prep["window_end"] = end_date
        geospatial_prep["field_area_ha_registered"] = (
            float(registered_ha) if registered_ha is not None else None
        )
        geospatial_prep["field_area_ha_geometry"] = (
            float(field_area) if geospatial_prep["geometry_source"] == "polygon"
            else None
        )

        total_days_approx = (today - snapped_start).days
        logger.info(
            "Window %s → %s (~%d d); anchor=%s (Kharif=Jun15, Rabi=Oct15)",
            start_date,
            end_date,
            total_days_approx,
            start_date,
        )
        logger.info("BBox: %s", [round(v, 5) for v in bbox])

        if self.use_gee and self.gee_ready:
            result = self._collect_continuous_gee(
                bbox=bbox,
                centroid_lat=centroid_lat,
                centroid_lon=centroid_lon,
                field_area=field_area,
                snapped_start=snapped_start,
                today=today,
                start_date=start_date,
                end_date=end_date,
            )
            result["geospatial_prep"] = geospatial_prep
            return result

        # ── Search for all scenes in 3-year period ─────────────────────────
        split_years = bool(getattr(PipelineConfig, "SATELLITE_STAC_SPLIT_BY_YEAR", True))
        logger.info(
            "\nSearching for satellite scenes..."
            + (" (parallel by calendar year)" if split_years else "")
        )
        if split_years:
            candidates = self._search_scenes_multi_year_parallel(bbox, start_date, end_date)
        else:
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
            mid = date.fromordinal((d0.toordinal() + d1.toordinal()) // 2)
            candidates = self._search_scenes_continuous(
                bbox, start_date, end_date, cloud_lt=self._cloud_lt_for_calendar_day(mid)
            )
        logger.info(f"Found {len(candidates)} candidate scenes (before same-day dedupe)")
        candidates = self._deduplicate_scenes_by_day(candidates)

        if not candidates:
            logger.warning("No satellite data available for the period")
            empty = self._empty_continuous_result(centroid_lat, centroid_lon, field_area)
            empty["geospatial_prep"] = geospatial_prep
            return empty

        interval = max(1, int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)))
        slots = self._build_interval_slots(candidates, snapped_start, today, interval)
        n_bins = len(slots)
        n_pick = sum(1 for _, it in slots if it is not None)
        n_miss = n_bins - n_pick
        logger.info(
            "%d-day bins: total=%d  STAC pick=%d  empty=%d  (unique-day candidates=%d)",
            interval,
            n_bins,
            n_pick,
            n_miss,
            len(candidates),
        )

        to_fetch: List[Tuple[Any, str]] = [
            (it, b0.strftime("%Y-%m-%d")) for b0, it in slots if it is not None
        ]
        if not to_fetch:
            logger.warning("No bins contained STAC items; widen cloud or check AOI")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        logger.info(
            "Reading %d COG scene(s) (one stream at a time; grid date = bin start)...",
            len(to_fetch),
        )
        by_anchor = self._download_anchor_scene_pairs(to_fetch, bbox)

        processed_scenes: List[Dict] = []
        for b0, it in slots:
            ds = b0.strftime("%Y-%m-%d")
            if it is None:
                processed_scenes.append(self._missing_scene_placeholder(b0))
            elif ds in by_anchor:
                processed_scenes.append(by_anchor[ds])
            else:
                processed_scenes.append(self._missing_scene_placeholder(b0))

        n_ok = sum(1 for s in processed_scenes if not s.get("missing"))
        logger.info("Grid merge: slots=%d  downloaded_ok=%d  placeholders=%d", len(slots), n_ok, len(slots) - n_ok)
        if n_ok == 0:
            logger.error("No scenes produced valid indices")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        # ── Extract time-series data ───────────────────────────────────────
        dates = [s['date'] for s in processed_scenes]
        ndvi_values = [s['indices'].get('NDVI_mean', np.nan) for s in processed_scenes]
        evi_values = [s['indices'].get('EVI_mean', np.nan) for s in processed_scenes]
        ndmi_values = [s['indices'].get('NDMI_mean', np.nan) for s in processed_scenes]
        psri_values = [s['indices'].get('PSRI_mean', np.nan) for s in processed_scenes]
        ndre_values = [s['indices'].get('NDRE_mean', np.nan) for s in processed_scenes]
        ndwi_values = [s['indices'].get('NDWI_mean', np.nan) for s in processed_scenes]

        # ── Summary stats ──────────────────────────────────────────────────
        valid_ndvi = [v for v in ndvi_values if not np.isnan(v)]
        if valid_ndvi:
            logger.info(f"\nNDVI statistics:")
            logger.info(f"  Range: {min(valid_ndvi):.3f} - {max(valid_ndvi):.3f}")
            logger.info(f"  Mean: {np.mean(valid_ndvi):.3f}")
            logger.info(f"  Scenes with valid NDVI: {len(valid_ndvi)}/{len(ndvi_values)}")

        total_days = (today - snapped_start).days

        logger.info("%s", "=" * 70)
        logger.info("CONTINUOUS COLLECTION COMPLETE")
        logger.info("  Grid slots: %d  |  interval: %d d  |  valid NDVI: %d", len(processed_scenes), interval, len(valid_ndvi))
        logger.info("  Span: %s → %s  (%d d)", dates[0], dates[-1], total_days)
        logger.info("%s", "=" * 70)

        out = {
            'mode': 'continuous',
            'location': {'latitude': centroid_lat, 'longitude': centroid_lon},
            'bbox': bbox,
            'field_area_ha': field_area,
            'geospatial_prep': geospatial_prep,
            'satellite_provider': 'stac',
            # HONEST LABEL. This path requests only B02/B03/B04/B05/B06/B08/B11
            # (see _download_bands) — it fetches neither SCL nor QA60, so there
            # is NO per-pixel cloud mask here at all. Scene selection uses the
            # item-level eo:cloud_cover property only, which says nothing about
            # whether the parcel itself was clear. It previously claimed
            # 'scl_qa60_v1', which is the GEE path's mask, not this one.
            'cloud_mask_version': 'scene_level_cloud_cover_only',
            'cloud_mask_note': (
                'STAC path: no per-pixel cloud mask. Scenes are filtered on '
                'item-level eo:cloud_cover; residual cloud over the parcel is '
                'not removed. Prefer the GEE path where mask quality matters.'
            ),
            'continuous_data': {
                'scenes': processed_scenes,
                'dates': dates,
                'ndvi_values': ndvi_values,
                'evi_values': evi_values,
                'ndmi_values': ndmi_values,
                'psri_values': psri_values,
                'ndre_values': ndre_values,
                'ndwi_values': ndwi_values,
                'start_date': start_date,
                'end_date': end_date,
                'total_days': total_days,
                'interval_days': interval,
                'scene_count': len(processed_scenes),
                'valid_observations': n_ok,
                'missing_observations': len(processed_scenes) - n_ok,
            },
            'collection_date': datetime.now().isoformat(),
            'summary': {
                'total_scenes': len(processed_scenes),
                'valid_ndvi_scenes': len(valid_ndvi),
                'missing_slots': len(processed_scenes) - n_ok,
                'interval_days': interval,
                'date_range': f"{dates[0]} to {dates[-1]}",
                'collection_mode': 'continuous_3year_grid',
            },
        }
        # Pillar 1: composite VS + smoothing + provenance (STAC = optical-only).
        self._build_composite_signal(
            out["continuous_data"], field_area_ha=field_area, sar_rvi_values=None
        )
        idx_meta = self._indices_availability(processed_scenes)
        out.update(idx_meta)
        return out

    # =========================================================================
    # SEASON-ALIGNED DATE SNAPPING
    # =========================================================================

    @staticmethod
    def _snap_to_season_start(reference_date: date, lookback_years: int = 3) -> date:
        """
        Earliest date for the rolling window: latest agricultural anchor on or before
        ``reference_date - lookback_years`` (approximate calendar span).

        Anchors (Indian cropping calendar) from PipelineConfig.SEASON_SNAP_ANCHORS:
          default Kharif 15 June, Rabi 15 October.
        """
        anchors = getattr(
            PipelineConfig,
            "SEASON_SNAP_ANCHORS",
            ((6, 15), (10, 15), (2, 15)),  # Kharif / Rabi / Zaid (Pillar 1)
        )

        # Build a list of all season-start dates from (lookback_years+1) years
        # back to today, then find the one that is at least lookback_years back
        # and most recent.
        cutoff = reference_date - timedelta(days=lookback_years * 365)

        candidates = []
        for year_offset in range(lookback_years + 2):
            yr = reference_date.year - year_offset
            for month, day in anchors:
                try:
                    candidate = date(yr, month, day)
                    if candidate <= cutoff:
                        candidates.append(candidate)
                except ValueError:
                    pass  # shouldn't happen with fixed month/day

        # Return the most recent candidate that is still at/before the cutoff
        # (to guarantee at least lookback_years of data)
        if candidates:
            return max(candidates)

        # Ultimate fallback: plain arithmetic
        return cutoff

    # =========================================================================
    # GEE continuous collection
    # =========================================================================

    def _collect_continuous_gee(
        self,
        bbox: List[float],
        centroid_lat: float,
        centroid_lon: float,
        field_area: float,
        snapped_start: date,
        today: date,
        start_date: str,
        end_date: str,
    ) -> Dict:
        interval = max(1, int(getattr(PipelineConfig, "CONTINUOUS_SCENE_INTERVAL_DAYS", 10)))
        aoi = ee.Geometry.Rectangle(bbox, proj="EPSG:4326", geodesic=False)
        logger.info("Searching for satellite scenes from GEE...")
        scenes_by_day = self._fetch_gee_scene_stats(aoi, start_date, end_date)

        if not scenes_by_day:
            logger.warning("No GEE scenes available for the period")
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        slots = self._build_date_slots_with_stats(scenes_by_day, snapped_start, today, interval)
        processed_scenes: List[Dict] = []
        for b0, stats in slots:
            if stats is None:
                processed_scenes.append(self._missing_scene_placeholder(b0))
                continue
            ndvi_m = stats.get("NDVI_mean", np.nan)
            if not np.isfinite(self._safe_float(ndvi_m)):
                processed_scenes.append(self._missing_scene_placeholder(b0))
                continue
            processed_scenes.append(
                {
                    "date": b0.strftime("%Y-%m-%d"),
                    "missing": False,
                    "cloud_cover": stats.get("cloud_cover"),
                    "indices": {
                        "NDVI_mean": stats.get("NDVI_mean", np.nan),
                        "NDVI_std": stats.get("NDVI_std", np.nan),
                        "NDVI_p90": stats.get("NDVI_p90", np.nan),
                        "EVI_mean": stats.get("EVI_mean", np.nan),
                        "NDMI_mean": stats.get("NDMI_mean", np.nan),
                        "PSRI_mean": stats.get("PSRI_mean", np.nan),
                        "NDRE_mean": stats.get("NDRE_mean", np.nan),
                        "NDWI_mean": stats.get("NDWI_mean", np.nan),
                        # Pillar 1 additions
                        "MSAVI2_mean": stats.get("MSAVI2_mean", np.nan),
                        "NIRv_mean": stats.get("NIRv_mean", np.nan),
                        "LSWI_mean": stats.get("LSWI_mean", np.nan),
                        "GCVI_mean": stats.get("GCVI_mean", np.nan),
                        "kNDVI_mean": stats.get("kNDVI_mean", np.nan),
                    },
                    "bands_available": ["B02", "B03", "B04", "B05", "B06", "B08", "B11"],
                    "acquisition_date": stats.get("date"),
                }
            )

        n_ok = sum(1 for s in processed_scenes if not s.get("missing"))
        if n_ok == 0:
            return self._empty_continuous_result(centroid_lat, centroid_lon, field_area)

        dates = [s["date"] for s in processed_scenes]
        ndvi_values = [s["indices"].get("NDVI_mean", np.nan) for s in processed_scenes]
        evi_values = [s["indices"].get("EVI_mean", np.nan) for s in processed_scenes]
        ndmi_values = [s["indices"].get("NDMI_mean", np.nan) for s in processed_scenes]
        psri_values = [s["indices"].get("PSRI_mean", np.nan) for s in processed_scenes]
        ndre_values = [s["indices"].get("NDRE_mean", np.nan) for s in processed_scenes]
        ndwi_values = [s["indices"].get("NDWI_mean", np.nan) for s in processed_scenes]
        valid_ndvi = [v for v in ndvi_values if not np.isnan(v)]
        total_days = (today - snapped_start).days

        # Pillar 1: Sentinel-1 RVI aligned to the same bins (guarded; None-safe).
        sar_rvi_values = self._collect_sar_rvi_series(
            aoi, snapped_start, today, interval, n_bins=len(processed_scenes)
        )

        _mask_ver = (
            "csplus_scl_v2"
            if getattr(PipelineConfig, "USE_CLOUD_SCORE_PLUS", True)
            else "scl_qa60_v1"
        )
        result = {
            "mode": "continuous",
            "location": {"latitude": centroid_lat, "longitude": centroid_lon},
            "bbox": bbox,
            "field_area_ha": field_area,
            "satellite_provider": "gee",
            "cloud_mask_version": _mask_ver,
            "continuous_data": {
                "scenes": processed_scenes,
                "dates": dates,
                "ndvi_values": ndvi_values,
                "evi_values": evi_values,
                "ndmi_values": ndmi_values,
                "psri_values": psri_values,
                "ndre_values": ndre_values,
                "ndwi_values": ndwi_values,
                "start_date": start_date,
                "end_date": end_date,
                "total_days": total_days,
                "interval_days": interval,
                "scene_count": len(processed_scenes),
                "valid_observations": n_ok,
                "missing_observations": len(processed_scenes) - n_ok,
            },
            "collection_date": datetime.now().isoformat(),
            "summary": {
                "total_scenes": len(processed_scenes),
                "valid_ndvi_scenes": len(valid_ndvi),
                "missing_slots": len(processed_scenes) - n_ok,
                "interval_days": interval,
                "date_range": f"{dates[0]} to {dates[-1]}",
                "collection_mode": "continuous_3year_grid_gee",
            },
            **self._indices_availability(processed_scenes),
        }

        # Pillar 1: composite VS + optical/SAR fusion + smoothing + provenance.
        self._build_composite_signal(
            result["continuous_data"],
            field_area_ha=field_area,
            sar_rvi_values=sar_rvi_values,
        )
        # Refresh availability (composite adds per-scene VS_mean / RVI_mean).
        result.update(self._indices_availability(processed_scenes))
        return result

    # =========================================================================
    # PILLAR 1 — Sentinel-1 SAR (RVI) collection, aligned to the S2 grid.
    # Fully guarded: ANY failure (no S1 coverage, auth, quota) returns None and
    # the composite falls back to optical + Whittaker gap-fill. Requires a live
    # GEE environment to validate.
    # =========================================================================
    def _collect_sar_rvi_series(
        self,
        aoi,
        snapped_start: date,
        today: date,
        interval: int,
        n_bins: int,
    ) -> Optional[List[float]]:
        if not bool(getattr(PipelineConfig, "SAR_ENABLED", True)):
            return None
        if not (GEE_AVAILABLE and getattr(self, "gee_ready", False) and ee is not None):
            return None
        try:
            collection = str(getattr(PipelineConfig, "SAR_COLLECTION", "COPERNICUS/S1_GRD"))
            orbit = str(getattr(PipelineConfig, "SAR_ORBIT_PREFERENCE", "ASCENDING")).upper()
            start_s = snapped_start.strftime("%Y-%m-%d")
            end_s = today.strftime("%Y-%m-%d")

            coll = (
                ee.ImageCollection(collection)
                .filterBounds(aoi)
                .filterDate(start_s, end_s)
                .filter(ee.Filter.eq("instrumentMode", "IW"))
                .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
                .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            )
            if orbit in ("ASCENDING", "DESCENDING"):
                coll = coll.filter(ee.Filter.eq("orbitProperties_pass", orbit))

            def _sar_feat(img):
                # S1_GRD backscatter is in dB -> convert to linear power for RVI.
                lin = ee.Image(10.0).pow(img.select(["VV", "VH"]).divide(10.0))
                vv = lin.select("VV")
                vh = lin.select("VH")
                rvi = vh.multiply(4.0).divide(vv.add(vh).add(1e-6)).rename("RVI")
                stats = rvi.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=aoi,
                    scale=10,
                    maxPixels=1e8,
                    bestEffort=True,
                )
                return ee.Feature(
                    None,
                    ee.Dictionary(stats).combine(
                        ee.Dictionary({"date": img.date().format("YYYY-MM-dd")})
                    ),
                )

            feats = coll.map(_sar_feat).getInfo().get("features", [])
            by_day: Dict[str, List[float]] = {}
            for f in feats:
                p = f.get("properties", {})
                ds = p.get("date")
                rvi = self._safe_float(p.get("RVI"))
                if ds and np.isfinite(rvi):
                    by_day.setdefault(ds, []).append(float(rvi))
            if not by_day:
                logger.info("SAR: no S1 observations for AOI/period (optical-only).")
                return None

            series: List[float] = []
            for k in range(n_bins):
                b0 = snapped_start + timedelta(days=k * interval)
                b1 = b0 + timedelta(days=interval)
                vals: List[float] = []
                for ds, rs in by_day.items():
                    try:
                        d = datetime.strptime(ds, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    if b0 <= d < b1:
                        vals.extend(rs)
                series.append(float(np.mean(vals)) if vals else np.nan)

            n_ok = int(np.sum(np.isfinite(series)))
            logger.info("SAR: RVI filled %d/%d bins from S1", n_ok, n_bins)
            return series
        except Exception as e:  # never break the pipeline on SAR
            logger.warning("SAR RVI collection skipped (%s)", str(e)[:120])
            return None

    @staticmethod
    def _indices_availability(scenes: List[Dict]) -> Dict[str, Any]:
        """Which index means are finite in ≥1 non-missing scene."""
        names = [
            "NDVI", "EVI", "NDMI", "PSRI", "NDRE", "NDWI",
            # Pillar 1 additions
            "MSAVI2", "NIRv", "LSWI", "GCVI", "kNDVI", "RVI", "VS",
        ]
        available: List[str] = []
        sparse: List[str] = []
        for name in names:
            key = f"{name}_mean"
            n_ok = 0
            for s in scenes or []:
                if s.get("missing"):
                    continue
                v = (s.get("indices") or {}).get(key, np.nan)
                try:
                    if np.isfinite(float(v)):
                        n_ok += 1
                except (TypeError, ValueError):
                    pass
            if n_ok > 0:
                available.append(name)
            else:
                sparse.append(name)
        return {
            "indices_available": available,
            "indices_sparse": sparse,
        }

    # =========================================================================
    # PILLAR 1 — composite vegetation signal (VS), optical/SAR fusion, smoothing
    # Runs identically for GEE and STAC assembled ``continuous_data``. Pure
    # Python over the (unit-tested) DataProcessor helpers, so it is safe and
    # deterministic regardless of the imagery backend.
    # =========================================================================
    @classmethod
    def _build_composite_signal(
        cls,
        continuous_data: Dict,
        field_area_ha: Optional[float] = None,
        sar_rvi_values: Optional[List[float]] = None,
    ) -> Dict:
        """
        Compute per-bin optical quality, build the composite detection signal
        ``VS`` (quality-weighted, SAR-fused), smooth+gap-fill it, and stamp
        per-bin provenance (``signal_source`` / ``bin_quality``). Also exposes
        the Pillar-1 index series and a signal-quality summary for the
        Data-Confidence sub-index. Mutates and returns ``continuous_data``.
        """
        scenes = continuous_data.get("scenes") or []
        n = len(scenes)
        if n == 0:
            return continuous_data

        P = PipelineConfig
        weights = dict(getattr(P, "SIGNAL_COMPOSITE_WEIGHTS",
                               {"kNDVI": 0.5, "EVI": 0.3, "NDMI": 0.2}))
        backbone = getattr(P, "SIGNAL_COMPOSITE_BACKBONE", "kNDVI")

        def _idx_series(nm: str) -> np.ndarray:
            vals = []
            for s in scenes:
                if s.get("missing"):
                    vals.append(np.nan)
                else:
                    vals.append(cls._safe_float((s.get("indices") or {}).get(f"{nm}_mean", np.nan)))
            return np.array(vals, dtype=float)

        # Normalisation mode. "fixed_range" maps each index against its physical
        # bounds, so the same reflectance always yields the same VS value and an
        # absolute threshold means the same thing on every parcel. The legacy
        # "per_parcel_minmax" mode rescaled each series over its own extremes,
        # which guaranteed every parcel spanned ~0-1 regardless of whether
        # anything was growing on it. See PipelineConfig.SIGNAL_VERSION.
        norm_mode = str(getattr(P, "SIGNAL_NORMALIZATION", "fixed_range"))
        ranges = dict(getattr(P, "INDEX_PHYSICAL_RANGES", {}) or {})

        comp_terms: Dict[str, np.ndarray] = {}
        avail_weights: Dict[str, float] = {}
        for nm, w in weights.items():
            arr = _idx_series(nm)
            # Backbone fallback for older backends that did not compute the
            # configured backbone index.
            if nm == backbone and np.isfinite(arr).sum() == 0 and nm != "NDVI":
                arr = _idx_series("NDVI")
                nm_range = "NDVI"
            else:
                nm_range = nm
            if np.isfinite(arr).sum() == 0:
                continue

            if norm_mode == "fixed_range" and nm_range in ranges:
                lo, hi = ranges[nm_range]
                comp_terms[nm] = DataProcessor.normalize_fixed_range(arr, lo, hi)
            else:
                if norm_mode == "fixed_range":
                    logger.warning(
                        "No physical range configured for %s — falling back to "
                        "per-parcel min-max for this term, which is not "
                        "cross-parcel comparable.", nm_range,
                    )
                comp_terms[nm] = DataProcessor.normalize_to_range(arr, 0.0, 1.0)
            avail_weights[nm] = float(w)

        vs_optical = np.full(n, np.nan, dtype=float)
        if comp_terms:
            for i in range(n):
                num = den = 0.0
                for nm, norm in comp_terms.items():
                    v = norm[i]
                    if np.isfinite(v):
                        num += avail_weights[nm] * v
                        den += avail_weights[nm]
                if den > 0:
                    vs_optical[i] = num / den

        # Per-bin optical quality (fusion weight + Data-Confidence input).
        #
        # valid_pixel_fraction is the fraction of AOI pixels that survived
        # masking. Previously this argument was handed `1.0 if the mean is
        # finite else 0.0` — a binary flag, not a fraction — so a bin where 3 of
        # 400 pixels survived scored identically to a fully clear one. Since
        # this feeds the confidence gate, it systematically overstated how much
        # was actually seen.
        #
        # Where the backend reports a real fraction we use it. Where it does not
        # (currently the GEE path, which uses bestEffort and returns no pixel
        # count), we apply an explicit, documented discount rather than assuming
        # the scene was perfect — an unknown must not read as a best case.
        unknown_vpf = float(getattr(P, "BIN_QUALITY_UNKNOWN_VPF", 0.85))
        n_vpf_known = 0
        quality = np.zeros(n, dtype=float)
        for i, s in enumerate(scenes):
            if s.get("missing"):
                continue
            cc = cls._safe_float(s.get("cloud_cover"))
            cloud_prob = (cc / 100.0) if np.isfinite(cc) else None

            reported = cls._safe_float(s.get("valid_pixel_fraction"))
            if np.isfinite(reported):
                vpf = float(np.clip(reported, 0.0, 1.0))
                n_vpf_known += 1
            elif np.isfinite(vs_optical[i]):
                vpf = unknown_vpf
            else:
                vpf = 0.0

            quality[i] = DataProcessor.bin_quality(
                valid_pixel_fraction=vpf,
                cloud_prob=cloud_prob,
                parcel_area_ha=field_area_ha,
                min_area_ha=float(getattr(P, "BIN_QUALITY_MIN_AREA_HA", 0.20)),
            )

        # Optional SAR (RVI) fusion.
        sar_arr = None
        if sar_rvi_values is not None and len(sar_rvi_values) == n:
            sar_arr = np.array([cls._safe_float(v) for v in sar_rvi_values], dtype=float)

        fused, source = DataProcessor.fuse_optical_sar(
            vs_optical, sar_arr, quality,
            min_pairs=int(getattr(P, "SAR_FUSION_MIN_PAIRS", 6)),
        )

        # Smooth + gap-fill (Whittaker default).
        vs_smooth = DataProcessor.smooth_series(
            fused,
            method=getattr(P, "SIGNAL_SMOOTHER", "whittaker"),
            lmbd=float(getattr(P, "WHITTAKER_LAMBDA", 8.0)),
            d=int(getattr(P, "WHITTAKER_DIFF_ORDER", 2)),
            window=int(getattr(P, "SAVGOL_WINDOW", 7)),
            poly=int(getattr(P, "SAVGOL_POLYORDER", 2)),
        )

        labels = getattr(P, "SIGNAL_SOURCE_LABELS",
                         {0: "optical", 1: "fused", 2: "sar", 3: "imputed"})

        for i, s in enumerate(scenes):
            s["bin_quality"] = round(float(quality[i]), 3)
            s["signal_source"] = labels.get(int(source[i]), "optical")
            if np.isfinite(fused[i]):
                s.setdefault("indices", {})["VS_mean"] = round(float(fused[i]), 4)
            if sar_arr is not None and np.isfinite(sar_arr[i]):
                s.setdefault("indices", {})["RVI_mean"] = round(float(sar_arr[i]), 4)

        def _rnd_list(a: np.ndarray) -> List:
            return [None if not np.isfinite(v) else round(float(v), 4) for v in a]

        continuous_data["vs_values"] = _rnd_list(fused)
        continuous_data["vs_smooth"] = [round(float(v), 4) for v in vs_smooth]
        continuous_data["signal_source"] = [labels.get(int(x), "optical") for x in source]
        continuous_data["bin_quality"] = [round(float(q), 3) for q in quality]
        for nm in ("MSAVI2", "NIRv", "LSWI", "GCVI", "kNDVI"):
            continuous_data[f"{nm.lower()}_values"] = _rnd_list(_idx_series(nm))
        if sar_arr is not None:
            continuous_data["rvi_values"] = _rnd_list(sar_arr)

        # Signal-quality summary → Data-Confidence sub-index (Stage 7, later).
        src_counts = {v: int(np.sum(source == k)) for k, v in labels.items()}
        n_valid = int(np.sum(source != 3))
        continuous_data["signal_quality_summary"] = {
            "n_bins": n,
            "n_valid_bins": n_valid,
            "valid_fraction": round(n_valid / max(n, 1), 3),
            "source_counts": src_counts,
            "sar_fallback_fraction": round(src_counts.get("sar", 0) / max(n, 1), 3),
            "mean_bin_quality": round(float(np.mean(quality)), 3),
            "smoother": getattr(P, "SIGNAL_SMOOTHER", "whittaker"),
            # Report the backbone actually used, and the signal definition, so a
            # stored score can be traced to how its signal was built.
            "backbone": backbone if backbone in comp_terms else (
                next(iter(comp_terms), None)
            ),
            "backbone_configured": backbone,
            "normalization": norm_mode,
            "signal_version": getattr(P, "SIGNAL_VERSION", "unknown"),
            "composite_terms": sorted(comp_terms.keys()),
            # How much of mean_bin_quality rests on a measured valid-pixel
            # fraction rather than the assumed default. 0.0 means every bin used
            # the assumption — the quality figure is then an estimate, not a
            # measurement, and consumers should say so.
            "valid_pixel_fraction_known_bins": n_vpf_known,
            "valid_pixel_fraction_known_ratio": round(n_vpf_known / max(n, 1), 3),
            "valid_pixel_fraction_assumed": unknown_vpf,
        }
        return continuous_data

    @staticmethod
    def _safe_float(v: Any) -> float:
        try:
            if v is None:
                return np.nan
            return float(v)
        except (TypeError, ValueError):
            return np.nan

    def _gee_scene_preferred(self, prev: Dict[str, Any], new: Dict[str, Any]) -> bool:
        """Same calendar day: keep usable spectra over masked; then lower cloud %."""
        pn = np.isfinite(self._safe_float(prev.get("NDVI_mean")))
        nn = np.isfinite(self._safe_float(new.get("NDVI_mean")))
        if nn and not pn:
            return True
        if pn and not nn:
            return False
        pc, nc = self._safe_float(prev.get("cloud_cover")), self._safe_float(new.get("cloud_cover"))
        if np.isfinite(nc) and np.isfinite(pc):
            return nc < pc
        return bool(np.isfinite(nc))

    def _build_date_slots_with_stats(
        self,
        scenes_by_day: Dict[str, Dict[str, Any]],
        range_start: date,
        range_end: date,
        interval_days: int,
    ) -> List[Tuple[date, Optional[Dict[str, Any]]]]:
        slots: List[Tuple[date, Optional[Dict[str, Any]]]] = []
        cur = range_start
        while cur <= range_end:
            bin_end = cur + timedelta(days=interval_days)
            candidates: List[Tuple[str, Dict[str, Any]]] = []
            for ds, s in scenes_by_day.items():
                d = datetime.strptime(ds, "%Y-%m-%d").date()
                if cur <= d <= range_end and d < bin_end:
                    candidates.append((ds, s))
            if candidates:
                def _usable_ndvi(s: Dict[str, Any]) -> bool:
                    return np.isfinite(self._safe_float(s.get("NDVI_mean")))

                usable = [c for c in candidates if _usable_ndvi(c[1])]
                pool = usable if usable else candidates
                best = min(pool, key=lambda x: self._safe_float(x[1].get("cloud_cover")))
                slots.append((cur, best[1]))
            else:
                slots.append((cur, None))
            cur = bin_end
        return slots

    def _fetch_gee_scene_stats(
        self,
        aoi,
        start_date: str,
        end_date: str,
    ) -> Dict[str, Dict[str, Any]]:
        if not self.gee_ready:
            return {}

        cloud_cap = float(self.max_cloud_continuous)

        # Pillar 1 — Cloud Score+ (cs_cdf) is added on top of SCL/QA60 when
        # enabled. Kept behind a config flag so it can be disabled instantly if
        # the linked collection is unavailable in a given GEE project.
        use_csplus = bool(getattr(PipelineConfig, "USE_CLOUD_SCORE_PLUS", True))
        cs_band = str(getattr(PipelineConfig, "CLOUD_SCORE_PLUS_BAND", "cs_cdf"))
        cs_thresh = float(getattr(PipelineConfig, "CLOUD_SCORE_PLUS_THRESHOLD", 0.60))
        enable_gcvi = bool(getattr(PipelineConfig, "ENABLE_INDEX_GCVI", True))
        enable_kndvi = bool(getattr(PipelineConfig, "ENABLE_INDEX_KNDVI", True))

        def _scene_to_feature(img):
            # Base mask: QA60 opaque+cirrus clear AND SCL not shadow/cloud/cirrus.
            qa = img.select("QA60")
            cloud_mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
            scl = img.select("SCL")
            scl_mask = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
            mask = cloud_mask.And(scl_mask)
            if use_csplus:
                # cs_cdf is linked onto the image (see linkCollection below).
                mask = mask.And(img.select(cs_band).gte(cs_thresh))
            masked = img.updateMask(mask).divide(10000)

            NIR = masked.select("B8"); RED = masked.select("B4"); BLUE = masked.select("B2")
            GREEN = masked.select("B3"); SWIR1 = masked.select("B11")
            RE1 = masked.select("B5"); RE2 = masked.select("B6")

            ndvi = masked.normalizedDifference(["B8", "B4"]).rename("NDVI")
            evi = masked.expression(
                "2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))",
                {"NIR": NIR, "RED": RED, "BLUE": BLUE},
            ).rename("EVI")
            ndmi = masked.normalizedDifference(["B8", "B11"]).rename("NDMI")
            ndwi = masked.normalizedDifference(["B3", "B8"]).rename("NDWI")
            ndre = masked.normalizedDifference(["B8", "B5"]).rename("NDRE")
            psri = masked.expression(
                "(RED - BLUE) / (RE2 + 1e-6)",
                {"RED": RED, "BLUE": BLUE, "RE2": RE2},
            ).rename("PSRI")
            # LSWI shares NDMI's formula (NIR,SWIR1) but is carried with water semantics.
            lswi = masked.normalizedDifference(["B8", "B11"]).rename("LSWI")
            msavi2 = masked.expression(
                "(2 * NIR + 1 - sqrt((2 * NIR + 1) ** 2 - 8 * (NIR - RED))) / 2",
                {"NIR": NIR, "RED": RED},
            ).rename("MSAVI2")
            nirv = ndvi.multiply(NIR).rename("NIRv")

            band_list = [ndvi, evi, ndmi, ndwi, ndre, psri, lswi, msavi2, nirv]
            if enable_gcvi:
                band_list.append(NIR.divide(GREEN.add(1e-6)).subtract(1).rename("GCVI"))
            if enable_kndvi:
                band_list.append(ndvi.pow(2).tanh().rename("kNDVI"))

            indices = ee.Image.cat(band_list)

            # Single reduceRegion (more bands, still ONE aggregation call — this
            # does not add "concurrent aggregations", which come from many calls).
            stats = indices.reduceRegion(
                reducer=ee.Reducer.mean().combine(
                    ee.Reducer.stdDev(), None, True
                ).combine(
                    ee.Reducer.percentile([90]), None, True
                ),
                geometry=aoi,
                scale=10,
                maxPixels=1e8,
                bestEffort=True
            )

            props = ee.Dictionary(stats).combine(
                ee.Dictionary({
                    "date": img.date().format("YYYY-MM-dd"),
                    "cloud_cover": img.get("CLOUDY_PIXEL_PERCENTAGE"),
                })
            )
            return ee.Feature(None, props)

        try:
            features = []
            d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
            d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
            
            # Sequential year-by-year processing to avoid GEE rate limits
            years_to_process = list(range(d0.year, d1.year + 1))
            logger.info(f"Processing years sequentially: {years_to_process}")
            
            for year in years_to_process:
                year_start = max(d0, date(year, 1, 1))
                year_end = min(d1, date(year, 12, 31))
                
                logger.info(f"Processing year {year}: {year_start} to {year_end}")
                
                # Retry logic for each year's data with much longer delays
                max_retries = 2  # Reduce retries to avoid overwhelming GEE
                for attempt in range(max_retries):
                    try:
                        # Configurable pause between years (default 0 — was hardcoded 10s).
                        # Set SATELLITE_INTER_YEAR_PAUSE_SEC if GEE rate-limits.
                        if year > d0.year and attempt == 0:
                            inter_year_delay = float(
                                getattr(PipelineConfig, "SATELLITE_INTER_YEAR_PAUSE_SEC", 0) or 0
                            )
                            if inter_year_delay > 0:
                                logger.info(
                                    f"Waiting {inter_year_delay}s between year {year-1} and {year}"
                                )
                                time.sleep(inter_year_delay)
                        
                        if attempt > 0:
                            delay = 15.0 * (2 ** attempt)  # Much longer delays for retries
                            logger.warning(f"Year {year} GEE retry {attempt + 1}/{max_retries}, waiting {delay:.1f}s")
                            time.sleep(delay)
                        
                        # Simplify: limit collection to fewer scenes to reduce aggregations
                        year_coll = (
                            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                            .filterBounds(aoi)
                            .filterDate(year_start.strftime("%Y-%m-%d"), year_end.strftime("%Y-%m-%d"))
                            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_cap))
                            .limit(50)  # Limit scenes to reduce processing load
                        )
                        # Pillar 1: link Cloud Score+ (cs_cdf) so _scene_to_feature can
                        # mask thin/haze clouds SCL misses. Guarded: if the linked
                        # collection is unavailable, fall back to SCL/QA60 only.
                        if use_csplus:
                            try:
                                cs_plus = ee.ImageCollection(
                                    "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
                                )
                                year_coll = year_coll.linkCollection(cs_plus, [cs_band])
                            except Exception as _cs_e:
                                logger.warning(
                                    "Cloud Score+ link failed (%s); using SCL/QA60 only", _cs_e
                                )
                                # Disable for the closure (read lazily at getInfo).
                                use_csplus = False
                        
                        year_features = year_coll.map(_scene_to_feature).getInfo().get("features", [])
                        features.extend(year_features)
                        logger.info(f"Year {year}: extracted {len(year_features)} scenes")
                        break  # Success, move to next year
                        
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "concurrent aggregations" in error_msg or "too many concurrent" in error_msg:
                            if attempt < max_retries - 1:
                                logger.warning(f"Year {year} concurrent aggregation error (attempt {attempt + 1}/{max_retries}): {e}")
                                continue
                            else:
                                logger.error(f"Year {year} failed after {max_retries} attempts: {e}")
                                raise e
                        elif "rate limit" in error_msg or "quota" in error_msg:
                            if attempt < max_retries - 1:
                                delay = 10.0 * (2 ** attempt)  # Aggressive delay for rate limits
                                logger.warning(f"Year {year} rate limit (attempt {attempt + 1}/{max_retries}), waiting {delay:.1f}s")
                                time.sleep(delay)
                                continue
                            else:
                                logger.error(f"Year {year} rate limit exceeded after {max_retries} attempts: {e}")
                                raise e
                        else:
                            # Non-rate-limit error, re-raise immediately
                            logger.error(f"Year {year} scene extraction failed: {e}")
                            raise e
                
        except Exception as e:
            logger.error("GEE scene extraction failed: %s", e)
            return {}

        by_day: Dict[str, Dict[str, Any]] = {}
        logger.info("GEE: merged %d raw features into daily stats", len(features))

        for f in features:
            p = f.get("properties", {})
            ds = p.get("date")
            if not ds:
                continue
            
            ndvi = self._safe_float(p.get("NDVI_mean"))
            evi = self._safe_float(p.get("EVI_mean"))
            ndmi = self._safe_float(p.get("NDMI_mean"))
            # Fully masked / invalid reduceRegion — do not store as a real observation.
            if not (np.isfinite(ndvi) or np.isfinite(evi)):
                continue
            
            if len(by_day) < 5 and self.verbose:
                logger.debug("GEE scene %s: NDVI=%s EVI=%s NDMI=%s", ds, ndvi, evi, ndmi)
            
            scene_stats = {
                "date": ds,
                "cloud_cover": self._safe_float(p.get("cloud_cover")),
                "NDVI_mean": ndvi,
                "NDVI_std": self._safe_float(p.get("NDVI_stdDev")),
                "NDVI_p90": self._safe_float(p.get("NDVI_p90")),
                "EVI_mean": evi,
                "NDMI_mean": ndmi,
                "PSRI_mean": self._safe_float(p.get("PSRI_mean")),
                "NDRE_mean": self._safe_float(p.get("NDRE_mean")),
                "NDWI_mean": self._safe_float(p.get("NDWI_mean")),
                # Pillar 1 additions (present only when computed server-side)
                "MSAVI2_mean": self._safe_float(p.get("MSAVI2_mean")),
                "NIRv_mean": self._safe_float(p.get("NIRv_mean")),
                "LSWI_mean": self._safe_float(p.get("LSWI_mean")),
                "GCVI_mean": self._safe_float(p.get("GCVI_mean")),
                "kNDVI_mean": self._safe_float(p.get("kNDVI_mean")),
            }
            prev = by_day.get(ds)
            if prev is None or self._gee_scene_preferred(prev, scene_stats):
                by_day[ds] = scene_stats
        return by_day

    # =========================================================================
    # STAC search (year-split) + per-anchor downloads: _download_anchor_scene_pairs
    # =========================================================================

    @staticmethod
    def _yearly_date_segments(d0: date, d1: date) -> List[Tuple[date, date]]:
        """Split [d0, d1] into contiguous calendar-year segments."""
        if d0 > d1:
            return []
        out: List[Tuple[date, date]] = []
        cur = d0
        while cur <= d1:
            y = cur.year
            end_of_year = date(y, 12, 31)
            seg_end = min(end_of_year, d1)
            out.append((cur, seg_end))
            if seg_end >= d1:
                break
            cur = date(y + 1, 1, 1)
        return out

    def _search_scenes_multi_year_parallel(
        self,
        bbox: List[float],
        start_date: str,
        end_date: str,
    ) -> List:
        """
        Run one STAC query per calendar-year segment. Queries run in parallel
        (bounded by SATELLITE_STAC_YEAR_SEARCH_WORKERS) so long lookbacks do not
        rely on a single huge search response.
        """
        d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
        d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
        windows = self._yearly_date_segments(d0, d1)
        if not windows:
            return []

        logger.info(
            "STAC year segments: "
            + ", ".join(f"{a.isoformat()}→{b.isoformat()}" for a, b in windows)
        )

        def fetch_segment(seg: Tuple[date, date]) -> List:
            a, b = seg
            mid = date.fromordinal((a.toordinal() + b.toordinal()) // 2)
            cloud = self._cloud_lt_for_calendar_day(mid)
            return self._search_scenes_continuous(
                bbox,
                a.strftime("%Y-%m-%d"),
                b.strftime("%Y-%m-%d"),
                cloud_lt=cloud,
            )

        if len(windows) == 1:
            return fetch_segment(windows[0])

        nw = int(getattr(PipelineConfig, "SATELLITE_STAC_YEAR_SEARCH_WORKERS", 4))
        nw = max(1, min(nw, len(windows)))

        merged: List = []
        if nw == 1:
            for w in windows:
                merged.extend(fetch_segment(w))
        else:
            with ThreadPoolExecutor(max_workers=nw) as ex:
                futures = [ex.submit(fetch_segment, w) for w in windows]
                for fut in as_completed(futures):
                    try:
                        merged.extend(fut.result())
                    except Exception as e:
                        logger.warning(f"Parallel STAC segment failed: {e}")

        merged.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
        return merged

    def _cloud_lt_for_calendar_day(self, d: date) -> float:
        """STAC eo:cloud_cover upper bound: looser Jun–Oct (Kharif + monsoon tail)."""
        if d.month in (6, 7, 8, 9, 10):
            return float(self.max_cloud_kharif)
        return float(self.max_cloud_rabi)

    def _download_anchor_scene_pairs(
        self,
        pairs: List[Tuple[Any, str]],
        bbox: List[float],
    ) -> Dict[str, Dict]:
        """
        Download/process each (STAC item, grid_anchor_date) preserving anchor keys.
        One scene at a time; batches log lines by anchor year.
        """
        from collections import defaultdict

        if not pairs:
            return {}
        by_y: Dict[int, List[Tuple[Any, str]]] = defaultdict(list)
        for item, anchor_ds in pairs:
            by_y[int(anchor_ds[:4])].append((item, anchor_ds))
        out: Dict[str, Dict] = {}
        pause = float(getattr(PipelineConfig, "SATELLITE_INTER_YEAR_PAUSE_SEC", 0) or 0)
        years = sorted(by_y.keys())
        for yi, y in enumerate(years):
            batch = by_y[y]
            logger.info("  Year %s: reading %d scene(s)...", y, len(batch))
            for j, (item, anchor_ds) in enumerate(batch):
                r = self._download_and_process_single_scene(
                    item, bbox, j, len(batch), output_date=anchor_ds
                )
                if r is not None:
                    out[anchor_ds] = r
            if pause > 0 and yi < len(years) - 1:
                time.sleep(pause)
        return out

    def _download_and_process_single_scene(
        self,
        item,
        bbox: List[float],
        scene_num: int,
        total_scenes: int,
        output_date: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Download band windows for one STAC item and compute index statistics.
        Used from sequential loops and optionally from a per-scene thread pool.
        """
        try:
            acq = item.datetime.strftime('%Y-%m-%d') if item.datetime else None
            scene_date = output_date or acq
            if not scene_date:
                return None

            cloud_pct = float(item.properties.get('eo:cloud_cover', 0))
            
            # Download bands
            band_data = self._download_bands(item, bbox)
            
            if len(band_data) < 2:  # Need at least NIR and Red for NDVI
                return None
            
            # Calculate indices
            indices = self._calculate_indices(band_data)
            
            # Validate - must have NDVI at minimum
            if np.isnan(indices.get('NDVI_mean', np.nan)):
                return None
            
            rec: Dict = {
                'date': scene_date,
                'missing': False,
                'cloud_cover': cloud_pct,
                'indices': indices,
                'bands_available': list(band_data.keys()),
            }
            if output_date and acq and acq != output_date:
                rec['acquisition_date'] = acq
            return rec
            
        except Exception as e:
            logger.debug(f"Scene {scene_num} failed: {str(e)[:40]}")
            return None

    # =========================================================================
    # SCENE SEARCH & SELECTION (With Proper Cloud Thresholds)
    # =========================================================================

    def _search_scenes_continuous(
        self,
        bbox: List[float],
        start_date: str,
        end_date: str,
        cloud_lt: Optional[float] = None,
    ) -> List:
        """
        Search for scenes; cloud cap from ``cloud_lt`` or balanced default.
        Retries transient STAC/API failures (see SATELLITE_STAC_SEARCH_RETRIES).
        """
        retries = max(0, int(getattr(PipelineConfig, "SATELLITE_STAC_SEARCH_RETRIES", 0)))
        delay = float(getattr(PipelineConfig, "SATELLITE_STAC_SEARCH_RETRY_DELAY_SEC", 2.0))
        cap = float(
            cloud_lt if cloud_lt is not None else self.max_cloud_continuous
        )

        for attempt in range(retries + 1):
            try:
                search = self.catalog.search(
                    collections=[PipelineConfig.SENTINEL2_COLLECTION],
                    bbox=bbox,
                    datetime=f"{start_date}/{end_date}",
                    query={"eo:cloud_cover": {"lt": cap}},
                    limit=500,  # page size; items() paginates across all matches
                )
                items = list(search.items())
                items.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
                return items
            except Exception as e:
                if attempt < retries:
                    logger.warning(
                        f"STAC search retry {attempt + 1}/{retries} "
                        f"({start_date}..{end_date}): {e}"
                    )
                    time.sleep(delay * (attempt + 1))
                    continue
                logger.error(f"Scene search failed ({start_date}..{end_date}): {e}")
                return []
        return []

    def _deduplicate_scenes_by_day(self, items: List) -> List:
        """
        Keep only one best scene per calendar day (lowest cloud cover).
        STAC often returns overlapping tiles for the same date/field; de-duplicating
        by day preserves temporal continuity while avoiding redundant downloads.
        """
        if not items:
            return []

        best_by_day: Dict[str, object] = {}
        for it in items:
            dt = getattr(it, "datetime", None)
            if not dt:
                continue
            day = dt.strftime("%Y-%m-%d")
            cloud = float(it.properties.get("eo:cloud_cover", 100))
            prev = best_by_day.get(day)
            if prev is None:
                best_by_day[day] = it
            else:
                prev_cloud = float(prev.properties.get("eo:cloud_cover", 100))
                if cloud < prev_cloud:
                    best_by_day[day] = it

        deduped = list(best_by_day.values())
        deduped.sort(key=lambda i: i.datetime if i.datetime else datetime.min)
        dropped = max(0, len(items) - len(deduped))
        if dropped > 0:
            logger.info(
                f"Deduplicated same-day scenes: {len(items)} -> {len(deduped)} "
                f"(dropped {dropped})"
            )
        return deduped

    @staticmethod
    def _observation_date(item) -> Optional[date]:
        """Calendar date (UTC) of the STAC item observation."""
        dt = getattr(item, "datetime", None)
        if not dt:
            return None
        if getattr(dt, "tzinfo", None) is not None:
            dt = dt.astimezone(timezone.utc)
        return dt.date()

    @staticmethod
    def _nan_index_bundle() -> Dict:
        """
        Placeholder index dict aligned with _calculate_indices keys.

        Must stay in sync with the full key set emitted by _calculate_indices;
        a missing bin should be shape-identical to a real one so downstream
        consumers can iterate keys without special-casing gaps.
        """
        return {k: np.nan for k in SatelliteDataCollector.INDEX_KEYS}

    @staticmethod
    def _missing_scene_placeholder(bin_start: date) -> Dict:
        """No usable STAC scene in this bin — NaNs for downstream imputation."""
        return {
            'date': bin_start.strftime('%Y-%m-%d'),
            'missing': True,
            'cloud_cover': None,
            'indices': SatelliteDataCollector._nan_index_bundle(),
            'bands_available': [],
        }

    def _build_interval_slots(
        self,
        candidates: List,
        range_start: date,
        range_end: date,
        interval_days: int,
    ) -> List[Tuple[date, Optional[Any]]]:
        """
        Partition [range_start, range_end] into half-open [t, t+interval_days) bins.
        Returns (bin_start, best_item_or_None) for every bin through range_end.
        """
        if interval_days < 1 or range_start > range_end:
            return []

        slots: List[Tuple[date, Optional[Any]]] = []
        cur = range_start
        while cur <= range_end:
            bin_end = cur + timedelta(days=interval_days)
            pool = [
                c for c in candidates
                if (d := self._observation_date(c)) is not None
                and cur <= d <= range_end
                and d < bin_end
            ]
            best: Optional[Any] = None
            if pool:
                best = min(
                    pool,
                    key=lambda c: float(c.properties.get('eo:cloud_cover', 100)),
                )
            slots.append((cur, best))
            cur = bin_end
        return slots

    # =========================================================================
    # BAND DOWNLOAD & INDEX CALCULATION (FIXED)
    # =========================================================================

    def _fetch_one_band_array(
        self,
        item,
        bbox_wgs84: List[float],
        band: str,
        target_resolution: int = 10,
    ) -> Optional[np.ndarray]:
        """
        Read and validate a single band COG window. Returns None if missing or invalid.
        Each band is a separate HTTPS COG — reads can run in parallel (see _download_bands).
        """
        if band not in item.assets:
            return None
        retries = max(0, int(getattr(PipelineConfig, "SATELLITE_BAND_READ_RETRIES", 0)))
        delay = float(getattr(PipelineConfig, "SATELLITE_BAND_RETRY_DELAY_SEC", 0.5))

        for attempt in range(retries + 1):
            try:
                asset = planetary_computer.sign(item.assets[band])
                with rasterio.open(asset.href) as src:
                    bbox_t = transform_bounds("EPSG:4326", src.crs, *bbox_wgs84)
                    window = from_bounds(*bbox_t, transform=src.transform)
                    src_win = Window(0, 0, src.width, src.height)
                    window = window.intersection(src_win)

                    if window.width <= 0 or window.height <= 0:
                        return None

                    window = Window(
                        int(np.floor(window.col_off)),
                        int(np.floor(window.row_off)),
                        int(np.ceil(window.width)),
                        int(np.ceil(window.height)),
                    )

                    native_res = self.band_info.get(band, 20)
                    min_window_size = 2 if native_res == 20 else 5
                    if window.width < min_window_size or window.height < min_window_size:
                        return None

                    out_h, out_w = DataProcessor.resample_to_resolution(
                        np.zeros((int(window.height), int(window.width))),
                        native_res, target_resolution,
                    )
                    data = src.read(
                        1,
                        window=window,
                        out_shape=(out_h, out_w),
                        resampling=Resampling.bilinear,
                    ).astype(np.float32)
                    data = DataProcessor.clean_satellite_data(data, nodata_value=src.nodata)

                    if native_res == 20 and (out_h * out_w) < 10:
                        min_ratio = 0.05
                    else:
                        min_ratio = PipelineConfig.MIN_VALID_PIXEL_RATIO
                    if DataProcessor.validate_band_data(data, min_valid_ratio=min_ratio):
                        return data
                    return None
            except Exception as e:
                if attempt < retries:
                    time.sleep(delay * (attempt + 1))
                    continue
                logger.warning(
                    f"Band read failed after {attempt + 1} attempt(s); "
                    f"skipping band={band} item={getattr(item, 'id', 'unknown')}: {e}"
                )
                return None
        return None

    def _download_bands(self, item, bbox_wgs84, target_resolution=10) -> Dict:
        """
        Load all bands needed for vegetation indices.

        Sentinel-2 STAC items expose one COG URL per band; there is no single combined asset.
        Indices are computed once per scene after all bands are in memory — not one download
        pass per index.
        """
        band_data: Dict[str, np.ndarray] = {}
        # All bands needed for indices: fetch B11 in parallel with reflectance (separate COG).
        parallel_bands = ("B02", "B03", "B04", "B05", "B06", "B08", "B11")
        use_parallel = bool(getattr(PipelineConfig, "SATELLITE_BAND_DOWNLOAD_PARALLEL", False))
        nw = int(getattr(PipelineConfig, "SATELLITE_BAND_DOWNLOAD_MAX_WORKERS", 6))
        nw = max(1, nw)

        if use_parallel and nw > 1:
            workers = min(nw, len(parallel_bands))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(
                        self._fetch_one_band_array, item, bbox_wgs84, b, target_resolution
                    ): b
                    for b in parallel_bands
                }
                for fut in as_completed(futures):
                    b = futures[fut]
                    try:
                        arr = fut.result()
                        if arr is not None:
                            band_data[b] = arr
                    except Exception as e:
                        logger.debug(f"Band {b} parallel fetch failed: {e}")
        else:
            for b in parallel_bands:
                arr = self._fetch_one_band_array(item, bbox_wgs84, b, target_resolution)
                if arr is not None:
                    band_data[b] = arr

        # NDMI fallback when B11 missing or invalid for this window.
        if "B11" not in band_data:
            swir12 = self._fetch_one_band_array(item, bbox_wgs84, "B12", target_resolution)
            if swir12 is not None:
                band_data["B12"] = swir12

        return band_data

    def _calculate_indices(self, bands: Dict) -> Dict:
        """
        Calculate vegetation indices from bands.
        FIXED: Returns NaN instead of 0.0 for missing data.
        """
        indices = {}
        eps = 1e-10
        
        # NDVI: (NIR - Red) / (NIR + Red)
        if 'B08' in bands and 'B04' in bands:
            nir, red = DataProcessor.align_arrays([bands['B08'], bands['B04']])
            ndvi = DataProcessor.calculate_ndvi(nir, red)
            indices['NDVI_mean'] = float(np.nanmean(ndvi))
            indices['NDVI_std'] = float(np.nanstd(ndvi))
            indices['NDVI_p90'] = float(np.nanpercentile(ndvi, 90))
        else:
            indices['NDVI_mean'] = np.nan
            indices['NDVI_std'] = np.nan
            indices['NDVI_p90'] = np.nan
 
        # EVI: 2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)
        if all(b in bands for b in ['B08', 'B04', 'B02']):
            nir, red, blue = DataProcessor.align_arrays(
                [bands['B08'], bands['B04'], bands['B02']]
            )
            evi = DataProcessor.calculate_evi(nir, red, blue)
            indices['EVI_mean'] = float(np.nanmean(evi))
        else:
            indices['EVI_mean'] = np.nan
 
        # NDMI: (NIR - SWIR) / (NIR + SWIR)
        if 'B08' in bands and 'B11' in bands:
            nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B11']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
            if np.sum(valid_mask) > 0:
                ndmi = DataProcessor.calculate_ndmi(nir, swir)
                indices['NDMI_mean'] = float(np.nanmean(ndmi))
            else:
                indices['NDMI_mean'] = np.nan
        elif 'B08' in bands and 'B12' in bands:
            nir, swir = DataProcessor.align_arrays([bands['B08'], bands['B12']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(swir)) & (nir > 0) & (swir > 0)
            if np.sum(valid_mask) > 0:
                ndmi = DataProcessor.calculate_ndmi(nir, swir)
                indices['NDMI_mean'] = float(np.nanmean(ndmi))
            else:
                indices['NDMI_mean'] = np.nan
        else:
            indices['NDMI_mean'] = np.nan
 
        # PSRI: (Red - Blue) / Red Edge 2
        if all(b in bands for b in ['B04', 'B02', 'B06']):
            red, blue, re2 = DataProcessor.align_arrays(
                [bands['B04'], bands['B02'], bands['B06']]
            )
            valid_mask = (~np.isnan(red)) & (~np.isnan(blue)) & (~np.isnan(re2)) & (re2 > 0)
            if np.sum(valid_mask) > 0:
                psri = (red - blue) / (re2 + eps)
                psri = np.clip(psri, -1, 1)
                indices['PSRI_mean'] = float(np.nanmean(psri))
            else:
                indices['PSRI_mean'] = np.nan
        else:
            indices['PSRI_mean'] = np.nan
 
        # NDRE: (NIR - Red Edge 1) / (NIR + Red Edge 1)
        if all(b in bands for b in ['B08', 'B05']):
            nir, re1 = DataProcessor.align_arrays([bands['B08'], bands['B05']])
            valid_mask = (~np.isnan(nir)) & (~np.isnan(re1)) & (nir > 0) & (re1 > 0)
            if np.sum(valid_mask) > 0:
                ndre = (nir - re1) / (nir + re1 + eps)
                indices['NDRE_mean'] = float(np.nanmean(ndre))
            else:
                indices['NDRE_mean'] = np.nan
        else:
            indices['NDRE_mean'] = np.nan
 
        # NDWI: (Green - NIR) / (Green + NIR)
        if all(b in bands for b in ['B03', 'B08']):
            green, nir = DataProcessor.align_arrays([bands['B03'], bands['B08']])
            valid_mask = (~np.isnan(green)) & (~np.isnan(nir)) & (green > 0) & (nir > 0)
            if np.sum(valid_mask) > 0:
                ndwi = (green - nir) / (green + nir + eps)
                indices['NDWI_mean'] = float(np.nanmean(ndwi))
            else:
                indices['NDWI_mean'] = np.nan
        else:
            indices['NDWI_mean'] = np.nan

        # ---- Pillar 1: additional indices (delegated to DataProcessor) ----
        # MSAVI2 — early-season / sowing (robust on bare-ish soil)
        if 'B08' in bands and 'B04' in bands:
            nir, red = DataProcessor.align_arrays([bands['B08'], bands['B04']])
            indices['MSAVI2_mean'] = float(np.nanmean(DataProcessor.calculate_msavi2(nir, red)))
            # NIRv — yield-potential backbone (kept distinct from detection CVI)
            indices['NIRv_mean'] = float(np.nanmean(DataProcessor.calculate_nirv(nir, red)))
            # kNDVI — saturation-resistant detection backbone
            indices['kNDVI_mean'] = float(np.nanmean(DataProcessor.calculate_kndvi(nir, red)))
        else:
            indices['MSAVI2_mean'] = np.nan
            indices['NIRv_mean'] = np.nan
            indices['kNDVI_mean'] = np.nan

        # LSWI — canopy/soil water (paddy flood + water stress). Prefer B11, else B12.
        if 'B08' in bands and 'B11' in bands:
            nir, swir1 = DataProcessor.align_arrays([bands['B08'], bands['B11']])
            indices['LSWI_mean'] = float(np.nanmean(DataProcessor.calculate_lswi(nir, swir1)))
        elif 'B08' in bands and 'B12' in bands:
            nir, swir2 = DataProcessor.align_arrays([bands['B08'], bands['B12']])
            indices['LSWI_mean'] = float(np.nanmean(DataProcessor.calculate_lswi(nir, swir2)))
        else:
            indices['LSWI_mean'] = np.nan

        # GCVI — high-LAI vigor (optional)
        if getattr(PipelineConfig, 'ENABLE_INDEX_GCVI', True) and all(b in bands for b in ['B08', 'B03']):
            nir, green = DataProcessor.align_arrays([bands['B08'], bands['B03']])
            indices['GCVI_mean'] = float(np.nanmean(DataProcessor.calculate_gcvi(nir, green)))
        else:
            indices['GCVI_mean'] = np.nan

        return indices

    # =========================================================================
    # FALLBACK: Old Seasonal Approach (Backward Compatibility)
    # =========================================================================

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _empty_continuous_result(self, lat: float, lon: float, area: float) -> Dict:
        """Return empty result structure for continuous mode."""
        return {
            'mode': 'continuous',
            'location': {'latitude': lat, 'longitude': lon},
            'field_area_ha': area,
            'continuous_data': {
                'scenes': [],
                'dates': [],
                'ndvi_values': [],
                'evi_values': [],
                'ndmi_values': [],
                'psri_values': [],
                'ndre_values': [],
                'ndwi_values': [],
                'scene_count': 0,
            },
            'collection_date': datetime.now().isoformat(),
            'summary': {
                'total_scenes': 0,
                'collection_mode': 'continuous_3year',
                'error': 'No data available',
            },
        }
