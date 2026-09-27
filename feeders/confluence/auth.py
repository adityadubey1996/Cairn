"""Atlassian OAuth 2.0 (3LO) sign-in for Confluence.

The app already serves `/api/confluence/authorize` and `/api/confluence/callback`
— server/routers/oauth.py drives any connector whose SPEC declares an OAuthSpec
— so a real sign-in button costs this folder one module and no shared edit.

A 3LO token does not address the site by hostname. It names a **cloud id**, and
every call goes to `api.atlassian.com/ex/confluence/{cloud id}`. The site's own
URL is kept only to build the links citations point at.

The refresh token is the durable credential and lives in secrets/, the same
place and shape as the Google one. It is never logged and never returned
through the API.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from server import config

AUTHORIZE = "https://auth.atlassian.com/authorize"
TOKEN = "https://auth.atlassian.com/oauth/token"
RESOURCES = "https://api.atlassian.com/oauth/token/accessible-resources"
ME = "https://api.atlassian.com/me"
TOKEN_FILE = config.SECRETS_DIR / "confluence-oauth.json"

# Granular scopes, which is what the v2 API checks. `offline_access` is what
# makes a refresh token come back at all; without it the connection dies in an
# hour. `read:user:confluence` only buys editor display names — see
# sync.author_name, which degrades to the account id rather than failing.
SCOPES = ("read:page:confluence", "read:space:confluence", "read:user:confluence",
          "read:me", "offline_access")
PAGE_SCOPE = "read:page:confluence"

_access: dict = {"token": "", "expires": 0.0}


class ReauthRequired(RuntimeError):
    """Sign-in is missing or dead. The user presses Connect again."""


def client_id() -> str:
    return os.environ.get("CONFLUENCE_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("CONFLUENCE_CLIENT_SECRET", "").strip()


def ready() -> bool:
    return bool(client_id() and client_secret())


def consent_url(redirect_uri: str, state: str) -> str:
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "audience": "api.atlassian.com", "client_id": client_id(),
        "scope": " ".join(SCOPES), "redirect_uri": redirect_uri,
        "state": state, "response_type": "code", "prompt": "consent"})


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        # The body names the cause (invalid_grant, invalid_scope); the status
        # alone sends people hunting the wrong thing. It carries no token.
        detail = error.read().decode("utf-8", "replace")[:200]
        raise ReauthRequired(f"Atlassian refused the sign-in ({error.code}): {detail}") from error


def _get(url: str, token: str, params: dict | None = None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def _load() -> dict:
    if not TOKEN_FILE.is_file():
        return {}
    return json.loads(TOKEN_FILE.read_text())


def _save(state: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(state, indent=2))
    TOKEN_FILE.chmod(0o600)


def exchange_code(code: str, redirect_uri: str) -> str:
    """Trade the callback's code for tokens and remember which site they open."""
    tokens = _post(TOKEN, {"grant_type": "authorization_code", "client_id": client_id(),
                           "client_secret": client_secret(), "code": code,
                           "redirect_uri": redirect_uri})
    access = tokens.get("access_token", "")
    if not access:
        raise ReauthRequired("Atlassian returned no access token")
    sites = [site for site in _get(RESOURCES, access)
             if PAGE_SCOPE in (site.get("scopes") or [PAGE_SCOPE])]
    if not sites:
        raise ReauthRequired(
            "That account granted no Confluence site. Pick a site with Confluence on it, "
            "or ask its admin to approve the app.")
    site = sites[0]
    who = _get(ME, access)
    account = who.get("email") or who.get("name") or who.get("account_id") or ""
    _save({"refresh_token": tokens.get("refresh_token", ""), "cloud_id": site["id"],
           "site_url": site.get("url", ""), "site_name": site.get("name", ""),
           "account": account, "sites_available": len(sites)})
    _access.update(token=access, expires=time.time() + float(tokens.get("expires_in", 3600)))
    return f"{account} · {site.get('name') or site.get('url')}"


def access_token() -> str:
    """A live access token, refreshed when the cached one is close to expiry."""
    if _access["token"] and time.time() < _access["expires"] - 60:
        return _access["token"]
    state = _load()
    refresh = state.get("refresh_token")
    if not refresh:
        raise ReauthRequired("Confluence is not connected yet. Press Connect on the Connect screen.")
    tokens = _post(TOKEN, {"grant_type": "refresh_token", "client_id": client_id(),
                           "client_secret": client_secret(), "refresh_token": refresh})
    # Atlassian rotates refresh tokens: the one just spent is dead, so the
    # replacement has to reach disk before anything else can fail.
    _save({**state, "refresh_token": tokens.get("refresh_token", refresh)})
    _access.update(token=tokens.get("access_token", ""),
                   expires=time.time() + float(tokens.get("expires_in", 3600)))
    if not _access["token"]:
        raise ReauthRequired("Atlassian returned no access token on refresh")
    return _access["token"]


def _required(key: str) -> str:
    value = _load().get(key)
    if not value:
        raise ReauthRequired("Confluence is not connected yet. Press Connect on the Connect screen.")
    return value


def api_base() -> str:
    return f"https://api.atlassian.com/ex/confluence/{_required('cloud_id')}"


def site_url() -> str:
    return _load().get("site_url", "").rstrip("/")


def account() -> str:
    return _load().get("account", "")
