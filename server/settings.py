"""BYOK provider + embeddings settings. Single global row each
(brain_settings) — this is a single-user tool, so these are not per-project.

The provider registry and every real call live in server/llm.py; this module is
only the stored preference and how it is shown. "Test key" makes a real,
minimal call to the provider and is never faked.

A key in .env needs none of this: llm.resolve() falls back to the environment,
so an install with no saved row still answers. What that resolved to is
reported as `effective`, because "which provider am I actually using" is
otherwise unanswerable from the screen.
"""
from __future__ import annotations

import json

from . import llm
from .db import connect

_DEFAULT_PROVIDER = {"preset": "claude"}
_DEFAULT_EMBEDDINGS = {"preset": "ollama", "model": "nomic-embed-text"}


def _mask(key: str) -> str:
    key = key or ""
    return f"{key[:6]}···{key[-4:]}" if len(key) > 10 else "···"


def _row(id_: str, default: dict) -> dict:
    with connect() as c:
        r = c.execute("SELECT value, updated_at FROM brain_settings WHERE id = %s",
                      (id_,)).fetchone()
    if not r:
        return {**default, "verifiedAt": None}
    return {**default, **r["value"], "verifiedAt": r["value"].get("verifiedAt")}


def get_settings() -> dict:
    provider = _row("provider", _DEFAULT_PROVIDER)
    key = provider.pop("key", "")
    if key:
        provider["keyMasked"] = _mask(key)
    embeddings = _row("embeddings", _DEFAULT_EMBEDDINGS)
    embeddings.pop("key", None)
    if embeddings.get("preset", "ollama") == "ollama":
        embeddings["reachable"] = llm.ollama_status()["reachable"]

    # What a question would actually use right now, keys never included.
    try:
        resolved = llm.resolve()
        effective = {"preset": resolved["preset"], "model": resolved["model"],
                     "source": resolved["source"]}
    except llm.NoProvider as e:
        effective = {"preset": None, "model": None, "detail": str(e)}

    return {"provider": provider, "embeddings": embeddings,
            "effective": effective, "ollama": llm.ollama_status(),
            "presets": [{"id": p.id, "label": p.label, "needsKey": not p.local,
                         "keyEnv": p.key_env, "local": p.local,
                         "defaultModel": p.model}
                        for p in llm.PRESETS.values()]}


def save(payload: dict) -> dict:
    with connect() as c:
        for id_ in ("provider", "embeddings"):
            if id_ in payload:
                c.execute(
                    "INSERT INTO brain_settings (id, value) VALUES (%s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "value = brain_settings.value || EXCLUDED.value, updated_at = now()",
                    (id_, json.dumps(payload[id_])))
    return get_settings()


def _mark_verified(preset: str) -> None:
    from datetime import datetime, timezone
    with connect() as c:
        c.execute(
            "UPDATE brain_settings SET value = value || %s::jsonb, updated_at = now() "
            "WHERE id = 'provider' AND value->>'preset' = %s",
            (json.dumps({"verifiedAt": datetime.now(timezone.utc).isoformat()}), preset))


def test_provider(cfg: dict) -> dict:
    result = llm.test(cfg)
    if result["ok"]:
        try:
            _mark_verified(cfg.get("preset", "claude"))
        except Exception:
            pass  # a green test must not fail on bookkeeping
    return result
