from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from .. import llm, settings
from ..auth import current_user

router = APIRouter(prefix="/api/settings")


@router.get("/provider")
def get_settings(_email: str = Depends(current_user)):
    return settings.get_settings()


@router.put("/provider")
def save_settings(payload: dict = Body(...), _email: str = Depends(current_user)):
    return settings.save(payload)


@router.post("/provider/test")
def test_provider(payload: dict = Body(...), _email: str = Depends(current_user)):
    return settings.test_provider(payload)


@router.get("/ollama")
def ollama_status(_email: str = Depends(current_user)):
    """Installed local chat models, and what to pull when there are none."""
    return llm.ollama_status()
