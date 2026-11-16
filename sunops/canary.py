"""Data Canary: diff the data, not only the code.

Before a pipeline change is deployed, Jenkins runs two versions of the pipeline on the
same frozen "golden" sample:
  * baseline  = the code currently on `main`
  * candidate = the code in the branch / pull request
and this module compares the tables they produce. Unit tests check that the code
does what the author thinks. The canary checks that the *data* did not change
in ways nobody intended (lost rows, a metric that drifts by 3 %, a column that
disappeared, nulls that crept in).

Checks per table:
  schema          columns removed or retyped -> FAIL, columns added -> WARN
  row_count       relative delta of the number of rows
  key_coverage    keys present on one side only
  null_rate       increase of the share of NULL per column (percentage points)
  metric_sum      relative delta of SUM(metric)
  value_mismatch  share of joined rows where a metric differs beyond tolerance

Each check is PASS (100), WARN (60) or FAIL (0). The canary score is the average.
Verdict = PROMOTE if no FAIL and score >= min_score, otherwise BLOCK (exit code 1).

CLI (Postgres, used by Jenkins):
    python -m sunops.canary --config canary/thresholds.yml \
        --dsn "$SUNOPS_PG_DSN" --baseline-schema canary_baseline --candidate-schema canary_candidate
CLI (files, used by the offline demo):
    python -m sunops.canary --config canary/thresholds.yml --baseline-dir a/ --candidate-dir b/
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

STATUS_POINTS = {"PASS": 100, "WARN": 60, "FAIL": 0}

DEFAULT_THRESHOLDS = {
    "row_count_warn_pct": 0.5,
    "row_count_fail_pct": 2.0,
    "key_missing_fail_pct": 0.5,
    "null_rate_warn_pp": 0.5,
    "null_rate_fail_pp": 2.0,
    "metric_sum_warn_pct": 0.5,
    "metric_sum_fail_pct": 2.0,
    "value_mismatch_warn_pct": 1.0,
    "value_mismatch_fail_pct": 5.0,
    "value_rel_tolerance": 0.001,
    "min_score": 85,
}


@dataclass
class CheckResult:
    table: str
    check: str
    status: str
    observed: str
    detail: str = ""


@dataclass
class CanaryReport:
    checks: list[CheckResult] = field(default_factory=list)
    min_score: float = 85
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        if not self.checks:
            return 100.0
        return round(sum(STATUS_POINTS[c.status] for c in self.checks) / len(self.checks), 1)

    @property
    def verdict(self) -> str:
        if any(c.status == "FAIL" for c in self.checks) or self.score < self.min_score:
            return "BLOCK"
        return "PROMOTE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "score": self.score,
            "min_score": self.min_score,
            "meta": self.meta,
            "checks": [asdict(c) for c in self.checks],
        }


def _grade(value: float, warn: float, fail: float) -> str:
    if value > fail:
        return "FAIL"
    if value > warn:
        return "WARN"
    return "PASS"


def _pct(new: float, old: float) -> float:
    if old == 0:
        return 0.0 if new == 0 else 100.0
    return abs(new - old) / abs(old) * 100.0


def compare_tables(
    name: str,
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    keys: list[str],
    metrics: list[str],
    th: dict[str, float],
) -> list[CheckResult]:
    th = {**DEFAULT_THRESHOLDS, **(th or {})}
    out: list[CheckResult] = []

    # 1. schema -----------------------------------------------------------------------
    b_cols, c_cols = set(baseline.columns), set(candidate.columns)
    removed, added = sorted(b_cols - c_cols), sorted(c_cols - b_cols)
    retyped = sorted(
        col
        for col in b_cols & c_cols
        if _kind(baseline[col]) != _kind(candidate[col]) and not (baseline[col].isna().all() or candidate[col].isna().all())
    )
    if removed or retyped:
        status = "FAIL"
    elif added:
        status = "WARN"
    else:
        status = "PASS"
    parts = []
    if removed:
        parts.append(f"removed {removed}")
    if retyped:
        parts.append(f"retyped {retyped}")
    if added:
        parts.append(f"added {added}")
    out.append(CheckResult(name, "schema", status, "; ".join(parts) or "identical"))

    # 2. row count --------------------------------------------------------------------
    delta = _pct(len(candidate), len(baseline))
    out.append(
        CheckResult(
            name,
            "row_count",
            _grade(delta, th["row_count_warn_pct"], th["row_count_fail_pct"]),
            f"{len(baseline)} -> {len(candidate)} ({delta:.2f} %)",
        )
    )

    usable_keys = [k for k in keys if k in b_cols and k in c_cols]
    if usable_keys:
        b_idx = baseline[usable_keys].astype(str).agg("|".join, axis=1)
        c_idx = candidate[usable_keys].astype(str).agg("|".join, axis=1)
        b_set, c_set = set(b_idx), set(c_idx)
        missing = len(b_set - c_set)
        extra = len(c_set - b_set)
        dup = int(c_idx.duplicated().sum())
        miss_pct = 100.0 * (missing + extra) / max(len(b_set), 1)
        status = "FAIL" if (miss_pct > th["key_missing_fail_pct"] or dup) else ("WARN" if missing or extra else "PASS")
        out.append(
            CheckResult(
                name,
                "key_coverage",
                status,
                f"{missing} missing, {extra} new, {dup} duplicated keys",
                f"keys = {usable_keys}",
            )
        )

    # 3. null rate --------------------------------------------------------------------
    worst_col, worst_pp = None, 0.0
    for col in sorted(b_cols & c_cols):
        pp = (candidate[col].isna().mean() - baseline[col].isna().mean()) * 100.0 if len(candidate) and len(baseline) else 0.0
        if pp > worst_pp:
            worst_col, worst_pp = col, pp
    out.append(
        CheckResult(
            name,
            "null_rate",
            _grade(worst_pp, th["null_rate_warn_pp"], th["null_rate_fail_pp"]),
            f"+{worst_pp:.2f} pp on {worst_col}" if worst_col else "no increase",
        )
    )

    # 4. metrics ----------------------------------------------------------------------
    joined = None
    if usable_keys:
        joined = baseline.merge(candidate, on=usable_keys, how="inner", suffixes=("__b", "__c"))
    for m in metrics:
        if m not in b_cols or m not in c_cols:
            continue  # already reported by the schema check
        b_sum = float(pd.to_numeric(baseline[m], errors="coerce").sum())
        c_sum = float(pd.to_numeric(candidate[m], errors="coerce").sum())
        d = _pct(c_sum, b_sum)
        out.append(
            CheckResult(
                name,
                f"metric_sum:{m}",
                _grade(d, th["metric_sum_warn_pct"], th["metric_sum_fail_pct"]),
                f"{b_sum:,.2f} -> {c_sum:,.2f} ({d:.2f} %)",
            )
        )
        if joined is not None and len(joined):
            bv = pd.to_numeric(joined[f"{m}__b"], errors="coerce")
            cv = pd.to_numeric(joined[f"{m}__c"], errors="coerce")
            tol = th["value_rel_tolerance"] * bv.abs().clip(lower=1e-9)
            diff = (cv - bv).abs() > tol
            diff |= bv.isna() != cv.isna()
            rate = 100.0 * float(diff.mean())
            out.append(
                CheckResult(
                    name,
                    f"value_mismatch:{m}",
                    _grade(rate, th["value_mismatch_warn_pct"], th["value_mismatch_fail_pct"]),
                    f"{int(diff.sum())} / {len(joined)} rows differ ({rate:.2f} %)",
                )
            )
    return out


def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    return "text"


# --------------------------------------------------------------------------- loading
def load_from_dir(directory: Path, table: str) -> pd.DataFrame | None:
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        p = Path(directory) / f"{table}{ext}"
        if p.exists():
            return reader(p)
    return None


def load_from_postgres(dsn: str, schema: str, table: str) -> pd.DataFrame | None:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(dsn)
    if not inspect(engine).has_table(table, schema=schema):
        return None
    return pd.read_sql_table(table, engine, schema=schema)


def run_canary(config: dict[str, Any], load_baseline, load_candidate) -> CanaryReport:
    th = {**DEFAULT_THRESHOLDS, **config.get("thresholds", {})}
    report = CanaryReport(min_score=th["min_score"])
    for t in config["tables"]:
        b, c = load_baseline(t["name"]), load_candidate(t["name"])
        if b is None and c is None:
            report.checks.append(CheckResult(t["name"], "exists", "WARN", "missing on both sides"))
            continue
        if c is None:
            report.checks.append(CheckResult(t["name"], "exists", "FAIL", "table dropped by candidate"))
            continue
        if b is None:
            report.checks.append(CheckResult(t["name"], "exists", "WARN", "new table (no baseline)"))
            continue
        report.checks.extend(compare_tables(t["name"], b, c, t.get("keys", []), t.get("metrics", []), th))
    return report


# --------------------------------------------------------------------------- rendering
def to_markdown(report: CanaryReport) -> str:
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    lines = [
        f"## Data Canary: **{report.verdict}** (score {report.score} / 100, minimum {report.min_score})",
        "",
        "| Table | Check | Status | Observed |",
        "|---|---|---|---|",
    ]
    for c in report.checks:
        lines.append(f"| `{c.table}` | {c.check} | {icon[c.status]} {c.status} | {c.observed} |")
    return "\n".join(lines) + "\n"


def to_html(report: CanaryReport) -> str:
    color = {"PASS": "#1a7f37", "WARN": "#9a6700", "FAIL": "#cf222e"}
    rows = "\n".join(
        f"<tr><td><code>{html.escape(c.table)}</code></td><td>{html.escape(c.check)}</td>"
        f"<td><span class='b' style='background:{color[c.status]}'>{c.status}</span></td>"
        f"<td>{html.escape(c.observed)}</td><td class='d'>{html.escape(c.detail)}</td></tr>"
        for c in report.checks
    )
    verdict_color = "#1a7f37" if report.verdict == "PROMOTE" else "#cf222e"
    meta = " · ".join(f"{html.escape(str(k))}: {html.escape(str(v))}" for k, v in report.meta.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>SunOps Data Canary</title><style>
body{{font:14px/1.5 system-ui,sans-serif;margin:32px;color:#1f2328;background:#fff}}
h1{{margin:0 0 4px}} .v{{font-size:28px;font-weight:700;color:{verdict_color}}}
table{{border-collapse:collapse;width:100%;margin-top:20px}}
td,th{{border-bottom:1px solid #d0d7de;padding:8px;text-align:left;vertical-align:top}}
th{{background:#f6f8fa}} .b{{color:#fff;border-radius:10px;padding:2px 9px;font-size:12px;font-weight:600}}
.d,.m{{color:#59636e;font-size:12px}}</style></head><body>
<h1>Data Canary</h1><div class="v">{report.verdict} · score {report.score} / 100</div>
<div class="m">minimum score {report.min_score} · {meta}</div>
<table><tr><th>Table</th><th>Check</th><th>Status</th><th>Observed</th><th>Detail</th></tr>
{rows}</table></body></html>"""


