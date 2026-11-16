"""Load the Silver Parquet files into PostgreSQL, where dbt builds the Gold layer.

Idempotent: the days present in the Silver files are deleted then re-inserted, so a
rerun of the same day never creates duplicates. Run metrics from the Spark job are
stored in ops.pipeline_runs for the "data health" panel in Grafana.

    python -m sunops.load --silver lake/silver/telemetry --schema silver \
        --metrics lake/_metrics/bronze_to_silver.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd


def read_silver(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["date_day"] = pd.to_datetime(df["date_day"].astype(str)).dt.date
    for col in ("ts", "ingested_at"):
        df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert(None)  # naive UTC in Postgres
    return df


def load(df: pd.DataFrame, dsn: str, schema: str, table: str = "telemetry") -> int:
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(dsn)
    with engine.begin() as conn:
        conn.execute(text(f'create schema if not exists "{schema}"'))
        if inspect(conn).has_table(table, schema=schema):
            days = sorted({d.isoformat() for d in df["date_day"]})
            conn.execute(
                text(f'delete from "{schema}"."{table}" where date_day = any(cast(:days as date[]))'),
                {"days": days},
            )
        df.to_sql(table, conn, schema=schema, if_exists="append", index=False, method="multi", chunksize=2000)
    return len(df)


def record_metrics(dsn: str, metrics: dict) -> None:
    from sqlalchemy import create_engine, text

    with create_engine(dsn).begin() as conn:
        conn.execute(
            text(
                "insert into ops.pipeline_runs (rows_read, rows_silver, rows_quarantined, duplicates_removed, "
                "quarantine_pct, contract_version, errors_by_rule) values "
                "(:rows_read, :rows_silver, :rows_quarantined, :duplicates_removed, :quarantine_pct, "
                ":contract_version, cast(:errors as jsonb))"
            ),
            {**metrics, "errors": json.dumps(metrics.get("errors_by_rule", {}))},
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Load Silver Parquet into PostgreSQL")
    p.add_argument("--silver", type=Path, default=Path("lake/silver/telemetry"))
    p.add_argument("--schema", default=os.getenv("RAW_SCHEMA", "silver"))
    p.add_argument("--dsn", default=os.getenv("SUNOPS_PG_DSN"))
    p.add_argument("--metrics", type=Path, default=None)
    args = p.parse_args(argv)
    if not args.dsn:
        p.error("set SUNOPS_PG_DSN or pass --dsn")

    df = read_silver(args.silver)
    n = load(df, args.dsn, args.schema)
    print(f"[load] {n} rows -> {args.schema}.telemetry")
    if args.metrics and args.metrics.exists():
        record_metrics(args.dsn, json.loads(args.metrics.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
