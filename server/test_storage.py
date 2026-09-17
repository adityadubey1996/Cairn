#!/usr/bin/env python3
"""Self-check for the S3 backing store.  Run: python3 -m server.test_storage

Things worth pinning, both invisible until someone needs them:

  the commit metadata reaches the upload — without it a stored bucket version is
  a wiki file with no way back to the commit that produced it, and versioning
  buys history nobody can attribute.

  a file whose content already matches is not re-uploaded/re-downloaded —
  push() runs after every ingest and absorb, pull() runs on every boot and
  every 15 minutes, so losing either check means moving the whole corpus over
  the network for no reason, constantly.

  pull()'s downloads run through a thread pool (a cold ~3,300-object volume
  took 20+ minutes one at a time) — the "keep a newer local file" and
  "skip an identical one" checks still have to hold exactly as before.

No network: a fake client records what push()/pull() would have sent.
"""
from __future__ import annotations

import datetime
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import config, storage  # noqa: E402

_EPOCH = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

# push_into()/pull_into() swap a fake into storage._client and cannot restore
# it (the fake has to outlive them, into the test body). Captured here so the
# one test that wants the REAL client is not handed a leaked fake.
# ponytail: a fixture would scope this properly, at the cost of a pytest
# import this file deliberately does without so it runs standalone.
_REAL_CLIENT = storage._client

ARTICLE = """---
title: Arps Decline
type: system
built_from_commit: 4f2a9c1b3d5e7f8a9b0c1d2e3f4a5b6c7d8e9f01
sources: ["src/arps.py@a1b2c3d4"]
---

# Arps Decline

Two of three strategies raise NotImplementedError [code: src/arps.py@a1b2c3d4].
"""


class FakeS3:
    """Records uploads; reports/serves whatever `remote` was seeded with.

    `remote` maps rel-path -> (etag, body). push()-side tests only ever read
    the etag half; pull()-side tests need the body too, to write on download."""

    def __init__(self, remote: dict[str, tuple[str, str]] | None = None):
        self.remote = remote or {}
        self.uploads: dict[str, dict] = {}
        self.downloads: list[str] = []
        self._prefix = ""

    def get_paginator(self, _op):
        return self

    def paginate(self, Bucket=None, Prefix=""):
        self._prefix = Prefix
        return [{"Contents": [
            {"Key": Prefix + rel, "ETag": f'"{etag}"',
             "LastModified": _EPOCH}
            for rel, (etag, _body) in self.remote.items()]}]

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.uploads[key] = ExtraArgs or {}

    def download_file(self, Bucket, Key, Filename):
        # list.append is the one thing worker threads actually share here;
        # relying on it being safe under the GIL is fine for a test double.
        self.downloads.append(Key)
        rel = Key[len(self._prefix):]
        Path(Filename).write_text(self.remote[rel][1])


class FakeHeadS3:
    """head_object/generate_presigned_url only — for head_etag/presigned_url."""
    class exceptions:
        class ClientError(Exception):
            pass

    def __init__(self, objects):
        self.objects = objects  # key -> etag (unquoted)

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.ClientError()
        return {"ETag": f'"{self.objects[Key]}"'}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://signed.example/{Params['Key']}?Expires={ExpiresIn}"


def push_into(tmp: Path, fake: FakeS3) -> FakeS3:
    """Run push() with both durable trees pointed at tmp and a fake client."""
    config.S3_BUCKET, config.S3_PREFIX = "test-bucket", "dev"
    storage._client = lambda: fake
    storage.trees = lambda: [("wikis", tmp)]
    storage.push()
    return fake


def test_commit_is_read_from_frontmatter_only():
    assert storage.commit_of(ARTICLE.encode()) == \
        "4f2a9c1b3d5e7f8a9b0c1d2e3f4a5b6c7d8e9f01"
    # A citation in the body is not the build commit.
    body_only = b"# Notes\n\nbuilt_from_commit: deadbeefdeadbeef\n"
    assert storage.commit_of(body_only) == ""
    # Sources and JSON twins have no frontmatter; absence is correct.
    assert storage.commit_of(b'{"units": []}') == ""


def test_push_attaches_the_commit_as_metadata():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "systems").mkdir()
        (tmp / "systems" / "arps.md").write_text(ARTICLE)
        (tmp / "_manifest.json").write_text('{"units": []}')

        fake = push_into(tmp, FakeS3())

        assert set(fake.uploads) == {"dev/wikis/systems/arps.md",
                                     "dev/wikis/_manifest.json"}
        assert fake.uploads["dev/wikis/systems/arps.md"]["Metadata"]["commit"] == \
            "4f2a9c1b3d5e7f8a9b0c1d2e3f4a5b6c7d8e9f01"
        # No frontmatter, no metadata — never an empty or invented commit.
        assert fake.uploads["dev/wikis/_manifest.json"] == {}


