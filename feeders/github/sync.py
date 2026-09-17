"""Scheduled sweep for tracked GitHub repos: pull, re-graph, re-ingest.

ai-brain pulls; GitHub does not push. A webhook would be an accelerator, and
phase 2 can add one, but the sweep is the guarantee — the same reasoning
corpus.py already applies to its 15-minute loop.

Deliberately stops before absorb. Absorb is the only step that spends money, so
it is never triggered by a timer; the queue counts this leaves behind are what
the UI shows to make that decision.
"""
from __future__ import annotations

import logging

from server import repos

log = logging.getLogger("cairn.github")


def run(project_id: str | None = None) -> tuple[int, int]:
    """(repos_seen, repos_advanced). Called by connectors.run_now().

    project_id is accepted only for signature parity with the other feeders —
    this sweep advances every tracked repo regardless of project, since each
    brain_repos row already carries its own."""
    rows = [r for r in repos.list_repos()
            if r["state"] not in ("added", "evicted") and r["clone_path"]]
    advanced = 0
    for r in rows:
        if r["pinned_sha"]:
            continue  # parked on a commit-filtered preview; leave it alone
        try:
            before = r["head_sha"]
            repos.run_step(r["id"], "clone")
            after = (repos.get(r["id"]) or {}).get("head_sha")
            if after == before:
                continue  # nothing new on the branch — skip the free-but-slow steps
            repos.run_step(r["id"], "graph")
            repos.run_step(r["id"], "ingest")
            advanced += 1
        except repos.Busy:
            log.info("github sweep: %s busy, skipping", r["id"])
        except Exception:
            # One bad repo must not stop the sweep. run_step already recorded
            # the error on the row and on its run.
            log.exception("github sweep failed for %s", r["id"])
    return len(rows), advanced
