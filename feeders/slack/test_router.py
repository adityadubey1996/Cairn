"""Offline checks for the Slack sign-in route. No network, no browser, no database."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from feeders.slack import router as slack_router


class _Callback:
    """Stands in for server.signin.loopback's handle."""
    redirect_uri = "https://localhost:3000/slack/callback"

    def __init__(self, captured, error=None):
        self.captured = captured
        self.error = error

    def wait(self, timeout=300, state=None):
        if self.error:
            raise self.error
        assert state == self.captured["state"], "the route must check the state it sent"
        return {"code": "the-code", "state": state}


def _stub(monkeypatch, *, settings, stored=None, error=None, opened=True):
    """Wire the route's collaborators and hand back what it did."""
    seen = {"state": None, "loopback": None, "urls": [], "put": None}
    monkeypatch.setattr(slack_router.connections, "settings_for", lambda cid: settings)
    monkeypatch.setattr(slack_router.credentials, "get", lambda cid: dict(stored or {}))
    monkeypatch.setattr(slack_router.credentials, "put",
                        lambda cid, secrets: seen.__setitem__("put", (cid, secrets)))
    monkeypatch.setattr(slack_router.signin, "new_state", lambda: "st8")

    @contextmanager
    def loopback(port, path, https=True):
        seen["loopback"] = (port, path, https)
        seen["state"] = "st8"
        yield _Callback(seen, error)

    monkeypatch.setattr(slack_router.signin, "loopback", loopback)
    monkeypatch.setattr(slack_router.webbrowser, "open",
                        lambda url: (seen["urls"].append(url), opened)[1])
    monkeypatch.setattr(slack_router.sync, "exchange_code",
                        lambda cid, secret, code, uri: {"token": "xoxp-real", "user_id": "U1",
                                                        "team": "Acme", "team_id": "T1"})
    return seen


def test_the_sign_in_uses_the_shared_https_loopback_on_the_registered_url(monkeypatch):
    seen = _stub(monkeypatch, settings={"client_id": "1", "client_secret": "2"},
                 stored={"client_secret": "2"})
    message = slack_router.signin_route("slack-1")
    # The port and path are the redirect URL a user registers at Slack.
    assert seen["loopback"] == (3000, "/slack/callback", True)
    assert slack_router.REDIRECT_URL == "https://localhost:3000/slack/callback"
    assert seen["urls"][0].startswith("https://slack.com/oauth/v2/authorize?")
    assert "state=st8" in seen["urls"][0]
    assert "Acme" in message


def test_the_token_is_stored_as_a_credential_beside_the_client_secret(monkeypatch):
    seen = _stub(monkeypatch, settings={"client_id": "1", "client_secret": "2"},
                 stored={"client_secret": "2"})
    slack_router.signin_route("slack-1")
    connection_id, secrets = seen["put"]
    # Merged, not replaced: put() overwrites the whole blob.
    assert connection_id == "slack-1"
    assert secrets == {"client_secret": "2", "token": "xoxp-real"}


def test_without_app_credentials_the_route_says_to_add_them_first(monkeypatch):
    _stub(monkeypatch, settings={"client_id": "1"})
    with pytest.raises(HTTPException) as raised:
        slack_router.signin_route("slack-1")
    assert raised.value.status_code == 400
    assert "client ID and secret" in raised.value.detail


def test_an_unknown_connection_is_a_404(monkeypatch):
    def settings_for(cid):
        raise KeyError(cid)

    monkeypatch.setattr(slack_router.connections, "settings_for", settings_for)
    with pytest.raises(HTTPException) as raised:
        slack_router.signin_route("slack-nope")
    assert raised.value.status_code == 404


def test_a_mismatched_state_never_becomes_a_stored_token(monkeypatch):
    seen = _stub(monkeypatch, settings={"client_id": "1", "client_secret": "2"},
                 error=RuntimeError("callback state did not match; refusing it"))
    with pytest.raises(HTTPException) as raised:
        slack_router.signin_route("slack-1")
    assert raised.value.status_code == 400 and "state" in raised.value.detail
    assert seen["put"] is None


def test_a_sign_in_nobody_completes_times_out_without_storing_anything(monkeypatch):
    seen = _stub(monkeypatch, settings={"client_id": "1", "client_secret": "2"},
                 error=TimeoutError("no sign-in completed within 300s"))
    with pytest.raises(HTTPException) as raised:
        slack_router.signin_route("slack-1")
    assert raised.value.status_code == 400
    assert seen["put"] is None


def test_a_headless_host_is_given_the_url_to_open_by_hand(monkeypatch):
    seen = _stub(monkeypatch, settings={"client_id": "1", "client_secret": "2"}, opened=False)
    with pytest.raises(HTTPException) as raised:
        slack_router.signin_route("slack-1")
    assert raised.value.status_code == 500
    assert "https://slack.com/oauth/v2/authorize?" in raised.value.detail
    assert seen["put"] is None