def test_push_skips_a_file_that_is_already_identical():
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "systems").mkdir()
        (tmp / "systems" / "arps.md").write_text(ARTICLE)
        same = hashlib.md5(ARTICLE.encode()).hexdigest()

        fake = push_into(tmp, FakeS3({"systems/arps.md": (same, ARTICLE)}))

        assert fake.uploads == {}, "re-uploaded an unchanged article"


def pull_into(tmp: Path, fake: FakeS3) -> FakeS3:
    """Run pull() with both durable trees pointed at tmp and a fake client."""
    config.S3_BUCKET, config.S3_PREFIX = "test-bucket", "dev"
    storage._client = lambda: fake
    storage.trees = lambda: [("wikis", tmp)]
    storage.pull()
    return fake


def test_pull_downloads_every_new_file():
    """The behavior that changed: downloads now run through a thread pool
    instead of one at a time. All of them landing correctly, with the right
    content, is what would break if the concurrency were wrong."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        remote = {f"systems/pkg{i}.md": (f"etag{i}", f"body {i}") for i in range(25)}

        fake = pull_into(tmp, FakeS3(remote))

        assert len(fake.downloads) == 25
        for i in range(25):
            assert (tmp / "systems" / f"pkg{i}.md").read_text() == f"body {i}"


def test_pull_skips_a_file_that_is_already_identical():
    import hashlib
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "systems").mkdir()
        (tmp / "systems" / "arps.md").write_text(ARTICLE)
        same = hashlib.md5(ARTICLE.encode()).hexdigest()

        fake = pull_into(tmp, FakeS3({"systems/arps.md": (same, ARTICLE)}))

        assert fake.downloads == [], "re-downloaded an unchanged article"


def test_pull_never_overwrites_a_newer_local_file():
    """Two instances absorbing at once must not silently undo each other's
    paid work — a locally-newer file beats a stale remote copy every time."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "systems").mkdir()
        local = tmp / "systems" / "arps.md"
        local.write_text("locally revised, not pushed yet")
        future = _EPOCH.timestamp() + 3600
        import os
        os.utime(local, (future, future))

        fake = pull_into(tmp, FakeS3({"systems/arps.md": ("stale-etag", "old remote copy")}))

        assert fake.downloads == [], "clobbered a newer local file"
        assert local.read_text() == "locally revised, not pushed yet"


def test_head_etag_present_absent_and_unconfigured():
    """head_etag returns ETag, None if absent, RuntimeError if unconfigured."""
    fake = FakeHeadS3({"dev/sources/gchat/day.md": "abcdef0123456789abcdef0123456789"})
    orig_client = storage._client
    orig_bucket = config.S3_BUCKET
    orig_prefix = config.S3_PREFIX
    try:
        storage._client = lambda: fake
        config.S3_BUCKET = "b"
        config.S3_PREFIX = "dev"

        assert storage.head_etag("sources/gchat/day.md") == \
            "abcdef0123456789abcdef0123456789"
        assert storage.head_etag("sources/gchat/missing.md") is None

        config.S3_BUCKET = ""
        try:
            storage.head_etag("sources/gchat/day.md")
            assert False, "should raise RuntimeError when S3_BUCKET is empty"
        except RuntimeError as e:
            assert "S3_BUCKET unset" in str(e)
    finally:
        storage._client = orig_client
        config.S3_BUCKET = orig_bucket
        config.S3_PREFIX = orig_prefix


def test_presigned_url_uses_prefix_and_expiry():
    """presigned_url generates URL with correct prefix and expiry."""
    fake = FakeHeadS3({})
    orig_client = storage._client
    orig_bucket = config.S3_BUCKET
    orig_prefix = config.S3_PREFIX
    try:
        storage._client = lambda: fake
        config.S3_BUCKET = "b"
        config.S3_PREFIX = "dev"

        url = storage.presigned_url("sources/gchat/day.md")
        assert url == "https://signed.example/dev/sources/gchat/day.md?Expires=900"
    finally:
        storage._client = orig_client
        config.S3_BUCKET = orig_bucket
        config.S3_PREFIX = orig_prefix


def test_client_has_connect_and_read_timeouts():
    """_client returns a boto3 client with bounded connect and read timeouts."""
    client = _REAL_CLIENT()
    cfg = client.meta.config
    assert cfg.connect_timeout == 10, cfg.connect_timeout
    assert cfg.read_timeout == 30, cfg.read_timeout


def test_client_is_memoized():
    """_client is ~10ms to construct and called thousands of times per ingest
    run; lru_cache means every call after the first returns the same object."""
    assert storage._client() is storage._client()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("storage: all checks passed")
