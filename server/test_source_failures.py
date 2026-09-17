#!/usr/bin/env python3
"""Self-check that a recorded failure never leaks into a count or a search.
Run: python3 server/test_source_failures.py

The whole risk of putting failures in brain_sources is that six existing
readers would start counting non-files. This pins every one of them. Needs
Postgres; skips itself when DATABASE_URL is unreachable, same convention as
server/test_v2.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROBE = "test-failure-probe"


def _project() -> str:
    """A project id that really exists. ensure_schema() creates the tables but
    not a row, so a fresh database has no 'default' to point a source at —
    the same bootstrap the app does on boot."""
    from server import projects
    return projects.ensure_default()


def _reachable() -> bool:
    try:
        from server.db import connect
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def test_a_failure_is_invisible_to_every_reader_except_the_listing():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import connections, models, people, projects, sources
    from server.db import connect

    models.ensure_schema()
    PROJECT = _project()
    with connect() as c:
        c.execute("DELETE FROM brain_sources WHERE id = %s", (PROBE,))

    before = {
        "listed_ok": sources.list_sources(PROJECT, status="ok")["total"],
        "source_count": projects.list_projects()[0]["sourceCount"],
        "people": len(people.list_people(PROJECT, "dev@localhost")),
        "items": sum(c["itemCount"] for c in connections.list_connections(PROJECT)),
    }

    sources.record_failure(id=PROBE, project_id=PROJECT, kind="gdrive",
                           name="Unreadable probe.pptx",
                           reason=RuntimeError("403 from the provider"))
    try:
        failed = sources.list_sources(PROJECT, status="failed")
        assert PROBE in {r["id"] for r in failed["rows"]}, "the listing must show it"
        assert failed["rows"][0]["error"], "and carry the reason"

        after = {
            "listed_ok": sources.list_sources(PROJECT, status="ok")["total"],
            "source_count": projects.list_projects()[0]["sourceCount"],
            "people": len(people.list_people(PROJECT, "dev@localhost")),
            "items": sum(c["itemCount"] for c in connections.list_connections(PROJECT)),
        }
        assert after == before, f"a failure changed a count: {before} -> {after}"

        assert not sources.find(PROJECT, "Unreadable probe"), \
            "tier-1 search must not return a file that was never fetched"
        assert not sources.get_by_paths([""]), \
            "citation resolution must not resolve to a failed row"
    finally:
        with connect() as c:
            c.execute("DELETE FROM brain_sources WHERE id = %s", (PROBE,))


def test_clear_failures_only_removes_failures():
    if not _reachable():
        print("  skip (no database)")
        return
    from server import models, sources
    from server.db import connect

    models.ensure_schema()
    PROJECT = _project()
    ok_before = sources.list_sources(PROJECT, status="ok")["total"]
    sources.record_failure(id=PROBE, project_id=PROJECT, kind="gdrive",
                           name="probe", reason="boom")
    try:
        assert sources.clear_failures(PROJECT, "gdrive") >= 1
        # Scoped to the kind under test. Asserting "no failures exist at all"
        # quietly depended on no OTHER connector ever having a failed row —
        # which stopped being true the moment gchat started recording the
        # attachments it cannot read (an oversized .zip is a correct failure,
        # not a broken test).
        assert not [r for r in sources.list_sources(PROJECT, status="failed")["rows"]
                    if r["kind"] == "gdrive"]
        assert sources.list_sources(PROJECT, status="ok")["total"] == ok_before, \
            "clearing failures must not touch fetched files"
    finally:
        with connect() as c:
            c.execute("DELETE FROM brain_sources WHERE id = %s", (PROBE,))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("source failures: all checks passed")
