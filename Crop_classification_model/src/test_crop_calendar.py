"""
Checks for season-aware label attribution (src/crop_calendar.py).

    ../.conda/python.exe -m pytest src/test_crop_calendar.py -q
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from src.crop_calendar import pick_cycle_by_season, season_instances


@dataclass
class Cyc:
    peak: str
    duration_days: float = 120

    @property
    def peak_date(self):
        return datetime.strptime(self.peak, "%Y-%m-%d")


def test_gram_november_survey_skips_the_kharif_tail():
    # A 10-Nov gram survey: the kharif cycle peaking in September CONTAINS
    # the survey date, but it is not the gram crop.
    cycles = [Cyc("2022-09-05"), Cyc("2023-01-15")]
    idx, tag, inst = pick_cycle_by_season(cycles, "Gram", date(2022, 11, 10), 120)
    assert idx == 1 and tag == "season_rabi"
    assert inst.peak_start == date(2022, 12, 1)


def test_tobacco_june_survey_picks_kharif_or_rabi_by_peak():
    cycles = [Cyc("2024-08-10"), Cyc("2025-01-05")]
    idx, tag, _ = pick_cycle_by_season(cycles, "Tobacco", date(2024, 6, 10), 150)
    assert idx == 0 and tag == "season_kharif"


def test_rice_kharif_vs_boro():
    cycles = [Cyc("2022-09-20"), Cyc("2023-03-10")]
    assert pick_cycle_by_season(cycles, "Rice", date(2022, 9, 10), 120)[0] == 0


def test_no_consistent_cycle_returns_none():
    idx, tag, _ = pick_cycle_by_season([Cyc("2023-06-01")], "Wheat", date(2023, 1, 10), 130)
    assert idx is None and tag == "no_cycle_in_season"


def test_perennials_have_no_calendar():
    assert season_instances("Sugarcane", date(2023, 1, 10)) == []
