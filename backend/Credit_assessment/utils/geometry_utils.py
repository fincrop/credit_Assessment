"""
Geometry Utilities
==================
Handles geometric operations for farm boundaries and coordinate conversions.

Supports:
- Lat/long to bounding box conversion
- Farm boundary geometry to bbox and centroid
- Distance calculations
- Coordinate transformations
- Geometry QA vs registered area
"""

import os
from typing import Any, Dict, List, Optional, Tuple, Union

import logging
import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import transform

logger = logging.getLogger(__name__)


def _config_float(name: str, default: float) -> float:
    try:
        from config import PipelineConfig
        return float(getattr(PipelineConfig, name, default))
    except Exception:
        return default


class GeometryUtils:
    """Utility class for geometric operations"""
    
    # Earth parameters
    EARTH_RADIUS_KM = 6371.0
    KM_PER_DEGREE_LAT = 111.0

    @classmethod
    def adaptive_buffer_km(cls, field_area_ha: float) -> float:
        """
        Buffer radius for point-only farms from declared area.

        Half-side of a square of ``field_area_ha`` plus a small pad, floored at
        ``MIN_FIELD_BUFFER_KM`` (~0.15 km) unless FIELD_BUFFER_LEGACY restores 0.5.
        """
        area = max(float(field_area_ha or 0.0), 0.01)
        side_km = np.sqrt(area * 0.01)
        pad = _config_float("FIELD_BUFFER_PADDING_KM", 0.02)
        raw = side_km / 2.0 + pad

        legacy = os.environ.get("FIELD_BUFFER_LEGACY", "").strip().lower() in (
            "1", "true", "yes",
        )
        floor = 0.5 if legacy else _config_float("MIN_FIELD_BUFFER_KM", 0.15)
        cap = _config_float("FIELD_BUFFER_MAX_KM", 2.0)
        return float(min(cap, max(floor, raw)))
    
    @classmethod
    def calculate_bbox_from_point(
        cls,
        latitude: float,
        longitude: float,
        field_area_ha: float,
        min_buffer_km: Optional[float] = None,
    ) -> List[float]:
        """
        Calculate bounding box around a point based on field area.
        
        Args:
            latitude: Point latitude
            longitude: Point longitude
            field_area_ha: Field area in hectares (for buffer calculation)
            min_buffer_km: Optional override floor; default uses adaptive_buffer_km
        
        Returns:
            Bounding box as [min_lon, min_lat, max_lon, max_lat]
        """
        if min_buffer_km is None:
            buffer_km = cls.adaptive_buffer_km(field_area_ha)
        else:
            side_length_km = np.sqrt(max(float(field_area_ha or 0.01), 0.01) * 0.01)
            buffer_km = max(side_length_km / 2, float(min_buffer_km))
        
        # Calculate degrees per km at this latitude
        lat_rad = np.radians(latitude)
        km_per_deg_lat = cls.KM_PER_DEGREE_LAT
        km_per_deg_lon = cls.KM_PER_DEGREE_LAT * np.cos(lat_rad)
        
        # Convert buffer to degrees
        buffer_deg_lat = buffer_km / km_per_deg_lat
        buffer_deg_lon = buffer_km / km_per_deg_lon
        
        bbox = [
            longitude - buffer_deg_lon,  # min_lon
            latitude - buffer_deg_lat,    # min_lat
            longitude + buffer_deg_lon,   # max_lon
            latitude + buffer_deg_lat     # max_lat
        ]
        
        logger.debug(f"Calculated bbox for point ({latitude:.4f}, {longitude:.4f}): {bbox}")
        logger.debug(f"Buffer: {buffer_km:.3f} km, Field area: {field_area_ha:.2f} ha")
        
        return bbox

    @classmethod
    def validate_farm_geometry(
        cls,
        geometry: Union[Polygon, MultiPolygon, dict],
        field_area_ha: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        QA a farm polygon before trusting it for satellite bbox.

        Returns dict with keys: ok, reason, area_ha, ratio, geometry (possibly repaired).
        """
        from shapely.geometry import shape
        from shapely.validation import make_valid

        geom = shape(geometry) if isinstance(geometry, dict) else geometry
        if geom is None or geom.is_empty:
            return {
                "ok": False,
                "reason": "empty_geometry",
                "area_ha": 0.0,
                "ratio": None,
                "geometry": None,
            }

        if not geom.is_valid:
            try:
                geom = make_valid(geom)
            except Exception as exc:
                return {
                    "ok": False,
                    "reason": f"invalid_geometry:{exc}",
                    "area_ha": 0.0,
                    "ratio": None,
                    "geometry": None,
                }
            if geom.is_empty or not getattr(geom, "is_valid", True):
                return {
                    "ok": False,
                    "reason": "invalid_geometry_unrepairable",
                    "area_ha": 0.0,
                    "ratio": None,
                    "geometry": None,
                }

        area_ha = float(cls.calculate_area_from_geometry(geom) or 0.0)
        ratio = None
        if field_area_ha is not None:
            try:
                reg = float(field_area_ha)
            except (TypeError, ValueError):
                reg = 0.0
            if reg > 0 and area_ha > 0:
                ratio = area_ha / reg
                lo = _config_float("GEOMETRY_AREA_RATIO_MIN", 0.5)
                hi = _config_float("GEOMETRY_AREA_RATIO_MAX", 2.0)
                if ratio < lo or ratio > hi:
                    return {
                        "ok": False,
                        "reason": f"area_ratio_out_of_range:{ratio:.3f}",
                        "area_ha": area_ha,
                        "ratio": ratio,
                        "geometry": geom,
                    }

        return {
            "ok": True,
            "reason": "ok",
            "area_ha": area_ha,
            "ratio": ratio,
            "geometry": geom,
        }
    
    @classmethod
    def calculate_bbox_from_geometry(
        cls,
        geometry: Union[Polygon, dict]
    ) -> List[float]:
        """
        Calculate bounding box from a geometry (Polygon or GeoJSON).
        
        Args:
            geometry: Shapely Polygon or GeoJSON dict
        
        Returns:
            Bounding box as [min_lon, min_lat, max_lon, max_lat]
        """
        # Convert GeoJSON to Shapely if needed
        if isinstance(geometry, dict):
            from shapely.geometry import shape
            geometry = shape(geometry)
        
        # Get bounds
        bounds = geometry.bounds  # (minx, miny, maxx, maxy)
        
        bbox = [bounds[0], bounds[1], bounds[2], bounds[3]]
        
        logger.debug(f"Extracted bbox from geometry: {bbox}")
        
        return bbox
    
    @classmethod
    def calculate_centroid_from_geometry(
        cls,
        geometry: Union[Polygon, dict]
    ) -> Tuple[float, float]:
        """
        Calculate centroid (center point) from a geometry.
        
        Args:
            geometry: Shapely Polygon or GeoJSON dict
        
        Returns:
            Tuple of (latitude, longitude)
        """
        # Convert GeoJSON to Shapely if needed
        if isinstance(geometry, dict):
            from shapely.geometry import shape
            geometry = shape(geometry)
        
        # Get centroid
        centroid = geometry.centroid
        
        latitude = centroid.y
        longitude = centroid.x
        
        logger.debug(f"Calculated centroid: ({latitude:.6f}, {longitude:.6f})")
        
        return latitude, longitude
    
    @staticmethod
    def _ring_area_deg2(ring_coords: List[Tuple[float, float]]) -> float:
        """Shoelace area in degrees² for one closed ring."""
        coords = list(ring_coords)
        if len(coords) < 3:
            return 0.0
        area_deg2 = 0.0
        for i in range(len(coords) - 1):
            x1, y1 = coords[i]
            x2, y2 = coords[i + 1]
            area_deg2 += (x1 * y2 - x2 * y1)
        return abs(area_deg2) / 2.0

    @classmethod
    def _polygon_area_deg2(cls, polygon: Polygon) -> float:
        """Area in degrees² for one polygon (outer ring minus holes)."""
        if polygon.is_empty:
            return 0.0
        outer = cls._ring_area_deg2(polygon.exterior.coords)
        inner = sum(cls._ring_area_deg2(ring.coords) for ring in polygon.interiors)
        return max(0.0, outer - inner)

    @classmethod
    def _polygonal_geoms_deg2(cls, geometry) -> float:
        """
        Sum planar area (degrees²) for Polygon / MultiPolygon / collections of polygons.
        """
        from shapely.geometry import GeometryCollection

        if isinstance(geometry, Polygon):
            return cls._polygon_area_deg2(geometry)
        if isinstance(geometry, MultiPolygon):
            return sum(cls._polygon_area_deg2(p) for p in geometry.geoms if isinstance(p, Polygon))
        if isinstance(geometry, GeometryCollection):
            return sum(cls._polygonal_geoms_deg2(g) for g in geometry.geoms)

        geom_type = getattr(geometry, "geom_type", None)
        if geom_type == "Polygon":
            return cls._polygon_area_deg2(geometry)
        if geom_type == "MultiPolygon":
            return sum(cls._polygon_area_deg2(p) for p in geometry.geoms if isinstance(p, Polygon))
        logger.warning(f"calculate_area_from_geometry: unsupported geom_type={geom_type!r}")
        return 0.0

    @classmethod
    def calculate_area_from_geometry(
        cls,
        geometry: Union[Polygon, MultiPolygon, dict]
    ) -> float:
        """
        Calculate approximate area of a geometry in hectares.
        
        Note: This is an approximation for lat/long coordinates.
        For precise area, use projected coordinates.
        
        Args:
            geometry: Shapely Polygon/MultiPolygon or GeoJSON dict
        
        Returns:
            Area in hectares
        """
        # Convert GeoJSON to Shapely if needed
        if isinstance(geometry, dict):
            from shapely.geometry import shape
            geometry = shape(geometry)
        
        centroid_lat = geometry.centroid.y
        
        lat_rad = np.radians(centroid_lat)
        m_per_deg_lat = cls.KM_PER_DEGREE_LAT * 1000
        m_per_deg_lon = cls.KM_PER_DEGREE_LAT * 1000 * np.cos(lat_rad)
        
        area_deg2 = cls._polygonal_geoms_deg2(geometry)
        
        area_m2 = area_deg2 * m_per_deg_lon * m_per_deg_lat
        area_ha = area_m2 / 10000.0
        
        logger.debug(f"Calculated area: {area_ha:.2f} hectares")
        
        return area_ha
    
    @classmethod
    def validate_bbox(cls, bbox: List[float]) -> bool:
        """
        Validate bounding box format and values.
        
        Args:
            bbox: [min_lon, min_lat, max_lon, max_lat]
        
        Returns:
            True if valid, False otherwise
        """
        if len(bbox) != 4:
            logger.error(f"Invalid bbox length: {len(bbox)}, expected 4")
            return False
        
        min_lon, min_lat, max_lon, max_lat = bbox
        
        # Check longitude range
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
            logger.error(f"Invalid longitude values: {min_lon}, {max_lon}")
            return False
        
        # Check latitude range
        if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            logger.error(f"Invalid latitude values: {min_lat}, {max_lat}")
            return False
        
        # Check min < max
        if min_lon >= max_lon:
            logger.error(f"min_lon >= max_lon: {min_lon} >= {max_lon}")
            return False
        
        if min_lat >= max_lat:
            logger.error(f"min_lat >= max_lat: {min_lat} >= {max_lat}")
            return False
        
        return True
    
    @classmethod
    def validate_coordinates(cls, latitude: float, longitude: float) -> bool:
        """
        Validate latitude and longitude values.
        
        Args:
            latitude: Latitude value
            longitude: Longitude value
        
        Returns:
            True if valid, False otherwise
        """
        if not (-90 <= latitude <= 90):
            logger.error(f"Invalid latitude: {latitude}")
            return False
        
        if not (-180 <= longitude <= 180):
            logger.error(f"Invalid longitude: {longitude}")
            return False
        
        return True
    
    @classmethod
    def calculate_distance_km(
        cls,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        """
        Calculate distance between two lat/long points using Haversine formula.
        
        Args:
            lat1, lon1: First point
            lat2, lon2: Second point
        
        Returns:
            Distance in kilometers
        """
        # Convert to radians
        lat1_rad = np.radians(lat1)
        lon1_rad = np.radians(lon1)
        lat2_rad = np.radians(lat2)
        lon2_rad = np.radians(lon2)
        
        # Haversine formula
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        
        a = (np.sin(dlat / 2)**2 + 
             np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2)**2)
        
        c = 2 * np.arcsin(np.sqrt(a))
        
        distance_km = cls.EARTH_RADIUS_KM * c
        
        return distance_km
    
    @classmethod
    def bbox_to_polygon(cls, bbox: List[float]) -> Polygon:
        """
        Convert bounding box to Shapely Polygon.
        
        Args:
            bbox: [min_lon, min_lat, max_lon, max_lat]
        
        Returns:
            Shapely Polygon
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        return box(min_lon, min_lat, max_lon, max_lat)
    
    @classmethod
    def get_bbox_center(cls, bbox: List[float]) -> Tuple[float, float]:
        """
        Get center point of bounding box.
        
        Args:
            bbox: [min_lon, min_lat, max_lon, max_lat]
        
        Returns:
            Tuple of (latitude, longitude)
        """
        min_lon, min_lat, max_lon, max_lat = bbox
        
        center_lat = (min_lat + max_lat) / 2
        center_lon = (min_lon + max_lon) / 2
        
        return center_lat, center_lon
