"""Per-platform Steel session state — the piece the workbench deliberately
deferred (its session-recovery spec chose always-fresh re-login; here logged-in
sessions must survive between scheduled runs).

Two persistence models, on purpose:
  linkedin  cookie file. li_at IS the login; save cookies once logged in and
            inject them via sessionContext when creating a replacement session.
  whatsapp  pinned session only. WhatsApp Web keys live in IndexedDB, which
            cookie restore cannot carry — the session itself is the login, so
            it is never released and a Steel restart means re-scanning the QR.

STATE_FILE (var/) maps platform -> live steel session id; disposable — a stale
id is detected via GET /v1/sessions/{id} and replaced. Cookie files live in
secrets/ (gitignored, durable)."""
from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path

from server import config
from .steel import SteelClient, SteelError, resolve_cdp_websocket_url

log = logging.getLogger("cairn.steel")

PLATFORMS = {
    "whatsapp": "https://web.whatsapp.com",
    "linkedin": "https://www.linkedin.com/login",
}
STATE_FILE = config.VAR / "browser-sessions.json"

# ponytail: WhatsApp has no stable login cookie; the chat sidebar (#pane-side)
# has been the stable logged-in marker for years. Revisit if it breaks.
_WHATSAPP_LOGIN_JS = "!!document.querySelector('#pane-side')"


def _client() -> SteelClient:
    return SteelClient(config.STEEL_BASE_URL)


def _load_state() -> dict:
    if STATE_FILE.is_file():
        with contextlib.suppress(Exception):
            return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1))


def _cookie_file(platform: str) -> Path:
    return config.SECRETS_DIR / f"{platform}-cookies.json"


def _session_dict(raw: dict) -> dict:
    sid = raw["id"]
    return {
        "session_id": sid,
        "cdp_url": resolve_cdp_websocket_url(raw, config.STEEL_WS_URL),
        # Steel reports sessionViewerUrl as the bare API root here; the
        # embeddable INTERACTIVE viewer (clickable — for manual login / QR
        # scan) is the session-scoped debug page, same URL the workbench uses.
        "viewer_url": (f"{config.STEEL_VIEWER_BASE_URL}/v1/sessions/debug"
                       f"?sessionId={sid}&interactive=true&showControls=true"),
    }


async def _navigate(cdp_url: str, url: str) -> None:
    """Same best-effort pattern as the workbench (_navigate, v5 app.py)."""
    from browser_use import Browser
    b = Browser(cdp_url=cdp_url)
    await b.start()
    try:
        await b.navigate_to(url)
    finally:
        with contextlib.suppress(Exception):
            await b.stop()


async def _cdp_evaluate(cdp_url: str, expression: str):
    """CDP Runtime.evaluate — same call path as the workbench reconciler."""
    from browser_use import Browser
    b = Browser(cdp_url=cdp_url)
    await b.start()
    try:
        cdp_session = await b.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={"expression": expression, "returnByValue": True},
            session_id=cdp_session.session_id)
        return (result or {}).get("result", {}).get("value")
    finally:
        with contextlib.suppress(Exception):
            await b.stop()


async def _existing(client: SteelClient, platform: str) -> dict | None:
    """The platform's stored session if Steel still has it alive — NEVER creates.
    Status polling must be side-effect-free: if it created a session per poll,
    two platforms × a 5–15s poll would spawn sessions endlessly and crash
    single-context Steel (observed). Only ensure()/open_login()/a run create."""
    sid = _load_state().get(platform)
    if not sid:
        return None
    with contextlib.suppress(Exception):
        raw = await client.get_session(sid)
        if raw.get("status") not in ("released", "failed"):
            return _session_dict(raw)
    return None


async def ensure(platform: str) -> dict:
    """Live session for the platform: reuse the stored one when Steel still has
    it, else create (linkedin: with saved cookies injected)."""
    if platform not in PLATFORMS:
        raise ValueError(f"no such browser platform: {platform}")
    client = _client()
    try:
        if not await client.health():
            raise SteelError("Steel is not reachable — docker compose up -d steel-api")
        if existing := await _existing(client, platform):
            return existing
        session_context = None
        cf = _cookie_file(platform)
        if platform == "linkedin" and cf.is_file():
            with contextlib.suppress(Exception):
                session_context = json.loads(cf.read_text())
        raw = await client.create_session(session_context=session_context)
        state = _load_state()
        state[platform] = raw["id"]
        _save_state(state)
        return _session_dict(raw)
    finally:
        await client.close()


async def is_logged_in(platform: str, session_id: str) -> bool:
    """linkedin: li_at cookie in the Steel session context (v5-proven); on
    success the full cookie set is saved for future session restores.
    whatsapp: DOM check — the logged-in chat sidebar exists."""
    client = _client()
    try:
        if platform == "linkedin":
            ctx = await client.get_context(session_id)
            cookies = ctx.get("cookies", []) or []
            if any(c.get("name") == "li_at" for c in cookies):
                _cookie_file(platform).parent.mkdir(parents=True, exist_ok=True)
                _cookie_file(platform).write_text(json.dumps({"cookies": cookies}))
                return True
            return False
        raw = await client.get_session(session_id)
        cdp = resolve_cdp_websocket_url(raw, config.STEEL_WS_URL)
        return bool(await _cdp_evaluate(cdp, _WHATSAPP_LOGIN_JS))
    except Exception as e:
        log.warning("login check failed for %s: %s", platform, e)
        return False
    finally:
        await client.close()


async def status(platform: str) -> dict:
    """Read-only login state of the EXISTING session; never creates one (that is
    open_login's / a run's job). A poll that created sessions would churn Steel."""
    client = _client()
    try:
        if not await client.health():
            return {"status": "steel_down", "viewer_url": "", "detail": "Steel not reachable"}
        s = await _existing(client, platform)
    finally:
        await client.close()
    if not s:
        # LinkedIn's saved cookies ARE its login — report logged_in without
        # spinning up a session; a run injects them (stale ⇒ it fails clearly).
        # WhatsApp has no cookie file (pinned-session only), so its login truly
        # died with the session ⇒ needs_login.
        if platform == "linkedin" and _cookie_file(platform).is_file():
            return {"status": "logged_in", "viewer_url": ""}
        return {"status": "needs_login", "viewer_url": ""}
    ok = await is_logged_in(platform, s["session_id"])
    return {"status": "logged_in" if ok else "needs_login",
            "viewer_url": s["viewer_url"]}


async def open_login(platform: str) -> dict:
    s = await ensure(platform)
    with contextlib.suppress(Exception):
        await _navigate(s["cdp_url"], PLATFORMS[platform])
    return {"viewer_url": s["viewer_url"]}


async def require_logged_in(platform: str) -> dict:
    s = await ensure(platform)
    if not await is_logged_in(platform, s["session_id"]):
        raise RuntimeError(
            f"{platform} needs login — open Connectors and log in via the browser panel")
    return s
