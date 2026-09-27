"""Read-only GitLab feeder: one issue or merge request thread per source.

Scope is the API only. Cloning a GitLab repository for its code is a different
connector — it needs the repo trust boundary in server/repos.py, which this
folder deliberately does not touch.

`host` is supplied by whoever creates the connection, so every request this
module makes starts at a hostname the server was told to trust. It is
validated as a trust boundary before a socket is opened: https only, no
embedded credentials, and no address inside the server's own network unless
the operator set GITLAB_ALLOW_PRIVATE_HOST. Redirects are re-checked, because
the PRIVATE-TOKEN header travels with them.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from feeders import options
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, connections, sources as sources_index

PER_PAGE = 100
ALLOW_PRIVATE = "GITLAB_ALLOW_PRIVATE_HOST"


class HostNotAllowed(ValueError):
    """The configured GitLab host is not one this server may fetch from."""


def _reject_private_address(host: str) -> None:
    if os.environ.get(ALLOW_PRIVATE, "").strip():
        return
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise HostNotAllowed(f"could not resolve GitLab host {host!r}: {error}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        # is_global rejects loopback, RFC1918, link-local (169.254.169.254),
        # carrier-grade NAT and every other reserved range in one check.
        if not address.is_global:
            raise HostNotAllowed(
                f"refusing to reach {host}: it resolves to the non-public address "
                f"{address}. Set {ALLOW_PRIVATE}=1 to allow a GitLab inside your network.")


def _require_public_https(url: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise HostNotAllowed(f"GitLab host must use https://, not {parsed.scheme or 'a bare scheme'}")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise HostNotAllowed("remove the credentials embedded in the GitLab host; "
                             "the token field is where the token goes")
    if not parsed.hostname:
        raise HostNotAllowed("GitLab host is missing a hostname")
    _reject_private_address(parsed.hostname)
    return parsed


def base_url(host: str) -> str:
    """Validate the user-supplied host and return its /api/v4 base.

    A bare `gitlab.com` gets https:// added. A self-hosted instance served
    under a relative URL root keeps it: `https://host/gitlab` reaches
    `https://host/gitlab/api/v4`.
    """
    raw = (host or "").strip()
    if not raw:
        raise HostNotAllowed("GitLab host is required, for example gitlab.com")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = _require_public_https(raw)
    if parsed.query or parsed.fragment:
        raise HostNotAllowed("GitLab host must be a host, optionally with a path — not a full URL")
    return f"https://{parsed.netloc}{parsed.path.rstrip('/')}/api/v4"


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _require_public_https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def _get(base: str, token: str, path: str, params: dict | None = None) -> tuple:
    """One GET against the API. Returns (payload, response headers)."""
    url = f"{base}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(3):
        request = urllib.request.Request(
            url, headers={"PRIVATE-TOKEN": token, "Accept": "application/json"})
        try:
            with _opener.open(request, timeout=60) as response:
                return json.loads(response.read()), response.headers
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PermissionError(
                    "GitLab rejected the token. It must be a personal access token "
                    "with the read_api scope, and not expired.") from error
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                delay = min(float(error.headers.get("Retry-After", "0")), 60)
            except (ValueError, TypeError):
                delay = 0
            time.sleep(max(delay, 2 ** attempt))
    raise AssertionError("unreachable")


def _paged(base: str, token: str, path: str, params: dict | None = None,
           max_items: int = 0) -> tuple[list, bool]:
    """Follow x-next-page. GitLab stops sending it on the last page."""
    query = {**(params or {}), "per_page": PER_PAGE}
    rows: list = []
    while True:
        payload, headers = _get(base, token, path, query)
        rows.extend(payload)
        if max_items and len(rows) >= max_items:
            return rows[:max_items], True
        page = (headers.get("x-next-page") or "").strip()
        if not page:
            return rows, False
        query["page"] = page


def identity(base: str, token: str) -> str:
    return _get(base, token, "user")[0].get("username", "")


def project_paths(value) -> list[str]:
    """`projects` is typed by hand, so it is checked like any other option."""
    if value in (None, ""):
        return []
    if not isinstance(value, str):
        raise ValueError("projects must be a comma-separated list of GitLab project paths")
    paths = [part.strip().strip("/") for part in value.split(",")]
    paths = [path for path in paths if path]
    for path in paths:
        if "/" not in path or any(segment == "" for segment in path.split("/")):
            raise ValueError(f"{path!r} is not a GitLab project path like group/subgroup/project")
    return list(dict.fromkeys(paths))


def list_projects(base: str, token: str, paths: list[str]) -> list[dict]:
    if paths:
        return [_get(base, token, f"projects/{urllib.parse.quote(path, safe='')}")[0]
                for path in paths]
    rows, _ = _paged(base, token, "projects", {"membership": "true", "simple": "true"})
    return rows


def list_threads(base: str, token: str, projects: list[dict], updated_after: str = "",
                 max_items: int = 0) -> tuple[list[dict], bool]:
    """Every issue and merge request touched since the watermark."""
    threads: list[dict] = []
    truncated = False
    for project in projects:
        for kind, path in (("issue", "issues"), ("merge_request", "merge_requests")):
            if max_items and len(threads) >= max_items:
                return threads, True
            params = {"updated_after": updated_after} if updated_after else {}
            rows, more = _paged(base, token, f"projects/{project['id']}/{path}", params,
                                max_items - len(threads) if max_items else 0)
            threads += [{"project": project, "kind": kind, "item": row} for row in rows]
            truncated = truncated or more
    return threads, truncated


def list_notes(base: str, token: str, project_id, kind: str, iid) -> list[dict]:
    path = "issues" if kind == "issue" else "merge_requests"
    rows, _ = _paged(base, token, f"projects/{project_id}/{path}/{iid}/notes",
                     {"sort": "asc", "order_by": "created_at"})
    # System notes are GitLab's own audit trail ("changed the label"), not
    # anything a person wrote, and they drown the discussion in a diff.
    return [note for note in rows if not note.get("system")]


def thread_ref(project: dict, kind: str, item: dict) -> str:
    """`group/subgroup/project#12`, GitLab's own notation.

    Groups nest, so the project path has any number of segments. Merge
    requests use `!` because issue 12 and merge request 12 coexist.
    """
    path = project.get("path_with_namespace") or str(project.get("id", ""))
    return f"{path}{'#' if kind == 'issue' else '!'}{item.get('iid', '')}"


def _person(record: dict | None) -> str:
    record = record or {}
    return record.get("name") or record.get("username") or ""


def render_thread(project: dict, kind: str, item: dict,
                  notes: list[dict]) -> tuple[str, str, list[str], str]:
    """Returns (title, markdown, authors, updated_at). Bodies are Markdown already."""
    title = f"{thread_ref(project, kind, item)} {item.get('title') or ''}".strip()
    author = _person(item.get("author"))
    lines = [f"# {title}", "",
             f"Type: {'Issue' if kind == 'issue' else 'Merge request'}",
             f"State: {item.get('state') or 'unknown'}",
             f"Author: {author or 'unknown'}",
             f"Created: {item.get('created_at') or ''}",
             f"Updated: {item.get('updated_at') or ''}",
             f"URL: {item.get('web_url') or ''}"]
    if item.get("labels"):
        lines.append("Labels: " + ", ".join(item["labels"]))
    lines += ["", (item.get("description") or "").strip() or "_No description._"]
    authors = [author]
    for note in notes:
        who = _person(note.get("author"))
        authors.append(who)
        lines += ["", f"## Comment by {who or 'unknown'} — {note.get('created_at') or ''}",
                  "", (note.get("body") or "").strip()]
    updated = item.get("updated_at") or item.get("created_at") or datetime.now(timezone.utc).isoformat()
    return title, "\n".join(lines).strip() + "\n", list(dict.fromkeys(a for a in authors if a)), updated


def _credentials(settings: dict) -> tuple[str, str]:
    base = base_url(settings.get("host", ""))
    token = (settings.get("token") or "").strip()
    if not token:
        raise ValueError("GitLab needs a personal access token with the read_api scope")
    return base, token


def probe(settings: dict, limit: int = 3) -> dict:
    """Authenticate, list a few threads, write nothing."""
    base, token = _credentials(settings)
    username = identity(base, token)
    projects = list_projects(base, token, project_paths(settings.get("projects")))
    threads, more = list_threads(base, token, projects[:limit], max_items=limit)
    return {"account": username, "api": base, "projects": len(projects),
            "items": [{"id": thread_ref(t["project"], t["kind"], t["item"]),
                       "name": t["item"].get("title", "")} for t in threads],
            "more_available": more}


def run(project_id: str | None = None, on_progress=None, connection_id: str | None = None,
        updated_after: str = "", max_items: int = 0, **_kwargs) -> SyncResult:
    max_items = options.max_items(max_items)
    settings = connections.settings_for(connection_id) if connection_id else {}
    base, token = _credentials(settings)
    paths = project_paths(settings.get("projects"))
    if project_id is None:
        from server import projects as knowledge_projects
        project_id = knowledge_projects.ensure_default()
    if not updated_after and connection_id:
        updated_after = connections.watermark(connection_id)
    username = identity(base, token)
    threads, truncated = list_threads(base, token, list_projects(base, token, paths),
                                      updated_after, max_items)
    # Host and account scope the directory for the same reason Gmail's mailbox
    # does: two GitLab accounts can see the same numeric project id.
    scope = hashlib.sha256(f"{project_id}\0{base}\0{username}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/gitlab/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    written, failures = 0, []
    for index, thread in enumerate(threads, 1):
        project, kind, item = thread["project"], thread["kind"], thread["item"]
        slug = f"{project['id']}-{kind}-{item.get('iid', '')}"
        uid = f"gitlab-{scope}-{slug}"
        ref = thread_ref(project, kind, item)
        if on_progress:
            on_progress(index, len(threads), ref)
        try:
            notes = list_notes(base, token, project["id"], kind, item["iid"])
            title, text, authors, updated = render_thread(project, kind, item, notes)
            path = sources / f"{slug}.md"
            rel = f"sources/gitlab/{scope}/{slug}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous_sha != sha or not entry.is_file()
            if previous_sha != sha:
                path.write_text(text, encoding="utf-8")
            url = item.get("web_url") or ""
            if changed:
                date, _, clock = updated.partition("T")
                entry.write_text(
                    f"---\nid: {uid}\npath: {rel}\nsha: {sha}\nsource_type: doc\nstatus: active\n"
                    f"date: {date}\ntime: {json.dumps(clock[:8])}\nauthors: {json.dumps(authors)}\n"
                    f"url: {json.dumps(url)}\n---\n\n{text}\n", encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="gitlab", name=title, path=rel, url=url,
                                 detail=project.get("path_with_namespace", ""),
                                 size=len(text.encode()), sha=sha, authors=authors)
            written += int(changed)
        except Exception as error:
            failures.append(failure(uid, ref, error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="gitlab",
                                         name=ref, reason=error, connection_id=connection_id)
    if truncated:
        # A capped run saw a sample, not the whole window, so it must not let
        # the caller advance the watermark past what it actually reconciled.
        failures.append(failure("gitlab-backlog", "More GitLab threads remain",
                                "Sync reached max_items; raise the limit or narrow the project list"))
    return SyncResult(len(threads), written, failures)
