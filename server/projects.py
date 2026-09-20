"""Projects: the hard-isolation boundary every other V2 table scopes to.

No "all projects" read exists anywhere — a project_id is required wherever
one of these ids is used to filter another table.
"""
from __future__ import annotations

import re

from .db import connect


class Invalid(ValueError):
    """Rejected at the trust boundary. Maps to 400."""


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or "project"
    return s[:60]


FIELDS = "id, name, created_at"


def list_projects() -> list[dict]:
    with connect() as c:
        projects = c.execute(f"SELECT {FIELDS} FROM brain_projects ORDER BY created_at").fetchall()
        counts = c.execute(
            "SELECT project_id, count(*) AS n FROM brain_connector_connections "
            "GROUP BY project_id").fetchall()
        repo_counts = c.execute(
            "SELECT project_id, count(*) AS n FROM brain_repos "
            "WHERE project_id IS NOT NULL GROUP BY project_id").fetchall()
        source_counts = c.execute(
            "SELECT project_id, count(*) AS n FROM brain_sources "
            "WHERE status = 'ok' GROUP BY project_id").fetchall()
    connections = {r["project_id"]: r["n"] for r in counts}
    for r in repo_counts:
        connections[r["project_id"]] = connections.get(r["project_id"], 0) + r["n"]
    sources = {r["project_id"]: r["n"] for r in source_counts}
    return [{**p, "connectionCount": connections.get(p["id"], 0),
             "sourceCount": sources.get(p["id"], 0)} for p in projects]


def get(project_id: str) -> dict | None:
    with connect() as c:
        return c.execute(f"SELECT {FIELDS} FROM brain_projects WHERE id = %s",
                         (project_id,)).fetchone()


def create(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise Invalid("name is required")
    with connect() as c:
        base = _slug(name)
        pid = base
        n = 1
        while c.execute("SELECT 1 FROM brain_projects WHERE id = %s", (pid,)).fetchone():
            n += 1
            pid = f"{base}-{n}"
        return c.execute(
            "INSERT INTO brain_projects (id, name) VALUES (%s, %s) "
            f"RETURNING {FIELDS}", (pid, name)).fetchone()


def rename(project_id: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise Invalid("name is required")
    with connect() as c:
        row = c.execute(
            f"UPDATE brain_projects SET name = %s WHERE id = %s RETURNING {FIELDS}",
            (name, project_id)).fetchone()
    if not row:
        raise KeyError(f"no such project: {project_id}")
    return row


def delete(project_id: str) -> None:
    with connect() as c:
        row = c.execute("DELETE FROM brain_projects WHERE id = %s RETURNING id",
                         (project_id,)).fetchone()
    if not row:
        raise KeyError(f"no such project: {project_id}")


def ensure_default() -> str:
    """The id of some project, creating one bootstrap project if none exist yet
    (fresh install, or a feeder run before onboarding created one). Never picks
    among several — callers that need a specific project must say which."""
    with connect() as c:
        row = c.execute("SELECT id FROM brain_projects ORDER BY created_at LIMIT 1").fetchone()
        if row:
            return row["id"]
        # DO UPDATE (a no-op) rather than DO NOTHING so RETURNING always gives a
        # row back, including the rare race where another call won the insert.
        return c.execute(
            "INSERT INTO brain_projects (id, name) VALUES ('default', 'My first project') "
            "ON CONFLICT (id) DO UPDATE SET name = brain_projects.name "
            "RETURNING id").fetchone()["id"]


def wiki_root(project_id: str):
    from . import config
    if project_id == ensure_default() and config.WIKI_ROOTS:
        return config.WIKI_ROOTS[0]
    # Database IDs are validated by lookup; hash also makes filesystem isolation
    # independent of any future change to the project ID format.
    import hashlib
    key = hashlib.sha256(project_id.encode()).hexdigest()[:16]
    return config.REPO_WIKI_DIR / 'projects' / key / 'wiki'
