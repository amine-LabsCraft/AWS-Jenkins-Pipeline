"""Small database helpers used by scripts and CI.

    python -m sunops.db init              # run sql/init/*.sql (schemas + ops tables)
    python -m sunops.db wait              # wait until Postgres accepts connections
    python -m sunops.db drop-schema NAME  # clean the canary schemas after a CI run
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _engine(dsn: str):
    from sqlalchemy import create_engine

    return create_engine(dsn)


def init(dsn: str) -> None:
    from sqlalchemy import text

    with _engine(dsn).begin() as conn:
        for f in sorted((ROOT / "sql" / "init").glob("*.sql")):
            conn.execute(text(f.read_text(encoding="utf-8")))
            print(f"[db] applied {f.name}")


def wait(dsn: str, timeout_s: int = 60) -> None:
    from sqlalchemy import text

    deadline = time.time() + timeout_s
    while True:
        try:
            with _engine(dsn).connect() as conn:
                conn.execute(text("select 1"))
            print("[db] postgres is ready")
            return
        except Exception as exc:  # noqa: BLE001 - any connection error means "not ready yet"
            if time.time() > deadline:
                raise SystemExit(f"[db] postgres not reachable after {timeout_s}s: {exc}") from exc
            time.sleep(2)


def drop_schema(dsn: str, name: str) -> None:
    from sqlalchemy import text

    if not name.replace("_", "").isalnum():
        raise SystemExit(f"refusing to drop suspicious schema name {name!r}")
    with _engine(dsn).begin() as conn:
        conn.execute(text(f'drop schema if exists "{name}" cascade'))
    print(f"[db] dropped schema {name}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="SunOps database helpers")
    p.add_argument("cmd", choices=["init", "wait", "drop-schema"])
    p.add_argument("name", nargs="?")
    p.add_argument("--dsn", default=os.getenv("SUNOPS_PG_DSN"))
    args = p.parse_args(argv)
    if not args.dsn:
        p.error("set SUNOPS_PG_DSN or pass --dsn")
    if args.cmd == "init":
        init(args.dsn)
    elif args.cmd == "wait":
        wait(args.dsn)
    else:
        if not args.name:
            p.error("drop-schema needs a schema name")
        drop_schema(args.dsn, args.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
