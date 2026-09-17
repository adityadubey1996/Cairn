"""Connector registry: feeders that pull external content (Drive, Chat, ...)
into some repo's `raw/inbox/` for the normal ingest → absorb → validate
pipeline to pick up. A connector is code (how to fetch); its config and run
history are data (brain_connectors / brain_connector_runs).

Adding a connector = add one entry to REGISTRY and a `run()` in feeders/.
Nothing else — the health screen, history, and status derivation are generic.
"""
from __future__ import annotations

import importlib
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from . import config
from .db import connect
from .runs import start_run, finish_run

log = logging.getLogger("cairn.connectors")


@dataclass(frozen=True)
class OAuthSpec:
    """What a generic consent route needs from a connector.

    Named for Onyx's OAuthConnector, which declares the same two operations
    (authorization url, code -> token) so one Connect surface drives every
    provider instead of a hand-rolled setup route per connector.

    `provider` is the TOKEN's owner, not the connector's: one Google consent
    covers Drive and Chat, so those two share a single spec instance and a
    single stored refresh token. It is also the URL segment the provider's
    registered redirect URI already uses, which is why adding a provider needs
    no new route and no re-registration.

    Every callable imports its feeder lazily, for the same reason `module`
    below is a string: a missing optional dependency must not break the app.
    """
    provider: str
    consent_url: Callable[[str, str], str]   # (redirect_uri, state) -> url
    exchange: Callable[[str, str], str]      # (code, redirect_uri) -> account label
    ready: Callable[[], bool]                # client credentials present


def _gauth():
    from feeders.google import auth
    return auth


GOOGLE_OAUTH = OAuthSpec(
    provider="google",
    consent_url=lambda redirect_uri, state: _gauth().consent_url(redirect_uri, state),
    exchange=lambda code, redirect_uri: _gauth().exchange_code(code, redirect_uri),
    ready=lambda: bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET),
)


@dataclass
class Connector:
    id: str
    name: str
    kind: str
    description: str
    configured: Callable[[], bool]
    module: str  # feeders.<module>, imported lazily so missing optional
                 # deps (e.g. google-api-python-client) never break the app
    listed: bool = True  # shown on the Connectors screen
    oauth: OAuthSpec | None = None  # declared, not hard-coded in a router
    # Env vars a local install must set before this connector can run, and one
    # line on how to get them. Declared here so preflight() and CONNECTORS.md
    # cannot drift from the code that actually reads them.
    requires: tuple[str, ...] = ()
    setup: str = ""


