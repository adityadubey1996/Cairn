"""Read-only Jira Cloud issue feeder.

One item = one issue. Its description and its comments are rendered into a
single source, so a citation lands on the whole discussion rather than on a
fragment that reads as an unanswered question.

Authentication is HTTP Basic `email:API-token` — the token a user creates at
id.atlassian.com, which needs no site-admin approval and no redirect URI. The
same token also reaches Confluence; this connector never calls it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from feeders import options
from feeders.jira import auth
from feeders.jira.adf import to_text
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, connections, sources as sources_index

SEARCH = "/rest/api/3/search/jql"
IDENTITY = "/rest/api/3/myself"
# Exactly what a source is built from. Asking for less is the cheapest way to
# keep a full-site sweep inside Jira's response budget.
FIELDS = ["summary", "updated", "reporter", "creator", "description", "comment"]
PAGE = 50
COMMENT_CAP = 100
TRUTHY = ("1", "yes", "y", "true", "on")
log = logging.getLogger("cairn.jira")


def _site(settings: dict) -> str:
    """The host this connection reads.

    A signed-in connection names no site — the user never typed one — so the
    sign-in answers for it. Only a pasted-token connection must say which site
    it means, because a token carries no list of what it can reach.
    """
    site = str(settings.get("site") or "").strip()
    if not site:
        if settings.get("token"):
            raise ValueError("Jira needs a site URL such as yourteam.atlassian.net")
        return auth.site_for()["host"]
    host = urllib.parse.urlsplit(site if "//" in site else f"https://{site}").hostname
    if not host:
        raise ValueError("Jira needs a site URL such as yourteam.atlassian.net")
    return host


def _authorization(settings: dict) -> str:
    email = str(settings.get("email") or "").strip()
    token = str(settings.get("token") or "").strip()
    if not email or not token:
        raise ValueError("Jira needs the account email and its API token from id.atlassian.com")
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


def _transport(settings: dict) -> tuple[str, str]:
    """(base url, Authorization header) for whichever credential is in play.

    The sign-in wins when there is one: it is the path a user actually chose,
    and it reaches the site through the Atlassian gateway under that site's
    cloud id. A connection carrying a pasted API token talks to the site host
    directly. Everything above this function is identical either way.
    """
    if settings.get("token"):
        return f"https://{_site(settings)}", _authorization(settings)
    site = auth.site_for(str(settings.get("site") or "").strip())
    return f"{auth.GATEWAY}/ex/jira/{site['cloud_id']}", f"Bearer {auth.access_token()}"


def _request(settings: dict, path: str, body: dict | None = None) -> dict:
    base, authorization = _transport(settings)
    url = f"{base}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": authorization, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PermissionError("Jira rejected the credentials. " + (
                    "Check the site URL, the account email and that the API token is still listed "
                    "at id.atlassian.com → Security → API tokens."
                    if settings.get("token") else
                    "Sign in to Atlassian again from the Connect screen; the approval may have been revoked.")) from None
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            try:
                delay = min(float(error.headers.get("Retry-After", "0")), 30)
            except (ValueError, TypeError):
                delay = 0
            time.sleep(max(delay, 2 ** attempt))
    raise AssertionError("unreachable")


def incremental_jql(modified_after: str = "") -> str:
    """The window to re-read, as JQL.

    A relative `-Nm` rather than the timestamp literal the plan called for: JQL
    reads `updated >= "2026-09-20 09:00"` in the SITE's timezone, which is not
    the watermark's, so a site several hours off UTC silently skips or repeats a
    day's issues. Minutes-ago has no timezone to get wrong. Two extra minutes
    cover the clock skew between this host and Atlassian.
    """
    if not modified_after:
        return "ORDER BY updated ASC"
    stamp = datetime.fromisoformat(str(modified_after).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    minutes = math.ceil((datetime.now(timezone.utc) - stamp).total_seconds() / 60) + 2
    return f"updated >= -{max(minutes, 1)}m ORDER BY updated ASC"


def search_issues(settings: dict, jql: str, max_items: int = 0) -> tuple[list[dict], bool]:
    """Every issue the JQL selects, and whether max_items cut the window short."""
    rows: list[dict] = []
    page_token = ""
    while True:
        remaining = max_items - len(rows) if max_items else PAGE
        body = {"jql": jql, "fields": FIELDS, "maxResults": min(PAGE, max(1, remaining))}
        if page_token:
            body["nextPageToken"] = page_token
        page = _request(settings, SEARCH, body)
        rows.extend(page.get("issues") or [])
        page_token = page.get("nextPageToken") or ""
        if not page_token:
            return rows, False
        if max_items and len(rows) >= max_items:
            return rows[:max_items], True


def _comments(settings: dict, key: str) -> list[dict]:
    # ponytail: the first 100 comments of an issue. Page on startAt if a real
    # issue ever runs past that.
    path = (f"/rest/api/3/issue/{urllib.parse.quote(key, safe='')}/comment"
            f"?maxResults={COMMENT_CAP}&orderBy=created")
    return _request(settings, path).get("comments") or []


def render_issue(issue: dict, comments: list[dict] | None = None) -> tuple[str, str, list[str], str]:
    """(title, text, authors, updated) for one issue."""
    fields = issue.get("fields") or {}
    key = issue.get("key") or issue.get("id") or ""
    title = str(fields.get("summary") or key).strip()
    reporter = (fields.get("reporter") or fields.get("creator") or {}).get("displayName") or ""
    authors = [reporter.strip()] if reporter.strip() else []
    lines = [f"# {key} {title}".strip()]
    description = to_text(fields.get("description"))
    lines += ["", description] if description else []
    rows = comments if comments is not None else ((fields.get("comment") or {}).get("comments") or [])
    for comment in rows:
        who = str((comment.get("author") or {}).get("displayName") or "").strip()
        when = str(comment.get("created") or "")[:10]
        text = to_text(comment.get("body"))
        lines += ["", f"## Comment — {who or 'Unknown'} {when}".strip(),
                  "", text or "[No readable text]"]
        if who:
            authors.append(who)
    if not description and not rows:
        lines += ["", "[No description or comments]"]
    return title, "\n".join(lines), list(dict.fromkeys(authors)), str(fields.get("updated") or "")


def probe(settings: dict, limit: int = 3) -> dict:
    """Prove the token reads this site, and list a few issues. Writes nothing."""
    me = _request(settings, IDENTITY)
    issues, more = search_issues(settings, incremental_jql(), max_items=limit)
    return {
        "site": _site(settings),
        "account": f"{me.get('displayName', '')} <{me.get('emailAddress', '') or 'hidden'}>".strip(),
        "items": [{"id": issue.get("key"),
                   "name": (issue.get("fields") or {}).get("summary", ""),
                   # Answers the one thing a site can differ on: whether reading
                   # comments costs an extra request per issue.
                   "comments_inline": "comment" in (issue.get("fields") or {})}
                  for issue in issues],
        "more_available": more,
    }


def run(project_id: str | None = None, on_progress=None, connection_id: str | None = None,
        modified_after: str = "", max_items: int = 0) -> SyncResult:
    settings = connections.settings_for(connection_id) if connection_id else {}
    max_items = options.max_items(max_items or settings.get("max_items") or 0)
    site = _site(settings)
    _transport(settings)  # fail on a half-filled connection, not mid-sweep
    separate_comments = str(settings.get("comments") or "").strip().lower() in TRUTHY
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    if not modified_after and connection_id:
        # scripts/pipeline_run.py only forwards --since for the connectors named
        # in its SINCE_ARG map, so this connector reads its own checkpoint. The
        # pipeline still owns advancing it after a complete scrape.
        modified_after = connections.watermark(connection_id)
    issues, truncated = search_issues(
        settings, scoped_jql(settings.get("scope"), modified_after), max_items)
    # Project and site scope stop two Jira sites sharing an issue key — or the
    # same site in two knowledge projects — from overwriting each other.
    scope = hashlib.sha256(f"{project_id}\0{site}".encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/jira/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)
    written, failures = 0, []
    for index, issue in enumerate(issues, 1):
        key = str(issue.get("key") or "")
        uid = f"jira-{scope}-{key}"
        if on_progress:
            on_progress(index, len(issues), f"Issue {key}")
        try:
            fields = issue.get("fields") or {}
            comments = (_comments(settings, key)
                        if separate_comments and "comment" not in fields else None)
            title, text, authors, updated = render_issue(issue, comments)
            path = sources / f"{key}.md"
            rel = f"sources/jira/{scope}/{key}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous_sha != sha or not entry.is_file()
            if previous_sha != sha:
                path.write_text(text, encoding="utf-8")
            url = f"https://{site}/browse/{urllib.parse.quote(key, safe='')}"
            if changed:
                entry.write_text(
                    f"---\nid: {uid}\npath: {rel}\nsha: {sha}\nsource_type: doc\nstatus: active\n"
                    f"date: {updated[:10]}\ntime: {json.dumps(updated[11:19])}\n"
                    f"authors: {json.dumps(authors)}\nurl: {json.dumps(url)}\n---\n\n{text}\n",
                    encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="jira", name=title, path=rel, url=url, detail=site,
                                 size=len(text.encode()), sha=sha, authors=authors)
            written += int(changed)
        except Exception as error:
            failures.append(failure(uid, key, error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="jira",
                                         name=key, reason=error, connection_id=connection_id)
    if truncated:
        # A capped run saw a slice of the window, not the window. Reported as a
        # failure so the pipeline leaves the watermark where it is.
        failures.append(failure("jira-backlog", "More issues remain",
                                "Sync reached max_items; raise the limit to finish this window"))
    return SyncResult(len(issues), written, failures)


# --------------------------------------------------------------------- scope
# What this connection is allowed to read. The feeder expresses it as JQL
# because that is what it already sends: search_issues() takes a query, so a
# scope is a query change, not a new code path.

PROJECT_SEARCH = "/rest/api/3/project/search"

ISSUE_TYPES = ("Story", "Bug", "Task", "Epic", "Sub-task")
STATUS_CATEGORIES = ("To Do", "In Progress", "Done")


def _jql_quote(value: str) -> str:
    """JQL string literal. Project keys are bare word characters, but issue
    types and statuses are user-facing names that can hold spaces or quotes."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def list_projects(settings: dict) -> list[dict]:
    """Every project this connection can see, grouped by Jira's own project
    category — the grouping a Jira admin already made, rather than one Cairn
    invents.

    `insight.totalIssueCount` is what tells someone a scope's size before they
    save it. Jira omits it on some plans, so a missing count is left absent
    rather than guessed at.
    """
    groups: dict[str, dict] = {}
    start = 0
    while True:
        page = _request(settings, f"{PROJECT_SEARCH}?maxResults=50&startAt={start}"
                                  "&expand=insight&orderBy=name")
        for project in page.get("values", []):
            category = (project.get("projectCategory") or {}).get("name") or "Uncategorised"
            group = groups.setdefault(category, {"id": category, "name": category,
                                                 "note": "project category", "items": []})
            insight = project.get("insight") or {}
            group["items"].append({
                "id": project.get("key"),
                "name": project.get("name") or project.get("key"),
                "detail": project.get("key"),
                "count": insight.get("totalIssueCount"),
            })
        if page.get("isLast", True) or not page.get("values"):
            break
        start += len(page["values"])
    return sorted(groups.values(), key=lambda g: g["name"].lower())


