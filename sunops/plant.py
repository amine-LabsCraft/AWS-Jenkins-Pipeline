"""Layout of the fictional SunOps solar plant (OZZ-1, near Ouarzazate, Morocco).

3 zones x 2 inverters x 4 panel arrays = 24 arrays of 250 kWc, about 6 MWc in total.
Each zone has its own cleaning robot, which passes on a fixed weekday.
Zone C's robot is "broken" for the whole simulation, so its panels keep
accumulating dust. The cleaning-recommendation mart should spot that.
"""
from __future__ import annotations

from dataclasses import dataclass

PLANT_ID = "OZZ-1"
INTERVAL_MINUTES = 15
CAPACITY_KW = 250.0

# Weekday the cleaning robot passes (0 = Monday). None = robot out of service.
CLEANING_WEEKDAY = {"A": 0, "B": 3, "C": None}

# One array with a real electrical fault (not dust): it should be INSPECTED, not cleaned.
FAULTY_ARRAY = "B-INV2-S3"


@dataclass(frozen=True)
class Array:
    array_id: str
    zone: str
    inverter_id: str
    capacity_kw: float
    tilt_deg: int
    commissioned_on: str
    cleaning_weekday: int | None


def build_arrays() -> list[Array]:
    arrays = []
    for zone_idx, zone in enumerate("ABC"):
        for inv in (1, 2):
            for s in (1, 2, 3, 4):
                arrays.append(
                    Array(
                        array_id=f"{zone}-INV{inv}-S{s}",
                        zone=zone,
                        inverter_id=f"{zone}-INV{inv}",
                        capacity_kw=CAPACITY_KW,
                        tilt_deg=30 + zone_idx,
                        commissioned_on=f"202{2 + zone_idx}-0{inv + 2}-15",
                        cleaning_weekday=CLEANING_WEEKDAY[zone],
                    )
                )
    return arrays


ARRAYS: list[Array] = build_arrays()
