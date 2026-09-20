"""Legacy connector routes select work and return durable queue handles."""
import sqlite3
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from server.routers import connectors as router


@pytest.fixture
def queue(monkeypatch):
    submit = Mock(return_value={"run_id": "run-1", "status": "queued", "existing": False})
    monkeypatch.setattr(router.jobs, "submit", submit)
    monkeypatch.setattr(router.projects, "ensure_default", Mock(return_value="default-project"))
    monkeypatch.setattr(router.llm, "absorb_env", Mock(return_value={}))
    monkeypatch.setattr(router.sources, "set_queued", Mock())
    return submit


@pytest.fixture
def source_rows(monkeypatch):
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.execute("CREATE TABLE brain_sources (id TEXT, project_id TEXT, kind TEXT, "
                     "status TEXT, absorption_policy TEXT, absorbed_sha TEXT, sha TEXT)")

    class Connection:
        def execute(self, sql, args):
            return database.execute(sql.replace("%s", "?"), args)

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(router, "connect", connect)
    yield database
    database.close()


def test_github_run_queues_only_repos_in_requested_project(queue, monkeypatch):
    monkeypatch.setattr(router.repos, "list_repos", lambda: [
        {"id": "owner/one", "project_id": "chosen"},
        {"id": "owner/two", "project_id": "other"},
        {"id": "owner/three", "project_id": "chosen"},
    ])
    result = router.run_connector("github", project_id="chosen")
    assert result == {"runs": [queue.return_value, queue.return_value]}
    assert [call.kwargs["connection_id"] for call in queue.call_args_list] == ["owner/one", "owner/three"]
    assert all(call.args == ("github", "chosen") for call in queue.call_args_list)
    router.projects.ensure_default.assert_not_called()


def test_source_run_defaults_project_and_returns_existing_handle(queue):
    queue.return_value = {"run_id": "existing", "status": "running", "existing": True}
    assert router.run_connector("gdrive") == queue.return_value
    queue.assert_called_once_with("gdrive", "default-project")


@pytest.mark.parametrize("handler", [router.run_connector, router.absorb_connector])
def test_unknown_connector_is_404_without_queue_or_default_project(queue, handler):
    with pytest.raises(HTTPException) as error:
        handler("unknown", project_id="chosen")
    assert error.value.status_code == 404
    queue.assert_not_called()
    router.projects.ensure_default.assert_not_called()
    router.llm.absorb_env.assert_not_called()


def test_absorb_selects_only_changed_eligible_sources_in_requested_project(queue, source_rows):
    source_rows.executemany("INSERT INTO brain_sources VALUES (?,?,?,?,?,?,?)", [
        ("new", "chosen", "gmail", "ok", "auto", None, "v1"),
        ("changed", "chosen", "gmail", "ok", "manual", "v1", "v2"),
        ("same", "chosen", "gmail", "ok", "auto", "v1", "v1"),
        ("excluded", "chosen", "gmail", "ok", "exclude", None, "v1"),
        ("failed", "chosen", "gmail", "error", "auto", None, "v1"),
        ("other-project", "other", "gmail", "ok", "auto", None, "v1"),
        ("other-connector", "chosen", "gdrive", "ok", "auto", None, "v1"),
    ])
    assert router.absorb_connector("gmail", project_id="chosen") == queue.return_value
    router.sources.set_queued.assert_called_once_with("chosen", ["new", "changed"], True)
    queue.assert_called_once_with("wiki", "chosen", absorb=True, ids=["new", "changed"])
    router.llm.absorb_env.assert_called_once()


def test_absorb_empty_selection_does_not_require_model_or_submit(queue, source_rows):
    assert router.absorb_connector("gmail") == {"queued": 0}
    router.projects.ensure_default.assert_called_once()
    router.llm.absorb_env.assert_not_called()
    router.sources.set_queued.assert_not_called()
    queue.assert_not_called()


def test_queue_conflict_returns_409(queue):
    queue.side_effect = RuntimeError("worker configuration unavailable")
    with pytest.raises(HTTPException) as error:
        router.run_connector("gdrive", project_id="chosen")
    assert error.value.status_code == 409


def test_missing_model_leaves_sources_unqueued(queue, source_rows):
    source_rows.execute("INSERT INTO brain_sources VALUES (?,?,?,?,?,?,?)",
                        ("new", "chosen", "gmail", "ok", "auto", None, "v1"))
    router.llm.absorb_env.side_effect = RuntimeError("configure a model")
    with pytest.raises(HTTPException) as error:
        router.absorb_connector("gmail", project_id="chosen")
    assert error.value.status_code == 409
    router.sources.set_queued.assert_not_called()
    queue.assert_not_called()
