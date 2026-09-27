"""Offline checks for the Slack feeder. No network, no database."""
import urllib.error
from unittest.mock import Mock

import pytest

from feeders.slack import sync

NAMES = {"U1": "dana", "U2": "sam", "C9": "general"}


def _message(ts="1789894800.000100", text="hello", user="U1", **extra):
    return {"ts": ts, "text": text, "user": user, **extra}


class _Body:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        import json
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


# --------------------------------------------------------------------------
# mrkdwn — the converter most likely to be wrong
# --------------------------------------------------------------------------
def test_user_mentions_become_names_and_unknown_ids_survive():
    assert sync.to_markdown("hi <@U1> and <@U2>", NAMES) == "hi @dana and @sam"
    assert sync.to_markdown("hi <@U404>", NAMES) == "hi @U404"
    # Slack's own fallback label beats an unresolved id.
    assert sync.to_markdown("hi <@U404|rory>", NAMES) == "hi @rory"


def test_channel_references_become_hash_names():
    assert sync.to_markdown("see <#C123|engineering>") == "see #engineering"
    assert sync.to_markdown("see <#C9>", NAMES) == "see #general"
    assert sync.to_markdown("see <#C404>") == "see #C404"


def test_links_become_plain_or_labelled_markdown():
    assert sync.to_markdown("<https://example.com>") == "https://example.com"
    assert sync.to_markdown("<https://example.com|the docs>") == "[the docs](https://example.com)"
    assert sync.to_markdown("read <https://example.com/a?b=1|this> now") \
        == "read [this](https://example.com/a?b=1) now"


def test_broadcasts_and_user_groups_read_as_mentions():
    assert sync.to_markdown("<!here> standup") == "@here standup"
    assert sync.to_markdown("<!channel>") == "@channel"
    assert sync.to_markdown("<!subteam^S123|@designers> ping") == "@designers ping"


def test_slack_html_escapes_are_undone_without_double_unescaping():
    assert sync.to_markdown("a &amp; b &lt;tag&gt;") == "a & b <tag>"
    # &amp;lt; is a literal "&lt;" the user typed, not a second escape level.
    assert sync.to_markdown("&amp;lt;") == "&lt;"


def test_a_message_with_everything_at_once():
    assert sync.to_markdown(
        "<@U1> see <#C9> &amp; <https://x.dev|x> <!here>", NAMES
    ) == "@dana see #general & [x](https://x.dev) @here"


def test_empty_text_is_not_an_error():
    assert sync.to_markdown("") == "" and sync.to_markdown(None) == ""


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def test_an_http_429_is_retried_after_the_header_it_carried(monkeypatch):
    slept = []
    monkeypatch.setattr(sync.time, "sleep", slept.append)
    attempts = []

    def urlopen(request, timeout=0):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down",
                                         {"Retry-After": "11"}, None)
        return _Body({"ok": True, "channels": []})

    monkeypatch.setattr(sync.urllib.request, "urlopen", urlopen)
    assert sync._call("tok", "conversations.list") == {"ok": True, "channels": []}
    assert 11 in slept and len(attempts) == 2


def test_a_two_hundred_with_ok_false_is_still_a_failure(monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sync.urllib.request, "urlopen",
                        lambda request, timeout=0: _Body({"ok": False, "error": "missing_scope"}))
    with pytest.raises(sync.SlackError, match="channels:history"):
        sync._call("tok", "conversations.history")


def test_a_revoked_token_points_at_the_sign_in(monkeypatch):
    monkeypatch.setattr(sync.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sync.urllib.request, "urlopen",
                        lambda request, timeout=0: _Body({"ok": False, "error": "token_revoked"}))
    with pytest.raises(sync.SlackError, match="signin"):
        sync._call("tok", "auth.test")


def test_paging_follows_the_cursor_and_stops(monkeypatch):
    calls = []

    def call(token, method, params=None):
        calls.append(dict(params or {}))
        if len(calls) == 1:
            return {"ok": True, "members": [{"id": "U1"}],
                    "response_metadata": {"next_cursor": "second"}}
        return {"ok": True, "members": [{"id": "U2"}]}

    monkeypatch.setattr(sync, "_call", call)
    rows = sync._paged("tok", "users.list", "members", {"limit": 200})
    assert [r["id"] for r in rows] == ["U1", "U2"]
    assert calls[1]["cursor"] == "second"


