"""GET /api/search?q= — raw recall + expansion, for debugging retrieval
without burning a Groq call."""
from fastapi import APIRouter, Depends

from .. import sources
from ..application.assemble import assemble
from ..application.recall.local import recall
from ..auth import current_user
from ..pipeline.search import search_parts

router = APIRouter()


@router.get("/api/search")
def search(q: str, k: int = 4, _email: str = Depends(current_user)):
    return {
        "query": q,
        "recall": [{"root": str(root), "path": rel, "score": round(score, 4)}
                   for root, rel, score in recall(q, k)],
        "context": [{"root": str(a["root"]), "path": a["rel"],
                     "title": a["title"], "chars": len(a["text"])}
                    for a in assemble(q, k)],
    }


@router.get("/api/search/find")
def find(project_id: str, q: str, kind: str | None = None,
         _email: str = Depends(current_user)):
    """Chat's Search mode — title/snippet/connector/scraped-at over fed
    sources, free and instant. A sibling of /api/search, which stays the
    retrieval-debug shape (root/path/score) some tooling already depends on.

    `kind` scopes it to one connector: Connect > Sources searches inside
    whichever chip is active, and the Files screen searches inside uploads."""
    return {"query": q, "rows": sources.find(project_id, q, kind=kind)}


@router.get("/api/search/parts")
def search_parts_route(q: str, project_id: str = "default", limit: int = 10,
                       _email: str = Depends(current_user)):
    """Tier-1 Search over ingested parts. Free; no model key involved."""
    return {"hits": search_parts(q, project_id, limit)}
