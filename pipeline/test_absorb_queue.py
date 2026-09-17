#!/usr/bin/env python3
"""Self-check: the absorb queue must process oldest-first, and a renamed unit
must never land in both 'renamed' and 'new' in the same run.
Run: python3 test_absorb_queue.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ingest  # noqa: E402


def check_new_bucket_is_chronological_not_alphabetical():
    units = [
        {"id": "z-unit", "sha": "s1", "status": "active", "path": "z.md",
         "first": "2020-01-01T00:00:00"},
        {"id": "a-unit", "sha": "s2", "status": "active", "path": "a.md",
         "first": "2026-01-01T00:00:00"},
    ]
    pending = ingest.build_pending(units, {}, {}, set())
    assert pending["new"] == ["z-unit", "a-unit"], pending["new"]


def check_renamed_and_absorbed_unit_not_duplicated_into_new():
    # docs-x was absorbed under its old id, then the file was renamed to
    # docs-y with unchanged content (same sha) — this must repoint via
    # "renamed" only, never also appear in "new".
    #
    # This is a WITHIN-ONE-INGEST-RUN guarantee only: build_pending() does
    # not repoint `_absorb_log.json` or the old article's `unit:` frontmatter,
    # so it proves nothing about the NEXT ingest run, once the manifest has
    # caught up and docs-y is no longer a rename candidate. See the docstring
    # on build_pending() in pipeline/ingest.py.
    prev = {"docs-x": ("sha1", "active")}
    prev_path = {"docs-x": "docs/X.md"}
    units = [{"id": "docs-y", "sha": "sha1", "status": "active",
              "path": "docs/Y.md", "first": "2026-01-01T00:00:00"}]
    absorbed = {"docs-x"}
    pending = ingest.build_pending(units, prev, prev_path, absorbed)
    assert pending["new"] == [], pending["new"]
    assert any(r["from"] == "docs-x" and r["to"] == "docs-y"
              for r in pending["renamed"]), pending["renamed"]


if __name__ == "__main__":
    check_new_bucket_is_chronological_not_alphabetical()
    check_renamed_and_absorbed_unit_not_duplicated_into_new()
    print("ok")