def write_reports(report: CanaryReport, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "canary.json").write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    (out_dir / "canary.md").write_text(to_markdown(report), encoding="utf-8")
    (out_dir / "index.html").write_text(to_html(report), encoding="utf-8")


def record_run(dsn: str, report: CanaryReport) -> None:
    """Keep the history in ops.canary_runs so Grafana can show deployment quality over time."""
    from sqlalchemy import create_engine, text

    engine = create_engine(dsn)
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into ops.canary_runs (build_id, git_sha, verdict, score, failed_checks, report) "
                "values (:b, :s, :v, :sc, :f, cast(:r as jsonb))"
            ),
            {
                "b": report.meta.get("build", "local"),
                "s": report.meta.get("git_sha", "local"),
                "v": report.verdict,
                "sc": report.score,
                "f": sum(c.status == "FAIL" for c in report.checks),
                "r": json.dumps(report.to_dict()),
            },
        )


def utf8_console() -> None:
    """Windows consoles default to cp1252, which cannot print the report icons."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    p = argparse.ArgumentParser(description="SunOps Data Canary")
    p.add_argument("--config", default="canary/thresholds.yml")
    p.add_argument("--baseline-dir", type=Path)
    p.add_argument("--candidate-dir", type=Path)
    p.add_argument("--dsn", default=os.getenv("SUNOPS_PG_DSN"))
    p.add_argument("--baseline-schema", default="canary_baseline")
    p.add_argument("--candidate-schema", default="canary_candidate")
    p.add_argument("--out", type=Path, default=Path("reports/canary"))
    p.add_argument("--record", action="store_true", help="store the result in ops.canary_runs")
    args = p.parse_args(argv)

    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.baseline_dir and args.candidate_dir:
        report = run_canary(
            config,
            lambda t: load_from_dir(args.baseline_dir, t),
            lambda t: load_from_dir(args.candidate_dir, t),
        )
    elif args.dsn:
        report = run_canary(
            config,
            lambda t: load_from_postgres(args.dsn, args.baseline_schema, t),
            lambda t: load_from_postgres(args.dsn, args.candidate_schema, t),
        )
    else:
        p.error("give --baseline-dir/--candidate-dir or a Postgres --dsn")

    report.meta.update(
        {
            "build": os.getenv("BUILD_TAG", "local"),
            "git_sha": os.getenv("GIT_COMMIT", "local")[:10],
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
    )
    write_reports(report, args.out)
    print(to_markdown(report))
    if args.record and args.dsn:
        record_run(args.dsn, report)
    return 0 if report.verdict == "PROMOTE" else 1


if __name__ == "__main__":
    sys.exit(main())
