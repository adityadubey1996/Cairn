#!/usr/bin/env python3
"""Self-check for absorb_runner's token accounting.
Run: python3 pipeline/test_absorb_usage.py

No network and no Postgres: add_usage is pure arithmetic, which is the only
part of the accounting that can silently go wrong.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from absorb_runner import add_usage  # noqa: E402


def test_empty_total_starts_from_zero():
    assert add_usage({}, {"prompt_tokens": 10, "completion_tokens": 3}) == {"in": 10, "out": 3}


def test_retries_accumulate_rather_than_replace():
    total = add_usage({}, {"prompt_tokens": 100, "completion_tokens": 20})
    total = add_usage(total, {"prompt_tokens": 140, "completion_tokens": 25})
    assert total == {"in": 240, "out": 45}, "a retry costs real tokens and must be counted"


def test_missing_usage_block_is_not_an_error():
    # Some OpenAI-compatible providers omit usage entirely. A missing block
    # must read as zero, never as a crash mid-batch.
    assert add_usage({"in": 5, "out": 1}, {}) == {"in": 5, "out": 1}
    assert add_usage({"in": 5, "out": 1}, {"prompt_tokens": None}) == {"in": 5, "out": 1}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("absorb usage: all checks passed")
