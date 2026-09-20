"""OAuth for the Google feeders — one consent, shared by Drive, Chat and Gmail.

The same Web client backs sign-in (`config.GOOGLE_CLIENT_ID`); sign-in only
verifies an ID token, while ingestion runs the authorization-code exchange and
therefore also needs the secret.

WHY USER OAUTH AND NOT A SERVICE ACCOUNT: the org enforces
`iam.disableServiceAccountKeyCreation`, so no SA key can be minted — by the
admin either. And a service account only ever sees what is explicitly shared
with it, which cannot express "everything I can already read". See
the WHY above — it is the whole justification for this module's shape.

The token is a refresh token on disk under secrets/ (gitignored, mode 600). It
is never logged, never printed, and never returned through an API response.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from server import config

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

# Read-only throughout: the token cannot write, delete, send or post.
SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/chat.spaces.readonly",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
)


class ReauthRequired(RuntimeError):
    """The refresh token is dead. Only a human at a browser can fix it, so this
    must reach the UI — a silently empty sync looks exactly like "no new
    documents" and would hide the outage indefinitely."""


def connected() -> bool:
    return config.GOOGLE_TOKEN_FILE.is_file()


def has_scopes(required: tuple[str, ...]) -> bool:
    """Old Google connections stay useful while new scopes await re-consent."""
    try:
        saved = json.loads(config.GOOGLE_TOKEN_FILE.read_text())
        return bool(saved.get("refresh_token")) and set(required).issubset(saved.get("scopes", []))
    except (OSError, ValueError, TypeError):
        return False


def account() -> str:
    if not connected():
        return ""
    return json.loads(config.GOOGLE_TOKEN_FILE.read_text()).get("account", "")


def consent_url(redirect_uri: str, state: str) -> str:
    """`prompt=consent` is not optional: without it Google omits the refresh
    token on any repeat authorization, and the connector ends up with an access
    token it can never renew."""
    return AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state})


def _post(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}").get("error", "")
        if detail in ("invalid_grant", "unauthorized_client"):
            raise ReauthRequired(detail)
        raise


def _whoami(access: str) -> str:
    """Drive's about endpoint already carries the account email, so we can label
    the connection without asking for the extra `email` scope at consent time."""
    req = urllib.request.Request(
        "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)",
        headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("user", {}).get("emailAddress", "")


def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade the one-time code for a refresh token. Returns the account email —
    never the token itself, so a caller cannot leak it into a response."""
    tok = _post(TOKEN_URI, {
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"})
    if "refresh_token" not in tok:
        raise ReauthRequired("no refresh_token returned — prompt=consent missing?")
    email = _whoami(tok["access_token"])
    expected = os.environ.get("GOOGLE_ACCOUNT_EMAIL", "").strip().lower()
    if expected and email.lower() != expected:
        raise ReauthRequired(f"Connect with {expected}; the selected account was different")
    granted = tok.get("scope", "").split() or list(SCOPES)
    save_token(tok["refresh_token"], email, scopes=granted)
    _cache_access(tok["access_token"], tok.get("expires_in", 3600))
    return email


def save_token(refresh_token: str, email: str, scopes: list[str] | None = None) -> None:
    path = config.GOOGLE_TOKEN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({
        "refresh_token": refresh_token,
        "account": email,
        "scopes": list(SCOPES) if scopes is None else scopes,
        "obtained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2)
    # tempfile creates mode 600; replace only after a complete write, so a
    # crashed/restarted callback cannot destroy the previously working token.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as token_file:
            temporary = token_file.name
            token_file.write(data)
            token_file.flush()
            os.fsync(token_file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def disconnect() -> None:
    config.GOOGLE_TOKEN_FILE.unlink(missing_ok=True)
    global _access
    _access = None


_access: tuple[str, float] | None = None  # (token, expiry epoch)


def _cache_access(token: str, expires_in: int) -> None:
    global _access
    _access = (token, time.time() + expires_in)


def access_token() -> str:
    """Cached until 60s before expiry. Same signature as the service-account
    version it replaces, so feeder call sites did not change."""
    if _access and _access[1] > time.time() + 60:
        return _access[0]
    if not connected():
        raise ReauthRequired("Google is not connected")
    saved = json.loads(config.GOOGLE_TOKEN_FILE.read_text())
    tok = _post(TOKEN_URI, {
        "refresh_token": saved["refresh_token"],
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token"})
    _cache_access(tok["access_token"], tok.get("expires_in", 3600))
    return tok["access_token"]
