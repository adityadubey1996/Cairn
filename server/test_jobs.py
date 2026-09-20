"""Queue orchestration tests with no real database, subprocesses or model calls."""
from types import SimpleNamespace

import pytest

from server import jobs


class Result:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Database:
    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.respond(sql, params) or Result()


@pytest.fixture(autouse=True)
def isolated_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_children", {})
    monkeypatch.setattr(jobs, "_last_tick", None)
    monkeypatch.setattr(jobs, "_schedule", lambda: None)
    monkeypatch.setattr(jobs.config, "VAR", tmp_path)


def test_cancelling_queued_work_does_not_start_a_process(monkeypatch):
    db = Database(lambda sql, _: Result({"status": "queued"}) if sql.startswith("SELECT status") else None)
    monkeypatch.setattr(jobs, "connect", lambda: db)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("cancel must not spawn"))
    result = jobs.request_cancel("run-1")
    assert result == {"stopping": False, "status": "stopped"}
    update = next(params for sql, params in db.calls if sql.startswith("UPDATE"))
    assert update[0] == "stopped" and update[-1] == "run-1"


def test_cancelling_running_work_is_durable_until_worker_acknowledges(monkeypatch):
    db = Database(lambda sql, _: Result({"status": "running"}) if sql.startswith("SELECT status") else None)
    monkeypatch.setattr(jobs, "connect", lambda: db)
    assert jobs.request_cancel("run-1") == {"stopping": True, "status": "cancelling"}
    assert any("cancel_requested=true" in sql for sql, _ in db.calls)


def test_global_active_worker_prevents_another_spawn(monkeypatch):
    db = Database(lambda sql, _: Result({"present": 1}) if sql.startswith("SELECT 1 FROM") else None)
    monkeypatch.setattr(jobs, "connect", lambda: db)
    monkeypatch.setattr(jobs.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("active work must serialize"))
    jobs.tick()
    assert not jobs._children


def test_wiki_worker_receives_exact_sources_and_resolved_local_provider(monkeypatch):
    from server import llm
    job = {"connector": "wiki", "project_id": "personal", "ids": ["doc-a", "doc-b"], "absorb": True}
    db = Database(lambda sql, _: Result({"id": "run-1", "job": job})
                  if sql.startswith("SELECT id,job") else None)
    monkeypatch.setattr(jobs, "connect", lambda: db)
    monkeypatch.setattr(llm, "absorb_env", lambda: {"ABSORB_PROTOCOL": "ollama", "ABSORB_API_KEY": "local-test"})
    calls = []

    def spawn(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(pid=12345, poll=lambda: None)

    monkeypatch.setattr(jobs.subprocess, "Popen", spawn)
    jobs.tick()
    args, options = calls[0]
    assert "--absorb-only" in args
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "--only-source"] == ["doc-a", "doc-b"]
    assert options["env"]["ABSORB_PROTOCOL"] == "ollama"
    assert options["start_new_session"] is True
    assert "run-1" in jobs._children


def test_spawn_failure_is_recorded_and_does_not_leave_a_child(monkeypatch):
    from server import pipeline_runs
    job = {"connector": "upload", "project_id": "personal", "absorb": False}
    db = Database(lambda sql, _: Result({"id": "run-1", "job": job})
                  if sql.startswith("SELECT id,job") else None)
    monkeypatch.setattr(jobs, "connect", lambda: db)

    def fail(*_args, **_kwargs):
        raise OSError("could not launch worker")

    monkeypatch.setattr(jobs.subprocess, "Popen", fail)
    finished = []
    monkeypatch.setattr(pipeline_runs, "finish", lambda rid, **kw: finished.append((rid, kw)))
    jobs.tick()
    assert finished == [("run-1", {"status": "error", "error": "could not launch worker"})]
    assert not jobs._children


def test_retry_keeps_the_saved_work_scope(monkeypatch):
    job = {"connector": "wiki", "project_id": "personal", "ids": ["doc-a"],
           "connection_id": "", "absorb": True, "full": False}
    db = Database(lambda sql, _: Result({"job": job, "status": "error"}))
    monkeypatch.setattr(jobs, "connect", lambda: db)
    requests = []
    monkeypatch.setattr(jobs, "submit", lambda *args, **kw: requests.append((args, kw)) or {"run_id": "new"})
    assert jobs.retry("old") == {"run_id": "new"}
    assert requests == [(("wiki", "personal"), {"connection_id": "", "absorb": True,
                                              "full": False, "ids": ["doc-a"]})]
