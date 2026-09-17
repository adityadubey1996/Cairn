"""GET /api/timeline — one reverse-chronological feed merging sync and absorb
runs, each row carrying the per-phase breakdown its run recorded.

A read model over brain_connector_runs. Two writers fill that table and they
disagree about the top-level counters, which is the whole reason _written()
exists — see its docstring. pipeline_runs.py owns the richer phase/heartbeat
columns and this module reads them: `phases` is, in that module's words, "the
shape the summary view renders", and this is the summary view.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .db import connect
from .pipeline_runs import derive_status

_NOUN = {"gdrive": "files", "gchat": "messages", "github": "files",
        "links": "links", "whatsapp": "messages", "linkedin": "messages",
        "upload": "files"}
_DISPLAY = {"gdrive": "Google Drive", "gchat": "Google Chat", "github": "GitHub",
           "links": "Saved links", "whatsapp": "WhatsApp", "linkedin": "LinkedIn",
           "upload": "Upload"}

# Pipeline order, which is also display order. push runs twice in a full run —
# once after scrape, once after absorb — but `phases` is an object, so the
# second overwrites the first and only the final tally survives.
_PHASE_ORDER = ("scrape", "push", "ingest", "absorb")


def _phase_detail(name: str, rec: dict) -> str:
    """Each phase counts a different noun, so each gets its own sentence."""
    if name == "scrape":
        return f"{rec.get('written') or 0} written of {rec.get('seen') or 0} seen"
    if name == "push":
        return f"{rec.get('files') or 0} files to S3"
    if name == "ingest":
        return f"{rec.get('queued') or 0} units queued"
    tokens = (rec.get("tokens_in") or 0) + (rec.get("tokens_out") or 0)
    detail = f"{rec.get('written') or 0} of {rec.get('seen') or 0} articles"
    return detail + (f" · {tokens:,} tokens" if tokens else "")


def _phases(phases: dict) -> list[dict]:
    return [{"name": name, "detail": _phase_detail(name, rec), "seconds": rec.get("seconds")}
            for name in _PHASE_ORDER if isinstance(rec := phases.get(name), dict)]


def _written(row: dict, phases: dict, phase: str) -> int:
    """How many items the named phase wrote.

    Not row["items_written"], which pipeline_runs.begin_phase() resets to 0 on
    entering each phase so the progress bar needs no arithmetic. A scrape that
    wrote 3 files is followed by push and ingest, so by the end of the run that
    column holds the last phase's tally — which is why this feed read "Synced 0
    files from Google Drive" while phases.scrape.written said 3.

    The frozen per-phase record is authoritative. items_written is trusted only
    for old-runner rows, which record no phases at all.
    """
    rec = phases.get(phase)
    if isinstance(rec, dict):
        return rec.get("written") or 0
    return row["items_written"] or 0


def _event(row: dict, repo_label: str | None, now: datetime) -> dict:
    display = repo_label or _DISPLAY.get(row["connector_id"], row["connector_id"])
    phases = row["phases"] or {}
    status = derive_status(row, now)
    # step is NULL on every pipeline-launched run — only the old runner sets it
    # — so a recorded absorb phase is the other half of this test.
    is_absorb = row["step"] == "absorb" or "absorb" in phases
    verb = "Absorb" if is_absorb else "Sync"

    if status == "error":
        kind = "error"
        text = f"{verb} failed — {display}" + (f": {row['error']}" if row["error"] else "")
    elif status == "interrupted":
        # Reuses the error kind rather than inventing a fifth one: a run whose
        # heartbeat died is a failure you have to look at, whatever killed it.
        kind = "error"
        text = f"{verb} interrupted — {display} stopped reporting"
    elif status == "running":
        kind = "absorb" if is_absorb else "sync"
        text = f"{verb}ing {display}" + (f" — {row['phase']}" if row["phase"] else "")
    elif is_absorb:
        kind = "absorb"
        n = _written(row, phases, "absorb")
        text = f"Absorbed {n} article" + ("" if n == 1 else "s")
    else:
        kind = "sync"
        n = _written(row, phases, "scrape")
        text = f"Synced {n} {_NOUN.get(row['connector_id'], 'items')} from {display}"

    at = row["finished_at"] or row["started_at"]
    return {"id": str(row["id"]), "kind": kind, "text": text, "at": at.isoformat(),
            "status": status, "connector": row["connector_id"],
            "phases": _phases(phases),
            "seconds": (round((row["finished_at"] - row["started_at"]).total_seconds(), 1)
                        if row["finished_at"] else None)}


_COLS = ("r.id, r.connector_id, r.status, r.step, r.items_written, r.error, "
         "r.started_at, r.finished_at, r.phases, r.phase, r.heartbeat_at")


def list_timeline(project_id: str, limit: int = 200) -> list[dict]:
    with connect() as c:
        repo_runs = c.execute(
            f"SELECT {_COLS}, rep.owner, rep.name AS repo_name "
            "FROM brain_connector_runs r JOIN brain_repos rep ON rep.id = r.repo_id "
            "WHERE rep.project_id = %s "
            "ORDER BY r.started_at DESC LIMIT %s", (project_id, limit)).fetchall()
        connected_kinds = [r["kind"] for r in c.execute(
            "SELECT DISTINCT kind FROM brain_connector_connections WHERE project_id = %s",
            (project_id,)).fetchall()]
        other_runs = c.execute(
            f"SELECT {_COLS} FROM brain_connector_runs r "
            "WHERE r.repo_id IS NULL AND r.connector_id = ANY(%s) "
            "ORDER BY r.started_at DESC LIMIT %s", (connected_kinds, limit)).fetchall()

    now = datetime.now(timezone.utc)
    events = [_event(r, f"{r['owner']}/{r['repo_name']}", now) for r in repo_runs]
    events += [_event(r, None, now) for r in other_runs]
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]
