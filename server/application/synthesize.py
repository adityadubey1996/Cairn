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

from .. import index, llm
from ..db import connect
from ..gitmeta import github_blob_url, github_slug_and_ref
from ..wikilib import CITE, GRADE
from .assemble import assemble
from .condense import condense
from .framing import system_prompt
from .recall.local import recall


def _sse(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _github_fields(root) -> dict:
    """Public GitHub coordinates — a checkout's folder name need not match the
    remote slug, so the slug is resolved from the remote, never assumed."""
    head = index.head_of(root)
    preferred = head if head and head not in ("?",) else None
    resolved = github_slug_and_ref(root.parent, preferred)
    if not resolved:
        return {}
    slug, ref = resolved
    return {"github": slug, "ref": ref}


def _citations(answer: str, context: list[dict]) -> list[dict]:
    """Unique [path@sha] cited in the answer, with the repo each came from."""
    out, seen = [], set()
    for path, sha in CITE.findall(answer):
        if (path, sha) in seen:
            continue
        seen.add((path, sha))
        if path.startswith("sources/"):
            # sources/ are S3-only: the href goes through the authed view
            # route, which mints a presigned URL at click time. Built even
            # without a matching context article — the path alone suffices.
            out.append({"path": path, "sha": sha, "repo": "ai-brain",
                        "href": f"/api/sources/view?path={quote(path)}&etag={sha}"})
            continue
        article = next((a for a in context if f"{path}@{sha}" in a["text"]), None)
        if not article:
            out.append({"path": path, "sha": sha, "repo": None})
            continue
        root = article["root"]
        fields = _github_fields(root)
        entry = {"path": path, "sha": sha, "repo": root.parent.name, **fields}
        href = github_blob_url(root.parent, path, fields.get("ref"))
        if href:
            entry["href"] = href
        out.append(entry)
    return out


def _trust(context: list[dict]) -> dict:
    grades = Counter()
    for a in context:
        grades.update(GRADE.findall(a["text"]))
    heads = {a["root"].parent.name: index.head_of(a["root"])[:8]
             for a in context}
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

    context = assemble(query, seeds=seeds)
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
