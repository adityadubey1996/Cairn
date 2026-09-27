"""Confluence storage format (XHTML) to readable text.

Storage format is not HTML. A page carries `<ac:structured-macro>` plumbing
and `<ac:link>`/`<ri:page>` elements whose *attributes* hold the only useful
content, so a generic HTML-to-text pass drops every internal link target and
keeps every macro argument — exactly backwards. Headings, lists, tables, code
blocks and link targets survive here; macro arguments do not.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

MAX_STORAGE_BYTES = 5 * 1024 * 1024

_HEADINGS = {f"h{level}": level for level in range(1, 7)}
# Subtrees that are plumbing, not prose: macro arguments, emoticon glyphs,
# image placeholders and the date macro's machine-readable stamp.
_DISCARDED = {"ac:parameter", "ac:emoticon", "ac:image", "time"}
# A row of a bulleted or numbered list, or a table row: adjacent lines rather
# than separate paragraphs.
_TIGHT = re.compile(r"^(\s*([-*]|\d+\.)\s|\|)")
# A dropped inline macro (a date, a status lozenge) leaves the space it was
# written with, stranding the punctuation that followed it.
_ORPHANED_PUNCTUATION = re.compile(r"\s+([.,;:!?)\]])")


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._inline: list[str] = []
        self._lists: list[int | None] = []   # None bullets, int counts
        self._discard = 0
        self._code: list[str] | None = None
        self._anchor: tuple[int, str] | None = None
        self._heading = 0
        self._cells: list[str] | None = None
        self._table_head = False

    # --- accumulation -----------------------------------------------------
    def _take(self) -> str:
        text = " ".join("".join(self._inline).split())
        self._inline.clear()
        return _ORPHANED_PUNCTUATION.sub(r"\1", text)

    def _emit(self, prefix: str = "") -> None:
        if self._cells is not None:
            return  # inside a table cell: keep accumulating, the cell flushes it
        text = self._take()
        if text:
            self.blocks.append(prefix + text)

    def _close_anchor(self) -> None:
        if self._anchor is None:
            return
        start, target = self._anchor
        self._anchor = None
        label = "".join(self._inline[start:]).strip()
        del self._inline[start:]
        if label and target and label != target:
            self._inline.append(f"[{label}]({target})")
        else:
            self._inline.append(label or target)

    # --- parser callbacks -------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if self._discard:
            if tag in _DISCARDED:
                self._discard += 1
            return
        if tag in _DISCARDED:
            self._discard = 1
            return
        values = dict(attrs)
        if tag == "ac:plain-text-body":
            self._emit()
            self._code = []
        elif tag in _HEADINGS:
            self._emit()
            self._heading = _HEADINGS[tag]
        elif tag in ("p", "li", "br", "blockquote"):
            self._emit()
        elif tag in ("ul", "ol"):
            self._emit()
            self._lists.append(None if tag == "ul" else 1)
        elif tag == "table":
            self._emit()
            self._table_head = True
        elif tag == "tr":
            self._cells = []
        elif tag in ("td", "th"):
            self._take()
        elif tag == "a":
            self._anchor = (len(self._inline), values.get("href", ""))
        elif tag == "ac:link":
            self._anchor = (len(self._inline), "")
        elif tag in ("ri:page", "ri:attachment", "ri:url", "ri:user", "ri:blog-post"):
            target = (values.get("ri:content-title") or values.get("ri:filename")
                      or values.get("ri:value") or values.get("ri:account-id") or "")
            if self._anchor is not None:
                self._anchor = (self._anchor[0], target)
            elif target:
                self._inline.append(target)

    def handle_endtag(self, tag):
        if self._discard:
            if tag in _DISCARDED:
                self._discard -= 1
            return
        if tag == "ac:plain-text-body":
            body = "".join(self._code or []).strip("\n")
            self._code = None
            if body:
                self.blocks.append(f"```\n{body}\n```")
        elif tag in _HEADINGS:
            self._emit("#" * self._heading + " ")
            self._heading = 0
        elif tag in ("p", "blockquote"):
            self._emit()
        elif tag == "li":
            depth = max(len(self._lists), 1)
            counter = self._lists[-1] if self._lists else None
            if counter is None:
                marker = "- "
            else:
                marker = f"{counter}. "
                self._lists[-1] = counter + 1
            self._emit("  " * (depth - 1) + marker)
        elif tag in ("ul", "ol"):
            if self._lists:
                self._lists.pop()
        elif tag in ("td", "th"):
            if self._cells is not None:
                self._cells.append(self._take())
        elif tag == "tr":
            cells = self._cells or []
            self._cells = None
            if cells:
                self.blocks.append("| " + " | ".join(cells) + " |")
                if self._table_head:
                    self.blocks.append("| " + " | ".join("---" for _ in cells) + " |")
            self._table_head = False
        elif tag == "table":
            self._emit()
            self._table_head = False
        elif tag in ("a", "ac:link"):
            self._close_anchor()

    def handle_data(self, data):
        if self._discard:
            return
        if self._code is not None:
            self._code.append(data)
        else:
            self._inline.append(data)

    def unknown_decl(self, data):
        # `<![CDATA[...]]>` — how storage format carries code and link labels.
        if data.startswith("CDATA["):
            self.handle_data(data[len("CDATA["):])

    def close(self):
        super().close()
        self._close_anchor()
        self._emit()


def to_text(storage: str) -> str:
    """Storage-format XHTML to text. Raises on a body too large to extract."""
    if len(storage.encode()) > MAX_STORAGE_BYTES:
        raise ValueError("Confluence page body exceeds the 5 MB extraction limit")
    parser = _Parser()
    parser.feed(storage)
    parser.close()
    out = ""
    for block in parser.blocks:
        if not out:
            out = block
        elif _TIGHT.match(block) and _TIGHT.match(out.rsplit("\n", 1)[-1]):
            out += "\n" + block
        else:
            out += "\n\n" + block
    return out
