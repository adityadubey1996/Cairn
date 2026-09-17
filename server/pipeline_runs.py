"""Run state for the Drive/Chat pipeline.

The runner is a detached process — the server spawns it and lets go — so
Postgres is the only channel between the two. The runner writes; the router
reads; neither holds a handle on the other. That is what makes a run survive a
redeploy, and it is why every field the UI shows has a column here.

items_seen/items_written hold the CURRENT phase only, so the progress bar is one
read with no arithmetic. The finished record of each phase accumulates in
`phases`, which is the shape the summary view renders.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .db import connect

# Long enough to clear groq()'s 180s urlopen timeout: a run sitting inside one
# slow call must not flap to "interrupted" just for being quiet.
STALE_AFTER = timedelta(minutes=5)

LIVE_STATUS = "running"


def derive_status(row: dict, now: datetime) -> str:
    """A row still marked running whose heartbeat has gone quiet is interrupted.

    Derived at read time and never stored: there is no boot sweep and nothing to
    migrate, and a runner that comes back simply stops looking interrupted.
    """
    if row["status"] != LIVE_STATUS:
        return row["status"]
    beat = row.get("heartbeat_at") or row["started_at"]
    return "interrupted" if now - beat > STALE_AFTER else LIVE_STATUS


def start(connector_id: str) -> str:
    with connect() as c:
        row = c.execute(
            "INSERT INTO brain_connector_runs (connector_id, status, heartbeat_at) "
            "VALUES (%s, 'running', now()) RETURNING id", (connector_id,)).fetchone()
        return str(row["id"])


def set_pid(run_id: str, pid: int) -> None:
    with connect() as c:
        c.execute("UPDATE brain_connector_runs SET pid = %s WHERE id = %s",
                  (pid, run_id))


def begin_phase(run_id: str, phase: str, total: int = 0) -> None:
    """Enter a phase and reset the live counters to it."""
    with connect() as c:
        c.execute(
            "UPDATE brain_connector_runs SET phase = %s, items_seen = %s, "
            "items_written = 0, heartbeat_at = now() WHERE id = %s",
            (phase, total, run_id))


def progress(run_id: str, *, seen: int | None = None,
             written: int | None = None) -> None:
    """Move the live counters and bump the heartbeat. Either may be omitted."""
    sets, args = ["heartbeat_at = now()"], []
    if seen is not None:
        sets.append("items_seen = %s")
        args.append(seen)
    if written is not None:
        sets.append("items_written = %s")
        args.append(written)
    args.append(run_id)
    with connect() as c:
        c.execute(f"UPDATE brain_connector_runs SET {', '.join(sets)} WHERE id = %s",
                  args)


def end_phase(run_id: str, phase: str, record: dict) -> None:
    """Freeze this phase's numbers into `phases` under its own key."""
    with connect() as c:
        c.execute(
            "UPDATE brain_connector_runs SET phases = phases || %s::jsonb, "
            "heartbeat_at = now() WHERE id = %s",
            (json.dumps({phase: record}), run_id))


def finish(run_id: str, *, status: str, error: str | None = None) -> None:
    with connect() as c:
        c.execute(
            "UPDATE brain_connector_runs SET status = %s, error = %s, phase = NULL, "
            "finished_at = now() WHERE id = %s", (status, error, run_id))


def log(run_id: str, lines: list[str]) -> int:
    """Append lines and return the last sequence number written.

    # ponytail: the next seq comes from max(seq)+1 rather than a sequence object.
    # One run has exactly one writer, so there is no race to lose. Revisit only
    # if a phase ever fans out across processes.
    """
    with connect() as c:
        row = c.execute("SELECT coalesce(max(seq), 0) AS s FROM brain_run_log "
                        "WHERE run_id = %s", (run_id,)).fetchone()
        seq = row["s"]
        if not lines:
            return seq
        for line in lines:
            seq += 1
            c.execute("INSERT INTO brain_run_log (run_id, seq, line) VALUES (%s, %s, %s)",
                      (run_id, seq, line[:4000]))
        c.execute("UPDATE brain_connector_runs SET heartbeat_at = now() WHERE id = %s",
                  (run_id,))
        return seq


def read_log(run_id: str, after: int, limit: int = 500) -> list[dict]:
    with connect() as c:
        return c.execute(
            "SELECT seq, at, line FROM brain_run_log WHERE run_id = %s AND seq > %s "
            "ORDER BY seq LIMIT %s", (run_id, after, limit)).fetchall()


def _hydrate(row: dict | None) -> dict | None:
    if not row:
        return None
    row["id"] = str(row["id"])
    row["status"] = derive_status(row, datetime.now(timezone.utc))
    return row


def get(run_id: str) -> dict | None:
    with connect() as c:
        return _hydrate(c.execute("SELECT * FROM brain_connector_runs WHERE id = %s",
                                  (run_id,)).fetchone())


def recent(limit: int = 25, connector_id: str | None = None) -> list[dict]:
    """Past runs, newest first.

    The history was always in this table; nothing exposed it, so a write-up
    could only be watched while the tab that started it stayed open. Losing the
    run id meant losing the run.
    """
    where, args = "", []
    if connector_id:
        where = "WHERE connector_id = %s"
        args.append(connector_id)
    with connect() as c:
        rows = c.execute(
            f"SELECT * FROM brain_connector_runs {where} "
            "ORDER BY started_at DESC LIMIT %s", tuple(args) + (limit,)).fetchall()
    return [_hydrate(r) for r in rows]


def live(connector_id: str) -> dict | None:
    """This connector's run in flight, or None. A stale heartbeat is not live."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM brain_connector_runs WHERE connector_id = %s "
            "AND status = 'running' ORDER BY started_at DESC LIMIT 1",
            (connector_id,)).fetchone()
    row = _hydrate(row)
    return row if row and row["status"] == LIVE_STATUS else None


def absorb_rate(connector_id: str) -> dict | None:
    """Tokens and seconds per absorbed unit, from this connector's last real run.

    None until one exists, which is what makes the UI say its estimate is a
    fallback rather than claiming accuracy it has not measured.
    """
    with connect() as c:
        row = c.execute(
            "SELECT (phases->'absorb'->>'tokens_in')::bigint  AS tokens_in, "
            "       (phases->'absorb'->>'tokens_out')::bigint AS tokens_out, "
            "       (phases->'absorb'->>'written')::int       AS units, "
            "       (phases->'absorb'->>'seconds')::float     AS seconds "
            "  FROM brain_connector_runs "
            " WHERE connector_id = %s AND status IN ('ok', 'stopped') "
            "   AND (phases->'absorb'->>'written')::int > 0 "
            " ORDER BY started_at DESC LIMIT 1", (connector_id,)).fetchone()
    if not row or not row["units"]:
        return None
    return {"tokens_per_unit": (row["tokens_in"] + row["tokens_out"]) / row["units"],
            "seconds_per_unit": row["seconds"] / row["units"]}
