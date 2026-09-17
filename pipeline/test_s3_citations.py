"""Offline tests for S3-etag citation versioning in absorb + validate."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import absorb_runner  # noqa: E402
from server import storage  # noqa: E402


def test_version_of_dispatches_sources_to_s3(monkeypatch, tmp_path):
    monkeypatch.setattr(
        storage, "head_etag",
        lambda rel: "abcdef0123456789" if rel == "sources/gchat/day.md" else None)

    assert absorb_runner.version_of(tmp_path, "sources/gchat/day.md") == "abcdef01"
    assert absorb_runner.version_of(tmp_path, "sources/gchat/missing.md") is None


def test_version_of_uses_git_for_non_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(absorb_runner, "blob_sha",
                        lambda repo, p: "1234abcd")
    monkeypatch.setattr(
        storage, "head_etag",
        lambda rel: pytest.fail("S3 must not be consulted for wiki/ paths"))

    assert absorb_runner.version_of(tmp_path, "wiki/systems/x.md") == "1234abcd"


def test_version_of_refuses_sources_without_s3(monkeypatch, tmp_path):
    def boom(rel):
        raise RuntimeError("S3_BUCKET unset")
    monkeypatch.setattr(storage, "head_etag", boom)

    with pytest.raises(SystemExit):
        absorb_runner.version_of(tmp_path, "sources/gchat/day.md")


from pipeline import validate_wiki  # noqa: E402


def test_validate_source_etag_caches_and_dispatches(monkeypatch):
    calls = []

    def fake_head(rel):
        calls.append(rel)
        return "abcdef0123456789"

    monkeypatch.setattr(storage, "head_etag", fake_head)
    validate_wiki._ETAG_CACHE.clear()

    assert validate_wiki.source_etag("sources/gchat/day.md") == "abcdef0123456789"
    assert validate_wiki.source_etag("sources/gchat/day.md") == "abcdef0123456789"
    assert calls == ["sources/gchat/day.md"], "second call must hit the cache"
