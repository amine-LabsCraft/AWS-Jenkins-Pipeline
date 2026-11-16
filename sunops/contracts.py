"""Data contracts: validate records against a YAML contract and detect breaking changes.

Two uses:
  * at run time, the Silver layer quarantines every row that breaks a rule;
  * in CI, Jenkins compares the contract on the branch with the one on `main` and
    fails if a breaking change ships without a major version bump.

CLI:
    python -m sunops.contracts validate contracts/telemetry.yml lake/bronze/telemetry
    python -m sunops.contracts compat  old.yml contracts/telemetry.yml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

PY_TYPES = {"string": (str,), "float": (int, float), "integer": (int,), "timestamp": (str,)}


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        contract = yaml.safe_load(fh)
    for key in ("contract", "version", "fields", "primary_key"):
        if key not in contract:
            raise ValueError(f"contract {path} is missing '{key}'")
    return contract


def _parse_ts(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (TypeError, ValueError):
        return False


def validate_record(record: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    """Return the list of broken rules (empty list = valid). Rule names look like `field:rule`."""
    errors: list[str] = []
    for name, spec in contract["fields"].items():
        value = record.get(name)
        if value is None:
            if spec.get("required"):
                errors.append(f"{name}:required")
            continue
        ftype = spec.get("type", "string")
        if isinstance(value, bool) or not isinstance(value, PY_TYPES[ftype]):
            errors.append(f"{name}:type")
            continue
        if ftype == "timestamp" and not _parse_ts(value):
            errors.append(f"{name}:type")
        if "min" in spec and value < spec["min"]:
            errors.append(f"{name}:min")
        if "max" in spec and value > spec["max"]:
            errors.append(f"{name}:max")
        if "allowed" in spec and value not in spec["allowed"]:
            errors.append(f"{name}:allowed")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(f"{name}:pattern")
    return errors


@dataclass
class ValidationSummary:
    total: int = 0
    valid: int = 0
    errors_by_rule: dict[str, int] = field(default_factory=dict)

    @property
    def invalid(self) -> int:
        return self.total - self.valid


def validate_records(records, contract) -> ValidationSummary:
    summary = ValidationSummary()
    for rec in records:
        summary.total += 1
        errs = validate_record(rec, contract)
        if not errs:
            summary.valid += 1
        for e in errs:
            summary.errors_by_rule[e] = summary.errors_by_rule.get(e, 0) + 1
    return summary


# --------------------------------------------------------------------------- compatibility
def _version(v: str) -> tuple[int, int, int]:
    major, minor, patch = (int(x) for x in str(v).split("."))
    return major, minor, patch


@dataclass
class CompatReport:
    breaking: list[str] = field(default_factory=list)
    non_breaking: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def compare_contracts(old: dict[str, Any], new: dict[str, Any]) -> CompatReport:
    rep = CompatReport()
    old_f, new_f = old["fields"], new["fields"]

    if list(old["primary_key"]) != list(new["primary_key"]):
        rep.breaking.append(f"primary key changed {old['primary_key']} -> {new['primary_key']}")

    for name, o in old_f.items():
        if name not in new_f:
            rep.breaking.append(f"field '{name}' removed")
            continue
        n = new_f[name]
        if o.get("type") != n.get("type"):
            rep.breaking.append(f"field '{name}' type {o.get('type')} -> {n.get('type')}")
        if n.get("required") and not o.get("required"):
            rep.breaking.append(f"field '{name}' became required")
        elif o.get("required") and not n.get("required"):
            rep.non_breaking.append(f"field '{name}' became optional")
        if "min" in n and n["min"] > o.get("min", float("-inf")):
            rep.breaking.append(f"field '{name}' min tightened {o.get('min')} -> {n['min']}")
        if "max" in n and n["max"] < o.get("max", float("inf")):
            rep.breaking.append(f"field '{name}' max tightened {o.get('max')} -> {n['max']}")
        if "allowed" in n:
            removed = set(o.get("allowed", [])) - set(n["allowed"]) if "allowed" in o else {"<any>"}
            if removed:
                rep.breaking.append(f"field '{name}' no longer accepts {sorted(removed)}")
            added = set(n["allowed"]) - set(o.get("allowed", []))
            if "allowed" in o and added:
                rep.non_breaking.append(f"field '{name}' now also accepts {sorted(added)}")
        if n.get("pattern") != o.get("pattern") and n.get("pattern"):
            rep.breaking.append(f"field '{name}' pattern changed")

    for name, n in new_f.items():
        if name not in old_f:
            (rep.breaking if n.get("required") else rep.non_breaking).append(
                f"field '{name}' added ({'required' if n.get('required') else 'optional'})"
            )

    old_v, new_v = _version(old["version"]), _version(new["version"])
    changed = bool(rep.breaking or rep.non_breaking)
    if new_v < old_v:
        rep.errors.append(f"version went backwards {old['version']} -> {new['version']}")
    if rep.breaking and new_v[0] <= old_v[0]:
        rep.errors.append(
            f"{len(rep.breaking)} breaking change(s) need a major version bump "
            f"(currently {old['version']} -> {new['version']})"
        )
    elif changed and new_v == old_v:
        rep.errors.append("contract changed but version was not bumped")
    return rep


# --------------------------------------------------------------------------- CLI
def _iter_jsonl(path: Path):
    files = [path] if path.is_file() else sorted(path.rglob("*.jsonl"))
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SunOps data contracts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate JSONL files against a contract")
    v.add_argument("contract")
    v.add_argument("data", type=Path)
    v.add_argument("--max-invalid-pct", type=float, default=5.0)
    c = sub.add_parser("compat", help="check that NEW is a compatible evolution of OLD")
    c.add_argument("old")
    c.add_argument("new")
    args = parser.parse_args(argv)

    if args.cmd == "validate":
        contract = load_contract(args.contract)
        s = validate_records(_iter_jsonl(args.data), contract)
        pct = 100.0 * s.invalid / max(s.total, 1)
        print(f"[contract] {s.total} rows, {s.invalid} invalid ({pct:.2f} %)")
        for rule, n in sorted(s.errors_by_rule.items(), key=lambda kv: -kv[1]):
            print(f"  {rule:<28} {n}")
        return 0 if pct <= args.max_invalid_pct else 1

    old_path = Path(args.old)
    if not old_path.exists() or old_path.stat().st_size == 0:
        print("[contract] no previous contract on main: nothing to compare (first version).")
        return 0
    rep = compare_contracts(load_contract(old_path), load_contract(args.new))
    for line in rep.breaking:
        print(f"  BREAKING      {line}")
    for line in rep.non_breaking:
        print(f"  compatible    {line}")
    for line in rep.errors:
        print(f"  ERROR         {line}")
    print("[contract] OK" if rep.ok else "[contract] FAILED")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
