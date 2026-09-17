"""Raw-source viewing: exchange a citation's path@etag for a short-lived
presigned S3 URL, minted per click and never stored — old conversations keep
working because the link is generated at read time, not at answer time.

Returns JSON {url} for frontend fetches with Bearer auth, which cannot follow
redirects cross-origin into S3. For top-level browser navigations (which send
Accept: text/html), returns a 302 redirect so middle-click works in dev mode.
Under google auth a bare navigation still 401s at the dependency, which is
unavoidable without cookies.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse

from .. import sources, storage
from ..auth import current_user

router = APIRouter(prefix="/api/sources")

_ETAG = re.compile(r"[0-9a-f]{7,40}")


@router.get("")
def list_sources(project_id: str, q: str = "", kind: str | None = None,
                 status: str | None = None, connection_id: str | None = None,
                 group: str | None = None, cursor: str = "", limit: int = 200,
                 _email: str = Depends(current_user)):
    if status not in (None, "", "ok", "failed"):
        raise HTTPException(400, "status must be 'ok' or 'failed'")
    return sources.list_sources(project_id, q=q, kind=kind, status=status or None,
                                connection_id=connection_id or None,
                                group=group or None,
                                limit=max(1, min(limit, 500)), cursor=cursor)


@router.get("/failures")
def failures(project_id: str, kind: str | None = None,
             _email: str = Depends(current_user)):
    """Why the failed rows failed, grouped and counted."""
    return {"reasons": sources.failure_reasons(project_id, kind)}


@router.get("/groups")
def list_source_groups(project_id: str, kind: str | None = None, q: str = "",
                       status: str | None = None, connection_id: str | None = None,
                       _email: str = Depends(current_user)):
    """The groups at one level of the drill-down, biggest first.

    No `kind`: the connectors this project has. With one: that connector's own
    unit — a Chat space, a Drive owner.
    """
    if status not in (None, "", "ok", "failed"):
        raise HTTPException(400, "status must be 'ok' or 'failed'")
    return sources.list_groups(project_id, kind=kind or None, q=q, status=status or None,
                               connection_id=connection_id or None)


@router.get("/folders")
def list_source_folders(project_id: str, kind: str, folder: str = "",
                        q: str = "", status: str | None = None,
                        connection_id: str | None = None,
                        _email: str = Depends(current_user)):
    """One level of a connector's folder tree: the children of `folder`.

    Files sitting directly in `folder` are not here — fetch those from the row
    list with group=<folder>, which is already an exact folder match.
    """
    if status not in (None, "", "ok", "failed"):
        raise HTTPException(400, "status must be 'ok' or 'failed'")
    return sources.list_subfolders(project_id, kind=kind, folder=folder, q=q,
                                   status=status or None,
                                   connection_id=connection_id or None)


# Registered before /{source_id}/articles so "queue" can never be read as a
# source id — FastAPI matches in declaration order.
@router.post("/queue")
def queue_sources(payload: dict = Body(...), _email: str = Depends(current_user)):
    """Mark or unmark sources for a wiki write-up. Free and reversible — the
    paid step is POST /api/pipeline/wiki-queue/run."""
    project_id = (payload or {}).get("project_id") or ""
    ids = (payload or {}).get("ids") or []
    if not project_id:
        raise HTTPException(400, "project_id is required")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "ids must be a list of source ids")
    if len(ids) > 5000:
        raise HTTPException(400, "too many ids in one request")
    changed = sources.set_queued(project_id, ids, bool(payload.get("queued", True)))
    return {"changed": changed, "queued": len(sources.queued(project_id))}


@router.post("/by-url")
def sources_by_url(payload: dict = Body(...), _email: str = Depends(current_user)):
    """Which of these URLs we hold a fetched copy of. POST, not GET: a
    transcript can mention dozens of URLs and they do not fit a query string."""
    project_id = (payload or {}).get("project_id") or ""
    urls = (payload or {}).get("urls") or []
    if not project_id:
        raise HTTPException(400, "project_id is required")
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        raise HTTPException(400, "urls must be a list of strings")
    if len(urls) > 500:
        raise HTTPException(400, "too many urls in one request")
    return sources.get_by_urls(project_id, urls)


@router.get("/{source_id}/articles")
def source_articles(source_id: str, project_id: str,
                    _email: str = Depends(current_user)):
    try:
        return sources.articles_citing(source_id, project_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


def _guard(path: str, etag: str) -> None:
    """Shared by both readers of a stored source.

    A CITATION passes the etag it was written with, and a mismatch is the whole
    point: the answer quoted a version of this file that no longer exists, and
    silently serving the current one would make the quote look supported when
    it is not.

    The Sources browser passes none. It is asking for whatever is there now, so
    there is no earlier version to have drifted from — requiring an etag there
    just meant the button could not work at all, since brain_sources stores the
    feeder's content sha and S3's etag is a different, opaque value.
    """
    if not path.startswith("sources/") or ".." in path:
        raise HTTPException(400, "path must be a sources/ file")
    if etag and not _ETAG.fullmatch(etag):
        raise HTTPException(400, "etag must be 7-40 hex chars")
    try:
        current = storage.head_etag(path)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    if current is None:
        raise HTTPException(404, "source not found in S3")
    if etag and not current.startswith(etag):
        raise HTTPException(
            409, f"source changed since this citation was written "
                 f"(cited {etag}, current {current[:8]})")


@router.get("/content")
def source_content(path: str, etag: str = "", _email: str = Depends(current_user)):
    """The stored markdown itself, for rendering inside the app.

    Separate from /view, which hands back a presigned URL the browser follows
    out of the app. This one keeps the reader on the page.
    """
    _guard(path, etag)
    text, truncated = storage.read_text(path)
    return {"path": path, "text": text, "truncated": truncated}


@router.get("/view")
def view_source(path: str, etag: str = "", _email: str = Depends(current_user),
                accept: str = Header("")):
    """A short-lived presigned URL for opening the raw file outside the app."""
    _guard(path, etag)
    url = storage.presigned_url(path)
    if "text/html" in accept:
        return RedirectResponse(url, status_code=302)
    return {"url": url}
