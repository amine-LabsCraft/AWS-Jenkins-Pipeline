import json

import pandas as pd
import pytest

from sunops import canary, demo

KEYS = ["array_id", "date_day"]
METRICS = ["energy_kwh", "performance_ratio"]


def statuses(checks):
    return {c.check: c.status for c in checks}


def test_identical_tables_promote(daily):
    checks = canary.compare_tables("t", daily, daily.copy(), KEYS, METRICS, {})
    assert {c.status for c in checks} == {"PASS"}
    report = canary.CanaryReport(checks=checks)
    assert report.score == 100 and report.verdict == "PROMOTE"


def test_row_order_and_column_order_do_not_matter(daily):
    shuffled = daily[list(reversed(daily.columns))].sample(frac=1.0, random_state=1)
    checks = canary.compare_tables("t", daily, shuffled, KEYS, METRICS, {})
    assert {c.status for c in checks} == {"PASS"}


def test_lost_rows_fail(daily):
    s = statuses(canary.compare_tables("t", daily, daily.iloc[:-10], KEYS, METRICS, {}))
    assert s["row_count"] == "FAIL" and s["key_coverage"] == "FAIL"


def test_metric_drift_is_caught_even_with_same_row_count(daily):
    drifted = daily.copy()
    drifted["energy_kwh"] = drifted["energy_kwh"] * 1.03
    s = statuses(canary.compare_tables("t", daily, drifted, KEYS, METRICS, {}))
    assert s["row_count"] == "PASS"
    assert s["metric_sum:energy_kwh"] == "FAIL"
    assert s["value_mismatch:energy_kwh"] == "FAIL"


def test_removed_column_fails_added_column_warns(daily):
    s = statuses(canary.compare_tables("t", daily, daily.drop(columns=["peak_power_kw"]), KEYS, METRICS, {}))
    assert s["schema"] == "FAIL"
    s = statuses(canary.compare_tables("t", daily, daily.assign(extra=1), KEYS, METRICS, {}))
    assert s["schema"] == "WARN"


def test_new_nulls_fail(daily):
    worse = daily.copy()
    worse.loc[worse.sample(frac=0.1, random_state=3).index, "performance_ratio"] = None
    s = statuses(canary.compare_tables("t", daily, worse, KEYS, METRICS, {}))
    assert s["null_rate"] == "FAIL"


def test_duplicated_keys_fail(daily):
    dup = pd.concat([daily, daily.head(1)])
    s = statuses(canary.compare_tables("t", daily, dup, KEYS, METRICS, {}))
    assert s["key_coverage"] == "FAIL"


def test_low_score_blocks_without_fail():
    checks = [canary.CheckResult("t", f"c{i}", "WARN", "") for i in range(5)]
    report = canary.CanaryReport(checks=checks, min_score=85)
    assert report.score == 60 and report.verdict == "BLOCK"


def test_dropped_table_blocks(daily):
    config = {"tables": [{"name": "fct", "keys": KEYS, "metrics": METRICS}]}
    report = canary.run_canary(config, lambda t: daily, lambda t: None)
    assert report.verdict == "BLOCK"


@pytest.mark.parametrize(
    "scenario, verdict",
    [("lost-dedup", "BLOCK"), ("unit-bug", "BLOCK"), ("renamed-column", "BLOCK"), ("safe-refactor", "PROMOTE")],
)
def test_demo_scenarios(tmp_path, scenario, verdict):
    report = demo.run(scenario, tmp_path, days=3)
    assert report.verdict == verdict
    saved = json.loads((tmp_path / "report" / "canary.json").read_text(encoding="utf-8"))
    assert saved["verdict"] == verdict
    assert (tmp_path / "report" / "index.html").read_text(encoding="utf-8").startswith("<!doctype html>")
