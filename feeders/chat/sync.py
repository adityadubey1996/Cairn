"""Google Chat feeder.

Named team spaces only (`spaceType = "SPACE"`): Google excludes group chats
and 1:1 DMs server-side, and the client guard in list_spaces() keeps that
boundary even if the filter string is ever mis-edited. Granularity is one
entry per space per day — the transcript-like shape the wiki already grades
well, and per-thread would fragment a discussion that ran across threads.
A late reply rewrites that day's sha and re-absorbs it; that is why `sha`
is feeder-owned in the inbox contract.

THE CONTRACT (matches pipeline/ingest.py's load_inbox() exactly):
  raw/inbox/gchat-<space id>-<date>.md, frontmatter the feeder owns:
    id: gchat-<space id>-<date>
    path: sources/gchat/<slug>.md          <-- committed file, never a chat URL
    sha: <sha1 of the rendered day, 8 chars>
    source_type: chat_thread
    status: active
    date: YYYY-MM-DD           time: "HH:MM:SS"   (last message that day)
    authors: ["sender", ...]
  Body: `# <space> — <date>` then one `HH:MM Sender: text` line per message.

WHY `path` POINTS AT A COMMITTED FILE: same reason as the Drive feeder —
every claim carries `[grade: path@sha]`, a citation only the feeder can
resolve breaks the moment access is revoked, and validate_wiki checks the
path exists in this repo. This script never runs git; committing sources/
is a human action.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.parse
import urllib.request
from collections import defaultdict

from pathlib import Path

from feeders.gdrive import sync as _gdrive
from feeders.google.auth import access_token
from feeders.result import SyncResult, failure
from feeders import options
from pipeline.ingest import BINARY_EXT, DATA_EXT, PROSE_EXT, extract_binary, summarize_dataset
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

API = "https://chat.googleapis.com/v1"

# Attachments live beside the transcripts they were posted in, with originals
# kept the same way the upload feeder keeps them — a PDF dropped into a space
# is the same kind of artifact as one uploaded by hand.
ATTACHMENTS_SUBDIR = "attachments"
ORIGINALS_SUBDIR = "originals"

# ponytail: same 25MB ceiling the links and upload feeders use. A Chat
# attachment above this is almost certainly a video, which nothing downstream
# can read anyway.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Screenshots and diagrams are most of what gets pasted into a busy space.
# They are STORED (the original is what a human wants to look at, and the
# viewer renders these inline) but never read: there is no text layer, and OCR
# is a dependency and a bill this does not take on.
# ponytail: add OCR here if diagrams turn out to carry decisions nothing else
# records — the storage side already works, only _extract would change.
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}

# Not in pipeline/ingest.py's PROSE_EXT (which is .md/.rst/.txt), but a shared
# .html report is a document by any reasonable reading — the links feeder
# already knows how to strip one down to its text, so reuse that rather than
# growing a second HTML path.
HTML_EXT = {".html", ".htm"}

log = logging.getLogger("cairn.gchat")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token()}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _pages(url: str, params: dict, key: str) -> list[dict]:
    out, page_token = [], None
    while True:
        p = dict(params, pageSize="1000")
        if page_token:
            p["pageToken"] = page_token
        page = _get_json(f"{url}?{urllib.parse.urlencode(p)}")
        out.extend(page.get(key, []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return out


def list_spaces() -> list[dict]:
    """Named spaces the user is a member of.

    No server-side filter: verified empirically that `filter=spaceType =
    "SPACE"` silently drops most meeting-auto-created spaces (accessState
    PRIVATE) and only returns ones flagged DISCOVERABLE — an undocumented
    quirk, not a real scope boundary (85 of 95 real named spaces were
    missing under the filter). List everything and let the client-side type
    check be the real boundary against DMs/group chats instead."""
    spaces = _pages(f"{API}/spaces", {}, "spaces")
    return [s for s in spaces if s.get("spaceType") == "SPACE"]


def list_messages(space_name: str, created_after: str = "") -> list[dict]:
    params = {}
    if created_after:
        params["filter"] = f'createTime > "{created_after}"'
    msgs = _pages(f"{API}/{space_name}/messages", params, "messages")
    return sorted(msgs, key=lambda m: m["createTime"])


def list_message_sample(space_name: str, max_days: int) -> tuple[list[dict], bool]:
    """Fetch complete recent days, or fail without writing a partial day.

    A sample has a five-page budget per space. Descending order lets the first
    message from an older day prove that the selected newer day is complete.
    API ordering: developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/list
    """
    out, days, token = [], set(), None
    for _ in range(5):
        params = {"pageSize": 100, "orderBy": "createTime DESC"}
        if token:
            params["pageToken"] = token
        page = _get_json(f"{API}/{space_name}/messages?{urllib.parse.urlencode(params)}")
        for message in page.get("messages", []):
            day = message["createTime"].split("T")[0]
            if day not in days and len(days) >= max_days:
                return sorted(out, key=lambda m: m["createTime"]), True
            if carries_content(message):
                days.add(day)
                out.append(message)
        token = page.get("nextPageToken")
        if not token:
            return sorted(out, key=lambda m: m["createTime"]), False
    raise ValueError("Chat sample exceeded five message pages before a complete day; use an uncapped sync to retrieve it")


def _sender(m: dict) -> str:
    s = m.get("sender", {})
    # ponytail: user-auth message lists often omit displayName; the user-id
    # tail keeps lines attributable. Real names need chat.memberships.readonly
    # added to SCOPES plus one re-consent — do that if ids show up in articles.
    return s.get("displayName") or s.get("name", "users/unknown").split("/")[-1]


def rich_links(m: dict) -> list[str]:
    """URIs of link chips in a message. These live in annotations, NOT in
    `text` — a message that is only a pasted Drive link has an empty text
    field, so without this it reads as an empty message and used to be
    dropped entirely."""
    out = []
    for a in m.get("annotations") or []:
        uri = (a.get("richLinkMetadata") or {}).get("uri")
        if uri:
            out.append(uri)
    return out


def carries_content(m: dict) -> bool:
    """Whether a message is worth keeping. Text is not the only kind of
    content: 68 attachment-only messages in one space alone were being
    discarded by a bare `if m["text"]` check, taking their PDFs with them."""
    return bool((m.get("text") or "").strip()
                or m.get("attachment")
                or rich_links(m))


def _render_day(space: dict, day: str, msgs: list[dict]) -> str:
    title = space.get("displayName") or space["name"]
    lines = [f"# {title} — {day}", ""]
    for m in msgs:
        hhmm = m["createTime"].split("T")[1][:5]
        text = (m.get("text") or "").strip()
        # .get, not [..] — an attachment-only message has no text key at all,
        # and indexing it raised KeyError for the whole space-day.
        lines.append(f"{hhmm} {_sender(m)}: {text}" if text
                     else f"{hhmm} {_sender(m)}:")
        # Named in the transcript even when the bytes cannot be fetched, so the
        # reader (and absorb) can see that a file was shared here at all.
        for a in m.get("attachment") or []:
            name = a.get("contentName") or "(unnamed)"
            ctype = a.get("contentType") or "unknown"
            lines.append(f"    [attachment] {name} ({ctype})")
        for uri in rich_links(m):
            lines.append(f"    [link] {uri}")
    return "\n".join(lines)



def attachment_id(space_id: str, content_name: str, ref: str) -> str:
    """Keyed on the attachment's own resource ref, not its filename: "image.png"
    is posted dozens of times in a busy space and every one of them is a
    different file. The space scopes it the way project scopes an upload id."""
    key = f"{space_id}\0{content_name}\0{ref}"
    return f"gchat-att-{hashlib.sha1(key.encode()).hexdigest()[:12]}"


def attachment_slug(content_name: str, uid: str) -> str:
    stem = Path(content_name).stem
    base = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:50]
    return f"{base or 'file'}-{uid[-8:]}"


def _attachment_ref(att: dict) -> str:
    """The stable identifier for this attachment, whichever backing it has."""
    return ((att.get("attachmentDataRef") or {}).get("resourceName")
            or (att.get("driveDataRef") or {}).get("driveFileId")
            or att.get("name") or "")


def already_stored(target_repo: Path, space_id: str, att: dict) -> bool:
    """Whether this exact attachment is already on disk.

    A Chat attachment is IMMUTABLE — editing a message cannot change the bytes
    behind a file someone posted, and the resource ref the id is keyed on is
    unique per upload. So unlike a Drive doc or a web page, there is nothing to
    re-check: if the original is here, downloading it again can only produce the
    same bytes. Without this, every re-sync re-downloaded every attachment in
    the corpus just to compute a sha it already knew — gigabytes to learn
    nothing.
    """
    content_name = att.get("contentName") or "attachment"
    uid = attachment_id(space_id, content_name, _attachment_ref(att))
    slug = attachment_slug(content_name, uid)
    ext = Path(content_name).suffix.lower()
    original = resolve_source_path(target_repo, f"sources/gchat/{ATTACHMENTS_SUBDIR}/{ORIGINALS_SUBDIR}/{slug}{ext}")
    inbox = target_repo / "raw" / "inbox" / f"{uid}.md"
    return original.is_file() and inbox.is_file()


def download_attachment(att: dict) -> bytes:
    """Raw bytes for one attachment.

    Two backings, two APIs: an uploaded file comes from Chat's media endpoint
    under chat.messages.readonly; a Drive-backed one is a Drive file the
    message merely points at, and comes from Drive under drive.readonly. Both
    scopes are already granted — this needs no re-consent.
    """
    drive_id = (att.get("driveDataRef") or {}).get("driveFileId")
    if drive_id:
        url = f"{_gdrive.API}/files/{drive_id}?alt=media&supportsAllDrives=true"
    else:
        res = (att.get("attachmentDataRef") or {}).get("resourceName")
        if not res:
            raise ValueError("attachment has neither a media ref nor a drive id")
        url = f"{API}/media/{urllib.parse.quote(res, safe='')}?alt=media"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read(MAX_ATTACHMENT_BYTES + 1)
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"attachment exceeds {MAX_ATTACHMENT_BYTES} bytes")
    return data


def _extract(content_name: str, raw: bytes, tmp: Path) -> tuple[str, str | bytes]:
    """(source_type, payload) for one downloaded attachment, reusing
    pipeline/ingest.py's extension taxonomy so a .docx or .csv means the same
    thing here as it does for an upload or a fetched link.

    Images are stored but never extracted: there is no text layer to read and
    OCR is a dependency and a bill this does not take on. They still get a row,
    so the corpus knows the screenshot exists and where it came from.
    """
    ext = Path(content_name).suffix.lower()
    if ext in HTML_EXT:
        from feeders.links.sync import extract_html_text
        text = extract_html_text(raw.decode("utf-8", "replace"))
        if not text.strip():
            raise ValueError("html had no extractable text")
        return "external_article", text
    if ext in IMAGE_EXT:
        # A stub body, not a failure: the row is what makes the image findable
        # and the Original tab is what makes it viewable.
        return "image", f"Image shared in chat: {content_name}\n"
    if ext in BINARY_EXT:
        blob = tmp / f"probe{ext}"
        blob.write_bytes(raw)
        text, method = extract_binary(blob)
        if not text.strip():
            raise ValueError(f"no extractable text ({method})")
        return "binary_doc", text
    if ext in DATA_EXT:
        return "dataset", raw
    if ext in PROSE_EXT:
        return "doc", raw.decode("utf-8", "replace")
    # Anything else — a .zip, a .drawio, a bare extensionless file — is KEPT
    # rather than dropped. Nothing can read it, but "a file called X was shared
    # here" is itself a fact worth having, and the original stays downloadable.
    # Failing these was how the feeder lost things quietly in the first place.
    return "attachment", f"Unreadable attachment kept as-is: {content_name}\n"



def write_attachment(target_repo: Path, att: dict, raw: bytes, *, space_id: str,
                     space_label: str, day: str, project_id: str,
                     found_in: str | None = None,
                     connection_id: str | None = None) -> bool:
    """Store one attachment as a source of its own. Mirrors
    feeders/upload/sync.py:write_entry — same layout, same originals/ rule, so
    a PDF shared in Chat and the same PDF uploaded by hand are indistinguishable
    downstream and both offer the Original tab.

    Returns True when it wrote, False when the bytes were already on disk.
    """
    import tempfile

    content_name = att.get("contentName") or "attachment"
    uid = attachment_id(space_id, content_name, _attachment_ref(att))
    slug = attachment_slug(content_name, uid)
    ext = Path(content_name).suffix.lower()

    sources_dir = resolve_source_path(target_repo, f"sources/gchat/{ATTACHMENTS_SUBDIR}")
    originals_dir = sources_dir / ORIGINALS_SUBDIR
    inbox_dir = target_repo / "raw" / "inbox"
    for d in (sources_dir, originals_dir, inbox_dir):
        d.mkdir(parents=True, exist_ok=True)

    original_rel = f"sources/gchat/{ATTACHMENTS_SUBDIR}/{ORIGINALS_SUBDIR}/{slug}{ext}"
    original_path = resolve_source_path(target_repo, original_rel)
    inbox_path = inbox_dir / f"{uid}.md"

    with tempfile.TemporaryDirectory() as d:
        source_type, payload = _extract(content_name, raw, Path(d))

    # A citation hashes the file its path resolves to. For a PDF that file is
    # extracted Markdown, while the original PDF is kept separately.
    stored = payload if isinstance(payload, bytes) else payload.encode()
    sha = hashlib.sha1(stored).hexdigest()[:8]
    stored_ext = ext.lstrip(".") if isinstance(payload, bytes) else "md"
    source_path = sources_dir / f"{slug}.{stored_ext}"
    # Same self-heal condition the upload feeder uses: an unchanged attachment
    # still rewrites when the inbox entry or the kept original is missing,
    # which is what lets rows written before originals/ existed repair
    # themselves on the next sync.
    if (source_path.is_file() and inbox_path.is_file() and original_path.is_file()
            and hashlib.sha1(source_path.read_bytes()).hexdigest()[:8] == sha):
        sources_index.attribute(uid, connection_id)
        return False

    original_path.write_bytes(raw)
    if isinstance(payload, bytes):
        source_path.write_bytes(payload)
        body = summarize_dataset(source_path.parent, [source_path.name])
    else:
        source_path.write_text(payload, encoding="utf-8")
        body = payload

    fm = ["---", f"id: {uid}",
          f"path: sources/gchat/{ATTACHMENTS_SUBDIR}/{slug}.{stored_ext}",
          f"sha: {sha}", f"source_type: {source_type}", "status: active",
          f"date: {day}", 'time: "00:00:00"',
          "authors: []", "---"]
    inbox_path.write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")

    sources_index.record(
        id=uid, project_id=project_id, kind="gchat", name=content_name,
        path=f"sources/gchat/{ATTACHMENTS_SUBDIR}/{slug}.{stored_ext}",
        folder=space_label, original_path=original_rel, found_in=found_in,
        size=len(raw), sha=sha, authors=[], connection_id=connection_id)
    return True


def run(created_after: str = "", project_id: str | None = None,
        on_progress=None, connection_id: str | None = None,
        max_items: int = 0) -> SyncResult:
    """Returns (items_seen, items_written); one item = one space-day.
    Called by connectors.run_now(). Full history each run — the Chat API is
    free and the sha compare keeps unchanged days from being rewritten; the
    created_after remains accepted for caller compatibility, but daily source
    files require complete days. Reconcile complete histories until messages
    have their own durable store: filtering a response by createTime and then
    replacing the daily file loses earlier messages and misses old edits.

    on_progress(done, total, label) counts SPACES, not days or attachments:
    neither count is known until every space has been walked, so spaces are the
    only denominator available up front. It fires again per attachment with the
    same counter and a new label, so the run log shows movement during the long
    download stretch inside one space — the counter stalls, the log does not.

    connection_id attributes every row to the connection whose sync
    produced it. Optional because a hand or cron run has no connection —
    those rows stay unattributed rather than being guessed at.

    max_items caps source entries (complete days and attachments), and the
    number of spaces whose messages are fetched. A cap never truncates a day;
    omitted days, spaces or attachments make the result incomplete.
    """
    max_items = options.max_items(max_items)
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    sources = resolve_source_path(config.GDRIVE_TARGET_REPO, "sources/gchat")
    # Same knowledge home as Drive: ai-brain's own tree by default.
    inbox = config.GDRIVE_TARGET_REPO / "raw" / "inbox"
    sources.mkdir(parents=True, exist_ok=True)
    inbox.mkdir(parents=True, exist_ok=True)

    seen = written = 0
    failures = []
    truncated = False
    settings = {}
    if connection_id:
        # Scope only narrows; it can never be the reason a sync fails. A
        # connection_id is also passed for attribution alone (a hand run, a
        # test), so an id with no row behind it means no scope, not an error.
        try:
            from server import connections
            settings = connections.settings_for(connection_id)
        except Exception:
            log.debug("no stored settings for %s; syncing unscoped", connection_id)
    spaces = scoped_spaces(settings.get("scope"))
    for n, space in enumerate(spaces, 1):
        if max_items and (seen >= max_items or n > max_items):
            truncated = True
            break
        if on_progress:
            on_progress(n, len(spaces), space.get("displayName") or space["name"])
        try:
            by_day: dict[str, list[dict]] = defaultdict(list)
            if max_items:
                messages, more = list_message_sample(space["name"], max_items - seen)
                truncated = truncated or more
            else:
                messages = list_messages(space["name"])
            for m in messages:
                if carries_content(m):
                    by_day[m["createTime"].split("T")[0]].append(m)
        except Exception as e:
            log.exception("gchat: failed to list messages for %r, skipping",
                         space.get("name"))
            # Keyed on the space, not a space-day: the day is unknown when the
            # listing itself failed. phase_scrape clears these before each run,
            # so a space that later succeeds stops being reported as failed.
            sources_index.record_failure(
                id=f"gchat-{space['name'].split('/')[-1]}",
                project_id=project_id, kind="gchat",
                name=space.get("displayName") or space["name"], reason=e,
                connection_id=connection_id)
            failures.append(failure(f"gchat-{space['name'].split('/')[-1]}",
                                    space.get("displayName") or space["name"], e))
            continue
        space_id = space["name"].split("/")[-1]
        for day, msgs in sorted(by_day.items()):
            seen += 1
            text = _render_day(space, day, msgs)
            sha = hashlib.sha1(text.encode()).hexdigest()[:8]
            slug = (f"{_slug(space.get('displayName') or space_id)[:40]}"
                    f"-{day}-{space_id[-6:].lower()}")
            source_path = sources / f"{slug}.md"
            existing_sha = (hashlib.sha1(source_path.read_text().encode()).hexdigest()[:8]
                            if source_path.is_file() else None)
            inbox_path = inbox / f"gchat-{space_id}-{day}.md"
            changed = existing_sha != sha or not inbox_path.is_file()
            if existing_sha != sha:
                source_path.write_text(text, encoding="utf-8")
            source_rel = f"sources/gchat/{slug}.md"
            authors = list(dict.fromkeys(_sender(m) for m in msgs))
            entry = (
                "---\n"
                f"id: gchat-{space_id}-{day}\n"
                f"path: {source_rel}\n"
                f"sha: {sha}\n"
                "source_type: chat_thread\n"
                "status: active\n"
                f"date: {day}\n"
                f'time: "{msgs[-1]["createTime"].split("T")[1][:8]}"\n'
                f"authors: [{', '.join(json.dumps(a) for a in authors)}]\n"
                "---\n\n"
                + text + "\n"
            )
            if changed:
                inbox_path.write_text(entry, encoding="utf-8")
            sources_index.record(
                id=f"gchat-{space_id}-{day}", project_id=project_id, kind="gchat",
                name=f"{space.get('displayName') or space_id} — {day}",
                path=source_rel,
                size=len(text.encode()), sha=sha, authors=authors,
                connection_id=connection_id)
            written += int(changed)

        # Attachments after the transcript, so a download failure never costs
        # the day's text. Each is isolated: one unreadable file must not take
        # the rest of the space with it, the same per-item isolation the links
        # feeder uses per URL.
        for day, msgs in sorted(by_day.items()):
            for m in msgs:
                for att in m.get("attachment") or []:
                    if max_items and seen >= max_items:
                        truncated = True
                        break
                    name = att.get("contentName") or "attachment"
                    seen += 1
                    if already_stored(config.GDRIVE_TARGET_REPO, space_id, att):
                        sources_index.attribute(
                            attachment_id(space_id, name, _attachment_ref(att)),
                            connection_id)
                        continue
                    # Ticks inside the space, not just between spaces. A busy
                    # space is minutes of downloads, and a counter that only
                    # moves per space reads as a hung run ("2/117" for an hour).
                    if on_progress:
                        on_progress(n, len(spaces),
                                    f"{space.get('displayName') or space_id} — {name}")
                    try:
                        raw = download_attachment(att)
                        if write_attachment(
                                config.GDRIVE_TARGET_REPO, att, raw,
                                space_id=space_id,
                                space_label=space.get("displayName") or space_id,
                                day=day, project_id=project_id,
                                # The space-day transcript this file was posted
                                # in — the id the Sources row for that day uses.
                                found_in=f"gchat-{space_id}-{day}",
                                connection_id=connection_id):
                            written += 1
                    except Exception as e:
                        log.warning("gchat: attachment %r in %s: %s", name, space_id, e)
                        sources_index.record_failure(
                            id=attachment_id(space_id, name, _attachment_ref(att)),
                            project_id=project_id, kind="gchat", name=name,
                            reason=e, connection_id=connection_id)
                        failures.append(failure(
                            attachment_id(space_id, name, _attachment_ref(att)), name, e))
    if truncated:
        failures.append(failure("gchat-backlog", "Google Chat sample",
                                f"max_items={max_items} left days, spaces or attachments unsynced; watermark unchanged"))
    return SyncResult(seen, written, failures)


# --------------------------------------------------------------------- scope
# Spaces, split by whether Google made them or a person did. Meeting spaces are
# listed separately because most hold one short conversation and there are
# usually far more of them — syncing all of them buries the team spaces the
# wiki is actually for.

MEETING_SPACE_TYPES = ("MEETING", "HUDDLE")


def _is_meeting_space(space: dict) -> bool:
    if space.get("spaceType") in MEETING_SPACE_TYPES:
        return True
    # Meet names an auto-created space after the call, and marks it externally
    # user-allowed far more often than a team space. The name check is the
    # reliable half; treat anything else as a team space rather than hiding it.
    return bool(space.get("externalUserAllowed")) and not space.get("displayName")


def list_space_options(_settings: dict | None = None) -> list[dict]:
    groups = {"team": {"id": "team", "name": "Team spaces",
                       "note": "created by a person", "items": []},
              "meeting": {"id": "meeting", "name": "Meeting spaces",
                          "note": "created automatically by Google Meet", "items": []}}
    for space in list_spaces():
        target = "meeting" if _is_meeting_space(space) else "team"
        groups[target]["items"].append({
            "id": space["name"],
            "name": space.get("displayName") or space["name"],
            "detail": str(space.get("membershipCount", {}).get("joinedDirectHumanUserCount") or ""),
        })
    for group in groups.values():
        group["items"].sort(key=lambda s: s["name"].lower())
    return [g for g in groups.values() if g["items"]]


def scoped_spaces(scope: dict | None) -> list[dict]:
    """The spaces this connection reads. No scope means every named space,
    which is what this feeder did before scope existed."""
    spaces = list_spaces()
    chosen = set((scope or {}).get("items") or ())
    return [s for s in spaces if s["name"] in chosen] if chosen else spaces
