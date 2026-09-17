"""LinkedIn feeder — scrapes configured messaging threads (group or 1:1)
through the logged-in Steel session.

Threads have stable URLs, so navigation is a plain navigate (no agent at all).
Extraction is deterministic DOM read: day headings + per-message rows from the
msg-s-* classes LinkedIn has used for years. Each rendered day carries the
thread URL in its body, so an absorbed article's citation trail leads back to
the actual conversation.

Inbox contract identical to feeders/whatsapp/sync.py / feeders/chat/sync.py:
raw/inbox/linkedin-<threadid>-<date>.md, source_type chat_thread, sha-skip."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta

from server import config, sources as sources_index
from feeders.browser import sessions
from feeders.browser.sessions import _cdp_evaluate, _navigate

log = logging.getLogger("cairn.linkedin")

# One pass over the message list: emit day headings and message rows in DOM
# order; Python assigns each row the last-seen heading. Sender name repeats
# only on the first message of a group — carry it forward the same way.
_EXTRACT_JS = """
(() => {
  const out = [];
  const list = document.querySelector('.msg-s-message-list-content')
            || document.querySelector('.msg-s-message-list');
  if (!list) return '[]';
  for (const el of list.querySelectorAll(
      'time.msg-s-message-list__time-heading, li.msg-s-message-list__event')) {
    if (el.tagName === 'TIME') {
      out.push({kind: 'day', label: el.innerText.trim()});
      continue;
    }
    const sender = el.querySelector('.msg-s-message-group__name')?.innerText.trim() || '';
    const time = el.querySelector('time.msg-s-message-group__timestamp')?.innerText.trim() || '';
    const body = el.querySelector('.msg-s-event-listitem__body')?.innerText.trim() || '';
    if (body) out.push({kind: 'msg', sender, time, text: body});
  }
  return JSON.stringify(out);
})()
"""

_SCROLL_TOP_JS = """
(() => {
  const first = document.querySelector(
    '.msg-s-message-list__event, time.msg-s-message-list__time-heading');
  if (!first) return false;
  first.scrollIntoView(true);
  return true;
})()
"""

_TOP_DAY_JS = """
document.querySelector('time.msg-s-message-list__time-heading')?.innerText.trim() || ''
"""

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def resolve_day(label: str, today: date) -> str | None:
    """'Today' / 'Yesterday' / 'Aug 15' / 'Aug 15, 2025' -> ISO date."""
    t = label.strip().lower().rstrip(".")
    if not t:
        return None
    if t == "today":
        return today.isoformat()
    if t == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    m = re.match(r"([a-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?$", t)
    if not m or m[1][:3] not in _MONTHS:
        return None
    mo, d = _MONTHS[m[1][:3]], int(m[2])
    y = int(m[3]) if m[3] else today.year
    try:
        resolved = date(y, mo, d)
    except ValueError:
        return None
    if not m[3] and resolved > today:   # "Dec 30" seen in August = last year
        resolved = date(y - 1, mo, d)
    return resolved.isoformat()


def _hhmm(label: str) -> str:
    """'2:03 PM' -> '14:03'; unparseable labels become '00:00'."""
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            return datetime.strptime(label.strip().upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return "00:00"


def render_day(title: str, url: str, day: str, msgs: list[dict]) -> str:
    lines = [f"# {title} — {day}", "", f"Thread: {url}", ""]
    for m in msgs:
        lines.append(f"{m['time']} {m['sender']}: {m['text']}")
    return "\n".join(lines)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def write_days(title: str, url: str, thread_id: str, msgs: list[dict],
               sources, inbox, project_id: str | None = None,
               connection_id: str | None = None) -> int:
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = {}
    for m in msgs:
        by_day.setdefault(m["day"], []).append(m)
    written = 0
    tslug = _slug(thread_id)[:24] or "thread"
    for day, dmsgs in sorted(by_day.items()):
        text = render_day(title, url, day, dmsgs)
        sha = hashlib.sha1(text.encode()).hexdigest()[:8]
        slug = f"{tslug}-{day}"
        source_path = sources / f"{slug}.md"
        inbox_path = inbox / f"linkedin-{slug}.md"
        existing = (hashlib.sha1(source_path.read_text().encode()).hexdigest()[:8]
                    if source_path.is_file() else None)
        authors = list(dict.fromkeys(m["sender"] for m in dmsgs if m["sender"]))
        entry = (
            "---\n"
            f"id: linkedin-{slug}\n"
            f"path: sources/linkedin/{slug}.md\n"
            f"sha: {sha}\n"
            "source_type: chat_thread\n"
            "status: active\n"
            f"date: {day}\n"
            f'time: "{dmsgs[-1]["time"]}:00"\n'
            f"authors: [{', '.join(json.dumps(a) for a in authors)}]\n"
            "---\n\n" + text + "\n")
        indexed = False
        if existing == sha:
            # unchanged → keep the source, but self-heal a missing inbox entry
            # so a never-changing thread-day can't fall out of the pipeline.
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
                id=f"linkedin-{slug}", project_id=project_id, kind="linkedin",
                name=f"{title} — {day}", path=f"sources/linkedin/{slug}.md",
                size=len(text.encode()), sha=sha, authors=authors,
                connection_id=connection_id)
    return written


def _thread_id(url: str) -> str:
    m = re.search(r"/thread/([^/?#]+)", url)
    return m[1] if m else hashlib.sha1(url.encode()).hexdigest()[:12]


async def _scroll_to(s: dict, since_day: str, cap: int = 200) -> None:
    prev = None
    for _ in range(cap):
        label = await _cdp_evaluate(s["cdp_url"], _TOP_DAY_JS)
        day = resolve_day(label or "", date.today())
        if day and day < since_day:
            return
        moved = await _cdp_evaluate(s["cdp_url"], _SCROLL_TOP_JS)
        if not moved or label == prev:
            return
        prev = label
        await asyncio.sleep(0.8)


def _since() -> str:
    return config.LINKEDIN_SINCE or (date.today() - timedelta(days=7)).isoformat()


async def _run(project_id: str | None = None, on_progress=None,
               connection_id: str | None = None) -> tuple[int, int]:
    s = await sessions.require_logged_in("linkedin")
    since = _since()
    sources = config.SOURCES_DIR / "linkedin"
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    seen = written = 0
    threads = config.LINKEDIN_THREADS
    for n, url in enumerate(threads, 1):
        if on_progress:
            on_progress(n, len(threads), url)
        try:
            await _navigate(s["cdp_url"], url)
            await asyncio.sleep(3)
            await _scroll_to(s, since)
            raw = await _cdp_evaluate(s["cdp_url"], _EXTRACT_JS)
            rows = json.loads(raw) if raw else []
            title = (await _cdp_evaluate(
                s["cdp_url"],
                "document.querySelector('.msg-entity-lockup__entity-title')"
                "?.innerText.trim() || ''") or _thread_id(url))
            msgs, day, sender = [], None, ""
            for r in rows:
                if r["kind"] == "day":
                    day = resolve_day(r["label"], date.today())
                    continue
                sender = r["sender"] or sender
                if day and day >= since:
                    msgs.append({"day": day, "time": _hhmm(r["time"]),
                                 "sender": sender, "text": r["text"]})
            seen += len({m["day"] for m in msgs})
            written += write_days(title, url, _thread_id(url), msgs, sources, inbox,
                                  project_id, connection_id)
        except Exception:
            log.exception("linkedin %r: crashed, skipping", url)
            continue
    return seen, written


def run(project_id: str | None = None, on_progress=None,
        connection_id: str | None = None) -> tuple[int, int]:
    """Sync entrypoint for connectors.run_now(); one item = one thread-day.

    on_progress(done, total, label) fires once per THREAD, not per day — same
    reasoning as the WhatsApp feeder: the day count is not known up front.
    """
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    return asyncio.run(_run(project_id, on_progress, connection_id))
