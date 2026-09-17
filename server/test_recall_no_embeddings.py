"""Embeddings are optional at QUERY time, not just at index time.

index.build() already caught a failed embed per article; recall() did not,
so a container (where the default OLLAMA_BASE points at the container
itself) raised on every chat message instead of degrading to FTS-only.
"""
from server import index
from server.application.recall import local


def test_hybrid_without_query_vector_skips_embeddings(tmp_path):
    # No index db for this root — the point is that an empty qvec takes the
    # FTS-only branch instead of scoring every embedding row 0.0.
    assert index.hybrid(tmp_path, "anything", []) == []


def test_recall_degrades_when_embeddings_unreachable(monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(local, "embed", boom)
    monkeypatch.setattr(local.config, "OLLAMA_BASE", "http://localhost:11434")
    monkeypatch.setattr(local.corpus, "roots", lambda *_a, **_kw: [])
    assert local.recall("anything") == []


def test_recall_degrades_when_ollama_unconfigured(monkeypatch):
    monkeypatch.setattr(local.config, "OLLAMA_BASE", "")
    monkeypatch.setattr(local.corpus, "roots", lambda *_a, **_kw: [])
    assert local.recall("anything") == []