def test_an_endless_cursor_is_refused_rather_than_looped_forever(monkeypatch):
    monkeypatch.setattr(sync, "_call", lambda *a, **k: {
        "ok": True, "messages": [], "response_metadata": {"next_cursor": "always"}})
    with pytest.raises(sync.SlackError, match="without finishing"):
        sync._paged("tok", "conversations.history", "messages", {}, pages=3)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def test_the_incremental_start_is_floored_to_the_watermark_day():
    assert sync.day_floor("") == "0"
    floored = float(sync.day_floor("2026-09-20T14:35:09.123456+00:00"))
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(floored, timezone.utc).isoformat() == "2026-09-20T00:00:00+00:00"


def test_thread_replies_are_fetched_and_the_parent_is_not_duplicated(monkeypatch):
    parent = _message("100.0", "topic", thread_ts="100.0", reply_count=2)
    asked = []

    def paged(token, method, key, params, pages=0):
        asked.append(method)
        if method == "conversations.history":
            return [parent, _message("300.0", "unrelated")]
        return [parent, _message("150.0", "first reply", thread_ts="100.0"),
                _message("200.0", "second reply", thread_ts="100.0")]

    monkeypatch.setattr(sync, "_paged", paged)
    messages = sync.channel_messages("tok", "C1")
    assert [m["ts"] for m in messages] == ["100.0", "150.0", "200.0", "300.0"]
    assert asked.count("conversations.replies") == 1


def test_a_channel_with_no_threads_never_calls_replies(monkeypatch):
    monkeypatch.setattr(sync, "_paged",
                        lambda token, method, key, params, pages=0: [_message("100.0")])
    calls = []
    monkeypatch.setattr(sync, "_paged", lambda token, method, key, params, pages=0:
                        (calls.append(method), [_message("100.0")])[1])
    sync.channel_messages("tok", "C1")
    assert calls == ["conversations.history"]


def test_joins_and_topic_changes_are_not_discussion():
    assert sync.carries_content(_message()) is True
    assert sync.carries_content(_message(text="", subtype="channel_join")) is False
    assert sync.carries_content(_message(text="")) is False
    assert sync.carries_content(_message(text="", files=[{"name": "plan.pdf"}])) is True


def test_a_day_renders_with_names_threads_and_files():
    messages = [_message("1789894800.0", "morning <@U2>"),
                _message("1789898400.0", "reply", user="U2", thread_ts="1789894800.0"),
                _message("1789902000.0", "", user="U1", files=[{"name": "plan.pdf",
                                                               "mimetype": "application/pdf"}])]
    text = sync.render_day("general", "2026-09-20", messages, NAMES)
    assert text.splitlines()[0] == "# #general — 2026-09-20"
    assert "dana: morning @sam" in text
    assert "  ↳ " in text and "sam: reply" in text
    assert "[file] plan.pdf (application/pdf)" in text


def test_channel_selection_rejects_a_channel_you_are_not_in(monkeypatch):
    monkeypatch.setattr(sync, "_paged", lambda *a, **k: [
        {"id": "C1", "name": "general", "is_member": True},
        {"id": "C2", "name": "random", "is_member": False}])
    assert [c["id"] for c in sync.list_channels("tok")] == ["C1"]
    assert [c["id"] for c in sync.list_channels("tok", " #General ")] == ["C1"]
    with pytest.raises(sync.SlackError, match="random"):
        sync.list_channels("tok", "general, random")


def test_a_workspace_policy_that_hides_users_leaves_ids_alone(monkeypatch):
    def paged(*_a, **_k):
        raise sync.SlackError("Slack refused users.list: missing_scope")

    monkeypatch.setattr(sync, "_paged", paged)
    assert sync.user_names("tok") == {}


def test_user_names_prefer_the_display_name(monkeypatch):
    monkeypatch.setattr(sync, "_paged", lambda *a, **k: [
        {"id": "U1", "name": "d.ana", "profile": {"display_name": "dana", "real_name": "Dana R"}},
        {"id": "U2", "name": "sam", "profile": {"real_name": "Sam T"}},
        {"id": "U3", "name": "rory", "profile": {}}])
    assert sync.user_names("tok") == {"U1": "dana", "U2": "Sam T", "U3": "rory"}


