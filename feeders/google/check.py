"""Offline self-checks for the Google OAuth module. Run from ai-brain root:
.venv/bin/python -m feeders.google.check   (no network, no credentials)"""
from __future__ import annotations

import io
import json
import tempfile
import time
import urllib.error
import urllib.parse
from pathlib import Path

from feeders.google import auth


def check_consent_url() -> None:
    auth.config.GOOGLE_CLIENT_ID = "cid.apps.googleusercontent.com"
    url = auth.consent_url("http://localhost:8300/api/google/callback", "st8")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    # Without these two Google returns no refresh token on a re-authorization,
    # and the connector can never renew — the failure this check exists for.
    assert q["access_type"] == ["offline"], q
    assert q["prompt"] == ["consent"], q
    assert q["response_type"] == ["code"] and q["state"] == ["st8"]
    assert q["redirect_uri"] == ["http://localhost:8300/api/google/callback"]
    assert set(q["scope"][0].split()) == set(auth.SCOPES), q["scope"]
    assert all(s.endswith("readonly") for s in auth.SCOPES), auth.SCOPES


def check_token_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        auth.config.GOOGLE_TOKEN_FILE = Path(d) / "nested" / "google-oauth.json"
        assert auth.connected() is False and auth.account() == ""
        auth.save_token("1//refresh", "someone@example.com")
        assert auth.connected() is True
        assert auth.account() == "someone@example.com"
        saved = json.loads(auth.config.GOOGLE_TOKEN_FILE.read_text())
        assert saved["scopes"] == list(auth.SCOPES), saved
        assert auth.config.GOOGLE_TOKEN_FILE.stat().st_mode & 0o077 == 0, "not 600"
        auth.disconnect()
        assert auth.connected() is False


def check_access_token_refresh() -> None:
    with tempfile.TemporaryDirectory() as d:
        auth.config.GOOGLE_TOKEN_FILE = Path(d) / "google-oauth.json"
        auth.save_token("1//refresh", "someone@example.com")
        auth._access = None
        posts = []

        def fake_post(url, fields):
            posts.append(fields)
            return {"access_token": "ya29.fresh", "expires_in": 3600}

        real_post, auth._post = auth._post, fake_post
        assert auth.access_token() == "ya29.fresh"
        assert posts[0]["grant_type"] == "refresh_token"
        assert posts[0]["refresh_token"] == "1//refresh"
        assert auth.access_token() == "ya29.fresh"
        assert len(posts) == 1, "second call must come from cache"

        auth._access = ("ya29.stale", time.time() + 30)  # inside the 60s margin
        assert auth.access_token() == "ya29.fresh"
        assert len(posts) == 2, "a token expiring in 30s must be refreshed"
        auth._post = real_post  # the next check exercises the real one


def check_reauth_required() -> None:
    with tempfile.TemporaryDirectory() as d:
        auth.config.GOOGLE_TOKEN_FILE = Path(d) / "google-oauth.json"
        auth._access = None
        try:
            auth.access_token()
            raise AssertionError("no token file must raise ReauthRequired")
        except auth.ReauthRequired:
            pass

        def fake_urlopen(req, timeout=0):
            raise urllib.error.HTTPError(
                "https://oauth2.googleapis.com/token", 400, "Bad Request", {},
                io.BytesIO(json.dumps({"error": "invalid_grant"}).encode()))

        auth.urllib.request.urlopen = fake_urlopen
        try:
            auth._post(auth.TOKEN_URI, {})
            raise AssertionError("invalid_grant must raise ReauthRequired")
        except auth.ReauthRequired as e:
            assert "invalid_grant" in str(e)


if __name__ == "__main__":
    check_consent_url()
    check_token_roundtrip()
    check_access_token_refresh()
    check_reauth_required()
    print("google auth checks OK")
