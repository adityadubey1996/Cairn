"""Sync wiki roots into the local index. State-based and idempotent: fingerprint
the root (path/mtime/size of every .md); if it moved since the last build,
reindex. Triggered by boot, a 15-minute loop, and POST /internal/sync — the
webhook is an accelerator; the loop is the guarantee.

Local dev points WIKI_ROOTS straight at working trees. A server deploy points
it at sparse checkouts instead, one per repo, created with per-repo READ-ONLY
deploy keys:

    git clone --filter=blob:none --sparse git@github.com:your-org/<repo>.git
    cd <repo> && git sparse-checkout set wiki

and a timer runs `git pull --ff-only` in each checkout; the 15-minute loop
then notices the moved files. Git is the only storage of knowledge — every
index this module writes is a disposable cache.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from pathlib import Path

from . import config, index

_lock = threading.Lock()
STATE = config.VAR / "sync_state.json"


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.md")):
        st = p.stat()
        h.update(f"{p.relative_to(root)}|{st.st_mtime_ns}|{st.st_size}\n".encode())
    return h.hexdigest()


def load_state() -> dict:
    """Last-known sync state per root, for the Connectors/health screen."""
    return json.loads(STATE.read_text()) if STATE.is_file() else {}


def _head(root: Path) -> str:
    out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else "worktree"


def roots(project_id: str | None = None) -> list[Path]:
    """Env-configured roots plus the wiki of every tracked repo that has one.

    WIKI_ROOTS stays for hand-managed trees; generated wikis are rows, not env.
    Degrades to the env list if the database is unreachable — a search index
    that is merely incomplete beats a service that will not boot.

    project_id, when given, scopes this to one project: only the repos tracked
    under that project, plus WIKI_ROOTS (the one shared, non-github wiki) —
    but ONLY for the default project. Splitting that shared tree per project
    would mean rewriting where every non-github feeder writes, which Batch 6
    deliberately did not do (see the handover note); until then it honestly
    belongs to one project rather than leaking into every project's isolation
    boundary. None (the internal reindex loop's own use) still means "every
    root", matching this function's pre-V2 behaviour.
    """
    out = []
    scope_shared = project_id is None
    if project_id is not None:
        try:
            from . import projects
            scope_shared = project_id == projects.ensure_default()
        except Exception:
            pass
    if scope_shared:
        out = list(config.WIKI_ROOTS)
    try:
        from . import repos
        seen = {p.resolve() for p in out if p.exists()}
        for r in repos.list_repos():
            if not r["wiki_root"]:
                continue
            if project_id is not None and r["project_id"] != project_id:
                continue
            p = Path(r["wiki_root"])
            if p.is_dir() and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append(p)
    except Exception:
        pass
    return out


def sync(force: bool = False) -> list[dict]:
    """Reindex every root whose files moved. Returns per-root status."""
    with _lock:
        config.VAR.mkdir(exist_ok=True)
        state = load_state()
        report = []
        live = roots()
        # A root that vanished must lose its state, or the screen keeps
        # rendering the last count it ever saw. It reported "some-repo ·
        # 51 articles" for a directory that had been deleted.
        for gone in [k for k in state if Path(k) not in live or not Path(k).is_dir()]:
            state.pop(gone, None)
            report.append({"root": gone, "status": "dropped"})

        for root in live:
            key = str(root)
            if not root.is_dir():
                report.append({"root": key, "status": "missing"})
                state.pop(key, None)
                continue
            fp = fingerprint(root)
            if (not force and state.get(key, {}).get("fingerprint") == fp
                    and index.db_path(root).is_file()):
                report.append({"root": key, "status": "unchanged"})
                continue
            head = _head(root)
            n = index.build(root, head)
            state[key] = {"fingerprint": fp, "head": head,
                          "articles": n, "indexed_at": int(time.time())}
            report.append({"root": key, "status": "reindexed", "articles": n})
        STATE.write_text(json.dumps(state, indent=1))
        return report
