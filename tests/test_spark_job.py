"""Runs only where pyspark is installed (the Docker image, Jenkins). Skipped elsewhere."""
import pytest

pyspark = pytest.importorskip("pyspark")

from pyspark.sql import SparkSession  # noqa: E402

from spark_jobs.bronze_to_silver import bronze_schema, clean  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    s = SparkSession.builder.master("local[1]").appName("sunops-tests").config("spark.ui.enabled", "false").getOrCreate()
    yield s
    s.stop()


def test_spark_matches_reference(spark, bronze_dir, contract, silver):
    bronze = spark.read.schema(bronze_schema(contract)).json(str(bronze_dir))
    spark_silver, quarantine, metrics = clean(bronze, contract)
    assert metrics["rows_silver"] == len(silver)
    assert metrics["rows_quarantined"] == quarantine.count() > 0
    assert metrics["duplicates_removed"] > 0
    assert "array_id:pattern" in metrics["errors_by_rule"] or "irradiance_wm2:required" in metrics["errors_by_rule"]
