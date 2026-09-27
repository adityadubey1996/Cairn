"""Offline checks for the Atlassian sign-in. No network, no database, no browser."""
import base64
import hashlib
import json
import time
import urllib.parse

import pytest

from feeders.jira import auth


@pytest.fixture(autouse=True)
def _store(monkeypatch, tmp_path):
    """Every test gets its own token file and a client id, never the real ones."""
    monkeypatch.setattr(auth, "STORE", tmp_path / "atlassian-oauth.json")
    monkeypatch.setenv("ATLASSIAN_CLIENT_ID", "cairn-client")
    return tmp_path / "atlassian-oauth.json"


def _granted(**over):
    return {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, **over}


def _sites(monkeypatch, rows=None):
    monkeypatch.setattr(auth, "_get", lambda *_a: rows if rows is not None else [
        {"id": "cloud-abc", "url": "https://acme.atlassian.net", "name": "Acme"}])


def test_an_install_without_a_client_id_cannot_start_a_sign_in(monkeypatch):
    monkeypatch.delenv("ATLASSIAN_CLIENT_ID")
    assert auth.ready() is False
    with pytest.raises(auth.NotSignedIn, match="ATLASSIAN_CLIENT_ID"):
        auth.consent_url("http://localhost:8310/api/jira/callback", "st")


def test_the_consent_url_is_pkce_read_only_and_asks_for_a_refresh_token():
    url = auth.consent_url("http://localhost:8310/api/jira/callback", "st")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert url.startswith("https://auth.atlassian.com/authorize?")
    assert query["client_id"] == ["cairn-client"] and query["state"] == ["st"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    assert query["audience"] == ["api.atlassian.com"]
    scope = query["scope"][0]
    assert "read:jira-work" in scope and "offline_access" in scope and "write" not in scope
    # A public client must never be asked to prove itself with a shared secret.
    assert "client_secret" not in query


def test_the_challenge_really_is_the_verifier_hashed(_store):
    url = auth.consent_url("http://localhost:8310/api/jira/callback", "st")
    sent = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["code_challenge"][0]
    verifier = json.loads(_store.read_text())["pending"]["verifier"]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert sent == expected


def test_a_completed_sign_in_stores_what_it_granted_and_sends_no_secret(monkeypatch, _store):
    sent = {}
    monkeypatch.setattr(auth, "_post", lambda payload: sent.update(payload) or _granted())
    _sites(monkeypatch)
    auth.consent_url("http://localhost:8310/api/jira/callback", "st")
    assert auth.exchange_code("the-code", "http://localhost:8310/api/jira/callback") == "acme.atlassian.net"
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "the-code"
    assert sent["code_verifier"] and "client_secret" not in sent
    stored = json.loads(_store.read_text())
    assert stored["sites"] == [{"host": "acme.atlassian.net", "cloud_id": "cloud-abc", "name": "Acme"}]
    assert "pending" not in stored  # the verifier is single-use
    assert auth.signed_in()


def test_redeeming_a_code_nobody_asked_for_is_refused(monkeypatch):
    monkeypatch.setattr(auth, "_post", lambda payload: _granted())
    with pytest.raises(PermissionError, match="No sign-in is in progress"):
        auth.exchange_code("stolen", "http://localhost:8310/api/jira/callback")


def test_a_sign_in_granting_no_jira_site_is_an_error(monkeypatch):
    monkeypatch.setattr(auth, "_post", lambda payload: _granted())
    _sites(monkeypatch, [])
    auth.consent_url("http://localhost:8310/api/jira/callback", "st")
    with pytest.raises(PermissionError, match="granted no Jira site"):
        auth.exchange_code("the-code", "http://localhost:8310/api/jira/callback")


def test_a_live_token_is_reused_and_an_expired_one_is_refreshed(monkeypatch, _store):
    _store.write_text(json.dumps({"access_token": "at-1", "refresh_token": "rt-1",
                                  "expires_at": time.time() + 600}))
    monkeypatch.setattr(auth, "_post", lambda p: pytest.fail("must not refresh a live token"))
    assert auth.access_token() == "at-1"

    _store.write_text(json.dumps({"access_token": "at-1", "refresh_token": "rt-1",
                                  "expires_at": time.time() - 1, "sites": [{"host": "h", "cloud_id": "c"}]}))
    sent = {}
    monkeypatch.setattr(auth, "_post",
                        lambda p: sent.update(p) or _granted(access_token="at-2", refresh_token="rt-rotated"))
    assert auth.access_token() == "at-2"
    assert sent["grant_type"] == "refresh_token" and "client_secret" not in sent
    stored = json.loads(_store.read_text())
    # Atlassian rotates it on every use; keeping the old one breaks the NEXT sync.
    assert stored["refresh_token"] == "rt-rotated"
    assert stored["sites"] == [{"host": "h", "cloud_id": "c"}]  # survives a refresh


def test_no_sign_in_at_all_says_to_connect_first():
    with pytest.raises(auth.NotSignedIn, match="Connect Jira"):
        auth.access_token()
    with pytest.raises(auth.NotSignedIn, match="Connect Jira"):
        auth.site_for()


def test_the_only_granted_site_is_used_and_a_named_one_must_be_granted(_store):
    _store.write_text(json.dumps({"refresh_token": "rt-1", "sites": [
        {"host": "acme.atlassian.net", "cloud_id": "cloud-abc"},
        {"host": "other.atlassian.net", "cloud_id": "cloud-zzz"}]}))
    assert auth.site_for()["cloud_id"] == "cloud-abc"
    assert auth.site_for("other.atlassian.net")["cloud_id"] == "cloud-zzz"
    # Never a silent fallback: the wrong site would sync somebody else's project.
    with pytest.raises(auth.NotSignedIn, match="does not cover nope.atlassian.net"):
        auth.site_for("nope.atlassian.net")


def test_the_token_file_is_not_world_readable(monkeypatch, _store):
    monkeypatch.setattr(auth, "_post", lambda payload: _granted())
    _sites(monkeypatch)
    auth.consent_url("http://localhost:8310/api/jira/callback", "st")
    auth.exchange_code("the-code", "http://localhost:8310/api/jira/callback")
    assert oct(_store.stat().st_mode)[-3:] == "600"
