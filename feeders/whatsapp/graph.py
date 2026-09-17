"""WhatsApp scrape as a small LangGraph: discrete steps, a guard before each,
sharing ONE persistent browser connection.

    check_login ─logged in?─▶ open_group ─opened?─▶ to_latest ⇄ (at newest?) ─▶ collect ⇄ (older loading?) ─▶ write ─▶ END
         └no─▶ END               └no─▶ END

Why a graph, not the old linear _run: the old code opened a NEW browser-use
connection for EVERY CDP call (start()/stop() each time) — inefficient, and
stacked up enough to destabilise WhatsApp Web into logging out. Here one WAConn
opens once and threads through every node, and each edge re-checks state before
the next step, so a wedged/logged-out session fails a guard and stops cleanly
instead of hammering.

WhatsApp Web VIRTUALISES the message list — it unloads newer rows as older ones
load on scroll-up, so the visible window is only ~30 rows and its row count
fluctuates. Two consequences the `collect` node handles: progress is tracked by
the OLDEST loaded date (which moves back monotonically), not row count; and each
window is extracted and accumulated as we scroll, because a single extract at the
end would only see the oldest window and miss everything newer."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from server import config
from feeders.browser.llm import build_llm
from .sync import parse_pre_plain, write_days

log = logging.getLogger("cairn.whatsapp")

_LOGIN_JS = "!!document.querySelector('#pane-side')"
_STATE_JS = """(() => {
  const rows = document.querySelectorAll('#main [data-pre-plain-text]');
  return JSON.stringify({count: rows.length,
    oldest: rows[0] ? rows[0].getAttribute('data-pre-plain-text') : '',
    newest: rows.length ? rows[rows.length - 1].getAttribute('data-pre-plain-text') : ''});
})()"""


def _scroll_js(to_top: bool) -> str:
    """Nudge the message scroll-container to the top (load older) or bottom
    (jump to most-recent), firing a scroll event so WhatsApp reacts."""
    target = "0" if to_top else "n.scrollHeight"
    return f"""(() => {{
      let n = document.querySelector('#main [data-pre-plain-text]');
      while (n && n !== document.body) {{
        const s = getComputedStyle(n);
        if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && n.scrollHeight > n.clientHeight + 10) {{
          n.scrollTop = {target}; n.dispatchEvent(new Event('scroll', {{bubbles: true}})); return true;
        }}
        n = n.parentElement;
      }}
      return false;
    }})()"""


_SCROLL_JS = _scroll_js(to_top=True)          # load older history
_SCROLL_BOTTOM_JS = _scroll_js(to_top=False)  # jump to most-recent (known start)
_EXTRACT_JS = """(() => {
  const out = [];
  for (const row of document.querySelectorAll('#main [data-pre-plain-text]')) {
    const meta = row.getAttribute('data-pre-plain-text') || '';
    const span = row.querySelector('span.selectable-text');
    const body = (span ? span.innerText : row.innerText) || '';
    if (meta && body.trim()) out.push({meta, text: body.trim()});
  }
  return JSON.stringify(out);
})()"""

MAX_SCROLLS = 40  # ponytail: ~weeks of a normal group; raise if a far-back
                  # cutoff over a dense group needs deeper history.


class WAConn:
    """One persistent CDP connection for a whole scrape (open once, reuse)."""
    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url
        self._b = None
        self._cdp = None

    async def open(self) -> None:
        from browser_use import Browser
        self._b = Browser(cdp_url=self.cdp_url)
        await self._b.start()
        self._cdp = await self._b.get_or_create_cdp_session()

    async def ev(self, expr: str):
        r = await self._cdp.cdp_client.send.Runtime.evaluate(
            params={"expression": expr, "returnByValue": True},
            session_id=self._cdp.session_id)
        return (r or {}).get("result", {}).get("value")

    async def close(self) -> None:
        if self._b:
            with contextlib.suppress(Exception):
                await self._b.stop()


class WAState(TypedDict, total=False):
    group: str
    since: str
    opened: bool
    cur_newest: str      # newest date seen this down-round; when it stops advancing
    prev_newest: str     # we're at the latest message (a known start for collect)
    down_rounds: int
    acc: dict            # message-key -> {meta, text}, accumulated across scrolls
    cur_oldest: str      # oldest data-pre-plain-text seen this round
    prev_oldest: str     # ...last round, to detect "scroll stopped moving back"
    rounds: int
    seen: int
    written: int
    error: str


async def _agent_open_group(cdp_url: str, group: str) -> None:
    """Agent-driven navigation (bounded, read-only) on its own short-lived
    connection — one nav, then it stops."""
    from browser_use import Agent, Browser
    browser = Browser(cdp_url=cdp_url)
    try:
        agent = Agent(
            task=(f"In the open WhatsApp Web page, find the chat named {group!r} "
                  "in the left sidebar and click it so its messages show in the "
                  "main pane. Do NOT type anything and do NOT send any message — "
                  "only open the chat, then stop."),
            llm=build_llm(), browser=browser, max_steps=8, use_vision=False)
        await agent.run()
    finally:
        with contextlib.suppress(Exception):
            await browser.stop()


def _at_latest(state: WAState) -> bool:
    """The go-to-latest guard: we've reached the most-recent message once the
    newest loaded date stops advancing (scrolling down loads nothing newer), or
    the cap is hit. This gives the collect loop a known bottom to start from,
    regardless of where a previous run left the chat scrolled."""
    stable = (state.get("down_rounds", 0) > 1
              and state.get("cur_newest", "") == state.get("prev_newest", ""))
    return stable or state.get("down_rounds", 0) >= MAX_SCROLLS


def _reached_or_stalled(state: WAState) -> bool:
    """The scroll-loop guard. WhatsApp virtualises the list, so row COUNT can't
    signal progress (it fluctuates as newer rows unload). The OLDEST loaded date
    can — it moves back monotonically while history is still loading. Stop when
    it predates the cutoff, stops moving back (no older messages left), or the
    round cap is hit."""
    oldest = parse_pre_plain(state.get("cur_oldest", ""), config.WHATSAPP_DATE_ORDER)
    reached = bool(oldest) and oldest[0] < state["since"]
    stalled = (state.get("rounds", 0) > 1
               and state.get("cur_oldest", "") == state.get("prev_oldest", ""))
    capped = state.get("rounds", 0) >= MAX_SCROLLS
    return reached or stalled or capped


def build_graph(conn: WAConn, project_id: str | None = None,
                connection_id: str | None = None):
    g = StateGraph(WAState)

    async def check_login(state: WAState) -> WAState:
        return {} if bool(await conn.ev(_LOGIN_JS)) else {"error": "whatsapp not logged in"}

    async def open_group(state: WAState) -> WAState:
        await _agent_open_group(conn.cdp_url, state["group"])
        await asyncio.sleep(2)
        st = json.loads(await conn.ev(_STATE_JS) or "{}")
        return {"opened": st.get("count", 0) > 0}

    async def to_latest(state: WAState) -> WAState:
        # Scroll DOWN to the most-recent message, whatever position the chat was
        # left in (a prior run leaves it scrolled up). Newest-date advancing =
        # still loading toward now; stops = we're at the bottom.
        st = json.loads(await conn.ev(_STATE_JS) or "{}")
        await conn.ev(_SCROLL_BOTTOM_JS)
        await asyncio.sleep(2)
        after = json.loads(await conn.ev(_STATE_JS) or "{}")
        return {"prev_newest": st.get("newest", ""), "cur_newest": after.get("newest", ""),
                "down_rounds": state.get("down_rounds", 0) + 1}

    async def collect(state: WAState) -> WAState:
        # Capture the CURRENT window first — WhatsApp unloads newer rows as we
        # scroll up, so each window must be read before we scroll past it — then
        # scroll up to load older history for the next round.
        raw = await conn.ev(_EXTRACT_JS)
        acc = dict(state.get("acc", {}))
        for r in (json.loads(raw) if raw else []):
            acc[r["meta"] + "\x00" + r["text"]] = r  # dedup identical rows across overlapping windows
        st = json.loads(await conn.ev(_STATE_JS) or "{}")
        cur_oldest = st.get("oldest", "")
        await conn.ev(_SCROLL_JS)
        await asyncio.sleep(2.5)
        return {"acc": acc, "prev_oldest": state.get("cur_oldest", ""),
                "cur_oldest": cur_oldest, "rounds": state.get("rounds", 0) + 1}

    async def write(state: WAState) -> WAState:
        since = state["since"]
        msgs = []
        for r in state.get("acc", {}).values():
            p = parse_pre_plain(r["meta"], config.WHATSAPP_DATE_ORDER)
            if p and p[0] >= since:
                msgs.append({"day": p[0], "time": p[1], "sender": p[2], "text": r["text"]})
        written = write_days(state["group"], msgs,
                             config.SOURCES_DIR / "whatsapp",
                             config.GDRIVE_TARGET_REPO / "raw" / "inbox",
                             project_id, connection_id)
        return {"seen": len({m["day"] for m in msgs}), "written": written}

    g.add_node("check_login", check_login)
    g.add_node("open_group", open_group)
    g.add_node("to_latest", to_latest)
    g.add_node("collect", collect)
    g.add_node("write", write)

    g.add_edge(START, "check_login")
    g.add_conditional_edges("check_login",
                            lambda s: "end" if s.get("error") else "open",
                            {"open": "open_group", "end": END})
    g.add_conditional_edges("open_group",
                            lambda s: "latest" if s.get("opened") else "end",
                            {"latest": "to_latest", "end": END})
    g.add_conditional_edges("to_latest",
                            lambda s: "collect" if _at_latest(s) else "latest",
                            {"latest": "to_latest", "collect": "collect"})
    g.add_conditional_edges("collect",
                            lambda s: "write" if _reached_or_stalled(s) else "collect",
                            {"collect": "collect", "write": "write"})
    g.add_edge("write", END)
    return g.compile()


async def scrape_group(group: str, since: str, cdp_url: str,
                       project_id: str | None = None,
                       connection_id: str | None = None) -> tuple[int, int, str | None]:
    """One group through the graph on a single persistent connection. Returns
    (items_seen, items_written, error|None)."""
    conn = WAConn(cdp_url)
    await conn.open()
    try:
        out = await build_graph(conn, project_id, connection_id).ainvoke(
            {"group": group, "since": since, "rounds": 0, "down_rounds": 0},
            config={"recursion_limit": 2 * MAX_SCROLLS + 10})
        return out.get("seen", 0), out.get("written", 0), out.get("error")
    finally:
        await conn.close()
