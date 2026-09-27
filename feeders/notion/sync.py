"""Read-only Notion feeder: one source file per page.

A page is the document boundary because it is the unit a human links to and the
unit Notion timestamps. Its block tree is rendered to Markdown in one pass and
the whole page is replaced, so an edit to a paragraph halfway down does not
leave the earlier text stale.

THE SILENT EMPTY WORKSPACE
--------------------------
An integration secret authenticates immediately, but Notion shows an integration
only the pages a human has explicitly shared with it (page ⋯ → Connections).
Until then `/v1/search` returns `{"results": []}` with HTTP 200 — no error, no
warning. probe() treats that as a setup failure and says what to click, because
every other reading of it ("the workspace is empty", "the token is wrong") sends
the user somewhere useless.

Read-only: nothing here creates, updates or deletes at Notion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from feeders import options
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://api.notion.com/v1"

# Pinned, not a user field: a version is a promise about response shapes this
# code reads, so it changes when this code is updated to match, never because a
# workspace was configured differently. Confirmed current at developers.notion.com/reference/versioning.
NOTION_VERSION = "2026-03-11"

# Notion's published ceiling is an average of three requests per second. A page
# with many blocks is many requests, so the pacing has to live below the request
# helper rather than in the page loop.
# ponytail: one process-wide gap, which is correct while syncs run one at a
# time. Give each token its own gap if connections ever sync concurrently.
MIN_REQUEST_GAP = 1 / 3
_last_request_at = 0.0

# A page can nest toggles inside toggles indefinitely, and a synced or duplicated
# block can point back up its own tree. Depth is the cheap guard that turns a
# runaway walk into a visible note in the rendered page.
MAX_BLOCK_DEPTH = 6

BLOCK_PREFIX = {
    "heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
    "bulleted_list_item": "- ", "numbered_list_item": "1. ",
    "quote": "> ", "callout": "> ", "toggle": "- ",
}
MEDIA_BLOCKS = ("image", "file", "pdf", "video", "audio", "bookmark", "embed",
                "link_preview")

log = logging.getLogger("cairn.notion")


class NotionError(RuntimeError):
    """A failure the user can act on. connector_check.py prints it verbatim."""


def _instant(value: str) -> datetime:
    """Notion's ISO timestamps and this app's watermarks, as comparable values.

    They are not comparable as strings: Notion writes `2026-09-20T09:00:00.000Z`
    and a watermark is `2026-09-20T09:00:00.123456+00:00`.
    """
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


def _pace() -> None:
    global _last_request_at
    gap = MIN_REQUEST_GAP - (time.monotonic() - _last_request_at)
    if gap > 0:
        time.sleep(gap)
    _last_request_at = time.monotonic()


def _detail(error: urllib.error.HTTPError) -> str:
    """Notion's own explanation, which names the missing capability precisely."""
    try:
        return json.loads(error.read()).get("message", "")
    except Exception:
        return ""


def _request(token: str, method: str, path: str, body: dict | None = None,
             params: dict | None = None) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    payload = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}",
               "Notion-Version": NOTION_VERSION,
               "Content-Type": "application/json"}
    for attempt in range(3):
        _pace()
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                try:
                    wait = min(float(error.headers.get("Retry-After", "0")), 60)
                except (TypeError, ValueError):
                    wait = 0
                time.sleep(max(wait, 2 ** attempt))
                continue
            if error.code == 401:
                raise NotionError(
                    "Notion rejected the integration secret. Copy it again from "
                    "notion.so/my-integrations — it starts with ntn_ or secret_.") from error
            raise NotionError(f"Notion returned {error.code} for {method} {path}: "
                              f"{_detail(error)}".strip()) from error
    raise AssertionError("unreachable")


def plain_text(rich_text: list[dict]) -> str:
    return "".join(run.get("plain_text", "") for run in rich_text or [])


def page_title(page: dict) -> str:
    """The value of whichever property is this page's title.

    A standalone page calls it "title"; a page in a database calls it whatever
    the database column is called ("Name", "Task", "客户"). Only the property's
    `type` is reliable, so that is what this matches on.
    """
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return plain_text(prop.get("title") or []) or "(untitled)"
    return "(untitled)"


def _media_url(body: dict) -> str:
    return (body.get("external") or {}).get("url") or \
           (body.get("file") or {}).get("url") or body.get("url") or ""


