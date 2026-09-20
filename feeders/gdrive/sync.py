"""Google Drive feeder.

Auth lives in feeders/google/auth.py and is shared with the Chat feeder: one
user consent, three read-only scopes. connectors.py only calls run() once that
consent exists, so the health screen reports "not configured" until then.

THE CONTRACT (matches ingest.py's load_inbox() exactly):
  raw/inbox/<id>.md, frontmatter the feeder owns:
    id: gdrive-<file id>
    path: sources/gdrive/<file id>.md      <-- see note below, NOT a gdrive:// URI
    sha: <sha1 of the fetched content>
    source_type: meeting_transcript | gdrive_doc
    status: active
    date: YYYY-MM-DD           time: "HH:MM:SS"
    authors: ["name", ...]
  Then the article body verbatim below the frontmatter.

WHY `path` POINTS AT A COMMITTED FILE, NOT A gdrive:// URI:
  Every claim the wiki writer makes needs `[grade: path@sha]` — a citation
  the validator can open and re-check. A citation only the feeder can resolve
  (a live Drive URL) breaks that the moment someone revokes access or the
  file moves, and breaks `validate_wiki.py`'s "does the cited path exist in
  this repo" check outright. So the feeder writes the fetched content to a
  real, trackable file under sources/gdrive/ FIRST; the inbox pointer then
  cites that path. Whoever commits sources/gdrive/ (raw/ stays gitignored —
  derived and disposable, like raw/entries/) — a human, or later a scoped CI
  step mirroring wiki-absorb.yml's bot-PR-and-required-merge pattern — makes
  the transcript a normal, citable git blob — never a special case. This
  script never runs git itself: writing files is its job, exactly like
  ingest.py never commits raw/entries/ either.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from feeders.google.auth import access_token
from feeders.result import SyncResult, failure
from feeders import options
from pipeline.ingest import extract_binary
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://www.googleapis.com/drive/v3"

log = logging.getLogger("cairn.gdrive")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# Google Docs export; uploaded text files (Meet=Doc, Zoom=.vtt, Otter=.txt);
# PDFs (decks, reports, signed docs) via their text layer.
_EXPORTABLE_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_SLIDES_MIME = "application/vnd.google-apps.presentation"
_PDF_MIME = "application/pdf"

# What each Google-native type exports as. Sheets and Slides are deliberately
# NOT in _wanted() below: a Drive sweep that pulled every spreadsheet would
# bury the corpus. They are reachable only when a link explicitly points at
# one, which is a deliberate reference rather than a blanket sweep.
_EXPORT_AS = {
    _EXPORTABLE_MIME: "text/markdown",
    _SHEET_MIME: "text/csv",
    _SLIDES_MIME: "text/plain",
}
_OFFICE_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}
_TEXT_EXTS = (".txt", ".md", ".vtt", ".srt", ".pdf", ".docx", ".xlsx", ".pptx")
_FOLDER_MIME = "application/vnd.google-apps.folder"


_FIELDS = "id,name,mimeType,modifiedTime,parents,owners(displayName)"


# folder id -> (name, parent id), or None when Drive will not show it. One
# lookup per folder no matter how many files sit in it: twelve folders cover
# this whole corpus, so the walk below costs a couple of dozen calls a sync.
_FOLDER_CACHE: dict[str, tuple[str, str | None] | None] = {}


def _folder_meta(folder_id: str) -> tuple[str, str | None] | None:
    """(name, parent id) for one folder, or None when Drive refuses it.

    Refusal is the normal case, not an error: a transcript shared out of a
    teammate's Drive has a parent this account cannot read — 252 of 331 files
    here — and a sync must not fail over it.
    """
    if folder_id not in _FOLDER_CACHE:
        try:
            meta = _get_json(f"{API}/files/{folder_id}"
                             "?fields=id,name,parents&supportsAllDrives=true")
            _FOLDER_CACHE[folder_id] = (meta["name"],
                                        (meta.get("parents") or [None])[0])
        except Exception:
            _FOLDER_CACHE[folder_id] = None
    return _FOLDER_CACHE[folder_id]


# Drive permits "/" inside a folder name, and Google Meet uses it: every
# meeting folder is named like "Connect with Satya - 2026/08/27 10:03 IST".
# Joining those with "/" made one folder look like four nested ones, so the
# separator is swapped for a division slash — identical on screen, and not the
# character the path is split on.
_PATH_SEP, _IN_NAME = "/", "\u2215"


def _folder_path(f: dict) -> str | None:
    """Top-down folder path for a file, or None when Drive shows no parent.

    Walked to the root rather than stopped at the immediate parent because
    folder names repeat — this corpus has two folders called "findings" — so
    only the path tells them apart. "My Drive" is dropped once something sits
    below it, where it is only a prefix on every row.
    """
    parents = f.get("parents") or []
    if not parents:
        return None
    chain: list[str] = []
    at, seen = parents[0], set()
    # Drive should not produce a parent cycle, but a loop walking remote data
    # it does not control says so rather than trusting it.
    while at and at not in seen:
        seen.add(at)
        meta = _folder_meta(at)
        if not meta:
            break
        chain.append(meta[0].replace(_PATH_SEP, _IN_NAME))
        at = meta[1]
    if not chain:
        return None
    chain.reverse()
    if len(chain) > 1 and chain[0] == "My Drive":
        del chain[0]
    return _PATH_SEP.join(chain)


def _norm(f: dict) -> dict:
    return {"id": f["id"], "name": f["name"], "mime_type": f["mimeType"],
            "modified_time": f["modifiedTime"], "folder": _folder_path(f),
            "authors": [o["displayName"] for o in f.get("owners", [])]}


def _wanted(f: dict) -> bool:
    return (f["mimeType"] in (_EXPORTABLE_MIME, _PDF_MIME, *_OFFICE_MIMES)
            or f["name"].lower().endswith(_TEXT_EXTS))


def _excluded(name: str) -> bool:
    low = name.lower()
    return any(fnmatch.fnmatch(low, p.lower()) or p.lower() in low
               for p in config.GDRIVE_EXCLUDE)


def _pages(query: str, modified_after: str = "") -> list[dict]:
    if modified_after:
        query += f" and modifiedTime > '{modified_after}'"
    out, page_token = [], None
    while True:
        params = {"q": query, "fields": f"nextPageToken,files({_FIELDS})",
                  "pageSize": "1000", "supportsAllDrives": "true",
                  "includeItemsFromAllDrives": "true"}
        if page_token:
            params["pageToken"] = page_token
        page = _get_json(f"{API}/files?{urllib.parse.urlencode(params)}")
        out.extend(page.get("files", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return out


def _walk_folder(folder_id: str, modified_after: str) -> list[dict]:
    """Google's Meet folder gives every meeting its own subfolder, so a
    configured folder often holds no files directly. Shortcuts are not
    followed."""
    out, queue, visited = [], [folder_id], set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        # A file update does not update its parent folder's modifiedTime.
        # Walk every folder, applying the watermark only to leaf documents.
        for f in _pages(f"'{current}' in parents and trashed = false"):
            if f["mimeType"] == _FOLDER_MIME:
                queue.append(f["id"])
            elif not modified_after or f["modifiedTime"] > modified_after:
                out.append(f)
    return out


# How Drive actually names meeting artefacts. Matching only "transcript" filed
# every "Meeting started 2026/06/29 20:25 IST - Notes by Gemini" as a plain doc,
# throwing away the one thing that marks it a dated conversation rather than a
# document — and with it the true meeting date the article is built around.
_MEETING_MARKERS = ("transcript", "notes by gemini", "meeting started",
                    "meeting notes", "- notes by")

# Drive `name contains` is case-insensitive, so the markers above cover the
# query too — one vocabulary for "what we fetch" and "what we classify".
_TRANSCRIPT_NAME_Q = "(" + " or ".join(
    f"name contains '{m}'" for m in _MEETING_MARKERS) + ")"


def list_drive_files(modified_after: str = "", source_ids: list[str] | None = None) -> list[dict]:
    """Default (GDRIVE_SOURCE_IDS empty): every text-like file the user owns,
    plus meeting transcripts anyone shared with them.

    Owned-only alone is not enough for transcripts specifically: Meet/Gemini
    saves a transcript to whoever ran the notetaker, not to every attendee, so
    "'me' in owners" misses most meetings the user didn't personally organize
    (verified: 214 of 227 visible transcript-shaped files are owned by
    teammates, not the user). Generic shared docs/decks stay owned-only —
    ~1200 files are visible to this account in total, and admitting all of
    them would mean every deck anyone ever shared company-wide (spec
    see the module docstring); widening beyond transcript-shaped
    names is a separate, deliberate decision, not this one.

    With GDRIVE_SOURCE_IDS set, each id may be a folder (walked recursively) or
    a single file (taken as-is); the shared-transcript widening below only
    applies to the default, unscoped listing.
    """
    found = []
    selected_ids = config.GDRIVE_SOURCE_IDS if source_ids is None else source_ids
    if selected_ids:
        for source_id in selected_ids:
            meta = _get_json(f"{API}/files/{source_id}"
                             f"?fields={_FIELDS}&supportsAllDrives=true")
            if meta["mimeType"] == _FOLDER_MIME:
                found.extend(_walk_folder(source_id, modified_after))
            else:
                found.append(meta)
    else:
        found = _pages("'me' in owners and trashed = false", modified_after)
        seen = {f["id"] for f in found}
        shared = _pages(f"trashed = false and {_TRANSCRIPT_NAME_Q}", modified_after)
        found += [f for f in shared if f["id"] not in seen]
    return [_norm(f) for f in found
            if _wanted(f) and not _excluded(f["name"])]


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _captions_to_prose(raw: str) -> str:
    """Strip VTT/SRT cue machinery (headers, indices, timestamps) down to the
    spoken lines; rolling captions repeat lines, so drop consecutive dupes."""
    out, last = [], None
    for ln in raw.splitlines():
        s = ln.strip()
        # ponytail: s.isdigit() also eats a caption that is purely a number —
        # acceptable for meeting speech; revisit if it ever bites.
        if (not s or "-->" in s or s.isdigit() or s == "WEBVTT"
                or s.startswith(("NOTE", "STYLE", "REGION"))):
            continue
        if s != last:
            out.append(s)
            last = s
    return "\n".join(out)


def _pdf_to_text(raw: bytes) -> str:
    """The PDF's text, page by page. `pdftotext -layout` first — it keeps
    table columns aligned, which pypdf's extract_text() does not — falling
    back to pypdf if poppler isn't installed. If neither finds a text layer
    at all (scanned/photographed page), OCR every page with tesseract.
    run() skips a source that still comes back "" after all three.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        text = ""
        if shutil.which("pdftotext"):
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"],
                                 capture_output=True, text=True, timeout=60)
            text = out.stdout.strip()
        if not text:
            pages = PdfReader(io.BytesIO(raw)).pages
            text = "\n\n".join((p.extract_text() or "").strip() for p in pages).strip()
        if text or not (shutil.which("pdftoppm") and shutil.which("tesseract")):
            return text
        # ponytail: no page cap — a huge scanned deck OCRs page by page and
        # will just be slow, not wrong. Add a cap if one ever blocks a sync.
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["pdftoppm", "-png", "-r", "200", f.name, f"{d}/p"],
                           capture_output=True, timeout=180)
            parts = [subprocess.run(["tesseract", str(png), "-"],
                                    capture_output=True, text=True, timeout=60).stdout.strip()
                     for png in sorted(Path(d).glob("p-*.png"))]
            return "\n\n".join(p for p in parts if p).strip()


