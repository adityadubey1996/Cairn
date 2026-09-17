"""Tracked GitHub repos: add, probe, run the pipeline, read commits and runs.

Thin, like routers/connectors.py — validation, git and job logic live in
server/repos.py. The only thing this file decides is which exception becomes
which status code.

Every mutating step returns 202 with a run id and the client polls GET /api/repos.
Cloning and absorb take minutes; a request must never wait on them.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException

from .. import config, corpus, repos, storage
from ..auth import current_user

log = logging.getLogger("cairn.repos")
router = APIRouter(prefix="/api/repos")


def _fail(e: Exception):
    if isinstance(e, repos.TooBig):
        raise HTTPException(413, str(e))
    if isinstance(e, repos.Invalid):
        raise HTTPException(400, str(e))
    if isinstance(e, KeyError):
        raise HTTPException(404, str(e).strip("'"))
    if isinstance(e, (repos.Busy, repos.NotCloned)):
        raise HTTPException(409, str(e))
    raise HTTPException(502, str(e)[:2000])


@router.get("")
def list_repos(_email: str = Depends(current_user)):
    running = repos.busy()
    rows = repos.list_repos()
    for r in rows:
        r["running_step"] = running.get(r["id"])
        r["can_absorb"] = bool(config.GROQ_API_KEY)
        # Count on disk, not from the column. absorb writes the column once at
        # the end, so a 15-minute run showed a frozen number the whole time;
        # articles land one at a time, so the filesystem is the live counter.
        r["articles"] = repos.count_articles(r["wiki_root"])
        r["queue_new"], r["absorbed"] = repos.queue_remaining(r)
        r["last_run"] = (repos.runs(r["id"], 1) or [None])[0]
    return {"repos": rows, "groq_configured": bool(config.GROQ_API_KEY),
            "max_tracked": config.REPO_MAX_TRACKED}


@router.get("/{owner}/{name}")
def get_repo(owner: str, name: str, _email: str = Depends(current_user)):
    rid = f"{owner}/{name}".lower()
    row = repos.get(rid)
    if not row:
        raise HTTPException(404, f"no such repo: {rid}")
    row["running_step"] = repos.busy().get(rid)
    row["runs"] = repos.runs(rid, 10)
    return row


@router.post("/check")
def check(payload: dict = Body(...), _email: str = Depends(current_user)):
    """Reachability probe. Never clones — this is what confirms a repo is public
    before any bytes hit the disk."""
    try:
        return repos.probe(payload.get("url", ""))
    except Exception as e:
        _fail(e)


@router.post("/sweep", status_code=202)
def sweep(bg: BackgroundTasks, _email: str = Depends(current_user)):
    """Pull and re-ingest every tracked repo. Free — never absorbs.

    Declared before the /{owner}/{name} routes so "sweep" is not read as an
    owner. It is the same function the scheduled timer calls.
    """
    def job():
        try:
            from feeders.github import sync as gh
            gh.run()
        except Exception:
            log.exception("repo sweep failed")

    bg.add_task(job)
    return {"accepted": True, "step": "sweep"}


@router.post("", status_code=201)
def add_repo(payload: dict = Body(...), _email: str = Depends(current_user)):
    try:
        return repos.add(payload.get("url", ""), payload.get("branch"),
                         payload.get("token", ""))
    except Exception as e:
        _fail(e)


@router.delete("/{owner}/{name}")
def delete_repo(owner: str, name: str, keep_wiki: bool = True,
                _email: str = Depends(current_user)):
    try:
        return repos.remove(f"{owner}/{name}".lower(), keep_wiki=keep_wiki)
    except Exception as e:
        _fail(e)


@router.get("/{owner}/{name}/commits")
def list_commits(owner: str, name: str, limit: int = 50,
                 _email: str = Depends(current_user)):
    try:
        return {"commits": repos.commits(f"{owner}/{name}".lower(), limit)}
    except Exception as e:
        _fail(e)


@router.get("/{owner}/{name}/queue")
def get_queue(owner: str, name: str, _email: str = Depends(current_user)):
    try:
        return repos.queue(f"{owner}/{name}".lower())
    except Exception as e:
        _fail(e)


@router.get("/{owner}/{name}/runs")
def list_runs(owner: str, name: str, limit: int = 20,
              _email: str = Depends(current_user)):
    return {"runs": repos.runs(f"{owner}/{name}".lower(), limit)}


def _spawn(bg: BackgroundTasks, rid: str, step: str, **kwargs):
    """Claim the job synchronously so a duplicate request 409s immediately
    rather than being accepted and racing in the background."""
    row = repos.get(rid)
    if not row:
        raise HTTPException(404, f"no such repo: {rid}")
    if repos.busy().get(rid):
        raise HTTPException(409, f"{rid} is already running {repos.busy()[rid]}")

    def job():
        try:
            repos.run_step(rid, step, **kwargs)
            if step == "absorb":
                corpus.sync()  # make the new articles searchable now, not in 15 min
            if step in ("ingest", "absorb"):
                # Both write into the durable wiki: absorb writes articles,
                # ingest writes the manifest twin that is the drift baseline.
                # Push on either, so nothing durable lives only on this
                # container's disk. No-op without S3.
                try:
                    storage.push()
                except Exception:
                    log.exception("s3 push failed; the work is still on disk")
        except Exception:
            log.exception("%s: %s failed", rid, step)

    bg.add_task(job)
    return {"accepted": True, "repo": rid, "step": step}


@router.post("/{owner}/{name}/clone", status_code=202)
def clone(owner: str, name: str, bg: BackgroundTasks,
          _email: str = Depends(current_user)):
    return _spawn(bg, f"{owner}/{name}".lower(), "clone")


@router.post("/{owner}/{name}/graph", status_code=202)
def graph(owner: str, name: str, bg: BackgroundTasks,
          _email: str = Depends(current_user)):
    return _spawn(bg, f"{owner}/{name}".lower(), "graph")


@router.post("/{owner}/{name}/ingest", status_code=202)
def ingest(owner: str, name: str, bg: BackgroundTasks,
           payload: dict = Body(default={}), _email: str = Depends(current_user)):
    commit = (payload or {}).get("commit")
    if commit:
        try:
            repos.check_sha(commit)
        except repos.Invalid as e:
            raise HTTPException(400, str(e))
    return _spawn(bg, f"{owner}/{name}".lower(), "ingest", commit=commit)


@router.post("/{owner}/{name}/absorb", status_code=202)
def absorb(owner: str, name: str, bg: BackgroundTasks,
           payload: dict = Body(default={}), _email: str = Depends(current_user)):
    if not config.GROQ_API_KEY:
        raise HTTPException(409, "GROQ_API_KEY is not set — absorb is the only paid step")
    p = payload or {}
    return _spawn(bg, f"{owner}/{name}".lower(), "absorb",
                  limit=int(p.get("limit", 5)), only=p.get("only"),
                  kind=p.get("kind", "code_package"),
                  since=int(p["since"]) if p.get("since") else None)


@router.post("/{owner}/{name}/sync", status_code=202)
def sync(owner: str, name: str, bg: BackgroundTasks,
         _email: str = Depends(current_user)):
    """clone → graph → ingest. Stops before absorb, deliberately: the free path
    should be one button, and the paid one should never be implicit."""
    rid = f"{owner}/{name}".lower()
    if not repos.get(rid):
        raise HTTPException(404, f"no such repo: {rid}")
    if repos.busy().get(rid):
        raise HTTPException(409, f"{rid} is already running {repos.busy()[rid]}")

    def job():
        try:
            repos.run_sync(rid)
        except Exception:
            log.exception("%s: sync failed", rid)

    bg.add_task(job)
    return {"accepted": True, "repo": rid, "step": "sync"}
