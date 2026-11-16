"""Daily SunOps run, orchestrated by Airflow.

Each task starts the `sunops-pipeline` image (the one Jenkins built and the canary approved)
with DockerOperator, so Airflow only schedules: it holds no business code.

    ingest_bronze -> bronze_to_silver -> load_silver -> dbt_build -> check_freshness

On AWS the same DAG runs on MWAA; set SUNOPS_LAKE_URI=s3a://<lake bucket> and swap the
Spark task for EmrServerlessStartJobOperator (see README, "Deploying on AWS").
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

IMAGE = os.getenv("SUNOPS_IMAGE", "sunops-pipeline:local")
NETWORK = os.getenv("SUNOPS_NETWORK", "sunops_default")
LAKE_VOLUME = os.getenv("SUNOPS_LAKE_VOLUME", "sunops_lake")

PG_ENV = {
    "PGHOST": "postgres",
    "PGUSER": "sunops",
    "PGPASSWORD": "sunops",
    "PGDATABASE": "sunops",
    "SUNOPS_PG_DSN": "postgresql+psycopg2://sunops:sunops@postgres:5432/sunops",
    "RAW_SCHEMA": "silver",
    "DBT_SCHEMA": "analytics",
}

default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
}


def step(task_id: str, command: str) -> DockerOperator:
    return DockerOperator(
        task_id=task_id,
        image=IMAGE,
        command=["bash", "-c", command],
        environment=PG_ENV,
        network_mode=NETWORK,
        mounts=[Mount(source=LAKE_VOLUME, target="/app/lake", type="volume")],
        mount_tmp_dir=False,
        auto_remove="success",
        docker_url="unix://var/run/docker.sock",
    )


with DAG(
    dag_id="sunops_daily",
    description="OZZ-1 solar telemetry: Bronze -> Silver -> Gold, every day",
    start_date=datetime(2026, 3, 2),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["sunops", "solar", "medallion"],
) as dag:
    ingest = step(
        "ingest_bronze",
        # In production the gateway drops files in S3; locally we simulate the day being processed.
        "python -m sunops.generator --start {{ ds }} --days 1 --seed {{ ds_nodash }} "
        "--out lake/bronze/telemetry",
    )
    silver = step(
        "bronze_to_silver",
        "spark-submit --master 'local[*]' spark_jobs/bronze_to_silver.py --lake lake "
        "--contract contracts/telemetry.yml --metrics-out lake/_metrics/bronze_to_silver.json",
    )
    load = step(
        "load_silver",
        "python -m sunops.db init && python -m sunops.load --silver lake/silver/telemetry "
        "--metrics lake/_metrics/bronze_to_silver.json",
    )
    gold = step("dbt_build", "dbt build --project-dir dbt --profiles-dir dbt")
    freshness = step("check_freshness", "dbt source freshness --project-dir dbt --profiles-dir dbt")

    ingest >> silver >> load >> gold >> freshness
