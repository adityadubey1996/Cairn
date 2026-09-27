#!/usr/bin/env python3
"""Offline checks for the Confluence connector. No network, no database."""
import json
import sys
import urllib.error
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from feeders.confluence import auth, storage_format, sync  # noqa: E402

SAMPLE = (Path(__file__).parent / "testdata" / "sample_page.xhtml").read_text()
SETTINGS: dict = {}
CLOUD = "11223344-a1b2-3b33-c444-def123456789"


@pytest.fixture(autouse=True)
def signed_in(monkeypatch, tmp_path):
    """Every test runs as an account that has completed the consent, with the
    token file under tmp_path so no test can read or write the real one."""
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    monkeypatch.setattr(auth, "TOKEN_FILE", secrets / "confluence-oauth.json")
    monkeypatch.setattr(auth, "_access", {"token": "live-access-token", "expires": 1e18})
    auth.TOKEN_FILE.write_text(json.dumps(
        {"refresh_token": "r1", "cloud_id": CLOUD,
         "site_url": "https://team.atlassian.net", "account": "you@example.com"}))


def _page(page_id="1001", title="Release checklist", modified="2026-09-20T09:00:00.000Z",
          body=SAMPLE, author="Ada Lovelace"):
    return {"id": page_id, "title": title,
            "version": {"createdAt": modified, "by": {"displayName": author}},
            "body": {"storage": {"value": body}},
            "_links": {"webui": f"/spaces/ENG/pages/{page_id}/Release"}}


# --- storage format ---------------------------------------------------------

def test_storage_format_keeps_structure_and_drops_macro_plumbing():
    text = storage_format.to_text(SAMPLE)
    assert "# Release checklist" in text and "## Before the cut" in text
    assert "- Drain the queue\n- Tag the release" in text
    assert "1. Run migrations\n2. Flip the flag" in text
    assert "```\n./deploy.sh --env prod\n./smoke.sh\n```" in text
    assert "| Service | Owner |\n| --- | --- |\n| api | Ada |" in text
    assert "[the runbook](https://example.com/runbook)" in text
    assert "Deploy policy" in text, "an internal link must keep its page target"
    assert "Freeze starts at 17:00." in text, "a macro body is prose, not plumbing"
    assert "Heads up" not in text and "schema-version" not in text
    assert "ac:" not in text and "CDATA" not in text


def test_a_body_too_large_to_extract_is_refused():
    with pytest.raises(ValueError, match="5 MB"):
        storage_format.to_text("<p>" + "x" * storage_format.MAX_STORAGE_BYTES + "</p>")


# --- sign-in ----------------------------------------------------------------

def test_consent_asks_for_the_scopes_the_v2_api_checks(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_CLIENT_ID", "client-123")
    url = auth.consent_url("http://localhost:8310/api/confluence/callback", "state-abc")
    query = parse_qs(urlsplit(url).query)
    assert url.startswith(auth.AUTHORIZE)
    assert query["audience"] == ["api.atlassian.com"] and query["client_id"] == ["client-123"]
    assert query["state"] == ["state-abc"] and query["response_type"] == ["code"]
    scopes = query["scope"][0].split()
    assert "read:page:confluence" in scopes and "read:space:confluence" in scopes
    assert "offline_access" in scopes, "without it no refresh token comes back"
    assert query["redirect_uri"] == ["http://localhost:8310/api/confluence/callback"]


def test_exchange_stores_the_cloud_id_and_never_the_access_token(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_post", lambda *_a: {
        "access_token": "ACCESS-NOT-DURABLE", "refresh_token": "r1", "expires_in": 3600})
    monkeypatch.setattr(auth, "_get", lambda url, _token, params=None: (
        [{"id": CLOUD, "url": "https://team.atlassian.net", "name": "Team",
          "scopes": ["read:page:confluence"]}] if url == auth.RESOURCES
        else {"email": "you@example.com", "name": "Ada"}))
    label = auth.exchange_code("code", "http://localhost:8310/api/confluence/callback")
    stored = json.loads(auth.TOKEN_FILE.read_text())
    assert "you@example.com" in label and "Team" in label
    assert stored["cloud_id"] == CLOUD and stored["refresh_token"] == "r1"
    assert "ACCESS-NOT-DURABLE" not in auth.TOKEN_FILE.read_text(), \
        "only the refresh token is durable state"
    assert auth.api_base().endswith(f"/ex/confluence/{CLOUD}")


