"""Auth per the locked decision: AUTH_MODE=dev bypasses everything (localhost);
AUTH_MODE=google verifies a Google ID token entirely server-side — JWKS
signature, aud == GOOGLE_CLIENT_ID, iss, email_verified, and hd ==
ALLOWED_DOMAIN when set — then mints the brain's own short-lived session JWT.
"""
from __future__ import annotations

import time

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import config

SESSION_TTL = 12 * 3600
_jwks = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")

router = APIRouter(prefix="/api/auth")


def verify_google_id_token(id_token: str) -> str:
    """Return the verified email, or raise ValueError."""
    try:
        key = _jwks.get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(id_token, key, algorithms=["RS256"],
                            audience=config.GOOGLE_CLIENT_ID)
    except jwt.PyJWTError as e:
        raise ValueError(f"token verification failed: {e}")
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise ValueError("wrong issuer")
    if not claims.get("email_verified"):
        raise ValueError("email not verified")
    if config.ALLOWED_DOMAIN and claims.get("hd") != config.ALLOWED_DOMAIN:
        raise ValueError("account outside the allowed domain")
    return claims["email"]


def mint_session(email: str) -> str:
    return jwt.encode({"sub": email, "exp": int(time.time()) + SESSION_TTL},
                      config.SESSION_SECRET, algorithm="HS256")


def current_user(request: Request) -> str:
    if config.AUTH_MODE == "dev":
        return "dev@localhost"
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return jwt.decode(header[7:], config.SESSION_SECRET,
                          algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired session")


class GoogleLogin(BaseModel):
    id_token: str


@router.post("/google")
def google_login(body: GoogleLogin):
    if config.AUTH_MODE == "dev":
        return {"token": mint_session("dev@localhost"), "email": "dev@localhost"}
    try:
        email = verify_google_id_token(body.id_token)
    except ValueError as e:
        raise HTTPException(401, str(e))
    return {"token": mint_session(email), "email": email}
