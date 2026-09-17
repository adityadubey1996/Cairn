#!/usr/bin/env python3
"""Self-check for WhatsApp parsing/render/dedup.  Run: python3 -m feeders.whatsapp.test_sync

No browser: extraction returns [data-pre-plain-text] strings + body text; this
pins the locale-sensitive attribute parse, the gchat-shaped render, and the
sha-skip that makes recurring runs store only fresh messages."""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from feeders.whatsapp import sync  # noqa: E402
from feeders.whatsapp.sync import parse_pre_plain, render_day, write_days  # noqa: E402

# WhatsApp emits "[HH:MM, D/M/YYYY] Sender: " (order varies by locale)
assert parse_pre_plain("[14:03, 18/08/2026] Samta Sharma: ", "DMY") == \
    ("2026-08-18", "14:03", "Samta Sharma")
assert parse_pre_plain("[2:03 pm, 8/18/2026] Samta Sharma: ", "MDY") == \
    ("2026-08-18", "14:03", "Samta Sharma")
assert parse_pre_plain("garbage", "DMY") is None

msgs = [{"day": "2026-08-18", "time": "14:03", "sender": "Samta", "text": "shipped it"},
        {"day": "2026-08-18", "time": "14:05", "sender": "Aditya", "text": "nice"}]
text = render_day("Ops Group", "2026-08-18", msgs)
assert text.splitlines()[0] == "# Ops Group — 2026-08-18"
assert "14:03 Samta: shipped it" in text and "14:05 Aditya: nice" in text

# dedup: same content twice → written once
tmp = Path(tempfile.mkdtemp())
sources, inbox = tmp / "sources", tmp / "inbox"
w1 = write_days("Ops Group", msgs, sources, inbox)
w2 = write_days("Ops Group", msgs, sources, inbox)
assert w1 == 1 and w2 == 0, (w1, w2)
inbox_file = next(inbox.glob("whatsapp-*.md"))
body = inbox_file.read_text()
assert "source_type: chat_thread" in body and "sha: " in body
sha = hashlib.sha1(text.encode()).hexdigest()[:8]
assert f"sha: {sha}" in body

# fresh message → rewritten (day sha changed)
msgs.append({"day": "2026-08-18", "time": "15:00", "sender": "Samta", "text": "one more"})
assert write_days("Ops Group", msgs, sources, inbox) == 1

# self-heal: a lost inbox entry is re-emitted even though the source is unchanged,
# so a never-changing day can't silently fall out of the ingest→absorb pipeline
heal_file = next(inbox.glob("whatsapp-*.md"))
heal_file.unlink()
assert write_days("Ops Group", msgs, sources, inbox) == 1  # re-emitted
assert heal_file.exists()
assert write_days("Ops Group", msgs, sources, inbox) == 0  # entry present again → no-op

def check_one_crashing_group_does_not_abort_the_others() -> None:
    import asyncio
    calls = []
    async def fake_scrape_group(group, since, cdp_url, project_id=None,
                                connection_id=None):
        if group == "bad":
            raise RuntimeError("boom")
        calls.append(group)
        return 1, 1, None
    async def fake_require_logged_in(platform):
        return {"cdp_url": "x"}
    async def run_it():
        sync.sessions.require_logged_in = fake_require_logged_in
        sync.config.WHATSAPP_GROUPS = ["bad", "good"]
        from feeders.whatsapp import graph
        graph.scrape_group = fake_scrape_group
        return await sync._run()
    seen, written = asyncio.run(run_it())
    assert calls == ["good"], calls


check_one_crashing_group_does_not_abort_the_others()


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

print("ok: whatsapp parse/render/dedup")
