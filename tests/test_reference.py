"""Business rules of the Gold layer, pinned on the pandas reference implementation."""
import csv
from pathlib import Path

from sunops import reference
from sunops.plant import ARRAYS, FAULTY_ARRAY

ROOT = Path(__file__).resolve().parent.parent


def test_silver_has_no_duplicates_and_no_invalid_rows(silver, contract):
    assert not silver.duplicated(subset=contract["primary_key"]).any()
    assert silver["ac_power_kw"].min() >= 0
    assert set(silver["array_id"]) == {a.array_id for a in ARRAYS}


def test_quarantine_keeps_the_reason(records, contract):
    _, quarantine = reference.bronze_to_silver(records, contract)
    assert len(quarantine) > 0
    assert quarantine["_errors"].map(len).min() >= 1


def test_performance_ratio_is_plausible(daily):
    pr = daily["performance_ratio"].dropna()
    assert pr.between(0.4, 1.0).all()
    assert 0.7 < pr.median() < 0.9


def test_zone_without_robot_gets_dirtier(daily):
    last, first = daily["date_day"].max(), daily["date_day"].min()
    by_zone = daily.groupby(["date_day", "zone"])["performance_ratio"].mean().unstack()
    gap_first = by_zone.loc[first, "A"] - by_zone.loc[first, "C"]
    gap_last = by_zone.loc[last, "A"] - by_zone.loc[last, "C"]
    assert gap_last > gap_first


def test_faulty_array_is_inspected_not_cleaned(daily):
    reco = reference.cleaning_recommendations(daily)
    faulty = reco[reco["array_id"] == FAULTY_ARRAY]["recommendation"]
    assert set(faulty) == {"INSPECT"}


def test_lost_revenue_is_never_negative(daily):
    reco = reference.cleaning_recommendations(daily)
    assert (reco["lost_revenue_mad"] >= 0).all()
    assert reco["recommendation"].isin(["OK", "SCHEDULE", "CLEAN_NOW", "INSPECT"]).all()


def test_dbt_seed_matches_plant_layout():
    with open(ROOT / "dbt" / "seeds" / "arrays.csv", encoding="utf-8") as fh:
        seed = {row["array_id"]: row for row in csv.DictReader(fh)}
    assert set(seed) == {a.array_id for a in ARRAYS}
    for a in ARRAYS:
        assert seed[a.array_id]["zone"] == a.zone
        assert float(seed[a.array_id]["capacity_kw"]) == a.capacity_kw
