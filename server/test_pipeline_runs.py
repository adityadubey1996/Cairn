#!/usr/bin/env python3
"""Self-check for pipeline run state.
Run: python3 server/test_pipeline_runs.py

derive_status is pure and always runs. The round-trip test needs Postgres and
skips itself when DATABASE_URL is unreachable, so this file is safe in CI
without a database.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import pipeline_runs as pr  # noqa: E402

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def test_finished_status_is_returned_verbatim():
    for status in ("ok", "error", "stopped"):
        row = {"status": status, "started_at": NOW, "heartbeat_at": None}
        assert pr.derive_status(row, NOW) == status


def test_fresh_heartbeat_stays_running():
    row = {"status": "running", "started_at": NOW - timedelta(hours=6),
           "heartbeat_at": NOW - timedelta(seconds=30)}
    assert pr.derive_status(row, NOW) == "running"


def test_stale_heartbeat_reads_as_interrupted():
    row = {"status": "running", "started_at": NOW - timedelta(hours=6),
           "heartbeat_at": NOW - timedelta(minutes=9)}
    assert pr.derive_status(row, NOW) == "interrupted"


def test_a_unit_slower_than_the_urlopen_timeout_is_not_called_dead():
    # groq() waits up to 180s on one call. A run mid-unit must not flap to
    # interrupted just because it has not written a counter for three minutes.
    row = {"status": "running", "started_at": NOW - timedelta(hours=1),
           "heartbeat_at": NOW - timedelta(seconds=181)}
    assert pr.derive_status(row, NOW) == "running"


def test_missing_heartbeat_falls_back_to_started_at():
    row = {"status": "running", "started_at": NOW - timedelta(minutes=30),
           "heartbeat_at": None}
    assert pr.derive_status(row, NOW) == "interrupted"


def _db_up() -> bool:
    try:
        from server.db import connect
        with connect() as c:
            c.execute("SELECT 1")
        return True
    except Exception:
        return False


def _drop_run(run_id: str) -> None:
    """brain_run_log cascades on the run, so one delete clears both."""
    from server.db import connect
    with connect() as c:
        c.execute("DELETE FROM brain_connector_runs WHERE id = %s", (run_id,))


def test_run_roundtrips_through_postgres():
    """Writes to the real database — there is no second one — so it removes its
    own row afterwards. Without that, every run of this suite left a finished
    gdrive run behind, and those showed up in the UI's activity feed as real
    syncs reading "Synced 3 files from Google Drive".
    """
    if not _db_up():
        print("  skip  postgres unreachable")
        return
    from server import connectors, models
    models.ensure_schema()
    connectors.ensure_rows()

    run_id = pr.start("gdrive")
    try:
        pr.set_pid(run_id, 4242)
        pr.begin_phase(run_id, "scrape", total=7)
        pr.progress(run_id, written=3)
        assert pr.get(run_id)["items_written"] == 3
        assert pr.get(run_id)["items_seen"] == 7
        assert pr.get(run_id)["phase"] == "scrape"

        pr.end_phase(run_id, "scrape", {"seen": 7, "written": 3, "seconds": 1.5})
        pr.begin_phase(run_id, "absorb", total=2)
        assert pr.get(run_id)["items_written"] == 0, "a new phase resets the live counters"
        assert pr.get(run_id)["phases"]["scrape"]["written"] == 3

        last = pr.log(run_id, ["first", "second"])
        assert last == 2
        assert [r["line"] for r in pr.read_log(run_id, after=0)] == ["first", "second"]
        assert [r["line"] for r in pr.read_log(run_id, after=1)] == ["second"]

        # Asserted about THIS run rather than about live() being empty: a real
        # sync of the same connector may be in flight on the developer's
        # machine, and that is not this test's business.
        assert (pr.live("gdrive") or {}).get("id") == run_id
        pr.finish(run_id, status="ok")
        assert pr.get(run_id)["status"] == "ok"
        assert (pr.live("gdrive") or {}).get("id") != run_id, "a finished run is not live"
    finally:
        _drop_run(run_id)
    assert pr.get(run_id) is None, "the test cleaned up after itself"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("pipeline runs: all checks passed")
