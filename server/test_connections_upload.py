#!/usr/bin/env python3
"""Self-check for connections.stage_upload() — the one place a client-supplied
relative path reaches the filesystem, so the traversal guard is what this
file is really pinning down.
Run: python3 server/test_connections_upload.py
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import connections  # noqa: E402
from feeders.upload import sync as upload_sync  # noqa: E402

ROW = {"id": "upload-abc123", "project_id": "proj-1", "kind": "upload",
      "name": "Upload", "config": {}}


def test_stage_upload_writes_each_file_under_the_staging_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "upload-abc123"
        with patch.object(connections, "_require", return_value=ROW), \
             patch.object(upload_sync, "staging_dir", return_value=root):
            n = connections.stage_upload("upload-abc123",
                                         [("notes/a.md", b"A"), ("b.csv", b"x,y")])
        assert n == 2
        assert (root / "notes" / "a.md").read_bytes() == b"A"
        assert (root / "b.csv").read_bytes() == b"x,y"


def test_stage_upload_rejects_a_traversal_path():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "upload-abc123"
        with patch.object(connections, "_require", return_value=ROW), \
             patch.object(upload_sync, "staging_dir", return_value=root):
            try:
                connections.stage_upload("upload-abc123", [("../../etc/passwd", b"evil")])
            except connections.Invalid:
                assert not (Path(tmp).parent / "etc" / "passwd").exists()
                return
    raise AssertionError("a path escaping the staging root must be rejected, not written")


def test_stage_upload_rejects_an_oversized_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "upload-abc123"
        with patch.object(connections, "_require", return_value=ROW), \
             patch.object(upload_sync, "staging_dir", return_value=root), \
             patch.object(upload_sync, "MAX_UPLOAD_BYTES", 10):
            try:
                connections.stage_upload("upload-abc123", [("big.bin", b"x" * 11)])
            except connections.Invalid:
                return
    raise AssertionError("a file over the size ceiling must be rejected")


def test_stage_upload_rejects_a_non_upload_connection():
    other = {**ROW, "kind": "gdrive"}
    with patch.object(connections, "_require", return_value=other):
        try:
            connections.stage_upload("gdrive-xyz", [("a.md", b"A")])
        except connections.Invalid:
            return
    raise AssertionError("staging must be refused for a connection that isn't kind='upload'")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("connections upload: all checks passed")
