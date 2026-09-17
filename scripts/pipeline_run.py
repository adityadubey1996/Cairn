#!/usr/bin/env python3
"""Run one connector's pipeline end to end, reporting to Postgres as it goes.

    python3 scripts/pipeline_run.py --connector gchat --run-id <uuid>

Spawned detached by server/routers/pipeline.py, which never waits on it: a
redeploy restarts the server without touching a run in flight. Also runnable by
hand and by cron with no server at all, the same way scripts/refresh.py is.

Five phases. Only absorb costs money.

    scrape   the feeder's run(), in-process, with a progress callback
    links    fetch the URLs that scrape just wrote into its output, so a link
             posted in a chat message resolves in the same pass as the message
    ingest   pipeline/ingest.py, rebuilding raw/_pending.md
    absorb   pipeline/absorb_runner.py over this connector's queued units
    push     storage.push(), every durable tree
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, pipeline_runs as runs, storage  # noqa: E402

CONNECTORS = ("gdrive", "gchat", "links", "whatsapp", "linkedin", "upload")

# What each feeder calls its watermark. The names differ because the APIs do:
# Drive filters on the file's modifiedTime, Chat on the message's createTime.
# A connector absent here simply ignores --since.
SINCE_ARG = {"gdrive": "modified_after", "gchat": "created_after"}

# Keys are connector ids, which are also the sources/<id>/ subdirectory names
# units_for() selects on — so a new entry needs no other change here.
FEEDER = {
    "gdrive": "feeders.gdrive.sync",
    "gchat": "feeders.chat.sync",
    "links": "feeders.links.sync",
    "whatsapp": "feeders.whatsapp.sync",
    "linkedin": "feeders.linkedin.sync",
    "upload": "feeders.upload.sync",
}

# Above the ~11M a full Drive plus Chat sweep is expected to need. A runaway
# guard, not a per-run budget the operator is meant to tune.
DEFAULT_MAX_TOKENS = 15_000_000

# `- `<unit id>` — `<source path>` (<kind>)`, the line ingest.py writes into
# each bucket of raw/_pending.md. The separator is an em dash, not a hyphen.
_QUEUE_LINE = re.compile(r"^- `([^`]+)` — `([^`]+)`")

_PROGRESS = re.compile(r"^\[(\d+)/(\d+)\]")

# The same line as _PROGRESS, read for the unit id rather than the counter —
# a separate pattern so parse_progress keeps its two-tuple contract.
_UNIT = re.compile(r"^\[\d+/\d+\]\s+(\S+)")

# "(attempt N)" sits between the article path and the token counts, so the two
# halves must not be required to be adjacent.
_RESULT = re.compile(r"^\s+ok\s+->\s+(\S+).*?\btok=(\d+)/(\d+)\s+([\d.]+)s")


def units_for(pending_text: str, connector: str) -> list[str]:
    """Queued unit ids whose source file lives under sources/<connector>/.

    The SOURCE PATH is the selector, not the unit id and not the kind.
    connectors._queued_units() matches a trailing -YYYY-MM-DD that Chat ids
    carry and Drive ids do not, so it returns nothing for gdrive; and Drive's
    `doc` units share that kind with the repo's own DESIGN.md. Only the path is
    right for both feeders.

    The removed bucket is skipped: a removed unit has no entry left to read.
    """
    prefix = f"sources/{connector}/"
    out, bucket = [], None
    for line in pending_text.splitlines():
        if line.startswith("## "):
            bucket = line
        elif bucket and ("new" in bucket or "changed" in bucket):
            m = _QUEUE_LINE.match(line)
            if m and m.group(2).startswith(prefix):
                out.append(m.group(1))
    return out


def parse_progress(line: str) -> tuple[int, int] | None:
    """(unit index, batch size) from absorb_runner's `[i/N] <id>` line."""
    m = _PROGRESS.match(line)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_unit(line: str) -> str | None:
    """The unit id from absorb_runner's `[i/N] <id>` line, or None."""
    m = _UNIT.match(line)
    return m.group(1) if m else None


def parse_result(line: str) -> dict | None:
    """One published article's cost, or None for any line that is not a success.

    Quarantines, errors and skips deliberately return None: they did not produce
    an article, and counting them as written would overstate what a run bought.
    Their tokens are still counted by absorb_runner's own batch total.
    """
    m = _RESULT.match(line)
    if not m:
        return None
    return {"article": m.group(1), "tok_in": int(m.group(2)),
            "tok_out": int(m.group(3)), "seconds": float(m.group(4))}


