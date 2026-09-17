#!/usr/bin/env python3
"""Self-check for ingest, end to end against the bucket and Postgres.

Uses one real pointer, scoped by prefix, under a throwaway project that is
deleted at the end. Skips itself when either store is unreachable.

Run: python3 -m server.pipeline.test_ingest
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import config, storage  # noqa: E402
from server.db import connect  # noqa: E402
from server.pipeline.ingest import _body_of, ingest  # noqa: E402

PROJECT = "test-phase-a"
CHAT_PREFIX = "raw/inbox/gchat-AAQADWehVa0-2026-03-21"     # source copy exists
LINK_PREFIX = "raw/inbox/link-f88398332379"                # source copy missing in the bucket


def _up() -> bool:
    try:
        with connect() as c:
            c.execute("SELECT 1")
        return storage.enabled() and storage.head_etag(CHAT_PREFIX + ".md") is not None
    except Exception:
        return False


def _cleanup():
    with connect() as db:
        db.execute("DELETE FROM brain_projects WHERE id = %s", (PROJECT,))
        db.commit()


def _rows(sql, *args):
    with connect() as db:
        return db.execute(sql, args).fetchall()


def test_data_uri_is_replaced_with_a_placeholder_before_storage():
    body = ("Some real text.\n\n"
            "![img](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA)\n\n"
            "More text.")
    result = _body_of(body)
    assert "base64" not in result and "iVBOR" not in result, result
    assert "[image data omitted]" in result, result
    assert "Some real text." in result and "More text." in result, result


def test_one_pointer_becomes_a_source_and_parts_then_is_unchanged():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    try:
        first = ingest(PROJECT, CHAT_PREFIX)
        assert first["seen"] == 1 and first["new"] == 1 and first["parts"] >= 1, first
        src = _rows("SELECT id, sha, status, source_type, occurred_at FROM brain_sources WHERE project_id = %s", PROJECT)
        assert len(src) == 1 and src[0]["sha"] == "572e8928" and src[0]["status"] == "ok", src
        assert src[0]["source_type"] == "chat_thread" and str(src[0]["occurred_at"]) == "2026-03-21", src
        parts = _rows("SELECT id, n, total, chars FROM brain_parts WHERE project_id = %s ORDER BY n", PROJECT)
        assert parts[0]["id"] == "gchat-AAQADWehVa0-2026-03-21-p01", parts[0]
        assert all(p["chars"] <= 12_000 for p in parts), parts
        second = ingest(PROJECT, CHAT_PREFIX)
        assert second["unchanged"] == 1 and second["new"] == 0 and second["changed"] == 0, second
    finally:
        _cleanup()


def test_missing_source_copy_is_recorded_not_fatal():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    try:
        counts = ingest(PROJECT, LINK_PREFIX)
        assert counts["seen"] == 1 and counts["missing"] == 1 and counts["parts"] == 0, counts
        src = _rows("SELECT status FROM brain_sources WHERE project_id = %s", PROJECT)
        assert src[0]["status"] == "missing", src
        again = ingest(PROJECT, LINK_PREFIX)
        assert again["missing"] == 1, "a missing source is retried on every run, not treated as unchanged"
        assert again["changed"] == 0, again
    finally:
        _cleanup()


def test_source_recovering_from_missing_counts_as_changed():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    try:
        ingest(PROJECT, CHAT_PREFIX)
        # Simulate "missing last run" without touching the real pointer or sha in
        # S3: flip the stored status back to missing and drop its parts, exactly
        # what ingest() itself would have left behind had the fetch failed once.
        with connect() as db:
            db.execute("UPDATE brain_sources SET status = 'missing' WHERE project_id = %s", (PROJECT,))
            db.execute("DELETE FROM brain_parts WHERE project_id = %s", (PROJECT,))
            db.commit()
        counts = ingest(PROJECT, CHAT_PREFIX)
        assert counts["seen"] == 1 and counts["changed"] == 1 and counts["new"] == 0, counts
        assert counts["missing"] == 0 and counts["unchanged"] == 0, counts
        src = _rows("SELECT status FROM brain_sources WHERE project_id = %s", PROJECT)
        assert src[0]["status"] == "ok", src
        parts = _rows("SELECT count(*) AS c FROM brain_parts WHERE project_id = %s", PROJECT)
        assert parts[0]["c"] >= 1, parts
    finally:
        _cleanup()


def test_pointer_that_disappears_marks_its_source_removed():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    try:
        ingest(PROJECT, CHAT_PREFIX)
        counts = ingest(PROJECT, "raw/inbox/no-such-prefix-")
        assert counts["seen"] == 0 and counts["removed"] == 1, counts
        src = _rows("SELECT status FROM brain_sources WHERE project_id = %s", PROJECT)
        assert src[0]["status"] == "removed", src
        parts = _rows("SELECT count(*) AS c FROM brain_parts WHERE project_id = %s", PROJECT)
        assert parts[0]["c"] == 0, parts
    finally:
        _cleanup()


def test_pointer_that_fails_to_parse_does_not_abort_the_run():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    bad_prefix = "raw/inbox/test-phase-a-malformed-pointer"
    key = storage._key(bad_prefix + ".md")
    s3 = storage._client()
    try:
        s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=b"not a pointer, no frontmatter at all")
        counts = ingest(PROJECT, bad_prefix)
        assert counts["failed"] >= 1, counts
        assert counts["seen"] == 0, counts
    finally:
        s3.delete_object(Bucket=config.S3_BUCKET, Key=key)
        _cleanup()


def test_pointer_that_fails_to_parse_after_prior_success_is_not_marked_removed():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    flaky_id = "test-phase-a-flaky-pointer"
    flaky_prefix = f"raw/inbox/{flaky_id}"
    key = storage._key(flaky_prefix + ".md")
    s3 = storage._client()
    good_pointer = (
        "---\n"
        f"id: {flaky_id}\n"
        "path: sources/gchat/hands-on-alphacarbon-tutorial-21-mar-2026-03-21-wehva0.md\n"
        "sha: aaaaaaaa\n"
        "source_type: chat_thread\n"
        "---\n")
    try:
        # First run: a real, parseable pointer whose path resolves to a real
        # source body, so it gets an actual brain_sources row and brain_parts.
        s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=good_pointer.encode())
        first = ingest(PROJECT, flaky_prefix)
        assert first["seen"] == 1 and first["new"] == 1 and first["parts"] >= 1, first
        parts_before = _rows("SELECT count(*) AS c FROM brain_parts WHERE source_id = %s", flaky_id)[0]["c"]
        assert parts_before >= 1, parts_before

        # Second run: the same key, now corrupted (no frontmatter) — simulates
        # a pointer that used to work and now fails to parse for some other
        # reason, without it ever having disappeared from S3.
        s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=b"not a pointer, no frontmatter at all")
        second = ingest(PROJECT, flaky_prefix)
        assert second["failed"] == 1 and second["removed"] == 0, second

        src = _rows("SELECT status FROM brain_sources WHERE id = %s", flaky_id)
        assert src[0]["status"] == "ok", "a pointer that merely fails to re-parse must not be marked removed"
        parts_after = _rows("SELECT count(*) AS c FROM brain_parts WHERE source_id = %s", flaky_id)[0]["c"]
        assert parts_after == parts_before, (parts_before, parts_after)
    finally:
        s3.delete_object(Bucket=config.S3_BUCKET, Key=key)
        _cleanup()


def test_prune_false_leaves_a_disappeared_pointer_untouched():
    if not _up():
        print("skipped: DB or S3 unreachable")
        return
    _cleanup()
    try:
        ingest(PROJECT, CHAT_PREFIX)
        counts = ingest(PROJECT, "raw/inbox/no-such-prefix-", prune=False)
        assert counts["seen"] == 0 and counts["removed"] == 0, counts
        src = _rows("SELECT status FROM brain_sources WHERE project_id = %s", PROJECT)
        assert src[0]["status"] == "ok", src
        parts = _rows("SELECT count(*) AS c FROM brain_parts WHERE project_id = %s", PROJECT)
        assert parts[0]["c"] >= 1, parts
    finally:
        _cleanup()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all ingest checks passed")
