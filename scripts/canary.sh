#!/usr/bin/env bash
# Data Canary, as run by Jenkins.
#
# 1. check out `main` (the code in production) into .canary/baseline
# 2. run main's pipeline and the branch's pipeline on the same golden sample
#    (fixed seed and dates), each into its own Postgres schemas
# 3. diff the Gold tables with sunops.canary -> reports/canary/{index.html,canary.md,canary.json}
# 4. exit 1 (BLOCK) if the data changed beyond canary/thresholds.yml, or if the candidate
#    pipeline itself failed (a dbt test, the Spark quarantine gate...)
set -euo pipefail
cd "$(dirname "$0")/.."

BASE_REF="${CANARY_BASE_REF:-origin/main}"
export DAYS="${CANARY_DAYS:-7}" START_DATE="${CANARY_START:-2026-03-02}" SEED=42 CHAOS=0.01
# Build every model even when a dbt test fails, so the diff still shows what moved.
export DBT_RUN_THEN_TEST=1
# Canary runs are not production runs: keep them out of ops.pipeline_runs.
export RECORD_METRICS=0

cleanup() {
  for s in canary_baseline canary_candidate canary_baseline_raw canary_candidate_raw; do
    python -m sunops.db drop-schema "$s" >/dev/null 2>&1 || true
  done
  git worktree remove --force .canary/baseline >/dev/null 2>&1 || true
  rm -rf .canary /tmp/sunops-canary
}
trap cleanup EXIT

run_side() {  # $1 = side name, $2 = source directory
  local side="$1" dir="$2"
  echo "== canary: building ${side} from ${dir}"
  ( cd "${dir}" && RAW_SCHEMA="canary_${side}_raw" DBT_SCHEMA="canary_${side}" \
      LAKE_DIR="/tmp/sunops-canary/${side}" bash scripts/run_pipeline.sh )
}

rm -rf .canary && mkdir -p .canary
if git rev-parse --verify --quiet "${BASE_REF}" >/dev/null; then
  git worktree add --detach .canary/baseline "${BASE_REF}" >/dev/null
  run_side baseline .canary/baseline
else
  echo "== canary: ${BASE_REF} not found (first build?): baseline = candidate"
  run_side baseline .
fi

candidate_ok=1
run_side candidate . || { candidate_ok=0; echo "== canary: the candidate pipeline FAILED (see above), diffing what it produced"; }

status=0
python -m sunops.canary --config canary/thresholds.yml \
  --baseline-schema canary_baseline --candidate-schema canary_candidate \
  --out reports/canary --record || status=$?

if [ "${candidate_ok}" = "0" ]; then
  echo "== canary: BLOCK (candidate pipeline failed)"
  exit 1
fi
exit "${status}"