def _render_block(fetch_children, block: dict, depth: int) -> list[str]:
    kind = block.get("type", "")
    body = block.get(kind) or {}
    indent = "    " * depth
    text = plain_text(body.get("rich_text") or [])
    if kind == "code":
        lines = [f"{indent}```{body.get('language', '')}",
                 *(indent + line for line in text.splitlines()),
                 f"{indent}```"]
    elif kind == "divider":
        lines = [f"{indent}---"]
    elif kind == "table_row":
        cells = " | ".join(plain_text(cell) for cell in body.get("cells") or [])
        lines = [f"{indent}| {cells} |"]
    elif kind in ("child_page", "child_database"):
        lines = [f"{indent}- {body.get('title') or '(untitled)'}"]
    elif kind == "equation":
        lines = [f"{indent}{body.get('expression', '')}"]
    elif kind in MEDIA_BLOCKS:
        caption = plain_text(body.get("caption") or [])
        lines = [f"{indent}[{kind}] {caption or _media_url(body)}".rstrip()]
    elif kind == "to_do":
        lines = [f"{indent}- [{'x' if body.get('checked') else ' '}] {text}"]
    else:
        # Everything else that carries prose — paragraph, headings, list items,
        # quote, callout, toggle, and any block type Notion adds later — is the
        # same shape. An unknown type renders its text rather than vanishing.
        lines = [f"{indent}{BLOCK_PREFIX.get(kind, '')}{text}"] if text else []
    if block.get("has_children"):
        lines += render_children(fetch_children, block["id"], depth + 1)
    return lines


def render_children(fetch_children, block_id: str, depth: int = 0) -> list[str]:
    if depth >= MAX_BLOCK_DEPTH:
        return ["    " * depth + f"[nested deeper than {MAX_BLOCK_DEPTH} levels; not followed]"]
    lines: list[str] = []
    for block in fetch_children(block_id):
        lines += _render_block(fetch_children, block, depth)
    return lines


def child_blocks(token: str, block_id: str) -> list[dict]:
    blocks, cursor = [], None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        page = _request(token, "GET",
                        f"blocks/{urllib.parse.quote(block_id, safe='')}/children", params=params)
        blocks.extend(page.get("results", []))
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return blocks


def render_page(token: str, page: dict) -> str:
    title = page_title(page)
    lines = render_children(lambda bid: child_blocks(token, bid), page["id"])
    return f"# {title}\n\n" + "\n".join(lines).strip() + "\n"


def search_pages(token: str, edited_after: str = "", max_items: int = 0) -> tuple[list[dict], bool]:
    """Pages the integration can see, most recently edited first.

    Newest-first ordering is what makes the incremental stop correct: the first
    page older than the watermark proves every page behind it is older too.
    Returns (pages, truncated).
    """
    cutoff = _instant(edited_after) - timedelta(seconds=1) if edited_after else None
    pages, cursor = [], None
    while True:
        body: dict = {"filter": {"property": "object", "value": "page"},
                      "sort": {"timestamp": "last_edited_time", "direction": "descending"},
                      "page_size": 50}
        if cursor:
            body["start_cursor"] = cursor
        page = _request(token, "POST", "search", body)
        for row in page.get("results", []):
            edited = row.get("last_edited_time", "")
            if cutoff and edited and _instant(edited) <= cutoff:
                return pages, False
            pages.append(row)
            if max_items and len(pages) >= max_items:
                return pages, True
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            return pages, False


def user_name(token: str, user_id: str, cache: dict[str, str]) -> str:
    """A Notion user id turned into a display name, once per run.

    Reading users needs the integration's "read user information" capability,
    which a user may not have ticked. The raw id still attributes the edit, so a
    missing capability degrades the author line instead of failing the sync.
    """
    if not user_id:
        return ""
    if user_id not in cache:
        try:
            cache[user_id] = _request(
                token, "GET", f"users/{urllib.parse.quote(user_id, safe='')}").get("name") or user_id
        except Exception as error:
            log.info("notion: cannot resolve user %s (%s); keeping the id", user_id, error)
            cache[user_id] = user_id
    return cache[user_id]


