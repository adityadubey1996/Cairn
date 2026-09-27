"""Read-only GitHub issue and pull request feeder.

One item is one thread: the issue or PR body plus every comment on it, so a
citation points at the discussion rather than at a single message. The issues
endpoint returns pull requests too — each PR carries a `pull_request` key,
which labels the item instead of discarding it.

Incremental syncs pass `since`, and GitHub bumps an issue's `updated_at` when a
comment is added (verified against a public repository), so a thread that only
gained a comment is still returned. Nothing here writes at the provider.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from feeders import options
from feeders.result import SyncResult, failure
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://api.github.com"
PER_PAGE = 100
REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
log = logging.getLogger("cairn.ghissues")


class RateLimited(RuntimeError):
    """The hourly quota is spent. Stop cleanly and leave the watermark alone."""


def parse_repos(value: str) -> list[str]:
    """Accept the comma-separated owner/name list the Connect form collects."""
    names = []
    for part in str(value or "").replace("\n", ",").split(","):
        part = part.strip().removeprefix("https://github.com/").rstrip("/")
        if not part:
            continue
        if not REPO.fullmatch(part):
            raise ValueError(f"{part!r} is not a repository; use owner/name, e.g. octocat/hello-world")
        names.append(part)
    if not names:
        raise ValueError("List at least one repository as owner/name")
    return list(dict.fromkeys(names))


def _fail(error: urllib.error.HTTPError) -> Exception:
    """Translate the refusals a user can actually act on."""
    if error.code == 401:
        return PermissionError(
            "GitHub rejected the token. Create a fine-grained personal access token with "
            "Issues: read, Pull requests: read and Contents: read.")
    if error.code == 403 and error.headers.get("X-GitHub-SSO"):
        return PermissionError(
            "This organisation uses SAML single sign-on and the token is not authorised for it. "
            "Open the token at github.com/settings/tokens, choose Configure SSO, and authorise the org.")
    if error.code == 404:
        return FileNotFoundError(
            "Repository not found, or the token has no access to it. A fine-grained token must "
            "list the repository explicitly and be approved by the organisation that owns it.")
    return error


def _get(path: str, token: str, params: dict | None = None):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "cairn-ghissues"}
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
                if response.headers.get("X-RateLimit-Remaining") == "0":
                    raise RateLimited("GitHub's hourly request quota is spent; the rest of this sync was skipped")
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            retry_after = error.headers.get("Retry-After", "")
            spent = error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0"
            if spent and not retry_after:
                raise RateLimited("GitHub's hourly request quota is spent; the rest of this sync was skipped")
            if (error.code in (429, 500, 502, 503, 504) or retry_after) and attempt < 2:
                try:
                    delay = min(float(retry_after), 60)
                except ValueError:
                    delay = 0
                time.sleep(max(delay, 2 ** attempt))
                continue
            raise _fail(error) from error
    raise AssertionError("unreachable")


def _paged(path: str, token: str, params: dict | None = None,
           max_rows: int = 0) -> tuple[list[dict], bool]:
    rows, page = [], 1
    while True:
        batch = _get(path, token, {**(params or {}), "per_page": PER_PAGE, "page": page}) or []
        rows.extend(batch)
        if max_rows and len(rows) >= max_rows:
            return rows[:max_rows], len(batch) == PER_PAGE or len(rows) > max_rows
        if len(batch) < PER_PAGE:
            return rows, False
        page += 1


def list_threads(repo: str, token: str, since: str = "",
                 max_items: int = 0) -> tuple[list[dict], bool]:
    """Issues and pull requests, oldest change first so a capped run resumes."""
    params = {"state": "all", "sort": "updated", "direction": "asc"}
    if since:
        params["since"] = since
    return _paged(f"/repos/{repo}/issues", token, params, max_items)


def render_thread(repo: str, issue: dict, comments: list[dict]) -> tuple[str, str, list[str], str]:
    """Markdown in, Markdown out — GitHub bodies need no converter."""
    number = issue["number"]
    kind = "Pull request" if "pull_request" in issue else "Issue"
    title = (issue.get("title") or "").strip() or f"{kind} #{number}"
    author = (issue.get("user") or {}).get("login") or "unknown"
    labels = [l.get("name") for l in issue.get("labels") or [] if isinstance(l, dict) and l.get("name")]
    lines = [f"# {title}", "",
             f"{repo}#{number} · {kind} · {issue.get('state', '')} · "
             f"opened by {author} on {issue.get('created_at', '')}"]
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    lines += ["", (issue.get("body") or "").strip() or "_No description._"]
    authors = [author]
    for comment in comments:
        who = (comment.get("user") or {}).get("login") or "unknown"
        authors.append(who)
        lines += ["", f"## Comment by {who} on {comment.get('created_at', '')}", "",
                  (comment.get("body") or "").strip() or "_Empty comment._"]
    updated = issue.get("updated_at") or issue.get("created_at") or ""
    return title, "\n".join(lines) + "\n", list(dict.fromkeys(authors)), updated


def probe(settings: dict, limit: int = 3) -> dict:
    """Prove the token and the list call against the first repository. No writes."""
    token = str(settings.get("token") or "").strip()
    if not token:
        raise ValueError(
            "Paste a GitHub personal access token with Issues: read, Pull requests: read "
            "and Contents: read.")
    repos = parse_repos(settings.get("repos", ""))
    account = (_get("/user", token) or {}).get("login", "")
    threads, more = _paged(f"/repos/{repos[0]}/issues", token,
                           {"state": "all", "sort": "updated", "direction": "desc"}, limit)
    return {"account": account, "repositories": repos,
            "items": [{"id": f"{repos[0]}#{t['number']}", "name": t.get("title", ""),
                       "type": "pull_request" if "pull_request" in t else "issue",
                       "comments": t.get("comments", 0),
                       "updated_at": t.get("updated_at", "")} for t in threads],
            "more_available": more}


def run(project_id: str | None = None, on_progress=None, connection_id: str | None = None,
        modified_after: str = "", token: str = "", repos: str = "",
        max_items: int = 0) -> SyncResult:
    settings: dict = {}
    if connection_id:
        from server import connections
        settings = connections.settings_for(connection_id)
        # scripts/pipeline_run.py's SINCE_ARG does not name this connector, so
        # the watermark it advances is read back here rather than passed in.
        modified_after = modified_after or connections.watermark(connection_id)
    token = (token or settings.get("token") or "").strip()
    if not token:
        raise ValueError("This GitHub connection has no personal access token; reconnect it.")
    names = parse_repos(repos or settings.get("repos", ""))
    max_items = options.max_items(max_items or settings.get("max_items") or 0)
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()

    # Two projects following the same repository must not overwrite one
    # another's source records, so the path and the id carry the project.
    scope = hashlib.sha256(project_id.encode()).hexdigest()[:12]
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, f"sources/ghissues/{scope}")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    threads, failures, truncated = [], [], False
    for repo in names:
        budget = max_items - len(threads) if max_items else 0
        if max_items and budget <= 0:
            truncated = True
            break
        try:
            found, more = list_threads(repo, token, modified_after, budget)
            threads += [(repo, issue) for issue in found]
            truncated = truncated or more
        except RateLimited as error:
            failures.append(failure(f"ghissues-{repo}", repo, error))
            break
        except Exception as error:
            failures.append(failure(f"ghissues-{repo}", repo, error))

    written = 0
    for index, (repo, issue) in enumerate(threads, 1):
        number = issue["number"]
        owner, _, name = repo.partition("/")
        uid = f"ghissues-{scope}-{owner}__{name}__{number}"
        if on_progress:
            on_progress(index, len(threads), f"{repo}#{number}")
        try:
            comments = _paged(f"/repos/{repo}/issues/{number}/comments", token)[0] if issue.get("comments") else []
            title, text, authors, modified = render_thread(repo, issue, comments)
            path = sources / f"{owner}__{name}__{number}.md"
            rel = f"sources/ghissues/{scope}/{owner}__{name}__{number}.md"
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            previous_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:8] if path.is_file() else None
            entry = inbox / f"{uid}.md"
            changed = previous_sha != sha or not entry.is_file()
            if previous_sha != sha:
                path.write_text(text, encoding="utf-8")
            url = issue.get("html_url") or f"https://github.com/{repo}/issues/{number}"
            if changed:
                date, _, clock = modified.partition("T")
                entry.write_text(
                    f"---\nid: {uid}\npath: {rel}\nsha: {sha}\nsource_type: doc\nstatus: active\n"
                    f"date: {date}\ntime: {json.dumps(clock[:8])}\nauthors: {json.dumps(authors)}\n"
                    f"url: {json.dumps(url)}\n---\n\n{text}\n", encoding="utf-8")
            sources_index.record(id=uid, project_id=project_id, connection_id=connection_id,
                                 kind="ghissues", name=title, path=rel, url=url,
                                 detail=f"{repo}#{number}", size=len(text.encode()),
                                 sha=sha, authors=authors)
            written += int(changed)
        except RateLimited as error:
            failures.append(failure(uid, f"{repo}#{number}", error))
            break
        except Exception as error:
            failures.append(failure(uid, f"{repo}#{number}", error))
            sources_index.record_failure(id=uid, project_id=project_id, kind="ghissues",
                                         name=f"{repo}#{number}", reason=error,
                                         connection_id=connection_id)
    if truncated:
        # A capped sample cannot certify a complete window, so it must never let
        # the caller advance this connection's watermark.
        failures.append(failure("ghissues-backlog", "More issue threads remain",
                                "Sync reached max_items; raise the limit to finish the window"))
    return SyncResult(len(threads), written, failures)