def test_a_rotated_refresh_token_reaches_disk_before_it_is_needed(monkeypatch):
    monkeypatch.setattr(auth, "_access", {"token": "", "expires": 0.0})
    monkeypatch.setattr(auth, "_post", lambda *_a: {
        "access_token": "at2", "refresh_token": "r2", "expires_in": 3600})
    assert auth.access_token() == "at2"
    assert json.loads(auth.TOKEN_FILE.read_text())["refresh_token"] == "r2"


def test_an_account_that_granted_no_confluence_site_is_refused(monkeypatch):
    monkeypatch.setattr(auth, "_post", lambda *_a: {"access_token": "at", "refresh_token": "r1"})
    monkeypatch.setattr(auth, "_get", lambda url, _t, params=None: (
        [{"id": "x", "url": "https://team.atlassian.net", "scopes": ["read:jira-work"]}]
        if url == auth.RESOURCES else {}))
    with pytest.raises(auth.ReauthRequired, match="no Confluence site"):
        auth.exchange_code("code", "http://localhost:8310/api/confluence/callback")


def test_a_connector_with_no_sign_in_yet_says_so(monkeypatch):
    monkeypatch.setattr(auth, "_access", {"token": "", "expires": 0.0})
    auth.TOKEN_FILE.unlink()
    with pytest.raises(auth.ReauthRequired, match="Press Connect"):
        auth.access_token()


# --- listing ----------------------------------------------------------------

def test_listing_follows_the_cursor_until_it_runs_out(monkeypatch):
    calls = []

    def request(path, params=None):
        calls.append(path)
        if "cursor" in path:
            return {"results": [_page("2")], "_links": {}}
        return {"results": [_page("1")], "_links": {"next": "/wiki/api/v2/pages?cursor=abc"}}

    monkeypatch.setattr(sync, "_request", request)
    pages, truncated = sync.list_pages(SETTINGS)
    assert [p["id"] for p in pages] == ["1", "2"] and not truncated
    assert calls == ["/wiki/api/v2/pages", "/wiki/api/v2/pages?cursor=abc"]


def test_the_watermark_stops_paging_rather_than_filtering(monkeypatch):
    newest = _page("new", modified="2026-09-20T09:00:00.000Z")
    older = _page("old", modified="2026-09-01T09:00:00.000Z")
    # A "next" cursor the watermark must never follow.
    monkeypatch.setattr(sync, "_request", lambda *_a, **_k: {
        "results": [newest, older], "_links": {"next": "/wiki/api/v2/pages?cursor=more"}})
    pages, truncated = sync.list_pages(SETTINGS, modified_after="2026-09-10T00:00:00+00:00")
    assert [p["id"] for p in pages] == ["new"] and not truncated


def test_unknown_spaces_are_refused_before_any_page_is_read(monkeypatch):
    monkeypatch.setattr(sync, "_request", lambda *_a, **_k: {"results": [{"id": "5", "key": "ENG"}]})
    with pytest.raises(sync.NotConfigured, match="NOPE"):
        sync.list_pages({**SETTINGS, "spaces": "ENG, NOPE"})


def test_a_rate_limit_waits_for_retry_after(monkeypatch):
    slept, attempts = [], []

    def urlopen(request, timeout=0):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down",
                                         {"Retry-After": "7"}, None)
        return _Response(b'{"results": []}')

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sync.time, "sleep", slept.append)
    assert sync._request("/wiki/api/v2/pages") == {"results": []}
    assert slept == [7.0] and len(attempts) == 2


def test_a_revoked_grant_says_to_connect_again(monkeypatch):
    def urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    with pytest.raises(auth.ReauthRequired, match="Press Connect again"):
        sync._request("/wiki/api/v2/pages")


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# --- run --------------------------------------------------------------------

