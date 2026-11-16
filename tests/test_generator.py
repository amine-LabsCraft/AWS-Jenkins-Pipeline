from datetime import date

from sunops.generator import clear_sky_irradiance, generate
from sunops.plant import ARRAYS, INTERVAL_MINUTES


def test_volume_without_chaos(tmp_path):
    stats = generate(date(2026, 3, 2), 2, tmp_path, seed=1, chaos=0)
    assert stats.rows == len(ARRAYS) * (24 * 60 // INTERVAL_MINUTES) * 2
    assert stats.duplicates == stats.corrupted == 0
    assert len(list(tmp_path.glob("ingest_date=*/telemetry.jsonl"))) == 2


def test_same_seed_same_data(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate(date(2026, 3, 2), 1, a, seed=7)
    generate(date(2026, 3, 2), 1, b, seed=7)
    file = "ingest_date=2026-03-02/telemetry.jsonl"
    assert (a / file).read_text() == (b / file).read_text()


def test_no_sun_at_night():
    assert clear_sky_irradiance(2.0) == 0
    assert clear_sky_irradiance(23.0) == 0
    assert clear_sky_irradiance(12.4) > 900


def test_chaos_injects_dirt(records):
    keys = [(r["array_id"], r["ts"]) for r in records]
    assert len(keys) > len(set(keys)), "expected duplicate readings"
    assert any(r["irradiance_wm2"] is None for r in records) or any(
        r["array_id"] == "X-INV9-S9" for r in records
    )
