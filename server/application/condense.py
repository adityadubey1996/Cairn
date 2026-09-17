"""Turn-2+ messages lean on chat history ("and what about gas?"). One small
provider call rewrites the latest message into a standalone retrieval query.
Condensation is an optimization, never a gate — any failure falls back to the
raw message."""
from __future__ import annotations

from .. import llm

PROMPT = ("Rewrite the user's last message as ONE standalone search query, "
          "resolving pronouns and references from the conversation so far. "
          "Output only the query text, nothing else.")


def condense(history: list[dict], message: str) -> str:
    if not history:
        return message
    msgs = [{"role": "system", "content": PROMPT}]
    msgs += history[-6:]
    msgs.append({"role": "user", "content": message})
    try:
        out = llm.complete(msgs, temperature=0.0)
        return out.strip().strip('"') or message
    except Exception:
        return message
