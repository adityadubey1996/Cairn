"""Connector health + manual trigger — the screen this router feeds shows
every registered feeder, whether it's configured, and its run history."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config
from .. import connectors, jobs, projects, repos, sources, llm
from ..db import connect
from ..auth import current_user
from feeders.browser import sessions as browser_sessions

router = APIRouter(prefix="/api/connectors")


@router.get("")
def list_connectors(_email: str = Depends(current_user)):
    return connectors.health()


@router.get("/preflight")
def preflight(_email: str = Depends(current_user)):
    """What each connector still needs on THIS machine. Declared before the
    /{connector_id} routes so "preflight" is never read as a connector id."""
    return connectors.preflight()


@router.post("/{connector_id}/run")
def run_connector(connector_id: str, project_id: str = '', _email: str = Depends(current_user)):
    try:
        if connector_id not in {c.id for c in connectors.REGISTRY}:
            raise ValueError('unknown connector')
        project_id = project_id or projects.ensure_default()
        if connector_id == 'github':
            return {'runs': [jobs.submit('github', project_id, connection_id=r['id'])
                             for r in repos.list_repos() if r['project_id'] == project_id]}
        return jobs.submit(connector_id, project_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"connector run failed: {e}")


@router.post("/view-auth")
def view_auth(payload: dict = Body(default={}), _email: str = Depends(current_user)):
    """Gate for the live-browser iframe: it exposes the logged-in WhatsApp/
    LinkedIn session, so the panel reveals it only after this password matches
    config.BROWSER_VIEW_PASSWORD (set in .env)."""
    if not config.BROWSER_VIEW_PASSWORD:
        raise HTTPException(403, "view password not set — add BROWSER_VIEW_PASSWORD to .env")
    pw = (payload or {}).get("password", "")
    if hmac.compare_digest(str(pw), config.BROWSER_VIEW_PASSWORD):
        return {"ok": True}
    raise HTTPException(403, "wrong password")


@router.post("/{connector_id}/absorb")
def absorb_connector(connector_id: str, project_id: str = '', _email: str = Depends(current_user)):
    """Queue changed, eligible sources for the project's configured model.

    Returns a durable run handle immediately; the worker performs absorption.
    """
    try:
        if connector_id not in {c.id for c in connectors.REGISTRY}:
            raise ValueError('unknown connector')
        project_id = project_id or projects.ensure_default()
        with connect() as c:
            rows = c.execute("SELECT id FROM brain_sources WHERE project_id=%s AND kind=%s "
                             "AND status='ok' AND absorption_policy!='exclude' "
                             'AND absorbed_sha IS DISTINCT FROM sha', (project_id, connector_id)).fetchall()
        ids = [r['id'] for r in rows]
        if not ids:
            return {'queued': 0}
        llm.absorb_env()
        sources.set_queued(project_id, ids, True)
        return jobs.submit('wiki', project_id, absorb=True, ids=ids)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"absorb failed: {e}")


@router.get("/{connector_id}/session")
async def session_status(connector_id: str, _email: str = Depends(current_user)):
    """Login state of a browser connector's Steel session. Only whatsapp and
    linkedin are browser-backed; every other connector 404s here."""
    if connector_id not in browser_sessions.PLATFORMS:
        raise HTTPException(404, f"{connector_id} is not a browser connector")
    return await browser_sessions.status(connector_id)


@router.post("/{connector_id}/session")
async def session_login(connector_id: str, _email: str = Depends(current_user)):
    """Ensure a session and point it at the platform login page; the returned
    viewer_url is what the UI embeds as the login iframe."""
    if connector_id not in browser_sessions.PLATFORMS:
        raise HTTPException(404, f"{connector_id} is not a browser connector")
    try:
        return await browser_sessions.open_login(connector_id)
    except Exception as e:
        raise HTTPException(503, f"steel unavailable: {e}")