def _source_type(file: dict) -> str:
    """ingest.py's own `kind` vocabulary — load_inbox() maps our `source_type`
    straight onto `kind`, so a Drive PDF must declare itself the same kind of
    thing a repo PDF is (`binary_doc`), not a connector-private label. Sharing
    the vocabulary is what lets one article cite a deck and a code package
    side by side. Only the dated-conversation kinds are genuinely new: the
    repo has no unit that is a conversation on a date.
    """
    name = file["name"].lower()
    if name.endswith((".vtt", ".srt")) or any(m in name for m in _MEETING_MARKERS):
        return "meeting_transcript"
    if file["mime_type"] == _PDF_MIME or name.endswith(".pdf") or file["mime_type"] in _OFFICE_MIMES or name.endswith(tuple(_OFFICE_MIMES.values())):
        return "binary_doc"
    return "doc"


def _source_slug(file: dict) -> str:
    """Drive names are not unique — one account here holds `UI_SCREENS_SPEC.html.pdf`
    three times and `document (3).pdf` twice, all distinct files. Keying the
    source path on the name alone made them collide: three inbox entries citing
    one path, each with a different sha, the file rewritten every run and every
    citation resolving to whichever document was fetched last. The id suffix is
    unconditional because suffixing only on collision changes a surviving file's
    path the day its twin is deleted, silently breaking citations already made.
    """
    return f"{_slug(file['name'])[:60]}-{file['id'][-6:].lower()}"


