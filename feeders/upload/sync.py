"""Upload feeder — files or a folder handed over directly, with no external
account behind them. Unlike every other feeder, nothing here is fetched: a
staging endpoint (server/connections.py:stage_upload) already wrote the bytes
this connector's run() finds on disk.

See docs/superpowers/specs/2026-09-16-upload-connector-design.md.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from pipeline.ingest import BINARY_EXT, DATA_EXT, PROSE_EXT, extract_binary, summarize_dataset
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index
from feeders.result import SyncResult, failure

SOURCES_SUBDIR = "upload"

# ponytail: 25MB ceiling on a single staged file, the same cap `links`
# already uses for a fetched URL (feeders/links/sync.py) — generous for a
# document, adjustable if a legitimate larger file shows up.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

_SOURCE_TYPE = {"binary": "binary_doc", "text": "doc", "data": "dataset"}


def staging_dir(connection_id: str) -> Path:
    """Where staged bytes for this connection wait to be run(). The only
    place this path is computed — server/connections.py's stage_upload()
    imports this rather than recomputing it, so writer and reader can never
    drift onto different directories."""
    return config.GDRIVE_TARGET_REPO / "raw" / "uploads" / "staged" / connection_id


def upload_id(project_id: str, relative_path: str) -> str:
    """Keyed on PROJECT + PATH, not content or path alone — two different
    projects uploading the same relative path, or two individually-picked
    files that happen to share a bare filename, must never collide. The
    same principle as links (keyed on URL) and Drive (keyed on file id),
    scoped further because a local filename has no global uniqueness at all."""
    key = f"{project_id}\0{relative_path}"
    return f"upload-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def upload_slug(project_id: str, relative_path: str) -> str:
    stem = Path(relative_path).stem
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:50]
    return f"{base or 'file'}-{upload_id(project_id, relative_path)[-8:]}"


def classify_upload(ext: str) -> str:
    """Which extraction path a staged file's extension needs — reuses
    ingest.py's own extension taxonomy so there is exactly one place that
    decides what a .docx or a .csv is."""
    ext = ext.lower()
    if ext in BINARY_EXT:
        return "binary"
    if ext in DATA_EXT:
        return "data"
    if ext in PROSE_EXT:
        return "text"
    return "unsupported"


def _dispatch_extract(path: Path) -> tuple[str, str | bytes]:
    """(source_type, payload) for one staged file. payload is text for
    text/binary, raw bytes for data — a data file's bytes ARE the citable
    artifact (same convention `links` uses for a fetched CSV/JSON); a binary
    doc keeps only its extracted text, never the original file (same
    convention gdrive/links use for a PDF's text layer).

    Raises ValueError for an extension classify_upload() doesn't recognize,
    or a binary file with no extractable text layer — the caller (write_entry)
    turns this into a recorded failure, never a silently empty article."""
    kind = classify_upload(path.suffix)
    if kind == "text":
        return "doc", path.read_text(encoding="utf-8", errors="replace")
    if kind == "binary":
        text, method = extract_binary(path)
        if not text.strip():
            raise ValueError(f"no extractable text ({method})")
        return "binary_doc", text
    if kind == "data":
        return "dataset", path.read_bytes()
    raise ValueError(f"unsupported file type: {path.suffix or '(none)'}")


ORIGINALS_SUBDIR = "originals"


def write_entry(target_repo: Path, relative_path: str, source_type: str,
                payload: str | bytes, *, project_id: str,
                connection_id: str | None = None,
                original: Path | None = None) -> bool:
    """The one write path run() calls once extraction has already happened.
    Mirrors feeders/links/sync.py:write_entry.

    sha compares whichever of (bytes, text) was actually stored, so an
    unchanged re-upload never rewrites — only attribute()/set_folder() run,
    for the same reason gdrive's run() calls them on its own unchanged
    branch: connection_id or folder may be newly known even when the
    content is not.

    `original` is the staged file to keep a byte-identical copy of, passed
    only where extraction LOSES something — a PDF's layout, a .docx's
    everything. A .txt or .csv is its own original and gets none: a second
    identical copy under originals/ would just double the S3 push."""
    uid = upload_id(project_id, relative_path)
    raw_bytes = payload if isinstance(payload, bytes) else None
    stored = raw_bytes if raw_bytes is not None else payload.encode()
    sha = hashlib.sha1(stored).hexdigest()[:8]

    rel = Path(relative_path)
    ext = rel.suffix.lstrip(".") if raw_bytes is not None else "md"
    slug = upload_slug(project_id, relative_path)
    folder = str(rel.parent) if rel.parent != Path(".") else None

    sources_dir = resolve_source_path(target_repo, f"sources/{SOURCES_SUBDIR}")
    inbox_dir = target_repo / "raw" / "inbox"
    sources_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    source_path = sources_dir / f"{slug}.{ext}"
    inbox_path = inbox_dir / f"{uid}.md"
    # Sits under sources/, which storage.trees() already pushes to S3 — the
    # viewer reads it back through the same /api/sources/view every other
    # stored file uses, so keeping it needs no new durable tree.
    original_rel = (f"sources/{SOURCES_SUBDIR}/{ORIGINALS_SUBDIR}/{slug}{rel.suffix}"
                   if original is not None else None)
    original_path = resolve_source_path(target_repo, original_rel) if original_rel else None
    existing = (hashlib.sha1(source_path.read_bytes()).hexdigest()[:8]
               if source_path.is_file() else None)
    # existing == sha alone is not enough: if an earlier run wrote the source
    # file but crashed before the inbox entry, the sha would match forever
    # and the inbox entry would never be created. Same self-heal reasoning as
    # feeders/links/sync.py:write_entry — and a missing original is the same
    # kind of half-written state, including for rows written before
    # originals/ existed at all.
    if existing == sha and inbox_path.is_file() and not (
            original_path is not None and not original_path.is_file()):
        # Layout/images can change while extracted text stays identical.
        # Keep the download current without re-enqueuing the same evidence.
        if original_path is not None:
            original_bytes = original.read_bytes()
            if original_path.read_bytes() != original_bytes:
                original_path.write_bytes(original_bytes)
        sources_index.record(id=uid, project_id=project_id, kind=SOURCES_SUBDIR,
                             name=rel.name, path=f"sources/{SOURCES_SUBDIR}/{slug}.{ext}",
                             folder=folder, original_path=original_rel,
                             size=len(stored), sha=sha, authors=[],
                             connection_id=connection_id)
        return False

    if original_path is not None:
        original_path.parent.mkdir(parents=True, exist_ok=True)
        original_path.write_bytes(original.read_bytes())

    if raw_bytes is not None:
        source_path.write_bytes(raw_bytes)
    else:
        source_path.write_text(payload, encoding="utf-8")

    body = (summarize_dataset(source_path.parent, [source_path.name])
           if raw_bytes is not None else payload)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    date, tm = now.split("T", 1)
    fm = ["---", f"id: {uid}", f"path: sources/{SOURCES_SUBDIR}/{slug}.{ext}",
         f"sha: {sha}", f"source_type: {source_type}", "status: active",
         f"date: {date}", f'time: "{tm.rstrip("Z")}"', "authors: []", "---"]
    inbox_path.write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")

    sources_index.record(id=uid, project_id=project_id, kind=SOURCES_SUBDIR,
                         name=rel.name, path=f"sources/{SOURCES_SUBDIR}/{slug}.{ext}",
                         folder=folder, original_path=original_rel,
                         size=len(stored), sha=sha, authors=[],
                         connection_id=connection_id)
    return True


def run(project_id: str | None = None, on_progress=None,
       connection_id: str | None = None) -> tuple[int, int]:
    """Returns (items_seen, items_written). Called by connections.sync() via
    scripts/pipeline_run.py, exactly like every other connector.

    Unlike every other feeder, nothing here is fetched: every file under this
    connection's staging area was already placed there by the upload
    endpoint (server/connections.py:stage_upload). Successful files leave
    staging; failed files stay there so a transient extraction failure can be
    retried without destroying the only available original.
    """
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    if not connection_id:
        raise ValueError("upload.run() requires connection_id — there is no staging area without one")

    target_repo = config.GDRIVE_TARGET_REPO
    root = staging_dir(connection_id)
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []

    written = 0
    failures = []
    for n, path in enumerate(files, 1):
        rel = str(path.relative_to(root))
        if on_progress:
            on_progress(n, len(files), rel)
        try:
            source_type, payload = _dispatch_extract(path)
            # Only a binary loses something in extraction — a PDF keeps its
            # text layer and nothing else. Text and data files are stored
            # byte-for-byte already, so they are their own original.
            keep = path if classify_upload(path.suffix) == "binary" else None
            if write_entry(target_repo, rel, source_type, payload,
                           project_id=project_id, connection_id=connection_id,
                           original=keep):
                written += 1
        except Exception as e:
            sources_index.record_failure(
                id=upload_id(project_id, rel), project_id=project_id, kind=SOURCES_SUBDIR,
                name=rel, path=rel, reason=e, connection_id=connection_id)
            failures.append(failure(upload_id(project_id, rel), rel, e))
        else:
            path.unlink(missing_ok=True)
    return SyncResult(len(files), written, failures)
