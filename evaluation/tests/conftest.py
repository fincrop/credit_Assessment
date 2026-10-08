import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DHASWADI = ROOT / "Crop_Monitoring" / "Results" / "Dhaswadi_kharif_2026.geojson"
DHASWADI_ANALYSIS = ROOT / "Crop_Monitoring" / "Results" / "Dhaswadi_kharif_analysis.json"


def square(lon, lat, d=0.001):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d],
                                                [lon, lat + d], [lon, lat]]]}


def make_fc(spec, start_lon=76.80, start_lat=18.80, d=0.001):
    """spec: list of (crop, n, area_ha, extra_props)."""
    feats, fid = [], 1
    for crop, n, area, extra in spec:
        for _ in range(n):
            lon = start_lon + (fid % 50) * d * 1.5
            lat = start_lat + (fid // 50) * d * 1.5
            props = {"field_id": fid, "crop": crop, "area_ha": area}
            props.update(extra or {})
            feats.append({"type": "Feature", "geometry": square(lon, lat, d), "properties": props})
            fid += 1
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def synthetic_fc():
    return make_fc([("Cotton", 120, 1.0, None), ("Soyabean", 60, 0.8, None),
                    ("Fallow", 25, 0.5, None),
                    ("Abstained", 15, 0.6, {"status": "abstained"}),
                    ("Cotton", 8, 1.0, {"status": "phenology_disagrees"})])
