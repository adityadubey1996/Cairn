"""Unified connections: Health shows one card per actual connection.

GitHub already has one row per connection (brain_repos — two repos are two
rows, own credentials via their own token/url), so a github "connection" IS
its brain_repos row; nothing new is stored for it. Every other kind
(gdrive/gchat/links/whatsapp/linkedin) shares one set of process-wide
credentials today (one Google token file, one Steel session) — see
docs/superpowers/plans/2026-09-14-v2-ui-build.md's Batch 6 notes — so
multiple connections of the same kind run the same underlying feeder and
differ only in which project their output is tagged with.
"""
from __future__ import annotations

import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import automation, config, credentials, jobs, pipeline_runs, repos
from .connectors import REGISTRY
from .db import connect
from feeders.upload import sync as upload_sync

# Derived, never listed: a connector is declared once, in connectors.REGISTRY.
NON_GITHUB_KINDS = tuple(c.id for c in REGISTRY if c.id != "github")
AUTH_OF_KIND = {c.id: c.auth for c in REGISTRY}


def _spec(kind: str):
    """Looked up per call, not at import, so the registry stays the single
    source of truth for what a connection may be."""
    spec = next((c for c in REGISTRY if c.id == kind and c.id != "github"), None)
    if not spec:
        raise Invalid(f"unknown connector kind: {kind!r}")
    return spec


class Invalid(ValueError):
    """Rejected at the trust boundary. Maps to 400."""


def _is_github(connection_id: str) -> bool:
    return "/" in connection_id


def _repo_status(state: str) -> str:
    if state == "failed":
        return "error"
    if state in ("added",):
        return "never_run"
    if state in ("ready",):
        return "ok"
    return "running"  # cloning/cloned/graphed/ingested/absorbing


def _repo_as_connection(r: dict) -> dict:
    return {
        "id": r["id"], "projectId": r["project_id"], "kind": "github",
        "name": f"{r['owner']}/{r['name']}", "detail": f"GitHub · {r['branch']}",
        "status": _repo_status(r["state"]),
        "lastSyncAt": r["updated_at"].isoformat() if r["updated_at"] else None,
        "itemCount": r["articles"], "auth": "token", "error": r["last_error"],
    }


def list_connections(project_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, project_id, kind, name, config, created_at, synced_at "
            "FROM brain_connector_connections WHERE project_id = %s ORDER BY created_at",
            (project_id,)).fetchall()
        # Two accounts of the same provider have independent jobs. Legacy
        # unattributed runs cannot safely be assigned to either connection.
        last_by_connection = {r["connection_id"]: r for r in c.execute(
            "SELECT DISTINCT ON (connection_id) id, connection_id, connector_id, status, items_written, "
            "started_at, finished_at, heartbeat_at, error FROM brain_connector_runs "
            "WHERE project_id = %s AND connection_id IS NOT NULL "
            "ORDER BY connection_id, started_at DESC, id DESC", (project_id,)).fetchall()}
        repo_rows = c.execute(
            "SELECT id, owner, name, branch, state, last_error, articles, "
            "project_id, updated_at FROM brain_repos "
            "WHERE project_id = %s ORDER BY id", (project_id,)).fetchall()
        # Cumulative, from the source index — NOT the last run's items_written,
        # which is a delta. A Chat connection holding 573 space-days read as
        # "5 items" because the most recent sync happened to write five.
        items_by_connection = {r["connection_id"]: r["n"] for r in c.execute(
            "SELECT connection_id, count(*) AS n FROM brain_sources "
            "WHERE project_id = %s AND status = 'ok' AND connection_id IS NOT NULL "
            "GROUP BY connection_id",
            (project_id,)).fetchall()}

    now = datetime.now(timezone.utc)
    out = []
    for row in rows:
        last = last_by_connection.get(row["id"])
        # A run still marked `running` used to render as "ok", so a card claimed
        # synced while a sync was in flight — and a run that died mid-flight
        # claimed it forever (this table had links runs stuck since August).
        # pipeline_runs.derive_status already does exactly this arbitration,
        # falling back to started_at when nothing writes a heartbeat, which is
        # the case for every run server/runs.py starts.
        derived = pipeline_runs.derive_status(last, now) if last else None
        status = derived or "never_run"
        out.append({
            "id": row["id"], "projectId": row["project_id"], "kind": row["kind"],
            "name": row["name"],
            "detail": (row["config"] or {}).get("detail") or row["kind"],
            "status": status,
            "lastSyncAt": last["finished_at"].isoformat() if last and last["finished_at"] else None,
            # The checkpoint belongs to this connection, independently of
            # the completion time of its latest job.
            "syncedAt": row["synced_at"].isoformat() if row["synced_at"] else None,
            "itemCount": items_by_connection.get(row["id"], 0),
            "auth": AUTH_OF_KIND.get(row["kind"], "none"),
            "error": last["error"] if last else None,
            "runId": str(last["id"]) if last and status in ("queued", "running", "cancelling") else None,
        })
    for row in repo_rows:
        connection = _repo_as_connection(row)
        last = last_by_connection.get(row["id"])
        if last:
            status = pipeline_runs.derive_status(last, now)
            connection.update(status=status, error=last["error"],
                              lastSyncAt=last["finished_at"].isoformat() if last["finished_at"] else None,
                              runId=str(last["id"]) if status in ("queued", "running", "cancelling") else None)
        elif row["state"] in ("cloned", "graphed", "ingested"):
            # These are completed preparation steps, not evidence of a
            # currently running process.
            connection["status"] = "ok"
        connection["itemCount"] = items_by_connection.get(row["id"], 0)
        out.append(connection)
    return out


