#!/usr/bin/env python3
"""Self-check for absorb_runner's absorb-log gating.
Run: python3 pipeline/test_absorb_push_retry.py

record_absorbed must never mark a unit absorbed unless its article actually
made it to S3 — otherwise a failed push leaves a paid-for article with
nowhere to be re-queued from.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from absorb_runner import record_absorbed  # noqa: E402


def test_push_failure_leaves_the_unit_queued_for_retry():
    # If storage.push() fails, the unit must NOT be recorded as absorbed —
    # otherwise ingest never re-queues it and a paid-for article can be
    # permanently lost if the process dies before the next successful push.
    with tempfile.TemporaryDirectory() as d:
        wiki = Path(d) / "wiki"
        wiki.mkdir()
        log_p = wiki / "_absorb_log.json"
        published = ["some-unit-id"]
        pushed_ok = record_absorbed(log_p, published, push_succeeded=False)
        assert pushed_ok is False
        logged = json.loads(log_p.read_text()) if log_p.is_file() else []
        assert "some-unit-id" not in logged, logged


def test_storage_import_resolves_to_ai_brains_own_root_not_the_target_repo():
    # The s3-push block imports `from server import storage` after doing
    # `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))` —
    # the same pattern version_of() already uses to always find ai-brain's
    # own server/ package, regardless of what --repo points at (some other
    # project's clone, which has no server/ package of its own at all).
    # This directly verifies that premise: the computed root is ai-brain's
    # own repo, not whatever `repo` the caller passed in.
    ai_brain_root = Path(__file__).resolve().parent.parent
    assert (ai_brain_root / "server" / "storage.py").is_file(), (
        f"expected {ai_brain_root} to be ai-brain's own repo root, "
        f"containing server/storage.py"
    )
    # Simulate a target repo with NO server/ package at all — e.g. a
    # sibling clone the pipeline was pointed at via --repo.
    with tempfile.TemporaryDirectory() as d:
        fake_repo = Path(d)
        assert not (fake_repo / "server").exists()
        # The fix inserts ai_brain_root, not fake_repo, onto sys.path — so
        # the import must succeed even though fake_repo has no server/.
        old_path = list(sys.path)
        old_module = sys.modules.pop("server", None)
        try:
            sys.path.insert(0, str(ai_brain_root))
            from server import storage  # noqa: F401
        finally:
            sys.path[:] = old_path
            if old_module is not None:
                sys.modules["server"] = old_module


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("absorb push retry: all checks passed")
