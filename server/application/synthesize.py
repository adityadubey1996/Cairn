"""The full answer pipeline as one SSE stream.

Stages are emitted as they happen — condense, recall, context — so the UI can
show what part of the knowledge base is being read before the first token
arrives. Then the answer streams as {"delta"} frames and the final frame
carries citations + trust + the process record, which is also persisted with
the message. Stage frames are additive: consumers that only know delta/done
keep working.
"""
from __future__ import annotations

import json
from collections import Counter
from urllib.parse import quote

from .. import corpus, llm
from ..db import connect
from ..wikilib import CITE, GRADE
from .assemble import assemble
from .condense import condense
from .framing import system_prompt
from .evidence import hydrate
from .recall.local import recall


def _sse(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _github_fields(root, article_text: str = '') -> dict:
    metadata = corpus.provenance(root, article_text)
    return {k: metadata[k] for k in ('github', 'ref') if k in metadata}


def _citations(answer: str, context: list[dict]) -> list[dict]:
    """Unique [path@sha] cited in the answer, with the repo each came from."""
    out, seen = [], set()
    for path, sha in CITE.findall(answer):
        if (path, sha) in seen:
            continue
        seen.add((path, sha))
        if path.startswith("sources/"):
            # The authenticated reader resolves the cited local revision,
            # with optional object-store fallback, at click time.
            out.append({"path": path, "sha": sha, "repo": "ai-brain",
                        "href": f"/api/sources/view?path={quote(path)}&etag={sha}"})
            continue
        article = next((a for a in context if f"{path}@{sha}" in a["text"]), None)
        if not article:
            out.append({"path": path, "sha": sha, "repo": None})
            continue
        root = article["root"]
        fields = _github_fields(root, article['text'])
        entry = {"path": path, "sha": sha, "repo": root.parent.name, **fields}
        if fields.get('github') and fields.get('ref'):
            entry['href'] = (f"https://github.com/{fields['github']}/blob/"
                             f"{quote(fields['ref'], safe='')}/{quote(path.lstrip('/'), safe='/')}")
        out.append(entry)
    return out


def _trust(context: list[dict]) -> dict:
    grades = Counter()
    for a in context:
        grades.update(GRADE.findall(a["text"]))
    heads = {}
    for a in context:
        metadata = corpus.provenance(a['root'], a['text'])
        name = metadata.get('github') or metadata['repo']
        ref = metadata['head'][:8]
        # A root can contain articles compiled at several pinned revisions.
        if name in heads and heads[name] != ref:
            heads[name] = ', '.join(sorted(set(heads[name].split(', ')) | {ref}))
        else:
            heads[name] = ref
    return {"grades": dict(grades), "heads": heads,
            "articles": [a["title"] for a in context]}


def run_pipeline(conversation_id, turns: list[dict], content: str, project_id: str | None = None):
    process: dict = {}

    query = content
    if turns:
        yield _sse({"stage": "condensing"})
        query = condense(turns, content)
        if query != content:
            process["query"] = query
            yield _sse({"stage": "condensed", "query": query})

    yield _sse({"stage": "searching"})
    seeds = recall(query, k=4, project_id=project_id)
    process["hits"] = [{"path": rel, "score": round(score, 4)}
                       for _root, rel, score in seeds]
    yield _sse({"stage": "recall", "hits": process["hits"]})

    context = hydrate(assemble(query, seeds=seeds), query)
    process["articles"] = [{"title": a["title"], "path": a["rel"],
                            "repo": a["root"].parent.name} for a in context]
    yield _sse({"stage": "context", "articles": process["articles"]})

    messages = ([{"role": "system", "content": system_prompt(context)}]
                + turns[-6:]
                + [{"role": "user", "content": content}])
    yield _sse({"stage": "generating"})

    chunks = []
    try:
        for delta in llm.stream(messages):
            chunks.append(delta)
            yield _sse({"delta": delta})
    except Exception as e:
        yield _sse({"error": f"synthesis failed: {e}"})

    answer = "".join(chunks)
    citations = _citations(answer, context)
    trust = _trust(context)
    if answer:
        with connect() as c:
            c.execute(
                "INSERT INTO brain_messages (conversation_id, role, content, "
                "citations) VALUES (%s, 'assistant', %s, %s)",
                (conversation_id, answer,
                 json.dumps({"citations": citations, "trust": trust,
                             "process": process})))
    yield _sse({"done": True, "citations": citations, "trust": trust,
                "process": process})
