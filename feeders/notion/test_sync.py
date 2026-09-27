"""Offline checks for the Notion feeder. No network, no database."""
import urllib.error
from unittest.mock import Mock

import pytest

from feeders.notion import sync


def _rich(text):
    return [{"plain_text": text}]


def _page(pid="page-1", title="Launch plan", edited="2026-09-20T09:00:00.000Z"):
    return {"id": pid, "url": f"https://notion.so/{pid}",
            "last_edited_time": edited,
            "last_edited_by": {"id": "user-1"},
            "properties": {"Name": {"type": "title", "title": _rich(title)}}}


def _tree(children):
    """A fetch_children stub over {block_id: [block, ...]}."""
    return lambda block_id: children.get(block_id, [])


def test_title_is_found_under_whatever_the_database_calls_that_column():
    assert sync.page_title(_page(title="Launch plan")) == "Launch plan"
    assert sync.page_title({"properties": {"任务": {"type": "title", "title": _rich("Ship it")}}}) == "Ship it"
    assert sync.page_title({"properties": {"Status": {"type": "select"}}}) == "(untitled)"


def test_block_kinds_render_and_unknown_types_keep_their_text():
    blocks = [
        {"id": "b1", "type": "heading_1", "heading_1": {"rich_text": _rich("Scope")}},
        {"id": "b2", "type": "paragraph", "paragraph": {"rich_text": _rich("We ship in March.")}},
        {"id": "b3", "type": "to_do", "to_do": {"rich_text": _rich("Sign the lease"), "checked": True}},
        {"id": "b4", "type": "code", "code": {"rich_text": _rich("print(1)"), "language": "python"}},
        {"id": "b5", "type": "table_row", "table_row": {"cells": [_rich("Q1"), _rich("42")]}},
        {"id": "b6", "type": "bookmark", "bookmark": {"url": "https://example.com", "caption": []}},
        {"id": "b7", "type": "breadcrumb", "breadcrumb": {}},
        {"id": "b8", "type": "future_block", "future_block": {"rich_text": _rich("Still readable")}},
    ]
    lines = sync.render_children(_tree({"root": blocks}), "root")
    assert lines == ["# Scope", "We ship in March.", "- [x] Sign the lease",
                     "```python", "print(1)", "```", "| Q1 | 42 |",
                     "[bookmark] https://example.com", "Still readable"]


def test_nested_children_are_followed_and_a_cycle_stops_at_the_depth_guard():
    nested = {"id": "outer", "type": "toggle", "has_children": True,
              "toggle": {"rich_text": _rich("Details")}}
    inner = {"id": "inner", "type": "paragraph", "has_children": False,
             "paragraph": {"rich_text": _rich("Hidden note")}}
    lines = sync.render_children(_tree({"root": [nested], "outer": [inner]}), "root")
    assert lines == ["- Details", "    Hidden note"]

    # A synced block pointing back at its own parent would otherwise walk forever.
    loop = {"id": "root", "type": "toggle", "has_children": True,
            "toggle": {"rich_text": _rich("Loop")}}
    rendered = sync.render_children(_tree({"root": [loop]}), "root")
    assert sum(line.strip() == "Loop" or line.strip() == "- Loop" for line in rendered) == sync.MAX_BLOCK_DEPTH
    assert "not followed" in rendered[-1]


def test_search_stops_at_the_watermark_without_reading_older_pages(monkeypatch):
    results = [_page("new", edited="2026-09-22T10:00:00.000Z"),
               _page("old", edited="2026-09-01T10:00:00.000Z")]
    monkeypatch.setattr(sync, "_request",
                        lambda *a, **k: {"results": results, "has_more": False})
    pages, truncated = sync.search_pages("tok", edited_after="2026-09-20T09:00:00.123456+00:00")
    assert [p["id"] for p in pages] == ["new"] and truncated is False


def test_a_capped_run_reports_a_backlog_so_the_watermark_stays_put(monkeypatch):
    monkeypatch.setattr(sync, "_request", lambda *a, **k: {
        "results": [_page("a"), _page("b")], "has_more": True, "next_cursor": "c"})
    pages, truncated = sync.search_pages("tok", max_items=1)
    assert len(pages) == 1 and truncated is True


def test_search_pages_asks_for_pages_newest_first(monkeypatch):
    sent = []

    def request(token, method, path, body=None, params=None):
        sent.append((method, path, body))
        return {"results": [], "has_more": False}

    monkeypatch.setattr(sync, "_request", request)
    sync.search_pages("tok")
    method, path, body = sent[0]
    assert (method, path) == ("POST", "search")
    assert body["filter"] == {"property": "object", "value": "page"}
    assert body["sort"] == {"timestamp": "last_edited_time", "direction": "descending"}


def test_rate_limited_request_waits_the_retry_after_it_was_given(monkeypatch):
    slept = []
    monkeypatch.setattr(sync.time, "sleep", slept.append)
    attempts = []

    class _Body:
        def read(self):
            return b'{"results": []}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def urlopen(request, timeout=0):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down",
                                         {"Retry-After": "7"}, None)
        return _Body()

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    assert sync._request("tok", "POST", "search", {}) == {"results": []}
    assert 7 in slept and len(attempts) == 2


def test_a_bad_secret_says_where_to_get_a_good_one(monkeypatch):
    def urlopen(request, timeout=0):
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(sync.time, "sleep", lambda _s: None)
    with pytest.raises(sync.NotionError, match="my-integrations"):
        sync._request("tok", "GET", "users/me")


