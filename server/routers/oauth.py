"""One consent flow for every provider that declares one.

Replaces a hand-rolled router per provider. A connector carries an OAuthSpec
(see server/connectors.py) and this serves it — so adding Microsoft means one
spec entry and a feeders/microsoft/auth.py, not a second copy of this file.

The route shape is `/api/{provider}/authorize` and `/api/{provider}/callback`,
which is exactly the path Google's live OAuth client already has registered.
That is deliberate: a redirect URI is an external contract registered byte for
byte with the provider, so the generic pattern was chosen to match what is
already deployed rather than force a re-registration.
"""
from __future__ import annotations

import logging
import secrets
import hashlib
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from .. import connections, connectors, projects
from ..auth import current_user
from ..db import connect

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# Short-lived, single-use state survives an app restart during user consent.
def _save_state(provider: str, state: str, payload: dict) -> None:
    with connect() as c:
        c.execute('DELETE FROM brain_oauth_states WHERE expires_at < now()')
        c.execute("INSERT INTO brain_oauth_states(state_hash,provider,payload,expires_at) "
                  "VALUES (%s,%s,%s,now()+interval '15 minutes')",
                  (hashlib.sha256(state.encode()).hexdigest(), provider, json.dumps(payload)))


def _consume_state(provider: str, state: str) -> dict | None:
    with connect() as c:
        row = c.execute('DELETE FROM brain_oauth_states WHERE state_hash=%s AND provider=%s '
                        'AND expires_at > now() RETURNING payload',
                        (hashlib.sha256(state.encode()).hexdigest(), provider)).fetchone()
    return row['payload'] if row else None


def _spec(provider: str) -> connectors.OAuthSpec:
    for c in connectors.REGISTRY:
        if c.oauth and c.oauth.provider == provider:
            return c.oauth
    raise HTTPException(404, f"no connector uses a {provider!r} consent")


def _kinds(provider: str) -> dict[str, str]:
    """kind -> display name, for every connector this consent covers.

    A consent can only produce a connection for a connector whose scopes it
    actually requested, so this is also the allow-list.
    """
    return {c.id: c.name for c in connectors.REGISTRY
            if c.oauth and c.oauth.provider == provider}


def _redirect_uri(request: Request, provider: str) -> str:
    """Must match a URI registered on the OAuth client byte for byte.

    # ponytail: trusts request.base_url. Behind a TLS-terminating proxy set
    # --forwarded-allow-ips so the scheme comes back https, or this builds an
    # http:// URI the provider will reject.
    """
    return f"{str(request.base_url).rstrip('/')}/api/{provider}/callback"


@router.get("/{provider}/authorize")
def authorize(provider: str, request: Request, project_id: str = "", kind: str = "",
              max_items: int = 0,
              _email: str = Depends(current_user)):
    spec = _spec(provider)
    if not spec.ready():
        raise HTTPException(400, f"{provider} client id/secret are not set in .env")
    covered = _kinds(provider)
    if kind and kind not in covered:
        raise HTTPException(400, f"the {provider} consent does not cover {kind!r}")
    state = secrets.token_urlsafe(24)
    # The project and the kind both have to survive the trip to the provider and
    # back, and `state` already exists to prove the trip started here.
    if not 0 <= max_items <= 10000:
        raise HTTPException(400, 'max_items must be between 0 and 10000')
    _save_state(provider, state, {"project_id": project_id, "kind": kind, 'max_items': max_items})
    return RedirectResponse(spec.consent_url(_redirect_uri(request, provider), state),
                            status_code=302)


@router.get("/{provider}/callback")
def callback(provider: str, request: Request, code: str = "", state: str = "",
             error: str = ""):
    """No auth dependency: this is the provider redirecting the browser back.
    The `state` check is what proves the round trip started here."""
    spec = _spec(provider)
    pending = _consume_state(provider, state) if state else None
    if error:
        return RedirectResponse(f"/?connect_error={quote(error)}", status_code=302)
    if not code or not state or not pending:
        return RedirectResponse("/?connect_error=bad_state", status_code=302)
    try:
        spec.exchange(code, _redirect_uri(request, provider))
    except Exception as e:
        log.exception("%s: code exchange failed", provider)
        return RedirectResponse(f"/?connect_error={quote(str(e)[:120])}", status_code=302)
    # The row is created here, not by the browser, because the token is what it
    # attests to: a connection that exists without a usable token reads as
    # configured and syncs nothing. One row for the kind that started the flow —
    # V2 starts empty, so a connection exists because somebody connected it.
    covered = _kinds(provider)
    kind = pending.get("kind") or ""
    if kind in covered:
        try:
            connections.create(pending["project_id"] or projects.ensure_default(),
                               kind, covered[kind], {'max_items': pending.get('max_items', 0)})
        except Exception:
            log.exception("%s: token saved but the connection row failed", provider)
    # Never echo the token — a redirect is the whole response.
    return RedirectResponse(f"/?connected={provider}", status_code=302)
