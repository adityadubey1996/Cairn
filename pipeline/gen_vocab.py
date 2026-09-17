#!/usr/bin/env python3
"""Write the article vocabulary into the sites that cannot import vocab.py.

Python consumers import TYPE_DIR / TYPE_ORDER from vocab.py directly. Three
sites cannot:

  SKILL.md          the frontmatter enum inside a fenced example
  absorb_runner.py  the allowed-types line inside the SYSTEM prompt string
  ArticleList.jsx   TYPE_COLOR, which is JavaScript

Each is rewritten between markers, so the surrounding prose is never touched.
Run after editing vocab.py; run --check in CI.

    python3 gen_vocab.py
    python3 gen_vocab.py --check

--check exits 1 on drift and prints which site is stale. That is the whole point:
the previous four hand-maintained copies disagreed and nothing noticed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from vocab import NAMES, TYPES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def skill_enum() -> str:
    return "type: " + " | ".join(NAMES)


def prompt_enum() -> str:
    return "type: " + "|".join(NAMES)


def type_color() -> str:
    """JS object literal, two entries per line to stay inside a sane line length."""
    pairs = [f"{t.name}: '{t.colour}'" for t in TYPES]
    rows = ["  " + ", ".join(pairs[i:i + 3]) + "," for i in range(0, len(pairs), 3)]
    return "export const TYPE_COLOR = {\n" + "\n".join(rows) + "\n}"


def replace_line(text: str, pattern: str, new: str) -> tuple[str, bool]:
    """Swap the single line matching `pattern`. Returns (text, changed)."""
    rx = re.compile(pattern, re.M)
    if not rx.search(text):
        raise SystemExit(f"anchor not found: {pattern!r} — did the file move?")
    out = rx.sub(lambda _: new, text, count=1)
    return out, out != text


def replace_block(text: str, start: str, new: str) -> tuple[str, bool]:
    """Swap a brace-delimited block beginning with `start` through its closing }."""
    i = text.find(start)
    if i == -1:
        raise SystemExit(f"anchor not found: {start!r} — did the file move?")
    j = text.find("\n}", i)
    if j == -1:
        raise SystemExit(f"unterminated block after {start!r}")
    out = text[:i] + new + text[j + 2:]
    return out, out != text


def targets() -> list[tuple[Path, callable]]:
    return [
        (ROOT / "pipeline" / "SKILL.md",
         lambda t: replace_line(t, r"^type: \w+(?: \| \w+)+$", skill_enum())),
        (ROOT / "pipeline" / "absorb_runner.py",
         lambda t: replace_line(t, r"^type: \w+(?:\|\w+)+$", prompt_enum())),
        (ROOT / "web2" / "src" / "screens" / "wiki" / "ArticleList.jsx",
         lambda t: replace_block(t, "export const TYPE_COLOR = {", type_color())),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit 1 if any site is stale")
    a = ap.parse_args()

    stale = []
    for path, transform in targets():
        if not path.is_file():
            print(f"missing: {path}", file=sys.stderr)
            return 2
        before = path.read_text(encoding="utf-8")
        after, changed = transform(before)
        rel = path.relative_to(ROOT)
        if a.check:
            print(f"  {'STALE' if changed else 'ok   '}  {rel}")
            if changed:
                stale.append(rel)
        else:
            if changed:
                path.write_text(after, encoding="utf-8")
            print(f"  {'wrote' if changed else 'ok   '}  {rel}")

    print(f"\n{len(NAMES)} types: {', '.join(NAMES)}")
    if stale:
        print(f"\n{len(stale)} site(s) stale — run: python3 gen_vocab.py",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
