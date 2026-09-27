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

from .. import config, corpus, repos, storage, llm, projects, jobs
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
def list_repos(project_id: str | None = None, _email: str = Depends(current_user)):
    running = repos.busy()
    active = repos.active_jobs()
    rows = repos.list_repos()
    if project_id:
        rows = [r for r in rows if r['project_id'] == project_id]
    try:
        llm.absorb_env()
        can_absorb = True
    except llm.NoProvider:
        can_absorb = False
    for r in rows:
        r["running_step"] = running.get(r["id"])
        r['active_run'] = active.get(r['id'])
        r["can_absorb"] = can_absorb
        # Count on disk, not from the column. absorb writes the column once at
        # the end, so a 15-minute run showed a frozen number the whole time;
        # articles land one at a time, so the filesystem is the live counter.
        r["articles"] = repos.count_articles(r["wiki_root"])
        r["queue_new"], r["absorbed"] = repos.queue_remaining(r)
        r["last_run"] = (repos.runs(r["id"], 1) or [None])[0]
    return {"repos": rows, "groq_configured": can_absorb, 'model_configured': can_absorb,
            "max_tracked": config.REPO_MAX_TRACKED}


@router.post("/check")
def check(payload: dict = Body(...), _email: str = Depends(current_user)):
    """Reachability probe. Never clones — this is what confirms a repo is public
    before any bytes hit the disk."""
    try:
        return repos.probe(payload.get("url", ""))
    except Exception as e:
        _fail(e)


@router.post("/sweep", status_code=202)
def sweep(bg: BackgroundTasks, project_id: str | None = None, _email: str = Depends(current_user)):
    """Pull and re-ingest every tracked repo. Free — never absorbs.

    Declared before the /{repo_id:path} routes so "sweep" is not read as a
    repo id. It is the same function the scheduled timer calls.
    """
    results = [jobs.submit('github', r['project_id'], connection_id=r['id']) for r in repos.list_repos()
               if not project_id or r['project_id'] == project_id]
    return {"accepted": True, "step": "sweep", 'runs': results}


@router.post("", status_code=201)
def add_repo(payload: dict = Body(...), _email: str = Depends(current_user)):
    try:
        project_id = payload.get('project_id') or projects.ensure_default()
        if not projects.get(project_id):
            raise HTTPException(404, 'project not found')
        return repos.add(payload.get("url", ""), payload.get("branch"),
                         payload.get("token", ""), project_id=project_id)
    except HTTPException:
        raise
    except Exception as e:
        _fail(e)


@router.delete("/{repo_id:path}")
def delete_repo(repo_id: str, keep_wiki: bool = True,
                _email: str = Depends(current_user)):
    try:
        return repos.remove(repo_id.lower(), keep_wiki=keep_wiki)
    except Exception as e:
        _fail(e)


@router.get("/{repo_id:path}/commits")
def list_commits(repo_id: str, limit: int = 50,
                 _email: str = Depends(current_user)):
    try:
        return {"commits": repos.commits(repo_id.lower(), limit)}
    except Exception as e:
        _fail(e)


@router.get("/{repo_id:path}/queue")
def get_queue(repo_id: str, _email: str = Depends(current_user)):
    try:
        return repos.queue(repo_id.lower())
    except Exception as e:
        _fail(e)


@router.get("/{repo_id:path}/runs")
def list_runs(repo_id: str, limit: int = 20,
              _email: str = Depends(current_user)):
    return {"runs": repos.runs(repo_id.lower(), limit)}


def _spawn(bg: BackgroundTasks, rid: str, step: str, **kwargs):
    """Claim the job synchronously so a duplicate request 409s immediately
    rather than being accepted and racing in the background."""
    row = repos.get(rid)
    if not row:
        raise HTTPException(404, f"no such repo: {rid}")
    result = jobs.submit('github', row['project_id'], connection_id=rid,
                         absorb=step == 'absorb', repo_step=step, options=kwargs)
    return {"accepted": True, "repo": rid, "step": step, 'runId': result['run_id'],
            'status': result['status']}


@router.post("/{repo_id:path}/clone", status_code=202)
def clone(repo_id: str, bg: BackgroundTasks,
          _email: str = Depends(current_user)):
    return _spawn(bg, repo_id.lower(), "clone")


@router.post("/{repo_id:path}/graph", status_code=202)
def graph(repo_id: str, bg: BackgroundTasks,
          _email: str = Depends(current_user)):
    return _spawn(bg, repo_id.lower(), "graph")


@router.post("/{repo_id:path}/ingest", status_code=202)
def ingest(repo_id: str, bg: BackgroundTasks,
           payload: dict = Body(default={}), _email: str = Depends(current_user)):
    commit = (payload or {}).get("commit")
    if commit:
        try:
            repos.check_sha(commit)
        except repos.Invalid as e:
            raise HTTPException(400, str(e))
    return _spawn(bg, repo_id.lower(), "ingest", commit=commit)


@router.post("/{repo_id:path}/absorb", status_code=202)
def absorb(repo_id: str, bg: BackgroundTasks,
           payload: dict = Body(default={}), _email: str = Depends(current_user)):
    try:
        from .. import llm
        llm.absorb_env()
    except Exception as e:
        raise HTTPException(409, str(e))
    p = payload or {}
    import re
    if p.get('kind', '') not in repos.KINDS | {''}:
        raise HTTPException(400, 'unknown document kind')
    if type(p.get('limit', 5)) is not int or not 1 <= p.get('limit', 5) <= 50:
        raise HTTPException(400, 'limit must be between 1 and 50')
    if p.get('only') is not None and (not isinstance(p['only'], list) or
            not all(isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9._/-]{1,200}', s) for s in p['only'])):
        raise HTTPException(400, 'only must contain valid unit IDs')
    if p.get('since') is not None and (type(p['since']) is not int or p['since'] < 1):
        raise HTTPException(400, 'since must be a positive commit count')
    return _spawn(bg, repo_id.lower(), "absorb",
                  limit=int(p.get("limit", 5)), only=p.get("only"),
                  kind=p.get("kind", ""),
                  since=int(p["since"]) if p.get("since") else None)


@router.post("/{repo_id:path}/sync", status_code=202)
def sync(repo_id: str, bg: BackgroundTasks,
         _email: str = Depends(current_user)):
    """clone → graph → ingest. Stops before absorb, deliberately: the free path
    should be one button, and the paid one should never be implicit."""
    rid = repo_id.lower()
    return _spawn(bg, rid, 'sync')


# Last on purpose. `{repo_id:path}` is greedy, so declared any earlier it would
# swallow ".../commits", ".../queue" and ".../runs" and answer 404 for all
# three. The path converter is what lets a nested GitLab id — four segments,
# not two — address a repo at all.
@router.get("/{repo_id:path}")
def get_repo(repo_id: str, _email: str = Depends(current_user)):
    rid = repo_id.lower()
    row = repos.get(rid)
    if not row:
        raise HTTPException(404, f"no such repo: {rid}")
    row["running_step"] = repos.busy().get(rid)
    row["runs"] = repos.runs(rid, 10)
    return row
