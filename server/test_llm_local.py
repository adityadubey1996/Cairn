"""Native local model transport keeps retrieved context and streams NDJSON."""
import io
import json

import pytest

from server import llm


PROVIDER = {"preset": "ollama", "base": "http://localhost:11434/v1",
            "model": "llama3.1:8b", "kind": "openai", "key": ""}


def test_native_local_request_sets_explicit_context_without_auth(monkeypatch):
    requests = []
    monkeypatch.setattr(llm, "_post", lambda *args: requests.append(args))
    llm._request([{"role": "user", "content": "question"}], PROVIDER, 0, True)
    url, body, headers = requests[0]
    assert url == "http://localhost:11434/api/chat"
    assert body["options"]["num_ctx"] == 32768
    assert body["stream"] and not headers


def test_complete_reads_native_message(monkeypatch):
    monkeypatch.setattr(llm, "_request", lambda *_a, **_kw:
                        io.BytesIO(json.dumps({"message": {"content": "Grounded answer"}}).encode()))
    assert llm.complete([], PROVIDER) == "Grounded answer"


def test_native_stream_reads_deltas_and_stops_at_done(monkeypatch):
    events = [{"message": {"content": "Grounded "}},
              {"message": {"content": "answer"}, "done": True},
              {"message": {"content": "ignored"}}]
    response = io.BytesIO("\n".join(json.dumps(e) for e in events).encode())
    monkeypatch.setattr(llm, "_request", lambda *_a, **_kw: response)
    assert "".join(llm.stream([], PROVIDER)) == "Grounded answer"
    assert response.closed


def test_native_stream_surfaces_model_errors(monkeypatch):
    response = io.BytesIO(b'{"error":"model not loaded"}\n')
    monkeypatch.setattr(llm, "_request", lambda *_a, **_kw: response)
    with pytest.raises(RuntimeError, match="model not loaded"):
        list(llm.stream([], PROVIDER))
    assert response.closed
