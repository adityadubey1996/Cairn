"""Fetch a page through a real browser, for the pages plain HTTP cannot read.

Two failure shapes justify the cost, and only those two:
  * the server answered but the body is a shell — the content is written by
    JavaScript after load, so urllib correctly reports no text;
  * the server refused a non-browser client (403/406/503 on a page that opens
    fine in a browser).

Everything else — 404, a login wall we hold no session for, a CAPTCHA — a
browser does not fix, so it is not tried.

Steel rather than Selenium or a second Playwright install: feeders/browser/
already vendors a Steel client and the per-platform session persistence that
keeps a logged-in session alive between runs. This goes through Steel's HTTP
scrape endpoint, so it needs no browser driver in this process at all.

OFF unless STEEL_ENABLED is set. Without it the caller records the same failure
reason it always did, so nothing changes for an install with no Steel.
"""
from __future__ import annotations

import logging
import threading

import httpx

from server import config

log = logging.getLogger("cairn.links.browser")

# A page load is seconds where urllib is milliseconds, so the slow path is
# capped per pass. Without a ceiling one backlog could run for a day.
_used = 0
_used_lock = threading.Lock()


class BrowserUnavailable(RuntimeError):
    """Steel is off, unreachable, or the per-pass ceiling is spent."""


def reset_budget() -> None:
    """Call once at the start of a links pass."""
    global _used
    with _used_lock:
        _used = 0


def _take_budget() -> None:
    global _used
    with _used_lock:
        if _used >= config.LINK_BROWSER_MAX:
            raise BrowserUnavailable(
                f"browser budget spent for this pass "
                f"({config.LINK_BROWSER_MAX} pages)")
        _used += 1


def available() -> bool:
    return bool(config.STEEL_ENABLED)


def browser_fetch(url: str) -> str:
    """The page's HTML after the browser has rendered it. Raises
    BrowserUnavailable when the browser route cannot be used at all, which the
    caller turns back into the original failure reason."""
    if not config.STEEL_ENABLED:
        raise BrowserUnavailable("STEEL_ENABLED is off")
    _take_budget()
    try:
        r = httpx.post(f"{config.STEEL_BASE_URL}/v1/scrape",
                       json={"url": url, "format": ["html"]},
                       timeout=config.LINK_BROWSER_TIMEOUT)
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPError as e:
        raise BrowserUnavailable(f"steel unreachable: {e}") from e
    # Steel returns {"content": {"html": "..."}} — tolerate a flatter shape too
    # rather than KeyError-ing a whole pass on a version difference.
    content = body.get("content") or body
    html = content.get("html") if isinstance(content, dict) else None
    if not html:
        raise BrowserUnavailable("steel returned no html")
    log.info("fetched via steel: %s", url[:120])
    return html
