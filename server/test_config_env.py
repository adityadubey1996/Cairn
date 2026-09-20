#!/usr/bin/env python3
"""Self-check that a blank line in .env falls back to the default.
Run: python3 -m server.test_config_env

No database, no network.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import config  # noqa: E402


def test_blank_value_leaves_the_default_in_place():
    saved = os.environ.copy()
    try:
        os.environ.pop("CAIRN_TEST_BLANK", None)
        os.environ.pop("CAIRN_TEST_SET", None)
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("# comment\nCAIRN_TEST_BLANK=\nCAIRN_TEST_SET= value \n")
            config._load_env(env)
        assert "CAIRN_TEST_BLANK" not in os.environ, "blank must not shadow the default"
        assert os.environ["CAIRN_TEST_SET"] == "value"
    finally:
        os.environ.clear()
        os.environ.update(saved)


if __name__ == "__main__":
    test_blank_value_leaves_the_default_in_place()
    print("  ok  test_blank_value_leaves_the_default_in_place")