def create(project_id: str, kind: str, name: str, config: dict | None = None) -> dict:
    spec = _spec(kind)
    if not name or not name.strip():
        raise Invalid("name is required")
    if not project_id:
        raise Invalid('projectId is required')
    if config is not None and not isinstance(config, dict):
        raise Invalid('config must be an object')
    config = dict(config or {})
    if 'max_items' in config and (type(config['max_items']) is not int or not 0 <= config['max_items'] <= 10000):
        raise Invalid('max_items must be an integer between 0 and 10000')
    for key in ('urls', 'source_ids'):
        if key in config and (not isinstance(config[key], list) or len(config[key]) > 1000
                              or not all(isinstance(s, str) and s.strip() for s in config[key])):
            raise Invalid(f'{key} must be a list of up to 1000 nonempty strings')
    if 'query' in config and not isinstance(config['query'], str):
        raise Invalid('query must be text')
    missing = [f.label for f in spec.fields
               if f.required and not str(config.get(f.name, "")).strip()]
    if missing:
        raise Invalid(f"missing: {', '.join(missing)}")
    secret_names = {f.name for f in spec.fields if f.secret}
    public = {k: v for k, v in config.items() if k not in secret_names}
    secret = {k: v for k, v in config.items() if k in secret_names}
    cid = f"{kind}-{uuid.uuid4().hex[:8]}"
    with connect() as c:
        c.execute(
            "INSERT INTO brain_connector_connections (id, project_id, kind, name, config) "
            "VALUES (%s, %s, %s, %s, %s)",
            (cid, project_id, kind, name.strip(), json.dumps(public)))
    if secret:
        credentials.put(cid, secret)
    config = public
    return {"id": cid, "projectId": project_id, "kind": kind, "name": name.strip(),
            "detail": (config or {}).get("detail") or kind, "status": "never_run",
            "lastSyncAt": None, "itemCount": 0,
            "auth": AUTH_OF_KIND.get(kind, "none"), "error": None}