def test_an_unshared_workspace_is_named_as_a_sharing_problem(monkeypatch):
    monkeypatch.setattr(sync, "_identity", lambda token: ("Cairn", "Acme"))
    monkeypatch.setattr(sync, "search_pages", lambda *a, **k: ([], False))
    with pytest.raises(sync.NotionError, match="Connections"):
        sync.probe({"token": "ntn_x"})


def test_probe_without_a_secret_asks_for_one():
    with pytest.raises(sync.NotionError, match="my-integrations"):
        sync.probe({})


def test_probe_lists_pages_and_the_workspace_it_read_them_from(monkeypatch):
    monkeypatch.setattr(sync, "_identity", lambda token: ("Cairn", "Acme"))
    monkeypatch.setattr(sync, "search_pages", lambda *a, **k: ([_page()], False))
    result = sync.probe({"token": "ntn_x"}, limit=1)
    assert result["account"] == "Cairn · Acme"
    assert result["items"] == [{"id": "page-1", "name": "Launch plan",
                               "last_edited_time": "2026-09-20T09:00:00.000Z"}]


def test_an_integration_without_user_read_keeps_the_raw_id(monkeypatch):
    def request(*_a, **_k):
        raise sync.NotionError("Insufficient permissions for this endpoint.")

    monkeypatch.setattr(sync, "_request", request)
    cache = {}
    assert sync.user_name("tok", "user-1", cache) == "user-1"
    assert cache == {"user-1": "user-1"}


def _run_offline(monkeypatch, tmp_path, pages, blocks):
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync, "_identity", lambda token: ("Cairn", "Acme"))
    monkeypatch.setattr(sync, "search_pages", lambda *a, **k: (pages, False))
    monkeypatch.setattr(sync, "child_blocks", lambda token, bid: blocks.get(bid, []))
    monkeypatch.setattr(sync, "user_name", lambda token, uid, cache: "Dana")
    monkeypatch.setattr("server.connections.settings_for", lambda cid: {"token": "ntn_x"})
    rows = []
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.append(row))
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    return rows


def test_a_resync_rewrites_only_the_pages_that_changed(monkeypatch, tmp_path):
    body = [{"id": "b1", "type": "paragraph", "paragraph": {"rich_text": _rich("First draft")}}]
    blocks = {"page-1": body}
    rows = _run_offline(monkeypatch, tmp_path, [_page()], blocks)

    assert sync.run(project_id="one", connection_id="notion-1", modified_after="") == (1, 1)
    assert sync.run(project_id="one", connection_id="notion-1", modified_after="") == (1, 0)
    blocks["page-1"] = body + [{"id": "b2", "type": "paragraph",
                               "paragraph": {"rich_text": _rich("Second draft")}}]
    assert sync.run(project_id="one", connection_id="notion-1", modified_after="") == (1, 1)

    text = (tmp_path / rows[-1]["path"]).read_text()
    assert "First draft" in text and "Second draft" in text
    entry = (tmp_path / "raw" / "inbox" / f"{rows[-1]['id']}.md").read_text()
    assert 'authors: ["Dana"]' in entry and "date: 2026-09-20" in entry
    assert 'time: "09:00:00"' in entry


def test_two_projects_never_share_a_page_id(monkeypatch, tmp_path):
    rows = _run_offline(monkeypatch, tmp_path, [_page()], {"page-1": []})
    sync.run(project_id="one", connection_id="notion-1", modified_after="")
    sync.run(project_id="two", connection_id="notion-1", modified_after="")
    assert rows[0]["id"] != rows[-1]["id"]


def test_one_unreadable_page_does_not_lose_the_others(monkeypatch, tmp_path):
    rows = _run_offline(monkeypatch, tmp_path,
                        [_page("page-1"), _page("page-2", title="Broken")], {})

    def render(token, page):
        if page["id"] == "page-2":
            raise sync.NotionError("Notion returned 404 for GET blocks")
        return "# Launch plan\n\nok\n"

    monkeypatch.setattr(sync, "render_page", render)
    result = sync.run(project_id="one", connection_id="notion-1", modified_after="")
    assert (result.seen, result.written, result.failed) == (2, 1, 1)
    assert result.complete is False
    assert [row["name"] for row in rows] == ["Launch plan"]


def test_a_capped_sync_refuses_to_certify_the_window(monkeypatch, tmp_path):
    _run_offline(monkeypatch, tmp_path, [_page()], {"page-1": []})
    monkeypatch.setattr(sync, "search_pages", lambda *a, **k: ([_page()], True))
    result = sync.run(project_id="one", connection_id="notion-1", modified_after="", max_items=1)
    assert result.complete is False
    assert "max_items" in result.failures[-1]["error"]


def test_a_connection_without_a_secret_says_so(monkeypatch):
    monkeypatch.setattr("server.connections.settings_for", lambda cid: {})
    with pytest.raises(sync.NotionError, match="integration secret"):
        sync.run(project_id="one", connection_id="notion-1")


def test_an_incremental_run_asks_the_connection_for_its_watermark(monkeypatch, tmp_path):
    _run_offline(monkeypatch, tmp_path, [], {})
    monkeypatch.setattr("server.connections.watermark", lambda cid: "2026-09-20T09:00:00+00:00")
    asked = []
    monkeypatch.setattr(sync, "search_pages",
                        lambda token, edited_after="", max_items=0: (asked.append(edited_after), ([], False))[1])
    sync.run(project_id="one", connection_id="notion-1")
    assert asked == ["2026-09-20T09:00:00+00:00"]
