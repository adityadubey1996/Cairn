#!/usr/bin/env python3
"""Self-check for topics.py.  Run: python3 pipeline/test_topics.py

No network, no Postgres: the LLM call and DB fetch are thin shells around
pure functions, and only the pure functions are tested here.
"""
import json
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import topics  # noqa: E402
from topics import load_state, save_state, slugify, STATE_DEFAULT, catalog  # noqa: E402
from topics import coherence_check, corpus_baseline, read_vectors  # noqa: E402
from topics import build_proposal_prompt, merge_candidates, parse_proposal  # noqa: E402
from topics import apply_proposal, recompute_unassigned  # noqa: E402
from topics import render_hub, write_hubs  # noqa: E402
from topics import score_messages  # noqa: E402
from topics import mine_pressure, pressure_says_propose, member_texts_for, cmd_status  # noqa: E402


def test_missing_state_file_yields_default():
    with tempfile.TemporaryDirectory() as td:
        state = load_state(Path(td))
        assert state == STATE_DEFAULT, state
        assert state is not STATE_DEFAULT  # a copy — callers mutate it


def test_state_roundtrips_and_is_pretty_printed():
    with tempfile.TemporaryDirectory() as td:
        wiki = Path(td)
        state = load_state(wiki)
        state["topics"]["carbon-ai"] = {"name": "Carbon AI", "status": "candidate"}
        save_state(wiki, state)
        assert load_state(wiki) == state
        raw = (wiki / "_topics.json").read_text()
        assert raw.startswith("{\n"), "must be indented like the other sidecars"
        assert raw.endswith("\n")


def test_corrupt_state_file_is_a_hard_error():
    with tempfile.TemporaryDirectory() as td:
        wiki = Path(td)
        (wiki / "_topics.json").write_text("{not json")
        try:
            load_state(wiki)
            assert False, "corrupt state must raise, not silently reset"
        except json.JSONDecodeError:
            pass


def test_slugify_matches_house_convention():
    assert slugify("Carbon AI") == "carbon-ai"
    assert slugify("  Data Center / Site-Intelligence!  ") == "data-center-site-intelligence"


def article(title, type_, body="First sentence of prose.", related=()):
    rel = ", ".join(f'"[[{r}]]"' for r in related)
    return (f"---\ntitle: {title}\ntype: {type_}\nrelated: [{rel}]\n---\n\n"
            f"# {title}\n\n{body}\n")


def wiki_fixture(tmp: Path) -> Path:
    """Six articles in two obvious clusters + one loner, plus noise to skip."""
    wiki = tmp / "wiki"
    for d in ("domain", "decisions", "flows", "topics", "packages"):
        (wiki / d).mkdir(parents=True)
    (wiki / "domain/carbon-ai.md").write_text(article("Carbon AI", "domain"))
    (wiki / "domain/carbon-mrv.md").write_text(article("Carbon MRV", "domain"))
    (wiki / "decisions/carbon-registry.md").write_text(article("Carbon Registry Choice", "decision"))
    (wiki / "domain/well-economics.md").write_text(article("Well Economics", "domain"))
    (wiki / "flows/well-forecast.md").write_text(article("Well Forecast Flow", "flow"))
    (wiki / "domain/wolfram.md").write_text(article("Wolfram Community", "domain"))
    (wiki / "_index.md").write_text("# Index\n")                      # skipped: underscore
    (wiki / "topics/old-hub.md").write_text("---\ntitle: Old Hub\nhub: true\n---\n# Old Hub\n")  # skipped: hub
    return wiki


def test_catalog_lists_articles_not_hubs_not_sidecars():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        rels = [c["rel"] for c in cat]
        assert len(cat) == 6, rels
        assert "topics/old-hub.md" not in rels
        assert "_index.md" not in rels
        assert rels == sorted(rels)
        row = next(c for c in cat if c["rel"] == "domain/carbon-ai.md")
        assert row["title"] == "Carbon AI" and row["type"] == "domain"
        assert row["lede"].startswith("First sentence"), row