def _pending_text() -> str:
    p = config.ROOT / "raw" / "_pending.md"
    return p.read_text() if p.is_file() else ""


def phase_scrape(run_id: str, connector: str, project_id: str,
                 connection_id: str = "", since: str = "") -> dict:
    mod = importlib.import_module(FEEDER[connector])
    t0 = time.monotonic()

    def on_progress(done: int, total: int, label: str) -> None:
        runs.progress(run_id, seen=total, written=done)
        runs.log(run_id, [f"  [{done}/{total}] {label}"])

    runs.begin_phase(run_id, "scrape")
    # Cleared here rather than inside each feeder: one call covers every
    # connector and anchors the meaning of a failed row to the latest scrape.
    from server import sources as sources_index
    dropped = sources_index.clear_failures(project_id, connector)
    if dropped:
        runs.log(run_id, [f"cleared {dropped} failure(s) from the previous scrape"])
    # project_id is not optional here: omitting it makes the feeder fall back to
    # the default project, so every pipeline-path scrape landed in that project
    # regardless of which one the connection belongs to.
    kwargs = {"project_id": project_id, "on_progress": on_progress,
              "connection_id": connection_id or None}
    if since and connector in SINCE_ARG:
        kwargs[SINCE_ARG[connector]] = since
        runs.log(run_id, [f"incremental: only what changed after {since}"])
    seen, written = mod.run(**kwargs)
    record = {"seen": seen, "written": written,
              "seconds": round(time.monotonic() - t0, 1)}
    runs.log(run_id, [f"scrape: {written} written of {seen} seen"])
    runs.end_phase(run_id, "scrape", record)
    return record


# Every connector whose output is prose that can mention a URL. `links` is
# absent on purpose: it IS the fetcher, and re-scanning its own output is how a
# one-hop crawl turns into an unbounded one (see discover_urls').
LINK_SCANNING_CONNECTORS = ("gdrive", "gchat", "whatsapp", "linkedin", "upload")


def phase_links(run_id: str, project_id: str) -> dict:
    """Fetch the URLs the scrape just wrote into its transcripts.

    A phase rather than a separate connector run, because a link posted in a
    chat message is part of that message — needing to remember a second sync to
    resolve it is how 643 URLs sat unfetched. The links feeder stays the only
    thing that fetches a URL, so the SSRF guard, the size cap and the
    HTML/PDF/data classification live in exactly one place.
    """
    t0 = time.monotonic()
    runs.begin_phase(run_id, "links")
    from feeders.links import sync as links

    def on_progress(done: int, total: int, url: str) -> None:
        runs.progress(run_id, seen=total, written=done)
        runs.log(run_id, [f"  [{done}/{total}] {url[:120]}"])

    seen, written = links.run(project_id=project_id, on_progress=on_progress)
    record = {"seen": seen, "written": written,
              "seconds": round(time.monotonic() - t0, 1)}
    runs.log(run_id, [f"links: {written} fetched of {seen} attempted"])
    runs.end_phase(run_id, "links", record)
    return record


def _stream(run_id: str, args: list[str], on_line=None) -> int:
    """Run a pipeline subprocess, mirroring every line into the run log.

    Line-buffered and read as it arrives, which is the whole point: the absorb
    phase runs for hours and its output is the only window into it.
    """
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen(args, cwd=str(config.ROOT), env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         bufsize=1)
    batch = []
    for line in p.stdout:
        line = line.rstrip("\n")
        batch.append(line)
        if on_line:
            on_line(line)
        # ponytail: flush every 20 lines rather than every line. One INSERT per
        # line over a 700-unit absorb is thousands of round trips to save at
        # most 20 lines of lag in a view that polls every 2s.
        if len(batch) >= 20:
            runs.log(run_id, batch)
            batch = []
    if batch:
        runs.log(run_id, batch)
    return p.wait()


def phase_ingest(run_id: str, connector: str) -> dict:
    t0 = time.monotonic()
    runs.begin_phase(run_id, "ingest")
    code = _stream(run_id, [sys.executable, "pipeline/ingest.py",
                            "--repo", ".", "--out", "raw/entries"])
    if code != 0:
        raise RuntimeError(f"ingest exited {code}")
    queued = len(units_for(_pending_text(), connector))
    record = {"queued": queued, "seconds": round(time.monotonic() - t0, 1)}
    runs.log(run_id, [f"ingest: {queued} {connector} units queued"])
    runs.end_phase(run_id, "ingest", record)
    return record


