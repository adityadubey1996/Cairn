#!/usr/bin/env python3
"""One-time: rewrite sources/ citations from git blob shas to S3 etag prefixes.

    python3 scripts/migrate_sources_citations.py --dry-run
    python3 scripts/migrate_sources_citations.py
    python3 scripts/migrate_sources_citations.py --selftest

Git-sha and S3-etag are unrelated values, so the etag is looked up fresh from
S3 for every cited source path (content is unchanged, so the current etag IS
the right tag). A cited source missing from S3 is reported and left untouched
— run storage.push() (or any connector) first, then re-run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Mirrors validate_wiki.CITE, narrowed to sources/ paths (incl. optional
# single space in the filename, same as the validator's grammar).
CITE_SRC = re.compile(
    r"(sources/[\w./\-]+(?: [\w./\-]+)?\.\w+)@([0-9a-f]{7,40})")


def rewrite(text: str, etag_of) -> tuple[str, list[str]]:
    """Replace each sources/ citation tag via etag_of(path); collect misses."""
    missing: list[str] = []

    def repl(m):
        path = m.group(1)
        etag = etag_of(path)
        if not etag:
            missing.append(path)
            return m.group(0)
        return f"{path}@{etag[:8]}"

    return CITE_SRC.sub(repl, text), missing


def _selftest() -> None:
    etags = {"sources/gchat/day.md": "abcdef0123456789abcdef0123456789"}
    new, missing = rewrite(
        "See sources/gchat/day.md@11223344 and wiki/systems/x.md@55667788 "
        "and sources/links/gone.md@99aabbcc.",
        etags.get)
    assert "sources/gchat/day.md@abcdef01" in new, new
    assert "wiki/systems/x.md@55667788" in new, "non-sources must be untouched"
    assert "sources/links/gone.md@99aabbcc" in new, "missing source left as-is"
    assert missing == ["sources/links/gone.md"], missing
    print("ok  migrate: rewrites sources tags, leaves wiki tags and misses alone")


def main() -> int:
    if "--selftest" in sys.argv:
        _selftest()
        return 0
    dry = "--dry-run" in sys.argv

    from server import storage
    cache: dict[str, str | None] = {}

    def etag_of(path: str) -> str | None:
        if path not in cache:
            cache[path] = storage.head_etag(path)
        return cache[path]

    changed, all_missing = 0, set()
    for f in sorted((ROOT / "wiki").rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        new, missing = rewrite(text, etag_of)
        all_missing.update(missing)
        if new != text:
            changed += 1
            print(("would rewrite  " if dry else "rewrote  ") +
                  str(f.relative_to(ROOT)))
            if not dry:
                f.write_text(new, encoding="utf-8")
    print(f"\n{changed} article(s) {'would be ' if dry else ''}rewritten; "
          f"{len(all_missing)} cited source path(s) missing from S3")
    for p in sorted(all_missing)[:20]:
        print(f"  MISSING from S3: {p}")
    return 1 if all_missing else 0


if __name__ == "__main__":
    sys.exit(main())