def scoped_jql(scope: dict | None, modified_after: str = "") -> str:
    """The incremental window, narrowed to what the scope allows.

    Order matters: the clauses are ANDed and the ORDER BY has to stay last,
    which is why incremental_jql's own ordering is stripped and re-appended
    rather than concatenated onto.
    """
    window = incremental_jql(modified_after)
    clauses = [window.replace("ORDER BY updated ASC", "").strip()]

    scope = scope or {}
    if scope.get("items"):
        clauses.append("project in (%s)" % ", ".join(_jql_quote(k) for k in scope["items"]))
    if scope.get("issue_types"):
        clauses.append("issuetype in (%s)" % ", ".join(_jql_quote(t) for t in scope["issue_types"]))
    if scope.get("statuses"):
        clauses.append("statusCategory in (%s)" % ", ".join(_jql_quote(s) for s in scope["statuses"]))
    # Only on a first sync. Once there is a watermark the window above is
    # already narrower, and ANDing a fixed -Nd on top would silently stop
    # re-reading anything older that changed.
    if not modified_after and scope.get("updated_within_days"):
        clauses.append(f"updated >= -{int(scope['updated_within_days'])}d")

    where = " AND ".join(c for c in clauses if c)
    return f"{where} ORDER BY updated ASC" if where else "ORDER BY updated ASC"
