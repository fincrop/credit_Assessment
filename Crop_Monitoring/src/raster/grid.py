"""Village tile grid and per-field pixel masks.

One 10 m UTM grid per village. Each field is burned into it; its interior is
the field shrunk by one pixel, so a bund, road or neighbour mixed into an edge
pixel never enters the field's statistics (framework §5, plan §5.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor

import numpy as np

PIXEL_M = 10.0
# Fewer interior pixels than this and a field is reported `low_resolution`:
# its numbers come from the field mean only, never a within-field map.
MIN_INTERIOR_PIXELS = 8
# Within-field maps (plan §5.8) need at least this many interior pixels.
MIN_MAP_PIXELS = 30


def utm_epsg(lon: float, lat: float) -> int:
    zone = int(floor((lon + 180.0) / 6.0)) + 1
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class Grid:
    epsg: int
    x0: float          # left edge, metres
    y0: float          # top edge, metres
    width: int
    height: int
    res: float = PIXEL_M

    @property
    def crs(self) -> str:
        return f"EPSG:{self.epsg}"

    @property
    def transform(self):
        from rasterio.transform import from_origin
        return from_origin(self.x0, self.y0, self.res, self.res)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    def ee_grid(self) -> dict:
        """Grid spec for ee.data.computePixels."""
        return {
            "dimensions": {"width": self.width, "height": self.height},
            "affineTransform": {
                "scaleX": self.res, "shearX": 0, "translateX": self.x0,
                "shearY": 0, "scaleY": -self.res, "translateY": self.y0,
            },
            "crsCode": self.crs,
        }

    def bounds(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0 - self.height * self.res, self.x0 + self.width * self.res, self.y0

    def lonlat_bounds(self) -> tuple[float, float, float, float]:
        from pyproj import Transformer
        t = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True)
        xmin, ymin, xmax, ymax = self.bounds()
        xs = [xmin, xmax, xmin, xmax]
        ys = [ymin, ymin, ymax, ymax]
        lon, lat = t.transform(xs, ys)
        return min(lon), min(lat), max(lon), max(lat)

    def tiles(self, max_side: int = 512) -> list["Grid"]:
        """Sub-grids no larger than max_side, for request-size limits."""
        out = []
        for r in range(0, self.height, max_side):
            for c in range(0, self.width, max_side):
                out.append(Grid(
                    self.epsg, self.x0 + c * self.res, self.y0 - r * self.res,
                    min(max_side, self.width - c), min(max_side, self.height - r), self.res,
                ))
        return out

    def offset_of(self, sub: "Grid") -> tuple[int, int]:
        """(row, col) of a sub-grid's top-left pixel in this grid."""
        return int(round((self.y0 - sub.y0) / self.res)), int(round((sub.x0 - self.x0) / self.res))


