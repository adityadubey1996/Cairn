#!/usr/bin/env python3
"""Self-check for connections.sync()'s two branches.
Run: python3 server/test_connections_sync.py

The github branch is here because it regressed silently: run_sync returns step
results, not a repo row, and the resulting KeyError surfaced as a 404 only
AFTER a successful clone/graph/ingest. Nothing about the failure pointed at the
return shape.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import connections  # noqa: E402

REPO_ROW = {
    "id": "owner/repo", "owner": "owner", "name": "repo", "branch": "main",
    "state": "ready", "last_error": None, "articles": 12,
    "project_id": "default", "updated_at": None,
}


def test_github_sync_returns_the_repo_row_not_the_step_results():
    steps = {"clone": {}, "graph": {}, "ingest": {}}
    with patch.object(connections.repos, "run_sync", return_value=steps) as run_sync, \
         patch.object(connections.repos, "get", return_value=REPO_ROW):
        got = connections.sync("owner/repo")
    run_sync.assert_called_once_with("owner/repo")
    assert got["id"] == "owner/repo", got
    assert got["kind"] == "github", got
    assert got["itemCount"] == 12, got


def test_github_sync_raises_when_the_repo_vanished():
    with patch.object(connections.repos, "run_sync", return_value={}), \
         patch.object(connections.repos, "get", return_value=None):
        try:
            connections.sync("owner/gone")
        except KeyError:
            return
    raise AssertionError("a missing repo must raise, not return a half-built row")


def test_non_github_sync_spawns_the_runner_instead_of_blocking():
    """sync() used to call the feeder inline, which held the request thread for
    the whole scrape plus the S3 push and returned no handle. It now spawns the
    phased runner and hands back a run id to poll."""
    row = {"id": "gdrive-abc123", "project_id": "proj-7", "kind": "gdrive",
           "name": "Google Drive", "config": {}}
    with patch.object(connections, "_require", return_value=row), \
         patch.object(connections.pipeline_runs, "live", return_value=None), \
         patch.object(connections.pipeline_runs, "start", return_value="run-9"), \
         patch.object(connections.subprocess, "Popen") as popen:
        got = connections.sync("gdrive-abc123")

    assert got == {"id": "gdrive-abc123", "status": "running", "runId": "run-9"}, got
    argv = popen.call_args[0][0]
    assert "--connector" in argv and argv[argv.index("--connector") + 1] == "gdrive", argv
    assert argv[argv.index("--run-id") + 1] == "run-9", argv
    # the connection's own project, not the default one
    assert argv[argv.index("--project-id") + 1] == "proj-7", argv
    # absorb is the only phase that spends money; this path never runs it
    assert "--skip-absorb" in argv, argv
    # detached, so a redeploy of the server does not signal the run
    assert popen.call_args[1]["start_new_session"] is True


def test_a_second_sync_while_one_is_in_flight_is_refused():
    row = {"id": "gdrive-abc123", "project_id": "proj-7", "kind": "gdrive",
           "name": "Google Drive", "config": {}}
    with patch.object(connections, "_require", return_value=row), \
         patch.object(connections.pipeline_runs, "live", return_value={"id": "run-1"}), \
         patch.object(connections.subprocess, "Popen") as popen:
        try:
            connections.sync("gdrive-abc123")
        except connections.Invalid:
            popen.assert_not_called()
            return
    raise AssertionError("a concurrent run must be refused, not spawned alongside")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("connections sync: all checks passed")
