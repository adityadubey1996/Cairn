#!/usr/bin/env python3
"""Connections submit durable jobs with the saved absorption policy.

No network, Postgres or subprocesses: queue execution belongs to jobs.tick().
Run: python3 server/test_connections_sync.py
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


def test_github_sync_queues_the_repo_with_its_saved_absorption_policy():
    with patch.object(connections.repos, "run_sync") as run_sync, \
         patch.object(connections.repos, "get", return_value=REPO_ROW), \
         patch.object(connections.automation, "get", return_value={"auto_absorb": True}), \
         patch.object(connections.jobs, "submit", return_value={"run_id": "job-1", "status": "queued"}) as submit:
        got = connections.sync("owner/repo")
    run_sync.assert_not_called()
    submit.assert_called_once_with("github", "default", connection_id="owner/repo", absorb=True, full=False)
    assert got == {"id": "owner/repo", "status": "queued", "runId": "job-1"}


def test_github_sync_raises_when_the_repo_vanished():
    with patch.object(connections.repos, "get", return_value=None), \
         patch.object(connections.jobs, "submit") as submit:
        try:
            connections.sync("owner/gone")
        except KeyError:
            submit.assert_not_called()
            return
    raise AssertionError("a missing repo must raise, not return a half-built row")


def test_source_sync_uses_its_project_and_can_remain_manual_absorb():
    row = {"id": "gdrive-abc123", "project_id": "proj-7", "kind": "gdrive",
           "name": "Google Drive", "config": {}}
    with patch.object(connections, "_require", return_value=row), \
         patch.object(connections.automation, "get", return_value={"auto_absorb": False}), \
         patch.object(connections.jobs, "submit", return_value={"run_id": "run-9", "status": "queued"}) as submit, \
         patch.object(connections.subprocess, "Popen") as popen:
        got = connections.sync("gdrive-abc123", full=True)
    assert got == {"id": "gdrive-abc123", "status": "queued", "runId": "run-9"}
    submit.assert_called_once_with("gdrive", "proj-7", connection_id="gdrive-abc123", absorb=False, full=True)
    popen.assert_not_called()


def test_an_existing_queued_or_running_job_keeps_its_handle():
    row = {"id": "gdrive-abc123", "project_id": "proj-7", "kind": "gdrive",
           "name": "Google Drive", "config": {}}
    with patch.object(connections, "_require", return_value=row), \
         patch.object(connections.automation, "get", return_value={"auto_absorb": True}), \
         patch.object(connections.jobs, "submit", return_value={"run_id": "run-1", "status": "running", "existing": True}), \
         patch.object(connections.subprocess, "Popen") as popen:
        first = connections.sync("gdrive-abc123")
        second = connections.sync("gdrive-abc123")
    assert first == second == {"id": "gdrive-abc123", "status": "running", "runId": "run-1"}
    popen.assert_not_called()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("connections sync: all checks passed")
