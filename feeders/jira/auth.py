"""One Atlassian sign-in for this install, with no secret to keep.

Atlassian's authorization server advertises `token_endpoint_auth_methods: none`
and PKCE S256, so Cairn is registered once — by whoever ships it, not by each
user — and the resulting client id travels in the open. That is what turns the
Connect button into a login window instead of a setup checklist.

It has no `registration_endpoint`, which is the whole reason the client id has
to come from somewhere: ATLASSIAN_CLIENT_ID, exactly like GOOGLE_CLIENT_ID.
There is no ATLASSIAN_CLIENT_SECRET on purpose — a public client that kept one
would be shipping it to every user, and PKCE is what makes it unnecessary.

The token lives in one file for the whole install, like the Google one beside
it. Two Jira sites on one machine therefore share a sign-in; the site each
connection reads is chosen from what that sign-in actually grants.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from server import config

AUTHORIZE = "https://auth.atlassian.com/authorize"
TOKEN = "https://auth.atlassian.com/oauth/token"
GATEWAY = "https://api.atlassian.com"
RESOURCES = f"{GATEWAY}/oauth/token/accessible-resources"
# Read-only, plus the refresh token an unattended sync needs. No write scope is
# requested, so no approval here can be turned into a change in Jira.
SCOPES = ("read:jira-work", "read:jira-user", "offline_access")
STORE = config.SECRETS_DIR / "atlassian-oauth.json"
log = logging.getLogger("cairn.jira")


class NotSignedIn(RuntimeError):
    """No usable Atlassian sign-in. Maps to "connect it first"."""


def client_id() -> str:
    return os.environ.get("ATLASSIAN_CLIENT_ID", "").strip()


def ready() -> bool:
    """Whether this install can start a sign-in at all."""
    return bool(client_id())


def _load() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(state, indent=2))
    STORE.chmod(0o600)


def signed_in() -> bool:
    return bool(_load().get("refresh_token"))


def sites() -> list[dict]:
    """The Jira sites this sign-in granted: [{"host": ..., "cloud_id": ...}]."""
    return list(_load().get("sites") or [])


def account() -> str:
    return str(_load().get("account") or "")


def _post(payload: dict) -> dict:
    request = urllib.request.Request(
        TOKEN, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = (error.read() or b"").decode()[:300]
        except Exception:
            pass
        # No secret of ours is in this payload — a public client has none — so
        # Atlassian's own reason is the most useful thing to show.
        raise PermissionError(f"Atlassian refused the sign-in ({error.code}). {detail}".strip()) from None


def _get(url: str, token: str) -> list | dict:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read() or b"[]")


def consent_url(redirect_uri: str, state: str) -> str:
    """Where to send the browser. Called by server/routers/oauth.py.

    ponytail: one sign-in at a time, because the PKCE verifier has to reach
    exchange_code() and the generic callback hands it no state. True for a
    single-user local install; key the pending block by state otherwise.
    """
    if not ready():
        raise NotSignedIn("ATLASSIAN_CLIENT_ID is not set. See feeders/jira/SETUP.md.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    _save({**_load(), "pending": {"verifier": verifier, "redirect_uri": redirect_uri,
                                  "state": state, "at": time.time()}})
    return AUTHORIZE + "?" + urllib.parse.urlencode({
        "audience": "api.atlassian.com", "client_id": client_id(),
        "scope": " ".join(SCOPES), "redirect_uri": redirect_uri, "state": state,
        "response_type": "code", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256"})


def _store_tokens(granted: dict, previous: dict) -> dict:
    access = granted.get("access_token")
    if not access:
        raise PermissionError("Atlassian completed the sign-in without returning an access token.")
    return {**previous, "access_token": access,
            "refresh_token": granted.get("refresh_token") or previous.get("refresh_token", ""),
            # A minute of headroom: a token that dies mid-sweep fails a page
            # that has already been fetched.
            "expires_at": time.time() + max(int(granted.get("expires_in") or 3600) - 60, 0)}


def exchange_code(code: str, redirect_uri: str) -> str:
    """Redeem the code, record what it grants, and name the account.

    Returns the label server/routers/oauth.py puts on the connection.
    """
    state = _load()
    pending = state.get("pending") or {}
    if not pending.get("verifier"):
        raise PermissionError("No sign-in is in progress. Start again from the Connect screen.")
    kept = _store_tokens(_post({
        "grant_type": "authorization_code", "client_id": client_id(), "code": code,
        "redirect_uri": redirect_uri, "code_verifier": pending["verifier"],
    }), {k: v for k, v in state.items() if k != "pending"})
    granted_sites = [{"host": urllib.parse.urlsplit(row.get("url", "")).hostname,
                      "cloud_id": row.get("id"), "name": row.get("name", "")}
                     for row in _get(RESOURCES, kept["access_token"]) or []]
    granted_sites = [s for s in granted_sites if s["host"] and s["cloud_id"]]
    if not granted_sites:
        raise PermissionError("The sign-in granted no Jira site. Approve a site with Jira on it.")
    kept["sites"] = granted_sites
    kept["account"] = granted_sites[0]["host"]
    _save(kept)
    log.info("jira: signed in to %s", ", ".join(s["host"] for s in granted_sites))
    return kept["account"]


def access_token() -> str:
    """A token good for this call, refreshed when the stored one has run out.

    Atlassian rotates the refresh token on every use, so the replacement is
    written back — miss that and the sync after this one cannot sign in.
    """
    state = _load()
    if not state.get("refresh_token"):
        raise NotSignedIn("Not signed in to Atlassian. Connect Jira from the Connect screen.")
    if state.get("access_token") and float(state.get("expires_at") or 0) > time.time():
        return state["access_token"]
    state = _store_tokens(_post({"grant_type": "refresh_token", "client_id": client_id(),
                                 "refresh_token": state["refresh_token"]}), state)
    _save(state)
    return state["access_token"]


def site_for(preferred: str = "") -> dict:
    """Which granted site a connection reads.

    `preferred` wins when the sign-in covers it; otherwise the only site does,
    and the first does when there are several. A wrong guess here would sync
    somebody else's project, so a named site that was NOT granted is an error
    rather than a silent fallback.
    """
    granted = sites()
    if not granted:
        raise NotSignedIn("Not signed in to Atlassian. Connect Jira from the Connect screen.")
    if preferred:
        for row in granted:
            if row["host"] == preferred:
                return row
        raise NotSignedIn(
            f"The Atlassian sign-in does not cover {preferred}. It covers: "
            + ", ".join(row["host"] for row in granted))
    return granted[0]


def forget() -> None:
    STORE.unlink(missing_ok=True)
