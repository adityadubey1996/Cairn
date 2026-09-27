"""The Slack sign-in, owned by this folder.

Slack will not register an `http://localhost` redirect URL, so the listener has
to be HTTPS. That whole half — the self-signed certificate, the one-shot
listener, the state check — is server/signin.py's job and is shared with every
other connector; what is Slack-specific stays here: the authorize URL, the
scopes, and the token exchange.

One endpoint, deliberately. It blocks while the user approves the app in the
other tab, because the alternative is holding a half-finished sign-in in
process memory between two requests, and there is nothing to gain from that on
a single-user local install.

The token it obtains goes to server/credentials.py, never to the connection's
config row — that row is read to build every connection card.
"""
from __future__ import annotations

import logging
import webbrowser

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from server import connections, credentials, signin

from . import sync

router = APIRouter(prefix="/api/slack")

# The exact redirect URL a user registers at api.slack.com/apps. Changing either
# half means re-registering it there, so they are named here once.
CALLBACK_PORT = 3000
CALLBACK_PATH = "/slack/callback"
REDIRECT_URL = f"https://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"

log = logging.getLogger("cairn.slack")


@router.get("/signin/{connection_id}", response_class=PlainTextResponse)
def signin_route(connection_id: str) -> str:
    """Sign this connection in to Slack and store the user token.

    Open it in the browser on this machine. It sends you to Slack, waits for the
    redirect, and answers once the token is stored.
    """
    try:
        settings = connections.settings_for(connection_id)
    except KeyError:
        raise HTTPException(404, f"no such connection: {connection_id}")
    client_id = str(settings.get("client_id") or "").strip()
    client_secret = str(settings.get("client_secret") or "").strip()
    if not (client_id and client_secret):
        raise HTTPException(400, "This connection has no Slack app client ID and secret. "
                                 "Add them on the Connect screen first.")

    state = signin.new_state()
    with signin.loopback(CALLBACK_PORT, CALLBACK_PATH) as callback:
        url = sync.authorize_url(client_id, callback.redirect_uri, state)
        if not webbrowser.open(url):
            raise HTTPException(500, "Could not open a browser on this machine. "
                                     f"Finish the sign-in by visiting: {url}")
        try:
            code = callback.wait(state=state)["code"]
        except (TimeoutError, RuntimeError) as error:
            raise HTTPException(400, str(error))
        identity = sync.exchange_code(client_id, client_secret, code, callback.redirect_uri)

    # Merged, not replaced: put() overwrites the whole blob, and the client
    # secret lives in it too.
    credentials.put(connection_id, {**credentials.get(connection_id),
                                    "token": identity["token"]})
    log.info("slack: %s signed in to %s", connection_id, identity["team"] or identity["team_id"])
    return (f"Signed in to {identity['team'] or identity['team_id']}. "
            "You can close this tab and start a sync.")
