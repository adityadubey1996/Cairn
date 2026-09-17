#!/usr/bin/env python3
"""Self-check for the pipeline router's pure decisions.
Run: python3 server/test_pipeline_router.py

The estimate arithmetic and the fallback rate are what the UI shows before a
user spends money, so they are pure and tested. Spawning is not tested here —
Task 6 exercises the runner directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.routers.pipeline import FALLBACK_RATE, estimate_from  # noqa: E402


def test_fallback_is_used_and_flagged_when_no_run_has_happened():
    got = estimate_from(queued=100, rate=None)
    assert got["measured"] is False
    assert got["tokens"] == 100 * FALLBACK_RATE["tokens_per_unit"]
    assert got["seconds"] == 100 * FALLBACK_RATE["seconds_per_unit"]


def test_a_measured_rate_wins_and_is_flagged():
    rate = {"tokens_per_unit": 9000.0, "seconds_per_unit": 30.0}
    got = estimate_from(queued=10, rate=rate)
    assert got == {"queued": 10, "tokens": 90000, "seconds": 300, "measured": True}, got


def test_an_empty_queue_estimates_nothing():
    got = estimate_from(queued=0, rate=None)
    assert got["tokens"] == 0 and got["seconds"] == 0
    assert got["queued"] == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("pipeline router: all checks passed")
