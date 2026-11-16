#!/usr/bin/env bash
# Publish a release to AWS once Terraform has created the infrastructure.
#   1. push the pipeline image to ECR
#   2. upload the Spark job, the data contract and the dbt project to the artifacts bucket
#   3. upload the Airflow DAG to the DAGs prefix (read by MWAA or any Airflow that syncs from S3)
# Usage: scripts/deploy_aws.sh reports/tf-outputs.json
set -euo pipefail
cd "$(dirname "$0")/.."

OUTPUTS="${1:-reports/tf-outputs.json}"
out() { python -c "import json,sys; print(json.load(open('${OUTPUTS}'))['$1']['value'])"; }

REGION="${AWS_REGION:-eu-west-3}"
ECR_URL="$(out ecr_repository_url)"
ARTIFACTS="$(out artifacts_bucket)"
TAG="${BUILD_NUMBER:-manual}"

echo "== push image ${ECR_URL}:${TAG}"
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ECR_URL%%/*}"
docker tag "sunops-pipeline:${TAG}" "${ECR_URL}:${TAG}"
docker tag "sunops-pipeline:${TAG}" "${ECR_URL}:latest"
docker push "${ECR_URL}:${TAG}"
docker push "${ECR_URL}:latest"

echo "== upload job code to s3://${ARTIFACTS}/releases/${TAG}/"
aws s3 cp spark_jobs/bronze_to_silver.py "s3://${ARTIFACTS}/releases/${TAG}/spark_jobs/bronze_to_silver.py"
aws s3 cp contracts/telemetry.yml        "s3://${ARTIFACTS}/releases/${TAG}/contracts/telemetry.yml"
aws s3 sync dbt "s3://${ARTIFACTS}/releases/${TAG}/dbt" --exclude "target/*" --exclude "logs/*"
aws s3 cp reports/canary "s3://${ARTIFACTS}/releases/${TAG}/canary" --recursive || true

echo "== publish DAG"
aws s3 sync airflow/dags "s3://${ARTIFACTS}/dags" --delete --exclude "__pycache__/*"

echo "== release ${TAG} deployed"
