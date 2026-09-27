#!/usr/bin/env python3
"""Self-check that a secret typed into a connection never reaches the row the
Connections screen lists.  Run: python3 -m server.test_connection_secrets

Needs Postgres; skips itself when DATABASE_URL is unreachable.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reachable() -> bool:
    try:
        from server.db import connect
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _fake_token_connector():
    from server.connectors import Connector, Field
    return Connector(
        id="faketok", name="Fake token", kind="faketok", description="",
        configured=lambda: True, module="feeders.upload.sync", auth="token",
        fields=(Field("site", "Site"), Field("token", "API token", secret=True)))


def test_secret_fields_never_reach_the_listed_row():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import connections, connectors, credentials, models, projects
    from server.db import connect
    models.ensure_schema()
    pid = projects.ensure_default()
    fake = _fake_token_connector()
    connectors.REGISTRY.append(fake)
    cid = None
    try:
        cid = connections.create(pid, "faketok", "Fake",
                                 {"site": "acme.example", "token": "tok_abcdef123456"})["id"]
        with connect() as c:
            row = c.execute("SELECT config FROM brain_connector_connections "
                            "WHERE id = %s", (cid,)).fetchone()
        assert row["config"] == {"site": "acme.example"}, row
        assert credentials.get(cid) == {"token": "tok_abcdef123456"}
        assert connections.settings_for(cid) == {"site": "acme.example",
                                                 "token": "tok_abcdef123456"}
    finally:
        if cid:
            connections.remove(cid)
        connectors.REGISTRY.remove(fake)
    assert credentials.get(cid) == {}, "removing a connection must drop its secrets"


def test_a_missing_required_field_is_rejected_by_label():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import connections, connectors, models, projects
    models.ensure_schema()
    pid = projects.ensure_default()
    fake = _fake_token_connector()
    connectors.REGISTRY.append(fake)
    try:
        try:
            connections.create(pid, "faketok", "Fake", {"site": "acme.example"})
        except connections.Invalid as e:
            assert "API token" in str(e), e
        else:
            raise AssertionError("accepted a connection with no token")
    finally:
        connectors.REGISTRY.remove(fake)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
