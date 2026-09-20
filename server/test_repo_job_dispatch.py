"""Repository HTTP actions persist their scope; no DB, Git, model or worker."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import jobs
from server.routers import repos as routes


@pytest.fixture
def dispatch(monkeypatch):
    submitted = []
    row = {"id": "octocat/hello-world", "project_id": "personal-kb"}

    def submit(*args, **kwargs):
        submitted.append((args, deepcopy(kwargs)))
        return {"run_id": "durable-17", "status": "queued"}

    monkeypatch.setattr(routes.repos, "get", lambda rid: row if rid == row["id"] else None)
    monkeypatch.setattr(routes.jobs, "submit", submit)
    monkeypatch.setattr(routes.llm, "absorb_env", lambda: {"ABSORB_PROTOCOL": "ollama"})
    monkeypatch.setattr(routes.repos, "run_step", lambda *_a, **_kw: pytest.fail("HTTP must not execute work"))
    monkeypatch.setattr(routes.repos, "run_sync", lambda *_a, **_kw: pytest.fail("HTTP must not execute work"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.current_user] = lambda: "local@example.test"
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, submitted=submitted)


@pytest.mark.parametrize("step,options", [
    ("clone", {}), ("graph", {}), ("ingest", {"commit": None}), ("sync", {}),
])
def test_repo_steps_return_durable_run_without_background_execution(dispatch, step, options):
    response = dispatch.client.post(f"/api/repos/Octocat/Hello-World/{step}", json={})
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "repo": "octocat/hello-world", "step": step,
                               "runId": "durable-17", "status": "queued"}
    assert dispatch.submitted == [(("github", "personal-kb"), {
        "connection_id": "octocat/hello-world", "absorb": False,
        "repo_step": step, "options": options,
    })]


def test_commit_scope_is_preserved_for_deferred_ingestion(dispatch):
    commit = "a" * 40
    response = dispatch.client.post("/api/repos/octocat/hello-world/ingest", json={"commit": commit})
    assert response.status_code == 202
    assert dispatch.submitted[0][1]["options"] == {"commit": commit}


def test_invalid_commit_never_reaches_queue(dispatch):
    response = dispatch.client.post("/api/repos/octocat/hello-world/ingest", json={"commit": "--upload-pack=bad"})
    assert response.status_code == 400
    assert dispatch.submitted == []


@pytest.mark.parametrize("kind", sorted(routes.repos.KINDS))
def test_absorption_keeps_document_kind_limit_and_unit_scope(dispatch, kind):
    options = {"kind": kind, "limit": 7, "only": ["docs-readme", "src/api"], "since": 3}
    response = dispatch.client.post("/api/repos/octocat/hello-world/absorb", json=options)
    assert response.status_code == 202
    assert dispatch.submitted == [(("github", "personal-kb"), {
        "connection_id": "octocat/hello-world", "absorb": True,
        "repo_step": "absorb", "options": options,
    })]


@pytest.mark.parametrize("options", [
    {"kind": "unknown"}, {"limit": 0}, {"limit": 51}, {"limit": True},
    {"since": -1}, {"only": "doc-id"}, {"only": ["--all; bad"]},
])
def test_invalid_absorption_scope_is_rejected_before_queueing(dispatch, options):
    response = dispatch.client.post("/api/repos/octocat/hello-world/absorb", json=options)
    assert response.status_code == 400
    assert dispatch.submitted == []


def test_missing_repo_never_creates_an_unscoped_job(dispatch):
    response = dispatch.client.post("/api/repos/octocat/missing/clone")
    assert response.status_code == 404
    assert dispatch.submitted == []


@pytest.mark.parametrize("body", [{"limit": 5}, {"kind": "", "limit": 5}])
def test_general_absorb_includes_document_only_repositories(dispatch, body):
    response = dispatch.client.post("/api/repos/octocat/hello-world/absorb", json=body)
    assert response.status_code == 202
    assert dispatch.submitted[0][1]["options"] == {
        "kind": "", "limit": 5, "only": None, "since": None,
    }


def test_sync_all_only_enqueues_repositories_from_the_selected_project(dispatch, monkeypatch):
    monkeypatch.setattr(routes.repos, "list_repos", lambda: [
        {"id": "octocat/hello-world", "project_id": "personal-kb"},
        {"id": "other/work", "project_id": "another-kb"},
    ])
    response = dispatch.client.post("/api/repos/sweep?project_id=personal-kb")
    assert response.status_code == 202
    assert dispatch.submitted == [(("github", "personal-kb"), {"connection_id": "octocat/hello-world"})]


def test_changed_source_revision_is_not_hidden_by_older_absorption(tmp_path):
    raw = tmp_path / "repo" / "raw"
    wiki = tmp_path / "wiki"
    raw.mkdir(parents=True)
    wiki.mkdir()
    (raw / "_pending.json").write_text(json.dumps({"new": [], "changed": ["readme"]}))
    (raw / "_manifest.json").write_text(json.dumps([{"id": "readme", "sha": "new-revision"}]))
    (wiki / "_absorb_log.json").write_text(json.dumps({"readme": "old-revision"}))
    row = {"clone_path": str(raw.parent), "wiki_root": str(wiki)}
    assert routes.repos.queue_remaining(row) == (1, 0)
    (wiki / "_absorb_log.json").write_text(json.dumps({"readme": "new-revision"}))
    assert routes.repos.queue_remaining(row) == (0, 1)


def test_queue_counts_use_pinned_revision_instead_of_branch_head(tmp_path):
    clone = tmp_path / "repo"
    raw = clone / "raw"
    pinned_sha = "a" * 40
    pinned = raw / f"at-{pinned_sha[:12]}"
    pinned.mkdir(parents=True)
    (raw / "_pending.json").write_text(json.dumps({"new": ["head-only"], "changed": []}))
    (pinned / "_pending.json").write_text(json.dumps({"new": ["old-doc", "old-code"], "changed": []}))
    row = {"clone_path": str(clone), "pinned_sha": pinned_sha, "wiki_root": ""}
    assert routes.repos.queue_remaining(row) == (2, 0)


def test_uncloned_repo_does_not_read_current_working_directory(monkeypatch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "_pending.json").write_text(json.dumps({"new": ["unrelated-private-unit"]}))
    monkeypatch.chdir(tmp_path)
    assert routes.repos.queue_remaining({"clone_path": "", "wiki_root": ""}) == (0, 0)


def test_retry_of_stopped_repo_work_preserves_original_scope(monkeypatch):
    options = {"kind": "doc", "limit": 4, "only": ["docs-readme"], "since": 2}
    saved = {"connector": "github", "project_id": "personal-kb", "connection_id": "octocat/hello-world",
             "absorb": True, "full": False, "ids": [], "repo_step": "absorb", "options": options}

    class DB:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, *_args): return self
        def fetchone(self): return {"job": saved, "status": "stopped"}

    monkeypatch.setattr(jobs, "connect", DB)
    submitted = []
    monkeypatch.setattr(jobs, "submit", lambda *args, **kwargs: submitted.append((args, kwargs)) or {"run_id": "new"})
    assert jobs.retry("stopped-run") == {"run_id": "new"}
    assert submitted == [(("github", "personal-kb"), {
        "connection_id": "octocat/hello-world", "absorb": True, "full": False, "ids": [],
        "repo_step": "absorb", "options": options,
    })]