def test_catalog_sorts_by_rel_string_not_path_tuples():
    """Catch the case where Path().rglob sorts differ from string sorts.

    Path objects compare by component tuple (prefix-first), while rel strings
    sort by character value. When a dirname is a prefix of another with a
    character sorting before "/" (like "-"), the orderings diverge:
    Path("flows") < Path("flows-legacy"), but "flows-legacy/x" < "flows/y"
    (because "-" (0x2D) < "/" (0x2F)).
    """
    with tempfile.TemporaryDirectory() as td:
        wiki = Path(td) / "wiki"
        for d in ("flows", "flows-legacy", "domain"):
            (wiki / d).mkdir(parents=True)
        (wiki / "flows-legacy/y.md").write_text(article("Y Article", "flow"))
        (wiki / "flows/x.md").write_text(article("X Article", "flow"))
        (wiki / "domain/z.md").write_text(article("Z Article", "domain"))
        cat = catalog(wiki)
        rels = [c["rel"] for c in cat]
        assert rels == ["domain/z.md", "flows-legacy/y.md", "flows/x.md"], rels


def fake_index(tmp: Path, vecs: dict[str, tuple[float, ...]]) -> Path:
    """Minimal replica of server/index.py's embeddings table."""
    import sqlite3
    db_p = tmp / "index.sqlite3"
    db = sqlite3.connect(db_p)
    db.execute("CREATE TABLE embeddings(path TEXT PRIMARY KEY, vec BLOB)")
    for rel, v in vecs.items():
        db.execute("INSERT INTO embeddings VALUES (?,?)",
                   (rel, struct.pack(f"{len(v)}f", *v)))
    db.commit(); db.close()
    return db_p


def test_coherent_cluster_passes_and_scattered_fails():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = fake_index(tmp, {
            "a.md": (1.0, 0.0, 0.0), "b.md": (0.99, 0.05, 0.0), "c.md": (0.98, 0.1, 0.0),
            "x.md": (0.0, 1.0, 0.0), "y.md": (0.0, 0.0, 1.0), "z.md": (0.5, 0.5, 0.0),
        })
        base = corpus_baseline(db)
        ok, note = coherence_check(["a.md", "b.md", "c.md"], db, base)
        assert ok, note
        ok, note = coherence_check(["a.md", "x.md", "y.md"], db, base)
        assert not ok, note


def test_too_few_vectors_skips_coherence():
    with tempfile.TemporaryDirectory() as td:
        db = fake_index(Path(td), {"a.md": (1.0, 0.0)})
        ok, note = coherence_check(["a.md", "missing.md", "also-missing.md"], db, 0.9)
        assert ok and "skipped" in note, (ok, note)


def test_read_vectors_returns_only_present_rels():
    with tempfile.TemporaryDirectory() as td:
        db = fake_index(Path(td), {"a.md": (1.0, 0.0)})
        vecs = read_vectors(db, ["a.md", "ghost.md"])
        assert set(vecs) == {"a.md"} and len(vecs["a.md"]) == 2, vecs


def state_with(topics=None, unassigned=(), veto=()):
    s = json.loads(json.dumps({"topics": topics or {}, "unassigned": list(unassigned),
                               "pressure": {"packages_fallback": 0, "domain_overflow": 0,
                                            "since": ""},
                               "last_scored_at": None}))
    for name in veto:
        s["topics"][slugify(name)] = {"name": name, "description": "", "status": "vetoed",
                             "pinned": False, "members": {}, "created": "2026-08-18",
                             "score": {"window_uses": 0, "cycles_unused": 0, "last_used": ""},
                             "history": []}
    return s


def topic(name, members, status="active", pinned=False):
    return {"name": name, "description": f"About {name}.", "status": status,
            "pinned": pinned, "members": {m: 0.9 for m in members},
            "created": "2026-08-18",
            "score": {"window_uses": 0, "cycles_unused": 0, "last_used": ""},
            "history": []}


def test_prompt_carries_catalog_actives_unassigned_vetoes_and_rules():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with({"well-work": topic("Well Work", ["flows/well-forecast.md"])},
                       unassigned=["domain/wolfram.md"], veto=["Misc"])
        p = build_proposal_prompt(cat, s)
        assert "domain/carbon-ai.md" in p and "Carbon AI" in p
        assert "Well Work" in p and "domain/wolfram.md" in p
        assert "Misc" in p and "never propose" in p.lower()
        assert "at most 2 topics" in p          # membership cap stated
        assert "JSON" in p


