"""End-to-end checks on the synthetic season and the stage functions."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.library import get_prior
from src.models import PixelRecord, PixelTrack, WeatherDay
from src.pipeline import run_monitoring
from src.progress import measure
from src.sowing import SowingEstimate, fuse_sowing
from src.state import StateDay
from src.synthetic import soybean_mixed
from src.zones import Zone, split_harvest, split_zones


class MonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request, cls.stack = soybean_mixed(2024)
        cls.doc = run_monitoring(cls.request, cls.stack)

    def _zone(self, zone_id):
        return next(z for z in self.doc["zones"] if z["zone_id"] == zone_id)

    def test_two_cohorts_and_a_tree(self):
        kinds = [z["kind"] for z in self.doc["zones"]]
        self.assertIn("non_crop", kinds)
        self.assertEqual(self.doc["non_crop_pixels"], 1)
        self.assertEqual(self.doc["zone_count"], 2)
        features = self.doc["fields"]["features"]
        self.assertEqual(len(features), 2)
        self.assertTrue(all(f["properties"].get("color") for f in features))
        early = self._zone("z1")
        late = self._zone("z2")
        self.assertGreater(early["pixel_count"], late["pixel_count"])
        self.assertLess(early["greenup"], late["greenup"])
        gap = (date.fromisoformat(late["greenup"]) - date.fromisoformat(early["greenup"])).days
        self.assertGreaterEqual(gap, 21)

    def test_sowing_uses_rain_and_radar_not_only_optical(self):
        sowing = self._zone("z1")["sowing"]
        self.assertTrue(sowing["known"])
        when = date.fromisoformat(sowing["date"])
        self.assertGreaterEqual(when, date(2024, 6, 14))
        self.assertLessEqual(when, date(2024, 7, 5))
        self.assertIn("rain", sowing["sources"])
        self.assertTrue({"radar", "optical"} & set(sowing["sources"]))
        self.assertGreaterEqual(sowing["confidence"], 0.45)
        self.assertLess(sowing["early"], sowing["date"])
        self.assertGreater(sowing["late"], sowing["date"])

    def test_second_cohort_is_not_scored_as_stress_on_the_first(self):
        early = self._zone("z1")
        late = self._zone("z2")
        self.assertNotEqual(early["sowing"]["date"], late["sowing"]["date"])
        peak_stress = [
            item["stress"] for item in early["intervals"]
            if item["date"] == "2024-09-05" and item.get("stress")
        ]
        self.assertTrue(peak_stress)
        stress = peak_stress[0]
        self.assertEqual(stress["type"], "Crop Water Deficit")
        self.assertGreater(stress["stressed_fraction"], 0.15)
        self.assertLess(stress["stressed_fraction"], 0.5)

    def test_irrigation_flag_on_a_dry_week(self):
        self.assertIn("2024-08-02", self._zone("z1")["irrigation_dates"])

    def test_cloud_gap_keeps_an_inferred_state_and_a_radar_correction(self):
        intervals = self._zone("z1")["intervals"]
        radar = [i for i in intervals if i["kind"] == "radar" and i["date"].startswith("2024-07")]
        inferred = [i for i in intervals if i["kind"] == "inferred" and "2024-07-0" <= i["date"] <= "2024-07-12"]
        self.assertTrue(radar)
        self.assertTrue(inferred)
        self.assertLess(min(i["uncertainty"] for i in radar), max(i["uncertainty"] for i in inferred))
        self.assertGreater(radar[0]["biomass_kg_ha"], 0)

    def test_yield_ensemble(self):
        yld = self._zone("z1")["yield"]
        self.assertIsNotNone(yld)
        self.assertGreater(yld["t_ha"], 0.2)
        self.assertLess(yld["t_ha"], 6.0)
        self.assertLessEqual(yld["low"], yld["t_ha"])
        self.assertGreaterEqual(yld["high"], yld["t_ha"])
        self.assertIn("biomass_hi", yld["estimators"])
        self.assertIn("peak_canopy", yld["estimators"])
        self.assertEqual(yld["reference_pool"], "district_statistic")
        self.assertGreater(self._zone("z1")["progress"]["tau"], 0.2)

    def test_low_confidence_withholds_yield(self):
        request, stack = soybean_mixed(2024)
        request.confidence = 0.2
        doc = run_monitoring(request, stack)
        self.assertFalse(doc["typed"])
        for zone in doc["zones"]:
            if zone["kind"] == "non_crop":
                continue
            self.assertIsNone(zone["yield"])
            self.assertTrue(zone["intervals"])

    def test_peer_pool(self):
        request, stack = soybean_mixed(2024)
        request.peer_integrals = [float(i) for i in range(15)]
        doc = run_monitoring(request, stack)
        yld = next(z["yield"] for z in doc["zones"] if z["zone_id"] == "z1")
        self.assertEqual(yld["reference_pool"], "district_peers")
        self.assertIn("peers", yld["estimators"])

    def test_no_cue_does_not_invent_sowing(self):
        estimate = fuse_sowing([], date(2024, 6, 1), date(2024, 7, 31))
        self.assertFalse(estimate.known)
        self.assertIsNone(estimate.date)

    def test_duration_outlier_widens_rather_than_stretching(self):
        prior = get_prior("Wheat")
        start = date(2023, 11, 15)
        rows = []
        for i in range(45):
            cover = 0.08
            if 8 <= i <= 28:
                cover = 0.72
            elif i > 28:
                cover = 0.12
            rows.append(StateDay(
                date=start + timedelta(days=i),
                cover=cover, water=0.6, biomass=400 + i, gain=8,
                uncertainty=0.2, kind="optical",
            ))
        sowing = SowingEstimate(True, start, start, start, 0.9, ["provided"], "")
        progress = measure(rows, sowing, prior)
        self.assertTrue(progress.duration_outlier)
        self.assertIsNotNone(progress.duration_days)
        self.assertLess(progress.duration_days, prior.min_days)

    def test_uniform_field_stays_one_zone(self):
        prior = get_prior("Wheat")
        day = date(2024, 1, 10)
        pixels = []
        for i in range(16):
            records = [
                PixelRecord(date=day, sensor="s2", ndvi=0.2),
                PixelRecord(date=day + timedelta(days=20), sensor="s2", ndvi=0.55),
                PixelRecord(date=day + timedelta(days=40), sensor="s2", ndvi=0.7),
            ]
            pixels.append(PixelTrack(f"p{i}", 75.0, 19.0, records))
        zones = [z for z in split_zones(pixels, prior) if z.kind == "crop"]
        self.assertEqual(len(zones), 1)

    def test_small_fields_use_fewer_sample_points_than_a_grid(self):
        from src.observe import points_for_field

        tiny = {
            "type": "Polygon",
            "coordinates": [[
                [74.50, 18.50], [74.5004, 18.50], [74.5004, 18.5004], [74.50, 18.5004], [74.50, 18.50],
            ]],
        }
        points = points_for_field(tiny)
        self.assertGreaterEqual(len(points), 1)
        self.assertLessEqual(len(points), 4)

    def test_many_fields_combine_into_one_document(self):
        from src.pipeline import combine_runs

        def one(fid: str, crop: str) -> tuple[dict, dict]:
            return (
                {"field_id": fid, "crop": crop, "area_ha": 0.2, "geometry": {"type": "Polygon", "coordinates": []}},
                {
                    "zones": [{
                        "zone_id": "z1",
                        "kind": "crop",
                        "crop": crop,
                        "sowing": {"date": "2024-06-20"},
                        "harvest": {"date": "2024-10-15"},
                        "yield": {"t_ha": 1.1},
                        "intervals": [{"date": "2024-09-01", "stress": {"type": "Healthy"}}],
                        "indices": [{"date": "2024-09-01", "ndvi": 0.7}],
                    }],
                    "fields": {"type": "FeatureCollection", "features": [{
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": []},
                        "properties": {"zone_id": "z1", "crop": f"z1 · {crop}", "color": "#2E7D4F"},
                    }]},
                },
            )

        doc = combine_runs(
            [one("a", "Cotton"), one("b", "Soyabean")],
            name="Dhaswadi", season="kharif", as_of="2024-10-01", lineage={},
        )
        self.assertEqual(doc["farm_count"], 2)
        self.assertEqual(doc["crop"], "multiple")
        self.assertEqual({z["zone_id"] for z in doc["zones"]}, {"a-z1", "b-z1"})
        self.assertNotIn("intervals", doc["zones"][0])
        self.assertIn("intervals", doc["parcel_zones"][0])
        self.assertEqual(len(doc["fields"]["features"]), 2)

    def test_each_classified_crop_keeps_its_own_calendar(self):
        wheat = get_prior("Wheat")
        rice = get_prior("Rice")
        soy = get_prior("Soyabean")
        cotton = get_prior("Cotton")
        lengths = {wheat.typical_days, rice.typical_days, soy.typical_days, cotton.typical_days}
        self.assertGreater(len(lengths), 1)
        self.assertLess(soy.typical_days, cotton.typical_days)
        self.assertIn("pod fill", [name for _, _, name in soy.stages])
        self.assertIn("boll", [name for _, _, name in cotton.stages])
        self.assertIn("grain fill", [name for _, _, name in wheat.stages])
        soy_months = self.doc["phenology"]["harvest_months"]
        self.assertEqual(len(soy_months), 2)
        self.assertEqual(self.doc["phenology"]["crop"], "Soyabean")

    def test_one_sowing_with_two_harvests_becomes_two_parcels(self):
        prior = get_prior("Soyabean")
        green = date(2024, 7, 1)
        peak = date(2024, 9, 1)
        early_crash = date(2024, 9, 25)
        late_crash = date(2024, 11, 5)
        days = [date(2024, 6, 10), green, date(2024, 8, 1), peak, early_crash, late_crash]

        def track(pid: str, crash: date, lon: float) -> PixelTrack:
            records = []
            for day in days:
                if day < green:
                    ndvi = 0.12
                elif day < peak:
                    ndvi = 0.55
                elif day < crash:
                    ndvi = 0.78
                else:
                    ndvi = 0.16
                records.append(PixelRecord(date=day, sensor="s2", ndvi=ndvi, ndmi=0.2))
            return PixelTrack(pid, lon, 18.5, records)

        pixels = [track(f"e{i}", early_crash, 74.50 + i * 0.0001) for i in range(14)]
        pixels += [track(f"l{i}", late_crash, 74.52 + i * 0.0001) for i in range(14)]
        zone = Zone("z1", "crop", pixels, green, "boundary")
        parts = split_harvest(zone, prior)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.split_reason == "harvest" for part in parts))
        self.assertTrue(all(part.greenup == green for part in parts))

    def test_bare_boundary_is_removed_from_the_crop(self):
        from src.models import MonitorRequest, ObservationStack, WeatherDay

        start = date(2024, 6, 1)
        pixels = []
        for i in range(8):
            records = []
            day = start
            while day <= date(2024, 10, 1):
                if (day - start).days % 10 == 0:
                    records.append(PixelRecord(date=day, sensor="s2", ndvi=0.12, ndmi=0.02))
                day += timedelta(days=1)
            pixels.append(PixelTrack(f"b{i}", 74.50 + i * 0.0001, 18.50, records))
        weather = [
            WeatherDay(date=start + timedelta(days=i), rain_p50=2, tmean=28, tmax=32, tmin=24)
            for i in range(140)
        ]
        request = MonitorRequest(
            crop="Cotton", confidence=0.9, season="kharif", as_of=date(2024, 10, 15),
            geometry={"type": "Polygon", "coordinates": [[
                [74.50, 18.50], [74.51, 18.50], [74.51, 18.51], [74.50, 18.51], [74.50, 18.50],
            ]]},
        )
        doc = run_monitoring(request, ObservationStack(pixels, weather))
        self.assertFalse(doc["accepted"])
        self.assertEqual(doc["fields"]["features"], [])
        self.assertIn("bare", (doc["exclusion_reason"] or "").lower())


class AddedSourceTests(unittest.TestCase):
    def test_coarse_ndvi_fills_a_day_without_a_field_view(self):
        from src.sowing import SowingEstimate
        from src.state import simulate

        zone = Zone("z1", "crop", [PixelTrack("p", 74.5, 18.5, [])], date(2024, 6, 20))
        sowing = SowingEstimate(
            True, date(2024, 6, 1), date(2024, 6, 1), date(2024, 6, 3),
            0.8, ["optical"], "",
        )
        rows = simulate(
            zone, [], sowing, get_prior("Soyabean"),
            date(2024, 6, 15), date(2024, 6, 15),
            {date(2024, 6, 15): 0.62},
        )
        self.assertEqual(rows[0].kind, "coarse")
        self.assertGreater(rows[0].cover, 0.1)

    def test_power_fills_only_missing_weather(self):
        from src.observe import _merge_power

        day = date(2024, 7, 1)
        filled = WeatherDay(date=day, rain_p50=12, tmean=30)
        days = {day: filled}
        _merge_power(
            days,
            {day: {"tmean": 18, "tmax": 22, "tmin": 16, "rain": 40}},
            tempered={day},
            gauged={day},
        )
        self.assertEqual(days[day].tmean, 30)
        self.assertEqual(days[day].rain_p50, 12)
        gap = date(2024, 7, 2)
        _merge_power(days, {gap: {"tmean": 26, "rain": 4}}, tempered=set(), gauged=set())
        self.assertEqual(days[gap].tmean, 26)
        self.assertEqual(days[gap].rain_p50, 4)

    def test_village_peers_replace_the_crop_curve(self):
        from src.yield_model import with_village_peers

        updated = with_village_peers({
            "t_ha": 1.2,
            "low": 0.8,
            "high": 1.6,
            "estimators": {"biomass_hi": 1.1, "peak_canopy": 1.3},
            "reference_pool": "crop_shape",
            "baseline_t_ha": 1.2,
            "cover_integral": 40,
            "keep": 0.9,
            "band_inputs": {"uncertainty": 0.2, "span": 7, "outlier": False, "before_peak": False},
        }, [float(i) for i in range(20, 40)])
        self.assertEqual(updated["reference_pool"], "village_peers")
        self.assertIn("peers", updated["estimators"])
        self.assertNotIn("band_inputs", updated)


class CompositeReadTests(unittest.TestCase):
    def test_period_step_stays_coarse_enough_for_one_read(self):
        from src.observe import period_length

        self.assertEqual(period_length(date(2024, 6, 1), date(2024, 8, 30)), 10)
        self.assertGreaterEqual(period_length(date(2024, 5, 1), date(2024, 11, 1)), 10)
        self.assertLessEqual(period_length(date(2024, 5, 1), date(2025, 5, 1)), 30)

    def test_stacked_bands_become_one_row_per_date(self):
        from src.observe import unstack_features

        info = unstack_features({
            "features": [{
                "properties": {
                    "pixel_id": "f1::p00",
                    "0_d20240615_NDVI": 0.22,
                    "0_d20240615_NDMI": 0.05,
                    "1_d20240715_NDVI": 0.61,
                    "1_d20240715_NDMI": None,
                },
            }],
        })
        rows = info["features"]
        self.assertEqual(len(rows), 2)
        june = next(row["properties"] for row in rows if row["properties"]["date"] == "2024-06-15")
        self.assertEqual(june["NDVI"], 0.22)
        self.assertEqual(june["pixel_id"], "f1::p00")
        july = next(row["properties"] for row in rows if row["properties"]["date"] == "2024-07-15")
        self.assertNotIn("NDMI", july)


if __name__ == "__main__":
    unittest.main()
