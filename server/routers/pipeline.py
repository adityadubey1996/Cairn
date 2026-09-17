"""Drive and Chat pipeline runs: start one, watch it, stop it.

Every handler here is a thin shell over server/pipeline_runs.py. The work runs
in a detached process that this server does not hold a handle on — starting a
run means spawning and letting go, and watching one means reading Postgres.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, ingest_units, pipeline_runs as runs, projects, sources
from ..auth import current_user

sys.path.insert(0, str(config.ROOT / "scripts"))
from pipeline_run import CONNECTORS, units_for  # noqa: E402

router = APIRouter(prefix="/api/pipeline")


@router.get("/units")
def list_units(project_id: str, state: str | None = None,
               _email: str = Depends(current_user)):
    """Per-file ingestion state: done, running, not started, failed."""
    return {"summary": ingest_units.summary(project_id),
            "rows": ingest_units.listing(project_id, state)}


@router.post("/links/fetch", status_code=202)
def fetch_links(project_id: str, _email: str = Depends(current_user)):
    """Work the project's unfetched URL backlog.

    Its own action because it is its own shape of work: project-wide rather
    than per-connection, slow, and rate-limited by whichever hosts it is
    fetching from. Dragging it behind every connector sync is what made an
    incremental sync take an hour and what got three crawls throttling each
    other.
    """
    if runs.live("links"):
        raise HTTPException(409, "a link fetch is already running")
    run_id = runs.start("links")
    subprocess.Popen(
        [sys.executable, str(config.ROOT / "scripts" / "pipeline_run.py"),
         "--connector", "links", "--run-id", run_id,
         "--project-id", project_id, "--skip-absorb"],
        cwd=str(config.ROOT), start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"runId": run_id}


@router.post("/units/retry", status_code=202)
def retry_units(project_id: str, _email: str = Depends(current_user)):
    """Put failed units back in the queue. Spends nothing — the next write-up
    is what picks them up."""
    return {"requeued": ingest_units.requeue_failed(project_id)}

# Until a run has measured a real rate. 15k tokens is MAX_TOTAL_CHARS (60_000)
# at four characters per token; 45s is the middle of the observed per-unit range.
FALLBACK_RATE = {"tokens_per_unit": 15_000.0, "seconds_per_unit": 45.0}


def estimate_from(queued: int, rate: dict | None) -> dict:
    r = rate or FALLBACK_RATE
    return {"queued": queued,
            "tokens": round(queued * r["tokens_per_unit"]),
            "seconds": round(queued * r["seconds_per_unit"]),
            "measured": rate is not None}


def _pending_text() -> str:
    p = config.ROOT / "raw" / "_pending.md"
    return p.read_text() if p.is_file() else ""


def _check_connector(connector: str) -> None:
    if connector not in CONNECTORS:
        raise HTTPException(400, f"pipeline covers {', '.join(CONNECTORS)}, not {connector}")


@router.get("/estimate")
def estimate(connector: str, _email: str = Depends(current_user)):
    _check_connector(connector)
    return estimate_from(len(units_for(_pending_text(), connector)),
                         runs.absorb_rate(connector))


# The queue a human built by ticking rows in Sources, and the paid run that
# empties it. Bookkept under the connector id 'wiki' (seeded in models.py) so
# it gets the same phases, log and one-at-a-time guard as a feeder run without
# blocking one.
WIKI_RUN = "wiki"


@router.get("/wiki-queue")
def wiki_queue(project_id: str = "", _email: str = Depends(current_user)):
    project_id = project_id or projects.ensure_default()
    rows = sources.queued(project_id)
    live = runs.live(WIKI_RUN)
    return {**estimate_from(len(rows), runs.absorb_rate(WIKI_RUN)),
            "ids": [r["id"] for r in rows],
            "runId": str(live["id"]) if live else None}


@router.post("/wiki-queue/run", status_code=202)
def run_wiki_queue(payload: dict = Body(default={}), _email: str = Depends(current_user)):
    project_id = (payload or {}).get("project_id") or projects.ensure_default()
    ids = [r["id"] for r in sources.queued(project_id)]
    if not ids:
        raise HTTPException(409, "nothing is queued for the wiki")
    if not config.GROQ_API_KEY:
        raise HTTPException(409, "GROQ_API_KEY is not set — writing up is the only paid step")
    if runs.live(WIKI_RUN):
        raise HTTPException(409, "a wiki write-up is already in flight")

    run_id = runs.start(WIKI_RUN)
    args = [sys.executable, str(config.ROOT / "scripts" / "pipeline_run.py"),
            "--run-id", run_id, "--project-id", project_id, "--absorb-only"]
    for source_id in ids:
        args += ["--only-source", source_id]
    subprocess.Popen(args, cwd=str(config.ROOT), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"run_id": run_id, "queued": len(ids)}


@router.post("/runs", status_code=202)
def start_run(payload: dict = Body(default={}), _email: str = Depends(current_user)):
    connector = (payload or {}).get("connector", "")
    skip_absorb = bool((payload or {}).get("skip_absorb"))
    project_id = (payload or {}).get("project_id") or projects.ensure_default()
    _check_connector(connector)
    if not skip_absorb and not config.GROQ_API_KEY:
        raise HTTPException(409, "GROQ_API_KEY is not set — absorb is the only paid step")
    if runs.live(connector):
        raise HTTPException(409, f"{connector} already has a run in flight")

    run_id = runs.start(connector)
    args = [sys.executable, str(config.ROOT / "scripts" / "pipeline_run.py"),
            "--connector", connector, "--run-id", run_id,
            "--project-id", project_id]
    if skip_absorb:
        args.append("--skip-absorb")
    # start_new_session detaches it from this server's process group, so a
    # redeploy that stops the container's main process does not signal the run.
    subprocess.Popen(args, cwd=str(config.ROOT), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"run_id": run_id}


@router.get("/runs")
def list_runs(project_id: str, connector: str | None = None, limit: int = 25,
              _email: str = Depends(current_user)):
    """Run history with each run's per-file outcome.

    The counts come from brain_ingest_units rather than the run's own
    items_written, because that figure is a live counter: a run that was killed
    leaves it wherever it stopped, which reads as "wrote 3" with no way to see
    that 9 never started.
    """
    rows = runs.recent(limit, connector)
    by_run = ingest_units.per_run(project_id, [r["id"] for r in rows])
    for r in rows:
        r["units"] = by_run.get(r["id"], {})
    return {"runs": rows}


@router.get("/runs/{run_id}")
def get_run(run_id: str, _email: str = Depends(current_user)):
    row = runs.get(run_id)
    if not row:
        raise HTTPException(404, "no such run")
    return row


@router.get("/runs/{run_id}/log")
def get_log(run_id: str, after: int = 0, _email: str = Depends(current_user)):
    lines = runs.read_log(run_id, after)
    return {"lines": lines, "last": lines[-1]["seq"] if lines else after}


@router.post("/runs/{run_id}/stop")
def stop_run(run_id: str, _email: str = Depends(current_user)):
    row = runs.get(run_id)
    if not row:
        raise HTTPException(404, "no such run")
    if row["status"] != "running":
        raise HTTPException(409, f"run is {row['status']}, not running")
    if not row["pid"]:
        raise HTTPException(409, "run has not reported a pid yet")
    try:
        os.kill(row["pid"], signal.SIGTERM)
    except ProcessLookupError:
        # Already gone. The stale heartbeat will render it interrupted; saying
        # "stopped" here would claim a clean shutdown that did not happen.
        raise HTTPException(409, "process is already gone")
    return {"stopping": True}
