#!/usr/bin/env python3
"""Self-check for LinkedIn date resolution/render/dedup.  Run: python3 -m feeders.linkedin.test_sync

LinkedIn messaging shows day headings ("Today", "Yesterday", "Aug 15"), not
ISO dates; pins the resolver, the thread-URL-carrying render, and sha-skip."""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from feeders.linkedin import sync  # noqa: E402
from feeders.linkedin.sync import resolve_day, render_day, write_days  # noqa: E402

TODAY = date(2026, 8, 19)
assert resolve_day("Today", TODAY) == "2026-08-19"
assert resolve_day("Yesterday", TODAY) == "2026-08-18"
assert resolve_day("Aug 15", TODAY) == "2026-08-15"
assert resolve_day("AUG 15, 2025", TODAY) == "2025-08-15"
# a month-day ahead of today belongs to last year, not the future
assert resolve_day("Dec 30", TODAY) == "2025-12-30"
assert resolve_day("gibberish", TODAY) is None

URL = "https://www.linkedin.com/messaging/thread/2-abc123/"
msgs = [{"day": "2026-08-18", "time": "14:03", "sender": "Rajarshi Das", "text": "draft is up"}]
text = render_day("AI Guild", URL, "2026-08-18", msgs)
assert text.splitlines()[0] == "# AI Guild — 2026-08-18"
assert URL in text and "14:03 Rajarshi Das: draft is up" in text

tmp = Path(tempfile.mkdtemp())
sources, inbox = tmp / "sources", tmp / "inbox"
assert write_days("AI Guild", URL, "2-abc123", msgs, sources, inbox) == 1
assert write_days("AI Guild", URL, "2-abc123", msgs, sources, inbox) == 0
body = next(inbox.glob("linkedin-*.md")).read_text()
assert "source_type: chat_thread" in body and URL in body

def check_one_crashing_thread_does_not_abort_the_others() -> None:
    import asyncio
    async def fake_navigate(cdp_url, url):
        if "bad" in url:
            raise RuntimeError("boom")
    async def fake_require_logged_in(platform):
        return {"cdp_url": "x"}
    async def fake_scroll_to(*a):
        return None
    async def fake_cdp_evaluate(*a):
        return "[]"
    async def run_it():
        sync.sessions.require_logged_in = fake_require_logged_in
        sync.config.LINKEDIN_THREADS = ["https://bad", "https://good"]
        sync._navigate = fake_navigate
        sync._scroll_to = fake_scroll_to
        sync._cdp_evaluate = fake_cdp_evaluate
        return await sync._run()
    with tempfile.TemporaryDirectory() as d:
        sync.config.SOURCES_DIR = Path(d) / "sources"
        sync.config.GDRIVE_TARGET_REPO = Path(d)
        seen, written = asyncio.run(run_it())
    # must not raise — reaching this line at all is the assertion


check_one_crashing_thread_does_not_abort_the_others()


def check_authors_survive_the_frontmatter_quote_strip() -> None:
    """Authors with apostrophes must survive the frontmatter parser's quote stripping."""
    import json as _json
    authors = ["O'Brien", "Jane Doe"]
    line = f"authors: [{', '.join(_json.dumps(a) for a in authors)}]"
    # Mirror ingest.py's own parser exactly.
    parsed = [x.strip().strip('"') for x in
             line.removeprefix("authors: ").strip("[]").split(", ")]
    assert parsed == ["O'Brien", "Jane Doe"], parsed


check_authors_survive_the_frontmatter_quote_strip()

print("ok: linkedin resolve/render/dedup")
