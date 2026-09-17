#!/usr/bin/env python3
"""Self-check for the WhatsApp scrape graph.  Run: python3 -m feeders.whatsapp.test_graph

Pins the scroll-loop guard (the "check before the next step") and that the graph
compiles. No browser — the nodes' DOM work is exercised live, not here."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from feeders.whatsapp.graph import (  # noqa: E402
    _reached_or_stalled, _at_latest, build_graph, WAConn, MAX_SCROLLS)

# go-to-latest guard: stop scrolling down once the newest date stops advancing
assert _at_latest({"cur_newest": "[10:00, 19/8/2026] A: ", "prev_newest": "[10:00, 19/8/2026] A: ", "down_rounds": 3})
assert not _at_latest({"cur_newest": "[10:00, 19/8/2026] A: ", "prev_newest": "[10:00, 14/8/2026] A: ", "down_rounds": 3})
assert not _at_latest({"cur_newest": "[10:00, 06/8/2026] A: ", "prev_newest": "", "down_rounds": 1})  # first round
assert _at_latest({"cur_newest": "x", "prev_newest": "y", "down_rounds": MAX_SCROLLS})  # capped

S = "2026-08-13"

# oldest predates the cutoff → reached, stop scrolling
assert _reached_or_stalled(
    {"since": S, "cur_oldest": "[10:00, 12/8/2026] A: ", "prev_oldest": "[10:00, 15/8/2026] A: ", "rounds": 3})
# oldest still moving back (cur != prev), not past cutoff, under cap → keep scrolling
assert not _reached_or_stalled(
    {"since": S, "cur_oldest": "[10:00, 15/8/2026] A: ", "prev_oldest": "[10:00, 18/8/2026] A: ", "rounds": 3})
# oldest stopped moving back (cur == prev, round > 1) → stalled, stop
assert _reached_or_stalled(
    {"since": S, "cur_oldest": "[10:00, 15/8/2026] A: ", "prev_oldest": "[10:00, 15/8/2026] A: ", "rounds": 3})
# first round (rounds == 1, prev empty) is never "stalled"
assert not _reached_or_stalled(
    {"since": S, "cur_oldest": "[10:00, 19/8/2026] A: ", "prev_oldest": "", "rounds": 1})
# round cap hit → stop
assert _reached_or_stalled(
    {"since": S, "cur_oldest": "[10:00, 19/8/2026] A: ", "prev_oldest": "[10:00, 18/8/2026] A: ", "rounds": MAX_SCROLLS})

# graph compiles (nodes + conditional edges wire up) with a dummy connection
assert build_graph(WAConn("ws://x")) is not None

print("ok: whatsapp graph routing/compile")