def _absorb_has_token_ceiling() -> bool:
    """Whether absorb_runner accepts --max-tokens.

    It grows that flag in Task 2 of the pipeline-refresh plan. Until then an
    absorb run has no spend ceiling, which is the exact failure the flag exists
    to prevent, so phase_absorb refuses instead of spending uncapped.
    """
    return "--max-tokens" in (config.ROOT / "pipeline" / "absorb_runner.py").read_text()


def phase_absorb(run_id: str, ids: list[str], max_tokens: int,
                 project_id: str = "") -> dict:
    """Absorb exactly `ids`. The caller decides what they are: a connector run
    passes units_for(_pending_text(), connector), a wiki write-up passes the
    ids a human ticked in the Sources list."""
    t0 = time.monotonic()
    runs.begin_phase(run_id, "absorb", total=len(ids))
    if not ids:
        record = {"seen": 0, "written": 0, "seconds": 0.0,
                  "tokens_in": 0, "tokens_out": 0}
        runs.log(run_id, ["absorb: nothing queued"])
        runs.end_phase(run_id, "absorb", record)
        return record
    if not _absorb_has_token_ceiling():
        raise RuntimeError(
            "absorb_runner has no --max-tokens ceiling yet (pipeline-refresh "
            "Task 2). Re-run with --skip-absorb, or land that task first — "
            "spawning an uncapped absorb over "
            f"{len(ids)} units is not safe.")

    from server import ingest_units
    # Everything about to be attempted is visible as "not started" before the
    # first line of output arrives — otherwise a long absorb shows nothing at
    # all until its first unit lands.
    if project_id:
        ingest_units.seed(project_id, ids)

    tally = {"written": 0, "tok_in": 0, "tok_out": 0}
    # The unit absorb_runner last announced, and the ones that went on to
    # publish. A stopped or failed run must leave everything it did NOT write
    # up still queued, so the queue is cleared per published unit rather than
    # wholesale at the end.
    at = {"uid": None}
    published: list[str] = []

    def on_line(line: str) -> None:
        if (p := parse_progress(line)):
            runs.progress(run_id, seen=p[1])
            at["uid"] = parse_unit(line)
            if at["uid"] and project_id:
                ingest_units.mark(at["uid"], project_id, "running", run_id=run_id)
        elif (r := parse_result(line)):
            tally["written"] += 1
            tally["tok_in"] += r["tok_in"]
            tally["tok_out"] += r["tok_out"]
            runs.progress(run_id, written=tally["written"])
            if at["uid"]:
                published.append(at["uid"])
                if project_id:
                    ingest_units.mark(at["uid"], project_id, "done",
                                      run_id=run_id, article=r["article"])

    # --kind "" disables absorb_runner's kind filter, which still applies even
    # when --only is given. Drive units span meeting_transcript, doc and
    # binary_doc, so any single --kind would silently drop most of them.
    #
    # ponytail: 718 ids on argv is about 37 KB, far under every ARG_MAX we run
    # on. Add an --only-file argument to absorb_runner if the queue ever reaches
    # a few thousand units.
    args = [sys.executable, "pipeline/absorb_runner.py", "--repo", ".",
            "--kind", "", "--max-tokens", str(max_tokens)]
    # Without this absorb_runner defaults to <repo>/wiki, so a run spawned by
    # the V2 server wrote articles into the V1 wiki while the V2 UI read an
    # empty directory — 11 articles and 583k tokens landed somewhere the app
    # could not show. WIKI_ROOTS[0] is what the UI serves, and storage.trees()
    # pushes the same directory, so all three agree by construction.
    if config.WIKI_ROOTS:
        args += ["--wiki", str(config.WIKI_ROOTS[0])]
    for uid in ids:
        args += ["--only", uid]
    code = _stream(run_id, args, on_line)
    # Before the raise, deliberately: a run that died halfway still wrote up
    # everything it published, and re-queueing those would pay for them twice.
    if published and project_id:
        from server import sources as sources_index
        sources_index.set_queued(project_id, published, False)
        runs.log(run_id, [f"cleared {len(published)} source(s) from the wiki queue"])
    # Whatever is still "running" never reported a result — the process ended
    # first. Left alone it would claim to be running forever.
    if project_id:
        stale = ingest_units.reset_stale(project_id)
        if stale:
            runs.log(run_id, [f"{stale} unit(s) did not finish; back in the queue"])
    if code != 0:
        raise RuntimeError(f"absorb exited {code}")
    record = {"seen": len(ids), "written": tally["written"],
              "seconds": round(time.monotonic() - t0, 1),
              "tokens_in": tally["tok_in"], "tokens_out": tally["tok_out"]}
    runs.end_phase(run_id, "absorb", record)
    return record