def stage_upload(connection_id: str, files: list[tuple[str, bytes]]) -> int:
    """Write each (relative_path, content) pair under this connection's
    staging area. No extraction here — this is only as slow as the upload
    itself; feeders.upload.sync.run() does the actual work later.

    The relative path comes straight from the browser, so it is untrusted
    input reaching a filesystem write. Guarded the same way
    server/repos.py:136-143's _sandboxed() guards a clone destination:
    resolve the joined path and refuse it if it no longer falls under the
    staging root, on top of rejecting any '..' segment outright.
    """
    row = _require(connection_id)
    if row["kind"] != "upload":
        raise Invalid(f"{connection_id} is not an upload connection")

    root = upload_sync.staging_dir(connection_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel_path, content in files:
        if not rel_path or ".." in Path(rel_path).parts:
            raise Invalid(f"unsafe upload path: {rel_path!r}")
        if len(content) > upload_sync.MAX_UPLOAD_BYTES:
            raise Invalid(f"{rel_path} exceeds the upload size ceiling")
        dest = (root / rel_path).resolve()
        if not dest.is_relative_to(root):
            raise Invalid(f"upload path escapes the staging area: {rel_path!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        n += 1
    return n


def _budget(policy: dict) -> dict:
    """The plan's ceilings, in the shape the job carries them. Absent keys mean
    no ceiling of this connection's own."""
    return {k: v for k, v in (('limit_units', policy.get('absorb_limit_units')),
                              ('max_tokens', policy.get('absorb_max_tokens'))) if v}


def sync(connection_id: str, full: bool = False) -> dict:
    row = repos.get(connection_id) if _is_github(connection_id) else _require(connection_id)
    if not row:
        raise KeyError(f"no such connection: {connection_id}")
    policy = automation.get(connection_id)
    absorb = automation.absorbs_on_sync(policy)
    if absorb:
        _guardrail(row['project_id'], policy)
    budget = _budget(policy) if absorb else {}
    result = jobs.submit('github' if _is_github(connection_id) else row['kind'],
                         row['project_id'], connection_id=connection_id,
                         absorb=absorb, full=full,
                         **({'budget': budget} if budget else {}))
    return {'id': connection_id, 'status': result['status'], 'runId': result['run_id']}


def _guardrail(project_id: str, policy: dict) -> None:
    """One ceiling, so a widened scope cannot quietly become an all-night run.

    Checked before the job is queued rather than inside it: the point is that
    nobody is surprised, and a run that has already started spending is too
    late to be a warning.
    """
    ceiling = policy.get('absorb_guardrail_units') or 0
    if not ceiling:
        return
    from . import sources
    waiting = len(sources.queued(project_id))
    if waiting > ceiling:
        raise Invalid(
            f"{waiting} units are queued, over this connection's ceiling of {ceiling}. "
            "Raise the ceiling, narrow the scope, or absorb by hand.")


def absorb(connection_id: str) -> dict:
    """Write up what this connection's project has queued, under its plan.

    Scheduled absorption calls this; so does a person pressing the button. The
    queue is per project rather than per connection because that is what
    sources.queued() answers and what an absorb run reads.
    """
    row = repos.get(connection_id) if _is_github(connection_id) else _require(connection_id)
    if not row:
        raise KeyError(f"no such connection: {connection_id}")
    policy = automation.get(connection_id)
    _guardrail(row['project_id'], policy)
    from . import sources
    ids = [r['id'] for r in sources.queued(row['project_id'])]
    if not ids:
        return {'id': connection_id, 'status': 'idle', 'runId': None, 'queued': 0}
    result = jobs.submit('wiki', row['project_id'], absorb=True, ids=ids,
                         budget=_budget(policy))
    return {'id': connection_id, 'status': result['status'],
            'runId': result['run_id'], 'queued': len(ids)}


def remove(connection_id: str) -> None:
    if _is_github(connection_id):
        repos.remove(connection_id)
        return
    with connect() as c:
        row = c.execute(
            "DELETE FROM brain_connector_connections WHERE id = %s RETURNING id",
            (connection_id,)).fetchone()
    if not row:
        raise KeyError(f"no such connection: {connection_id}")
    credentials.drop(connection_id)


def settings_for(connection_id: str) -> dict:
    """What a feeder needs at run time: the connection's config plus its
    secrets. Never returned through an API route."""
    return {**(_require(connection_id)["config"] or {}), **credentials.get(connection_id)}


def watermark(connection_id: str) -> str:
    """RFC3339 the feeders hand straight to Google, or "" for a full sync."""
    at = _require(connection_id).get("synced_at")
    return at.isoformat() if at else ""


def advance_watermark(connection_id: str, to: str) -> None:
    with connect() as c:
        c.execute("UPDATE brain_connector_connections SET synced_at = %s "
                  "WHERE id = %s", (to, connection_id))


def _require(connection_id: str) -> dict:
    with connect() as c:
        row = c.execute(
            "SELECT id, project_id, kind, name, config, synced_at "
            "FROM brain_connector_connections WHERE id = %s",
            (connection_id,)).fetchone()
    if not row:
        raise KeyError(f"no such connection: {connection_id}")
    return row
