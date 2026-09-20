"""Per-unit ingestion state: what is done, what is running, what failed.

Before this, the three states lived in three places and one of them nowhere:
queued in brain_sources.wiki_queued_at, done in a _absorb_log.json file on
disk, and running not recorded at all — so a run that died mid-absorb left no
trace of the unit it was on.

absorb_runner deliberately does NOT write here. It has to keep running from
cron with no server and no database; instead pipeline_run.py marks state from
the progress lines it already parses.
"""
from __future__ import annotations

from .db import connect

# A unit is only ever retried so many times before it is left alone. Without a
# ceiling a permanently broken unit is retried on every run, forever.
MAX_ATTEMPTS = 3


def seed(project_id: str, unit_ids: list[str]) -> None:
    """Record units as queued. Existing rows keep their state — a unit already
    done must not be dragged back to pending just because it was re-queued."""
    if not unit_ids:
        return
    # executemany lives on the cursor, not the connection: psycopg3's
    # Connection.execute is a convenience shortcut and has no executemany twin.
    with connect() as c, c.cursor() as cur:
        cur.executemany(
            "INSERT INTO brain_ingest_units (unit_id, project_id, state) "
            "VALUES (%s, %s, 'pending') ON CONFLICT (unit_id, project_id) "
            "DO NOTHING",
            [(u, project_id) for u in unit_ids])


def mark(unit_id: str, project_id: str, state: str, *, run_id: str | None = None,
         error: str | None = None, article: str | None = None) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO brain_ingest_units "
            "  (unit_id, project_id, state, attempts, run_id, error, article, "
            "   started_at, ended_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, "
            "        CASE WHEN %s = 'running' THEN now() END, "
            "        CASE WHEN %s IN ('done','failed') THEN now() END) "
            "ON CONFLICT (unit_id, project_id) DO UPDATE SET "
            "  state = EXCLUDED.state, "
            "  run_id = COALESCE(EXCLUDED.run_id, brain_ingest_units.run_id), "
            "  error = EXCLUDED.error, "
            "  article = COALESCE(EXCLUDED.article, brain_ingest_units.article), "
            "  attempts = brain_ingest_units.attempts "
            "    + CASE WHEN EXCLUDED.state = 'running' THEN 1 ELSE 0 END, "
            "  started_at = CASE WHEN EXCLUDED.state = 'running' THEN now() "
            "                    ELSE brain_ingest_units.started_at END, "
            "  ended_at = CASE WHEN EXCLUDED.state IN ('done','failed') "
            "                  THEN now() ELSE brain_ingest_units.ended_at END",
            (unit_id, project_id, state, 1 if state == "running" else 0,
             run_id, error, article, state, state))


def summary(project_id: str) -> dict:
    with connect() as c:
        rows = c.execute(
            "SELECT state, count(*) AS n FROM brain_ingest_units "
            "WHERE project_id = %s GROUP BY state", (project_id,)).fetchall()
    counts = {r["state"]: r["n"] for r in rows}
    return {s: counts.get(s, 0) for s in ("pending", "running", "done", "failed")}


def listing(project_id: str, state: str | None = None, limit: int = 200) -> list[dict]:
    """Units with the source name where one matches. The join is LEFT and on
    the id because a unit id is usually a source id but is not guaranteed to
    be one — a repo file's unit has no brain_sources row at all."""
    where, args = "u.project_id = %s", [project_id]
    if state:
        where += " AND u.state = %s"
        args.append(state)
    with connect() as c:
        rows = c.execute(
            "SELECT u.unit_id, u.state, u.attempts, u.error, u.article, "
            "       u.started_at, u.ended_at, s.name, s.kind "
            "FROM brain_ingest_units u "
            "LEFT JOIN brain_sources s ON s.id = u.unit_id "
            f"WHERE {where} "
            "ORDER BY u.ended_at DESC NULLS FIRST, u.started_at DESC NULLS LAST "
            "LIMIT %s", tuple(args) + (limit,)).fetchall()
    return [{"unitId": r["unit_id"], "state": r["state"], "attempts": r["attempts"],
             "error": r["error"], "article": r["article"],
             "name": r["name"] or r["unit_id"], "kind": r["kind"],
             "startedAt": r["started_at"].isoformat() if r["started_at"] else None,
             "endedAt": r["ended_at"].isoformat() if r["ended_at"] else None}
            for r in rows]


def per_run(project_id: str, run_ids: list[str]) -> dict[str, dict]:
    """{run_id: {done: n, failed: n, ...}} for a page of runs, in one query.

    A run's own items_written is a live counter and stops wherever the process
    did; these counts are what each file actually ended up as.
    """
    if not run_ids:
        return {}
    with connect() as c:
        rows = c.execute(
            "SELECT run_id::text AS run_id, state, count(*) AS n "
            "FROM brain_ingest_units "
            "WHERE project_id = %s AND run_id = ANY(%s::uuid[]) "
            "GROUP BY run_id, state", (project_id, list(run_ids))).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out.setdefault(r["run_id"], {})[r["state"]] = r["n"]
    return out


def reset_stale(project_id: str, run_id: str | None = None) -> int:
    """A killed run leaves units claiming to be running and nothing will ever
    move them. Put them back where the next run can pick them up."""
    with connect() as c:
        rows = c.execute(
            "UPDATE brain_ingest_units SET state = 'pending', "
            "  error = 'the run ended before this unit finished' "
            "WHERE project_id = %s AND state = 'running' "
            + ('AND run_id = %s ' if run_id else '') + 'RETURNING unit_id',
            (project_id, run_id) if run_id else (project_id,)).fetchall()
    return len(rows)


def requeue_failed(project_id: str) -> int:
    """Failed units still under the attempt ceiling go back to pending. Nothing
    is spent here — the next write-up is what picks them up."""
    with connect() as c:
        rows = c.execute(
            "UPDATE brain_ingest_units SET state = 'pending', error = NULL "
            "WHERE project_id = %s AND state = 'failed' AND attempts < %s "
            "RETURNING unit_id", (project_id, MAX_ATTEMPTS)).fetchall()
    return len(rows)
