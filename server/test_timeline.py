"""The feed's counts come from `phases`, not items_written. No DB: _event is a
pure row -> event function, which is the whole reason it takes `now`.
"""
from datetime import datetime, timedelta, timezone

from .timeline import _event

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def row(**over):
    base = {"id": "r1", "connector_id": "gdrive", "status": "ok", "step": None,
            "items_written": 0, "error": None,
            "started_at": NOW - timedelta(minutes=2), "finished_at": NOW,
            "phases": None, "phase": None, "heartbeat_at": NOW}
    return {**base, **over}


def test_count_comes_from_the_phase_not_the_clobbered_column():
    """begin_phase resets items_written per phase, so a finished pipeline run
    carries the LAST phase's tally. Reading it made the feed say "Synced 0"."""
    e = _event(row(items_written=0,
                   phases={"scrape": {"seen": 7, "written": 3, "seconds": 1.5},
                           "push": {"files": 3, "seconds": 0.4}}), None, NOW)
    assert e["text"] == "Synced 3 files from Google Drive"
    assert [p["name"] for p in e["phases"]] == ["scrape", "push"]
    assert e["phases"][0]["detail"] == "3 written of 7 seen"
    assert e["phases"][1]["detail"] == "3 files to S3"


def test_items_written_is_still_trusted_without_phases():
    """The old runner sets it once and never resets it."""
    e = _event(row(items_written=66, phases=None), None, NOW)
    assert e["text"] == "Synced 66 files from Google Drive"
    assert e["phases"] == []


def test_absorb_recognised_from_the_phase_when_step_is_null():
    """pipeline_runs.start() never writes `step`; only the old runner does."""
    e = _event(row(step=None, phases={"absorb": {"seen": 12, "written": 1,
                                                 "seconds": 9.0, "tokens_in": 1000,
                                                 "tokens_out": 204}}), None, NOW)
    assert e["kind"] == "absorb"
    assert e["text"] == "Absorbed 1 article"
    assert e["phases"][0]["detail"] == "1 of 12 articles · 1,204 tokens"


def test_a_run_whose_heartbeat_died_reads_as_a_failure():
    e = _event(row(status="running", finished_at=None,
                   heartbeat_at=NOW - timedelta(hours=1)), None, NOW)
    assert e["kind"] == "error" and e["status"] == "interrupted"
    assert e["seconds"] is None


def test_a_live_run_names_its_phase():
    e = _event(row(status="running", finished_at=None, phase="scrape"), None, NOW)
    assert e["kind"] == "sync" and e["status"] == "running"
    assert e["text"] == "Syncing Google Drive — scrape"
