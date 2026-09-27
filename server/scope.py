"""What one connection is allowed to read.

Scope lives in the connection's own `config` under "scope", because that is
already what `connections.settings_for()` hands a feeder at run time — a
feeder reads its scope back the same way it reads `max_items`, and nothing in
the job runner had to learn about any of this.

An empty scope means "everything this connection can reach", which is what
every connector did before scope existed. That is the only safe default: a
connection created by an OAuth callback has no scope yet, and it must not
silently sync nothing.
"""
from __future__ import annotations

import json

from .db import connect

# An item list can select a lot, but not unboundedly: these end up in a JQL
# `in (...)` or a Drive folder walk, and a request that names ten thousand
# projects fails at the provider rather than here.
MAX_ITEMS = 500
MAX_WINDOW_DAYS = 3650


class Invalid(ValueError):
    """A scope the UI should refuse, with a sentence saying why."""


def _spec(kind: str):
    from . import connectors
    for c in connectors.REGISTRY:
        if c.kind == kind and c.scope:
            return c.scope
    return None


def validate(kind: str, scope: dict | None) -> dict:
    """The stored shape: {"items": [...], "<choice>": [...] | int}.

    Unknown keys are refused rather than dropped. A scope that quietly loses a
    filter would sync more than the user agreed to, which is the one mistake
    this whole feature exists to prevent.
    """
    if scope is None:
        return {}
    if not isinstance(scope, dict):
        raise Invalid("scope must be an object")

    spec = _spec(kind)
    if spec is None:
        raise Invalid(f"{kind} connections cannot be scoped")

    allowed = {"items"} | {f.name for f in spec.fields}
    unknown = set(scope) - allowed
    if unknown:
        raise Invalid(f"unknown scope fields: {', '.join(sorted(unknown))}")

    out: dict = {}
    items = scope.get("items", [])
    if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
        raise Invalid("items must be a list of ids")
    if len(items) > MAX_ITEMS:
        raise Invalid(f"a scope can name at most {MAX_ITEMS} {spec.kind}s")
    # Deduplicated but order-preserving, so the saved scope reads back the way
    # it was ticked.
    out["items"] = list(dict.fromkeys(i.strip() for i in items))

    for field in spec.fields:
        if field.name not in scope:
            continue
        value = scope[field.name]
        if not field.values:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_WINDOW_DAYS:
                raise Invalid(f"{field.name} must be a whole number of days, 0 for no limit")
            out[field.name] = value
            continue
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise Invalid(f"{field.name} must be a list")
        bad = [v for v in value if v not in field.values]
        if bad:
            raise Invalid(f"{field.name}: {', '.join(sorted(bad))} "
                          f"is not one of {', '.join(field.values)}")
        out[field.name] = list(dict.fromkeys(value))
    return out


def defaults(kind: str) -> dict:
    """What a connection reads before anyone has chosen: no item filter, and
    each choice at whatever the connector says is sensible."""
    spec = _spec(kind)
    if spec is None:
        return {}
    return {f.name: list(f.default) for f in spec.fields if f.default}


def _row(connection_id: str) -> dict:
    """The connection, whichever table it lives in.

    A tracked repo is a connection on the Connect screen but a brain_repos row
    underneath, exactly as automation.py treats it. Answering "not scopeable"
    for one is the honest reply; 404 would make every card ask a question the
    screen then has to swallow.
    """
    if "/" in connection_id:
        with connect() as c:
            row = c.execute("SELECT id FROM brain_repos WHERE id = %s",
                            (connection_id,)).fetchone()
        if not row:
            raise KeyError(f"no such connection: {connection_id}")
        return {"kind": "github", "config": {}}
    with connect() as c:
        row = c.execute("SELECT kind, config FROM brain_connector_connections WHERE id = %s",
                        (connection_id,)).fetchone()
    if not row:
        raise KeyError(f"no such connection: {connection_id}")
    return row


def get(connection_id: str) -> dict:
    row = _row(connection_id)
    saved = (row["config"] or {}).get("scope")
    return {"kind": row["kind"], "scope": saved if isinstance(saved, dict) else None,
            "scopeable": _spec(row["kind"]) is not None}


def save(connection_id: str, scope: dict | None) -> dict:
    row = _row(connection_id)
    checked = validate(row["kind"], scope)
    config = dict(row["config"] or {})
    # An explicitly empty scope is a removal, not an empty selection: it puts
    # the connection back to reading everything rather than nothing.
    if checked.get("items") or any(k != "items" for k in checked):
        config["scope"] = checked
    else:
        config.pop("scope", None)
    with connect() as c:
        c.execute("UPDATE brain_connector_connections SET config = %s WHERE id = %s",
                  (json.dumps(config), connection_id))
    return get(connection_id)


def options(connection_id: str) -> dict:
    """The live choices, read from the provider with this connection's own
    credentials. Slow by nature — it is one or more API calls — so it is its
    own route rather than part of the connection list."""
    from . import connections
    row = _row(connection_id)
    spec = _spec(row["kind"])
    if spec is None:
        raise Invalid(f"{row['kind']} connections cannot be scoped")
    groups = spec.options(connections.settings_for(connection_id))
    return {
        "kind": spec.kind, "note": spec.note, "groups": groups,
        "fields": [{"name": f.name, "label": f.label, "values": list(f.values),
                    "default": list(f.default), "multiple": f.multiple}
                   for f in spec.fields],
    }
