import json
from unittest.mock import Mock

from feeders.google import auth


def test_new_gmail_scope_requires_real_consent(monkeypatch, tmp_path):
    monkeypatch.setattr(auth.config, "GOOGLE_TOKEN_FILE", tmp_path / "oauth.json")
    auth.save_token("test-refresh", "person@example.com", scopes=[auth.SCOPES[0]])
    assert auth.has_scopes((auth.SCOPES[0],))
    assert not auth.has_scopes(("https://www.googleapis.com/auth/gmail.readonly",))
    assert auth.config.GOOGLE_TOKEN_FILE.stat().st_mode & 0o077 == 0


def test_exchange_records_granted_scopes_not_requested_scopes(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_ACCOUNT_EMAIL", raising=False)
    monkeypatch.setattr(auth.config, "GOOGLE_TOKEN_FILE", tmp_path / "oauth.json")
    monkeypatch.setattr(auth, "_post", lambda *_: {
        "refresh_token": "test-refresh", "access_token": "test-access",
        "scope": auth.SCOPES[0], "expires_in": 3600})
    monkeypatch.setattr(auth, "_whoami", lambda _: "person@example.com")
    monkeypatch.setattr(auth, "_cache_access", Mock())
    auth.exchange_code("test-code", "http://localhost/callback")
    assert json.loads(auth.config.GOOGLE_TOKEN_FILE.read_text())["scopes"] == [auth.SCOPES[0]]


def test_wrong_account_does_not_replace_working_token(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "expected@example.com")
    monkeypatch.setattr(auth.config, "GOOGLE_TOKEN_FILE", tmp_path / "oauth.json")
    auth.save_token("old-refresh", "expected@example.com")
    monkeypatch.setattr(auth, "_post", lambda *_: {
        "refresh_token": "different-refresh", "access_token": "test-access"})
    monkeypatch.setattr(auth, "_whoami", lambda _: "other@example.com")
    try:
        auth.exchange_code("test-code", "http://localhost/callback")
    except auth.ReauthRequired:
        pass
    else:
        raise AssertionError("wrong mailbox must be rejected")
    assert json.loads(auth.config.GOOGLE_TOKEN_FILE.read_text())["refresh_token"] == "old-refresh"
