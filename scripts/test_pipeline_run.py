#!/usr/bin/env python3
"""Self-check for the pipeline runner's pure core.
Run: python3 scripts/test_pipeline_run.py

Unit selection and stdout parsing are where this feature can silently report
wrong numbers, so both are pure and both are tested here. No network, no
Postgres, no subprocess.
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_run import parse_progress, parse_result, parse_unit, units_for  # noqa: E402

PENDING = """# Absorb queue

## new (5) — never absorbed

- `design-md` — `DESIGN.md` (doc)
- `gdrive-116onVkyroGG5I7j5K2YEJE_Yw1ND9lsf` — `sources/gdrive/09-competitors-nd9lsf.md` (doc)
- `gdrive-1-QUqe4BrW2ai19AAF6qiu` — `sources/gdrive/carbon-ai-2026-01-16-hxnut8.md` (meeting_transcript)
- `gchat-AAQA-TS8xh0-2026-08-21` — `sources/gchat/alphara-2026-08-21-ts8xh0.md` (chat_thread)
- `link-civic-scraper` — `sources/links/civic-scraper-052755.md` (external_article)

## changed (1) — sha moved since last ingest — RE-absorb

- `gchat-AAQA0UxHeV8-2026-08-07` — `sources/gchat/alphara-2026-08-07-uxhev8.md` (chat_thread)

## removed (1) — gone from the repo — articles citing it are now dead

