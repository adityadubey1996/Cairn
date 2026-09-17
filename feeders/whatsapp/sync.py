"""WhatsApp feeder — scrapes tracked group chats through the Steel session.

The scrape runs as a small LangGraph (graph.py): one persistent browser
connection, agent-driven navigation to open the group, then a gentle scroll +
deterministic DOM read, with a guard before each step. This module owns the
pure pieces — parsing, rendering and the sha-dedup write.

EXTRACTION IS NEVER LLM: the workbench measured $0.075–0.12 per LLM-driven
run on identical pages; here the message rows are read straight off the DOM
([data-pre-plain-text] carries "[HH:MM, D/M/YYYY] Sender:"), so a scheduled
re-run costs zero tokens when nothing changed.

THE CONTRACT (same as feeders/chat/sync.py — one entry per group per day):
  raw/inbox/whatsapp-<groupslug>-<date>.md
    id: whatsapp-<groupslug>-<date>      path: sources/whatsapp/<slug>.md
    sha: <sha1 of rendered day, 8ch>     source_type: chat_thread
    status: active                       date/time/authors: as gchat
A late message rewrites that day's sha and the day re-absorbs — dedup and
"only fresh messages" both fall out of the sha compare, no watermark state."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, timedelta

from server import config, sources as sources_index
from feeders.browser import sessions

log = logging.getLogger("cairn.whatsapp")

_PRE_PLAIN = re.compile(
    r"\[(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\.?)?,\s*(\d{1,4})[./](\d{1,2})[./](\d{1,4})\]\s*(.+?):\s*$",
    re.I)


def parse_pre_plain(attr: str, date_order: str) -> tuple[str, str, str] | None:
    """'[14:03, 18/08/2026] Sender: ' -> ('2026-08-18', '14:03', 'Sender')."""
    m = _PRE_PLAIN.match(attr.strip())
    if not m:
        return None
    hh, mm, ampm, a, b, c = int(m[1]), m[2], m[3], int(m[4]), int(m[5]), int(m[6])
    if ampm:
        hh = hh % 12 + (12 if ampm.lower() == "p" else 0)
    if a > 1000:            # some locales lead with the year
        y, d, mo = a, c, b
    elif c > 1000:
        y = c
        d, mo = (a, b) if date_order.upper() == "DMY" else (b, a)
    else:
        return None
    try:
        day = date(y, mo, d).isoformat()
    except ValueError:
        return None
    return day, f"{hh:02d}:{mm}", m[7].strip()


def render_day(group: str, day: str, msgs: list[dict]) -> str:
    lines = [f"# {group} — {day}", ""]
    for m in msgs:
        lines.append(f"{m['time']} {m['sender']}: {m['text']}")
    return "\n".join(lines)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def write_days(group: str, msgs: list[dict], sources, inbox,
              project_id: str | None = None,
              connection_id: str | None = None) -> int:
    """gchat's render→sha→skip-or-write, verbatim in shape. Returns files written."""
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for m in msgs:
        by_day.setdefault(m["day"], []).append(m)
    written = 0
    gslug = _slug(group)[:40]
    for day, dmsgs in sorted(by_day.items()):
        dmsgs.sort(key=lambda m: m["time"])
        text = render_day(group, day, dmsgs)
        sha = hashlib.sha1(text.encode()).hexdigest()[:8]
        slug = f"{gslug}-{day}"
        source_path = sources / f"{slug}.md"
        inbox_path = inbox / f"whatsapp-{slug}.md"
        existing = (hashlib.sha1(source_path.read_text().encode()).hexdigest()[:8]
                    if source_path.is_file() else None)
        authors = list(dict.fromkeys(m["sender"] for m in dmsgs))
        entry = (
            "---\n"
            f"id: whatsapp-{slug}\n"
            f"path: sources/whatsapp/{slug}.md\n"
            f"sha: {sha}\n"
            "source_type: chat_thread\n"
            "status: active\n"
            f"date: {day}\n"
            f'time: "{dmsgs[-1]["time"]}:00"\n'
            f"authors: [{', '.join(json.dumps(a) for a in authors)}]\n"
            "---\n\n" + text + "\n")
        indexed = False
        if existing == sha:
            # content unchanged → don't rewrite the source, but self-heal a
            # missing inbox entry so a never-changing day can't fall out of the
            # ingest→absorb pipeline (its entry can be lost to a raw/ clear).
            if not inbox_path.is_file():
                inbox_path.write_text(entry, encoding="utf-8")
                written += 1
                indexed = True
        else:
            source_path.write_text(text, encoding="utf-8")
            inbox_path.write_text(entry, encoding="utf-8")
            written += 1
            indexed = True
        if indexed and project_id:
            sources_index.record(
                id=f"whatsapp-{slug}", project_id=project_id, kind="whatsapp",
                name=f"{group} — {day}", path=f"sources/whatsapp/{slug}.md",
                size=len(text.encode()), sha=sha, authors=authors,
                connection_id=connection_id)
    return written


def _since() -> str:
    return config.WHATSAPP_SINCE or (date.today() - timedelta(days=7)).isoformat()


async def _run(project_id: str | None = None, on_progress=None,
               connection_id: str | None = None) -> tuple[int, int]:
    s = await sessions.require_logged_in("whatsapp")
    since = _since()
    from .graph import scrape_group  # local import: keeps sync.py importable
    seen = written = 0               # even if langgraph isn't installed yet
    groups = config.WHATSAPP_GROUPS
    for n, group in enumerate(groups, 1):
        if on_progress:
            on_progress(n, len(groups), group)
        try:
            gs, gw, err = await scrape_group(group, since, s["cdp_url"],
                                             project_id, connection_id)
        except Exception:
            log.exception("whatsapp %r: crashed, skipping", group)
            continue
        if err:
            log.warning("whatsapp %r: %s", group, err)
        seen += gs
        written += gw
    return seen, written


def run(project_id: str | None = None, on_progress=None,
        connection_id: str | None = None) -> tuple[int, int]:
    """Sync entrypoint for connectors.run_now(); one item = one group-day.

    on_progress(done, total, label) fires once per GROUP, not per day: the day
    count is unknown until a group has been scraped, so groups are the only
    denominator available before the work is done.
    """
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    return asyncio.run(_run(project_id, on_progress, connection_id))
