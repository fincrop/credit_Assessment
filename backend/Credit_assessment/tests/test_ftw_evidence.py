"""FTW Global as evidence inside the watershed: outlines on the grid, same-field joins."""
from __future__ import annotations

import numpy as np
from shapely.geometry import box

from crop_analysis.field_delineation import (BoundaryArrays, _to_wgs, ftw_on_grid,
                                             merge_by_ftw)


def test_pieces_inside_one_ftw_field_join_but_a_bund_still_separates():
    H, W = 20, 40
    labels = np.zeros((H, W), np.int32)
    labels[:, 0:10] = 1          # A   } one FTW field, faint line between
    labels[:, 10:20] = 2         # A'  }
    labels[:, 20:30] = 3         # B   } another FTW field, but a strong bund
    labels[:, 30:40] = 4         # B'  } inside it (two farms FTW merged)
    ids = np.zeros((H, W), np.int32)
    ids[:, 0:20] = 1
    ids[:, 20:40] = 2
    edge = np.full((H, W), 0.05)
    edge[:, 9:11] = 0.2          # faint
    edge[:, 19:21] = 1.0         # bund between the FTW fields
    edge[:, 29:31] = 1.0         # bund FTW missed
    out = merge_by_ftw(labels, ids, edge)
    assert out[0, 0] == out[0, 15]
    assert out[0, 15] != out[0, 25]
    assert out[0, 25] != out[0, 35]


def test_segment_mostly_outside_ftw_is_left_alone():
    labels = np.zeros((10, 20), np.int32)
    labels[:, 10:] = 1
    ids = np.zeros((10, 20), np.int32)
    ids[:, 8:12] = 5             # FTW field straddles the line but covers neither side
    edge = np.full((10, 20), 0.05)
    out = merge_by_ftw(labels, ids, edge)
    assert out[0, 0] != out[0, 15]


def test_ftw_polygons_land_on_the_grid():
    epsg = 32643
    ba = BoundaryArrays(np.zeros((20, 20, 4), np.float32), 500000.0, 2080200.0, epsg, 2026)
    # Two adjacent 100 x 200 m FTW fields, west and east halves of the grid.
    west = _to_wgs(box(500000, 2080000, 500100, 2080200), epsg)
    east = _to_wgs(box(500100, 2080000, 500200, 2080200), epsg)
    edge, ids = ftw_on_grid([(west, {"confidence": 0.5}), (east, {"confidence": 0.5})], ba, 2)
    assert edge.shape == ids.shape == (40, 40)
    assert ids[20, 5] != ids[20, 35] and ids[20, 5] > 0 and ids[20, 35] > 0
    col = edge.mean(axis=0)
    assert int(np.argmax(col[5:35])) + 5 in (19, 20)      # the shared outline at x = 100 m