def test_parse_accepts_fenced_json_and_defaults_missing_keys():
    out = parse_proposal('```json\n{"new_topics": []}\n```')
    assert out == {"new_topics": [], "assignments": {}, "merges": []}, out


def test_parse_rejects_garbage_and_wrong_shapes():
    assert parse_proposal("I think Carbon AI would be nice.") is None
    assert parse_proposal('{"new_topics": "not-a-list"}') is None


def test_merge_candidates_flags_overlap_and_respects_pin():
    a = topic("A", ["1.md", "2.md", "3.md"])
    b = topic("B", ["2.md", "3.md", "4.md"])          # jaccard 2/4 = 0.5
    c = topic("C", ["1.md", "2.md", "3.md"], pinned=True)  # same as a (jaccard 1.0), but pinned
    s = state_with({"a": a, "b": b, "c": c})
    assert merge_candidates(s) == [("a", "b")], merge_candidates(s)


COH_PASS = lambda rels: (True, "stub: pass")
COH_FAIL = lambda rels: (False, "stub: fail")


def test_threshold_gates_materialization():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with()
        five = ["domain/carbon-ai.md", "domain/carbon-mrv.md", "decisions/carbon-registry.md",
                "domain/well-economics.md", "flows/well-forecast.md"]
        # name deliberately does NOT match any fixture article title (see the
        # collision-guard tests for that case) — this test is about the
        # MIN_MEMBERS/coherence threshold only.
        proposal = {"new_topics": [
            {"name": "Carbon Cluster", "description": "d", "members": {m: 0.9 for m in five}},
            {"name": "Tiny", "description": "d", "members": {"domain/wolfram.md": 0.9}},
        ], "assignments": {}, "merges": []}
        apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
        assert s["topics"]["carbon-cluster"]["status"] == "active"
        assert s["topics"]["tiny"]["status"] == "candidate"          # < MIN_MEMBERS
        events = [h["event"] for h in s["topics"]["carbon-cluster"]["history"]]
        assert events == ["proposed", "materialized"], events


def test_coherence_failure_keeps_candidate():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with()
        five = [c["rel"] for c in cat[:5]]
        proposal = {"new_topics": [{"name": "Scattered", "description": "d",
                                    "members": {m: 0.9 for m in five}}],
                    "assignments": {}, "merges": []}
        apply_proposal(s, proposal, cat, COH_FAIL, "2026-08-18")
        assert s["topics"]["scattered"]["status"] == "candidate"


def test_confidence_floor_cap_and_unknown_rels():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with({"a": topic("A", ["domain/carbon-ai.md"]),
                        "b": topic("B", ["domain/carbon-ai.md"])})
        proposal = {"new_topics": [{"name": "C", "description": "d", "members": {
            "domain/carbon-ai.md": 0.6,      # article already in 2 topics -> capped out
            "domain/wolfram.md": 0.4,        # below MIN_CONFIDENCE -> dropped
            "ghost/nope.md": 0.9,            # not in catalog -> dropped
        }}], "assignments": {}, "merges": []}
        apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
        assert s["topics"]["c"]["members"] == {}, s["topics"]["c"]["members"]


def test_new_topic_colliding_with_article_title_is_never_created():
    """The corpus already has two articles titled 'Carbon AI' — a topic must
    never claim that exact name (case-insensitively, whitespace-insensitively),
    or its hub file collides with both articles' title-to-path mapping."""
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with()
        proposal = {"new_topics": [
            {"name": "Carbon AI", "description": "d",
             "members": {"domain/well-economics.md": 0.9}},
            {"name": "  carbon ai  ", "description": "d",
             "members": {"domain/well-economics.md": 0.9}},
        ], "assignments": {}, "merges": []}
        log = apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
        assert "carbon-ai" not in s["topics"], s["topics"]
        assert sum("collides with an existing article title" in line for line in log) == 2, log


