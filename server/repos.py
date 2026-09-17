"""Track a public GitHub repo, clone it, and drive the pipeline against the clone.

ai-brain used to only consume wikis other repos had committed. This makes it
produce them: graphify → ingest → absorb, run against a clone we own.

Everything a browser sends ends up in a `git` argv, so §"validation" below is
the trust boundary and runs before any subprocess. The shape is whitelisted, not
sanitised — there is no cleaning step, only accept or reject.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import config, runs
from .db import connect

# ---------------------------------------------------------------- validation

OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")

STATES = ("added", "cloning", "cloned", "graphed", "ingested",
          "absorbing", "ready", "failed", "evicted")


class Invalid(ValueError):
    """Rejected at the trust boundary. Maps to 400."""


def parse_url(url: str) -> tuple[str, str]:
    """(owner, name) or raise. Whitelist the whole shape; never repair it.

    Rejected by construction: any other scheme or host, userinfo, a port, a
    query, extra path depth. `https://evil.com/github.com/a/b` fails the anchor,
    `https://user:pass@github.com/a/b` fails because userinfo is part of the
    authority the pattern does not admit.
    """
    m = URL_RE.match((url or "").strip())
    if not m:
        raise Invalid("expected https://github.com/<owner>/<repo>")
    owner, name = m.group(1), m.group(2)
    if not OWNER_RE.match(owner):
        raise Invalid(f"invalid owner: {owner!r}")
    if not NAME_RE.match(name) or name in (".", "..") or name.startswith("-"):
        raise Invalid(f"invalid repo name: {name!r}")
    return owner, name


def parse_local(raw: str) -> tuple[str, str, Path]:
    """(owner, name, path) for a working tree on this machine, or raise.

    A private repo needs no credential if it is already checked out here: git
    clones from a directory exactly like any other remote, full history intact.

    The path is untrusted input, so it must resolve inside REPO_LOCAL_ROOTS —
    otherwise a browser could clone ~/.ssh. `owner` is the containing folder,
    which keeps the on-disk layout identical to the github case.
    """
    p = (raw or "").strip()
    if p.startswith("file://"):
        p = p[7:]
    if not p.startswith(("/", "~")):
        raise Invalid("expected an absolute path, or https://github.com/<owner>/<repo>")
    path = Path(p).expanduser().resolve()
    if not any(path == r or path.is_relative_to(r) for r in config.REPO_LOCAL_ROOTS):
        raise Invalid(f"{path} is outside the allowed roots "
                      f"({', '.join(str(r) for r in config.REPO_LOCAL_ROOTS)})")
    if not (path / ".git").exists():
        raise Invalid(f"{path} is not a git repository")
    owner, name = path.parent.name or "local", path.name
    if not NAME_RE.match(name) or not NAME_RE.match(owner):
        raise Invalid(f"unusable folder name: {owner}/{name}")
    return owner, name, path


def is_local(url: str) -> bool:
    u = (url or "").strip()
    return u.startswith(("/", "~", "file://"))


def check_branch(branch: str) -> str:
    """git's own refname rules, plus a leading-dash ban.

    A leading `-` is argument injection: a branch called `--upload-pack=sh` would
    otherwise be read as a flag. Every git call also puts `--` before operands,
    so this is the second of two locks.
    """
    b = (branch or "").strip()
    if not BRANCH_RE.match(b) or b.startswith("-") or ".." in b \
            or b.endswith(".lock") or "//" in b or b.endswith("/"):
        raise Invalid(f"invalid branch: {branch!r}")
    return b


def check_sha(sha: str) -> str:
    s = (sha or "").strip().lower()
    if not SHA_RE.match(s):
        raise Invalid(f"invalid commit sha: {sha!r}")
    return s


def set_token(repo_id: str, token: str) -> None:
    """A per-repo access token, stored where the provider key already lives
    rather than in brain_repos beside the URL — a credential does not belong in
    a row the UI lists."""
    with connect() as c:
        c.execute(
            "INSERT INTO brain_settings (id, value) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()",
            (f"repo_token:{repo_id}", json.dumps({"token": token})))


def clear_token(repo_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM brain_settings WHERE id = %s",
                  (f"repo_token:{repo_id}",))


def token_for(repo_id: str) -> str:
    """The repo's own token, else the deployment-wide one. The specific beats
    the general, which is what lets one private repo be added without making
    every repo depend on a GITHUB_TOKEN in the environment."""
    with connect() as c:
        r = c.execute("SELECT value FROM brain_settings WHERE id = %s",
                      (f"repo_token:{repo_id}",)).fetchone()
    return ((r["value"] or {}).get("token") if r else "") or config.GITHUB_TOKEN


def clone_url(owner: str, name: str) -> str:
    """Rebuilt from validated parts, never echoed from user input.

    A token is embedded here and nowhere else: it never reaches the browser,
    never lands in brain_repos.url, and never appears in a run row. Errors are
    truncated stderr from git, which prints the URL with the token redacted,
    but treat any surfaced stderr as sensitive anyway.

    This is what makes a private repo reachable on a server, where there are no
    working trees to clone from and no SSH agent.
    """
    token = token_for(f"{owner}/{name}".lower())
    if token:
        return (f"https://x-access-token:{token}"
                f"@github.com/{owner}/{name}.git")
    return f"https://github.com/{owner}/{name}.git"


def _redact(msg: str) -> str:
    """Never let a token reach a run row or an HTTP response.

    Every known token, not just the environment's: a per-repo token would
    otherwise sail straight through into an error message.
    """
    out = msg
    if config.GITHUB_TOKEN:
        out = out.replace(config.GITHUB_TOKEN, "***")
    with connect() as c:
        rows = c.execute(
            "SELECT value FROM brain_settings WHERE id LIKE 'repo_token:%'").fetchall()
    for r in rows:
        tok = (r["value"] or {}).get("token")
        if tok:
            out = out.replace(tok, "***")
    return out


def _sandboxed(base: Path, owner: str, name: str) -> Path:
    """Belt and braces on top of the regex: a traversal that somehow survived
    validation still cannot escape the sandbox."""
    base = base.resolve()
    dest = (base / owner / name).resolve()
    if not dest.is_relative_to(base):
        raise Invalid("path escapes the sandbox")
    return dest


def clone_dir(owner: str, name: str) -> Path:
    return _sandboxed(config.REPO_CLONE_DIR, owner, name)


def wiki_dir(owner: str, name: str) -> Path:
    return _sandboxed(config.REPO_WIKI_DIR, owner, name) / "wiki"


# ---------------------------------------------------------------- subprocess

def _env() -> dict[str, str]:
    """Scrubbed: git must never prompt, never read a system config, and never
    pick up an ambient token. A private repo fails fast as "not reachable"."""
    e = {k: v for k, v in os.environ.items()
         if k not in ("GITHUB_TOKEN", "GH_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS")}
    e.update({"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/true",
              "GIT_CONFIG_NOSYSTEM": "1", "GCM_INTERACTIVE": "never"})
    return e


def run(args: list[str], *, cwd: Path | None = None, timeout: int = 120,
        env_extra: dict[str, str] | None = None) -> str:
    """Argument list, shell=False, explicit timeout. Never a shell string.

    `env_extra` is layered on the scrubbed env — absorb uses it to hand the
    resolved LLM provider to a subprocess that cannot import server/llm.py.
    """
    p = subprocess.run(args, cwd=str(cwd) if cwd else None,
                       env={**_env(), **(env_extra or {})},
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(_redact((p.stderr or p.stdout or "").strip()[-2000:])
                           or f"{args[0]} exited {p.returncode}")
    return p.stdout


def git(args: list[str], *, cwd: Path | None = None, timeout: int = 120) -> str:
    return run(["git", *args], cwd=cwd, timeout=timeout)


# ---------------------------------------------------------------- reachability

def probe(url: str) -> dict:
    """Is it reachable, without cloning anything.

    ls-remote is the authority for both cases: it needs no auth, has no API
    rate limit, and succeeds only for a repo we can actually clone. For a
    github URL the API call adds the size, best-effort.
    """
    if is_local(url):
        owner, name, path = parse_local(url)
        out = git(["ls-remote", "--heads", "--exit-code", "--", str(path)], timeout=20)
        branches = sorted({ln.split("refs/heads/", 1)[1]
                           for ln in out.splitlines() if "refs/heads/" in ln})
        cur = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path).strip()
        size_kb = _dir_bytes(path / ".git") // 1024
        return {"ok": True, "slug": f"{owner}/{name}".lower(), "url": str(path),
                "source": "local", "branches": branches,
                "default_branch": cur if cur in branches else (branches[0] if branches else None),
                "size_kb": size_kb, "private": None,
                "too_big": size_kb / 1024 > config.REPO_MAX_SIZE_MB}

    owner, name = parse_url(url)
    slug = f"{owner}/{name}"
    try:
        out = git(["ls-remote", "--heads", "--exit-code", "--", clone_url(owner, name)],
                  timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError("timed out contacting github")
    branches = sorted({ln.split("refs/heads/", 1)[1]
                       for ln in out.splitlines() if "refs/heads/" in ln})

    size_kb = default_branch = private = None
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{name}",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "ai-brain"})
        with urllib.request.urlopen(req, timeout=10) as r:
            meta = json.load(r)
        size_kb, default_branch = meta.get("size"), meta.get("default_branch")
        private = meta.get("private")
    except Exception:
        pass  # unauthenticated API is rate-limited; ls-remote already answered

    if default_branch not in branches:
        default_branch = ("main" if "main" in branches else
                          "master" if "master" in branches else
                          branches[0] if branches else None)
    too_big = bool(size_kb and size_kb / 1024 > config.REPO_MAX_SIZE_MB)
    return {"ok": True, "slug": slug, "url": f"https://github.com/{owner}/{name}",
            "branches": branches, "default_branch": default_branch,
            "size_kb": size_kb, "private": private, "too_big": too_big}


# ---------------------------------------------------------------- persistence

FIELDS = ("id, owner, name, url, branch, state, last_error, clone_path, "
          "wiki_root, head_sha, pinned_sha, articles, queue_new, queue_changed, "
          "clone_bytes, last_used_at, created_at, updated_at")


def list_repos() -> list[dict]:
    with connect() as c:
        return c.execute(f"SELECT {FIELDS} FROM brain_repos ORDER BY id").fetchall()


def get(repo_id: str) -> dict | None:
    with connect() as c:
        return c.execute(f"SELECT {FIELDS} FROM brain_repos WHERE id = %s",
                         (repo_id,)).fetchone()


def _require(repo_id: str) -> dict:
    row = get(repo_id)
    if not row:
        raise KeyError(f"no such repo: {repo_id}")
    return row


def update(repo_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE brain_repos SET {cols}, updated_at = now() WHERE id = %s",
                  (*fields.values(), repo_id))


def add(url: str, branch: str | None = None, token: str = "") -> dict:
    if is_local(url):
        owner, name, _p = parse_local(url)
    else:
        owner, name = parse_url(url)
    slug = f"{owner}/{name}".lower()
    if get(slug):
        raise Invalid(f"already tracked: {slug}")
    # Stored before probe(), because probe() is the first thing that needs it:
    # ls-remote against a private repo fails without auth.
    if token.strip():
        set_token(slug, token.strip())
    if len(list_repos()) >= config.REPO_MAX_TRACKED:
        raise Invalid(f"at the tracked-repo cap ({config.REPO_MAX_TRACKED})")

    info = probe(url)
    if info["too_big"]:
        raise TooBig(f"{slug} is ~{info['size_kb'] // 1024} MB, "
                     f"cap is {config.REPO_MAX_SIZE_MB} MB")
    b = check_branch(branch or info["default_branch"] or "main")
    if info["branches"] and b not in info["branches"]:
        raise Invalid(f"branch {b!r} not on {slug}")

    with connect() as c:
        c.execute(
            "INSERT INTO brain_repos (id, owner, name, url, branch, state) "
            "VALUES (%s, %s, %s, %s, %s, 'added')",
            (slug, owner, name, info["url"], b))
    return _require(slug)


class TooBig(Invalid):
    """Maps to 413 rather than 400."""


def remove(repo_id: str, keep_wiki: bool = True) -> dict:
    row = _require(repo_id)
    if row["clone_path"]:
        shutil.rmtree(row["clone_path"], ignore_errors=True)
    if not keep_wiki and row["wiki_root"]:
        # The wiki is the only thing here that cost money. Deleting it is
        # explicit and never the default, including when the row goes away.
        shutil.rmtree(Path(row["wiki_root"]).parent, ignore_errors=True)
    # Drop the owner directory too if it is now empty, or var/clones fills with
    # empty folders as repos come and go. rmdir only removes empty ones, so a
    # sibling repo under the same owner is never touched.
    for base in (config.REPO_CLONE_DIR, config.REPO_WIKI_DIR):
        owner_dir = Path(base) / row["owner"]
        try:
            owner_dir.rmdir()
        except OSError:
            pass

    with connect() as c:
        c.execute("DELETE FROM brain_repos WHERE id = %s", (repo_id,))
    return {"deleted": repo_id, "wiki_kept": keep_wiki}


def commits(repo_id: str, limit: int = 50) -> list[dict]:
    row = _require(repo_id)
    if not row["clone_path"] or not Path(row["clone_path"]).is_dir():
        raise NotCloned(repo_id)
    limit = max(1, min(int(limit), 200))
    out = git(["log", f"-{limit}", "--date=short",
               "--format=%H%x1f%h%x1f%ad%x1f%an%x1f%s", "--", "."],
              cwd=Path(row["clone_path"]))
    rows = []
    for ln in out.splitlines():
        parts = ln.split("\x1f")
        if len(parts) == 5:
            rows.append(dict(zip(("sha", "short", "date", "author", "subject"), parts)))
    return rows


class NotCloned(RuntimeError):
    """Maps to 409 — the step needs a clone that does not exist yet."""


def queue_remaining(row: dict) -> tuple[int, int]:
    """(remaining, absorbed) — what is actually left to buy.

    brain_repos.queue_new is written by ingest and never touched by absorb, so
    the card showed the same number after buying 34 articles as before. The
    honest figure is the pending queue minus the absorb log, which is what
    absorb_runner itself filters on.
    """
    clone = Path(row.get("clone_path") or "")
    pending = clone / "raw" / "_pending.json"
    if not pending.is_file():
        return int(row.get("queue_new") or 0), 0
    try:
        pend = json.loads(pending.read_text())
        ids = set(pend.get("new") or []) | set(pend.get("changed") or [])
    except Exception:
        return int(row.get("queue_new") or 0), 0

    absorbed: set[str] = set()
    log = Path(row.get("wiki_root") or "") / "_absorb_log.json"
    if log.is_file():
        try:
            absorbed = set(json.loads(log.read_text()))
        except Exception:
            pass
    return len(ids - absorbed), len(absorbed)


def count_articles(wiki_root: str | None) -> int:
    """Articles on disk. The live count during a run — absorb writes them one
    at a time, while the brain_repos column only updates when the run ends."""
    if not wiki_root:
        return 0
    p = Path(wiki_root)
    if not p.is_dir():
        return 0
    return sum(1 for f in p.rglob("*.md") if not f.name.startswith("_"))


def queue(repo_id: str) -> dict:
    """What an absorb would buy, unit by unit.

    The card shows a count; this shows the contents, which is the only way to
    see that a queue of 117 includes LICENSE.txt and a 6-line Makefile before
    paying for articles about them.
    """
    row = _require(repo_id)
    clone = Path(row["clone_path"] or "")
    pending = clone / "raw" / "_pending.json"
    manifest = clone / "raw" / "_manifest.json"
    if not pending.is_file() or not manifest.is_file():
        raise NotCloned("ingest has not run yet")
    try:
        pend = json.loads(pending.read_text())
        units = {u["id"]: u for u in json.loads(manifest.read_text())}
    except Exception as e:
        raise RuntimeError(f"unreadable queue: {e}")

    absorbed = set()
    log = Path(row["wiki_root"] or "") / "_absorb_log.json"
    if log.is_file():
        try:
            absorbed = set(json.loads(log.read_text()))
        except Exception:
            pass

    def rows(ids):
        out = []
        for uid in ids:
            u = units.get(uid) or {}
            out.append({"id": uid, "kind": u.get("kind", "?"),
                        "path": u.get("path", uid), "status": u.get("status"),
                        "first": (u.get("first") or "")[:10],
                        "absorbed": uid in absorbed})
        return sorted(out, key=lambda r: (r["kind"], r["path"]))

    by_kind: dict[str, int] = {}
    for r in rows(pend.get("new", [])) + rows(pend.get("changed", [])):
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1

    return {"repo": repo_id, "by_kind": by_kind, "absorbed_total": len(absorbed),
            "new": rows(pend.get("new", [])),
            "changed": rows(pend.get("changed", [])),
            "removed": pend.get("removed", [])}


def runs(repo_id: str, limit: int = 20) -> list[dict]:
    with connect() as c:
        return c.execute(
            "SELECT id, step, status, items_seen, items_written, error, "
            "started_at, finished_at FROM brain_connector_runs "
            "WHERE repo_id = %s ORDER BY started_at DESC LIMIT %s",
            (repo_id, max(1, min(int(limit), 100)))).fetchall()


# ---------------------------------------------------------------- job registry

# ponytail: in-process job registry — correct for one uvicorn worker, which is
# what ai-brain runs. Multi-worker would need the lock in Postgres
# (SELECT ... FOR UPDATE on brain_repos).
_jobs: dict[str, str] = {}
_lock = threading.Lock()


def busy() -> dict[str, str]:
    with _lock:
        return dict(_jobs)


def _claim(repo_id: str, step: str) -> None:
    with _lock:
        if repo_id in _jobs:
            raise Busy(f"{repo_id} is already running {_jobs[repo_id]}")
        _jobs[repo_id] = step


def _release(repo_id: str) -> None:
    with _lock:
        _jobs.pop(repo_id, None)


class Busy(RuntimeError):
    """Maps to 409."""


# ---------------------------------------------------------------- steps

TIMEOUTS = {"clone": 900, "graph": 600, "ingest": 600, "absorb": 1800}


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _seed_wiki(clone: Path, wiki: Path) -> None:
    """Adopt a wiki the repo committed, once, as prior state.

    Articles live outside the clone and both scripts take --wiki, so nothing is
    symlinked here. A symlink was the first design and it does not survive: a
    repo that commits its own wiki/ has `git reset --hard` restore it as a real
    directory on every clone, silently replacing the link.

    Seeding still matters — _manifest.json is the drift baseline and
    _absorb_log.json records what was already paid for, so adopting them resumes
    the queue instead of re-buying every article. Only ever seeds an empty
    durable wiki; generated articles are never overwritten from the clone.
    """
    wiki.mkdir(parents=True, exist_ok=True)
    committed = clone / "wiki"
    if not committed.is_dir() or committed.is_symlink() or any(wiki.iterdir()):
        return
    for src in committed.iterdir():
        dst = wiki / src.name
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)


def step_clone(row: dict) -> dict:
    owner, name, branch = row["owner"], row["name"], check_branch(row["branch"])
    clone = clone_dir(owner, name)
    wiki = wiki_dir(owner, name)
    # A local working tree is re-validated on every clone, not trusted from the
    # row: the allow-list may have changed since it was added.
    url = (str(parse_local(row["url"])[2]) if is_local(row["url"])
           else clone_url(owner, name))
    t = TIMEOUTS["clone"]

    if (clone / ".git").is_dir():
        git(["fetch", "--no-tags", "origin", "--", branch], cwd=clone, timeout=t)
        git(["reset", "--hard", f"origin/{branch}"], cwd=clone, timeout=t)
    else:
        shutil.rmtree(clone, ignore_errors=True)
        clone.parent.mkdir(parents=True, exist_ok=True)
        # Full history, never --depth or --filter: ingest.py derives each unit's
        # first-seen date from git log, and chronological absorb depends on it.
        # A shallow clone silently stamps 1970-01-01 and destroys the ordering.
        git(["clone", "--branch", branch, "--single-branch", "--no-tags",
             "--", url, str(clone)], timeout=t)

    _seed_wiki(clone, wiki)
    head = git(["rev-parse", "HEAD"], cwd=clone).strip()
    size = _dir_bytes(clone)
    update(row["id"], state="cloned", head_sha=head, clone_path=str(clone),
           wiki_root=str(wiki), clone_bytes=size, last_error=None,
           last_used_at=datetime.now(timezone.utc))
    return {"items_seen": 1, "items_written": 1, "head": head[:8],
            "bytes": size}


def step_graph(row: dict) -> dict:
    clone = Path(row["clone_path"] or "")
    if not clone.is_dir():
        raise NotCloned(row["id"])
    try:
        run([config.GRAPHIFY_BIN, "update", str(clone)],
            cwd=clone, timeout=TIMEOUTS["graph"])
        state = "graphed"
        warn = None
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        # Non-fatal by design: ingest.py degrades to unenriched fact sheets when
        # graph.json is absent. A worse wiki beats a stuck pipeline.
        state, warn = "graphed", f"graphify failed, continuing unenriched: {e}"[:2000]
    update(row["id"], state=state, last_error=warn)
    return {"items_seen": 1, "items_written": 0 if warn else 1, "warning": warn}


def _queue_counts(clone: Path, out: Path) -> tuple[int, int]:
    """What an absorb would cost, shown before it is bought."""
    for p in (out.parent / "_pending.json", clone / "raw" / "_pending.json"):
        if p.is_file():
            try:
                q = json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(q, dict):
                return len(q.get("new") or []), len(q.get("changed") or [])
    return 0, 0


def step_ingest(row: dict, commit: str | None = None) -> dict:
    clone = Path(row["clone_path"] or "")
    if not clone.is_dir():
        raise NotCloned(row["id"])
    branch = check_branch(row["branch"])

    if commit:
        sha = check_sha(commit)
        # Must be an ancestor of the tracked branch: this is also what stops a
        # sha copied from a different repo.
        try:
            git(["merge-base", "--is-ancestor", sha, f"origin/{branch}"], cwd=clone)
        except RuntimeError:
            raise Invalid(f"{sha[:8]} is not on {branch}")
        git(["checkout", "--detach", sha], cwd=clone)
        out = clone / "raw" / f"at-{sha[:12]}" / "entries"
    else:
        git(["checkout", branch], cwd=clone)
        git(["reset", "--hard", f"origin/{branch}"], cwd=clone)
        out = clone / "raw" / "entries"
        pinned = clone / "raw"
        for stale in pinned.glob("at-*"):
            shutil.rmtree(stale, ignore_errors=True)

    run([config.PYTHON_BIN, str(config.PIPELINE_DIR / "ingest.py"),
         "--repo", str(clone), "--out", str(out),
         "--wiki", str(wiki_dir(row["owner"], row["name"]))],
        cwd=clone, timeout=TIMEOUTS["ingest"])

    new, changed = _queue_counts(clone, out)
    head = git(["rev-parse", "HEAD"], cwd=clone).strip()
    update(row["id"], state="ingested", queue_new=new, queue_changed=changed,
           pinned_sha=(check_sha(commit) if commit else None),
           head_sha=head, last_error=None,
           last_used_at=datetime.now(timezone.utc))
    return {"items_seen": new + changed, "items_written": new + changed,
            "queue_new": new, "queue_changed": changed,
            "pinned": bool(commit)}


KINDS = {"code_package", "doc", "runtime_contract", "diagram", "binary_doc",
         "dataset", "meeting_transcript", "chat_thread"}


def units_touched_since(clone: Path, commits: int, kind: str | None = None) -> list[str]:
    """Unit ids covering every path changed in the last N commits.

    "Ingest at this commit" answers *what did it look like then*. This answers
    *what has moved lately* — the question that decides what is worth re-buying.

    The manifest drops each unit's file list (it is bulky), so a changed path is
    matched to the unit with the longest path prefix. Longest wins because units
    nest: src/application and src/application/arps both exist, and a file under
    arps belongs to arps.
    """
    if commits < 1:
        raise Invalid("commits must be >= 1")
    manifest = clone / "raw" / "_manifest.json"
    if not manifest.is_file():
        raise NotCloned("ingest has not run yet")
    try:
        units = json.loads(manifest.read_text())
    except Exception as e:
        raise RuntimeError(f"unreadable manifest: {e}")

    out = git(["diff", "--name-only", f"HEAD~{int(commits)}..HEAD"], cwd=clone)
    changed = [ln.strip() for ln in out.splitlines() if ln.strip()]

    by_len = sorted((u for u in units if not kind or u["kind"] == kind),
                    key=lambda u: len(u["path"]), reverse=True)
    hit = []
    for path in changed:
        for u in by_len:
            p = u["path"]
            if path == p or path.startswith(p.rstrip("/") + "/"):
                if u["id"] not in hit:
                    hit.append(u["id"])
                break
    return hit


def step_absorb(row: dict, limit: int = 5, only: list[str] | None = None,
                kind: str = "code_package", since: int | None = None) -> dict:
    clone = Path(row["clone_path"] or "")
    if not clone.is_dir():
        raise NotCloned(row["id"])
    from . import llm
    try:
        absorb_overrides = llm.absorb_env()
    except llm.NoProvider as e:
        raise Busy(str(e))

    update(row["id"], state="absorbing")
    if kind not in KINDS:
        raise Invalid(f"unknown kind: {kind!r}")
    if since:
        # Scope to what recent commits touched. Intersects with any explicit
        # --only rather than replacing it, so the two compose.
        touched = units_touched_since(clone, since, kind)
        only = [u for u in only if u in touched] if only else touched
        if not only:
            update(row["id"], state="ready")
            return {"items_seen": 0, "items_written": 0,
                    "articles": row["articles"],
                    "tail": f"no {kind} units touched in the last {since} commits"}
    args = [config.PYTHON_BIN, str(config.PIPELINE_DIR / "absorb_runner.py"),
            "--repo", str(clone), "--kind", kind,
            "--wiki", str(row["wiki_root"] or wiki_dir(row["owner"], row["name"])),
            "--limit", str(max(1, min(int(limit), 50)))]
    for uid in (only or []):
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", uid):
            raise Invalid(f"invalid unit id: {uid!r}")
        args += ["--only", uid]

    out = run(args, cwd=clone, timeout=TIMEOUTS["absorb"],
              env_extra=absorb_overrides)
    wiki = Path(row["wiki_root"] or (clone / "wiki"))
    n = len([p for p in wiki.rglob("*.md") if not p.name.startswith("_")])
    update(row["id"], state="ready", articles=n, last_error=None,
           last_used_at=datetime.now(timezone.utc))
    return {"items_seen": limit, "items_written": n, "articles": n,
            "tail": out.strip()[-1000:]}


STEPS = {"clone": step_clone, "graph": step_graph,
         "ingest": step_ingest, "absorb": step_absorb}


def run_step(repo_id: str, step: str, **kwargs) -> dict:
    """Execute one step, recording it as a connector run. Blocking — callers
    put it on a thread."""
    if step not in STEPS:
        raise Invalid(f"unknown step: {step}")
    row = _require(repo_id)
    _claim(repo_id, step)
    run_id = runs.start_run("github", repo_id=repo_id, step=step)
    prev_state = row["state"]
    try:
        result = STEPS[step](row, **kwargs)
        runs.finish_run(run_id, status="ok",
                        items_seen=result.get("items_seen", 0),
                        items_written=result.get("items_written", 0))
        return result
    except Exception as e:
        msg = _redact(str(e))[:2000]
        runs.finish_run(run_id, status="error", error=msg)
        # failed is not terminal: the row keeps its clone and wiki, and the
        # retry button re-runs this step. Recording the state we came from is
        # what makes that safe.
        update(repo_id, state="failed", last_error=f"{step}: {msg}")
        if prev_state in ("ready", "ingested", "graphed", "cloned"):
            update(repo_id, last_error=f"{step}: {msg}")
        raise
    finally:
        _release(repo_id)


def run_sync(repo_id: str) -> dict:
    """The one-button free path. Deliberately stops before absorb."""
    out = {}
    for step in ("clone", "graph", "ingest"):
        out[step] = run_step(repo_id, step)
    return out
