"""Wiki parsing + the embedding call, shared by corpus, index and chat.

Lifted from the canonical wiki tooling
(.cursor/skills/wiki/scripts/validate_wiki.py and absorb_runner.py) so the
brain runs standalone — a server deploy has only sparse wiki checkouts, not
those repos' script trees.
"""
from __future__ import annotations

import json
import re
import urllib.request

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
CITE = re.compile(r"([\w./\-]+\.\w+)@([0-9a-f]{7,64})(?![0-9a-f])")
GRADE = re.compile(r"\[(verified|code|doc|conflict|gap)[:\]]")
# An article body's own citations are grade-bracketed — [doc: path@sha] — unlike
# a chat answer's bare [path@sha] (framing.py's prompt asks the model for the
# simpler form). Both match CITE; this one also captures which grade.
ARTICLE_CITE = re.compile(
    r"\[(verified|code|doc|conflict|gap):\s*([\w./\-]+\.\w+)@([0-9a-f]{7,64})\]")


def parse_grades(raw: str) -> dict:
    """`{verified: 0, code: 0, doc: 9, conflict: 0, gap: 2}` — absorb writes a
    Python-dict literal (unquoted keys), not JSON. Parsed with a regex rather
    than pulling in a YAML dependency for one frontmatter line."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+):\s*(-?\d+)", raw)}

def frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Empty frontmatter if absent."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    return (text[3:end], text[end + 4:]) if end != -1 else ("", text)


def fm_field(fm: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", fm, re.M)
    return m.group(1).strip().strip('"') if m else ""



def embed(text: str, base: str, model: str = "nomic-embed-text") -> list[float]:
    # Ollama's /api/embeddings 500s past the model's num_ctx (2048 by default);
    # one vector per article is a recall signal, not the content store, so the
    # head of the article is enough. FTS still indexes the full text.
    with urllib.request.urlopen(
            urllib.request.Request(
                base + "/api/embeddings",
                data=json.dumps({"model": model, "prompt": text[:6000]}).encode(),
                headers={"Content-Type": "application/json"}),
            timeout=120) as r:
        return json.loads(r.read())["embedding"]
