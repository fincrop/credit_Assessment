"""Season-profile merging of watershed segments (one farm, one segment)."""
from __future__ import annotations

import numpy as np

from crop_analysis.field_delineation import merge_by_profile


def _scene():
    """40 x 60 grid, four farms side by side (15 columns each):

      A | A'   one farm cut by a faint internal line (same profile)
      B        a different crop, strong bund on both sides
      C        same crop as A, separated from B by a strong bund
    """
    H, W = 40, 60
    labels = np.zeros((H, W), np.int32)
    labels[:, 0:15] = 1        # A
    labels[:, 15:30] = 2       # A' (same farm as A)
    labels[:, 30:45] = 3       # B
    labels[:, 45:60] = 4       # C (same crop as A, own farm)
    edge = np.full((H, W), 0.05)
    edge[:, 14:16] = 0.25      # faint internal line A|A'
    edge[:, 29:31] = 1.0       # bund A'|B
    edge[:, 44:46] = 1.0       # bund B|C
    months = 6
    cotton = np.r_[np.linspace(0.2, 0.75, months), np.linspace(-22, -16, months), np.linspace(-10.5, -7.5, months)]
    soy = np.r_[[0.2, 0.6, 0.85, 0.8, 0.35, 0.25], np.linspace(-22, -17, months), np.linspace(-10.5, -8.0, months)]
    rng = np.random.default_rng(0)
    prof = np.zeros((H, W, 3 * months))
    for lab, curve in ((1, cotton), (2, cotton), (3, soy), (4, cotton)):
        prof[labels == lab] = curve + rng.normal(0, [0.02] * months + [0.4] * months + [0.3] * months,
                                                 ((labels == lab).sum(), 3 * months))
    prof[:, :, 2] = np.nan     # a cloudy monsoon month: NDVI missing everywhere
    return labels, prof, edge


def test_split_farm_merges_but_different_crops_and_bunded_farms_stay_apart():
    labels, prof, edge = _scene()
    out = merge_by_profile(labels, prof, edge, min_pixels=20)
    a, a2, b, c = out[0, 0], out[0, 20], out[0, 35], out[0, 50]
    assert a == a2                 # faint internal line removed
    assert a != b and b != c       # different crop stays separate
    assert a != c                  # same crop, but a real bund: two farms
    assert len(np.unique(out)) == 3


def test_fragments_join_their_most_similar_neighbour():
    labels, prof, edge = _scene()
    labels[0:3, 31:34] = 9         # a 9-pixel speck inside B with B's profile
    out = merge_by_profile(labels, prof, edge, min_pixels=20)
    assert out[1, 32] == out[20, 35]


def test_profiles_on_a_coarser_grid_are_resampled():
    labels, prof, edge = _scene()
    up = np.kron(labels, np.ones((2, 2), np.int32))
    e2 = np.kron(edge, np.ones((2, 2)))
    out = merge_by_profile(up, prof, e2, min_pixels=80)
    assert out[0, 0] == out[0, 40] and out[0, 0] != out[0, 70]


def test_vhr_edges_land_on_the_delineation_grid(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from crop_analysis.field_delineation import BoundaryArrays, vhr_edge_on_grid

    # 1 m image, 200 x 200 m, a bright bund running north-south at x = 100 m.
    img = np.full((3, 200, 200), 80, np.uint8)
    img[:, :, 99:101] = 220
    path = tmp_path / "vhr.tif"
    with rasterio.open(path, "w", driver="GTiff", width=200, height=200, count=3, dtype="uint8",
                       crs="EPSG:32643", transform=from_origin(500000, 2080200, 1, 1)) as dst:
        dst.write(img)
    ba = BoundaryArrays(np.zeros((20, 20, 4), np.float32), 500000.0, 2080200.0, 32643, 2026)
    e = vhr_edge_on_grid(str(path), ba, upsample=2)
    assert e.shape == (40, 40)
    col = e.mean(axis=0)
    assert int(np.argmax(col)) in (19, 20)        # 5 m cells around x = 100 m
    assert col[19:21].max() > 3 * np.median(col)