def test_veto_blocks_recreation_and_merge_moves_members():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with({"a": topic("A", ["domain/carbon-ai.md", "domain/carbon-mrv.md"]),
                        "b": topic("B", ["decisions/carbon-registry.md"])},
                       veto=["Junk Drawer"])
        proposal = {"new_topics": [{"name": "Junk Drawer", "description": "d",
                                    "members": {"domain/wolfram.md": 0.9}}],
                    "assignments": {}, "merges": [{"a": "a", "b": "b"}]}
        apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
        assert s["topics"]["junk-drawer"]["status"] == "vetoed"      # unchanged
        assert s["topics"]["b"]["status"] == "merged"
        assert "decisions/carbon-registry.md" in s["topics"]["a"]["members"]


def test_merge_transfers_member_shared_with_a_third_topic():
    """Regression: while b's members are being moved into a one rel at a time,
    b must not count as a holder of its own outgoing rel — otherwise a rel b
    shares with a genuinely separate topic looks like it already has 2
    holders, gets rejected by the cap/tie-break check, and is then wiped by
    b's clear-out anyway: dropped from the union instead of moved."""
    cat = [{"rel": "shared/x.md"}, {"rel": "b-only.md"}]
    s = state_with({"a": topic("A", []),
                    "b": topic("B", ["shared/x.md", "b-only.md"]),
                    "c": topic("C", ["shared/x.md"])})
    proposal = {"new_topics": [], "assignments": {}, "merges": [{"a": "a", "b": "b"}]}
    apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
    assert s["topics"]["a"]["members"] == {"shared/x.md": 0.9, "b-only.md": 0.9}, \
        s["topics"]["a"]["members"]
    assert s["topics"]["b"]["members"] == {}
    assert s["topics"]["b"]["status"] == "merged"
    assert s["topics"]["c"]["members"] == {"shared/x.md": 0.9}, "unrelated topic untouched"


def test_recompute_unassigned():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        s = state_with({"a": topic("A", ["domain/carbon-ai.md"])})
        recompute_unassigned(s, cat)
        assert "domain/carbon-ai.md" not in s["unassigned"]
        assert "domain/wolfram.md" in s["unassigned"]
        assert len(s["unassigned"]) == 5, s["unassigned"]


def test_malformed_assignment_value_is_skipped_not_crashed():
    """parse_proposal only checks assignments is a dict, not its values — a
    per-slug value like a bare string must be skipped, not crash apply_proposal."""
    cat = [{"rel": "domain/carbon-ai.md"}, {"rel": "domain/carbon-mrv.md"},
           {"rel": "decisions/carbon-registry.md"}]
    s = state_with({"a": topic("A", ["domain/carbon-ai.md"]),
                    "b": topic("B", ["domain/carbon-mrv.md"])})
    proposal = {"new_topics": [], "assignments": {
        "a": "not-a-dict",
        "b": {"decisions/carbon-registry.md": 0.9},
    }, "merges": []}
    log = apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
    assert s["topics"]["a"]["members"] == {"domain/carbon-ai.md": 0.9}, s["topics"]["a"]["members"]
    assert s["topics"]["b"]["members"] == {"domain/carbon-mrv.md": 0.9,
                                            "decisions/carbon-registry.md": 0.9}
    assert any("skipped malformed assignment for a" in line for line in log), log


def test_malformed_merge_entry_is_skipped_not_crashed():
    """parse_proposal only checks merges is a list, not its entries — a bare
    string entry must be skipped, not crash apply_proposal on m.get("a")."""
    cat = [{"rel": "domain/carbon-ai.md"}, {"rel": "domain/carbon-mrv.md"},
           {"rel": "decisions/carbon-registry.md"}]
    s = state_with({"a": topic("A", ["domain/carbon-ai.md", "domain/carbon-mrv.md"]),
                    "b": topic("B", ["decisions/carbon-registry.md"])})
    proposal = {"new_topics": [], "assignments": {},
                "merges": ["not-a-dict", {"a": "a", "b": "b"}]}
    log = apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
    assert s["topics"]["b"]["status"] == "merged"
    assert "decisions/carbon-registry.md" in s["topics"]["a"]["members"]
    assert any("skipped malformed merge entry" in line for line in log), log


