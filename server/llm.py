"""One BYOK provider layer for every LLM call the server makes.

Two request shapes cover every provider worth supporting: OpenAI-compatible
`/chat/completions` (Groq, DeepSeek, OpenRouter, Gemini, Ollama, anything
self-hosted) and Anthropic's `/v1/messages`, which is not OpenAI-compatible and
has no shim. Raw HTTP for both, matching the rest of this codebase — so an
install pulls no provider SDK it will never use.

Callers always pass OpenAI-shaped messages (a leading `system` role); the
Anthropic path translates. Nothing above this module knows which provider ran.

Resolution order, first usable one wins:

  1. what the Settings screen saved (`brain_settings`)
  2. `LLM_PROVIDER` in .env
  3. any known provider's key present in .env  (drop a key in, it just works)
  4. Ollama, if it answers on this machine

That order is what makes "clone it and run" true: with no config at all, a
machine with Ollama gets a working brain and never sees a key prompt.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import config

ANTHROPIC_VERSION = "2023-06-01"
# Anthropic requires max_tokens. Streaming gets room to breathe; a blocking
# call stays under the HTTP timeout.
MAX_TOKENS_STREAM = 64000
MAX_TOKENS_BLOCKING = 16000
TIMEOUT = 180


class NoProvider(RuntimeError):
    """No usable provider. The message is shown to the user verbatim, so it
    names every way out rather than just stating the failure."""


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    base: str
    model: str
    kind: str = "openai"      # "openai" | "anthropic"
    key_env: str = ""
    local: bool = False


PRESETS: dict[str, Preset] = {
    "claude": Preset("claude", "Claude", "https://api.anthropic.com/v1",
                     "claude-opus-5", "anthropic", "ANTHROPIC_API_KEY"),
    "groq": Preset("groq", "Groq", "https://api.groq.com/openai/v1",
                   "openai/gpt-oss-120b", key_env="GROQ_API_KEY"),
    "deepseek": Preset("deepseek", "DeepSeek", "https://api.deepseek.com/v1",
                       "deepseek-chat", key_env="DEEPSEEK_API_KEY"),
    "openrouter": Preset("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
                         "meta-llama/llama-3.3-70b-instruct:free",
                         key_env="OPENROUTER_API_KEY"),
    "gemini": Preset("gemini", "Gemini",
                     "https://generativelanguage.googleapis.com/v1beta/openai",
                     "gemini-2.0-flash", key_env="GEMINI_API_KEY"),
    "ollama": Preset("ollama", "Ollama (local)", "", "", local=True),
    "custom": Preset("custom", "Custom", "", "", key_env="LLM_API_KEY"),
}

# Which .env key gets picked up when nothing has been chosen. Hosted first:
# someone who pasted a key wants that key used, and Ollama is the free floor
# under everything.
AUTODETECT = ("claude", "groq", "deepseek", "openrouter", "gemini")

# Ollama chat models worth defaulting to, best first. Matched as name prefixes
# so `llama3.1:8b` and `llama3.1:70b` both hit the `llama3.1` entry.
OLLAMA_CHAT_PREFERENCE = (
    "qwen3", "llama3.3", "gemma3", "llama3.2", "llama3.1",
    "mistral", "phi4", "qwen2.5",
)

# Shown when Ollama is running but has no chat model pulled. The whole point of
# the Ollama path is that it costs nothing, so the suggestion has to be a model
# an ordinary laptop can actually hold.
OLLAMA_RECOMMENDED = [
    {"model": "llama3.1:8b", "why": "good default — ~5GB, runs on 16GB of RAM"},
    {"model": "qwen3:4b", "why": "smallest useful — ~3GB, for 8GB machines"},
    {"model": "llama3.3:70b", "why": "best answers — ~40GB, needs a workstation"},
]


def ollama_base() -> str:
    return (config.OLLAMA_BASE or "").rstrip("/")


def _env(name: str) -> str:
    return os.environ.get(name, "").strip() if name else ""


def _ollama_tags() -> list[dict] | None:
    """Every model Ollama has locally, or None when Ollama isn't reachable.
    The two cases are different answers and the UI shows different things."""
    base = ollama_base()
    if not base:
        return None
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=3) as r:
            return json.loads(r.read()).get("models") or []
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def ollama_chat_models() -> list[str]:
    """Installed models that can hold a conversation.

    Embedding models are excluded: `nomic-embed-text` is always present here
    (retrieval needs it) and has no chat endpoint, so offering it as the answer
    model would hand the user a guaranteed failure.
    """
    tags = _ollama_tags()
    if not tags:
        return []
    out = []
    for m in tags:
        name = m.get("name") or m.get("model") or ""
        caps = m.get("capabilities") or []
        if not name or "embed" in name:
            continue
        if caps and "completion" not in caps:
            continue
        out.append(name)
    return out


def suggest_ollama_model() -> str | None:
    """The best installed chat model, by OLLAMA_CHAT_PREFERENCE then whatever
    is there. None means Ollama has nothing usable and needs a pull."""
    installed = ollama_chat_models()
    for want in OLLAMA_CHAT_PREFERENCE:
        for name in installed:
            if name.startswith(want):
                return name
    return installed[0] if installed else None


def ollama_status() -> dict:
    """What the Settings screen needs to explain Ollama without guessing."""
    reachable = _ollama_tags() is not None
    models = ollama_chat_models()
    suggested = suggest_ollama_model()
    return {
        "base": ollama_base(),
        "reachable": reachable,
        "models": models,
        "suggested": suggested,
        # Only worth showing when there is nothing to select.
        "recommended": [] if models else [
            {**r, "command": f"ollama pull {r['model']}"} for r in OLLAMA_RECOMMENDED
        ],
    }


def _saved() -> dict:
    """The Settings row, or {} — a brain with no database yet still resolves
    from .env, which is what makes the CLI and the tests work."""
    try:
        from .db import connect
        with connect() as c:
            r = c.execute("SELECT value FROM brain_settings WHERE id = 'provider'").fetchone()
        return dict(r["value"]) if r else {}
    except Exception:
        return {}


def _build(preset_id: str, cfg: dict) -> dict | None:
    """A complete, usable provider — or None when this candidate can't run.

    `cfg` is the saved settings row for the chosen preset; .env fills every
    hole in it, so a key in the file works whether or not the UI was ever
    opened.
    """
    preset = PRESETS.get(preset_id)
    if not preset:
        return None
    base = (cfg.get("baseUrl") or _env("LLM_BASE") or preset.base).rstrip("/")
    model = cfg.get("model") or _env("LLM_MODEL") or preset.model

    if preset.local:
        base = base or ollama_base()
        model = model or suggest_ollama_model()
        if not base or not model:
            return None
        return {"preset": preset.id, "base": f"{base}/v1", "key": "",
                "model": model, "kind": "openai"}

    key = cfg.get("key") or _env(preset.key_env) or _env("LLM_API_KEY")
    if not key or not base or not model:
        return None
    return {"preset": preset.id, "base": base, "key": key,
            "model": model, "kind": preset.kind}


def _hint() -> str:
    keys = ", ".join(PRESETS[p].key_env for p in AUTODETECT)
    return ("No LLM provider is configured. Either open Settings and paste a "
            f"key, or put one of {keys} in .env, or run Ollama locally "
            f"({ollama_base() or 'OLLAMA_BASE unset'}) and pull a model "
            f"— e.g. `ollama pull {OLLAMA_RECOMMENDED[0]['model']}`.")


def resolve() -> dict:
    """The provider every call uses. See this module's docstring for order.

    The winner carries `source` — which rung of that order it came from — so
    the UI can say "answering with X (from .env)" instead of leaving the user
    to guess why an empty settings form still works.
    """
    saved = _saved()
    candidates: list[tuple[str, dict, str]] = []
    if saved.get("preset"):
        candidates.append((saved["preset"], saved, "settings"))
    if _env("LLM_PROVIDER"):
        candidates.append((_env("LLM_PROVIDER"), {}, "env"))
    candidates += [(p, {}, "env") for p in AUTODETECT]
    candidates.append(("ollama", {}, "ollama"))

    for preset_id, cfg, source in candidates:
        provider = _build(preset_id, cfg)
        if provider:
            return {**provider, "source": source}
    raise NoProvider(_hint())


def _post(url: str, body: dict, headers: dict):
    return urllib.request.urlopen(
        urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            # Cloudflare in front of api.groq.com rejects the default
            # Python-urllib agent with 403 "error code: 1010".
            headers={"Content-Type": "application/json",
                     "User-Agent": "cairn/1.0", **headers}),
        timeout=TIMEOUT)


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic takes `system` as its own field, and its first message must be
    a user turn. A history window can start on an assistant reply, so those are
    dropped rather than sent to a 400."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [m for m in messages if m["role"] != "system"]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return system, turns


def _openai_body(messages, provider, temperature, stream):
    return {"model": provider["model"], "messages": messages,
            "temperature": temperature, "stream": stream}


def _anthropic_body(messages, provider, temperature, stream):
    system, turns = _split_system(messages)
    body = {"model": provider["model"], "messages": turns,
            "max_tokens": MAX_TOKENS_STREAM if stream else MAX_TOKENS_BLOCKING,
            "temperature": temperature, "stream": stream}
    if system:
        body["system"] = system
    return body


def _request(messages, provider, temperature, stream):
    if provider.get("preset") == "ollama":
        # The OpenAI compatibility endpoint cannot set Ollama's context window.
        # Native requests reserve room for the full retrieved article context.
        return _post(f"{provider['base'].removesuffix('/v1')}/api/chat",
                     {"model": provider["model"], "messages": messages, "stream": stream,
                      "options": {"temperature": temperature, "num_ctx": 32768,
                                  "num_predict": 4096}}, {})
    if provider["kind"] == "anthropic":
        return _post(f"{provider['base']}/messages",
                     _anthropic_body(messages, provider, temperature, stream),
                     {"x-api-key": provider["key"],
                      "anthropic-version": ANTHROPIC_VERSION})
    headers = {"Authorization": f"Bearer {provider['key']}"} if provider["key"] else {}
    return _post(f"{provider['base']}/chat/completions",
                 _openai_body(messages, provider, temperature, stream), headers)


def complete(messages: list[dict], provider: dict | None = None,
             temperature: float = 0.2) -> str:
    provider = provider or resolve()
    with _request(messages, provider, temperature, stream=False) as r:
        data = json.loads(r.read())
    if provider.get("preset") == "ollama":
        if data.get("error"):
            raise RuntimeError(data["error"])
        return (data.get("message") or {}).get("content", "")
    if provider["kind"] == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")
    return data["choices"][0]["message"]["content"]


def stream(messages: list[dict], provider: dict | None = None,
           temperature: float = 0.2):
    """Yield answer text as it arrives, for either request shape."""
    provider = provider or resolve()
    anthropic = provider["kind"] == "anthropic"
    r = _request(messages, provider, temperature, stream=True)
    try:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if provider.get("preset") == "ollama":
                if not line:
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(event["error"])
                text = (event.get("message") or {}).get("content")
                if text:
                    yield text
                if event.get("done"):
                    break
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if anthropic:
                # Adaptive thinking is on by default for current Claude models
                # and streams thinking blocks with empty text; only text_delta
                # is the answer.
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif event.get("type") == "error":
                    raise RuntimeError((event.get("error") or {}).get("message", "stream error"))
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            text = (choices[0].get("delta") or {}).get("content")
            if text:
                yield text
    finally:
        r.close()


def absorb_env() -> dict:
    """Env overrides handing the resolved provider to pipeline/ subprocesses.

    absorb_runner.py and topics.py run standalone (no server package) and speak
    only the OpenAI shape, reading ABSORB_BASE/ABSORB_API_KEY/ABSORB_MODEL and
    falling back to GROQ_*. Anything already set in .env is left alone — an
    explicit ABSORB_* override is the user saying "absorb runs on this one".

    Anthropic has no /chat/completions, so a Claude-only install cannot absorb
    and is told exactly that instead of getting an opaque 404 from urllib.
    """
    if _env("ABSORB_API_KEY"):
        return {}
    provider = resolve()  # raises NoProvider with its own hint
    if provider["kind"] == "anthropic":
        raise NoProvider(
            "Article generation talks to OpenAI-compatible providers only, and "
            "Anthropic's API is not one. Put an OpenAI-compatible key in .env "
            "for this step — ABSORB_API_KEY (+ ABSORB_BASE, ABSORB_MODEL) — or "
            "run Ollama, which absorb can use for free. Answering in chat is "
            "unaffected and still uses Claude.")
    env = {"ABSORB_BASE": provider["base"], "ABSORB_MODEL": provider["model"]}
    if provider["preset"] == "ollama":
        env["ABSORB_PROTOCOL"] = "ollama"
    if provider["key"]:
        env["ABSORB_API_KEY"] = provider["key"]
    else:
        # Ollama wants no auth, but the runner sends the header unconditionally.
        # A placeholder keeps its "set a key" guard satisfied; Ollama ignores it.
        env["ABSORB_API_KEY"] = "ollama-local"
    return env


def test(cfg: dict) -> dict:
    """Make a real, minimal call so "Test key" never reports a fake success.

    Uses the resolution chain, so a preset whose key lives in .env tests green
    without the key being retyped into the browser.
    """
    preset_id = cfg.get("preset") or "claude"
    preset = PRESETS.get(preset_id)
    if not preset:
        return {"ok": False, "detail": f"unknown provider {preset_id!r}"}

    if preset.local:
        status = ollama_status()
        if not status["reachable"]:
            return {"ok": False, "detail": f"Couldn't reach Ollama at {status['base']}"}
        if not status["models"]:
            pull = status["recommended"][0]["command"]
            return {"ok": False,
                    "detail": f"Ollama is running but has no chat model — run `{pull}`"}
        return {"ok": True, "detail": f"Ollama is reachable · using {status['suggested']}"}

    provider = _build(preset_id, cfg)
    if not provider:
        missing = preset.key_env or "a base URL and model"
        return {"ok": False, "detail": f"no key provided (set {missing} or paste one above)"}
    try:
        with _request([{"role": "user", "content": "ping"}], provider, 0.0, False):
            pass
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"{preset.label} rejected the request ({e.code})"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "detail": f"couldn't reach {preset.label}: {e}"}
    return {"ok": True, "detail": f"Key works · {provider['model']}"}


def demo() -> None:
    """Self-check: resolution order and both body shapes, no network."""
    msgs = [{"role": "system", "content": "S"},
            {"role": "assistant", "content": "stale"},
            {"role": "user", "content": "Q"}]

    system, turns = _split_system(msgs)
    assert system == "S", system
    assert [t["role"] for t in turns] == ["user"], turns

    body = _anthropic_body(msgs, PRESETS["claude"].__dict__ | {"model": "m", "kind": "anthropic"},
                           0.2, stream=True)
    assert body["system"] == "S" and body["max_tokens"] == MAX_TOKENS_STREAM
    assert "system" not in _openai_body(msgs, {"model": "m"}, 0.2, False)

    # .env alone is enough, and an explicit LLM_PROVIDER outranks autodetect.
    saved = os.environ.copy()
    try:
        for p in PRESETS.values():
            os.environ.pop(p.key_env, None)
        for name in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE"):
            os.environ.pop(name, None)

        os.environ["GROQ_API_KEY"] = "k"
        assert _build("groq", {})["preset"] == "groq"
        assert _build("deepseek", {}) is None, "a key it doesn't have must not resolve"

        os.environ["DEEPSEEK_API_KEY"] = "k2"
        # Autodetect order is stable: groq outranks deepseek regardless of env.
        assert [p for p in AUTODETECT if _build(p, {})] == ["groq", "deepseek"]

        # A saved preset wins over both, and its key may still come from .env.
        assert _build("groq", {"model": "override"})["model"] == "override"
    finally:
        os.environ.clear()
        os.environ.update(saved)

    print("llm.demo ok")


if __name__ == "__main__":
    demo()
