"""Pandas reference implementation of the Silver and Gold logic.

The production path is Spark (Bronze -> Silver) + dbt on PostgreSQL (Silver -> Gold).
This module reproduces the same rules in pandas so that:
  * the offline demo (`python -m sunops.demo`) runs without Docker, Spark or Postgres;
  * unit tests can pin the business rules (performance ratio, soiling, cleaning).
Keep it in sync with spark_jobs/bronze_to_silver.py and dbt/models/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from sunops.contracts import validate_record
from sunops.plant import ARRAYS, INTERVAL_MINUTES

TARIFF_MAD_PER_KWH = 0.85
INTERVAL_H = INTERVAL_MINUTES / 60


def read_bronze(path: Path) -> list[dict]:
    files = [path] if path.is_file() else sorted(Path(path).rglob("*.jsonl"))
    rows: list[dict] = []
    for f in files:
        with f.open(encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    return rows


def bronze_to_silver(records: list[dict], contract: dict, dedupe: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (silver, quarantine). Mirrors spark_jobs/bronze_to_silver.py."""
    valid, quarantined = [], []
    for rec in records:
        errors = validate_record(rec, contract)
        (quarantined if errors else valid).append({**rec, "_errors": errors} if errors else rec)

    silver = pd.DataFrame(valid)
    if silver.empty:
        return silver, pd.DataFrame(quarantined)
    silver["ts"] = pd.to_datetime(silver["ts"], utc=True)
    silver["ingested_at"] = pd.to_datetime(silver["ingested_at"], utc=True)
    if dedupe:
        silver = (
            silver.sort_values("ingested_at", ascending=False)
            .drop_duplicates(subset=contract["primary_key"], keep="first")
            .sort_values(["ts", "array_id"])
        )
    silver["date_day"] = silver["ts"].dt.date
    return silver.reset_index(drop=True), pd.DataFrame(quarantined)


def arrays_frame() -> pd.DataFrame:
    return pd.DataFrame([a.__dict__ for a in ARRAYS])


def daily_performance(silver: pd.DataFrame, interval_h: float = INTERVAL_H) -> pd.DataFrame:
    """Gold: fct_daily_performance (one row per array and day)."""
    df = silver.merge(arrays_frame()[["array_id", "capacity_kw"]], on="array_id", how="left")
    df["energy_kwh"] = df["ac_power_kw"] * interval_h
    df["reference_kwh"] = df["capacity_kw"] * df["irradiance_wm2"] / 1000.0 * interval_h
    df["is_daylight"] = df["irradiance_wm2"] > 50
    df["is_offline"] = df["is_daylight"] & (df["status"] == "OFFLINE")
    df["is_degraded"] = df["is_daylight"] & (df["status"] == "DEGRADED")
    df["module_temp_day"] = df["module_temp_c"].where(df["is_daylight"])

    g = df.groupby(["array_id", "zone", "date_day"], as_index=False).agg(
        energy_kwh=("energy_kwh", "sum"),
        reference_kwh=("reference_kwh", "sum"),
        peak_power_kw=("ac_power_kw", "max"),
        avg_module_temp_c=("module_temp_day", "mean"),
        daylight_intervals=("is_daylight", "sum"),
        offline_intervals=("is_offline", "sum"),
        degraded_intervals=("is_degraded", "sum"),
    )
    g["performance_ratio"] = (g["energy_kwh"] / g["reference_kwh"]).where(g["reference_kwh"] > 0)
    g["degraded_share"] = (g["degraded_intervals"] / g["daylight_intervals"]).where(g["daylight_intervals"] > 0).fillna(0.0)
    for col in ("energy_kwh", "reference_kwh", "peak_power_kw", "avg_module_temp_c"):
        g[col] = g[col].round(3)
    g["performance_ratio"] = g["performance_ratio"].round(4)
    g["degraded_share"] = g["degraded_share"].round(4)
    return g.sort_values(["date_day", "array_id"]).reset_index(drop=True)


def cleaning_recommendations(daily: pd.DataFrame, tariff: float = TARIFF_MAD_PER_KWH) -> pd.DataFrame:
    """Gold: mart_cleaning_recommendations.

    Weather affects every array the same way on a given day, so each array is compared with
    the best array of the same day (usually a freshly cleaned one). The gap is the soiling loss.
    Arrays whose loss comes from an electrical fault (DEGRADED most of the day) are sent to
    INSPECT: cleaning them would waste water and robot time.
    """
    d = daily.copy()
    best = d.groupby("date_day")["performance_ratio"].transform("max")
    d["relative_pr"] = (d["performance_ratio"] / best).round(4)
    d["soiling_loss_pct"] = ((1 - d["relative_pr"]) * 100).round(2)
    d["lost_energy_kwh"] = (d["reference_kwh"] * (best - d["performance_ratio"])).clip(lower=0).round(2)
    d["lost_revenue_mad"] = (d["lost_energy_kwh"] * tariff).round(2)

    def decide(row) -> str:
        if row["degraded_share"] > 0.5:
            return "INSPECT"
        if row["soiling_loss_pct"] >= 6:
            return "CLEAN_NOW"
        if row["soiling_loss_pct"] >= 3:
            return "SCHEDULE"
        return "OK"

    d["recommendation"] = d.apply(decide, axis=1)
    cols = [
        "array_id", "zone", "date_day", "performance_ratio", "relative_pr",
        "soiling_loss_pct", "lost_energy_kwh", "lost_revenue_mad", "recommendation",
    ]
    return d[cols].sort_values(["date_day", "lost_revenue_mad"], ascending=[True, False]).reset_index(drop=True)
