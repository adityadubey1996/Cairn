#!/usr/bin/env python3
"""Self-check: history() must track a renamed file's TRUE origin date, not
the date of the commit that renamed it. Run: python3 test_ingest_history.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ingest  # noqa: E402


def _commit(repo, message, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
          "GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@example.com",
          "GIT_COMMITTER_NAME": "A", "GIT_COMMITTER_EMAIL": "a@example.com"}
    ingest.git(repo, "add", "-A")
    ingest.git(repo, "commit", "-q", "-m", message, env=env)


def check_history_tracks_true_origin_across_rename():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        ingest.git(repo, "init", "-q", "-b", "main")
        (repo / "notes").mkdir()
        (repo / "notes" / "plan.md").write_text("v1")
        _commit(repo, "add plan", "2023-01-01T00:00:00")
        (repo / "docs").mkdir()
        ingest.git(repo, "mv", "notes/plan.md", "docs/plan.md")
        _commit(repo, "reorganize", "2026-06-01T00:00:00")

        first, last, count, authors = ingest.history(repo)
        assert first["docs/plan.md"].startswith("2023-01-01"), first.get("docs/plan.md")
        assert last["docs/plan.md"].startswith("2026-06-01"), last.get("docs/plan.md")


if __name__ == "__main__":
    check_history_tracks_true_origin_across_rename()
    print("ok")
