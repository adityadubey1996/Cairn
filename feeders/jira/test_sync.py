"""Offline checks for the Jira connector. No network, no database."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from feeders.jira import sync
from feeders.jira.adf import to_text

SAMPLE = json.loads((Path(__file__).parent / "sample_adf.json").read_text())
SETTINGS = {"site": "acme.atlassian.net", "email": "you@acme.com", "token": "secret-token"}


def _adf(text):
    return {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _issue(key="ACME-1", summary="Deploy is flaky", updated="2026-09-20T09:00:00.000+0000",
           description="the description", comments=(), reporter="Dana Reed"):
    fields = {"summary": summary, "updated": updated,
              "reporter": {"displayName": reporter} if reporter else None,
              "description": _adf(description),
              "comment": {"comments": [
                  {"author": {"displayName": who}, "created": "2026-09-20T10:00:00.000+0000",
                   "body": _adf(body)} for who, body in comments]}}
    return {"key": key, "fields": fields}


def _connected(monkeypatch, tmp_path, settings=None):
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync.connections, "settings_for", lambda _cid: {**SETTINGS, **(settings or {})})
    monkeypatch.setattr(sync.connections, "watermark", lambda _cid: "")
    rows = []
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.append(row))
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    return rows


# --- Atlassian Document Format -------------------------------------------------

def test_saved_adf_sample_keeps_structure_text_and_link_targets():
    text = to_text(SAMPLE)
    assert "## Steps to reproduce" in text
    assert "[runbook](https://example.atlassian.net/wiki/runbook)" in text
    assert "`make deploy`" in text
    assert "@Dana Reed" in text
    assert "- Region eu-west-1\n\n  - Two replicas" in text   # nesting survives
    assert "1. Drain the queue\n2. Restart the worker" in text
    assert "```bash\nkubectl rollout restart deploy/worker\n```" in text
    assert "> The 04:12 alert fired first." in text
    assert "| prod | degraded |" in text
    assert "[attachment] latency.png" in text
    assert "Still readable." in text                           # unknown node type
    assert '"type"' not in text and "attrs" not in text


def test_adf_that_is_not_a_document_is_empty_not_an_error():
    assert to_text(None) == "" and to_text("plain string") == "" and to_text({}) == ""


# --- the incremental window ----------------------------------------------------

def test_first_run_reads_the_whole_window_in_update_order():
    assert sync.incremental_jql("") == "ORDER BY updated ASC"


def test_the_window_is_relative_so_the_site_timezone_cannot_shift_it():
    same_instant = [sync.incremental_jql("2026-09-20T09:00:00+00:00"),
                    sync.incremental_jql("2026-09-20T09:00:00Z"),
                    sync.incremental_jql("2026-09-20T09:00:00")]
    assert len(set(same_instant)) == 1
    assert same_instant[0].startswith("updated >= -") and same_instant[0].endswith("m ORDER BY updated ASC")
    minutes = int(same_instant[0].removeprefix("updated >= -").split("m")[0])
    assert minutes > 0


# --- paging and back-off -------------------------------------------------------

def test_search_pages_until_the_token_runs_out(monkeypatch):
    pages = [{"issues": [_issue("ACME-1")], "nextPageToken": "second"},
             {"issues": [_issue("ACME-2")]}]
    sent = []

    def request(_settings, _path, body=None):
        sent.append(body)
        return pages[len(sent) - 1]

    monkeypatch.setattr(sync, "_request", request)
    rows, truncated = sync.search_issues(SETTINGS, "ORDER BY updated ASC", max_items=0)
    assert [r["key"] for r in rows] == ["ACME-1", "ACME-2"] and not truncated
    assert "nextPageToken" not in sent[0] and sent[1]["nextPageToken"] == "second"


def test_a_capped_search_reports_that_the_window_is_incomplete(monkeypatch):
    monkeypatch.setattr(sync, "_request", lambda *_a, **_k: {
        "issues": [_issue("ACME-1"), _issue("ACME-2")], "nextPageToken": "more"})
    rows, truncated = sync.search_issues(SETTINGS, "ORDER BY updated ASC", max_items=1)
    assert len(rows) == 1 and truncated


def test_rate_limiting_waits_for_retry_after(monkeypatch):
    import urllib.error

    waited = []
    monkeypatch.setattr(sync.time, "sleep", waited.append)
    responses = [urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "7"}, None)]

    class Body:
        def read(self):
            return b'{"issues": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def urlopen(*_a, **_k):
        if responses:
            raise responses.pop()
        return Body()

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    assert sync._request(SETTINGS, sync.SEARCH, {"jql": ""}) == {"issues": []}
    assert waited == [7.0]


def test_a_rejected_token_says_what_to_check(monkeypatch):
    import urllib.error

    def urlopen(*_a, **_k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    with pytest.raises(PermissionError, match="id.atlassian.com"):
        sync._request(SETTINGS, sync.IDENTITY)


def test_a_half_filled_connection_is_rejected_before_any_request():
    with pytest.raises(ValueError, match="site URL"):
        sync._site({"email": "you@acme.com", "token": "t"})
    with pytest.raises(ValueError, match="API token"):
        sync._authorization({"site": "acme.atlassian.net", "email": "you@acme.com"})


# --- rendering one issue -------------------------------------------------------

def test_an_issue_renders_its_description_and_comments_with_their_authors():
    title, text, authors, updated = sync.render_issue(
        _issue(comments=[("Sam Okoro", "rolled back"), ("Dana Reed", "confirmed")]))
    assert title == "Deploy is flaky"
    assert text.startswith("# ACME-1 Deploy is flaky")
    assert "the description" in text and "rolled back" in text and "confirmed" in text
    assert authors == ["Dana Reed", "Sam Okoro"]
    assert updated == "2026-09-20T09:00:00.000+0000"


def test_an_issue_without_a_reporter_is_attributed_to_its_creator():
    issue = _issue(reporter=None)
    issue["fields"]["creator"] = {"displayName": "Automation Bot"}
    assert sync.render_issue(issue)[2] == ["Automation Bot"]


def test_an_empty_issue_still_produces_a_citable_source():
    empty = _issue(description="", comments=())
    empty["fields"]["description"] = None
    _title, text, _authors, _updated = sync.render_issue(empty)
    assert "[No description or comments]" in text


# --- the sync ------------------------------------------------------------------

def test_a_sync_writes_a_source_an_inbox_entry_and_an_index_row(monkeypatch, tmp_path):
    rows = _connected(monkeypatch, tmp_path)
    issues = [_issue(comments=[("Sam Okoro", "rolled back")])]
    monkeypatch.setattr(sync, "search_issues", lambda *_a, **_k: (issues, False))
    result = sync.run(project_id="one", connection_id="c1")
    assert result == (1, 1) and result.complete
    row = rows[-1]
    assert row["kind"] == "jira" and row["url"] == "https://acme.atlassian.net/browse/ACME-1"
    source = tmp_path / row["path"]
    assert "rolled back" in source.read_text()
    entry = (tmp_path / "raw/inbox" / f"{row['id']}.md").read_text()
    assert row["path"] in entry and "date: 2026-09-20" in entry and 'time: "09:00:00"' in entry


def test_an_unchanged_issue_is_not_rewritten_and_a_new_comment_is(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path)
    comments = [("Sam Okoro", "rolled back")]
    monkeypatch.setattr(sync, "search_issues",
                        lambda *_a, **_k: ([_issue(comments=comments)], False))
    assert sync.run(project_id="one", connection_id="c1") == (1, 1)
    assert sync.run(project_id="one", connection_id="c1") == (1, 0)
    comments.append(("Dana Reed", "confirmed"))
    assert sync.run(project_id="one", connection_id="c1") == (1, 1)
    source = next((tmp_path / "sources/jira").rglob("ACME-1.md"))
    assert "rolled back" in source.read_text() and "confirmed" in source.read_text()


def test_the_same_issue_in_two_projects_does_not_overwrite_itself(monkeypatch, tmp_path):
    rows = _connected(monkeypatch, tmp_path)
    monkeypatch.setattr(sync, "search_issues", lambda *_a, **_k: ([_issue()], False))
    sync.run(project_id="one", connection_id="c1")
    sync.run(project_id="two", connection_id="c1")
    assert rows[0]["id"] != rows[1]["id"] and rows[0]["path"] != rows[1]["path"]


def test_a_capped_run_reports_partial_so_the_watermark_stays_put(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path)
    monkeypatch.setattr(sync, "search_issues", lambda *_a, **_k: ([_issue()], True))
    result = sync.run(project_id="one", connection_id="c1", max_items=1)
    assert result == (1, 1) and not result.complete
    assert result.failures[0]["id"] == "jira-backlog"


def test_one_unreadable_issue_does_not_lose_the_rest(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path)
    monkeypatch.setattr(sync, "search_issues",
                        lambda *_a, **_k: ([_issue("ACME-1"), _issue("ACME-2")], False))
    real = sync.render_issue
    monkeypatch.setattr(sync, "render_issue", lambda issue, comments=None: (
        (_ for _ in ()).throw(RuntimeError("malformed ADF")) if issue["key"] == "ACME-1"
        else real(issue, comments)))
    result = sync.run(project_id="one", connection_id="c1")
    assert result == (2, 1) and not result.complete
    assert result.failures[0]["error"] == "malformed ADF"


def test_scope_is_validated_before_the_first_remote_call(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path)
    monkeypatch.setattr(sync, "search_issues", Mock(side_effect=AssertionError("must not fetch")))
    with pytest.raises(ValueError, match="max_items"):
        sync.run(project_id="one", connection_id="c1", max_items=-1)


def test_the_connection_checkpoint_drives_the_incremental_window(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path)
    monkeypatch.setattr(sync.connections, "watermark", lambda _cid: "2026-09-20T09:00:00+00:00")
    seen = []
    monkeypatch.setattr(sync, "search_issues", lambda _s, jql, _m: (seen.append(jql), ([], False))[1])
    sync.run(project_id="one", connection_id="c1")
    assert seen[0].startswith("updated >= -")


# --- the one thing a site can differ on ----------------------------------------

def test_comments_that_arrive_with_the_search_cost_no_extra_request(monkeypatch, tmp_path):
    _connected(monkeypatch, tmp_path, {"comments": "yes"})
    monkeypatch.setattr(sync, "search_issues",
                        lambda *_a, **_k: ([_issue(comments=[("Sam Okoro", "inline")])], False))
    monkeypatch.setattr(sync, "_comments", Mock(side_effect=AssertionError("must not refetch")))
    assert sync.run(project_id="one", connection_id="c1") == (1, 1)


def _issue_without_inline_comments():
    issue = _issue()
    issue["fields"].pop("comment")
    return issue


def _separate_comment_fetcher(monkeypatch):
    fetch = Mock(return_value=[{"author": {"displayName": "Sam Okoro"},
                                "created": "2026-09-20T10:00:00.000+0000",
                                "body": _adf("fetched separately")}])
    monkeypatch.setattr(sync, "_comments", fetch)
    monkeypatch.setattr(sync, "search_issues",
                        lambda *_a, **_k: ([_issue_without_inline_comments()], False))
    return fetch


def test_a_site_that_omits_comments_costs_no_extra_request_by_default(monkeypatch, tmp_path):
    rows = _connected(monkeypatch, tmp_path)
    fetch = _separate_comment_fetcher(monkeypatch)
    sync.run(project_id="one", connection_id="c1")
    fetch.assert_not_called()
    assert "fetched separately" not in (tmp_path / rows[-1]["path"]).read_text()


def test_a_site_that_omits_comments_reads_them_once_the_user_opts_in(monkeypatch, tmp_path):
    rows = _connected(monkeypatch, tmp_path, {"comments": "yes"})
    fetch = _separate_comment_fetcher(monkeypatch)
    sync.run(project_id="one", connection_id="c1")
    fetch.assert_called_once_with({**SETTINGS, "comments": "yes"}, "ACME-1")
    assert "fetched separately" in (tmp_path / rows[-1]["path"]).read_text()


# --- probe ---------------------------------------------------------------------

def test_probe_names_the_account_and_says_whether_comments_came_inline(monkeypatch):
    monkeypatch.setattr(sync, "_request", lambda _s, _p, body=None: {
        "displayName": "Dana Reed", "emailAddress": "you@acme.com"})
    monkeypatch.setattr(sync, "search_issues", lambda *_a, **_k: ([_issue()], True))
    out = sync.probe(SETTINGS, limit=2)
    assert out["site"] == "acme.atlassian.net"
    assert out["account"] == "Dana Reed <you@acme.com>"
    assert out["items"] == [{"id": "ACME-1", "name": "Deploy is flaky", "comments_inline": True}]
    assert out["more_available"] is True

# --- which credential a connection uses ----------------------------------------

def test_a_pasted_token_talks_to_the_site_host():
    import base64
    base, header = sync._transport(SETTINGS)
    assert base == "https://acme.atlassian.net"
    assert header == "Basic " + base64.b64encode(b"you@acme.com:secret-token").decode()


def test_a_sign_in_talks_to_the_gateway_under_the_granted_cloud_id(monkeypatch):
    monkeypatch.setattr(sync.auth, "site_for",
                        lambda preferred="": {"host": "acme.atlassian.net", "cloud_id": "cloud-abc"})
    monkeypatch.setattr(sync.auth, "access_token", lambda: "at-1")
    assert sync._transport({}) == ("https://api.atlassian.com/ex/jira/cloud-abc", "Bearer at-1")


def test_a_signed_in_connection_needs_no_site_typed_in(monkeypatch):
    monkeypatch.setattr(sync.auth, "site_for",
                        lambda preferred="": {"host": "acme.atlassian.net", "cloud_id": "c"})
    assert sync._site({}) == "acme.atlassian.net"


def test_a_token_connection_must_still_say_which_site_it_means():
    with pytest.raises(ValueError, match="site URL"):
        sync._site({"token": "t", "email": "you@acme.com"})


def test_a_revoked_sign_in_is_told_to_sign_in_again_not_to_check_its_token(monkeypatch):
    import urllib.error

    monkeypatch.setattr(sync.auth, "site_for",
                        lambda preferred="": {"host": "acme.atlassian.net", "cloud_id": "c"})
    monkeypatch.setattr(sync.auth, "access_token", lambda: "at-1")
    monkeypatch.setattr(sync.urllib.request, "urlopen", Mock(
        side_effect=urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)))
    with pytest.raises(PermissionError, match="Sign in to Atlassian again"):
        sync._request({}, sync.IDENTITY)
