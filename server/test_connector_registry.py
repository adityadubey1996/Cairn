#!/usr/bin/env python3
"""Self-check that REGISTRY is the only place a connector is declared.
Run: python3 -m server.test_connector_registry

No database, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from server import connections, connectors  # noqa: E402


def test_every_registered_connector_is_wired_everywhere():
    import pipeline_run as pr
    ids = {c.id for c in connectors.REGISTRY}
    assert set(connections.NON_GITHUB_KINDS) == ids - {"github"}
    assert set(connections.AUTH_OF_KIND) == ids
    assert set(pr.CONNECTORS) == ids
    assert set(pr.FEEDER) == ids - {"github"}
    for c in connectors.REGISTRY:
        if c.id != "github":
            assert pr.FEEDER[c.id] == c.module, c.id


def test_auth_is_declared_per_connector():
    auth = {c.id: c.auth for c in connectors.REGISTRY}
    assert auth["gdrive"] == auth["gchat"] == auth["gmail"] == "oauth"
    assert auth["whatsapp"] == auth["linkedin"] == "browser"
    assert auth["github"] == "token"
    assert auth["upload"] == auth["links"] == "none"


def test_credential_looking_fields_are_secret():
    for c in connectors.REGISTRY:
        for f in c.fields:
            if any(w in f.name for w in ("token", "key", "secret", "password")):
                assert f.secret, f"{c.id}.{f.name} holds a credential but is not secret"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
