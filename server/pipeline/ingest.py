#!/usr/bin/env python3
"""Pointers in S3 → brain_sources and brain_parts in Postgres. Deterministic, free.

Every run is a full pass over the pointers under `prefix`, but only pointers
whose sha changed (or whose source copy was missing last time) touch S3 for
the body and re-split. A pointer that vanished marks its source `removed`.

Run: python3 -m server.pipeline.ingest [--project default] [--prefix raw/inbox/]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import storage  # noqa: E402
from server.db import connect  # noqa: E402
from server.pipeline.pointers import Pointer, parse_pointer  # noqa: E402
from server.pipeline.schema import ensure_schema  # noqa: E402
from server.pipeline.split import UNIT_BODY_CHARS, split_body  # noqa: E402

LEADING_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
DATA_URI = re.compile(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=]+")


def _body_of(text: str) -> str:
    """Source copies are plain text; strip a frontmatter block if a connector left one."""
    body = LEADING_FRONTMATTER.sub("", text, count=1)
    return DATA_URI.sub("[image data omitted]", body)


def _upsert_source(db, ptr: Pointer, project_id: str, status: str) -> None:
    db.execute(
        """
        INSERT INTO brain_sources
            (id, project_id, kind, type, name, path, url, sha, authors, status,
             source_type, occurred_at, found_in, publisher)
        VALUES (%s, %s, %s, 'file', %s, %s, %s, %s, %s::jsonb, %s,
                %s, %s, %s, %s::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            project_id = EXCLUDED.project_id, kind = EXCLUDED.kind, name = EXCLUDED.name,
            path = EXCLUDED.path, url = EXCLUDED.url, sha = EXCLUDED.sha,
            authors = EXCLUDED.authors, status = EXCLUDED.status,
            source_type = EXCLUDED.source_type, occurred_at = EXCLUDED.occurred_at,
            found_in = EXCLUDED.found_in, publisher = EXCLUDED.publisher,
            scraped_at = now()
        """,
        (ptr.id, project_id, ptr.id.split("-", 1)[0], Path(ptr.path).name, ptr.path,
         ptr.source_url, ptr.sha, json.dumps(ptr.authors), status,
         ptr.source_type, ptr.date, ptr.found_in, json.dumps(ptr.publisher)))


def _replace_parts(db, ptr: Pointer, project_id: str, body: str) -> tuple[int, int]:
    db.execute("DELETE FROM brain_parts WHERE source_id = %s", (ptr.id,))
    parts = split_body(body, ptr.source_type, UNIT_BODY_CHARS)
    oversized = 0
    for p in parts:
        oversized += p.oversized
        db.execute(
            """INSERT INTO brain_parts
                   (id, source_id, project_id, n, total, anchor, chars, body, body_sha, oversized)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)""",
            (f"{ptr.id}-p{p.n:02d}", ptr.id, project_id, p.n, p.total,
             json.dumps(p.anchor), len(p.text), p.text,
             hashlib.sha256(p.text.encode("utf-8")).hexdigest()[:8], p.oversized))
    return len(parts), oversized


def ingest(project_id: str = "default", prefix: str = "raw/inbox/", prune: bool = True) -> dict:
    counts = dict(seen=0, new=0, changed=0, unchanged=0, removed=0,
                  missing=0, parts=0, oversized=0, failed=0)
    with connect() as db:
        ensure_schema(db)
        db.execute("INSERT INTO brain_projects (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                   (project_id, project_id))
        # Scoped to the outer project_id, not each pointer's resolved owner below —
        # a pointer whose own project_id differs from the argument is always "new"
        # here and sits outside removal detection too. Known, accepted limitation.
        existing = {r["id"]: (r["sha"], r["status"]) for r in db.execute(
            "SELECT id, sha, status FROM brain_sources WHERE project_id = %s", (project_id,))}
        seen: set[str] = set()
        for rel in storage.list_rel_paths(prefix):
            if not rel.endswith(".md"):
                continue
            # Added unconditionally, before parse_pointer() gets a chance to fail:
            # every real pointer's filename stem equals its `id` by connector
            # convention, so this is what keeps a pointer that errors below from
            # being wrongly swept up as "removed" further down — it hasn't
            # disappeared, it just failed to process this run.
            seen.add(Path(rel).stem)
            try:
                ptr = parse_pointer(storage.read_full(rel))
                counts["seen"] += 1
                prior = existing.get(ptr.id)
                if prior and prior == (ptr.sha, "ok"):
                    counts["unchanged"] += 1
                    continue
                # A pointer may name its own project_id (e.g. a shared link found from
                # another project's chat). Register it before upserting a source under
                # it, or the brain_sources_project_id_fkey insert below would crash the
                # whole run for one bad pointer instead of just recording that source.
                owner = ptr.project_id or project_id
                db.execute("INSERT INTO brain_projects (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                           (owner, owner))
                # new/changed is counted per outcome, not up front: whether this pointer
                # is new, sha-changed, or recovering from a prior "missing" all only
                # matter once we know whether the fetch below actually succeeds.
                try:
                    body = _body_of(storage.read_full(ptr.path))
                except FileNotFoundError:
                    counts["missing"] += 1
                    # Still missing with the same sha as last time: already counted once
                    # when it first went missing, so this pointer alone shouldn't also
                    # count as "changed" on every single re-run — but if the sha changed,
                    # or this is the first time we've ever seen it, it IS a real change
                    # worth counting even though the fetch failed again.
                    if not (prior and prior[0] == ptr.sha and prior[1] == "missing"):
                        counts["new" if prior is None else "changed"] += 1
                    _upsert_source(db, ptr, owner, "missing")
                    db.execute("DELETE FROM brain_parts WHERE source_id = %s", (ptr.id,))
                    continue
                # Reaching here means the fetch succeeded and we're already past the
                # "unchanged" fast path above, so this is unconditionally a real change:
                # new, sha-changed, or recovered from a prior "missing".
                counts["new" if prior is None else "changed"] += 1
                _upsert_source(db, ptr, owner, "ok")
                n, big = _replace_parts(db, ptr, owner, body)
                counts["parts"] += n
                counts["oversized"] += big
                if big:
                    print(f"  ingest: {ptr.id} has {big} part(s) over {UNIT_BODY_CHARS} chars with no structure to split on")
            except Exception as e:
                # Any other bad pointer (malformed frontmatter, an S3 timeout,
                # whatever) must not abort the whole run — everything already
                # processed this pass is still worth keeping at the final commit.
                counts["failed"] += 1
                print(f"  ingest: failed to process {rel}: {type(e).__name__}: {e}")
                continue
        if prune:
            for gone in set(existing) - seen:
                if existing[gone][1] != "removed":
                    db.execute("UPDATE brain_sources SET status = 'removed' WHERE id = %s", (gone,))
                    db.execute("DELETE FROM brain_parts WHERE source_id = %s", (gone,))
                    counts["removed"] += 1
        db.commit()
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="default")
    ap.add_argument("--prefix", default="raw/inbox/")
    ap.add_argument("--no-prune", action="store_false", dest="prune", default=True,
                     help="skip removal detection — for an exploratory run under a narrow --prefix")
    a = ap.parse_args()
    print(ingest(a.project, a.prefix, prune=a.prune))
