#!/usr/bin/env python3
"""Self-check for the shared sign-in listener.  Run: python3 -m server.test_signin

Loopback only: no provider is contacted. What is under test is that a redirect
is captured, that a mismatched state is refused, and that HTTPS works at all —
Slack will not register an http:// redirect URL, so the TLS path is the one
that matters most.
"""
from __future__ import annotations

import socket
import ssl
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import signin  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _get(url: str) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # self-signed on purpose
    urllib.request.urlopen(url, context=ctx, timeout=10).read()


def test_http_callback_is_captured():
    port = _free_port()
    with signin.loopback(port, "/cb", https=False) as cb:
        assert cb.redirect_uri == f"http://localhost:{port}/cb"
        threading.Timer(0.1, _get, [f"{cb.redirect_uri}?code=abc&state=xyz"]).start()
        assert cb.wait(10, state="xyz") == {"code": "abc", "state": "xyz"}


def test_https_callback_is_captured():
    port = _free_port()
    with signin.loopback(port, "/slack/callback") as cb:
        assert cb.redirect_uri.startswith("https://localhost:")
        threading.Timer(0.1, _get, [f"{cb.redirect_uri}?code=s1&state=st"]).start()
        assert cb.wait(10)["code"] == "s1"


def test_a_mismatched_state_is_refused():
    port = _free_port()
    with signin.loopback(port, "/cb", https=False) as cb:
        threading.Timer(0.1, _get, [f"{cb.redirect_uri}?code=abc&state=someone-else"]).start()
        try:
            cb.wait(10, state="mine")
        except RuntimeError as e:
            assert "state" in str(e)
        else:
            raise AssertionError("accepted a callback from a different sign-in")


def test_a_provider_error_is_raised_not_swallowed():
    port = _free_port()
    with signin.loopback(port, "/cb", https=False) as cb:
        threading.Timer(0.1, _get, [f"{cb.redirect_uri}?error=access_denied"]).start()
        try:
            cb.wait(10)
        except RuntimeError as e:
            assert "access_denied" in str(e)
        else:
            raise AssertionError("a refused sign-in must not look like success")


def test_the_key_is_owner_only():
    signin.ensure_cert()
    assert oct(signin.KEY_FILE.stat().st_mode)[-3:] == "600"


def test_states_are_unique():
    assert len({signin.new_state() for _ in range(50)}) == 50


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
