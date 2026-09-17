"""Build the model's context: recall seeds, expand ONE hop over wikilinks,
load FULL canonical articles from disk (never an index copy), rank into a
token budget.

Expansion is the whole point: the economics article that completes an Arps
answer is one [[wikilink]] away from the Arps article, not similar to the
query — similarity alone misses it. Ranking into the budget: seeds by
similarity, then outbound links of the top seed, then backlinks.
"""
from __future__ import annotations

from pathlib import Path

from ..wikilib import WIKILINK, fm_field, frontmatter
from .recall.local import recall

BUDGET_CHARS = 40_000  # ~10k tokens


def _scan(root: Path):
    """Title map + wikilink set + text for every article under a root.
    # ponytail: full rescan per request — tens of articles, milliseconds.
    # Cache on corpus fingerprint when a corpus outgrows that."""
    titles, links, texts = {}, {}, {}
    for p in sorted(root.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        rel = str(p.relative_to(root))
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = frontmatter(text)
        title = fm_field(fm, "title") or p.stem
        titles[title] = rel
        links[rel] = {t.strip() for t in WIKILINK.findall(text)}
        texts[rel] = (title, text)
    return titles, links, texts


def assemble(query: str, k: int = 4, seeds=None, project_id: str | None = None) -> list[dict]:
    """Return context articles [{root, rel, title, text}], best first,
    within budget. Pass precomputed `seeds` to avoid a second recall when the
    caller already ran one (the chat pipeline reports hits as a stage)."""
    if seeds is None:
        seeds = recall(query, k, project_id)
    scans = {root: _scan(root) for root in {r for r, _, _ in seeds}}

    ordered, seen = [], set()

    def add(root: Path, rel: str):
        if (str(root), rel) not in seen:
            seen.add((str(root), rel))
            ordered.append((root, rel))

    for root, rel, _ in seeds:
        add(root, rel)
    # Per-seed neighborhoods, best seed first: the top seed's backlinks are
    # better context than the fourth seed's outbound links. Ranking them after
    # ALL outbound once starved the most relevant backlink out of the budget.
    for root, seed_rel, _ in seeds:
        titles, links, texts = scans[root]
        for target in (titles.get(t) for t in links.get(seed_rel, ())):
            if target:
                add(root, target)
        # Inbound links are uncurated (generated articles pad their `related`
        # lists), so admit smallest-first — one bloated linker must not starve
        # the seed's whole backlink bucket out of the budget.
        backlinks = [rel for rel, outs in links.items()
                     if any(titles.get(t) == seed_rel for t in outs)]
        for rel in sorted(backlinks, key=lambda r: len(texts[r][1])):
            add(root, rel)

    out, used = [], 0
    for root, rel in ordered:
        title, text = scans[root][2][rel]
        room = BUDGET_CHARS - used
        if out and len(text) > room:
            if room < 4000:
                continue  # a smaller lower-ranked article may still fit
            # an oversized article at this rank beats skipping it for three
            # weaker ones — take its head
            text = text[:room] + "\n\n[article truncated to fit context budget]"
        out.append({"root": root, "rel": rel, "title": title, "text": text})
        used += len(text)
    return out
