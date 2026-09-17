"""POST /internal/sync — lets CI ping the brain after a wiki PR merges.
An accelerator only: the 15-minute loop guarantees convergence without it."""
from fastapi import APIRouter, HTTPException, Request

from .. import config, corpus

router = APIRouter()


@router.post("/internal/sync")
def sync_now(request: Request):
    auth = request.headers.get("authorization", "")
    if not config.SYNC_TOKEN or auth != f"Bearer {config.SYNC_TOKEN}":
        raise HTTPException(401, "bad sync token")
    return corpus.sync()
