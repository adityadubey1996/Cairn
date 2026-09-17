"""The recall port. One function: query -> ranked article hits across all
roots. Swap this module to change recall backends (a RAGFlow adapter would
implement the same signature)."""
from __future__ import annotations

from pathlib import Path

from ... import config, corpus, index
from ...wikilib import embed


def recall(query: str, k: int = 4, project_id: str | None = None) -> list[tuple[Path, str, float]]:
    """Return [(root, relpath, score)], best first, across all wiki roots.

    corpus.roots(), not config.WIKI_ROOTS: generated wikis are rows in
    brain_repos, not env. Reading the env list alone made recall return nothing
    once WIKI_ROOTS was emptied — and an empty recall does not fail loudly, it
    just lets the model answer from its own knowledge.

    project_id scopes this to one project's roots (V2's hard-isolation
    premise); None (V1's only caller) keeps the original cross-root behaviour.
    """
    # Embeddings are optional, exactly as index.build() already treats them:
    # no OLLAMA_BASE, or one that will not answer, degrades to FTS-only rather
    # than taking chat down. In a container the default localhost:11434 is the
    # container itself, so this is the normal case, not the edge case.
    try:
        qvec = embed(query, config.OLLAMA_BASE) if config.OLLAMA_BASE else []
    except Exception:
        qvec = []
    hits = []
    for root in corpus.roots(project_id):
        hits += [(root, rel, score) for rel, score in index.hybrid(root, query, qvec)]
    hits.sort(key=lambda t: -t[2])
    return hits[:k]
