"""The article type vocabulary. One definition, five consumers.

The list used to be typed out by hand in five places — SKILL.md's frontmatter
enum, TYPE_DIR, the SYSTEM prompt's allowed-types line, build_index's TYPE_ORDER,
and Wiki.jsx's TYPE_COLOR — and they drifted: the enum admitted 9 types, TYPE_DIR
mapped 12 (with `package`, without `ops`), TYPE_COLOR coloured 12 (with `ops`,
without `package`). An article could be typed into a folder the runner had no
route for, and 20 articles typed `service` sat in three different directories.

Python consumers import from here. The two that cannot — a markdown file and a
JSX file — are written by gen_vocab.py, which also has a --check mode for CI.

Changing the vocabulary means editing this file and running:

    python3 gen_vocab.py            # rewrite the generated sites
    python3 gen_vocab.py --check    # verify they match; exit 1 if not

TYPES below is the definition of record: names, colours, caps and length
targets. Keep the
`checkable` line here in sync with that table — it is what the writer is held to.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArticleType:
    name: str        # the `type:` value in frontmatter
    dir: str         # the directory under wiki/
    section: str     # the heading in _index.md
    colour: str      # node colour in the Wiki graph view
    cap: str         # how many of these should exist
    lines: str       # length target
    checkable: str   # what would prove an article of this type wrong


# Order is deliberate: it is the section order of _index.md, running from what
# the system is, through how it behaves, to why it is that way and what is
# unresolved. Do not sort alphabetically.
TYPES: tuple[ArticleType, ...] = (
    ArticleType("system", "systems", "Systems", "#7ee2a8",
                "1 per deployable", "60-100",
                "entrypoint or port cited at path@sha"),
    ArticleType("domain", "domain", "Domain", "#f0c98c",
                "10-20", "30-60",
                "mandatory one-line definition"),
    ArticleType("flow", "flows", "Flows", "#e8b46e",
                "5-15", "60-120",
                "every hop cites a real path"),
    ArticleType("boundary", "boundaries", "Boundaries", "#c9d16e",
                "5-15", "30-70",
                "route, table or schema exists at sha"),
    ArticleType("runtime", "runtime", "Runtime", "#b5f08c",
                "3-8", "30-70",
                "cites a runtime_contract unit"),
    ArticleType("decision", "decisions", "Decisions", "#8cf0c9",
                "unbounded", "40-70",
                "dated, options named, cites commit / doc / transcript"),
    ArticleType("conflict", "conflicts", "Conflicts", "#d98cf0",
                "few", "40-70",
                "both sides cited, neither resolved"),
    ArticleType("unknown", "unknowns", "Unknowns", "#e07a7a",
                "unbounded", "15-30",
                "names the unread source"),
    ArticleType("idea", "ideas", "Ideas", "#8ce0f0",
                "unbounded", "15-30",
                "the proposal event is cited; merit is never asserted"),
    ArticleType("outcome", "outcomes", "Outcomes", "#a0a8f0",
                "unbounded", "25-50",
                "links a decision and cites evidence dated after it"),
)

NAMES: tuple[str, ...] = tuple(t.name for t in TYPES)
TYPE_DIR: dict[str, str] = {t.name: t.dir for t in TYPES}
TYPE_ORDER: tuple[tuple[str, str], ...] = tuple((t.name, t.section) for t in TYPES)
DIR_TYPE: dict[str, str] = {t.dir: t.name for t in TYPES}

# Retired types, kept so a stale article gets a real error instead of landing in
# Unclassified with no explanation of where it should go.
RETIRED: dict[str, str] = {
    "module": "system", "package": "system", "service": "system",
    "entity": "domain", "workflow": "flow",
    "contract": "boundary", "interface": "boundary",
    "integration": "boundary", "dataset": "boundary",
    "ops": "runtime", "runtimes": "runtime",
    "tension": "conflict", "gap": "unknown", "pattern": "decision",
    "history": "", "person": "", "people": "", "incident": "",
}


def resolve(name: str) -> tuple[str | None, str | None]:
    """(canonical_type, note). Type is None when the article should not exist.

    A retired type is not a typo — it is an article written under the previous
    vocabulary, and the caller needs to know whether it moves or goes away.
    """
    if name in TYPE_DIR:
        return name, None
    if name in RETIRED:
        target = RETIRED[name]
        if target:
            return target, f"`{name}` is retired; use `{target}`"
        return None, (f"`{name}` is retired with no replacement — "
                      "git answers it, or there is no material for it")
    return None, f"`{name}` is not a known type"


if __name__ == "__main__":
    w = max(len(t.name) for t in TYPES)
    for t in TYPES:
        print(f"{t.name:<{w}}  {t.dir + '/':<13} {t.cap:<17} {t.lines:<7} {t.checkable}")
