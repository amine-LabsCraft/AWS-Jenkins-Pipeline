from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sunops import reference
from sunops.contracts import load_contract
from sunops.generator import generate

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def contract() -> dict:
    return load_contract(ROOT / "contracts" / "telemetry.yml")


@pytest.fixture(scope="session")
def bronze_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("bronze")
    generate(date(2026, 3, 2), 7, out, seed=42, chaos=0.01)
    return out


@pytest.fixture(scope="session")
def records(bronze_dir) -> list[dict]:
    return reference.read_bronze(bronze_dir)


@pytest.fixture(scope="session")
def silver(records, contract):
    df, _ = reference.bronze_to_silver(records, contract)
    return df


@pytest.fixture(scope="session")
def daily(silver):
    return reference.daily_performance(silver)
