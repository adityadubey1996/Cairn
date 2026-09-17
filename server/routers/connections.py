"""One card per actual connection — see server/connections.py for how a
github brain_repos row and a brain_connector_connections row unify here."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

from .. import connections
from ..auth import current_user

router = APIRouter(prefix="/api/connections")


def _fail(e: Exception):
    if isinstance(e, connections.Invalid):
        raise HTTPException(400, str(e))
    if isinstance(e, KeyError):
        raise HTTPException(404, str(e).strip("'"))
    raise HTTPException(502, str(e)[:2000])


@router.get("")
def list_connections(project_id: str, _email: str = Depends(current_user)):
    return connections.list_connections(project_id)


@router.post("", status_code=201)
def create_connection(payload: dict = Body(...), _email: str = Depends(current_user)):
    try:
        return connections.create(
            payload.get("projectId", ""), payload.get("kind", ""),
            payload.get("name", ""), payload.get("config"))
    except Exception as e:
        _fail(e)


@router.post("/{connection_id:path}/upload")
async def upload_files(connection_id: str, files: list[UploadFile] = File(...),
                       _email: str = Depends(current_user)):
    try:
        payload = [(f.filename or "", await f.read()) for f in files]
        n = connections.stage_upload(connection_id, payload)
        return {"staged": n}
    except Exception as e:
        _fail(e)


@router.post("/{connection_id:path}/sync")
def sync_connection(connection_id: str, full: bool = False,
                    _email: str = Depends(current_user)):
    try:
        return connections.sync(connection_id, full=full)
    except Exception as e:
        _fail(e)


@router.delete("/{connection_id:path}", status_code=204)
def remove_connection(connection_id: str, _email: str = Depends(current_user)):
    try:
        connections.remove(connection_id)
    except Exception as e:
        _fail(e)
