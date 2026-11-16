#!/usr/bin/env bash
# End-to-end run of the SunOps pipeline in one container:
#   generate (Bronze) -> Spark (Silver + quarantine) -> load into Postgres -> dbt build (Gold + tests)
#
# Every step is parameterised by environment variables, so the same script serves the local
# demo (docker compose), the Data Canary in Jenkins (two schemas) and the Airflow DAG.
set -euo pipefail
cd "$(dirname "$0")/.."

DAYS="${DAYS:-14}"
START_DATE="${START_DATE:-2026-03-02}"
SEED="${SEED:-42}"
CHAOS="${CHAOS:-0.01}"
LAKE_DIR="${LAKE_DIR:-lake}"
export RAW_SCHEMA="${RAW_SCHEMA:-silver}"
export DBT_SCHEMA="${DBT_SCHEMA:-analytics}"

echo "== SunOps pipeline | ${DAYS} days from ${START_DATE} | raw=${RAW_SCHEMA} gold=${DBT_SCHEMA}"

python -m sunops.db wait
python -m sunops.db init

echo "== 1/4 generate Bronze"
rm -rf "${LAKE_DIR}/bronze/telemetry"
python -m sunops.generator --start "${START_DATE}" --days "${DAYS}" --seed "${SEED}" --chaos "${CHAOS}" \
    --out "${LAKE_DIR}/bronze/telemetry"

echo "== 2/4 Spark Bronze -> Silver (rules from contracts/telemetry.yml)"
spark-submit --master "local[*]" --driver-memory 1g spark_jobs/bronze_to_silver.py \
    --lake "${LAKE_DIR}" --contract contracts/telemetry.yml \
    --metrics-out "${LAKE_DIR}/_metrics/bronze_to_silver.json"

echo "== 3/4 load Silver into Postgres"
# Run metrics go to ops.pipeline_runs (Grafana "data health"), except for canary runs.
METRICS_ARG=()
if [ "${RECORD_METRICS:-1}" = "1" ]; then
  METRICS_ARG=(--metrics "${LAKE_DIR}/_metrics/bronze_to_silver.json")
fi
python -m sunops.load --silver "${LAKE_DIR}/silver/telemetry" --schema "${RAW_SCHEMA}" "${METRICS_ARG[@]}"

DBT="--project-dir dbt --profiles-dir dbt"
if [ "${DBT_RUN_THEN_TEST:-0}" = "1" ]; then
  # Canary mode: build every model even if a test fails, so the diff can show what moved.
  echo "== 4/4 dbt seed + run, then test"
  dbt seed ${DBT}
  dbt run ${DBT}
  dbt test ${DBT}
else
  echo "== 4/4 dbt build (seeds, models, tests)"
  dbt build ${DBT}
fi

echo "== done. Grafana: http://localhost:3000"
