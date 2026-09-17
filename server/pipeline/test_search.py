#!/usr/bin/env python3
"""Self-check for keyword search over parts. Needs Postgres; skips otherwise.

Seeds two parts under a throwaway project so the check does not depend on
what the bucket happens to contain.

Run: python3 -m server.pipeline.test_search
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.db import connect  # noqa: E402
from server.pipeline.schema import ensure_schema  # noqa: E402
from server.pipeline.search import search_parts, view_url  # noqa: E402

PROJECT = "test-phase-a"


def _db_up() -> bool:
    try:
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _seed():
    with connect() as db:
        ensure_schema(db)
        db.execute("DELETE FROM brain_projects WHERE id = %s", (PROJECT,))
        db.execute("INSERT INTO brain_projects (id, name) VALUES (%s, %s)", (PROJECT, PROJECT))
        db.execute(
            """INSERT INTO brain_sources (id, project_id, kind, name, path, sha, source_type)
               VALUES ('spec-x', %s, 'gdrive', 'spec.md', 'sources/gdrive/spec.md', 'abcd1234', 'binary_doc')""",
            (PROJECT,))
        for n, anchor, body in ((1, {"pages": "1-3"}, "Login screen requires an email and a password."),
                                (2, {"pages": "4-6"}, "The setback distance from any residence is 1,500 feet.")):
            db.execute(
                """INSERT INTO brain_parts (id, source_id, project_id, n, total, anchor, chars, body, body_sha)
                   VALUES (%s, 'spec-x', %s, %s, 2, %s::jsonb, %s, %s, 'deadbeef')""",
                (f"spec-x-p0{n}", PROJECT, n, json.dumps(anchor), len(body), body))
        db.commit()


def _cleanup():
    with connect() as db:
        db.execute("DELETE FROM brain_projects WHERE id = %s", (PROJECT,))
        db.commit()


def test_finds_the_part_with_a_snippet_and_a_page_link():
    if not _db_up():
        print("skipped: DB unreachable")
        return
    _seed()
    try:
        hits = search_parts("setback distance residence", PROJECT)
        assert len(hits) == 1, hits
        h = hits[0]
        assert h["part_id"] == "spec-x-p02" and h["anchor"] == {"pages": "4-6"}, h
        assert "setback" in h["snippet"].lower(), h["snippet"]
        assert h["view_url"].startswith("/api/sources/view?path=sources%2Fgdrive%2Fspec.md&etag=abcd1234"), h["view_url"]
        assert h["view_url"].endswith("#page=4"), h["view_url"]
        assert search_parts("no such words anywhere", PROJECT) == []
        assert len(search_parts("setback distance residence", PROJECT, limit=-1)) == 1
    finally:
        _cleanup()


def test_view_url_without_pages_has_no_fragment():
    assert view_url("sources/gchat/a.md", "11112222", {"time": "13:34"}).endswith("&etag=11112222")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all search checks passed")
