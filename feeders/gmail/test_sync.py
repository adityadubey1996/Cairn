import base64
from unittest.mock import Mock

from feeders.gmail import sync


def _part(mime, text):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")}}


def _message(mid="one", text="hello", timestamp="1758362400000"):
    part = _part("text/plain", text)
    part["headers"] = [{"name": "From", "value": "Person <p@example.com>"},
                       {"name": "Subject", "value": "Test conversation"}]
    return {"id": mid, "internalDate": timestamp, "payload": part}


def test_mime_nested_alternative_prefers_plain_and_keeps_attachment_names():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [
            _part("text/plain", "Plain body"), _part("text/html", "<p>Duplicate</p>")]},
        {"filename": "report.pdf", "mimeType": "application/pdf", "body": {"attachmentId": "x"}},
    ]}
    text, attachments = sync.message_body(payload)
    assert text == "Plain body" and attachments == ["report.pdf"]


def test_html_only_mail_is_readable():
    text, _ = sync.message_body(_part("text/html", "<html><body><p>Useful mail</p></body></html>"))
    assert "Useful mail" in text and "<p>" not in text


def test_thread_render_preserves_all_messages():
    title, text, authors, modified = sync.render_thread({"messages": [
        _message(), _message("two", "reply", "1758362401000")
    ]})
    assert title == "Test conversation"
    assert "hello" in text and "reply" in text
    assert authors == ["Person"] and modified.endswith("+00:00")


def test_thread_listing_paginates_and_reports_a_truncated_window(monkeypatch):
    calls = []

    def get(path, params):
        calls.append(params)
        return {"threads": [{"id": "a"}], "nextPageToken": "next"}

    monkeypatch.setattr(sync, "_get_json", get)
    rows, truncated = sync.list_threads("label:work", "2026-09-20T09:00:00Z", max_items=1)
    assert rows == [{"id": "a"}] and truncated
    assert "after:" in calls[0]["q"] and calls[0]["maxResults"] == 1


def test_resync_fetches_full_conversation_and_is_project_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.auth, "has_scopes", lambda scopes: True)
    monkeypatch.setattr(sync.auth, "account", lambda: "person@example.com")
    monkeypatch.setattr(sync.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync, "list_threads", lambda *_: ([{"id": "thread"}], False))
    messages = [_message()]
    monkeypatch.setattr(sync, "_get_json", lambda *_: {"messages": messages})
    rows = []
    monkeypatch.setattr(sync.sources_index, "record", lambda **row: rows.append(row))
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    assert sync.run(project_id="one") == (1, 1)
    assert sync.run(project_id="one") == (1, 0)
    messages.append(_message("two", "reply", "1758362401000"))
    assert sync.run(modified_after="2026-09-20T09:00:00Z", project_id="one") == (1, 1)
    text = (tmp_path / rows[-1]["path"]).read_text()
    assert "hello" in text and "reply" in text
    first_id = rows[-1]["id"]
    sync.run(project_id="two")
    assert rows[-1]["id"] != first_id


def test_old_google_consent_cannot_claim_gmail_is_connected(monkeypatch):
    monkeypatch.setattr(sync.auth, "has_scopes", lambda scopes: False)
    try:
        sync.run(project_id="one")
    except sync.auth.ReauthRequired as error:
        assert "consent" in str(error)
    else:
        raise AssertionError("missing Gmail scope must require consent")
