"""Projects CRUD. Thin — validation and queries live in server/projects.py."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import projects
from ..auth import current_user

router = APIRouter(prefix="/api/projects")


def _fail(e: Exception):
    if isinstance(e, projects.Invalid):
        raise HTTPException(400, str(e))
    if isinstance(e, KeyError):
        raise HTTPException(404, str(e).strip("'"))
    raise HTTPException(502, str(e)[:2000])


@router.get("")
def list_projects(_email: str = Depends(current_user)):
    return projects.list_projects()


@router.post("", status_code=201)
def create_project(payload: dict = Body(...), _email: str = Depends(current_user)):
    try:
        return projects.create(payload.get("name", ""))
    except Exception as e:
        _fail(e)


@router.patch("/{project_id}")
def rename_project(project_id: str, payload: dict = Body(...),
                   _email: str = Depends(current_user)):
    try:
        return projects.rename(project_id, payload.get("name", ""))
    except Exception as e:
        _fail(e)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, _email: str = Depends(current_user)):
    try:
        projects.delete(project_id)
    except Exception as e:
        _fail(e)
