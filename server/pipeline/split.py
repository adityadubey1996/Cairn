"""Subdivide a source body on structure already in the text.

Structural only, never semantic. The boundary order is the spec's: page
breaks, slide markers, headings, numbered sections, speaker turns, then
paragraphs as the fallback. A part is never allowed to drop text, and a body
with no boundary at all is kept whole and flagged rather than cut.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# How much of a unit one downstream model call will read. Ingest splits to fit
# it; extraction and compile assume it. One constant, one owner.
UNIT_BODY_CHARS = 12_000

PAGE = re.compile(r"\f")
SLIDE = re.compile(r"^(?:<!-- Slide number: (\d+) -->|#{1,6}[ \t]+Slide[ \t]+(\d+)\b.*)$", re.M)
HEADING = re.compile(r"^#{1,6}[ \t]+(\S.*)$", re.M)
NUMBERED = re.compile(
    r"^[ \t]{0,3}((?:\d+\.\d+(?:\.\d+)?|§[ \t]*\d+|ARTICLE[ \t]+[IVXLC]+)[ \t]+\S.*)$", re.M)
SPEAKER = re.compile(r"^(\d{2}:\d{2}) .+?:[ \t]", re.M)
PARAGRAPH = re.compile(r"\n[ \t]*\n")

PATTERNS = {"page": PAGE, "slide": SLIDE, "heading": HEADING,
            "numbered": NUMBERED, "speaker": SPEAKER}

# Tried in order; the first that finds a boundary wins.
BOUNDARIES: dict[str, tuple[str, ...]] = {
    "binary_doc": ("page", "slide", "heading", "numbered"),
    "deck": ("slide", "page", "heading"),
    "doc": ("heading", "numbered"),
    "external_article": ("heading",),
    "chat_thread": ("speaker",),
    "meeting_transcript": ("speaker", "heading"),
}


@dataclass
class Part:
    n: int
    total: int
    text: str
    anchor: dict = field(default_factory=dict)
    oversized: bool = False


def _boundary(body: str, kind: str) -> tuple[str, list[int]]:
    """(boundary name, sorted offsets where a new section starts, always including 0)."""
    for name in BOUNDARIES.get(kind, ()):
        pat = PATTERNS[name]
        if name == "page":
            starts = [m.end() for m in pat.finditer(body)]
            if not starts:
                continue
        else:
            starts = [m.start() for m in pat.finditer(body)]
            if len(starts) < 2:
                continue
        return name, sorted({0, *starts})
    return "paragraph", sorted({0, *(m.end() for m in PARAGRAPH.finditer(body))})


def _pack(length: int, starts: list[int], limit: int) -> list[tuple[int, int]]:
    """Accumulate whole sections until the next would exceed the limit."""
    bounds = [s for s in starts if s < length] + [length]
    spans: list[tuple[int, int]] = []
    start = 0
    for i in range(1, len(bounds)):
        if bounds[i] - start > limit and bounds[i - 1] > start:
            spans.append((start, bounds[i - 1]))
            start = bounds[i - 1]
    spans.append((start, length))
    return spans


def _anchor(body: str, name: str, start: int, end: int) -> dict:
    if name == "page":
        first = body.count("\f", 0, start) + 1
        last = body.count("\f", 0, max(start, end - 1)) + 1
        return {"pages": f"{first}-{last}" if last > first else str(first)}
    if name == "slide":
        nums = [int(m.group(1) or m.group(2)) for m in SLIDE.finditer(body, start, end)]
        if not nums:
            return {}
        return {"slide": f"{nums[0]}-{nums[-1]}" if len(nums) > 1 else str(nums[0])}
    if name in ("heading", "numbered"):
        m = PATTERNS[name].search(body, start, end)
        return {"section": m.group(1).strip()} if m and m.start() == start else {}
    if name == "speaker":
        m = SPEAKER.search(body, start, end)
        return {"time": m.group(1)} if m and m.start() == start else {}
    return {}


def split_body(body: str, kind: str, limit: int = UNIT_BODY_CHARS) -> list[Part]:
    if not body:
        return []
    if len(body) <= limit:
        return [Part(1, 1, body)]
    name, starts = _boundary(body, kind)
    pieces: list[tuple[int, int, dict, bool]] = []
    for start, end in _pack(len(body), starts, limit):
        anchor = _anchor(body, name, start, end)
        if end - start <= limit:
            pieces.append((start, end, anchor, False))
            continue
        # One section larger than the cap: re-pack it on paragraph breaks.
        inner = body[start:end]
        para = sorted({0, *(m.end() for m in PARAGRAPH.finditer(inner))})
        for s, e in _pack(len(inner), para, limit):
            pieces.append((start + s, start + e, anchor if s == 0 else {}, e - s > limit))
    total = len(pieces)
    return [Part(i, total, body[s:e], a, big)
            for i, (s, e, a, big) in enumerate(pieces, 1)]
