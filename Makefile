# Shortcuts. On Windows without make, copy the command on the right of each target.
.PHONY: demo test lint up pipeline down airflow jenkins assets

demo:            ## Data Canary demo, no Docker needed
	python -m sunops.demo --scenario lost-dedup

test:            ## unit tests
	pytest -q

lint:
	ruff check sunops spark_jobs tests airflow

up:              ## whole platform: Postgres + pipeline + Grafana
	docker compose up --build

pipeline:        ## rerun the pipeline only
	docker compose run --rm pipeline

down:
	docker compose --profile airflow --profile ci down -v

airflow:
	docker compose --profile airflow up -d airflow

jenkins:
	docker compose --profile ci up -d --build jenkins

assets:          ## regenerate the charts in docs/images
	python scripts/make_readme_assets.py
