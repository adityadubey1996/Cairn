#!/usr/bin/env python3
"""Offline self-check for the template connector. No network, no database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from feeders.result import SyncResult  # noqa: E402


def test_run_returns_a_sync_result_that_unpacks():
    seen, written = SyncResult(2, 1, [])
    assert (seen, written) == (2, 1)


def test_a_failure_makes_the_run_incomplete():
    assert SyncResult(2, 1, [{"id": "x"}]).complete is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
