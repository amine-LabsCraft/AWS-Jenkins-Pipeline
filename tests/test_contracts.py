import copy

import pytest

from sunops.contracts import compare_contracts, main, validate_record, validate_records

GOOD = {
    "event_id": "A-INV1-S1-202603021200",
    "plant_id": "OZZ-1",
    "zone": "A",
    "inverter_id": "A-INV1",
    "array_id": "A-INV1-S1",
    "ts": "2026-03-02T12:00:00Z",
    "irradiance_wm2": 950.0,
    "ambient_temp_c": 27.1,
    "module_temp_c": 55.3,
    "dc_voltage_v": 650.0,
    "dc_current_a": 320.0,
    "ac_power_kw": 205.1,
    "status": "OK",
    "ingested_at": "2026-03-02T12:01:00Z",
}


def test_valid_record(contract):
    assert validate_record(GOOD, contract) == []


@pytest.mark.parametrize(
    "patch, rule",
    [
        ({"irradiance_wm2": None}, "irradiance_wm2:required"),
        ({"ac_power_kw": -3.0}, "ac_power_kw:min"),
        ({"module_temp_c": 9999.0}, "module_temp_c:max"),
        ({"array_id": "X-INV9-S9"}, "array_id:pattern"),
        ({"status": "BROKEN"}, "status:allowed"),
        ({"ts": "yesterday"}, "ts:type"),
        ({"ac_power_kw": "205"}, "ac_power_kw:type"),
    ],
)
def test_each_rule_is_enforced(contract, patch, rule):
    assert rule in validate_record({**GOOD, **patch}, contract)


def test_optional_field_may_be_missing(contract):
    rec = {k: v for k, v in GOOD.items() if k != "ingested_at"}
    assert validate_record(rec, contract) == []


def test_generated_data_is_mostly_valid(records, contract):
    s = validate_records(records, contract)
    assert 0 < s.invalid / s.total < 0.03
    assert sum(s.errors_by_rule.values()) >= s.invalid


def _bump(c, version):
    c = copy.deepcopy(c)
    c["version"] = version
    return c


def test_removing_a_field_without_major_bump_fails(contract):
    new = _bump(contract, "1.1.0")
    del new["fields"]["status"]
    rep = compare_contracts(contract, new)
    assert any("removed" in b for b in rep.breaking)
    assert not rep.ok


def test_breaking_change_with_major_bump_is_allowed(contract):
    new = _bump(contract, "2.0.0")
    new["fields"]["ac_power_kw"]["max"] = 280
    rep = compare_contracts(contract, new)
    assert rep.breaking and rep.ok


def test_compatible_changes(contract):
    new = _bump(contract, "1.1.0")
    new["fields"]["status"]["allowed"] = ["OK", "DEGRADED", "OFFLINE", "MAINTENANCE"]
    new["fields"]["wind_speed_ms"] = {"type": "float", "required": False}
    rep = compare_contracts(contract, new)
    assert rep.breaking == [] and len(rep.non_breaking) == 2 and rep.ok


def test_change_without_version_bump_fails(contract):
    new = copy.deepcopy(contract)
    new["fields"]["wind_speed_ms"] = {"type": "float", "required": False}
    assert not compare_contracts(contract, new).ok


def test_compat_cli_first_version(tmp_path, capsys):
    empty = tmp_path / "old.yml"
    empty.write_text("")
    assert main(["compat", str(empty), "contracts/telemetry.yml"]) == 0
