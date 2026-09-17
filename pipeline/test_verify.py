#!/usr/bin/env python3
"""Self-check for graph_verify claim classification.  Run: python3 test_verify.py

Only "missing" triggers a demotion to [gap]. Feeder-sourced claims cite URIs
(gchat://, https://) which a code graph can never contain — without the scheme
guard every conversation-sourced claim would be demoted on the first verify.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_verify import classify_claim_path  # noqa: E402

FILES = {
    "backend_new/src/domain/uncertainty/sampler.py",
    "backend_new/src/api/main.py",
}
IGNORE = ["backend/", "Asset-Manager/"]


def c(path):
    return classify_claim_path(path, IGNORE, FILES)


def test_feeder_uris_are_never_demoted():
    for uri in (
        "gchat://spaces/AAAAxyz/threads/BBBB",
        "https://youtube.com/watch?v=abc123",
        "https://drive.google.com/file/d/1a2b3c",
        "meet://recordings/2026-05-11",
        "HTTPS://Example.com/Thing",
    ):
        assert c(uri) == "external", (uri, c(uri))


def test_real_code_path_still_ok():
    assert c("backend_new/src/domain/uncertainty/sampler.py") == "ok"
    # a package cite resolves through any file beneath it
    assert c("backend_new/src/domain/uncertainty") == "ok"


def test_deleted_code_path_still_demotes():
    # the whole point of verify: this must NOT be silenced by the guard
    assert c("backend_new/src/domain/gone/vanished.py") == "missing"


def test_ignored_and_non_code_unchanged():
    assert c("backend/legacy/thing.py") == "ignored"
    assert c("docs/EOL_Well_Workflow.docx") == "non_code"
    assert c("backend_new/docs/modules/18_uncertainty.md") == "non_code"


def test_scheme_guard_is_not_over_broad():
    # a bare path that merely contains a colon is not a URI
    assert c("backend_new/src/api/main.py") == "ok"
    # ...and something scheme-like but pathy must not be swallowed
    assert c("backend_new/src/domain/gone/vanished.py") == "missing"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all graph_verify checks passed")