# --------------------------------------------------------------------------
# sign-in
# --------------------------------------------------------------------------
def test_the_authorize_url_asks_for_a_user_token_not_a_bot_token():
    url = sync.authorize_url("123.456", "https://localhost:3000/slack/callback", "st8")
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "user_scope=channels%3Ahistory" in url
    assert "&scope=" not in url
    assert "state=st8" in url and "redirect_uri=https%3A%2F%2Flocalhost%3A3000" in url


def test_the_code_exchange_returns_the_user_token(monkeypatch):
    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda request, timeout=0: _Body(
        {"ok": True, "authed_user": {"id": "U1", "access_token": "xoxp-real"},
         "team": {"id": "T1", "name": "Acme"}}))
    assert sync.exchange_code("id", "secret", "code", "https://localhost:3000/slack/callback") == {
        "token": "xoxp-real", "user_id": "U1", "team": "Acme", "team_id": "T1"}


def test_a_bot_only_app_is_named_as_the_problem(monkeypatch):
    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda request, timeout=0: _Body(
        {"ok": True, "access_token": "xoxb-bot", "team": {"id": "T1"}}))
    with pytest.raises(sync.SlackError, match="User Token Scopes"):
        sync.exchange_code("id", "secret", "code", "https://localhost:3000/slack/callback")


def test_a_refused_sign_in_says_what_slack_said(monkeypatch):
    monkeypatch.setattr(sync.urllib.request, "urlopen", lambda request, timeout=0: _Body(
        {"ok": False, "error": "bad_redirect_uri"}))
    with pytest.raises(sync.SlackError, match="bad_redirect_uri"):
        sync.exchange_code("id", "secret", "code", "https://localhost:3000/slack/callback")


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------
def test_probe_before_the_sign_in_points_at_the_sign_in():
    with pytest.raises(sync.SlackError, match="signin"):
        sync.probe({"client_id": "1", "client_secret": "2"})


def test_probe_with_no_readable_channels_says_which_scopes_to_check(monkeypatch):
    monkeypatch.setattr(sync, "identity", lambda token: {"user": "dana", "team": "Acme",
                                                         "user_id": "U1", "team_id": "T1"})
    monkeypatch.setattr(sync, "list_channels", lambda *a, **k: [])
    with pytest.raises(sync.SlackError, match="groups:read"):
        sync.probe({"token": "xoxp-1"})


def test_probe_lists_channels_and_the_workspace(monkeypatch):
    monkeypatch.setattr(sync, "identity", lambda token: {"user": "dana", "team": "Acme",
                                                         "user_id": "U1", "team_id": "T1"})
    monkeypatch.setattr(sync, "list_channels", lambda *a, **k: [
        {"id": "C1", "name": "general"}, {"id": "C2", "name": "secret", "is_private": True}])
    result = sync.probe({"token": "xoxp-1"}, limit=1)
    assert result["account"] == "dana · Acme"
    assert result["items"] == [{"id": "C1", "name": "#general", "private": False}]
    assert result["more_available"] is True


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def _run_offline(monkeypatch, tmp_path, channels, history):
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync, "identity", lambda token: {"user": "dana", "team": "Acme",
                                                         "user_id": "U1", "team_id": "T1"})
    monkeypatch.setattr(sync, "list_channels", lambda *a, **k: channels)
    monkeypatch.setattr(sync, "user_names", lambda token: NAMES)
    monkeypatch.setattr(sync, "channel_messages",
                        lambda token, cid, oldest="0": history.get(cid, []))
    monkeypatch.setattr("server.connections.settings_for", lambda cid: {"token": "xoxp-1"})
    rows = []
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.append(row))
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    return rows


def test_a_resync_rewrites_only_the_days_that_changed(monkeypatch, tmp_path):
    history = {"C1": [_message("1789894800.0", "morning")]}
    rows = _run_offline(monkeypatch, tmp_path, [{"id": "C1", "name": "general"}], history)

    assert sync.run(project_id="one", connection_id="slack-1", oldest="0") == (1, 1)
    assert sync.run(project_id="one", connection_id="slack-1", oldest="0") == (1, 0)
    history["C1"].append(_message("1789898400.0", "and a reply", user="U2"))
    assert sync.run(project_id="one", connection_id="slack-1", oldest="0") == (1, 1)

    text = (tmp_path / rows[-1]["path"]).read_text()
    assert "morning" in text and "and a reply" in text
    entry = (tmp_path / "raw" / "inbox" / f"{rows[-1]['id']}.md").read_text()
    assert "source_type: chat_thread" in entry
    assert 'authors: ["dana", "sam"]' in entry
    assert "date: 2026-09-20" in entry


