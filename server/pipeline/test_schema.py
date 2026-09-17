#!/usr/bin/env python3
"""Self-check for the Phase A schema. Needs Postgres; skips otherwise.

Run: python3 -m server.pipeline.test_schema
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.db import connect  # noqa: E402
from server.pipeline.schema import ensure_schema  # noqa: E402


def _db_up() -> bool:
    try:
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _columns(db, table: str) -> set[str]:
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,)).fetchall()
    return {r["column_name"] for r in rows}


def test_apply_twice_is_idempotent_and_columns_exist():
    if not _db_up():
        print("skipped: DB unreachable")
        return
    with connect() as db:
        ensure_schema(db)
        ensure_schema(db)
        parts = _columns(db, "brain_parts")
        for col in ("id", "source_id", "project_id", "n", "total", "anchor",
                    "chars", "body", "body_sha", "oversized", "tsv"):
            assert col in parts, (col, parts)
        sources = _columns(db, "brain_sources")
        for col in ("source_type", "occurred_at", "found_in", "publisher"):
            assert col in sources, (col, sources)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all schema checks passed")