def grid_for(geometries: list[dict], pad_m: float = 100.0, res: float = PIXEL_M) -> Grid:
    """Smallest pixel-aligned UTM grid covering every geometry plus a pad."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform, unary_union

    shapes = [shape(g) for g in geometries if g]
    if not shapes:
        raise ValueError("no geometry to grid")
    union = unary_union(shapes)
    c = union.centroid
    epsg = utm_epsg(c.x, c.y)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    xmin, ymin, xmax, ymax = shp_transform(to_utm, union).bounds
    x0 = floor((xmin - pad_m) / res) * res
    y1 = -floor(-(ymax + pad_m) / res) * res
    x1 = -floor(-(xmax + pad_m) / res) * res
    y0 = floor((ymin - pad_m) / res) * res
    return Grid(epsg, x0, y1, int(round((x1 - x0) / res)), int(round((y1 - y0) / res)), res)


MIN_INNER_PIXELS = 4


def _mask(shape: tuple[int, int], rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    out = np.zeros(shape, dtype=bool)
    if rows.size:
        out[rows, cols] = True
    return out


@dataclass
class FieldMask:
    """Pixel coordinates, not a full-village array per field.

    Umapur is ~15,000 fields on an 800,000-pixel grid. Three bool arrays
    each would be tens of gigabytes, and that is what stopped monitoring
    after classification. A bool view is built only when a caller indexes
    with it, then discarded.
    """
    field_id: str
    shape: tuple[int, int]
    full_rows: np.ndarray
    full_cols: np.ndarray
    interior_rows: np.ndarray
    interior_cols: np.ndarray
    area_ha: float
    inner_rows: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))
    inner_cols: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int32))

    @property
    def full(self) -> np.ndarray:
        return _mask(self.shape, self.full_rows, self.full_cols)

    @property
    def interior(self) -> np.ndarray:
        return _mask(self.shape, self.interior_rows, self.interior_cols)

    @property
    def inner(self) -> np.ndarray:
        return _mask(self.shape, self.inner_rows, self.inner_cols)

    @property
    def n_interior(self) -> int:
        return int(self.interior_rows.size)

    @property
    def n_inner(self) -> int:
        return int(self.inner_rows.size)

    @property
    def n_full(self) -> int:
        return int(self.full_rows.size)

    @property
    def low_resolution(self) -> bool:
        return self.n_interior < MIN_INTERIOR_PIXELS

    @property
    def mappable(self) -> bool:
        return self.n_interior >= MIN_MAP_PIXELS

    @property
    def pixel_basis(self) -> str:
        """Which pixels carry the field's numbers. Dhaswadi's median field
        (0.23 ha) keeps ~4 pixels after a 10 m inset, so the fallbacks matter:
        `interior` (>= 8 px, no edge mixing), `inner` (pixel inside the line,
        may touch it), `full` (every touching pixel; edge mixing likely)."""
        if self.n_interior >= MIN_INTERIOR_PIXELS:
            return "interior"
        if self.n_inner >= MIN_INNER_PIXELS:
            return "inner"
        return "full"

    def stat_pixels(self) -> np.ndarray:
        basis = self.pixel_basis
        if basis == "interior":
            return self.interior
        if basis == "inner":
            return self.inner
        return self.full


def _burn_window(geom, grid: Grid, *, all_touched: bool) -> tuple[np.ndarray, np.ndarray]:
    """Rows and columns of pixels burned by `geom`, on the field's own window."""
    from math import ceil

    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import mapping

    minx, miny, maxx, maxy = geom.bounds
    pad = 1
    col0 = max(0, int(floor((minx - grid.x0) / grid.res)) - pad)
    col1 = min(grid.width, int(ceil((maxx - grid.x0) / grid.res)) + pad)
    row0 = max(0, int(floor((grid.y0 - maxy) / grid.res)) - pad)
    row1 = min(grid.height, int(ceil((grid.y0 - miny) / grid.res)) + pad)
    empty = np.zeros(0, np.int32)
    if col1 <= col0 or row1 <= row0:
        return empty, empty
    window = from_origin(grid.x0 + col0 * grid.res, grid.y0 - row0 * grid.res, grid.res, grid.res)
    burned = rasterize([(mapping(geom), 1)], out_shape=(row1 - row0, col1 - col0), transform=window,
                       fill=0, all_touched=all_touched, dtype="uint8")
    rows, cols = np.nonzero(burned)
    if rows.size == 0:
        return empty, empty
    return (rows + row0).astype(np.int32), (cols + col0).astype(np.int32)


def field_masks(grid: Grid, fields: list[dict]) -> dict[str, FieldMask]:
    """Rasterise each field. `fields` items carry field_id and a GeoJSON geometry."""
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as shp_transform

    to_utm = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
    out: dict[str, FieldMask] = {}
    for f in fields:
        geom = shp_transform(to_utm, shape(f["geometry"]))
        if geom.is_empty:
            continue
        full_r, full_c = _burn_window(geom, grid, all_touched=True)

        def _centres_inside(inset: float) -> tuple[np.ndarray, np.ndarray]:
            core = geom.buffer(-inset)
            if core.is_empty:
                empty = np.zeros(0, np.int32)
                return empty, empty
            rows, cols = _burn_window(core, grid, all_touched=False)
            if rows.size == 0 or full_r.size == 0:
                empty = np.zeros(0, np.int32)
                return empty, empty
            full_key = full_r.astype(np.int64) * grid.width + full_c
            core_key = rows.astype(np.int64) * grid.width + cols
            sel = np.isin(core_key, full_key)
            return rows[sel], cols[sel]

        in_r, in_c = _centres_inside(grid.res)
        inn_r, inn_c = _centres_inside(grid.res / 2)
        out[str(f["field_id"])] = FieldMask(
            str(f["field_id"]), grid.shape, full_r, full_c, in_r, in_c, geom.area / 1e4, inn_r, inn_c,
        )
    return out
