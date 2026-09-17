#!/usr/bin/env python3
"""Self-check for browser session persistence.  Run: python3 -m feeders.browser.test_sessions

Pins three behaviors with a fake SteelClient (no network, no docker):
  1. a stored session id that is still alive is reused, not recreated
  2. a dead stored session id → a fresh session is created and stored
  3. linkedin login detection saves cookies; a later create injects them
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from feeders.browser import sessions  # noqa: E402
from server import config  # noqa: E402


class FakeSteel:
    def __init__(self):
        self.live = {}
        self.created = 0
        self.last_session_context = None

    async def health(self):
        return True

    async def create_session(self, session_context=None):
        self.created += 1
        self.last_session_context = session_context
        sid = f"s{self.created}"
        self.live[sid] = {"id": sid, "status": "live",
                          "sessionViewerUrl": f"http://0.0.0.0:3000/v1/sessions/{sid}/viewer"}
        return self.live[sid]

    async def get_session(self, sid):
        if sid not in self.live:
            raise RuntimeError("404")
        return self.live[sid]

    async def get_context(self, sid):
        return {"cookies": [{"name": "li_at", "value": "tok", "domain": ".linkedin.com"}]}

    async def release_session(self, sid):
        self.live.pop(sid, None)
        return True

    async def close(self):
        pass


async def main():
    tmp = Path(tempfile.mkdtemp())
    sessions.STATE_FILE = tmp / "browser-sessions.json"
    config.SECRETS_DIR = tmp
    fake = FakeSteel()
    sessions._client = lambda: fake  # noqa: SLF001
    navigated = []

    async def fake_navigate(cdp_url, url):
        navigated.append(url)
    sessions._navigate = fake_navigate  # noqa: SLF001

    # 1. create then reuse
    s1 = await sessions.ensure("linkedin")
    s2 = await sessions.ensure("linkedin")
    assert s1["session_id"] == s2["session_id"] and fake.created == 1
    assert s1["viewer_url"].startswith(config.STEEL_VIEWER_BASE_URL), s1["viewer_url"]

    # 2. dead session → recreate
    fake.live.clear()
    s3 = await sessions.ensure("linkedin")
    assert s3["session_id"] != s1["session_id"] and fake.created == 2

    # 3. login check saves cookies; next create injects them
    assert await sessions.is_logged_in("linkedin", s3["session_id"])
    saved = json.loads((tmp / "linkedin-cookies.json").read_text())
    assert any(c["name"] == "li_at" for c in saved["cookies"])
    fake.live.clear()
    await sessions.ensure("linkedin")
    assert fake.last_session_context and any(
        c["name"] == "li_at" for c in fake.last_session_context["cookies"])

    # 4. open_login navigates to the platform login URL
    await sessions.open_login("whatsapp")
    assert navigated[-1] == "https://web.whatsapp.com"

    # 5. status() is read-only — never creates a session (poll churn would crash
    #    single-context Steel). whatsapp w/o session → needs_login; linkedin has a
    #    saved cookie file (step 3) → optimistically logged_in, still no create.
    fake.live.clear()
    sessions._save_state({})
    before = fake.created
    assert (await sessions.status("whatsapp"))["status"] == "needs_login"
    assert (await sessions.status("linkedin"))["status"] == "logged_in"
    assert fake.created == before, fake.created

    print("ok: session manager")

asyncio.run(main())