- `gdrive-deleted-thing` — `sources/gdrive/gone-abc123.md` (doc)
"""


def test_gdrive_selects_by_path_not_by_unit_id_date():
    # The regression test for connectors._queued_units(): it matches a trailing
    # -YYYY-MM-DD that gdrive ids do not carry, so it returns nothing for Drive.
    ids = units_for(PENDING, "gdrive")
    assert ids == ["gdrive-116onVkyroGG5I7j5K2YEJE_Yw1ND9lsf",
                   "gdrive-1-QUqe4BrW2ai19AAF6qiu"], ids


def test_gdrive_does_not_swallow_the_repos_own_docs():
    # DESIGN.md is kind `doc`, exactly like a Drive document. Filtering by kind
    # would absorb it as if a feeder had fetched it.
    assert "design-md" not in units_for(PENDING, "gdrive")


def test_gchat_spans_the_new_and_changed_buckets():
    assert units_for(PENDING, "gchat") == ["gchat-AAQA-TS8xh0-2026-08-21",
                                           "gchat-AAQA0UxHeV8-2026-08-07"]


def test_removed_bucket_is_never_absorbed():
    # A removed unit has no entry file left to read.
    assert "gdrive-deleted-thing" not in units_for(PENDING, "gdrive")


def test_other_feeders_are_not_picked_up():
    assert units_for(PENDING, "gdrive") and units_for(PENDING, "gchat")
    assert "link-civic-scraper" not in units_for(PENDING, "gdrive")
    assert "link-civic-scraper" not in units_for(PENDING, "gchat")


def test_empty_queue_is_empty_not_an_error():
    assert units_for("", "gdrive") == []


def test_parse_progress_reads_the_batch_size():
    assert parse_progress("[3/718] gchat-AAQA-TS8xh0-2026-08-21") == (3, 718)
    assert parse_progress("    ok  -> wiki/flows/foo.md") is None


def test_parse_result_survives_the_attempt_suffix():
    # run_one's result string carries "(attempt N)" between the path and the
    # token counts, so the parser must not require them to be adjacent.
    got = parse_result("    ok  -> wiki/flows/foo.md (attempt 2)  tok=14210/2130  12.4s")
    assert got == {"article": "wiki/flows/foo.md", "tok_in": 14210,
                   "tok_out": 2130, "seconds": 12.4}, got


def test_parse_result_ignores_failures_and_quarantines():
    assert parse_result("    QUARANTINED after 3 attempts: x  tok=900/40  5.0s") is None
    assert parse_result("    error: ValueError: nope  tok=0/0  0.1s") is None
    assert parse_result("    skip: nothing citable to read  tok=0/0  0.0s") is None


def test_phase_scrape_forwards_the_project_id():
    """The feeder must be told which project and connection, not left to guess.

    Omitting it made every pipeline-path scrape land in the default project
    regardless of which one the connection belonged to — a silent
    mis-attribution with nothing in the output to hint at it.
    """
    import types
    import pipeline_run as pr
    from server import connections

    calls = {}

    class FakeRuns:
        def begin_phase(self, *a, **k): pass
        def end_phase(self, *a, **k): pass
        def progress(self, *a, **k): pass
        def log(self, *a, **k): pass

    fake_feeder = types.SimpleNamespace(
        run=lambda project_id=None, on_progress=None, connection_id=None: (
            calls.update(project_id=project_id, connection_id=connection_id,
                         got_callback=on_progress is not None),
            (7, 3))[1])

    orig_runs, orig_import = pr.runs, pr.importlib.import_module
    pr.runs = FakeRuns()
    pr.importlib.import_module = lambda _name: fake_feeder
    try:
        with patch.object(connections, "_require", return_value={"config": {}}):
            record = pr.phase_scrape("run-1", "gdrive", "proj-42", "gdrive-conn9")
    finally:
        pr.runs, pr.importlib.import_module = orig_runs, orig_import

    assert calls["project_id"] == "proj-42", calls
    assert calls["got_callback"] is True, calls
    # Phase 3: rows are attributed to the connection whose sync produced them.
    assert calls["connection_id"] == "gdrive-conn9", calls
    assert record["seen"] == 7 and record["written"] == 3, record


def test_scrape_only_forwards_allowed_source_scope_and_reports_partial_result():
    import types
    import pipeline_run as pr
    from feeders.result import SyncResult
    from server import connections

    received = {}
    options = {"urls": ["https://example.com"], "max_items": 2,
               "project_id": "untrusted-project", "connection_id": "other", "query": "not-for-links"}

    def scrape(**kwargs):
        received.update(kwargs)
        return SyncResult(2, 1, [{"id": "failed-url", "error": "timeout"}])

    with patch.object(pr, "runs"), \
         patch.object(pr.importlib, "import_module", return_value=types.SimpleNamespace(run=scrape)), \
         patch.object(connections, "_require", return_value={"config": options}):
        result = pr.phase_scrape("run", "links", "trusted-project", "trusted-connection")
    assert received["project_id"] == "trusted-project"
    assert received["connection_id"] == "trusted-connection"
    assert received["urls"] == options["urls"] and received["max_items"] == 2
    assert "query" not in received
    assert result["failed"] == 1 and result["complete"] is False


def test_optional_link_phase_keeps_its_partial_failure_signal():
    import pipeline_run as pr
    from feeders.result import SyncResult

    with patch.object(pr, "runs"), \
         patch("feeders.links.sync.run", return_value=SyncResult(2, 1, [{"id": "failed", "error": "timeout"}])):
        result = pr.phase_links("run", "project")
    assert result["seen"] == 2 and result["written"] == 1
    assert result["failed"] == 1 and result["complete"] is False


def test_chat_sample_limit_is_forwarded_and_truncation_stays_incomplete():
    import types
    import pipeline_run as pr
    from feeders.result import SyncResult
    from server import connections

    feeder = Mock(return_value=SyncResult(1, 1, [{"id": "gchat-backlog", "error": "sample limit"}]))
    with patch.object(pr, "runs"), \
         patch.object(pr.importlib, "import_module", return_value=types.SimpleNamespace(run=feeder)), \
         patch.object(connections, "_require", return_value={"config": {"max_items": 1, "query": "not-for-chat"}}):
        result = pr.phase_scrape("run", "gchat", "project", "connection")
    assert feeder.call_args.kwargs["max_items"] == 1
    assert "query" not in feeder.call_args.kwargs
    assert result["complete"] is False and result["failed"] == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("pipeline run core: all checks passed")


def test_parse_unit_reads_the_id_off_the_progress_line():
    assert parse_unit("[3/718] gchat-AAQA-TS8xh0-2026-08-21") == "gchat-AAQA-TS8xh0-2026-08-21"
    # A result line carries an article path, not a unit id — reading one off it
    # would dequeue whatever unit happened to be current under the wrong name.
    assert parse_unit("    ok  -> wiki/flows/foo.md  tok=1/2  3.0s") is None
