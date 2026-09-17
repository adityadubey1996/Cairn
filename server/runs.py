"""Pipeline run bookkeeping: one row per connector/repo-step run, in
brain_connectors / brain_connector_runs. Split out of connectors.py so
repos.py (github steps) can record runs without importing the connector
registry — connectors.py still owns REGISTRY/health/run_now/absorb_now
and imports start_run/finish_run from here.
"""
from __future__ import annotations

from .db import connect


def start_run(connector_id: str, *, repo_id: str | None = None,
              step: str | None = None) -> str:
    """repo_id/step are optional so the gdrive path is unchanged: a feeder run
    is one row with both null, a repo run is one row per pipeline step."""
    with connect() as c:
        row = c.execute(
            "INSERT INTO brain_connector_runs (connector_id, status, repo_id, step) "
            "VALUES (%s, 'running', %s, %s) RETURNING id",
            (connector_id, repo_id, step)).fetchone()
        return str(row["id"])


def finish_run(run_id: str, *, status: str, items_seen: int = 0,
               items_written: int = 0, error: str | None = None) -> None:
    with connect() as c:
        c.execute(
            "UPDATE brain_connector_runs SET status = %s, items_seen = %s, "
            "items_written = %s, error = %s, finished_at = now() WHERE id = %s",
            (status, items_seen, items_written, error, run_id))
