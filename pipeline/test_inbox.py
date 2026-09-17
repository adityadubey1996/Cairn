#!/usr/bin/env python3
"""Self-check for feeder inbox adoption.  Run: python3 test_inbox.py

Feeders (Google Chat, Meet transcripts, YouTube) write pre-rendered entries to
raw/inbox/. ingest.py must adopt them without rebuilding them — git cannot see
these sources, and only the feeder knows a chat thread's real date.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ingest import load_inbox, pair_renames  # noqa: E402

THREAD = """---
id: gchat-2026-07-12-buffer-release
date: 2026-07-12
time: "14:02:00"
source_type: conversation
path: "gchat://spaces/AAAAxyz/threads/BBBB"
sha: 8f2a1cdeadbeef
authors: ["Aditya", "Pankaj"]
status: active
---
**Aditya** 14:02 - buffer release still isn't implemented on either flow
**Pankaj** 14:05 - 1000 sample paths too slow, capping at 200
"""


def test_adopts_feeder_frontmatter():
    with tempfile.TemporaryDirectory() as d:
        inbox = Path(d) / "inbox"
        inbox.mkdir()
        (inbox / "gchat-buffer.md").write_text(THREAD)

        units = load_inbox(inbox)
        assert len(units) == 1, units
        u = units[0]
        # the feeder's date must win: git would have stamped 1970-01-01
        assert u["first"].startswith("2026-07-12"), u["first"]
        assert u["kind"] == "conversation", u["kind"]
        assert u["sha"] == "8f2a1cdeadbeef", u["sha"]
        assert u["path"].startswith("gchat://"), u["path"]
        assert u["authors"] == ["Aditya", "Pankaj"], u["authors"]
        # the source Path is carried privately; stripping "files" + _private keys
        # must leave something the manifest can actually serialise
        assert isinstance(u["_inbox_file"], Path)
        clean = {k: v for k, v in u.items() if k != "files" and not k.startswith("_")}
        json.dumps(clean)  # raises TypeError if a Path leaked through


def test_missing_dir_and_bad_frontmatter_are_survivable():
    assert load_inbox(Path("/nonexistent/inbox")) == []
    with tempfile.TemporaryDirectory() as d:
        inbox = Path(d) / "inbox"
        inbox.mkdir()
        (inbox / "junk.md").write_text("no frontmatter here")
        (inbox / "unterminated.md").write_text("---\nid: x\nstill going")
        assert load_inbox(inbox) == []


def test_sha_fallback_when_feeder_omits_it():
    with tempfile.TemporaryDirectory() as d:
        inbox = Path(d) / "inbox"
        inbox.mkdir()
        (inbox / "a.md").write_text('---\nid: a\ndate: 2026-01-01\n---\nbody\n')
        u = load_inbox(inbox)[0]
        assert len(u["sha"]) == 40, u["sha"]          # sha1 hexdigest
        assert u["kind"] == "doc"                      # default


def test_rename_pairing_is_exact_not_fuzzy():
    prev = {"old-a": ("sha111", "active"), "old-b": ("sha222", "active")}
    cur = {"docs-a": ("sha111", "active"), "docs-b": ("sha999", "active")}
    prev_path = {"old-a": "A.docx", "old-b": "B.docx"}

    renamed, paired_old, paired_new = pair_renames(prev, cur, prev_path)
    # a moved (same bytes) -> paired.  b moved AND changed -> genuinely delete+create
    assert [r["from"] for r in renamed] == ["old-a"], renamed
    assert renamed[0]["to"] == "docs-a"
    assert renamed[0]["from_path"] == "A.docx"
    assert paired_old == {"old-a"} and paired_new == {"docs-a"}


def test_identical_files_do_not_both_claim_one_target():
    # three byte-identical decks moved; each must pair to a distinct new unit
    prev = {f"old-{i}": ("same", "active") for i in range(3)}
    cur = {f"docs-{i}": ("same", "active") for i in range(3)}
    renamed, paired_old, paired_new = pair_renames(prev, cur, {})
    assert len(renamed) == 3
    assert len({r["to"] for r in renamed}) == 3, "a target was claimed twice"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all inbox + rename checks passed")
