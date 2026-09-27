"""Atlassian Document Format (a JSON tree) rendered as readable text.

Jira descriptions and comments are ADF, not text or HTML, so this is the one
place an issue's prose is recovered. Headings, lists, code blocks and link
targets are kept because a wiki article cites them; presentation-only nodes
(colour, alignment, layout columns) are dropped.

Unknown node types fall through to their children rather than raising: ADF
gains node types over time, and an unrecognised wrapper must not cost the text
inside it.
"""
from __future__ import annotations

_LIST_MARKERS = {"bulletList": False, "orderedList": True}


def to_text(node: dict | None) -> str:
    """The whole document as text. Anything that is not an ADF node is empty."""
    if not isinstance(node, dict):
        return ""
    return _block(node).strip()


def _inline(nodes) -> str:
    return "".join(_inline_one(n) for n in nodes or [] if isinstance(n, dict))


def _inline_one(node: dict) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        text = node.get("text", "")
        for mark in node.get("marks") or []:
            if not isinstance(mark, dict):
                continue
            if mark.get("type") == "code":
                text = f"`{text}`"
            elif mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                if href:
                    text = f"[{text}]({href})"
        return text
    if kind == "hardBreak":
        return "\n"
    if kind == "mention":
        return attrs.get("text") or "@unknown"
    if kind == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""
    if kind in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url") or ""
    if kind == "date":
        return attrs.get("timestamp", "")
    if kind == "status":
        return f"[{attrs.get('text', '')}]"
    return _inline(node.get("content"))


def _blocks(nodes) -> list[str]:
    rendered = (_block(n) for n in nodes or [] if isinstance(n, dict))
    return [chunk for chunk in rendered if chunk.strip()]


def _block(node: dict) -> str:
    kind = node.get("type")
    content = node.get("content")
    attrs = node.get("attrs") or {}
    if kind == "heading":
        level = min(max(int(attrs.get("level", 1) or 1), 1), 6)
        return f"{'#' * level} {_inline(content)}"
    if kind in _LIST_MARKERS:
        return _list(content, ordered=_LIST_MARKERS[kind])
    if kind in ("taskList", "decisionList"):
        return "\n".join(f"- {_inline(item.get('content'))}" if item.get("type") == "decisionItem"
                         else f"- [{'x' if (item.get('attrs') or {}).get('state') == 'DONE' else ' '}] "
                              f"{_inline(item.get('content'))}"
                         for item in content or [] if isinstance(item, dict))
    if kind == "codeBlock":
        return f"```{attrs.get('language', '') or ''}\n{_inline(content)}\n```"
    if kind == "blockquote":
        return "\n".join(f"> {line}" if line else ">"
                         for line in "\n\n".join(_blocks(content)).split("\n"))
    if kind == "panel":
        return "\n\n".join(_blocks(content))
    if kind == "rule":
        return "---"
    if kind == "table":
        return _table(content)
    if kind in ("media", "mediaInline"):
        name = attrs.get("alt") or attrs.get("id") or "file"
        return f"[attachment] {name}"
    if kind in ("expand", "nestedExpand"):
        heading = [f"### {attrs['title']}"] if attrs.get("title") else []
        return "\n\n".join(heading + _blocks(content))
    if kind == "paragraph":
        return _inline(content)
    # doc, listItem, mediaSingle, mediaGroup, layout* and anything ADF adds later.
    return "\n\n".join(_blocks(content)) if content else _inline_one(node)


def _list(items, ordered: bool) -> str:
    """Nesting comes from the two-space continuation prefix, so a list inside a
    list item indents once per level without tracking depth."""
    lines = []
    for number, item in enumerate(items or [], 1):
        if not isinstance(item, dict):
            continue
        marker = f"{number}. " if ordered else "- "
        first, *rest = ("\n\n".join(_blocks(item.get("content"))) or "").split("\n")
        lines.append(marker + first)
        lines += [f"  {line}" if line else "" for line in rest]
    return "\n".join(lines)


def _table(rows) -> str:
    out = []
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        cells = [" ".join("\n\n".join(_blocks(cell.get("content"))).split())
                 for cell in row.get("content") or [] if isinstance(cell, dict)]
        out.append("| " + " | ".join(cells) + " |")
        if index == 0:
            out.append("|" + "|".join(" --- " for _ in cells) + "|")
    return "\n".join(out)
