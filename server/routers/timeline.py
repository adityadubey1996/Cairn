from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import timeline
from ..auth import current_user

router = APIRouter(prefix="/api/timeline")


@router.get("")
def list_timeline(project_id: str, _email: str = Depends(current_user)):
    return timeline.list_timeline(project_id)