def _title(file: dict, text: str) -> str:
    """The document's own H1 wins over its filename. Drive is full of machine
    names — `compass_artifact_wf-982542dd-3db6-….md`, `screencapture-…pdf` — and
    the filename otherwise becomes the article title and the visible half of
    every citation made against it.
    """
    first = text.lstrip().split("\n", 1)[0].strip()
    if first.startswith("# "):
        return first[2:].strip()
    return re.sub(r"\.(md|txt|pdf|vtt|srt)$", "", file["name"], flags=re.I)


def export_text(file: dict) -> str:
    """Google Docs (Meet transcripts) export as markdown; uploaded files
    (Zoom .vtt, Otter .txt, .srt/.md, .pdf) download raw. Captions become
    prose, PDFs become their text layer, everything else is already text."""
    export_as = _EXPORT_AS.get(file["mime_type"])
    if export_as:
        raw = _get_bytes(f"{API}/files/{file['id']}/export"
                         f"?mimeType={urllib.parse.quote(export_as, safe='')}")
    else:
        raw = _get_bytes(f"{API}/files/{file['id']}?alt=media&supportsAllDrives=true")
    name = file["name"].lower()
    if file["mime_type"] == _PDF_MIME or name.endswith(".pdf"):
        return _pdf_to_text(raw)
    extension = _OFFICE_MIMES.get(file["mime_type"], Path(name).suffix)
    if extension in _OFFICE_MIMES.values():
        with tempfile.NamedTemporaryFile(suffix=extension) as temporary:
            temporary.write(raw)
            temporary.flush()
            text, method = extract_binary(Path(temporary.name))
        if not text.strip():
            raise ValueError(f"no extractable text ({method})")
        return text
    text = raw.decode("utf-8", errors="replace")
    if name.endswith((".vtt", ".srt")):
        text = _captions_to_prose(text)
    return text


