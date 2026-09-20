"""Wiki browsing for the UI — the article graph and the articles themselves.
The brain is the access layer for people who have no repo access, so this goes
through the same auth gate as chat.

Mostly read-only. The one write is deleting an article, which has to undo three
records rather than one — see remove_article."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from .. import config, corpus, index, ingest_units, sources
from ..application.assemble import _scan
from ..application.synthesize import _citations
from ..auth import current_user
from ..wikilib import ARTICLE_CITE, fm_field, frontmatter, parse_grades

router = APIRouter(prefix="/api/wiki")

_SOURCES_KIND = {"gdrive", "gchat", "links", "whatsapp", "linkedin"}


def _kind_of_path(path: str) -> str:
    """sources/<kind>/... for a fed file; anything else is a tracked repo's
    own code, browsed through the wiki rather than the Sources table."""
    if path.startswith("sources/"):
        kind = path.split("/", 2)[1]
        if kind in _SOURCES_KIND:
            return kind
    return "github"


@router.get("/sync-status")
def sync_status(project_id: str | None = None, _email: str = Depends(current_user)):
    """Per-root sync state for the Connectors screen: repo name, whether the
    on-disk fingerprint has moved since the last index build, when it was
    last indexed, at which commit, and how many articles it holds."""
    state = corpus.load_state()
    out = []
    for root in corpus.roots(project_id):
        s = state.get(str(root), {})
        out.append({
            "repo": root.parent.name,
            "path": str(root),
            "exists": root.is_dir(),
            "indexed": bool(s),
            "current": bool(s) and corpus.fingerprint(root) == s.get("fingerprint"),
            "head": s.get("head"),
            "articles": s.get("articles"),
            "indexed_at": s.get("indexed_at"),
        })
    return out


@router.post("/sync")
def sync_now(_email: str = Depends(current_user)):
    """Manual 'sync now' from the UI — same corpus.sync() the boot/15-min
    loop and the CI webhook both call, just gated by a user session instead
    of SYNC_TOKEN."""
    return corpus.sync()


@router.get("/graph")
def graph(project_id: str | None = None, _email: str = Depends(current_user)):
    """All articles as nodes, all resolvable [[wikilinks]] as edges.
    Wikilinks are a per-repo namespace, so edges never cross roots."""
    nodes, edges = [], []
    for root in corpus.roots(project_id):
        if not root.is_dir():
            continue
        repo = root.parent.name
        titles, links, texts = _scan(root)
        for rel, (title, text) in texts.items():
            fm, _ = frontmatter(text)
            nodes.append({
                "id": f"{repo}/{rel}", "repo": repo, "path": rel,
                "title": title, "type": fm_field(fm, "type") or "?",
                "stale": fm_field(fm, "stale") == "true",
                "grades": fm_field(fm, "grades"),
            })
        for rel, outs in links.items():
            for target in (titles.get(t) for t in outs):
                if target and target != rel:
                    edges.append({"source": f"{repo}/{rel}",
                                  "target": f"{repo}/{target}"})
    return {"nodes": nodes, "edges": edges}


def _related_title_map(project_id: str | None) -> dict[str, tuple[str, str]]:
    """title -> (repo, rel), across every root this project can see — a
    related article can live in a different repo than the one being read."""
    out = {}
    for root in corpus.roots(project_id):
        if not root.is_dir():
            continue
        repo = root.parent.name
        titles, _links, _texts = _scan(root)
        for title, rel in titles.items():
            out.setdefault(title, (repo, rel))
    return out


@router.get("/article")
def article(repo: str, path: str, project_id: str | None = None,
           _email: str = Depends(current_user)):
    """Full reader payload: web2's shape (type/grades/stale/sources/related/
    citations), not V1's raw {repo,path,text} — V1's own web/ never called
    this with project_id and still gets that same richer shape back, since
    every field it used to read (repo/path/text/head/github/ref) is still
    present alongside the new ones."""
    for root in corpus.roots(project_id):
        if root.parent.name != repo:
            continue
        p = (root / path).resolve()
        # file-serving endpoint: never step outside the wiki root
        if (not str(p).startswith(str(root.resolve()) + "/")
                or p.suffix != ".md" or p.name.startswith("_")):
            raise HTTPException(404, "no such article")
        if not p.is_file():
            raise HTTPException(404, "no such article")

        text = p.read_text(encoding="utf-8", errors="replace")
        fm, body = frontmatter(text)
        metadata = corpus.provenance(root, text)
        head = metadata['head']
        resolved = ((metadata['github'], metadata['ref'])
                    if metadata.get('github') and metadata.get('ref') else None)

        # Inline citations: an article's own body cites [grade: path@sha],
        # unlike a chat answer's bare [path@sha] — both match the same
        # underlying path@sha, which is all _citations() (shared with chat)
        # actually keys on.
        raw_citations = _citations(body, context=[{
            "root": root, "rel": path,
            "title": fm_field(fm, "title") or p.stem, "text": text}])
        fed = sources.get_by_paths([c["path"] for c in raw_citations
                                    if c["path"].startswith("sources/")])
        web_citations, by_key = [], {}
        for c in raw_citations:
            key = f"{c['path']}@{c['sha']}"
            src = fed.get(c["path"])
            link_kind = _kind_of_path(c["path"]) == "links"
            entry = {
                "id": key,
                "label": src["name"] if src and src["type"] == "link"
                        else f"{c['path'].split('/')[-1]}@{c['sha'][:7]}",
                "type": "link" if (src and src["type"] == "link") or link_kind else "file",
                "url": c.get("href") or "#",
            }
            web_citations.append(entry)
            by_key[key] = entry

        def _linkify(m):
            key = f"{m.group(2)}@{m.group(3)}"
            entry = by_key.get(key)
            return f"[{entry['label']}](cite:{key})" if entry else m.group(0)

        linked_body = ARTICLE_CITE.sub(_linkify, body)

        # The sources panel: everything frontmatter says this article was
        # built on, fed-source rows where one exists (bytes/scraped-at/url),
        # a bare-bones stand-in (a cited repo code file, not a fed source)
        # otherwise.
        raw_sources = fm_field(fm, "sources")
        source_keys = json.loads(raw_sources) if raw_sources else []
        source_paths = [k.rsplit("@", 1)[0] for k in source_keys]
        fed_panel = sources.get_by_paths(source_paths)
        panel = []
        for key in source_keys:
            spath, _, ssha = key.rpartition("@")
            row = fed_panel.get(spath)
            kind = _kind_of_path(spath)
            panel.append(row or {
                "id": key, "kind": kind, "type": "link" if kind == "links" else "file",
                "name": spath.split("/")[-1], "path": spath, "url": None,
                "detail": repo, "bytes": None, "sha": ssha, "scraped_at": None,
            })

        related_map = _related_title_map(project_id)
        related = []
        for raw in json.loads(fm_field(fm, "related") or "[]"):
            title = raw.strip("[]").strip()
            hit = related_map.get(title)
            if hit:
                related.append({"path": f"{hit[0]}/{hit[1]}", "title": title})

        payload = {
            "repo": repo, "path": f"{repo}/{path}",
            "title": fm_field(fm, "title") or p.stem,
            "type": fm_field(fm, "type") or "?",
            "stale": fm_field(fm, "stale") == "true",
            "grades": parse_grades(fm_field(fm, "grades")),
            "body": linked_body, "citations": web_citations,
            "sources": panel, "related": related,
            "text": text, "head": head[:8] if head else "?",
        }
        if resolved:
            payload["github"], payload["ref"] = resolved
        return payload
    raise HTTPException(404, "no such article")


@router.delete("/article")
def remove_article(repo: str, path: str, project_id: str,
                   _email: str = Depends(current_user)):
    """Delete an article and every record that remembers it was written.

    Three places, not one. The file is the obvious part; `_absorb_log.json` and
    the article's own `unit:` frontmatter are what ingest reads to decide a unit
    is already absorbed (pipeline/ingest.py:673, :786). Remove only the file and
    the unit is never queued again — the article is gone permanently and no
    write-up brings it back. Deleting the frontmatter happens for free with the
    file; the log and the unit row are cleared here.

    The source itself is untouched: this un-writes the article, it does not
    un-scrape the file.
    """
    for root in corpus.roots(project_id):
        if root.parent.name != repo:
            continue
        art = (root / path).resolve()
        # Same guard as the reader above: never step outside the wiki root.
        if (not str(art).startswith(str(root.resolve()) + "/")
                or art.suffix != ".md" or art.name.startswith("_")):
            raise HTTPException(404, "no such article")
        if not art.is_file():
            raise HTTPException(404, "no such article")

        unit = fm_field(frontmatter(art.read_text(encoding="utf-8"))[0], "unit")
        art.unlink()

        log = root / "_absorb_log.json"
        if unit and log.is_file():
            try:
                kept = [u for u in json.loads(log.read_text()) if u != unit]
                log.write_text(json.dumps(kept, indent=2))
            except (ValueError, OSError):
                # A corrupt or unreadable log is not worth failing the delete
                # over — the unit row below is what the product reads.
                pass

        if unit:
            ingest_units.mark(unit, project_id, "pending", error=None)
        return {"deleted": path, "unit": unit, "requeued": bool(unit)}

    raise HTTPException(404, "no such article")
