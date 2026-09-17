#!/usr/bin/env python3
"""Self-check for build_index.  Run: python3 test_build_index.py

The failure this guards: absorb puts _index.md in the prompt and wikilinks may
only use titles from it, so an article the index cannot see gets rewritten under
a near-identical name. One repo shipped 27 of 51 articles indexed and got
"Backend New API" beside "backend_new API", two "(Package)" twins, and four MCP
Server articles.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_index import duplicate_titles, rebuild  # noqa: E402


def article(title, type_, body="Some prose about it.", related=()):
    rel = ", ".join(f'"[[{r}]]"' for r in related)
    return (f"---\ntitle: {title}\ntype: {type_}\nrelated: [{rel}]\n---\n\n"
            f"# {title}\n\n{body}\n")


def wiki_fixture(tmp: Path) -> Path:
    wiki = tmp / "wiki"
    for d in ("systems", "domain", "flows", "ops"):
        (wiki / d).mkdir(parents=True)
    (wiki / "systems/backend-new.md").write_text(article(
        "Backend New", "system", "The active API. [code: src/main.py@a1b2c3d]",
        related=["Arps Forecasting"]))
    (wiki / "domain/arps.md").write_text(article(
        "Arps Forecasting", "domain", "Decline curves. [gap: no b-factor bounds]"))
    (wiki / "flows/pdp.md").write_text(article(
        "PDP Flow", "flow", "Runs end to end. [verified: src/pdp.py@e4f5a6b]",
        related=["Backend New", "Ghost Article"]))
    # A type with no TYPE_ORDER section — must not vanish from the index.
    (wiki / "ops/legacy.md").write_text(article("Legacy Deploy", "ops"))
    (wiki / "_index.md").write_text("# Stale\n\n- [[Only One]]\n")
    return wiki


def test_index_lists_every_article():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        r = rebuild(wiki, "Test Wiki")
        assert r["articles"] == 4, r
        idx = (wiki / "_index.md").read_text()
        for t in ("Backend New", "Arps Forecasting", "PDP Flow", "Legacy Deploy"):
            assert f"[[{t}]]" in idx, f"{t} missing from index"


def test_unknown_type_is_surfaced_not_dropped():
    """A type absent from TYPE_ORDER must land in Unclassified. Silently dropping
    it is how an article becomes invisible to the next absorb."""
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        rebuild(wiki)
        idx = (wiki / "_index.md").read_text()
        assert "## Unclassified" in idx
        assert "[[Legacy Deploy]]" in idx.split("## Unclassified")[1]


def test_backlinks_are_reciprocal_and_never_dangling():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        r = rebuild(wiki)
        bl = json.loads((wiki / "_backlinks.json").read_text())
        assert bl["Backend New"]["backlinks_from"] == ["PDP Flow"], bl["Backend New"]
        assert bl["Arps Forecasting"]["backlinks_from"] == ["Backend New"]
        # "Ghost Article" is linked but does not exist: it is a broken link, not
        # a node. Recording it would assert an article nobody wrote.
        assert "Ghost Article" not in bl
        assert r["dangling_links"] == ["Ghost Article"], r["dangling_links"]


def test_grades_are_counted_from_the_body():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        rebuild(wiki)
        g = json.loads((wiki / "_grades.json").read_text())
        assert g["PDP Flow"] == {"verified": 1}, g["PDP Flow"]
        assert g["Arps Forecasting"] == {"gap": 1}, g["Arps Forecasting"]


def test_stale_index_is_replaced_not_appended():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        rebuild(wiki)
        assert "[[Only One]]" not in (wiki / "_index.md").read_text()


def test_duplicate_detection_catches_the_real_shapes():
    """Both real-world shapes: a case/underscore variant, and a '(Package)' twin."""
    dupes = duplicate_titles([
        {"title": "backend_new API"}, {"title": "Backend New API"},
        {"title": "EOR Wells Workflow"}, {"title": "EOR Wells Workflow (Package)"},
        {"title": "Carbon Modeling"},
    ])
    assert len(dupes) == 2, dupes
    assert "Carbon Modeling" not in str(dupes)


def test_clean_wiki_reports_no_duplicates():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        assert rebuild(wiki)["duplicate_titles"] == {}


def test_hub_pages_get_a_topics_section_not_unclassified():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        (wiki / "topics").mkdir()
        (wiki / "topics/carbon-ai.md").write_text(
            "---\ntitle: Carbon AI\nhub: true\ncreated: 2026-08-18\n"
            "last_updated: 2026-08-18\n---\n\n# Carbon AI\n\nCarbon work.\n\n"
            "## Domain\n\n[[Arps Forecasting]]\n")
        rebuild(wiki)
        idx = (wiki / "_index.md").read_text()
        assert "## Topics" in idx
        topics_section = idx.split("## Topics")[1].split("\n## ")[0]
        assert "[[Carbon AI]]" in topics_section, topics_section
        if "## Unclassified" in idx:
            assert "[[Carbon AI]]" not in idx.split("## Unclassified")[1]
        # Verify Counts section includes "topics 1" and reconciles (4 pre-existing + 1 hub = 5 total)
        counts_section = idx.split("## Counts")[1] if "## Counts" in idx else ""
        assert "topics 1" in counts_section, f"Expected 'topics 1' in Counts section: {counts_section}"
        assert "5 articles" in counts_section, f"Expected '5 articles' in Counts section: {counts_section}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("build_index: all checks passed")
