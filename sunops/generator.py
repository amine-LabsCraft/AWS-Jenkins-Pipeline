"""Telemetry simulator for the OZZ-1 solar plant.

Writes one JSON line per array every 15 minutes into the Bronze zone, partitioned
by ingestion date:  <out>/ingest_date=YYYY-MM-DD/telemetry.jsonl

The physics is simple but plausible:
  * irradiance follows a sun curve, modulated by the day's weather;
  * "Chergui" days (hot, dusty wind from the Sahara) raise the temperature and
    speed up panel soiling; sandstorms cut irradiance in the afternoon;
  * dust lowers output a little each day until a cleaning robot passes;
  * panels lose ~0.4 % of power per degree above 25 °C.

With --chaos > 0, the generator also produces the kind of dirt real IoT feeds have
(duplicates, missing values, impossible readings, unknown devices) so that the
data contract and the Silver layer have something to catch.

Usage:
    python -m sunops.generator --days 7 --start 2026-03-02 --out lake/bronze/telemetry
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sunops.plant import ARRAYS, FAULTY_ARRAY, INTERVAL_MINUTES, PLANT_ID, Array

SUNRISE_UTC, SUNSET_UTC = 6.0, 18.75
WEATHER = {
    #            probability, irradiance factor, extra °C, daily soiling rate
    "clear": (0.68, 1.00, 0.0, 0.004),
    "cloudy": (0.10, 0.62, -3.0, 0.002),
    "chergui": (0.15, 0.90, 9.0, 0.015),
    "sandstorm": (0.07, 0.85, 4.0, 0.035),
}


@dataclass
class GenerationStats:
    rows: int = 0
    duplicates: int = 0
    corrupted: int = 0
    weather: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


def day_weather(rng: random.Random) -> str:
    r, acc = rng.random(), 0.0
    for name, (p, *_rest) in WEATHER.items():
        acc += p
        if r <= acc:
            return name
    return "clear"


def clear_sky_irradiance(hour: float) -> float:
    """Global irradiance on the panels (W/m2) for a clear day in the Moroccan south."""
    if hour <= SUNRISE_UTC or hour >= SUNSET_UTC:
        return 0.0
    x = (hour - SUNRISE_UTC) / (SUNSET_UTC - SUNRISE_UTC)
    return 1020.0 * math.sin(math.pi * x) ** 1.2


def ambient_temperature(hour: float, extra: float) -> float:
    return 17.0 + 11.0 * math.sin(math.pi * (hour - 9.0) / 12.0) + extra


def simulate_reading(
    array: Array,
    ts: datetime,
    weather: str,
    soiling: float,
    rng: random.Random,
) -> dict:
    hour = ts.hour + ts.minute / 60
    _, irr_factor, extra_temp, _ = WEATHER[weather]

    irradiance = clear_sky_irradiance(hour) * irr_factor
    if weather == "cloudy":
        irradiance *= rng.uniform(0.55, 1.15)
    if weather == "sandstorm" and 13 <= hour <= 17:
        irradiance *= 0.45
    irradiance = max(0.0, irradiance * rng.uniform(0.98, 1.02))

    ambient = ambient_temperature(hour, extra_temp) + rng.uniform(-0.6, 0.6)
    module = ambient + irradiance / 800.0 * 26.0
    temp_factor = 1 - 0.004 * max(0.0, module - 25.0)

    health = 0.72 if array.array_id == FAULTY_ARRAY else 1.0
    dc_kw = array.capacity_kw * irradiance / 1000.0 * soiling * temp_factor * health * 0.95
    status = "DEGRADED" if health < 1 and irradiance > 50 else "OK"
    if irradiance > 50 and rng.random() < 0.002:
        status, dc_kw = "OFFLINE", 0.0

    voltage = rng.uniform(610, 690) if irradiance > 20 and dc_kw > 0 else 0.0
    current = dc_kw * 1000.0 / voltage if voltage else 0.0
    ac_kw = dc_kw * 0.975

    return {
        "event_id": f"{array.array_id}-{ts:%Y%m%d%H%M}",
        "plant_id": PLANT_ID,
        "zone": array.zone,
        "inverter_id": array.inverter_id,
        "array_id": array.array_id,
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "irradiance_wm2": round(irradiance, 2),
        "ambient_temp_c": round(ambient, 2),
        "module_temp_c": round(module, 2),
        "dc_voltage_v": round(voltage, 1),
        "dc_current_a": round(current, 2),
        "ac_power_kw": round(ac_kw, 3),
        "status": status,
    }


def corrupt(record: dict, rng: random.Random) -> dict:
    """Return a copy of the record with one realistic defect."""
    bad = dict(record)
    defect = rng.choice(["null_irradiance", "negative_power", "stuck_sensor", "unknown_array"])
    if defect == "null_irradiance":
        bad["irradiance_wm2"] = None
    elif defect == "negative_power":
        bad["ac_power_kw"] = -abs(bad["ac_power_kw"]) - 1.0
    elif defect == "stuck_sensor":
        bad["module_temp_c"] = 9999.0
    else:
        bad["array_id"] = "X-INV9-S9"
    return bad


def generate(
    start: date,
    days: int,
    out_dir: Path,
    seed: int = 42,
    chaos: float = 0.01,
) -> GenerationStats:
    rng = random.Random(seed)
    stats = GenerationStats()
    out_dir = Path(out_dir)

    # Soiling factor per array: 1.0 = perfectly clean.
    soiling = {a.array_id: 1.0 - rng.uniform(0.0, 0.015) for a in ARRAYS}
    steps_per_day = 24 * 60 // INTERVAL_MINUTES

    for d in range(days):
        day = start + timedelta(days=d)
        weather = day_weather(random.Random(f"{seed}-{day.isoformat()}"))
        stats.weather[day.isoformat()] = weather
        soiling_rate = WEATHER[weather][3]

        for a in ARRAYS:
            if a.cleaning_weekday is not None and day.weekday() == a.cleaning_weekday:
                soiling[a.array_id] = 1.0 - rng.uniform(0.0, 0.004)
            else:
                soiling[a.array_id] = max(0.6, soiling[a.array_id] - soiling_rate * rng.uniform(0.8, 1.2))

        partition = out_dir / f"ingest_date={day.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        path = partition / "telemetry.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for step in range(steps_per_day):
                ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(
                    minutes=step * INTERVAL_MINUTES
                )
                for a in ARRAYS:
                    rec = simulate_reading(a, ts, weather, soiling[a.array_id], rng)
                    rec["ingested_at"] = (ts + timedelta(seconds=rng.randint(5, 240))).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    if chaos and rng.random() < chaos:
                        rec = corrupt(rec, rng)
                        stats.corrupted += 1
                    fh.write(json.dumps(rec) + "\n")
                    stats.rows += 1
                    if chaos and rng.random() < chaos:
                        # The gateway re-sends the same reading a bit later.
                        dup = dict(rec, ingested_at=(ts + timedelta(minutes=7)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                        fh.write(json.dumps(dup) + "\n")
                        stats.rows += 1
                        stats.duplicates += 1
        stats.files.append(str(path))
    return stats


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate OZZ-1 solar telemetry (Bronze).")
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="first day (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("lake/bronze/telemetry"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--chaos", type=float, default=0.01, help="share of corrupted / duplicated rows")
    args = parser.parse_args(argv)

    start = args.start or (date.today() - timedelta(days=args.days))
    stats = generate(start, args.days, args.out, seed=args.seed, chaos=args.chaos)
    print(
        f"[generator] {stats.rows} rows over {args.days} days "
        f"({stats.duplicates} duplicates, {stats.corrupted} corrupted) -> {args.out}"
    )
    for day, weather in stats.weather.items():
        print(f"  {day}: {weather}")


if __name__ == "__main__":
    main()