REGISTRY: list[Connector] = [
    Connector(
        id="gdrive",
        name="Google Drive",
        kind="gdrive",
        description="Transcripts, docs and PDFs from Google Drive",
        configured=lambda: config.GOOGLE_TOKEN_FILE.is_file(),
        module="feeders.gdrive.sync",
        oauth=GOOGLE_OAUTH,
        requires=("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        setup="Create a Google Cloud OAuth client (Web application), add http://localhost:8300/api/google/callback as a redirect URI, enable the Drive API, then click Connect to give consent.",
    ),
    Connector(
        id="links",
        name="Links",
        kind="links",
        description="External pages, datasets and PDFs referenced inside Drive/Chat content",
        configured=lambda: True,  # no credentials of its own — see feeders/links/sync.py
        module="feeders.links.sync",
        requires=(),
        setup="No credentials. Follows URLs already found in other connectors' content.",
    ),
    Connector(
        id="gchat",
        name="Google Chat",
        kind="gchat",
        description="Team-space discussions from Google Chat",
        configured=lambda: config.GOOGLE_TOKEN_FILE.is_file(),
        module="feeders.chat.sync",
        oauth=GOOGLE_OAUTH,
        requires=("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"),
        setup="Same Google OAuth client as Drive — one consent covers both. Also enable the Google Chat API on the project.",
    ),
    Connector(
        id="whatsapp",
        name="WhatsApp",
        kind="browser",
        description="Tracked group chats scraped via the Steel browser session",
        configured=lambda: bool(config.WHATSAPP_GROUPS),
        module="feeders.whatsapp.sync",
        requires=("WHATSAPP_GROUPS",),
        setup="Run Steel (docker compose up -d steel-api), set WHATSAPP_GROUPS to the exact chat titles from WhatsApp Web's sidebar, then scan the QR in the login panel.",
    ),
    Connector(
        id="linkedin",
        name="LinkedIn",
        kind="browser",
        description="Configured messaging threads scraped via the Steel browser session",
        configured=lambda: bool(config.LINKEDIN_THREADS),
        module="feeders.linkedin.sync",
        requires=("LINKEDIN_THREADS",),
        setup="Run Steel (docker compose up -d steel-api), set LINKEDIN_THREADS to thread URLs, then sign in once in the login panel.",
    ),
    Connector(
        id="github",
        name="GitHub repos",
        kind="github",
        description="Public repos cloned and turned into wiki articles",
        configured=lambda: bool(config.REPO_CLONE_DIR and shutil.which("git")),
        module="feeders.github.sync",
        # Repos are not a feeder: a feeder pulls outside content into some
        # repo's raw/inbox/, while this IS the repo. It has its own screen.
        # The row still exists because every repo step records a run against
        # it — brain_connector_runs.connector_id references it.
        listed=False,
        requires=(),
        setup="Nothing for public repos. A private repo needs GITHUB_TOKEN — a fine-grained PAT with Contents: read-only.",
    ),
    Connector(
        id="upload",
        name="Upload",
        kind="upload",
        description="Files or a folder added by hand — no account behind it",
        configured=lambda: True,  # nothing to configure — see feeders/upload/sync.py
        module="feeders.upload.sync",
        requires=(),
        setup="No credentials. Drag files in from the Connect screen.",
    ),
]


def ensure_rows() -> None:
    """Every REGISTRY connector gets a brain_connectors row, so a health query
    is one join even before its first run."""
    with connect() as c:
        for conn in REGISTRY:
            c.execute(
                "INSERT INTO brain_connectors (id, name, kind) VALUES (%s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                (conn.id, conn.name, conn.kind))


def health() -> list[dict]:
    """One row per registered connector: config state + its most recent run."""
    with connect() as c:
        runs = {r["connector_id"]: r for r in c.execute(
            "SELECT DISTINCT ON (connector_id) connector_id, status, "
            "items_seen, items_written, error, started_at, finished_at "
            "FROM brain_connector_runs "
            "ORDER BY connector_id, started_at DESC").fetchall()}
    out = []
    for conn in REGISTRY:
        if not conn.listed:
            continue
        is_configured = conn.configured()
        last = runs.get(conn.id)
        status = ("not_configured" if not is_configured
                  else "never_run" if not last
                  else last["status"])
        out.append({
            "id": conn.id, "name": conn.name, "kind": conn.kind,
            "description": conn.description, "configured": is_configured,
            "status": status,
            "last_run": {
                "status": last["status"], "items_seen": last["items_seen"],
                "items_written": last["items_written"], "error": last["error"],
                "started_at": last["started_at"].isoformat(),
                "finished_at": last["finished_at"].isoformat() if last["finished_at"] else None,
            } if last else None,
        })
    return out


def preflight() -> list[dict]:
    """Per connector: can it run here, and if not, exactly what is missing.

    Everything is read off REGISTRY, so a connector added later shows up with
    no change here. `missing` names unset env vars; a connector with none left
    to set but still unconfigured is waiting on a browser step (OAuth consent,
    a QR scan), which `setup` explains.
    """
    out = []
    for conn in REGISTRY:
        missing = [name for name in conn.requires if not os.environ.get(name, "").strip()]
        configured = conn.configured()
        out.append({
            "id": conn.id, "name": conn.name, "kind": conn.kind,
            "listed": conn.listed,
            "configured": configured,
            "requires": list(conn.requires),
            "missing": missing,
            "needsBrowserStep": bool(conn.oauth or conn.kind == "browser"),
            "setup": conn.setup,
            "ready": configured and not missing,
        })
    return out


def run_now(connector_id: str, project_id: str | None = None) -> dict:
    conn = next((c for c in REGISTRY if c.id == connector_id), None)
    if not conn:
        raise ValueError(f"no such connector: {connector_id}")
    if not conn.configured():
        raise RuntimeError(f"{conn.name} is not configured")
    if project_id is None:
        from . import projects
        project_id = projects.ensure_default()
    run_id = start_run(conn.id)
    try:
        mod = importlib.import_module(conn.module)
        seen, written = mod.run(project_id=project_id)
        finish_run(run_id, status="ok", items_seen=seen, items_written=written)
        if written:
            # A feeder writes sources/ — fetched Drive docs, transcripts. That
            # content is NOT derivable: re-fetching gives what the document says
            # today, not what was ingested, so losing it invalidates every
            # citation made against it. Push it the moment it exists.
            try:
                from . import storage
                storage.push()
            except Exception:
                log.exception("s3 push failed; %s sources are still on disk", conn.id)
        return {"status": "ok", "items_seen": seen, "items_written": written}
    except Exception as e:
        finish_run(run_id, status="error", error=str(e)[:2000])
        raise


def _connector_since(connector_id: str) -> str:
    """Absorb honours the same start-date as the scrape: the connector's *_SINCE
    (empty ⇒ last 7 days). This is the 'start date' the UI absorb respects."""
    cfg = {"whatsapp": config.WHATSAPP_SINCE, "linkedin": config.LINKEDIN_SINCE}
    return cfg.get(connector_id) or (date.today() - timedelta(days=7)).isoformat()


_UNIT_DATE = re.compile(r"-(\d{4}-\d{2}-\d{2})$")


def _queued_units(connector_id: str, since: str) -> list[str]:
    """This connector's queued unit ids (from raw/_pending.md) dated >= since."""
    pending = config.ROOT / "raw" / "_pending.md"
    if not pending.is_file():
        return []
    out = set()
    for line in pending.read_text().splitlines():
        m = re.search(r"`([^`]+)`", line)
        if not m or not m.group(1).startswith(f"{connector_id}-"):
            continue
        d = _UNIT_DATE.search(m.group(1))
        if d and d.group(1) >= since:
            out.add(m.group(1))
    return sorted(out)


def absorb_now(connector_id: str) -> dict:
    """Ingest, then absorb just this connector's queued chat units dated >= its
    start date — the paid step, UI-triggered. Runs the pipeline as a subprocess
    with ABSORB_MODEL set explicitly (absorb_runner defaults to a retired model
    when it is absent from the env)."""
    conn = next((c for c in REGISTRY if c.id == connector_id), None)
    if not conn:
        raise ValueError(f"no such connector: {connector_id}")
    if conn.kind != "browser":
        raise ValueError(f"{connector_id} is not a browser connector")
    from . import llm
    try:
        absorb_overrides = llm.absorb_env()
    except llm.NoProvider as e:
        raise RuntimeError(str(e))

    run_id = start_run(conn.id, step="absorb")
    try:
        root = str(config.ROOT)
        env = {**os.environ, **absorb_overrides}
        if config.ABSORB_MODEL:
            env["ABSORB_MODEL"] = config.ABSORB_MODEL
        # 1. refresh the queue from raw/inbox (free, no LLM)
        subprocess.run([sys.executable, "pipeline/ingest.py", "--repo", ".",
                        "--out", "raw/entries"],
                       cwd=root, env=env, check=True, capture_output=True, text=True,
                       timeout=600)
        since = _connector_since(connector_id)
        ids = _queued_units(connector_id, since)
        if not ids:
            finish_run(run_id, status="ok", items_seen=0, items_written=0)
            return {"status": "ok", "absorbed": 0, "queued": 0, "since": since}
        # 2. absorb them (paid)
        args = [sys.executable, "pipeline/absorb_runner.py", "--repo", ".",
                "--kind", "chat_thread"]
        for uid in ids:
            args += ["--only", uid]
        r = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True,
                           timeout=max(300, len(ids) * 60))
        published = r.stdout.count("ok  ->")
        finish_run(run_id, status="ok", items_seen=len(ids), items_written=published)
        # Refresh THIS server's search index so the new articles are queryable
        # right away. The absorb subprocess rebuilt the index db, but this
        # process holds the index it loaded at startup — without this, absorbed
        # content isn't found until the 15-min corpus loop or a restart.
        try:
            from . import corpus
            corpus.sync()
        except Exception:
            log.exception("corpus.sync after absorb failed; articles queryable after next sync")
        try:
            from . import storage
            storage.push()
        except Exception:
            log.exception("s3 push after absorb failed")
        return {"status": "ok", "absorbed": published, "queued": len(ids), "since": since}
    except subprocess.TimeoutExpired as e:
        finish_run(run_id, status="error", error=f"timed out after {e.timeout}s")
        raise RuntimeError(f"absorb timed out after {e.timeout}s")
    except subprocess.CalledProcessError as e:
        finish_run(run_id, status="error", error=(e.stderr or str(e))[:2000])
        raise RuntimeError(f"ingest failed: {(e.stderr or '')[-300:]}")
    except Exception as e:
        finish_run(run_id, status="error", error=str(e)[:2000])
        raise
