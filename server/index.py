"""One SQLite file per wiki root: FTS5 (BM25) + one nomic vector per article.

Built to a temp file and atomically swapped in with os.replace, so readers
never see a half-built index. Hybrid recall = reciprocal-rank fusion of BM25
ranks and cosine ranks — BM25 is what finds exact symbol names
(RoyaltyVault, submitEmissionReport) in a code-heavy corpus; vectors catch
the paraphrases. RRF scores are rank-based, so they merge cleanly across
multiple root databases.
"""
from __future__ import annotations

import os
import re
import sqlite3
import struct
from pathlib import Path

from . import config
from .wikilib import embed, fm_field, frontmatter

RRF_K = 60


def db_path(root: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", str(root).lower()).strip("-")
    return config.VAR / f"index-{slug[-80:]}.sqlite3"


def articles(root: Path):
    """Yield (relpath, title, full_text) for every article under a root."""
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = frontmatter(text)
        yield str(p.relative_to(root)), fm_field(fm, "title") or p.stem, text


def build(root: Path, head: str) -> int:
    config.VAR.mkdir(exist_ok=True)
    final = db_path(root)
    tmp = final.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    db.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE articles(path TEXT PRIMARY KEY, title TEXT, body TEXT);
        CREATE VIRTUAL TABLE fts USING fts5(path UNINDEXED, title, body);
        CREATE TABLE embeddings(path TEXT PRIMARY KEY, vec BLOB);
    """)
    n = 0
    for rel, title, text in articles(root):
        db.execute("INSERT INTO articles VALUES (?,?,?)", (rel, title, text))
        db.execute("INSERT INTO fts VALUES (?,?,?)", (rel, title, text))
        try:
            vec = embed(f"{title}\n\n{text}", config.OLLAMA_BASE)
            db.execute("INSERT INTO embeddings VALUES (?,?)",
                       (rel, struct.pack(f"{len(vec)}f", *vec)))
        except Exception:
            # absorb may be writing this corpus while we index it; one bad
            # article stays findable via FTS instead of sinking the whole build
            pass
        n += 1
    db.execute("INSERT INTO meta VALUES ('head',?)", (head,))
    db.commit()
    db.close()
    os.replace(tmp, final)
    return n


def head_of(root: Path) -> str:
    final = db_path(root)
    if not final.is_file():
        return "?"
    db = sqlite3.connect(final)
    row = db.execute("SELECT value FROM meta WHERE key='head'").fetchone()
    db.close()
    return row[0] if row else "?"


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def hybrid(root: Path, query: str, qvec: list[float],
           limit: int = 20) -> list[tuple[str, float]]:
    """(relpath, rrf_score) for one root, best first."""
    final = db_path(root)
    if not final.is_file():
        return []
    db = sqlite3.connect(final)
    # Quote each term: bare tokens that collide with FTS5 syntax (NEAR, -, .)
    # would otherwise turn a user question into a parse error.
    words = re.findall(r"\w+", query)
    bm25 = []
    if words:
        bm25 = [r[0] for r in db.execute(
            "SELECT path FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?",
            (" OR ".join(f'"{w}"' for w in words), limit))]
    # No query vector (embeddings unavailable) means FTS-only. Scoring every
    # row 0.0 instead would hand each one an equal RRF contribution in
    # arbitrary db order, which is worse than not voting at all.
    sims = [] if not qvec else sorted(
        ((rel, _cosine(qvec, struct.unpack(f"{len(blob) // 4}f", blob)))
         for rel, blob in db.execute("SELECT path, vec FROM embeddings")),
        key=lambda t: -t[1])[:limit]
    db.close()
    rrf: dict[str, float] = {}
    for rank, rel in enumerate(bm25):
        rrf[rel] = rrf.get(rel, 0) + 1 / (RRF_K + rank + 1)
    for rank, (rel, _s) in enumerate(sims):
        rrf[rel] = rrf.get(rel, 0) + 1 / (RRF_K + rank + 1)
    return sorted(rrf.items(), key=lambda t: -t[1])
