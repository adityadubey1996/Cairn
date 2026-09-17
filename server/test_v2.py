#!/usr/bin/env python3
"""Self-check for the V2 modules (projects, connections, sources, timeline,
people, settings). Needs Postgres; skips itself when DATABASE_URL is
unreachable, same convention as server/test_pipeline_runs.py.

Run: python3 -m server.test_v2
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _db_up() -> bool:
    try:
        from server.db import connect
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _drop_runs(run_ids: list[str]) -> None:
    """brain_run_log cascades on the run, so one delete clears both."""
    from server.db import connect
    with connect() as c:
        for run_id in run_ids:
            c.execute("DELETE FROM brain_connector_runs WHERE id = %s", (run_id,))


def test_v2_roundtrip():
    if not _db_up():
        print("  skip  postgres unreachable")
        return
    from server import connections, models, people, projects, settings, sources, timeline
    from server.runs import finish_run, start_run

    models.ensure_schema()

    proj = projects.create("__test_project__")
    pid = proj["id"]
    started: list[str] = []
    try:
        assert proj["name"] == "__test_project__"
        renamed = projects.rename(pid, "__test_project__ renamed")
        assert renamed["name"] == "__test_project__ renamed"
        assert any(p["id"] == pid for p in projects.list_projects())

        conn = connections.create(pid, "links", "Saved links", {"detail": "reading list"})
        assert conn["kind"] == "links" and conn["projectId"] == pid
        rows = connections.list_connections(pid)
        assert any(c["id"] == conn["id"] for c in rows)

        sources.record(id="link-test1", project_id=pid, kind="links", type="link",
                       name="Example — Pricing", path="sources/links/example-pricing.md",
                       url="https://example.com/pricing", size=42, sha="abc12345",
                       authors=[])
        listed = sources.list_sources(pid)
        assert listed["total"] == 1 and listed["rows"][0]["id"] == "link-test1"
        assert sources.get("link-test1")["name"] == "Example — Pricing"
        # search matches on name even when the file on disk doesn't exist
        found = sources.find(pid, "pricing")
        assert found and found[0]["id"] == "link-test1"
        # provenance: no article cites this path yet
        assert sources.articles_citing("link-test1", pid) == []

        sources.record(id="gdrive-test1", project_id=pid, kind="gdrive",
                       name="Q3 notes.docx", path="sources/gdrive/q3-notes.md",
                       size=100, sha="def45678", authors=["Ravi Kulkarni"])
        people_rows = people.list_people(pid, "dev@localhost")
        assert people_rows[0]["isOwner"] is True
        ravi = next(p for p in people_rows if p["name"] == "Ravi Kulkarni")
        assert ravi["eventCount"] == 1
        events = people.person_events(pid, ravi["id"], "dev@localhost")
        assert events and events[0]["role"] == "authored"

        run_id = start_run("links")
        started.append(run_id)
        finish_run(run_id, status="ok", items_seen=3, items_written=2)
        run_id2 = start_run("links")
        started.append(run_id2)
        finish_run(run_id2, status="error", error="boom")
        events = timeline.list_timeline(pid)
        kinds = {e["kind"] for e in events}
        assert "sync" in kinds and "error" in kinds

        s = settings.save({"provider": {"preset": "ollama"}})
        assert s["provider"]["preset"] == "ollama"
        got = settings.get_settings()
        assert got["provider"]["preset"] == "ollama"
        r = settings.test_provider({"preset": "not-a-real-provider"})
        assert r["ok"] is False
    finally:
        projects.delete(pid)  # cascades: connections, sources, conversations, repos
        # Runs hang off the connector, not the project, so that cascade does not
        # reach them. Without this, every run of this suite left two finished
        # `links` runs behind, and they showed up in the UI as real syncs.
        _drop_runs(started)

    print("  ok  test_v2_roundtrip")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
    print("v2 modules: all checks passed")
