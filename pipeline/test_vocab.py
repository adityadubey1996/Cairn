#!/usr/bin/env python3
"""Self-check for the article vocabulary.  Run: python3 test_vocab.py

The failure this guards: the type list was typed out by hand in five places and
they disagreed. SKILL.md's enum admitted 9 types, TYPE_DIR mapped 12 (with
`package`, without `ops`), TYPE_COLOR coloured 12 (with `ops`, without
`package`). An article could be typed into a folder the runner had no route for,
and 20 articles typed `service` ended up spread across three directories.

Two invariants: every consumer sees the same list, and the generated sites match
what vocab.py currently says.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gen_vocab  # noqa: E402
import vocab  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_every_field_is_populated():
    for t in vocab.TYPES:
        for field in ("name", "dir", "section", "colour", "cap", "lines", "checkable"):
            assert getattr(t, field), f"{t.name}.{field} is empty"


def test_names_dirs_and_colours_are_unique():
    """A duplicate dir silently merges two types into one folder; a duplicate
    colour makes the graph view lie about what a node is."""
    for field in ("name", "dir", "colour"):
        vals = [getattr(t, field) for t in vocab.TYPES]
        assert len(vals) == len(set(vals)), f"duplicate {field}: {vals}"


def test_colours_are_valid_hex():
    for t in vocab.TYPES:
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", t.colour), (t.name, t.colour)


def test_python_consumers_import_rather_than_copy():
    """absorb_runner routes files to folders, build_index groups the sections.
    If they ever disagree, an article files into one place and indexes under
    another — which is how articles became invisible to the next absorb."""
    import absorb_runner
    import build_index
    assert absorb_runner.TYPE_DIR is vocab.TYPE_DIR
    assert build_index.TYPE_ORDER is vocab.TYPE_ORDER
    assert set(absorb_runner.TYPE_DIR) == {t for t, _ in build_index.TYPE_ORDER}


def test_generated_sites_are_current():
    """The same check CI runs. If this fails, someone edited vocab.py without
    running gen_vocab.py, and the prompt now offers types the router cannot map."""
    for path, transform in gen_vocab.targets():
        assert path.is_file(), f"missing site: {path}"
        _, changed = transform(path.read_text(encoding="utf-8"))
        assert not changed, (f"{path.relative_to(ROOT)} is stale — "
                             "run: python3 gen_vocab.py")


def test_prompt_offers_exactly_what_the_router_maps():
    """The narrowest real failure: the model is offered a type in the prompt that
    TYPE_DIR has no directory for, so a valid completion has nowhere to be
    written."""
    text = (ROOT / "pipeline" / "absorb_runner.py").read_text()
    m = re.search(r"^type: (\w+(?:\|\w+)+)$", text, re.M)
    assert m, "allowed-types line not found in the SYSTEM prompt"
    assert set(m.group(1).split("|")) == set(vocab.TYPE_DIR)


def test_retired_types_resolve_to_a_successor_or_a_reason():
    assert vocab.resolve("system") == ("system", None)

    for old, new in (("module", "system"), ("tension", "conflict"),
                     ("gap", "unknown"), ("pattern", "decision"),
                     ("contract", "boundary"), ("ops", "runtime")):
        got, note = vocab.resolve(old)
        assert got == new, (old, got)
        assert new in note

    # Retired with no successor: git answers it, so the article should not exist.
    got, note = vocab.resolve("history")
    assert got is None and "no replacement" in note, (got, note)

    got, note = vocab.resolve("banana")
    assert got is None and "not a known type" in note, (got, note)


def test_every_retirement_target_is_a_real_type():
    for old, new in vocab.RETIRED.items():
        assert old not in vocab.TYPE_DIR, f"{old} is both live and retired"
        assert new == "" or new in vocab.TYPE_DIR, f"{old} -> unknown type {new}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print(f"vocab: all checks passed ({len(vocab.NAMES)} types)")
