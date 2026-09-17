#!/usr/bin/env python3
"""Absorb only raw/entries/ units dated on or after a cutoff.

Doesn't touch absorb_runner.py or its classification logic — it just filters
raw/entries/'s date-prefixed filenames (YYYY-MM-DD_<unit-id>.md) down to the
matching unit ids and forwards them to the real, unmodified absorb_runner.py
via its existing --only flag.

    python3 scripts/absorb_since.py 2026-07-01
    python3 scripts/absorb_since.py 2026-07-01 --kind chat_thread
    python3 scripts/absorb_since.py 2026-07-01 --dry-run
    python3 scripts/absorb_since.py 2026-07-01 --limit 5
    python3 scripts/absorb_since.py --selftest

Any trailing args are forwarded to absorb_runner.py as-is (--kind, --retries,
--dry-run, etc.) — --only is added automatically, once per matching unit.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ENTRY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)\.md$")


def matching_ids(entries_dir: Path, since: str) -> list[str]:
    out = []
    for f in sorted(entries_dir.glob("*.md")):
        m = ENTRY_RE.match(f.name)
        if m and m.group(1) >= since:
            out.append(m.group(2))
    return out


def _selftest() -> None:
    """assert-based self-check for matching_ids()."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        entries = Path(d)
        (entries / "2026-06-15_gchat-old.md").write_text("x")
        (entries / "2026-07-01_gchat-boundary.md").write_text("x")
        (entries / "2026-07-02_gdrive-new.md").write_text("x")
        (entries / "not-a-dated-entry.md").write_text("x")

        got = matching_ids(entries, "2026-07-01")
        assert got == ["gchat-boundary", "gdrive-new"], got
        assert matching_ids(entries, "2026-07-03") == []
        assert len(matching_ids(entries, "2026-01-01")) == 3  # excludes the undated file
    print("ok  absorb_since: date filter is inclusive of the cutoff, skips undated files")


def main() -> int:
    if "--selftest" in sys.argv:
        _selftest()
        return 0

    if len(sys.argv) < 2 or not re.match(r"^\d{4}-\d{2}-\d{2}$", sys.argv[1]):
        print("usage: absorb_since.py YYYY-MM-DD [absorb_runner.py args...]", file=sys.stderr)
        return 2
    since, extra = sys.argv[1], sys.argv[2:]

    ids = matching_ids(Path("raw/entries"), since)
    if not ids:
        print(f"no raw/entries units dated >= {since}")
        return 0

    print(f"{len(ids)} unit(s) dated >= {since}")
    cmd = [sys.executable, "pipeline/absorb_runner.py", *extra]
    for uid in ids:
        cmd += ["--only", uid]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
