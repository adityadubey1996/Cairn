"""Read-only Confluence Cloud page feeder.

One page is one source. Pages are listed newest-modified-first from the v2
API and followed through `_links.next` until the cursor runs out or a page
older than the connection's watermark appears.

Auth is an Atlassian OAuth 2.0 (3LO) sign-in — see auth.py. Calls go to
`api.atlassian.com/ex/confluence/{cloud id}`, not to the site host, and carry
the user's own permissions. Nothing is created, edited, moved or deleted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from feeders import options
from feeders.confluence import auth, storage_format
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, connections, sources as sources_index

API = "/wiki/api/v2"
PAGE_SIZE = 50
log = logging.getLogger("cairn.confluence")


class NotConfigured(ValueError):
    """A connection field the user must fix. connector_check prints it verbatim."""


def _request(path: str, params: dict | None = None) -> dict:
    """One GET against the signed-in site. Retries 429 and 5xx, honouring Retry-After."""
    if path.startswith("http"):
        split = urllib.parse.urlsplit(path)
        path = split.path + (f"?{split.query}" if split.query else "")
    # A v2 cursor link is site-relative ("/wiki/api/v2/pages?cursor=..."), while
    # a 3LO call is addressed through the cloud id. Keep from /wiki onwards and
    # drop whatever host prefix came with it.
    marker = path.find("/wiki")
    if marker > 0:
        path = path[marker:]
    url = auth.api_base() + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    for attempt in range(3):
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {auth.access_token()}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise auth.ReauthRequired(
                    "Confluence refused the sign-in. Press Connect again — the grant was "
                    "revoked, or the app is missing a Confluence scope.") from error
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                delay = min(float(error.headers.get("Retry-After", "0")), 60)
            except (ValueError, TypeError):
                delay = 0
            time.sleep(max(delay, 2 ** attempt))
    raise AssertionError("unreachable")


def _when(value: str) -> datetime:
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _modified(page: dict) -> str:
    return (page.get("version") or {}).get("createdAt") or ""


def current_user() -> str:
    return auth.account()


def _space_ids(settings: dict) -> list[str]:
    """The v2 pages endpoint filters on numeric space ids, not on the space
    keys a user can see in the URL bar, so the keys have to be resolved first.

    The field is named `spaces`, not `space_keys`: server/test_connector_registry.py
    requires any field whose name contains "key" to be a stored secret, and a
    space key is neither secret nor useful hidden from the connection row.
    """
    keys = [k.strip() for k in str(settings.get("spaces") or "").split(",") if k.strip()]
    if not keys:
        return []
    found = _request(f"{API}/spaces", {"keys": ",".join(keys), "limit": len(keys)})
    ids = [str(space["id"]) for space in found.get("results", []) if space.get("id")]
    missing = sorted(set(keys) - {space.get("key") for space in found.get("results", [])})
    if missing:
        raise NotConfigured(f"no Confluence space visible to this account for: {', '.join(missing)}")
    return ids


def list_pages(settings: dict, modified_after: str = "", max_items: int = 0) -> tuple[list[dict], bool]:
    """Newest-modified first, so the watermark ends paging rather than filtering."""
    params: dict | None = {"body-format": "storage", "limit": PAGE_SIZE, "sort": "-modified-date"}
    space_ids = _space_ids(settings)
    if space_ids:
        params["space-id"] = ",".join(space_ids)
    since = _when(modified_after) if modified_after else None
    path, pages = f"{API}/pages", []
    while True:
        payload = _request(path, params)
        params = None  # the cursor link carries the query the first call built
        for page in payload.get("results", []):
            if since and _modified(page) and _when(_modified(page)) <= since:
                return pages, False
            pages.append(page)
            if max_items and len(pages) >= max_items:
                return pages, True
        path = (payload.get("_links") or {}).get("next", "")
        if not path:
            return pages, False


def author_name(page: dict, cache: dict[str, str]) -> str:
    """`version.by` when the site expands it, otherwise one lookup per editor.

    ponytail: `cache` is per run, so it is bounded by the number of distinct
    editors in that run. A process-wide cache would need an eviction rule.
    """
    version = page.get("version") or {}
    named = (version.get("by") or {}).get("displayName")
    if named:
        return named
    account_id = version.get("authorId") or page.get("authorId") or ""
    if not account_id:
        return ""
    if account_id not in cache:
        try:
            who = _request("/wiki/rest/api/user", {"accountId": account_id})
            cache[account_id] = who.get("displayName") or account_id
        except Exception:
            # A missing display name must not fail an otherwise readable page.
            cache[account_id] = account_id
    return cache[account_id]


def render_page(page: dict, author: str = "") -> tuple[str, str, list[str], str]:
    title = (page.get("title") or "").strip() or f"Page {page.get('id')}"
    storage = ((page.get("body") or {}).get("storage") or {}).get("value") or ""
    body = storage_format.to_text(storage)
    text = f"# {title}\n\n{body}".rstrip() + "\n"
    return title, text, [author] if author else [], _modified(page)


def page_url(page: dict) -> str:
    site = auth.site_url()
    webui = (page.get("_links") or {}).get("webui") or ""
    if webui and not webui.startswith("/"):
        return webui
    return f"{site}/wiki{webui}" if webui else f"{site}/wiki/pages/viewpage.action?pageId={page.get('id')}"


def probe(settings: dict, limit: int = 3) -> dict:
    """Authenticate and list a few pages. Writes nothing."""
    account = current_user()
    pages, more = list_pages(settings, max_items=max(1, min(limit, 10)))
    items = []
    for page in pages:
        title, text, _authors, modified = render_page(page)
        items.append({"id": str(page.get("id")), "name": title,
                      "modified": modified, "extracted_characters": len(text)})
    return {"account": account, "site": auth.site_url(),
            "spaces": _space_ids(settings) or "all visible",
            "items": items, "more_available": more}


def run(modified_after: str = "", project_id: str | None = None, on_progress=None,
        connection_id: str | None = None, max_items: int = 0, **kwargs) -> SyncResult:
    settings = connections.settings_for(connection_id) if connection_id else {}
    max_items = options.max_items(max_items or settings.get("max_items") or 0)
    site = auth.site_url()
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    pages, truncated = list_pages(settings, modified_after, max_items)
    # Project and site scope keep two Confluence sites, or the same site in two
    # knowledge projects, from overwriting one another's source records.
    scope = hashlib.sha256(f"{project_id}\0{site}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/confluence/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    written, failures, names = 0, [], {}
    for index, page in enumerate(pages, 1):
        page_id = str(page.get("id"))
        uid = f"confluence-{scope}-{page_id}"
        if on_progress:
            on_progress(index, len(pages), f"Confluence page {index}")
        try:
            title, text, authors, modified = render_page(page, author_name(page, names))
            path = sources / f"{page_id}.md"
            rel = f"sources/confluence/{scope}/{page_id}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous_sha != sha or not entry.is_file()
            if previous_sha != sha:
                path.write_text(text, encoding="utf-8")
            url = page_url(page)
            if changed:
                date, clock = _when(modified).isoformat().split("T", 1) if modified else ("", "")
                entry.write_text(
                    f"---\nid: {uid}\npath: {rel}\nsha: {sha}\nsource_type: doc\nstatus: active\n"
                    f"date: {date}\ntime: {json.dumps(clock[:8])}\nauthors: {json.dumps(authors)}\n"
                    f"url: {json.dumps(url)}\n---\n\n{text}\n", encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="confluence", name=title, path=rel, url=url, detail=site,
                                 size=len(text.encode()), sha=sha, authors=authors)
            written += int(changed)
        except Exception as error:
            failures.append(failure(uid, page.get("title") or page_id, error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="confluence",
                                         name=page.get("title") or page_id, reason=error,
                                         connection_id=connection_id)
    if truncated:
        # A capped run saw a bounded sample, not a complete window. Reporting it
        # as a failure is what keeps the watermark where it was.
        failures.append(failure("confluence-backlog", "More Confluence pages remain",
                                "Sync reached max_items; raise the limit or narrow the spaces"))
    return SyncResult(len(pages), written, failures)
