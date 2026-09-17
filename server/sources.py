"""brain_sources: the DB index of fed files (not code — a tracked repo's own
files are browsed through the wiki, never listed here). A feeder inserts one
row the instant it WRITES a file under sources/, via record() below — and
writes only what changed, so this table tracks what syncs have written rather
than what the connectors can see. See record()'s docstring: that distinction
has been mistaken for a bug more than once.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import config, corpus
from .db import connect

log = logging.getLogger(__name__)

FIELDS = ("id, project_id, connection_id, kind, type, name, path, url, "
          "detail, folder, bytes, sha, authors, scraped_at, status, error, "
          "original_path, wiki_queued_at, found_in")

# Everything except the Sources listing wants fetched files only: a failed
# row has no bytes to search, no path to cite and no authors to credit.
OK_ONLY = "status = 'ok'"


def record(*, id: str, project_id: str, kind: str, name: str, path: str,
          connection_id: str | None = None, type: str = "file",
          url: str | None = None, detail: str | None = None,
          folder: str | None = None, original_path: str | None = None,
          found_in: str | None = None,
          size: int | None = None, sha: str | None = None,
          authors: list[str] | None = None,
          status: str = "ok", error: str | None = None) -> None:
    """Upsert one source row — safe to call every time a feeder (re)writes the
    same file, which is exactly when scraped_at should move.

    THE SHA-COMPARE PROPERTY, and why it keeps biting
    -------------------------------------------------
    Every feeder compares the content it just fetched against the file already
    at sources/<kind>/<slug>.md and, when the sha matches, `continue`s without
    writing. record() is therefore called ONLY for items that were new or
    changed — never for the unchanged majority of a re-sync.

    That is the right behaviour (it is the whole reason a re-sync is cheap) but
    it means **the state of this table is not a function of what the connector
    can see — it is a function of what a sync happened to write.** Three
    consequences, all of which were mistaken for bugs elsewhere before being
    traced back here:

    1. Rows for content scraped before this table existed are never created. A
       re-sync does not fix it: the files are already on disk, unchanged, so
       nothing calls record(). The corpus looks unindexed and no amount of
       syncing changes that.
    2. A sync's own numbers read as a failure when they are not. A Chat sync
       that lists 585 space-days and reports `wrote=9` did not miss 576 — they
       were byte-identical and skipped.
    3. A new column cannot be backfilled by re-syncing. connection_id stayed
       NULL through a full run for exactly this reason; attribute() below exists
       to set it from the skip branch, and deliberately does NOT move
       scraped_at, because nothing was refetched.

    So: anything that must converge on existing rows needs its own path out of
    the skip branch (see attribute()), or the file has to be removed from
    sources/ so the feeder sees it as new.

    status/error are set through record_failure() rather than here; the upsert
    resets them so a retry that succeeds clears the previous failure instead of
    leaving a stale error on a row that now has content.
    """
    with connect() as c:
        c.execute(
            "INSERT INTO brain_sources (id, project_id, connection_id, kind, type, "
            "name, path, url, detail, folder, original_path, found_in, bytes, sha, "
            "authors, scraped_at, status, error) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "project_id = EXCLUDED.project_id, connection_id = EXCLUDED.connection_id, "
            "name = EXCLUDED.name, path = EXCLUDED.path, url = EXCLUDED.url, "
            "detail = EXCLUDED.detail, folder = EXCLUDED.folder, "
            # COALESCE, not EXCLUDED: record_failure() re-upserts the same row
            # with everything but id/kind/name left at its default, and a
            # straight assignment there would throw away the original a
            # successful earlier scrape stored.
            "original_path = COALESCE(EXCLUDED.original_path, brain_sources.original_path), "
            # Same COALESCE reasoning: the first place a thing was found stays
            # the answer, and a failure re-upsert must not erase it.
            "found_in = COALESCE(EXCLUDED.found_in, brain_sources.found_in), "
            "bytes = EXCLUDED.bytes, sha = EXCLUDED.sha, "
            "authors = EXCLUDED.authors, scraped_at = now(), "
            "status = EXCLUDED.status, error = EXCLUDED.error",
            (id, project_id, connection_id, kind, type, name, path, url, detail,
             folder, original_path, found_in, size, sha, json.dumps(authors or []),
             status, error))


def record_failure(*, id: str, project_id: str, kind: str, name: str,
                   reason: str, path: str = "", url: str | None = None,
                   connection_id: str | None = None) -> None:
    """Record an item the connector saw and could not bring in.

    Called from a feeder's except branch, where the item id is still in hand —
    the same place it used to be logged and dropped. Never raises: a failure to
    record a failure must not abort the rest of the sync.
    """
    try:
        record(id=id, project_id=project_id, kind=kind, name=name, path=path,
               url=url, connection_id=connection_id,
               status="failed", error=str(reason)[:500])
    except Exception:
        log.exception("could not record the failure of %r", id)


# `scraped_at` alone is not unique — a sync writes many rows inside the same
# transaction timestamp — so the key includes `id` to break ties. Without that a
# page boundary landing mid-timestamp would drop or repeat rows.
def _encode_cursor(row: dict) -> str:
    return f"{row['scraped_at'].isoformat()}|{row['id']}"


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    at, sep, rid = cursor.partition("|")
    return (at, rid) if sep and at and rid else None


def attribute(id: str, connection_id: str | None) -> None:
    """Point an existing row at the connection that just re-checked it.

    Deliberately NOT record(): that row was not refetched — the sha compare
    skipped it — so moving scraped_at would claim a fetch that did not happen.
    Only the attribution is newly known.

    This exists because the same sha compare that makes a re-sync cheap also
    means an unchanged file never calls record(), so rows scraped before
    attribution existed would stay unattributed forever.
    """
    if not connection_id:
        return
    with connect() as c:
        c.execute(
            "UPDATE brain_sources SET connection_id = %s "
            "WHERE id = %s AND connection_id IS DISTINCT FROM %s",
            (connection_id, id, connection_id))


def set_folder(id: str, folder: str | None) -> None:
    """Record where an existing row sits in its connector's hierarchy.

    Not record(), for attribute()'s reason: the sha compare skipped this file,
    so it was never refetched and moving scraped_at would claim a fetch that
    did not happen. Only the folder is newly known.

    Without this, adding folder capture to a feeder would backfill nothing at
    all — every file already on disk comes back byte-identical, so none of them
    reach record() again. That is the sha-compare property in its purest form.
    """
    if not folder:
        return
    with connect() as c:
        c.execute(
            "UPDATE brain_sources SET folder = %s "
            "WHERE id = %s AND folder IS DISTINCT FROM %s",
            (folder, id, folder))


def set_queued(project_id: str, ids: list[str], queued: bool) -> int:
    """Mark or unmark sources for a wiki write-up. Returns rows changed.

    Scoped to project_id even though ids are globally unique: the id list comes
    straight from the browser, and the caller has a project in hand, so there
    is no reason to let a request touch another project's rows.

    Failed rows are excluded — there is no file behind one, so absorb would
    have nothing to read.
    """
    if not ids:
        return 0
    stamp = "now()" if queued else "NULL"
    with connect() as c:
        rows = c.execute(
            f"UPDATE brain_sources SET wiki_queued_at = {stamp} "
            f"WHERE project_id = %s AND id = ANY(%s) AND {OK_ONLY} RETURNING id",
            (project_id, list(ids))).fetchall()
    return len(rows)


def queued(project_id: str) -> list[dict]:
    """Every source waiting to be written up, oldest mark first — which is the
    order the absorb run will process them in."""
    with connect() as c:
        return c.execute(
            f"SELECT {FIELDS} FROM brain_sources "
            f"WHERE project_id = %s AND wiki_queued_at IS NOT NULL AND {OK_ONLY} "
            "ORDER BY wiki_queued_at, id", (project_id,)).fetchall()


def clear_failures(project_id: str, kind: str) -> int:
    """Drop this kind's recorded failures before a fresh scrape.

    Without it a failure row outlives the problem: a space-level failure is
    keyed on the space, a later successful sync writes space-DAY rows, so the
    ids never collide and the stale failure would sit in the listing forever.
    Clearing up front makes the listing mean "what the most recent scrape could
    not bring in", which is the only reading that stays true.
    """
    with connect() as c:
        return c.execute(
            "DELETE FROM brain_sources "
            "WHERE project_id = %s AND kind = %s AND status = 'failed'",
            (project_id, kind)).rowcount


# Where Drive shows no parent at all. A root-level pseudo-folder rather than a
# blank: these are real files, mostly transcripts shared out of other people's
# Drives, and hiding them because Drive will not say where they live would lose
# three quarters of the corpus.
NO_FOLDER = "No folder (shared)"

# What "one thing" means per connector, as a SQL expression over columns that
# already exist. gchat writes one file per space per day, named
# "<space> — <YYYY-MM-DD>", so the space is the part before the dash. gdrive has
# no folder to group on — its feeder never asked Drive for `parents` — and the
# owner is the only real axis in what it does record.
GROUP_BY = {
    # No connector chosen: the connectors themselves are the groups. This is
    # also the only correct way to list them — the chip row used to derive them
    # from one page of rows, so it showed whichever connector had scraped most
    # recently and hid the rest.
    "": "kind",
    "gchat": "split_part(name, ' — ', 1)",
    "gdrive": f"coalesce(folder, '{NO_FOLDER}')",
    "upload": f"coalesce(folder, '{NO_FOLDER}')",
}


def _filters(project_id: str, q: str, kind: str | None, status: str | None,
             connection_id: str | None, group: str | None = None):
    """The WHERE shared by the row list and the group counts.

    One builder because the two are read side by side: a group chip saying 146
    and a list showing 12 of them is worse than either alone, and that is what
    two copies of this drifting apart would produce.
    """
    where, args = ["project_id = %s"], [project_id]
    if q:
        where.append("name ILIKE %s")
        args.append(f"%{q}%")
    if kind:
        where.append("kind = %s")
        args.append(kind)
    # The one reader that deliberately shows failures: seeing what a sync could
    # not bring in is the point of the listing. `status` narrows to one or the
    # other; omitted means both.
    if status:
        where.append("status = %s")
        args.append(status)
    # Rows scraped before attribution existed, and any hand or cron run, carry
    # a NULL connection_id — so this narrows rather than partitions: no filter
    # still shows everything.
    if connection_id:
        where.append("connection_id = %s")
        args.append(connection_id)
    # `kind` is required, not just looked up: GROUP_BY[""] is "kind" so that the
    # chip row can list connectors as a group level, and without this guard a
    # group passed with no kind would narrow `kind` by a space name and quietly
    # match nothing. At the top level the connector IS the kind filter.
    if group and kind and (expr := GROUP_BY.get(kind)):
        where.append(f"{expr} = %s")
        args.append(group)
    return where, args


def list_groups(project_id: str, *, kind: str | None = None, q: str = "",
                status: str | None = None,
                connection_id: str | None = None) -> list[dict]:
    """One row per group at this level, with how many items it holds.

    Without `kind` the groups are the connectors; with one they are that
    connector's own unit — a Chat space, a Drive owner.

    Aggregated here rather than in the client because the row list is
    keyset-paginated: grouping a single 200-row page would show Dev-Group's 146
    days as however many of them happened to land on that page.
    """
    expr = GROUP_BY.get(kind or "")
    if not expr:
        return []
    where, args = _filters(project_id, q, kind, status, connection_id)
    with connect() as c:
        return c.execute(
            f"SELECT {expr} AS key, count(*) AS count, max(scraped_at) AS last "
            f"FROM brain_sources WHERE {' AND '.join(where)} "
            "GROUP BY 1 ORDER BY count DESC, 1", args).fetchall()


def list_subfolders(project_id: str, *, kind: str, folder: str = "", q: str = "",
                    status: str | None = None,
                    connection_id: str | None = None) -> list[dict]:
    """The immediate children of one folder, as a Drive-shaped tree level.

    `count` is everything beneath a child, not just its direct files — that is
    what tells you whether opening it is worth it. The files sitting directly
    in `folder` are not here: they come from list_sources(group=folder), whose
    GROUP_BY match is already an exact folder compare.
    """
    if not GROUP_BY.get(kind or "") or kind == "":
        return []
    where, args = _filters(project_id, q, kind, status, connection_id)
    if folder:
        # left() rather than LIKE: a folder name may contain _ or %, and LIKE
        # would read both as wildcards and pull in unrelated siblings.
        where.append("left(folder, %s) = %s")
        args.extend([len(folder) + 1, folder + "/"])
        depth = folder.count("/") + 2
    else:
        where.append("folder IS NOT NULL")
        depth = 1
    # depth is an int derived from a '/' count, never interpolated user text.
    seg = f"split_part(folder, '/', {depth})"
    with connect() as c:
        rows = c.execute(
            f"SELECT {seg} AS name, count(*) AS count, max(scraped_at) AS last "
            f"FROM brain_sources WHERE {' AND '.join(where)} AND {seg} <> '' "
            "GROUP BY 1 ORDER BY 1", args).fetchall()
        out = [{"name": r["name"],
                "path": f"{folder}/{r['name']}" if folder else r["name"],
                "count": r["count"], "last": r["last"]} for r in rows]
        if not folder:
            unparented, uargs = _filters(project_id, q, kind, status, connection_id)
            unparented.append("folder IS NULL")
            n = c.execute(f"SELECT count(*) AS n, max(scraped_at) AS last "
                          f"FROM brain_sources WHERE {' AND '.join(unparented)}",
                          uargs).fetchone()
            if n["n"]:
                out.append({"name": NO_FOLDER, "path": NO_FOLDER,
                            "count": n["n"], "last": n["last"]})
    return out


def list_sources(project_id: str, *, q: str = "", kind: str | None = None,
                 status: str | None = None, connection_id: str | None = None,
                 group: str | None = None,
                 limit: int = 500, cursor: str = "") -> dict:
    """One page of this project's scraped files, newest first.

    Keyset paging rather than OFFSET: rows arrive continuously while a scrape
    runs, and OFFSET would shift every page under the reader as new rows land
    at the top. `total` is counted separately and is the real match count, not
    the page size.
    """
    where, args = _filters(project_id, q, kind, status, connection_id, group)

    count_where, count_args = list(where), list(args)
    if cursor and (key := _decode_cursor(cursor)):
        where.append("(scraped_at, id) < (%s, %s)")
        args.extend(key)

    args.append(limit)
    with connect() as c:
        rows = c.execute(
            f"SELECT {FIELDS} FROM brain_sources WHERE {' AND '.join(where)} "
            "ORDER BY scraped_at DESC, id DESC LIMIT %s", args).fetchall()
        total = c.execute(
            f"SELECT count(*) AS n FROM brain_sources "
            f"WHERE {' AND '.join(count_where)}", count_args).fetchone()["n"]
    # A full page might still be the last one; the client stops when a fetch
    # comes back empty rather than guessing from the row count.
    next_cursor = _encode_cursor(rows[-1]) if len(rows) == limit else None
    return {"rows": rows, "total": total, "cursor": next_cursor}


def failure_reasons(project_id: str, kind: str | None = None) -> list[dict]:
    """Failed rows grouped by why, with the digits in HTTP codes normalised so
    "HTTP Error 403" and "HTTP Error 404" do not read as one bucket.

    Without this a couple of thousand failures is one undifferentiated wall, and
    the difference that matters — a dead page nothing recovers versus a rate
    limit that drains on its own — is invisible.
    """
    where, args = ["project_id = %s", "status = 'failed'"], [project_id]
    if kind:
        where.append("kind = %s")
        args.append(kind)
    with connect() as c:
        rows = c.execute(
            f"SELECT coalesce(error, 'unknown') AS reason, count(*) AS n "
            f"FROM brain_sources WHERE {' AND '.join(where)} "
            "GROUP BY reason ORDER BY n DESC LIMIT 200", tuple(args)).fetchall()
    return [{"reason": r["reason"], "count": r["n"]} for r in rows]


def get(source_id: str) -> dict | None:
    with connect() as c:
        return c.execute(f"SELECT {FIELDS} FROM brain_sources WHERE id = %s",
                         (source_id,)).fetchone()


def get_by_urls(project_id: str, urls: list[str]) -> dict[str, dict]:
    """url -> row, for the subset of `urls` this project has actually fetched.

    Powers the link pills on a transcript: a URL we hold gets a title and
    something to open, one we do not gets a bare-domain pill that says so.
    Failed rows are INCLUDED — "we tried this and could not reach it" is a
    different answer from "we never tried", and the pill shows both.
    """
    if not urls:
        return {}
    with connect() as c:
        rows = c.execute(
            f"SELECT {FIELDS} FROM brain_sources "
            "WHERE project_id = %s AND kind = 'links' AND url = ANY(%s)",
            (project_id, list(urls))).fetchall()
    return {r["url"]: r for r in rows if r["url"]}


def get_by_paths(paths: list[str]) -> dict[str, dict]:
    """path -> source row, for the subset of `paths` this project has fed
    (a citation into a tracked repo's own code has no brain_sources row —
    callers degrade gracefully for those)."""
    if not paths:
        return {}
    with connect() as c:
        rows = c.execute(
            f"SELECT {FIELDS} FROM brain_sources "
            f"WHERE path = ANY(%s) AND {OK_ONLY}",
                         (paths,)).fetchall()
    return {r["path"]: r for r in rows}


def find(project_id: str, q: str, limit: int = 30,
         kind: str | None = None) -> list[dict]:
    """Search mode: connector/title/snippet/scraped-at over fed sources — free,
    no LLM call, distinct from Ask's semantic wiki recall. A live text scan
    over this project's own sources, same budget as articles_citing() below.

    `kind` narrows it to one connector, which is what "search inside my files"
    is: the same scan with kind='upload'. It also bounds the worst case — a
    needle that matches nothing reads every file under that kind and stops,
    rather than every file in the project.

    Measured 2026-09-17 on a 4,127-source / 1.5GB corpus: 1.9s for a needle
    that matches nothing (reads everything), 0.16s for one that does. The limit
    below is what keeps the common case off the full scan. If this stops being
    comfortable, a tsvector column on brain_sources is the next move — not a
    smarter loop here.
    """
    needle = q.strip().lower()
    if not needle:
        return []
    where, args = ["project_id = %s", OK_ONLY], [project_id]
    if kind:
        where.append("kind = %s")
        args.append(kind)
    with connect() as c:
        rows = c.execute(
            f"SELECT {FIELDS} FROM brain_sources "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY scraped_at DESC", tuple(args)).fetchall()
    out = []
    for r in rows:
        if len(out) >= limit:
            break
        if needle in r["name"].lower():
            snippet = r["name"]
        else:
            try:
                text = (config.ROOT / r["path"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            low = text.lower()
            at = low.find(needle)
            if at == -1:
                continue
            start, end = max(0, at - 70), min(len(text), at + len(needle) + 70)
            snippet = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
        out.append({"id": r["id"], "kind": r["kind"], "type": r["type"], "name": r["name"],
                   "snippet": snippet, "scrapedAt": r["scraped_at"].isoformat(),
                   "url": r["url"], "path": r["path"], "sha": r["sha"]})
    return out


def articles_citing(source_id: str, project_id: str) -> list[dict]:
    """Every wiki article, within this project's roots, whose body cites this
    source's path — the Sources table's reverse lookup. A live grep rather
    than a maintained index: absorb doesn't record citations anywhere durable
    today, and a personal corpus is small enough that this is instant."""
    src = get(source_id)
    if not src:
        raise KeyError(f"no such source: {source_id}")
    needle = f"{src['path']}@"
    out = []
    for root in corpus.roots(project_id):
        if not root.is_dir():
            continue
        for md in root.rglob("*.md"):
            if md.name.startswith("_"):
                continue
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle not in text:
                continue
            from .wikilib import fm_field, frontmatter
            fm, _ = frontmatter(text)
            title = fm_field(fm, "title") or md.stem
            # The SAME composite id the wiki graph hands out — "<repo>/<rel>.md",
            # where repo is the root's parent directory. It used to return the
            # bare relative path with .md stripped, which no other endpoint
            # accepts: /api/wiki/article splits a repo off the front, so the
            # "Written up in" chips could not have opened anything even once
            # they were wired to navigate.
            out.append({"path": f"{root.parent.name}/{md.relative_to(root).as_posix()}",
                       "title": title, "type": fm_field(fm, "type") or "?"})
    return out
