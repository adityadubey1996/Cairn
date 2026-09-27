#!/usr/bin/env python3
"""Self-check that a connector folder alone registers a connector.
Run: python3 -m server.test_connector_discovery

This is the property parallel work depends on: adding a connector must touch
no shared file, so two sessions never edit the same list. No database, no network.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import connectors  # noqa: E402

PROBE = ROOT / "feeders" / "zzprobe"

SPEC_SRC = '''from server.connectors import Connector, Field

SPEC = Connector(
    id="zzprobe", name="Probe", kind="zzprobe", description="discovery self-check",
    configured=lambda: True, module="feeders.zzprobe.sync", auth="token",
    fields=(Field("token", "API token", secret=True),))
'''
ROUTER_SRC = '''from fastapi import APIRouter

router = APIRouter(prefix="/api/zzprobe")


@router.get("/ping")
def ping():
    return {"ok": True}
'''


def _write(router: bool = False):
    PROBE.mkdir(parents=True, exist_ok=True)
    (PROBE / "__init__.py").write_text("")
    (PROBE / "connector.py").write_text(SPEC_SRC)
    (PROBE / "sync.py").write_text("def run(**_kw):\n    return 0, 0\n")
    if router:
        (PROBE / "router.py").write_text(ROUTER_SRC)


def _clean():
    shutil.rmtree(PROBE, ignore_errors=True)
    for name in [m for m in sys.modules if m.startswith("feeders.zzprobe")]:
        del sys.modules[name]


def test_a_folder_alone_registers_a_connector_and_its_routes():
    _write(router=True)
    try:
        found = {c.id: c for c in connectors._discovered()}
        assert "zzprobe" in found, "a connector folder must register itself"
        assert found["zzprobe"].fields[0].secret, "declared fields survive discovery"
        saved = connectors.REGISTRY[:]
        connectors.REGISTRY.append(found["zzprobe"])
        try:
            paths = [r.prefix for r in connectors.routers()]
            assert "/api/zzprobe" in paths, paths
        finally:
            connectors.REGISTRY[:] = saved
    finally:
        _clean()


def test_a_broken_connector_is_skipped_not_fatal():
    PROBE.mkdir(parents=True, exist_ok=True)
    (PROBE / "__init__.py").write_text("")
    (PROBE / "connector.py").write_text("raise RuntimeError('half-written connector')\n")
    try:
        ids = [c.id for c in connectors._discovered()]
        assert "zzprobe" not in ids, "a broken folder must not register"
    finally:
        _clean()


def test_underscore_folders_are_not_connectors():
    template = ROOT / "feeders" / "_zztemplate"
    template.mkdir(parents=True, exist_ok=True)
    (template / "connector.py").write_text(SPEC_SRC)
    try:
        assert "zzprobe" not in [c.id for c in connectors._discovered()]
    finally:
        shutil.rmtree(template, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
