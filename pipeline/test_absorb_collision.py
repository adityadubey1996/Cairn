"""Self-check: two different units whose model-chosen titles slug to the
same wiki filename must never silently overwrite each other.

Run: python3 -m pytest pipeline/test_absorb_collision.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import absorb_runner  # noqa: E402


def _article(label: str, file: str) -> str:
    """A minimal but validate()-clean article body, as the model would emit
    it: a leading target comment, real frontmatter, one graded citation, and
    enough lines to clear the `unknown` type's 15-line floor."""
    return f"""<!-- target: wiki/unknowns/thing.md -->
---
title: Thing
type: unknown
created: 2026-01-01
last_updated: 2026-01-01
stale: false
grades: {{gap: 0}}
sources: []
related: []
---

# Thing

This article is about package {label}. [code: {file}@abc1234]

Package {label} provides a small capability used elsewhere in the system. [code: {file}@abc1234]

It has one file that implements the {label} behavior directly. [code: {file}@abc1234]

[code: {file}@abc1234]

There is no further detail recorded about this unit yet. [gap: further detail]

The remaining behavior of {label} is unexplored in this pass. [gap: remaining behavior]

Future absorbs may expand on this article as more of the package is read. [gap: additional context]

This line exists to satisfy the minimum length floor for unknown-type articles. [gap: further evidence]
"""


def test_slug_collision_between_different_units_does_not_overwrite(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    wiki = tmp_path / "wiki"
    (repo / "raw" / "entries").mkdir(parents=True)
    wiki.mkdir()
    (repo / "a.py").write_text("# package A\n")
    (repo / "b.py").write_text("# package B\n")

    # Sidestep real git: every cited path resolves to the same fixed sha,
    # in both absorb_runner's own citation gathering and validate_wiki's
    # independent citation check. absorb_runner.py adds pipeline/ to
    # sys.path and imports validate_wiki bare, so `sys.modules["validate_wiki"]`
    # (what absorb_runner.validate actually calls into) is a different module
    # object than the `pipeline.validate_wiki` this test imported — patch via
    # validate's own __globals__ so it lands on the instance that matters.
    monkeypatch.setattr(absorb_runner, "blob_sha", lambda repo, p: "abc1234")
    monkeypatch.setitem(absorb_runner.validate.__globals__, "git_blob_sha",
                        lambda repo, p: "abc1234")

    entry_a = repo / "raw" / "entries" / "2026-01-01_pkg-a.md"
    entry_a.write_text('---\nid: pkg-a\npath: "a.py"\n---\n\nEntry A\n')
    entry_b = repo / "raw" / "entries" / "2026-01-02_pkg-b.md"
    entry_b.write_text('---\nid: pkg-b\npath: "b.py"\n---\n\nEntry B\n')

    # Both units' models independently choose the title "Thing" -> the same
    # slug -> the same target path, wiki/unknowns/thing.md.
    outputs = iter([_article("A", "a.py"), _article("B", "b.py")])
    # groq() returns (content, usage) — the usage block is what feeds the
    # token ceiling, so the stub has to hand one back too.
    monkeypatch.setattr(absorb_runner, "groq",
                        lambda msgs, key, temperature=0.2: (next(outputs), {}))

    r1, _ = absorb_runner.run_one(repo, wiki, "pkg-a", entry_a, "key", "headsha", 1, False)
    assert r1.startswith("ok"), r1
    r2, _ = absorb_runner.run_one(repo, wiki, "pkg-b", entry_b, "key", "headsha", 1, False)
    assert r2.startswith("ok"), r2

    # The result line is a CONTRACT with scripts/pipeline_run.py, which greps
    # `tok=in/out  Ns` back out of the log to learn what a run cost. That
    # regex matched nothing for as long as run_one emitted no token counts, so
    # the two formats are asserted against each other here rather than trusted.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from pipeline_run import parse_result
    assert parse_result(f"    {r1}"), f"pipeline_run cannot parse: {r1!r}"

    original = wiki / "unknowns" / "thing.md"
    assert original.is_file(), list((wiki / "unknowns").iterdir())
    original_body = original.read_text()
    assert "unit: pkg-a" in original_body
    assert "package A" in original_body
    assert "package B" not in original_body, "pkg-b silently overwrote pkg-a's article"

    disambiguated = wiki / "unknowns" / "thing-pkg-b.md"
    assert disambiguated.is_file(), (
        f"expected a disambiguated file for pkg-b, found: {list((wiki / 'unknowns').iterdir())}")
    assert "unit: pkg-b" in disambiguated.read_text()
    assert "package B" in disambiguated.read_text()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
