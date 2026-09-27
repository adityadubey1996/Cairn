"""Offline checks for the GitLab feeder. No network, no database.

The HTTP layer is stubbed throughout; the host checks assert that no socket is
opened for a rejected host, not merely that the fetch eventually fails.
"""
import io
import urllib.error

import pytest

from feeders.gitlab import sync


# --- host: the trust boundary --------------------------------------------

@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Resolve every test host to a public address unless a test says otherwise."""
    monkeypatch.setattr(sync.socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.delenv(sync.ALLOW_PRIVATE, raising=False)


@pytest.mark.parametrize("host, expected", [
    ("gitlab.com", "https://gitlab.com/api/v4"),
    ("https://gitlab.com", "https://gitlab.com/api/v4"),
    ("https://gitlab.example.com/", "https://gitlab.example.com/api/v4"),
    # A self-hosted instance under a relative URL root keeps it.
    ("https://example.com/gitlab", "https://example.com/gitlab/api/v4"),
    ("gitlab.example.com:8443", "https://gitlab.example.com:8443/api/v4"),
])
def test_accepted_hosts_resolve_to_their_api_base(host, expected):
    assert sync.base_url(host) == expected


@pytest.mark.parametrize("host", [
    "",
    "http://gitlab.com",                      # plaintext
    "ftp://gitlab.com",
    "https://user:glpat-secret@gitlab.com",   # credentials in the URL
    "https://glpat-secret@gitlab.com",
    "https://gitlab.com?token=x",
])
def test_unsafe_hosts_are_refused(host):
    with pytest.raises(sync.HostNotAllowed):
        sync.base_url(host)


@pytest.mark.parametrize("address", [
    "127.0.0.1",        # loopback
    "10.0.0.5",         # RFC1918
    "192.168.1.10",
    "169.254.169.254",  # cloud metadata
    "0.0.0.0",
])
def test_hosts_resolving_inside_the_network_are_refused(monkeypatch, address):
    monkeypatch.setattr(sync.socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(sync.HostNotAllowed) as refusal:
        sync.base_url("gitlab.internal")
    assert address in str(refusal.value)


def test_a_private_host_is_allowed_only_when_the_operator_says_so(monkeypatch):
    monkeypatch.setattr(sync.socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    monkeypatch.setenv(sync.ALLOW_PRIVATE, "1")
    assert sync.base_url("gitlab.internal") == "https://gitlab.internal/api/v4"


def test_one_public_answer_does_not_excuse_a_private_one(monkeypatch):
    monkeypatch.setattr(sync.socket, "getaddrinfo", lambda host, *a, **k: [
        (2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(sync.HostNotAllowed):
        sync.base_url("gitlab.example.com")


def test_an_unresolvable_host_is_refused_not_fetched(monkeypatch):
    monkeypatch.setattr(sync.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no such host")))
    with pytest.raises(sync.HostNotAllowed):
        sync.base_url("gitlab.nowhere")


@pytest.mark.parametrize("host", ["http://gitlab.com", "https://user:pw@gitlab.com",
                                  "gitlab.internal"])
def test_a_rejected_host_never_opens_a_connection(monkeypatch, host):
    monkeypatch.setattr(sync.socket, "getaddrinfo",
                        lambda name, *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    monkeypatch.setattr(sync._opener, "open", _must_not_connect)
    with pytest.raises(sync.HostNotAllowed):
        sync._get(sync.base_url(host), "glpat-x", "user")


def test_a_redirect_into_the_network_is_refused(monkeypatch):
    """The PRIVATE-TOKEN header travels with a redirect, so the new host is
    checked exactly like the first one."""
    monkeypatch.setattr(sync.socket, "getaddrinfo", lambda host, *a, **k: [
        (2, 1, 6, "", ("127.0.0.1" if host == "internal.local" else "93.184.216.34", 443))])
    handler = sync._SafeRedirect()
    with pytest.raises(sync.HostNotAllowed):
        handler.redirect_request(None, None, 302, "Found", {},
                                 "https://internal.local/api/v4/user")


def _must_not_connect(*args, **kwargs):
    raise AssertionError("no connection may be opened for a rejected host")


# --- HTTP behaviour -------------------------------------------------------

class _Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_paging_follows_x_next_page_not_an_offset(monkeypatch):
    pages = [(b'[{"iid": 1}]', {"x-next-page": "2"}),
             (b'[{"iid": 2}]', {"x-next-page": ""})]
    seen = []

    def fake_open(request, timeout=None):
        seen.append(request.full_url)
        return _Response(*pages[len(seen) - 1])

    monkeypatch.setattr(sync._opener, "open", fake_open)
    rows, more = sync._paged("https://gitlab.com/api/v4", "glpat-x", "projects/1/issues")
    assert [row["iid"] for row in rows] == [1, 2] and more is False
    assert "&page=2" in seen[1] and "&page=" not in seen[0]


def test_a_429_backs_off_for_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(sync.time, "sleep", slept.append)
    attempts = []

    def fake_open(request, timeout=None):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests",
                                         {"Retry-After": "7"}, None)
        return _Response(b'{"username": "ada"}')

    monkeypatch.setattr(sync._opener, "open", fake_open)
    assert sync.identity("https://gitlab.com/api/v4", "glpat-x") == "ada"
    assert slept == [7.0]


def test_a_rejected_token_names_the_scope_to_fix(monkeypatch):
    def fake_open(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(sync._opener, "open", fake_open)
    with pytest.raises(PermissionError, match="read_api"):
        sync.identity("https://gitlab.com/api/v4", "glpat-x")


# --- shape ----------------------------------------------------------------

def test_a_thread_reference_keeps_nested_groups_and_separates_issues_from_mrs():
    project = {"id": 7, "path_with_namespace": "group/subgroup/project"}
    assert sync.thread_ref(project, "issue", {"iid": 12}) == "group/subgroup/project#12"
    assert sync.thread_ref(project, "merge_request", {"iid": 12}) == "group/subgroup/project!12"


def test_project_paths_rejects_anything_that_is_not_a_path():
    assert sync.project_paths(" group/a , group/sub/b ,") == ["group/a", "group/sub/b"]
    assert sync.project_paths(None) == []
    with pytest.raises(ValueError):
        sync.project_paths("justaname")


def test_render_keeps_the_discussion_and_credits_every_speaker():
    project = {"id": 7, "path_with_namespace": "group/sub/project"}
    item = {"iid": 3, "title": "Pump fails", "state": "opened", "web_url": "https://g/i/3",
            "author": {"name": "Ada"}, "description": "It **fails**.",
            "created_at": "2026-09-01T09:00:00.000Z", "updated_at": "2026-09-20T11:30:00.000Z",
            "labels": ["bug"]}
    notes = [{"author": {"name": "Linus"}, "body": "Reproduced.", "created_at": "2026-09-02T10:00:00Z"}]
    title, text, authors, updated = sync.render_thread(project, "issue", item, notes)
    assert title == "group/sub/project#3 Pump fails"
    assert "It **fails**." in text and "Reproduced." in text and "Comment by Linus" in text
    assert authors == ["Ada", "Linus"]
    assert updated == "2026-09-20T11:30:00.000Z"


def test_system_notes_are_dropped(monkeypatch):
    monkeypatch.setattr(sync, "_get", lambda *a, **k: (
        [{"body": "changed the label", "system": True}, {"body": "Real comment"}], {}))
    assert [n["body"] for n in sync.list_notes("b", "t", 1, "issue", 3)] == ["Real comment"]


# --- run ------------------------------------------------------------------

PROJECT = {"id": 7, "path_with_namespace": "group/sub/project"}


def _issue(iid=3, updated="2026-09-20T11:30:00.000Z", description="Body"):
    return {"iid": iid, "title": f"Issue {iid}", "state": "opened", "description": description,
            "web_url": f"https://gitlab.com/group/sub/project/-/issues/{iid}",
            "author": {"name": "Ada"}, "created_at": "2026-09-01T09:00:00.000Z",
            "updated_at": updated}


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    rows = {}
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.update({row["id"]: row}))
    monkeypatch.setattr(sync.sources_index, "record_failure", lambda **row: None)
    monkeypatch.setattr(sync.connections, "settings_for",
                        lambda _cid: {"host": "gitlab.com", "token": "glpat-x"})
    monkeypatch.setattr(sync.connections, "watermark", lambda _cid: "2026-09-19T00:00:00Z")
    monkeypatch.setattr(sync, "identity", lambda *a: "ada")
    monkeypatch.setattr(sync, "list_projects", lambda *a: [PROJECT])
    monkeypatch.setattr(sync, "list_notes", lambda *a: [])
    return tmp_path, rows


def test_a_run_writes_a_source_an_inbox_entry_and_a_row(sandbox, monkeypatch):
    tmp_path, rows = sandbox
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: ([{"project": PROJECT, "kind": "issue", "item": _issue()}], False))
    result = sync.run(project_id="p", connection_id="c")
    assert result == (1, 1) and result.complete
    source = next((tmp_path / "sources/gitlab").rglob("7-issue-3.md"))
    assert "group/sub/project#3 Issue 3" in source.read_text()
    entry = next((tmp_path / "raw/inbox").glob("gitlab-*-7-issue-3.md")).read_text()
    assert "date: 2026-09-20" in entry and 'time: "11:30:00"' in entry
    assert 'authors: ["Ada"]' in entry
    row = next(iter(rows.values()))
    assert row["kind"] == "gitlab" and row["connection_id"] == "c"
    assert row["path"] == source.relative_to(tmp_path).as_posix()


def test_an_unchanged_thread_is_not_rewritten(sandbox, monkeypatch):
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: ([{"project": PROJECT, "kind": "issue", "item": _issue()}], False))
    assert sync.run(project_id="p", connection_id="c") == (1, 1)
    assert sync.run(project_id="p", connection_id="c") == (1, 0)


def test_a_new_comment_rewrites_the_thread(sandbox, monkeypatch):
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: ([{"project": PROJECT, "kind": "issue", "item": _issue()}], False))
    assert sync.run(project_id="p", connection_id="c") == (1, 1)
    monkeypatch.setattr(sync, "list_notes", lambda *a: [{"author": {"name": "Linus"}, "body": "Fixed"}])
    assert sync.run(project_id="p", connection_id="c") == (1, 1)


def test_one_failed_thread_leaves_the_others_and_the_watermark(sandbox, monkeypatch):
    threads = [{"project": PROJECT, "kind": "issue", "item": _issue(3)},
               {"project": PROJECT, "kind": "merge_request", "item": _issue(4)}]
    monkeypatch.setattr(sync, "list_threads", lambda *a, **k: (threads, False))

    def notes(base, token, project_id, kind, iid):
        if iid == 3:
            raise RuntimeError("provider timeout")
        return []

    monkeypatch.setattr(sync, "list_notes", notes)
    result = sync.run(project_id="p", connection_id="c")
    assert result == (2, 1) and not result.complete
    assert result.failures[0]["name"] == "group/sub/project#3"


def test_a_capped_run_reports_partial_so_the_window_is_retried(sandbox, monkeypatch):
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: ([{"project": PROJECT, "kind": "issue", "item": _issue()}], True))
    result = sync.run(project_id="p", connection_id="c", max_items=1)
    assert result.written == 1 and not result.complete


def test_the_watermark_is_what_narrows_the_fetch(sandbox, monkeypatch):
    asked = []
    monkeypatch.setattr(sync, "list_threads",
                        lambda base, token, projects, updated_after="", max_items=0:
                        (asked.append(updated_after), ([], False))[1])
    sync.run(project_id="p", connection_id="c")
    assert asked == ["2026-09-19T00:00:00Z"]


def test_max_items_is_validated_before_any_request(sandbox, monkeypatch):
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    with pytest.raises(ValueError):
        sync.run(project_id="p", connection_id="c", max_items=-1)


def test_a_probe_authenticates_and_lists_without_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync, "identity", lambda *a: "ada")
    monkeypatch.setattr(sync, "list_projects", lambda *a: [PROJECT])
    monkeypatch.setattr(sync, "list_threads",
                        lambda *a, **k: ([{"project": PROJECT, "kind": "issue", "item": _issue()}], False))
    out = sync.probe({"host": "gitlab.com", "token": "glpat-x"}, limit=2)
    assert out["account"] == "ada" and out["items"][0]["id"] == "group/sub/project#3"
    assert "glpat-x" not in repr(out)
    assert not list(tmp_path.iterdir())


def test_a_probe_without_a_token_says_so():
    with pytest.raises(ValueError, match="read_api"):
        sync.probe({"host": "gitlab.com", "token": "  "})
