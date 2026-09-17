#!/usr/bin/env python3
"""Ingest + absorb already-fetched link content into wiki articles.

Assumes the links feeder has already run (fetched pages sit in
sources/links/*.md + raw/inbox/link-*.md). This script does the two steps
after that: re-run ingest.py so new fetches become raw/entries/ units, then
absorb_runner.py --kind external_article to turn them into wiki articles.

Refuses to proceed if any sources/links/*.md file is uncommitted — the
absorb citation format requires a real git blob sha for every cited path
(git rev-parse HEAD:<path>), so an uncommitted fetch can NEVER be cited and
absorb_runner.py silently reports "skip: nothing citable to read" for it.
Failing loudly here, once, beats that same skip happening silently per unit.

    python3 scripts/absorb_links.py
    python3 scripts/absorb_links.py --only link-369e8a7d8ff2
    python3 scripts/absorb_links.py --dry-run

If a unit gets QUARANTINED for being too short/malformed, the configured
ABSORB_MODEL (.env) may be too small for that content — retry once with a
larger model, e.g.:
    ABSORB_MODEL=openai/gpt-oss-120b python3 pipeline/absorb_runner.py \\
        --kind external_article --only <unit-id>
(confirmed: gpt-oss-20b quarantined the Stateless MCP HN thread 3/3 tries —
one giant paragraph instead of one claim per line — gpt-oss-120b absorbed
the same content correctly on attempt 1.)
"""
from __future__ import annotations

import subprocess
import sys


def uncommitted_link_sources() -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--", "sources/links/"],
        capture_output=True, text=True, check=True,
    )
    return [line[3:] for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    dirty = uncommitted_link_sources()
    if dirty:
        print("Refusing to run: these fetched links are uncommitted, so absorb "
              "cannot cite them (needs a real git blob sha):", file=sys.stderr)
        for f in dirty:
            print(f"  {f}", file=sys.stderr)
        print("\nCommit them first, e.g.:\n"
              f"  git add {' '.join(dirty)}\n"
              "  git commit -m 'Fetch link content for absorption'",
              file=sys.stderr)
        return 2

    print("== ingest.py ==")
    r = subprocess.call([sys.executable, "pipeline/ingest.py"])
    if r != 0:
        return r

    print("\n== absorb_runner.py --kind external_article ==")
    return subprocess.call(
        [sys.executable, "pipeline/absorb_runner.py", "--kind", "external_article", *sys.argv[1:]]
    )


if __name__ == "__main__":
    sys.exit(main())