def phase_push(run_id: str) -> dict:
    t0 = time.monotonic()
    runs.begin_phase(run_id, "push")
    try:
        n = storage.push()
    except Exception as e:
        # Never fatal. The articles and sources are on local disk and the next
        # push retries, exactly as everywhere else in this codebase.
        runs.log(run_id, [f"s3 push failed: {e} (everything is still on disk)"])
        n = 0
    record = {"files": n, "seconds": round(time.monotonic() - t0, 1)}
    runs.log(run_id, [f"push: {n} files to s3"])
    runs.end_phase(run_id, "push", record)
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connector", default="",
                    help=f"one of {', '.join(CONNECTORS)}; omitted only with --absorb-only")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--connection-id", default="",
                    help="attribute the scraped rows to this connection "
                         "(absent for hand and cron runs, which have none)")
    ap.add_argument("--project-id", default="",
                    help="which project the scraped units belong to "
                         "(default: the default project, for hand and cron runs)")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--since", default="",
                    help="RFC3339 watermark; fetch only what changed after it.")
    ap.add_argument("--full", action="store_true",
                    help="Ignore the stored watermark and re-list everything.")
    ap.add_argument("--skip-absorb", action="store_true",
                    help="free phases only — scrape, ingest and push")
    ap.add_argument("--skip-links", action="store_true",
                    help="do not follow URLs found in this scrape's output")
    ap.add_argument("--absorb-only", action="store_true",
                    help="absorb the --only-source units and push; no scrape, "
                         "no ingest — the units were already scraped by "
                         "whichever connector brought them in")
    ap.add_argument("--only-source", action="append", default=[],
                    help="absorb just this source id; repeatable")
    a = ap.parse_args()
    if a.absorb_only:
        if not a.only_source:
            ap.error("--absorb-only needs at least one --only-source")
    elif a.connector not in CONNECTORS:
        ap.error(f"--connector must be one of {', '.join(CONNECTORS)}")

    run_id = a.run_id
    runs.set_pid(run_id, os.getpid())
    label = "wiki write-up" if a.absorb_only else a.connector
    runs.log(run_id, [f"pipeline {label} starting (pid {os.getpid()})"])
    try:
        project_id = a.project_id
        if not project_id:
            from server import projects
            project_id = projects.ensure_default()
        if a.absorb_only:
            # No scrape and no ingest: absorb_runner resolves --only against
            # raw/entries, which holds every unit ever ingested, not just what
            # the last scrape queued. That is what lets a file scraped last
            # month be written up on demand.
            phase_absorb(run_id, a.only_source, a.max_tokens, project_id)
            phase_push(run_id)
            runs.finish(run_id, status="ok")
            runs.log(run_id, ["done"])
            return 0
        # The watermark advances to when this run STARTED, never to "now":
        # anything written while the scrape was in flight has to be caught by
        # the next run rather than falling in the gap between them.
        started_at = datetime.now(timezone.utc).isoformat()
        scraped = phase_scrape(run_id, a.connector, project_id, a.connection_id,
                               since="" if a.full else a.since)
        if a.connection_id:
            # Only after a scrape that did not raise. A failed scrape leaves the
            # watermark where it was so the missed window is retried.
            from server import connections
            connections.advance_watermark(a.connection_id, started_at)
        if scraped["written"]:
            # Sources reach S3 before the expensive phase starts, so a crash
            # during absorb never costs the fetched content. Same reasoning as
            # connectors.run_now().
            phase_push(run_id)
        # Before ingest, so a page fetched from this scrape's transcripts is
        # queued in the same pass rather than waiting for the next sync.
        if a.connector in LINK_SCANNING_CONNECTORS and not a.skip_links:
            try:
                phase_links(run_id, project_id)
            except Exception as e:
                # Never fatal: the transcripts are already on disk and pushed.
                # An unreachable web is not a reason to fail a chat sync.
                runs.log(run_id, [f"links phase failed: {e} (scrape is unaffected)"])
        phase_ingest(run_id, a.connector)
        if not a.skip_absorb:
            phase_absorb(run_id, units_for(_pending_text(), a.connector),
                         a.max_tokens, project_id)
            phase_push(run_id)
        runs.finish(run_id, status="ok")
        runs.log(run_id, ["done"])
        return 0
    except Exception as e:
        runs.log(run_id, [f"FAILED: {type(e).__name__}: {e}"])
        runs.finish(run_id, status="error", error=str(e)[:2000])
        return 1


if __name__ == "__main__":
    sys.exit(main())
