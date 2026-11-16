# One image for every step of the pipeline: generator, Spark job, loader, dbt and the canary.
# Jenkins, Airflow and docker compose all run this same image, so "works on my machine"
# means "works in CI" and "works in prod".
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Java for Spark (found on the PATH by pyspark), git for the canary's baseline checkout.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .
RUN chmod +x scripts/*.sh

CMD ["bash", "scripts/run_pipeline.sh"]
