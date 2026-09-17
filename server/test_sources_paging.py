#!/usr/bin/env python3
"""Self-check for list_sources' cursor encoding.
Run: python3 server/test_sources_paging.py

Only the cursor codec is pure; the query itself needs Postgres and is covered by
the manual check in the plan. The codec is here because `scraped_at` alone is
not unique — one sync writes many rows inside the same transaction timestamp —
so a key that dropped the id would silently skip or repeat rows at a page
boundary.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.sources import _decode_cursor, _encode_cursor  # noqa: E402

AT = datetime(2026, 9, 15, 12, 30, 45, tzinfo=timezone.utc)


def test_cursor_round_trips_both_halves_of_the_key():
    row = {"scraped_at": AT, "id": "gchat-space-2026-09-15"}
    got = _decode_cursor(_encode_cursor(row))
    assert got == (AT.isoformat(), "gchat-space-2026-09-15"), got


def test_an_id_containing_no_pipe_is_required_to_survive():
    # ids are slug-shaped (kind + hyphens), so '|' is a safe separator; this
    # pins that assumption rather than leaving it implicit.
    row = {"scraped_at": AT, "id": "link-1e5abc2e5dc0"}
    assert _decode_cursor(_encode_cursor(row))[1] == "link-1e5abc2e5dc0"


def test_a_malformed_cursor_is_rejected_rather_than_half_applied():
    for bad in ("", "no-separator", "|missing-at", "missing-id|"):
        assert _decode_cursor(bad) is None, bad


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("sources paging: all checks passed")
