"""Spark job: Bronze (raw JSON lines) -> Silver (typed, validated, deduplicated Parquet).

The validation rules are not hard-coded: they are generated from the YAML data contract,
the same file Jenkins checks for breaking changes. Rows that break a rule go to the
quarantine zone with the list of broken rules, so nothing is silently dropped.

Local:
    spark-submit spark_jobs/bronze_to_silver.py --lake lake --contract contracts/telemetry.yml
AWS (EMR Serverless): same script, `--lake s3://<bucket>` (see infra/terraform and Jenkinsfile).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

SPARK_TYPES = {
    "string": T.StringType(),
    "float": T.DoubleType(),
    "integer": T.LongType(),
    "timestamp": T.StringType(),  # parsed after reading, so bad formats can be quarantined
}
TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ssX"


def bronze_schema(contract: dict) -> T.StructType:
    return T.StructType(
        [T.StructField(name, SPARK_TYPES[spec.get("type", "string")], True) for name, spec in contract["fields"].items()]
    )


def contract_rules(contract: dict) -> list[tuple[str, F.Column]]:
    """One (rule_name, condition_that_must_hold) pair per rule of the contract."""
    rules: list[tuple[str, F.Column]] = []
    for name, spec in contract["fields"].items():
        col = F.col(name)
        if spec.get("required"):
            rules.append((f"{name}:required", col.isNotNull()))
        if spec.get("type") == "timestamp":
            rules.append((f"{name}:type", col.isNull() | F.to_timestamp(col, TS_FORMAT).isNotNull()))
        if "min" in spec:
            rules.append((f"{name}:min", col.isNull() | (col >= F.lit(spec["min"]))))
        if "max" in spec:
            rules.append((f"{name}:max", col.isNull() | (col <= F.lit(spec["max"]))))
        if "allowed" in spec:
            rules.append((f"{name}:allowed", col.isNull() | col.isin(*spec["allowed"])))
        if "pattern" in spec:
            rules.append((f"{name}:pattern", col.isNull() | col.rlike(spec["pattern"])))
    return rules


def clean(bronze: DataFrame, contract: dict) -> tuple[DataFrame, DataFrame, dict]:
    """Return (silver, quarantine, metrics)."""
    failures = [F.when(~F.coalesce(cond, F.lit(False)), F.lit(rule)) for rule, cond in contract_rules(contract)]
    checked = bronze.withColumn("_errors", F.filter(F.array(*failures), lambda x: x.isNotNull())).cache()

    valid = checked.filter(F.size("_errors") == 0).drop("_errors")
    quarantine = checked.filter(F.size("_errors") > 0).withColumn("quarantined_at", F.current_timestamp())

    typed = (
        valid.withColumn("ts", F.to_timestamp("ts", TS_FORMAT))
        .withColumn("ingested_at", F.to_timestamp("ingested_at", TS_FORMAT))
    )
    # The gateway may resend a reading: keep the latest copy per primary key.
    w = Window.partitionBy(*contract["primary_key"]).orderBy(F.col("ingested_at").desc_nulls_last())
    silver = (
        typed.withColumn("_rn", F.row_number().over(w))
        .filter("_rn = 1")
        .drop("_rn")
        .withColumn("date_day", F.to_date("ts"))
    )

    rows_read = bronze.count()
    rows_valid = valid.count()
    rows_silver = silver.count()
    errors_by_rule = {
        r["rule"]: r["count"]
        for r in quarantine.select(F.explode("_errors").alias("rule")).groupBy("rule").count().collect()
    }
    metrics = {
        "rows_read": rows_read,
        "rows_valid": rows_valid,
        "rows_quarantined": rows_read - rows_valid,
        "duplicates_removed": rows_valid - rows_silver,
        "rows_silver": rows_silver,
        "errors_by_rule": errors_by_rule,
        "contract_version": contract["version"],
    }
    return silver, quarantine, metrics


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SunOps Bronze -> Silver")
    p.add_argument("--lake", default="lake", help="local folder or s3a:// URI of the lake")
    p.add_argument("--contract", default="contracts/telemetry.yml")
    p.add_argument("--metrics-out", default=None, help="local JSON file for run metrics")
    p.add_argument("--max-quarantine-pct", type=float, default=5.0, help="fail the run above this")
    args = p.parse_args(argv)

    with open(args.contract, encoding="utf-8") as fh:
        contract = yaml.safe_load(fh)

    spark = (
        SparkSession.builder.appName("sunops-bronze-to-silver")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    lake = args.lake.rstrip("/")
    bronze = spark.read.schema(bronze_schema(contract)).json(f"{lake}/bronze/telemetry")
    silver, quarantine, metrics = clean(bronze, contract)

    silver.write.mode("overwrite").partitionBy("date_day").parquet(f"{lake}/silver/telemetry")
    quarantine.withColumn("date_day", F.substring("ts", 1, 10)).write.mode("overwrite").partitionBy(
        "date_day"
    ).json(f"{lake}/quarantine/telemetry")

    metrics["finished_at"] = datetime.now(timezone.utc).isoformat()
    pct = 100.0 * metrics["rows_quarantined"] / max(metrics["rows_read"], 1)
    metrics["quarantine_pct"] = round(pct, 3)
    print(f"[bronze_to_silver] {json.dumps(metrics)}")
    if args.metrics_out:
        Path(args.metrics_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.metrics_out).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    spark.stop()

    if pct > args.max_quarantine_pct:
        print(f"[bronze_to_silver] quarantine {pct:.2f} % above {args.max_quarantine_pct} %: failing the run")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
