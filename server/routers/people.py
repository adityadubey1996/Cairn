from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import people
from ..auth import current_user

router = APIRouter(prefix="/api/people")


@router.get("")
def list_people(project_id: str, email: str = Depends(current_user)):
    return people.list_people(project_id, email)


@router.get("/{person_id}/events")
def person_events(person_id: str, project_id: str, email: str = Depends(current_user)):
    return people.person_events(project_id, person_id, email)
