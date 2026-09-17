"""Direct-call tests for the sources view route — no TestClient needed."""
import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from server import storage
from server.routers import sources


def test_view_mints_url_on_etag_match(monkeypatch):
    monkeypatch.setattr(storage, "head_etag",
                        lambda rel: "abcdef0123456789abcdef0123456789")
    monkeypatch.setattr(storage, "presigned_url",
                        lambda rel: f"https://signed.example/{rel}")

    out = sources.view_source(path="sources/gchat/day.md",
                              etag="abcdef01", _email="dev@localhost", accept="")
    assert out == {"url": "https://signed.example/sources/gchat/day.md"}


def test_view_409_on_mismatch(monkeypatch):
    monkeypatch.setattr(storage, "head_etag",
                        lambda rel: "ffffffff00000000ffffffff00000000")
    with pytest.raises(HTTPException) as e:
        sources.view_source(path="sources/gchat/day.md",
                            etag="abcdef01", _email="dev@localhost")
    assert e.value.status_code == 409


def test_view_404_when_absent(monkeypatch):
    monkeypatch.setattr(storage, "head_etag", lambda rel: None)
    with pytest.raises(HTTPException) as e:
        sources.view_source(path="sources/gchat/gone.md",
                            etag="abcdef01", _email="dev@localhost")
    assert e.value.status_code == 404


@pytest.mark.parametrize("bad_path", [
    "wiki/systems/x.md",             # not sources/-rooted
    "sources/../server/config.py",   # traversal
])
def test_view_400_on_bad_path(monkeypatch, bad_path):
    monkeypatch.setattr(
        storage, "head_etag",
        lambda rel: pytest.fail("S3 must not be consulted for a rejected path"))
    with pytest.raises(HTTPException) as e:
        sources.view_source(path=bad_path, etag="abcdef01",
                            _email="dev@localhost")
    assert e.value.status_code == 400


def test_view_400_on_bad_etag():
    with pytest.raises(HTTPException) as e:
        sources.view_source(path="sources/gchat/day.md",
                            etag="not-hex!", _email="dev@localhost")
    assert e.value.status_code == 400


def test_view_503_when_s3_unconfigured(monkeypatch):
    def boom(rel):
        raise RuntimeError("S3_BUCKET unset")
    monkeypatch.setattr(storage, "head_etag", boom)
    with pytest.raises(HTTPException) as e:
        sources.view_source(path="sources/gchat/day.md",
                            etag="abcdef01", _email="dev@localhost")
    assert e.value.status_code == 503


def test_view_redirects_browser_navigation(monkeypatch):
    monkeypatch.setattr(storage, "head_etag",
                        lambda rel: "abcdef0123456789abcdef0123456789")
    monkeypatch.setattr(storage, "presigned_url",
                        lambda rel: f"https://signed.example/{rel}")

    out = sources.view_source(path="sources/gchat/day.md",
                              etag="abcdef01", _email="dev@localhost",
                              accept="text/html,application/xhtml+xml")
    assert isinstance(out, RedirectResponse)
    assert out.status_code == 302
    assert out.headers["location"] == "https://signed.example/sources/gchat/day.md"
