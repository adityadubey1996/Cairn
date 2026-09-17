"""Vendored Steel Browser client — trimmed from the steel-browser-workbench v5
SDK (sdk/steel_agent/session.py). Two host-reachability quirks it exists to
solve: Steel reports websocketUrl and sessionViewerUrl with host 0.0.0.0,
unusable from outside the container, so both are rewritten against the
configured bases. Sessions here are LONG-LIVED (login state lives in them) —
there is deliberately no context-manager auto-release."""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("cairn.steel")


class SteelError(RuntimeError):
    pass


def resolve_cdp_websocket_url(session: dict[str, Any], steel_ws_base: str) -> str:
    """Prefer {base}/ws?sessionId=… over whatever Steel reports (often 0.0.0.0
    or missing the sessionId)."""
    session_id = session.get("id") or ""
    canonical = f"{steel_ws_base.rstrip('/')}/ws?sessionId={session_id}"
    raw = (session.get("websocketUrl") or "").strip()
    if not raw or "0.0.0.0" in raw or (session_id and "sessionid=" not in raw.lower()):
        return canonical
    return raw


class SteelClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def create_session(self, session_context: dict | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "blockAds": True,
            "dimensions": {"width": 1280, "height": 800},
        }
        if session_context:
            payload["sessionContext"] = session_context
        r = await self.client.post(f"{self.base_url}/v1/sessions", json=payload)
        r.raise_for_status()
        data = r.json()
        log.info("steel session created: %s", data.get("id"))
        return data

    async def get_session(self, session_id: str) -> dict[str, Any]:
        r = await self.client.get(f"{self.base_url}/v1/sessions/{session_id}")
        r.raise_for_status()
        return r.json()

    async def get_context(self, session_id: str) -> dict[str, Any]:
        """Cookies (+ any storage Steel exposes) of a live session — this is
        both the login check and the cookie-save source."""
        r = await self.client.get(f"{self.base_url}/v1/sessions/{session_id}/context")
        r.raise_for_status()
        return r.json()

    async def release_session(self, session_id: str) -> bool:
        try:
            r = await self.client.delete(f"{self.base_url}/v1/sessions/{session_id}")
            return r.status_code in (200, 204, 404)
        except Exception as e:
            log.warning("steel release failed for %s: %s", session_id, e)
            return False

    async def health(self) -> bool:
        try:
            r = await self.client.get(f"{self.base_url}/v1/health")
            return r.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.aclose()
