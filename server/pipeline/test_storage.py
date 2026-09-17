#!/usr/bin/env python3
"""Self-check for the two storage helpers Phase A adds.

Needs LocalStack (S3_BUCKET, S3_ENDPOINT). Skips itself otherwise.

Run: python3 -m server.pipeline.test_storage
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import storage  # noqa: E402


def _s3_up() -> bool:
    try:
        return storage.enabled() and storage.head_etag("raw/inbox/gchat-AAQADWehVa0-2026-03-21.md") is not None
    except Exception:
        return False


def test_list_rel_paths_returns_pointer_keys_relative_to_prefix():
    if not _s3_up():
        print("skipped: S3 unreachable")
        return
    keys = storage.list_rel_paths("raw/inbox/gchat-AAQADWehVa0")
    assert keys == ["raw/inbox/gchat-AAQADWehVa0-2026-03-21.md"], keys


def test_read_full_returns_whole_object():
    if not _s3_up():
        print("skipped: S3 unreachable")
        return
    text = storage.read_full("sources/gdrive/ui-screens-spec-html-pdf-kmqh1i.md")
    assert len(text) == 50910, len(text)
    assert text.count("\f") == 26, text.count("\f")


def test_read_full_missing_key_raises_file_not_found():
    if not _s3_up():
        print("skipped: S3 unreachable")
        return
    try:
        storage.read_full("sources/links/does-not-exist.md")
    except FileNotFoundError as e:
        assert "does-not-exist" in str(e), e
        return
    raise AssertionError("expected FileNotFoundError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all storage checks passed")
