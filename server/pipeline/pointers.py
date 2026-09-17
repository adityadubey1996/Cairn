"""A connector pointer: the small file under raw/inbox/ that names one unit.

The connector is the only party that knows the unit's origin, so the pointer
is the only place these fields come from. Ingest copies them; it never
infers them.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
REQUIRED = ("id", "path", "sha")


@dataclass
class Pointer:
    id: str
    path: str
    sha: str
    source_type: str = "doc"
    status: str = "active"
    project_id: str | None = None
    date: str | None = None
    time: str | None = None
    authors: list[str] = field(default_factory=list)
    source_url: str | None = None
    found_in: str | None = None
    publisher: list[str] = field(default_factory=list)


def parse_pointer(text: str) -> Pointer:
    m = FRONTMATTER.match(text)
    if not m:
        raise ValueError("pointer has no frontmatter")
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        raise ValueError(f"pointer lacks {missing}")

    def text_of(key: str) -> str | None:
        v = fields.get(key, "").strip().strip('"')
        return v or None

    def list_of(key: str) -> list[str]:
        raw = fields.get(key, "").strip()
        if not raw:
            return []
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as e:
            raise ValueError(f"malformed {key!r}: {raw!r}") from e
        if not isinstance(parsed, list):
            raise ValueError(f"{key!r} is not a list: {raw!r}")
        return [str(x) for x in parsed]

    return Pointer(
        id=fields["id"], path=fields["path"], sha=fields["sha"],
        source_type=text_of("source_type") or "doc",
        status=text_of("status") or "active",
        project_id=text_of("project_id"), date=text_of("date"), time=text_of("time"),
        authors=list_of("authors"), source_url=text_of("source_url"),
        found_in=text_of("found_in"), publisher=list_of("publisher"))
