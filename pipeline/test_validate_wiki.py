#!/usr/bin/env python3
"""Self-check for wiki article validation.  Run: python3 test_validate_wiki.py

These checks exist so a cheap model can be trusted to generate articles. A weak
writer gets *form* wrong — rollups that disagree with the tags, invented
wikilinks, citations to files that moved — and all of that is catchable without
spending a token. Every assertion here corresponds to a failure mode seen in
real generated (or hand-written) output.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from validate_wiki import article_titles, fix_rollup, frontmatter, validate  # noqa: E402

GOOD = """---
title: Thing
type: gap
created: 2026-01-01
last_updated: 2026-01-01
stale: false
grades: {code: 1, gap: 1}
sources: ["a/b.py@1234abcd"]
related: []
---

# Thing

`run_thing` does the thing and returns a result.
[code: a/b.py@1234abcd]

More prose so the article clears the fifteen-line minimum, because an article
shorter than that is a stub rather than knowledge, and the length floor exists
to catch generators that emit a title and stop.

Additional context lines to clear the floor comfortably, since the fixture
must pass cleanly for the negative cases below to mean anything.
One. Two. Three.
Four. Five. Six.
Seven. Eight. Nine.
Ten. Eleven. Twelve.

[gap: nothing else was read during this pass]
"""


def repo_with(article: str, code: str = "def run_thing():\n    return 1\n"):
    """A throwaway git repo so blob-sha checks have something real to resolve."""
    d = Path(tempfile.mkdtemp())
    (d / "a").mkdir()
    (d / "a" / "b.py").write_text(code)
    (d / "wiki").mkdir()
    (d / "wiki" / "t.md").write_text(article)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c",
                "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(d), *cmd], capture_output=True)
    return d


def check(article, **kw):
    d = repo_with(article)
    sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD:a/b.py"],
                         capture_output=True, text=True).stdout.strip()[:8]
    body = article.replace("1234abcd", sha)
    (d / "wiki" / "t.md").write_text(body)
    return validate(d / "wiki" / "t.md", d, article_titles(d / "wiki"), None,
                    kw.get("anchor", False))


def test_a_well_formed_article_passes():
    r = check(GOOD)
    assert r.ok, r.errors


def test_missing_frontmatter_keys_fail():
    r = check(GOOD.replace("stale: false\n", ""))
    assert any(c == "frontmatter" for c, _ in r.errors), r.errors


def test_rollup_must_match_inline_tags():
    """The frontmatter rollup feeds /wiki status and the grade audit; if it
    drifts from the tags every downstream number is wrong."""
    r = check(GOOD.replace("{code: 1, gap: 1}", "{code: 9, gap: 1}"))
    assert any(c == "grades rollup" for c, _ in r.errors), r.errors


def test_graded_claim_without_a_citation_fails():
    """A claim with no path@sha cannot go stale, so it cannot be trusted."""
    r = check(GOOD.replace("[code: a/b.py@1234abcd]", "[code]"))
    assert any(c == "citation" for c, _ in r.errors), r.errors


def test_citation_to_a_missing_file_fails():
    r = check(GOOD.replace("a/b.py@1234abcd", "a/gone.py@1234abcd"))
    assert any(c == "cited path" for c, _ in r.errors), r.errors


def test_conflict_tag_vs_separator_is_not_swallowed_into_the_citation():
    """CITE's optional space-segment (added for `*.excalidraw copy` filenames)
    must not also absorb the literal "vs" in [conflict: A@sha vs B@sha] into
    the second citation — that corrupted it into "vs a/b.py@sha", which never
    resolves to a real path and quarantined every article using a conflict
    tag."""
    article = GOOD.replace(
        "grades: {code: 1, gap: 1}", "grades: {code: 1, gap: 1, conflict: 1}"
    ) + "\n[conflict: a/b.py@1234abcd vs a/b.py@1234abcd]\n"
    r = check(article)
    assert r.ok, r.errors


def test_stale_sha_fails():
    r = check(GOOD.replace("1234abcd", "deadbeef"))
    assert any(c == "stale sha" for c, _ in r.errors), r.errors


def test_dangling_wikilink_fails():
    r = check(GOOD.replace("# Thing", "# Thing\n\nSee [[No Such Article]]."))
    assert any(c == "wikilink" for c, _ in r.errors), r.errors


def test_too_short_fails():
    short = GOOD.split("More prose")[0] + "[gap: x]\n"
    r = check(short)
    assert any(c == "length" for c, _ in r.errors), r.errors


def test_ungraded_article_fails():
    import re
    r = check(re.sub(r"\[(code|gap)[^\]]*\]", "", GOOD))
    assert any(c == "grades" for c, _ in r.errors), r.errors


def test_anchor_flags_a_symbol_absent_from_the_cited_file():
    """The one thing mechanical checks cannot see: whether the file supports the
    claim. Found three genuinely mis-cited claims in a hand-written article."""
    r = check(GOOD.replace("`run_thing`", "`totally_invented_fn`"), anchor=True)
    assert any(c == "anchor" for c, _ in r.warnings), r.warnings


def test_anchor_tolerates_dotted_references():
    """`Class.method` never appears literally; the definition is `def method`."""
    r = check(GOOD.replace("`run_thing`", "`Thing.run_thing`"), anchor=True)
    assert not any(c == "anchor" for c, _ in r.warnings), r.warnings


def test_fix_rollup_rewrites_a_lying_rollup_from_the_tags():
    d = repo_with(GOOD.replace("{code: 1, gap: 1}", "{code: 9, gap: 1}"))
    p = d / "wiki" / "t.md"
    assert fix_rollup(p)
    assert "grades: {verified: 0, code: 1, doc: 0, conflict: 0, gap: 1}" in p.read_text()
    assert not fix_rollup(p)  # second pass: nothing left to fix


def test_fix_rollup_leaves_a_truthful_rollup_byte_identical():
    """A corpus-wide fix must only diff real mismatches — a sparse-but-correct
    rollup ({code: 1, gap: 1} with zeros implied) is not a mismatch."""
    d = repo_with(GOOD)
    p = d / "wiki" / "t.md"
    before = p.read_text()
    assert not fix_rollup(p)
    assert p.read_text() == before


def test_frontmatter_split_survives_no_frontmatter():
    fm, body = frontmatter("# just a heading\n")
    assert fm == "" and body.startswith("# just")


def test_bad_date_format_rejected():
    bad = GOOD.replace("created: 2026-01-01", "created: not-a-date")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "a.md"
        p.write_text(bad)
        r = validate(p, Path(d), set(), None, anchor=False)
        assert any("created" in msg for _, msg in r.errors), r.errors


def check_unresolvable_git_path_is_an_error_not_silently_skipped():
    # A citation to a path that .exists() on disk but that git cannot
    # resolve at HEAD (e.g. never committed, or a case mismatch) must be a
    # loud validation error, never a silent pass.
    article = GOOD.replace("a/b.py@1234abcd", "untracked.py@1234abcd")
    repo = repo_with(article)
    (repo / "untracked.py").write_text("x = 1\n")  # written to disk, never git add/commit
    p = repo / "wiki" / "t.md"
    r = validate(p, repo, set(), None, anchor=False)
    assert any("stale sha" in c for c, _ in r.errors), r.errors


def test_default_sweep_skips_generated_hubs():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        wiki = repo / "wiki"
        (wiki / "topics").mkdir(parents=True)
        (wiki / "topics/carbon-ai.md").write_text(
            "---\ntitle: Carbon AI\nhub: true\n---\n\n# Carbon AI\n\nNo grades here.\n")
        r = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "validate_wiki.py"),
             "--repo", str(repo), "--wiki", str(wiki)],
            capture_output=True, text=True)
        assert "no articles under" in r.stdout, r.stdout  # hub was not swept
        assert r.returncode == 0, (r.returncode, r.stdout)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") or name.startswith("check_"):
            fn()
            print(f"  ok  {name}")
    print("all wiki-validation checks passed")
