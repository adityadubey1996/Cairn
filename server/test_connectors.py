#!/usr/bin/env python3
"""Self-check: absorb_now()'s subprocess calls must carry a timeout.
Run: python3 -m server.test_connectors (from the ai-brain repo root)"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from server import connectors  # noqa: E402


def check_ingest_subprocess_has_a_timeout():
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()
    with patch("server.connectors.subprocess.run", fake_run), \
         patch("server.connectors.config.GROQ_API_KEY", "test-key"), \
         patch("server.connectors.start_run", return_value="run-1"), \
         patch("server.connectors.finish_run"), \
         patch("server.connectors._connector_since", return_value=None), \
         patch("server.connectors._queued_units", return_value=[]), \
         patch.object(connectors, "REGISTRY",
                      [type("C", (), {"id": "test-browser", "kind": "browser"})()]):
        connectors.absorb_now("test-browser")
    assert calls and all("timeout" in c for c in calls), calls


if __name__ == "__main__":
    check_ingest_subprocess_has_a_timeout()
    print("ok")
