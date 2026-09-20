"""Real compressed payloads exercise transport decoding before HTML extraction."""
import gzip
import io
import zlib

import pytest

from feeders.links import sync


@pytest.mark.parametrize("encoding,compress", [
    ("gzip", gzip.compress),
    ("", gzip.compress),
    ("deflate", zlib.compress),
    ("deflate", lambda body: zlib.compress(body)[2:-4]),
])
def test_compressed_http_response_extracts_readable_article(monkeypatch, encoding, compress):
    html = b"<html><body><h1>About Python</h1><p>Python is a programming language.</p></body></html>"
    response = io.BytesIO(compress(html))
    response.headers = {"Content-Encoding": encoding}
    monkeypatch.setattr(sync, "_validate_public_address", lambda _: None)
    monkeypatch.setattr(sync, "_wait_turn", lambda _: None)
    monkeypatch.setattr(sync._opener, "open", lambda *a, **kw: response)

    kind, text, _ = sync._dispatch_fetch("https://www.example.com/about/")

    assert kind == "external_article"
    assert "Python is a programming language." in text
    assert "\ufffd" not in text and "\x00" not in text


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_decompressed_size_is_bounded(monkeypatch, encoding, compress):
    monkeypatch.setattr(sync, "MAX_FETCH_BYTES", 100)
    with pytest.raises(ValueError, match="decoded response exceeds"):
        sync._decode_response(compress(b"x" * 1000), encoding)


def test_unsupported_encoding_is_not_misreported_as_article_text():
    with pytest.raises(ValueError, match="unsupported HTTP content encoding"):
        sync._decode_response(b"compressed", "br")
