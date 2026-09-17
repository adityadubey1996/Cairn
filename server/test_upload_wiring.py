#!/usr/bin/env python3
"""Self-check that 'upload' is wired into every place a connector id is
assumed to be one of the existing five. Each assertion here is exactly the
line that would otherwise make upload look like gdrive/gchat/links/whatsapp/
linkedin everywhere except the one spot nobody remembered to add it.
Run: python3 server/test_upload_wiring.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import connections, connectors, sources as sources_index  # noqa: E402
from scripts import pipeline_run  # noqa: E402


def test_upload_is_in_the_connector_registry():
    conn = next((c for c in connectors.REGISTRY if c.id == "upload"), None)
    assert conn is not None, "no 'upload' entry in connectors.REGISTRY"
    assert conn.oauth is None, "upload has no external account to authorize"
    assert conn.configured(), "upload needs no configuration to be usable"


def test_connections_create_accepts_upload():
    assert "upload" in connections.NON_GITHUB_KINDS
    assert connections.AUTH_OF_KIND.get("upload") == "none"


def test_sources_group_by_groups_uploads_by_folder():
    assert "upload" in sources_index.GROUP_BY
    assert sources_index.GROUP_BY["upload"] == sources_index.GROUP_BY["gdrive"], (
        "upload should group by folder exactly like gdrive does")


def test_pipeline_run_knows_the_upload_feeder():
    assert "upload" in pipeline_run.CONNECTORS
    assert pipeline_run.FEEDER["upload"] == "feeders.upload.sync"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("upload wiring: all checks passed")