def test_self_merge_is_skipped_not_corrupted():
    """a == b means both lookups return the SAME dict object — without a
    guard, the loop's clear-and-readmit is a no-op for members, but
    b["status"] = "merged" also sets a's status (a is b), silently flipping
    an active topic to merged with its members still intact and orphaned."""
    cat = [{"rel": "domain/carbon-ai.md"}, {"rel": "domain/carbon-mrv.md"}]
    a = topic("A", ["domain/carbon-ai.md", "domain/carbon-mrv.md"])
    s = state_with({"a": a})
    proposal = {"new_topics": [], "assignments": {}, "merges": [{"a": "a", "b": "a"}]}
    log = apply_proposal(s, proposal, cat, COH_PASS, "2026-08-18")
    assert s["topics"]["a"]["status"] == "active", s["topics"]["a"]
    assert s["topics"]["a"]["members"] == {"domain/carbon-ai.md": 0.9,
                                            "domain/carbon-mrv.md": 0.9}, s["topics"]["a"]
    assert any("self-merge" in line for line in log), log


def test_hub_renders_grouped_wikilinks_and_footer():
    with tempfile.TemporaryDirectory() as td:
        cat = catalog(wiki_fixture(Path(td)))
        t = topic("Carbon AI", ["domain/carbon-ai.md", "domain/carbon-mrv.md",
                                "decisions/carbon-registry.md", "ghost/gone.md"])
        text = render_hub(t, cat, "2026-08-18")
        assert text.startswith("---\ntitle: Carbon AI\nhub: true\n")
        assert "type:" not in text.split("---")[1]          # hubs have no type key
        assert "## Domain" in text and "## Decisions" in text
        assert "[[Carbon AI]] · [[Carbon MRV]]" in text
        assert "gone" not in text                            # dangling member dropped
        assert text.rstrip().endswith("hand edits are overwritten -->")


def test_hub_merges_multiple_unknown_types_into_single_other():
    with tempfile.TemporaryDirectory() as td:
        wiki = Path(td) / "wiki"
        wiki.mkdir()
        (wiki / "domain").mkdir()
        (wiki / "topics").mkdir()
        # Create articles with types that don't exist in vocab.TYPE_ORDER
        (wiki / "domain/faq-1.md").write_text(article("FAQ Article 1", "faq"))
        (wiki / "domain/glossary-1.md").write_text(article("Glossary Article 1", "glossary"))
        (wiki / "domain/known.md").write_text(article("Known Type", "domain"))
        cat = catalog(wiki)
        # Topic with two different unknown types and one known type
        t = topic("Mixed", ["domain/faq-1.md", "domain/glossary-1.md", "domain/known.md"])
        text = render_hub(t, cat, "2026-08-18")
        # Verify exactly one "## Other" heading
        assert text.count("## Other") == 1, f"Expected 1 '## Other' heading, got {text.count('## Other')}"
        # Verify both unknown-type articles are under that one heading
        assert "[[FAQ Article 1]] · [[Glossary Article 1]]" in text, \
            f"Expected wikilinks merged under Other, got:\n{text}"
        assert "## Domain" in text  # known type still there


def test_write_hubs_creates_removes_and_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        cat = catalog(wiki)
        s = state_with({
            "carbon-ai": topic("Carbon AI", ["domain/carbon-ai.md", "domain/carbon-mrv.md"]),
            "dead": topic("Dead", ["flows/well-forecast.md"], status="decayed"),
        })
        r = write_hubs(wiki, s, cat, "2026-08-18")
        hub = wiki / "topics" / "carbon-ai.md"
        assert hub.is_file() and r["written"] == ["topics/carbon-ai.md"], r
        assert not (wiki / "topics" / "dead.md").exists()
        assert r["removed"] == ["topics/old-hub.md"], r      # fixture's stale hub
        before = hub.read_text()
        r2 = write_hubs(wiki, s, cat, "2026-12-31")          # later day, same content
        assert r2["written"] == [] and hub.read_text() == before, r2


