#!/usr/bin/env python3
"""Self-check for the vendored Steel helpers.  Run: python3 -m feeders.browser.test_steel

Pins the CDP-websocket resolution copied from the workbench SDK: Steel reports
a 0.0.0.0 (or session-id-less) websocketUrl that no host client can use, so we
prefer a canonical {base}/ws?sessionId=… form. No network."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from feeders.browser.steel import resolve_cdp_websocket_url  # noqa: E402

WS = "ws://127.0.0.1:3000"

# 0.0.0.0 websocket → canonical host URL with sessionId
out = resolve_cdp_websocket_url({"id": "abc", "websocketUrl": "ws://0.0.0.0:3000/"}, WS)
assert out == "ws://127.0.0.1:3000/ws?sessionId=abc", out

# missing websocketUrl → canonical
out = resolve_cdp_websocket_url({"id": "abc"}, WS)
assert out == "ws://127.0.0.1:3000/ws?sessionId=abc", out

# good URL passes through untouched
good = "ws://127.0.0.1:3000/ws?sessionId=abc"
assert resolve_cdp_websocket_url({"id": "abc", "websocketUrl": good}, WS) == good

print("ok: steel URL helpers")
