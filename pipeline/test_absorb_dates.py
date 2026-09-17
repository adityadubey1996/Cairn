#!/usr/bin/env python3
"""Self-check: absorb must stamp real dates, never trust the model's own
created/last_updated. Run: python3 test_absorb_dates.py"""
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import absorb_runner  # noqa: E402

FIXTURE = ("---\ntitle: Thing\ntype: gap\ncreated: 1999-01-01\n"
           "last_updated: 1999-01-01\nstale: false\ngrades: {gap: 1}\n"
           "sources: []\nrelated: []\n---\n\n# Thing\n[gap: x]\n")


def check_dates_forced_on_new_article():
    fixed = absorb_runner.force_real_dates(FIXTURE, None, "2026-09-13")
    assert re.search(r"^created: 2026-09-13$", fixed, re.M), fixed
    assert re.search(r"^last_updated: 2026-09-13$", fixed, re.M), fixed


def check_created_preserved_across_reabsorb():
    with tempfile.TemporaryDirectory() as d:
        existing = Path(d) / "thing.md"
        existing.write_text(
            "---\ntitle: Thing\ncreated: 2026-03-01\nlast_updated: 2026-03-01\n"
            "---\n\n# Thing\n")
        fixed = absorb_runner.force_real_dates(FIXTURE, existing, "2026-09-13")
        assert re.search(r"^created: 2026-03-01$", fixed, re.M), fixed
        assert re.search(r"^last_updated: 2026-09-13$", fixed, re.M), fixed


if __name__ == "__main__":
    check_dates_forced_on_new_article()
    check_created_preserved_across_reabsorb()
    print("ok")