def _identity(token: str) -> tuple[str, str]:
    """(integration name, workspace name) — also the cheapest proof of auth."""
    me = _request(token, "GET", "users/me")
    bot = me.get("name") or "(unnamed integration)"
    return bot, (me.get("bot") or {}).get("workspace_name") or ""


NO_SHARED_PAGES = (
    "Notion accepted the secret but no pages are shared with this integration, so "
    "there is nothing to read — the API answers an empty list rather than an error. "
    "Open each page you want read in Notion, then ⋯ → Connections → add this "
    "integration. Sharing a parent page covers everything under it; a database has "
    "to be shared in its own right.")


def probe(settings: dict, limit: int = 3) -> dict:
    """Authenticate and list a few pages. Writes nothing."""
    token = str(settings.get("token") or "").strip()
    if not token:
        raise NotionError("Paste the internal integration secret from notion.so/my-integrations.")
    bot, workspace = _identity(token)
    pages, more = search_pages(token, max_items=limit)
    if not pages:
        raise NotionError(NO_SHARED_PAGES)
    return {"account": f"{bot} · {workspace}" if workspace else bot,
            "items": [{"id": page["id"], "name": page_title(page),
                       "last_edited_time": page.get("last_edited_time", "")}
                      for page in pages],
            "more_available": more}


def run(project_id: str | None = None, on_progress=None, connection_id: str | None = None,
        modified_after: str | None = None, max_items: int = 0) -> SyncResult:
    """One item = one Notion page. Returns SyncResult(seen, written, failures).

    `modified_after` defaults to this connection's watermark; pass "" for a full
    sweep. A run that stops at max_items reports a failure so the caller leaves
    the watermark where it was and the remaining backlog is retried.
    """
    from server import connections

    settings = connections.settings_for(connection_id) if connection_id else {}
    token = str(settings.get("token") or "").strip()
    if not token:
        raise NotionError(
            "No Notion integration secret for this sync. Notion is read per connection: "
            "add one on the Connect screen and paste the secret from notion.so/my-integrations.")
    max_items = options.max_items(max_items or settings.get("max_items") or 0)
    if modified_after is None:
        modified_after = connections.watermark(connection_id) if connection_id else ""
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()

    bot, workspace = _identity(token)
    # Project and workspace scope the ids and the directory: the same page synced
    # into two knowledge projects, or two workspaces holding a page with the same
    # uuid, must not overwrite one another's source records.
    scope = hashlib.sha256(f"{project_id}\0{workspace or bot}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/notion/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    pages, truncated = search_pages(token, modified_after, max_items)
    names: dict[str, str] = {}
    written, failures = 0, []
    for index, page in enumerate(pages, 1):
        page_id = str(page.get("id", ""))
        uid = f"notion-{scope}-{page_id.replace('-', '')}"
        title = page_title(page)
        if on_progress:
            on_progress(index, len(pages), title)
        try:
            text = render_page(token, page)
            editor = (page.get("last_edited_by") or page.get("created_by") or {}).get("id", "")
            authors = [name for name in (user_name(token, editor, names),) if name]
            edited = page.get("last_edited_time") or datetime.now(timezone.utc).isoformat()
            relative = f"sources/notion/{scope}/{page_id}.md"
            path = sources / f"{page_id}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous != sha or not entry.is_file()
            if previous != sha:
                path.write_text(text, encoding="utf-8")
            url = page.get("url") or f"https://notion.so/{page_id.replace('-', '')}"
            if changed:
                day, clock = _instant(edited).isoformat().split("T", 1)
                entry.write_text(
                    f"---\nid: {uid}\npath: {relative}\nsha: {sha}\nsource_type: doc\n"
                    f"status: active\ndate: {day}\ntime: {json.dumps(clock[:8])}\n"
                    f"authors: {json.dumps(authors)}\nurl: {json.dumps(url)}\n---\n\n{text}",
                    encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="notion", name=title, path=relative, url=url,
                                 detail=workspace or bot, size=len(text.encode()),
                                 sha=sha, authors=authors)
            written += int(changed)
        except Exception as error:
            failures.append(failure(uid, title, error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="notion",
                                         name=title, reason=error, connection_id=connection_id)
    if truncated:
        failures.append(failure(
            "notion-backlog", "More Notion pages remain",
            f"stopped at max_items={max_items}; raise the limit to finish the backlog"))
    return SyncResult(len(pages), written, failures)