def test_one_channel_per_day_and_a_day_is_its_own_item(monkeypatch, tmp_path):
    history = {"C1": [_message("1789894800.0", "day one"),
                      _message("1790067600.0", "day three")]}
    rows = _run_offline(monkeypatch, tmp_path, [{"id": "C1", "name": "general"}], history)
    assert sync.run(project_id="one", connection_id="slack-1", oldest="0") == (2, 2)
    assert sorted(row["name"] for row in rows) == ["#general — 2026-09-20",
                                                   "#general — 2026-09-22"]


def test_two_projects_never_share_a_channel_day_id(monkeypatch, tmp_path):
    rows = _run_offline(monkeypatch, tmp_path, [{"id": "C1", "name": "general"}],
                        {"C1": [_message()]})
    sync.run(project_id="one", connection_id="slack-1", oldest="0")
    sync.run(project_id="two", connection_id="slack-1", oldest="0")
    assert rows[0]["id"] != rows[-1]["id"]


def test_an_unreadable_channel_does_not_cost_the_others(monkeypatch, tmp_path):
    rows = _run_offline(monkeypatch, tmp_path,
                        [{"id": "C1", "name": "general"}, {"id": "C2", "name": "locked"}],
                        {"C1": [_message()]})

    def messages(token, cid, oldest="0"):
        if cid == "C2":
            raise sync.SlackError("Slack refused conversations.history: not_in_channel")
        return [_message()]

    monkeypatch.setattr(sync, "channel_messages", messages)
    result = sync.run(project_id="one", connection_id="slack-1", oldest="0")
    assert (result.seen, result.written, result.failed) == (1, 1, 1)
    assert result.complete is False
    assert [row["name"] for row in rows] == ["#general — 2026-09-20"]


def test_a_capped_run_refuses_to_certify_the_window(monkeypatch, tmp_path):
    history = {"C1": [_message("1789894800.0"), _message("1790067600.0")]}
    _run_offline(monkeypatch, tmp_path, [{"id": "C1", "name": "general"}], history)
    result = sync.run(project_id="one", connection_id="slack-1", oldest="0", max_items=1)
    assert (result.seen, result.written) == (1, 1)
    assert result.complete is False and "max_items" in result.failures[-1]["error"]


def test_an_incremental_run_floors_the_connection_watermark(monkeypatch, tmp_path):
    _run_offline(monkeypatch, tmp_path, [{"id": "C1", "name": "general"}], {"C1": []})
    monkeypatch.setattr("server.connections.watermark",
                        lambda cid: "2026-09-20T14:35:09.123456+00:00")
    asked = []
    monkeypatch.setattr(sync, "channel_messages",
                        lambda token, cid, oldest="0": (asked.append(oldest), [])[1])
    sync.run(project_id="one", connection_id="slack-1")
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(float(asked[0]), timezone.utc).isoformat() \
        == "2026-09-20T00:00:00+00:00"


def test_a_connection_that_never_signed_in_is_refused(monkeypatch):
    monkeypatch.setattr("server.connections.settings_for",
                        lambda cid: {"client_id": "1", "client_secret": "2"})
    with pytest.raises(sync.SlackError, match="signin"):
        sync.run(project_id="one", connection_id="slack-1")


def test_an_app_posting_alerts_is_attributed_to_its_username():
    alert = {"ts": "1789894800.0", "text": "deploy ok", "subtype": "bot_message",
             "username": "deploybot", "bot_id": "B1"}
    assert sync.author_of(alert, NAMES) == "deploybot"
    assert sync.author_of({"ts": "1.0", "bot_id": "B1"}, NAMES) == "B1"
    assert sync.author_of({"ts": "1.0"}, NAMES) == "unknown"
    assert sync.carries_content(alert) is True
    assert "deploybot: deploy ok" in sync.render_day("ops", "2026-09-20", [alert], NAMES)


def test_a_channel_too_large_to_finish_says_what_to_narrow(monkeypatch):
    monkeypatch.setattr(sync, "_call", lambda *a, **k: {
        "ok": True, "messages": [], "response_metadata": {"next_cursor": "always"}})
    with pytest.raises(sync.SlackError, match="narrow the channel list"):
        sync._paged("tok", "conversations.history", "messages", {}, pages=2)