def msg(mid, hits=(), articles=(), cites=(), created_at: str | None = None):
    created_at = created_at or f"2026-01-01T00:00:00.{mid:06d}"
    return {"id": mid, "created_at": created_at, "meta": {
        "process": {"hits": [{"path": h, "score": 0.5} for h in hits],
                    "articles": [{"title": "t", "path": a, "repo": "ai-brain"}
                                 for a in articles]},
        "citations": [{"path": p, "sha": s} for p, s in cites]}}


CARBON = ["domain/carbon-ai.md", "domain/carbon-mrv.md", "decisions/carbon-registry.md",
          "domain/well-economics.md", "flows/well-forecast.md"]


def carbon_state(pinned=False):
    s = state_with({"carbon-ai": topic("Carbon AI", CARBON, pinned=pinned)})
    return s


def test_co_usage_needs_two_members_and_a_seed():
    texts = {r: "prose" for r in CARBON}
    s = carbon_state()
    # two members in context but NONE was a seed -> hub-expansion echo, no credit
    score_messages(s, [msg(1, hits=["other/x.md"],
                           articles=CARBON[:2])], texts, "2026-08-18")
    assert s["topics"]["carbon-ai"]["score"]["window_uses"] == 0
    # one member seeded, second arrives in context -> external demand, credit 1
    score_messages(s, [msg(2, hits=[CARBON[0]], articles=[CARBON[1]])], texts, "2026-08-18")
    assert s["topics"]["carbon-ai"]["score"]["window_uses"] == 1


def test_hub_seed_and_citation_double():
    texts = {r: "prose" for r in CARBON}
    texts[CARBON[0]] = "claim [code: src/mrv.py@a1b2c3d4]"
    s = carbon_state()
    rows = [msg(1, hits=["topics/carbon-ai.md"]),                       # hub seed: 1
            msg(2, hits=[CARBON[0]], articles=[CARBON[1]],
                cites=[("src/mrv.py", "a1b2c3d4")])]                    # co-usage x2: 2
    score_messages(s, rows, texts, "2026-08-18")
    assert s["topics"]["carbon-ai"]["score"]["window_uses"] == 3


def test_cycles_decay_and_pin_immunity():
    texts = {r: "prose" for r in CARBON}
    for pinned, expect in ((False, "decayed"), (True, "active")):
        s = carbon_state(pinned=pinned)
        for cycle in range(3):
            rows = [msg(cycle * 20 + i, hits=["other/x.md"])
                    for i in range(1, 21)]                              # 20 msgs, 0 credit
            score_messages(s, rows, texts, "2026-08-18")
        assert s["topics"]["carbon-ai"]["status"] == expect, (pinned, s)
        decayed_in_history = any(h["event"] == "decayed"
                                 for h in s["topics"]["carbon-ai"]["history"])
        assert decayed_in_history == (expect == "decayed"), (pinned, s)


def test_short_run_is_not_a_cycle_and_id_advances():
    texts = {r: "prose" for r in CARBON}
    s = carbon_state()
    m7 = msg(7, hits=["other/x.md"])
    score_messages(s, [m7], texts, "2026-08-18")  # 1 < 20 msgs
    assert s["topics"]["carbon-ai"]["score"]["cycles_unused"] == 0
    assert s["last_scored_at"] == m7["created_at"]


def test_pressure_counts_fallback_and_overflow():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        (wiki / "packages/stray.md").write_text(article("Stray", "unknown"))
        for i in range(21):
            (wiki / f"domain/extra-{i}.md").write_text(article(f"Extra {i}", "domain"))
        s = state_with()
        mine_pressure(wiki, s, "2026-08-18")
        assert s["pressure"]["packages_fallback"] == 1     # delta vs no prior baseline == total
        assert s["pressure"]["packages_fallback_total"] == 1, s["pressure"]
        assert s["pressure"]["domain_overflow"] == 25 - 20, s["pressure"]  # 4 fixture + 21
        assert s["pressure"]["since"] == "2026-08-18"


def test_pressure_trigger_thresholds():
    s = state_with(unassigned=[f"a{i}.md" for i in range(15)])
    assert pressure_says_propose(s)
    s = state_with()
    s["pressure"]["packages_fallback"] = 5
    assert pressure_says_propose(s)
    assert not pressure_says_propose(state_with())


