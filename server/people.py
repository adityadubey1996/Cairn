"""GET /api/people + /api/people/{id}/events — derived from source authorship
the feeders already capture (Drive owners, Chat/WhatsApp/LinkedIn senders).

No connector captures @mentions or assignments today (no Jira/Gmail feeder
exists), so "mentioned"/"assigned" events never appear here rather than
being invented — see docs/superpowers/plans/2026-09-14-v2-handover.md.
"""
from __future__ import annotations

import re

from .db import connect

_ROLE_OF_KIND = {"gdrive": "authored", "gchat": "sent", "whatsapp": "sent",
                 "linkedin": "sent"}


def _pid(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "unknown"


def _owner_name(email: str) -> str:
    local = email.split("@", 1)[0]
    return local.replace(".", " ").replace("_", " ").title() or email


def list_people(project_id: str, owner_email: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT kind, authors, scraped_at FROM brain_sources "
            "WHERE project_id = %s AND status = 'ok' AND jsonb_array_length(authors) > 0",
            (project_id,)).fetchall()

    by_id: dict[str, dict] = {}
    for r in rows:
        if r["kind"] not in _ROLE_OF_KIND:
            continue
        for name in r["authors"]:
            pid = _pid(name)
            p = by_id.setdefault(
                pid, {"id": pid, "name": name, "lastActiveAt": r["scraped_at"], "eventCount": 0})
            p["eventCount"] += 1
            p["lastActiveAt"] = max(p["lastActiveAt"], r["scraped_at"])

    owner_name = _owner_name(owner_email)
    owner = by_id.pop(_pid(owner_name), {"eventCount": 0, "lastActiveAt": None})
    people = [{"id": "me", "name": owner_name, "isOwner": True,
              "lastActiveAt": owner["lastActiveAt"], "eventCount": owner["eventCount"]}]
    people += sorted(by_id.values(), key=lambda p: p["lastActiveAt"], reverse=True)
    for p in people:
        p["initials"] = "".join(w[0] for w in p["name"].split()[:2]).upper() or "?"
        p["lastActiveAt"] = p["lastActiveAt"].isoformat() if p["lastActiveAt"] else None
    return people


def person_events(project_id: str, person_id: str, owner_email: str) -> list[dict]:
    target = _pid(_owner_name(owner_email)) if person_id == "me" else person_id
    with connect() as c:
        rows = c.execute(
            "SELECT id, kind, name, authors, scraped_at, project_id "
            "FROM brain_sources WHERE project_id = %s AND status = 'ok' "
            "AND jsonb_array_length(authors) > 0 "
            "ORDER BY scraped_at DESC", (project_id,)).fetchall()
    out = []
    for r in rows:
        role = _ROLE_OF_KIND.get(r["kind"])
        if not role or not any(_pid(a) == target for a in r["authors"]):
            continue
        verb = "Authored" if role == "authored" else "Sent"
        out.append({
            "id": r["id"], "role": role, "kind": r["kind"],
            "text": f"{verb} {r['name']}",
            "at": r["scraped_at"].isoformat(), "sourceId": r["id"],
        })
    return out
