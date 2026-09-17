#!/usr/bin/env python3
"""Self-check for absorb_runner's stop decision.
Run: python3 pipeline/test_absorb_stop.py

The decision is pure so it can be tested without spawning a batch. What it
protects is the post-loop bookkeeping: stopping the RIGHT way is what keeps
_absorb_log.json, the index rebuild and the S3 push running for units already
bought.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from absorb_runner import stop_reason  # noqa: E402


def test_no_reason_to_stop_returns_none():
    assert stop_reason(False, {"in": 10, "out": 2}, 1000) is None


def test_sigterm_wins_over_budget():
    why = stop_reason(True, {"in": 0, "out": 0}, 1000)
    assert why and "SIGTERM" in why


def test_budget_counts_input_and_output_together():
    assert stop_reason(False, {"in": 600, "out": 399}, 1000) is None
    assert stop_reason(False, {"in": 600, "out": 400}, 1000) is not None


def test_zero_budget_means_no_ceiling():
    # Hand-run batches and the browser-connector path pass no budget at all.
    # Zero must mean unlimited, not "stop immediately".
    assert stop_reason(False, {"in": 9_000_000, "out": 1_000_000}, 0) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("absorb stop: all checks passed")