def inventory(modified_after: str = "") -> dict:
    """What a real run would ingest, without fetching or writing anything.

    A codebase is bounded and authored; a Drive is not. Nobody can eyeball three
    thousand files, and every one that gets through costs a Groq absorb call and
    dilutes the wiki — so the first look is always a count, never a sync. Same
    discipline as ingest.py printing its kind histogram before absorb.
    """
    files = list_drive_files(modified_after)
    return {
        "total": len(files),
        "by_kind": dict(Counter(_source_type(f) for f in files)),
        "by_owner": dict(Counter((f["authors"] or ["unknown"])[0] for f in files)),
        "sample": [f["name"] for f in files[:20]],
    }


def run(modified_after: str = "", project_id: str | None = None,
        on_progress=None, connection_id: str | None = None,
        source_ids: list[str] | None = None, max_items: int = 0) -> SyncResult:
    """Returns (items_seen, items_written). Called by connectors.run_now().

    on_progress(done, total, label) fires once per file before it is fetched, so
    a caller can render a bar. Default None keeps every existing caller unchanged.

    connection_id attributes every row to the connection whose sync
    produced it. Optional because a hand or cron run has no connection —
    those rows stay unattributed rather than being guessed at.
    """
    max_items = options.max_items(max_items)
    source_ids = options.source_ids(source_ids)
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    files = (list_drive_files(modified_after) if source_ids is None
             else list_drive_files(modified_after, source_ids=source_ids))
    truncated = bool(max_items and len(files) > max_items)
    if max_items:
        files = sorted(files, key=lambda f: f["modified_time"], reverse=True)[:max_items]
    # `path:` below stays repo-relative because that is what a citation must
    # resolve to; SOURCES_DIR defaults inside the repo, and pointing it outside
    # would break validate_wiki's "does this path exist here" check.
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, "sources/gdrive")
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    written = 0
    failures = []
    for n, f in enumerate(files, 1):
        if on_progress:
            on_progress(n, len(files), f.get("name") or f["id"])
        try:
            text = export_text(f)
            if not text.strip():
                raise ValueError("document has no extractable text")
        except Exception as e:
            log.exception("gdrive: failed to extract %r, skipping", f.get("id"))
            # The id is in hand here; it used to be logged and dropped, so a
            # sync could never say which files it missed.
            sources_index.record_failure(
                id=f"gdrive-{f['id']}", project_id=project_id, kind="gdrive",
                name=f.get("name") or f["id"], reason=e,
                connection_id=connection_id)
            failures.append(failure(f"gdrive-{f['id']}", f.get("name") or f["id"], e))
            continue
        sha = hashlib.sha1(text.encode()).hexdigest()[:8]
        slug = _source_slug(f)
        source_path = sources / f"{slug}.md"
        # Existing citations refer to the path, so a remote rename must never
        # move it. The DB's display name is updated independently below.
        prior = sources_index.get(f"gdrive-{f['id']}")
        if prior and prior.get("path", "").startswith("sources/gdrive/"):
            previous_path = resolve_source_path(config.GDRIVE_TARGET_REPO, prior["path"])
            if previous_path.is_relative_to(sources.resolve()):
                source_path = previous_path
        source_rel = "sources/gdrive/" + source_path.relative_to(sources).as_posix()
        existing_sha = (hashlib.sha1(source_path.read_text().encode()).hexdigest()[:8]
                        if source_path.is_file() else None)
        inbox_path = inbox / f"gdrive-{f['id']}.md"
        changed = existing_sha != sha or not inbox_path.is_file()
        if existing_sha != sha:
            source_path.write_text(text, encoding="utf-8")
        date, time = f["modified_time"].split("T", 1)
        entry = (
            "---\n"
            f'id: gdrive-{f["id"]}\n'
            f"path: {source_rel}\n"

            f"sha: {sha}\n"
            f"source_type: {_source_type(f)}\n"
            "status: active\n"
            f"date: {date}\n"
            f'time: "{time[:8]}"\n'
            f"authors: [{', '.join(json.dumps(a) for a in f.get('authors', []))}]\n"
            "---\n\n"
            + (text if text.lstrip().startswith("# ")
               else f"# {_title(f, text)}\n\n{text}") + "\n"
        )
        if changed:
            inbox_path.write_text(entry, encoding="utf-8")
        sources_index.record(
            id=f"gdrive-{f['id']}", project_id=project_id, kind="gdrive",
            name=f["name"], path=source_rel,
            size=len(text.encode()), sha=sha, authors=f.get("authors", []),
            folder=f.get("folder"), connection_id=connection_id)
        written += int(changed)
    if truncated:
        failures.append(failure("gdrive-backlog", "More Drive documents remain",
                                "Sync reached max_items; increase the limit or narrow source_ids"))
    return SyncResult(len(files), written, failures)
