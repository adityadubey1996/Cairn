"""Per-connection secrets, kept out of the row the Connections screen lists.

brain_connector_connections.config is read to build every connection card, so
a token there is one careless SELECT away from the browser. Secrets live in
brain_settings under `cred:<connection id>` — the same home repos.set_token()
already uses for per-repo GitHub tokens — and leave this module only through
get(), which a feeder calls at run time.

ponytail: stored as plaintext jsonb, like the provider key and repo tokens
beside them. Encrypt with a key derived from SESSION_SECRET if this ever
serves more than the one person who owns the machine.
"""
from __future__ import annotations

import json

from .db import connect

_PREFIX = "cred:"
# Shorter values are not worth redacting and would mangle ordinary words.
_MIN_REDACT = 6


def put(connection_id: str, secrets: dict[str, str]) -> None:
    kept = {k: v for k, v in secrets.items() if v}
    with connect() as c:
        c.execute(
            "INSERT INTO brain_settings (id, value) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (_PREFIX + connection_id, json.dumps(kept)))


def get(connection_id: str) -> dict[str, str]:
    with connect() as c:
        r = c.execute("SELECT value FROM brain_settings WHERE id = %s",
                      (_PREFIX + connection_id,)).fetchone()
    return dict(r["value"]) if r else {}


def drop(connection_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM brain_settings WHERE id = %s", (_PREFIX + connection_id,))


def redact(text: str) -> str:
    """Every stored secret replaced by ***, for error strings on their way to a
    run row or an HTTP response."""
    with connect() as c:
        rows = c.execute("SELECT value FROM brain_settings WHERE id LIKE %s",
                         (_PREFIX + "%",)).fetchall()
    for r in rows:
        for v in (r["value"] or {}).values():
            if isinstance(v, str) and len(v) >= _MIN_REDACT:
                text = text.replace(v, "***")
    return text
