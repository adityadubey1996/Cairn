#!/usr/bin/env python3
"""Self-check for the per-connection secret store.
Run: python3 -m server.test_credentials

Needs Postgres; skips itself when DATABASE_URL is unreachable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CID = "test-cred-probe"


def _reachable() -> bool:
    try:
        from server.db import connect
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def test_roundtrip_drop_and_redaction():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import credentials, models
    models.ensure_schema()
    try:
        credentials.put(CID, {"token": "tok_abcdef123456", "email": ""})
        assert credentials.get(CID) == {"token": "tok_abcdef123456"}, "empty values are not stored"
        assert credentials.redact("rejected tok_abcdef123456 at x") == "rejected *** at x"
    finally:
        credentials.drop(CID)
    assert credentials.get(CID) == {}


def test_unknown_connection_has_no_secrets():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import credentials, models
    models.ensure_schema()
    assert credentials.get("no-such-connection") == {}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
