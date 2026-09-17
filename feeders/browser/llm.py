"""LLM factory for the navigation-fallback agent (browser-use + Groq)."""
from __future__ import annotations

import logging
from typing import Any

from server import config

log = logging.getLogger("cairn.steel")


def build_llm() -> Any:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is required for the browser fallback agent")
    try:
        from browser_use import ChatGroq
        return ChatGroq(model=config.BROWSER_LLM_MODEL, api_key=config.GROQ_API_KEY)
    except ImportError:
        pass
    from browser_use import ChatOpenAI
    return ChatOpenAI(api_key=config.GROQ_API_KEY,
                      base_url="https://api.groq.com/openai/v1",
                      model=config.BROWSER_LLM_MODEL)