def test_pressure_fallback_is_a_delta_since_last_measurement():
    """§7's trigger is 'fallback landings SINCE THE LAST ROUND >= 5', not a
    standing total — otherwise, once packages/ ever crosses the threshold,
    every later --auto run re-proposes forever even with nothing new."""
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        for i in range(3):
            (wiki / f"packages/p{i}.md").write_text(article(f"P{i}", "unknown"))
        s = state_with()
        mine_pressure(wiki, s, "2026-08-18")
        assert s["pressure"]["packages_fallback"] == 3, s["pressure"]  # no prior baseline
        mine_pressure(wiki, s, "2026-08-19")            # same 3 files, nothing new landed
        assert s["pressure"]["packages_fallback"] == 0, s["pressure"]


def test_member_texts_reads_active_members_only():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        s = state_with({"c": topic("C", ["domain/carbon-ai.md", "ghost/gone.md"]),
                        "d": topic("D", ["domain/wolfram.md"], status="candidate")})
        texts = member_texts_for(wiki, s)
        assert set(texts) == {"domain/carbon-ai.md"}, set(texts)


def test_status_runs_without_network_or_db():
    with tempfile.TemporaryDirectory() as td:
        wiki = wiki_fixture(Path(td))
        save_state(wiki, state_with({"c": topic("C", ["domain/carbon-ai.md"])}))
        assert cmd_status(wiki) == 0


def test_proposal_prompt_has_a_size_ceiling():
    cat = [{"rel": f"wiki/gap/article-{i}.md", "type": "gap",
            "title": f"Article {i}", "lede": "x" * 200} for i in range(2000)]
    # {"topics": {}, "unassigned": []} is a state recompute_unassigned() could
    # never produce for this catalog — with no topics, every article is
    # unassigned. Derive it the same way production code does, so this
    # exercises a real reachable state.
    state = {"topics": {}, "unassigned": []}
    recompute_unassigned(state, cat)
    prompt = topics.build_proposal_prompt(cat, state)
    assert len(prompt) <= topics.MAX_PROMPT_CHARS, len(prompt)
    assert "omitted" in prompt


def test_proposal_prompt_ceiling_holds_with_large_dropped_count():
    # 2000 articles with 46-char ledes drops 1000+ articles (3+ digit count),
    # so the omitted line itself is longer than a flat 60-char reservation
    # would allow for. This exact shape used to land the prompt 2 chars over
    # MAX_PROMPT_CHARS (60002 vs 60000).
    cat = [{"rel": f"wiki/gap/article-{i}.md", "type": "gap",
            "title": f"Article {i}", "lede": "x" * 46} for i in range(2000)]
    state = {"topics": {}, "unassigned": []}
    recompute_unassigned(state, cat)
    prompt = topics.build_proposal_prompt(cat, state)
    assert len(prompt) <= topics.MAX_PROMPT_CHARS, len(prompt)
    assert "omitted" in prompt


def test_proposal_prompt_ceiling_holds_with_large_unassigned_and_catalog():
    # The realistic worst case: a fresh corpus with no topics yet, where
    # recompute_unassigned() makes `unassigned` the ENTIRE catalog. Both
    # droppable sections are large at once, which the previous two tests
    # (unassigned=[], an impossible state given this catalog) never exercised
    # — that impossible state is exactly how the original bug hid: the
    # "fixed" portion the budget was computed from could itself already
    # exceed MAX_PROMPT_CHARS once a real unassigned list was embedded in it.
    # 2500 articles reproduces that: the pre-fix function landed this exact
    # shape 7,237 chars over MAX_PROMPT_CHARS (67,237 vs 60,000).
    cat = [{"rel": f"wiki/gap/article-{i}.md", "type": "gap",
            "title": f"Article {i}", "lede": "x" * 200} for i in range(2500)]
    state = {"topics": {}, "unassigned": []}
    recompute_unassigned(state, cat)
    assert len(state["unassigned"]) == 2500          # sanity: the worst case
    prompt = topics.build_proposal_prompt(cat, state)
    assert len(prompt) <= topics.MAX_PROMPT_CHARS, len(prompt)
    assert "omitted" in prompt


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("topics: all checks passed")
