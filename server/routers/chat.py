"""Conversations CRUD + the SSE message endpoint that runs the whole chain:
condense -> assemble -> framing -> synthesize."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..application.synthesize import run_pipeline
from ..auth import current_user
from ..db import connect

router = APIRouter(prefix="/api/conversations")


class NewMessage(BaseModel):
    content: str


@router.get("")
def list_conversations(project_id: str, email: str = Depends(current_user)):
    with connect() as c:
        return c.execute(
            "SELECT id, title, created_at FROM brain_conversations "
            "WHERE user_email = %s AND project_id = %s ORDER BY created_at DESC",
            (email, project_id)).fetchall()


@router.post("", status_code=201)
def create_conversation(payload: dict = Body(default={}), email: str = Depends(current_user)):
    project_id = payload.get("projectId")
    if not project_id:
        raise HTTPException(400, "projectId is required")
    with connect() as c:
        return c.execute(
            "INSERT INTO brain_conversations (user_email, project_id) VALUES (%s, %s) "
            "RETURNING id, title, created_at", (email, project_id)).fetchone()


def _owned(c, cid: uuid.UUID, email: str):
    conv = c.execute(
        "SELECT id, title, project_id FROM brain_conversations WHERE id = %s "
        "AND user_email = %s", (cid, email)).fetchone()
    if not conv:
        raise HTTPException(404, "no such conversation")
    return conv


@router.get("/{cid}/messages")
def list_messages(cid: uuid.UUID, email: str = Depends(current_user)):
    with connect() as c:
        _owned(c, cid, email)
        return c.execute(
            "SELECT id, role, content, citations, created_at FROM brain_messages "
            "WHERE conversation_id = %s ORDER BY created_at", (cid,)).fetchall()


@router.delete("/{cid}", status_code=204)
def delete_conversation(cid: uuid.UUID, email: str = Depends(current_user)):
    with connect() as c:
        _owned(c, cid, email)
        c.execute("DELETE FROM brain_conversations WHERE id = %s", (cid,))


@router.post("/{cid}/messages")
def post_message(cid: uuid.UUID, body: NewMessage,
                 email: str = Depends(current_user)):
    with connect() as c:
        conv = _owned(c, cid, email)
        history = c.execute(
            "SELECT role, content FROM brain_messages WHERE conversation_id = %s "
            "ORDER BY created_at", (cid,)).fetchall()
        c.execute(
            "INSERT INTO brain_messages (conversation_id, role, content) "
            "VALUES (%s, 'user', %s)", (cid, body.content))
        if conv["title"] == "New conversation":
            c.execute("UPDATE brain_conversations SET title = %s WHERE id = %s",
                      (body.content[:60], cid))
    turns = [{"role": m["role"], "content": m["content"]} for m in history]
    return StreamingResponse(run_pipeline(cid, turns, body.content, conv["project_id"]),
                             media_type="text/event-stream")
