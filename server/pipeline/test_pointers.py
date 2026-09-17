#!/usr/bin/env python3
"""Self-check for connector pointer parsing. Pure; no DB, no S3.

The fixture is a pointer exactly as it sits in the bucket.

Run: python3 -m server.pipeline.test_pointers
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.pipeline.pointers import parse_pointer  # noqa: E402

BUCKET_POINTER = """---
id: gchat-AAQADWehVa0-2026-03-21
path: sources/gchat/hands-on-alphacarbon-tutorial-21-mar-2026-03-21-wehva0.md
sha: 572e8928
source_type: chat_thread
status: active
date: 2026-03-21
time: "14:28:06"
authors: ["107465432007070735434", "105068465726150792192"]
---

# Hands-on AlphaCarbon Tutorial - 21 Mar — 2026-03-21

13:34 107465432007070735434: https://metamask.io/download
"""

FUTURE_POINTER = """---
id: link-f88398332379
project_id: siting
path: sources/links/metamask-io-download-f88398.md
sha: f94dceb7
source_type: external_article
status: active
date: 2026-08-19
authors: ["Sreekar Reddy"]
publisher: []
source_url: "https://metamask.io/download"
found_in: sources/gchat/hands-on-alphacarbon-tutorial-21-mar-2026-03-21-wehva0.md
---
"""

SINGLE_QUOTED_POINTER = """---
id: link-0281d8ede4ae
path: sources/links/real-bucket-example-0281d8.md
sha: 8f3e2c1a
authors: ['Sreekar Reddy']
---
"""

BARE_STRING_AUTHORS_POINTER = """---
id: link-bare-string-authors
path: sources/links/bare-string-authors.md
sha: deadbeef
authors: 'Jane Doe'
---
"""

UNCLOSED_LIST_POINTER = """---
id: link-unclosed-list
path: sources/links/unclosed-list.md
sha: deadbeef
authors: [unclosed
---
"""


def test_parses_single_quoted_authors_from_real_bucket():
    p = parse_pointer(SINGLE_QUOTED_POINTER)
    assert p.id == "link-0281d8ede4ae", p.id
    assert p.authors == ["Sreekar Reddy"], p.authors


def test_parses_the_bucket_format():
    p = parse_pointer(BUCKET_POINTER)
    assert p.id == "gchat-AAQADWehVa0-2026-03-21", p.id
    assert p.path.endswith("wehva0.md"), p.path
    assert p.sha == "572e8928", p.sha
    assert p.source_type == "chat_thread", p.source_type
    assert p.date == "2026-03-21", p.date
    assert p.time == "14:28:06", p.time
    assert p.authors == ["107465432007070735434", "105068465726150792192"], p.authors
    assert p.project_id is None and p.source_url is None and p.found_in is None, p
    assert p.publisher == [], p.publisher


def test_parses_optional_provenance_fields_when_present():
    p = parse_pointer(FUTURE_POINTER)
    assert p.project_id == "siting", p.project_id
    assert p.source_url == "https://metamask.io/download", p.source_url
    assert p.found_in.startswith("sources/gchat/"), p.found_in
    assert p.authors == ["Sreekar Reddy"], p.authors


def test_bare_string_authors_is_an_error_not_a_per_character_list():
    try:
        parse_pointer(BARE_STRING_AUTHORS_POINTER)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a bare (non-list) authors string")


def test_malformed_list_syntax_raises_value_error_not_syntax_error():
    try:
        parse_pointer(UNCLOSED_LIST_POINTER)
    except SyntaxError:
        raise AssertionError("expected ValueError, not a raw SyntaxError, for malformed list syntax")
    except ValueError:
        return
    raise AssertionError("expected ValueError for malformed list syntax")


def test_missing_frontmatter_or_required_field_is_an_error():
    for bad in ("no frontmatter here", "---\nid: x\n---\n"):
        try:
            parse_pointer(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all pointer checks passed")
