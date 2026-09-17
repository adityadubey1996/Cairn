#!/usr/bin/env python3
"""Self-check: 'does an article already cover this unit' must match the
unit that produced the article, never a bystander that merely cites a file
inside the same package as a dependency.
Run: python3 test_absorb_coverage.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import absorb_runner  # noqa: E402


def check_dependency_citation_is_not_mistaken_for_coverage():
    with tempfile.TemporaryDirectory() as d:
        wiki = Path(d)
        # Article A is genuinely about package A, but cites a file from
        # package B as a dependency — this must NOT count as "B already has
        # an article."
        (wiki / "a.md").write_text(
            "---\ntitle: A\nunit: pkg-a\n---\n\n# A\n"
            "[code: pkg_b/helper.py@abc1234]\n")
        body, path = absorb_runner.find_existing_article(wiki, "pkg-b")
        assert path is None, f"pkg-b incorrectly matched to {path}"


def check_real_prior_absorb_of_the_same_unit_is_found():
    with tempfile.TemporaryDirectory() as d:
        wiki = Path(d)
        (wiki / "a.md").write_text("---\ntitle: A\nunit: pkg-a\n---\n\n# A\n")
        body, path = absorb_runner.find_existing_article(wiki, "pkg-a")
        assert path is not None and path.name == "a.md", path


if __name__ == "__main__":
    check_dependency_citation_is_not_mistaken_for_coverage()
    check_real_prior_absorb_of_the_same_unit_is_found()
    print("ok")
