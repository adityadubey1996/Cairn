"""Offline self-check for the GitHub issues connector. No network, no database."""
import urllib.error

import pytest

from feeders.ghissues import sync
from feeders.result import SyncResult


def _issue(number=1, **values):
    return {"number": number, "title": f"Thread {number}", "state": "open",
            "body": "the description", "comments": 0,
            "user": {"login": "octocat"}, "labels": [{"name": "bug"}],
            "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-02T11:30:00Z",
            "html_url": f"https://github.com/acme/widgets/issues/{number}", **values}


def _sandbox(monkeypatch, tmp_path, pages):
    """Stub the HTTP layer; every write lands in tmp_path."""
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    rows = {}
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.update({row["id"]: row}))
    monkeypatch.setattr(sync.sources_index, "record_failure", lambda **row: None)
    monkeypatch.setattr(sync, "_get", lambda path, token, params=None: pages(path, params or {}))
    return rows


def test_repos_are_parsed_and_junk_is_refused():
    assert sync.parse_repos("acme/widgets, https://github.com/acme/gears/ , acme/widgets") == [
        "acme/widgets", "acme/gears"]
    with pytest.raises(ValueError):
        sync.parse_repos("not-a-repo")
    with pytest.raises(ValueError):
        sync.parse_repos("  ")


def test_a_pull_request_is_labelled_not_discarded():
    title, text, authors, updated = sync.render_thread(
        "acme/widgets", _issue(7, pull_request={"url": "..."}),
        [{"user": {"login": "hubot"}, "body": "looks good", "created_at": "2026-09-02T11:30:00Z"}])
    assert title == "Thread 7"
    assert "acme/widgets#7 · Pull request · open · opened by octocat" in text
    assert "## Comment by hubot" in text and "looks good" in text
    assert authors == ["octocat", "hubot"] and updated == "2026-09-02T11:30:00Z"


def test_a_thread_is_written_once_and_skipped_when_unchanged(monkeypatch, tmp_path):
    def pages(path, params):
        if path.endswith("/comments"):
            return [{"user": {"login": "hubot"}, "body": "hi", "created_at": "2026-09-02T11:00:00Z"}]
        return [_issue(4, comments=1)] if params.get("page") == 1 else []

    rows = _sandbox(monkeypatch, tmp_path, pages)
    result = sync.run(project_id="p", token="t", repos="acme/widgets")
    assert result == (1, 1) and result.complete
    entry = next((tmp_path / "raw/inbox").glob("ghissues-*.md")).read_text()
    assert "path: sources/ghissues/" in entry and '"octocat", "hubot"' in entry
    assert "hi" in (tmp_path / "sources/ghissues").rglob("*.md").__next__().read_text()
    assert next(iter(rows.values()))["detail"] == "acme/widgets#4"
    assert sync.run(project_id="p", token="t", repos="acme/widgets") == (1, 0)


def test_the_watermark_is_sent_and_a_capped_run_reports_partial(monkeypatch, tmp_path):
    asked = []

    def pages(path, params):
        asked.append(params.get("since"))
        return [_issue(n) for n in range(1, sync.PER_PAGE + 1)] if params.get("page") == 1 else []

    _sandbox(monkeypatch, tmp_path, pages)
    result = sync.run(project_id="p", token="t", repos="acme/widgets",
                      modified_after="2026-09-01T00:00:00Z", max_items=2)
    assert asked[0] == "2026-09-01T00:00:00Z"
    assert result.seen == 2 and not result.complete
    assert "max_items" in result.failures[-1]["error"]


def test_a_spent_rate_limit_stops_cleanly_without_completing(monkeypatch, tmp_path):
    def pages(path, params):
        if path.endswith("/issues"):
            return [_issue(1, comments=1), _issue(2, comments=1)] if params.get("page") == 1 else []
        raise sync.RateLimited("quota spent")

    _sandbox(monkeypatch, tmp_path, pages)
    result = sync.run(project_id="p", token="t", repos="acme/widgets")
    assert result.seen == 2 and result.written == 0 and not result.complete
    assert len(result.failures) == 1, "a spent quota stops the loop instead of failing every item"


def test_sso_and_missing_access_are_explained(monkeypatch):
    def refuse(code, headers):
        error = urllib.error.HTTPError("u", code, "no", headers, None)
        return sync._fail(error)

    assert "single sign-on" in str(refuse(403, {"X-GitHub-SSO": "required; organizations=abc"}))
    assert "Issues: read" in str(refuse(401, {}))
    assert "no access" in str(refuse(404, {}))


def test_probe_lists_without_writing(monkeypatch):
    monkeypatch.setattr(sync, "_get", lambda path, token, params=None:
                        {"login": "octocat"} if path == "/user"
                        else [_issue(9, pull_request={})] if (params or {}).get("page") == 1 else [])
    out = sync.probe({"token": "t", "repos": "acme/widgets"}, limit=3)
    assert out["account"] == "octocat"
    assert out["items"] == [{"id": "acme/widgets#9", "name": "Thread 9", "type": "pull_request",
                             "comments": 0, "updated_at": "2026-09-02T11:30:00Z"}]
    with pytest.raises(ValueError, match="personal access token"):
        sync.probe({"repos": "acme/widgets"})


def test_result_unpacks_for_the_scheduler():
    assert SyncResult(2, 1, [{"id": "x"}]).complete is False
