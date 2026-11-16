"""Offline demo of the Data Canary: no Docker, no Spark, no database.

It plays the scenario the canary is built for:
  1. generate one week of telemetry (with realistic dirt) from a fixed seed;
  2. build the Gold tables with the code on `main` (baseline);
  3. build them again with a "pull request" (candidate);
  4. diff both outputs and print PROMOTE or BLOCK.

Scenarios:
  lost-dedup      a refactor of the Silver job forgets to drop duplicate readings.
                  Unit tests still pass, row counts are identical, but energy is inflated.
  unit-bug        energy computed per hour instead of per 15-minute interval (x4).
  renamed-column  `performance_ratio` renamed to `pr`: dashboards would break.
  safe-refactor   the code changed, the data did not. The canary lets it through.

Usage:
    python -m sunops.demo --scenario lost-dedup
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from sunops import canary, reference
from sunops.contracts import load_contract
from sunops.generator import generate

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_START = date(2026, 3, 2)
SCENARIOS = ("lost-dedup", "unit-bug", "renamed-column", "safe-refactor")


def build_gold(records, contract, scenario: str | None):
    silver, _ = reference.bronze_to_silver(records, contract, dedupe=scenario != "lost-dedup")
    interval_h = 1.0 if scenario == "unit-bug" else reference.INTERVAL_H
    daily = reference.daily_performance(silver, interval_h=interval_h)
    reco = reference.cleaning_recommendations(daily)
    if scenario == "renamed-column":
        daily = daily.rename(columns={"performance_ratio": "pr"})
    if scenario == "safe-refactor":
        daily = daily[list(reversed(daily.columns))].sample(frac=1.0, random_state=7)  # same data, new order
    return {"fct_daily_performance": daily, "mart_cleaning_recommendations": reco}


def run(scenario: str, out: Path, days: int = 7) -> canary.CanaryReport:
    contract = load_contract(ROOT / "contracts" / "telemetry.yml")
    config = yaml.safe_load((ROOT / "canary" / "thresholds.yml").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        bronze = Path(tmp) / "bronze"
        stats = generate(GOLDEN_START, days, bronze, seed=42, chaos=0.01)
        records = reference.read_bronze(bronze)
    print(f"[demo] golden sample: {stats.rows} readings, {stats.duplicates} duplicates, {stats.corrupted} corrupted")

    baseline = build_gold(records, contract, None)
    candidate = build_gold(records, contract, scenario)
    for side, tables in (("baseline", baseline), ("candidate", candidate)):
        d = out / side
        d.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            df.to_csv(d / f"{name}.csv", index=False)

    report = canary.run_canary(
        config,
        lambda t: canary.load_from_dir(out / "baseline", t),
        lambda t: canary.load_from_dir(out / "candidate", t),
    )
    report.meta.update({"scenario": scenario, "golden_sample": f"{GOLDEN_START} +{days}d"})
    canary.write_reports(report, out / "report")
    return report


def main(argv: list[str] | None = None) -> int:
    canary.utf8_console()
    p = argparse.ArgumentParser(description="SunOps Data Canary offline demo")
    p.add_argument("--scenario", choices=SCENARIOS, default="lost-dedup")
    p.add_argument("--out", type=Path, default=Path("reports/demo"))
    args = p.parse_args(argv)

    report = run(args.scenario, args.out)
    print(canary.to_markdown(report))
    print(f"[demo] HTML report: {(args.out / 'report' / 'index.html').resolve()}")
    return 0 if report.verdict == "PROMOTE" else 1


if __name__ == "__main__":
    sys.exit(main())
