"""Google token status and disconnect.

The consent flow itself lives in routers/oauth.py, which serves any provider
declaring an OAuthSpec — this file is only what is specific to Google's stored
token.
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from feeders.google import auth as gauth

from ..auth import current_user

router = APIRouter(prefix="/api/google")



def _redirect_uri(request: Request) -> str:
    """Must match a URI registered on the OAuth client byte for byte.
    # ponytail: trusts request.base_url. Behind a TLS-terminating proxy set
    # --forwarded-allow-ips so the scheme comes back https, or this builds an
    # http:// URI Google will reject.
    """
    return str(request.base_url).rstrip("/") + "/api/google/callback"


@router.get("/status")
def status(_email: str = Depends(current_user)):
    return {"connected": gauth.connected(), "account": gauth.account(),
            "scopes": list(gauth.SCOPES)}


@router.post("/disconnect")
def disconnect(_email: str = Depends(current_user)):
    gauth.disconnect()
    return {"connected": False}


def config_ready() -> bool:
    from .. import config
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)
