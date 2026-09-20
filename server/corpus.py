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
then notices the moved files. Generated project/repository wikis can also live
outside Git. Wiki files and source revisions are durable; this module's search
indexes are rebuildable caches.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    return provenance(root)['head']


def provenance(root: Path, article_text: str = '') -> dict:
    """Bind an article to its source repository, never its storage ancestor.

    Generated wikis live outside their clone, often underneath this app's own
    checkout. Git's upward discovery there would silently attribute every
    article to the app. Tracked repository metadata is authoritative; a
    hand-managed wiki can use Git only when its direct parent is a checkout.
    """
    from .gitmeta import github_slug_and_ref
    from .wikilib import fm_field, frontmatter
    root = Path(root).resolve()
    fm, _ = frontmatter(article_text)
    compiled = fm_field(fm, 'built_from_commit')
    compiled = compiled if re.fullmatch(r'[0-9a-f]{7,40}', compiled) else ''
    row = None
    try:
        from . import repos
        row = next((r for r in repos.list_repos() if r.get('wiki_root') and
                    Path(r['wiki_root']).resolve() == root), None)
    except Exception:
        pass
    if row:
        head = compiled or row.get('pinned_sha') or row.get('head_sha') or '?'
        result = {'repo': row['name'], 'head': head}
        remote = re.fullmatch(r'https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?', row.get('url') or '')
        resolved = None
        if remote:
            resolved = (f'{remote.group(1)}/{remote.group(2)}', head)
        elif row.get('clone_path') and (Path(row['clone_path']) / '.git').exists():
            resolved = github_slug_and_ref(Path(row['clone_path']), head)
        if resolved and head != '?':
            result.update(github=resolved[0], ref=head)
        return result
    result = {'repo': root.parent.name, 'head': 'local'}
    if (root.parent / '.git').exists():
        out = subprocess.run(['git', '-C', str(root.parent), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True)
        head = compiled or (out.stdout.strip() if out.returncode == 0 else '')
        if head:
            result['head'] = head
        resolved = github_slug_and_ref(root.parent, head or None)
        if resolved:
            result.update(github=resolved[0], ref=resolved[1])
    return result


def roots(project_id: str | None = None) -> list[Path]:
    """Env-configured roots plus the wiki of every tracked repo that has one.

    WIKI_ROOTS stays for hand-managed trees; generated wikis are rows, not env.
    Degrades to the env list if the database is unreachable — a search index
    that is merely incomplete beats a service that will not boot.

    project_id scopes generated project wikis and tracked repository wikis.
    Legacy WIKI_ROOTS belongs only to the default project. None (used by the
    internal reindex loop) means every project's roots.
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
        from . import projects
        project_ids = [project_id] if project_id else [r['id'] for r in projects.list_projects()]
        for pid in project_ids:
            p = projects.wiki_root(pid)
            if p.is_dir() and p not in out:
                out.append(p)
    except Exception:
        pass
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
            head = _head(root)
            if (not force and state.get(key, {}).get("fingerprint") == fp
                    and state.get(key, {}).get('head') == head
                    and index.db_path(root).is_file()):
                report.append({"root": key, "status": "unchanged"})
                continue
            n = index.build(root, head)
            state[key] = {"fingerprint": fp, "head": head,
                          "articles": n, "indexed_at": int(time.time())}
            report.append({"root": key, "status": "reindexed", "articles": n})
        STATE.write_text(json.dumps(state, indent=1))
        return report