def _sandbox(monkeypatch, tmp_path, pages, truncated=False):
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync.config, "ROOT", tmp_path)
    monkeypatch.setattr(sync.connections, "settings_for", lambda _cid: dict(SETTINGS))
    monkeypatch.setattr(sync, "list_pages", lambda *_a, **_k: (pages, truncated))
    rows = {}
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.update({row["id"]: row}))
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    return rows


def test_a_page_becomes_a_source_an_inbox_entry_and_a_row(monkeypatch, tmp_path):
    rows = _sandbox(monkeypatch, tmp_path, [_page()])
    assert sync.run(project_id="p", connection_id="confluence-1") == (1, 1)
    row = next(iter(rows.values()))
    assert row["kind"] == "confluence" and row["name"] == "Release checklist"
    assert row["authors"] == ["Ada Lovelace"]
    assert row["url"] == "https://team.atlassian.net/wiki/spaces/ENG/pages/1001/Release"
    source = tmp_path / row["path"]
    assert source.is_file() and "## Before the cut" in source.read_text()
    entry = next((tmp_path / "raw/inbox").glob("*.md")).read_text()
    assert row["path"] in entry and "date: 2026-09-20" in entry


def test_an_unchanged_page_is_not_rewritten(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path, [_page()])
    sync.run(project_id="p", connection_id="confluence-1")
    assert sync.run(project_id="p", connection_id="confluence-1") == (1, 0)


def test_an_unreadable_page_fails_alone_and_blocks_the_watermark(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path, [_page("1"), _page("2", body="<p>" + "x" * 6_000_000 + "</p>")])
    result = sync.run(project_id="p", connection_id="confluence-1")
    assert result == (2, 1) and not result.complete
    assert result.failures[0]["id"].endswith("-2")


def test_a_capped_run_reports_partial_so_the_watermark_stays_put(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path, [_page()], truncated=True)
    result = sync.run(project_id="p", connection_id="confluence-1", max_items=1)
    assert result.written == 1 and not result.complete
    assert result.failures[-1]["id"] == "confluence-backlog"


def test_a_nonsense_item_cap_is_refused_before_the_first_request(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path, [_page()])
    monkeypatch.setattr(sync, "list_pages", Mock(side_effect=AssertionError("must not list")))
    with pytest.raises(ValueError, match="max_items"):
        sync.run(project_id="p", connection_id="confluence-1", max_items=-1)


def test_probe_lists_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync, "current_user", lambda: "Ada Lovelace")
    monkeypatch.setattr(sync, "list_pages", lambda *_a, **_k: ([_page()], True))
    monkeypatch.setattr(sync, "_space_ids", lambda _s: [])
    result = sync.probe(SETTINGS, limit=2)
    assert result["account"] == "Ada Lovelace" and result["more_available"]
    assert result["items"][0]["extracted_characters"] > 0
    assert list(tmp_path.iterdir()) == [tmp_path / "secrets"], "a probe writes nothing"


def test_the_spec_declares_the_consent_the_generic_router_serves():
    from feeders.confluence.connector import SPEC

    assert SPEC.auth == "oauth" and SPEC.oauth is not None
    # server/routers/oauth.py routes on this, and ConnectorPicker.jsx sends a
    # non-Google kind to /api/<kind>/authorize — so the two must be equal or
    # the Connect button lands on a 404.
    assert SPEC.oauth.provider == SPEC.id == "confluence"
    assert SPEC.requires == ("CONFLUENCE_CLIENT_ID", "CONFLUENCE_CLIENT_SECRET")


def test_a_cursor_link_is_readdressed_through_the_cloud_id(monkeypatch):
    seen = []

    def urlopen(request, timeout=0):
        seen.append(request.full_url)
        return _Response(b"{}")

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    sync._request("https://team.atlassian.net/wiki/api/v2/pages?cursor=abc")
    sync._request("/wiki/api/v2/pages")
    assert seen == [f"https://api.atlassian.com/ex/confluence/{CLOUD}/wiki/api/v2/pages?cursor=abc",
                    f"https://api.atlassian.com/ex/confluence/{CLOUD}/wiki/api/v2/pages"]
