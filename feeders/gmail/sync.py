"""Read-only Gmail conversation feeder.

The upstream Onyx Gmail connector was studied for its full-thread document
boundary and MIME-part handling:
https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/gmail/connector.py
This is an independent, smaller implementation of the Gmail REST API for
Cairn's source/inbox contract; it does not import or vendor the Onyx runtime.

A search selects threads; each selected thread is fetched in FULL before its
source is replaced. This preserves earlier messages when a new reply arrives.
Only the authenticated mailbox (users/me) is read. Attachments are named in
the source, but their bytes are not fetched by this connector yet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses

from feeders.google import auth
from feeders.links.sync import extract_html_text
from feeders.result import SyncResult, failure
from feeders import options
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_QUERY = "newer_than:90d -in:spam -in:trash"
log = logging.getLogger("cairn.gmail")


def _get_json(path: str, params: dict | None = None) -> dict:
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {auth.access_token()}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                delay = min(float(error.headers.get("Retry-After", "0")), 30)
            except (ValueError, TypeError):
                delay = 0
            time.sleep(max(delay, 2 ** attempt))
    raise AssertionError("unreachable")


def list_threads(query: str, modified_after: str = "", max_items: int = 0) -> tuple[list[dict], bool]:
    if modified_after:
        # Gmail after: accepts epoch seconds. One-second overlap avoids losing
        # mail at the boundary when the DB timestamp has microseconds.
        stamp = datetime.fromisoformat(modified_after.replace("Z", "+00:00"))
        query = f"{query} after:{int(stamp.timestamp()) - 1}".strip()
    rows, page_token = [], ""
    while True:
        remaining = max_items - len(rows) if max_items else 500
        params = {"q": query, "maxResults": min(500, max(1, remaining)),
                  "includeSpamTrash": "false"}
        if page_token:
            params["pageToken"] = page_token
        page = _get_json("threads", params)
        rows.extend(page.get("threads", []))
        page_token = page.get("nextPageToken", "")
        if not page_token:
            return rows, False
        if max_items and len(rows) >= max_items:
            return rows[:max_items], True


def _header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, ValueError):
        return value


def _text_part(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    if len(data) * 3 // 4 > MAX_BODY_BYTES:
        raise ValueError("email text part exceeds the 10 MB extraction limit")
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
    content_type = headers.get("content-type", "")
    charset = "utf-8"
    for field in content_type.split(";")[1:]:
        name, _, value = field.strip().partition("=")
        if name.lower() == "charset":
            charset = value.strip('"\' ')
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def message_body(payload: dict) -> tuple[str, list[str]]:
    """Choose plain text over its HTML alternative; keep attachment names."""
    attachments = []

    def visit(part: dict) -> str:
        if part.get("filename"):
            attachments.append(part["filename"])
            return ""
        mime = part.get("mimeType", "")
        children = part.get("parts", [])
        if children:
            bodies = [(child.get("mimeType", ""), visit(child)) for child in children]
            if mime == "multipart/alternative":
                plain = [text for typ, text in bodies if typ == "text/plain" and text.strip()]
                return "\n\n".join(plain or [text for _, text in bodies if text.strip()][:1])
            return "\n\n".join(text for _, text in bodies if text.strip())
        if mime == "text/plain":
            return _text_part(part)
        if mime == "text/html":
            return extract_html_text(_text_part(part))
        return ""

    return visit(payload).strip(), attachments


def render_thread(thread: dict) -> tuple[str, str, list[str], str]:
    messages = sorted(thread.get("messages", []), key=lambda m: int(m.get("internalDate", "0")))
    if not messages:
        raise ValueError("mail thread contains no readable messages")
    rendered, authors, title = [], [], ""
    for message in messages:
        payload = message.get("payload", {})
        headers = {h["name"].lower(): _header(h["value"]) for h in payload.get("headers", [])}
        title = title or headers.get("subject", "")
        lines = [f"## Message {message['id']}"]
        for key in ("from", "to", "cc", "date", "subject"):
            if headers.get(key):
                lines.append(f"{key.title()}: {headers[key]}")
        body, attachments = message_body(payload)
        if body:
            lines += ["", body]
        lines += [f"[attachment] {name}" for name in attachments]
        if not body and not attachments:
            lines += ["", "[No readable text body]"]
        rendered.append("\n".join(lines))
        authors.extend(name or address for name, address in getaddresses([headers.get("from", "")]) if name or address)
    latest = datetime.fromtimestamp(int(messages[-1].get("internalDate", "0")) / 1000, timezone.utc)
    title = title or "(no subject)"
    return title, f"# {title}\n\n" + "\n\n".join(rendered), list(dict.fromkeys(authors)), latest.isoformat()


def run(modified_after: str = "", project_id: str | None = None, on_progress=None,
        connection_id: str | None = None, query: str | None = None,
        max_items: int = 0) -> SyncResult:
    max_items = options.max_items(max_items)
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a Gmail search string")
    if not auth.has_scopes((SCOPE,)):
        raise auth.ReauthRequired("Gmail needs read-only mail consent. Reconnect Google to enable Gmail.")
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    mailbox = auth.account().strip().lower()
    if not mailbox:
        raise auth.ReauthRequired("Google account identity is missing. Reconnect Google.")
    query = query if query is not None else getattr(config, "GMAIL_QUERY", DEFAULT_QUERY)
    threads, truncated = list_threads(query, modified_after, max_items)
    # Project and mailbox scope prevent equal provider IDs or the same thread
    # in two knowledge projects from overwriting one another's source records.
    scope = hashlib.sha256(f"{project_id}\0{mailbox}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/gmail/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    written, failures = 0, []
    for index, summary in enumerate(threads, 1):
        thread_id = summary["id"]
        uid = f"gmail-{scope}-{thread_id}"
        if on_progress:
            on_progress(index, len(threads), f"Email conversation {index}")
        try:
            thread = _get_json(f"threads/{urllib.parse.quote(thread_id, safe='')}", {"format": "full"})
            title, text, authors, modified = render_thread(thread)
            path = sources / f"{thread_id}.md"
            rel = f"sources/gmail/{scope}/{thread_id}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous_sha != sha or not entry.is_file()
            if previous_sha != sha:
                path.write_text(text, encoding="utf-8")
            url = f"https://mail.google.com/mail/u/?authuser={urllib.parse.quote(mailbox)}#all/{thread_id}"
            if changed:
                date, clock = modified.split("T", 1)
                entry.write_text(
                    f"---\nid: {uid}\npath: {rel}\nsha: {sha}\nsource_type: doc\nstatus: active\n"
                    f"date: {date}\ntime: {json.dumps(clock[:8])}\nauthors: {json.dumps(authors)}\n"
                    f"url: {json.dumps(url)}\n---\n\n{text}\n", encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="gmail", name=title, path=rel, url=url, detail=mailbox,
                                 size=len(text.encode()), sha=sha, authors=authors)
            written += int(changed)
        except Exception as error:
            failures.append(failure(uid, thread_id, error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="gmail",
                                         name=thread_id, reason=error, connection_id=connection_id)
    if truncated:
        # A caller-requested sample is useful for a demo but cannot certify a
        # complete time window. Never let it advance a connection watermark.
        failures.append(failure("gmail-backlog", "More email conversations remain",
                                "Sync reached max_items; increase the limit or narrow the Gmail query"))
    return SyncResult(len(threads), written, failures)
