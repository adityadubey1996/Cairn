"""Fetch this provider's content into the shared source contract.

Two entry points:
  run()   — the real sync. Writes sources + raw/inbox and records rows.
  probe() — bounded, read-only proof of access for scripts/connector_check.py.
"""
from __future__ import annotations

from feeders.result import SyncResult, failure


def probe(settings: dict, limit: int = 3) -> dict:
    """Authenticate and list a few items. Writes nothing.

    Raise a clear error naming what the user must fix; connector_check.py shows
    it verbatim.
    """
    raise NotImplementedError("list up to `limit` items and return them")


def run(project_id: str | None = None, on_progress=None,
        connection_id: str | None = None, **kwargs) -> SyncResult:
    """Returns SyncResult(seen, written, failures).

    on_progress(done, total, label) drives the run log. connection_id
    attributes every row to the connection whose sync produced it.
    """
    seen = written = 0
    failures: list[dict] = []
    # settings = connections.settings_for(connection_id) if connection_id else {}
    # for item in fetch(...):
    #     seen += 1
    #     try: ... write source + inbox entry ... ; written += 1
    #     except Exception as e: failures.append(failure(item_id, item_name, e))
    return SyncResult(seen, written, failures)
