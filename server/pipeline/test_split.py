#!/usr/bin/env python3
"""Self-check for structural splitting. Pure; no DB, no S3.

The invariant that matters: parts concatenate back to the body and none is
empty. Losing text here would silently drop evidence one stage before
anything reads it.

Run: python3 -m server.pipeline.test_split
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.pipeline.split import UNIT_BODY_CHARS, split_body  # noqa: E402

PAGED = "\f".join(f"Page {i} " + ("text. " * 80) for i in range(1, 11))  # 10 pages, ~500 chars each

SLIDES = "".join(f"<!-- Slide number: {i} -->\n# Title {i}\n" + ("bullet. " * 60) + "\n"
                 for i in range(1, 9))

ORDINANCE = ("CHAPTER 153: WIND ENERGY\n\n"
             + "153.01 Purpose\n" + ("purpose text. " * 60) + "\n\n"
             + "153.02 Definitions\n" + ("definition text. " * 60) + "\n\n"
             + "153.03 Public Participation\n" + ("participation text. " * 60) + "\n\n"
             + "153.04 Special Use\n" + ("special use text. " * 60) + "\n")

MARKDOWN = ("# Report\n" + ("intro. " * 50) + "\n\n"
            + "## Findings\n" + ("finding. " * 50) + "\n\n"
            + "## Method\n" + ("method. " * 50) + "\n")

CHAT = "".join(f"13:{m:02d} 107465432007070735434: " + ("said something. " * 30) + "\n"
               for m in range(12))

MEETING_NOTES = ("# **📝 Notes**\n\nAug 7, 2026\n\n## **Team Sync**\n" + "discussion. " * 60
                  + "\n\n## **Next Steps**\n" + "action items. " * 60)

WALL = ("wall of text with no markers at all. " * 200)

TRAILING_BLANK = ("no paragraph breaks until the very end. " * 40) + "\n\n"


def _check_invariant(body, parts):
    assert "".join(p.text for p in parts) == body
    assert all(p.text for p in parts), [len(p.text) for p in parts]
    assert [p.n for p in parts] == list(range(1, len(parts) + 1)), [p.n for p in parts]
    assert all(p.total == len(parts) for p in parts)


def test_short_body_is_one_part():
    parts = split_body("tiny", "doc", 1000)
    assert len(parts) == 1 and parts[0].anchor == {} and not parts[0].oversized, parts


def test_empty_body_returns_no_parts():
    assert split_body("", "doc", 900) == []


def test_no_text_is_ever_lost_and_no_part_is_empty():
    for body, kind in ((PAGED, "binary_doc"), (SLIDES, "deck"), (ORDINANCE, "binary_doc"),
                       (MARKDOWN, "doc"), (CHAT, "chat_thread"), (WALL, "doc"),
                       (TRAILING_BLANK, "doc")):
        _check_invariant(body, split_body(body, kind, 900))


def test_pdf_pages_pack_whole_pages_and_carry_page_ranges():
    parts = split_body(PAGED, "binary_doc", 1100)  # two ~500-char pages fit, three do not
    assert len(parts) == 5, [p.anchor for p in parts]
    assert parts[0].anchor == {"pages": "1-2"}, parts[0].anchor
    assert parts[-1].anchor == {"pages": "9-10"}, parts[-1].anchor
    assert all("\f" not in p.text[:1] for p in parts[1:]), "a part must not start with a page break"


def test_slides_carry_slide_numbers():
    parts = split_body(SLIDES, "deck", 1100)
    assert len(parts) > 1, len(parts)
    assert parts[0].anchor == {"slide": "1-2"}, parts[0].anchor
    assert all("slide" in p.anchor for p in parts), [p.anchor for p in parts]


def test_numbered_sections_carry_section_labels():
    parts = split_body(ORDINANCE, "binary_doc", 900)
    labels = [p.anchor.get("section") for p in parts]
    assert any(lab and lab.startswith("153.02") for lab in labels), labels


def test_markdown_headings_carry_section_labels():
    parts = split_body(MARKDOWN, "doc", 600)
    labels = [p.anchor.get("section") for p in parts]
    assert "Findings" in labels, labels


def test_chat_splits_on_speaker_turns_and_carries_the_first_time():
    parts = split_body(CHAT, "chat_thread", 900)
    assert len(parts) > 1, len(parts)
    for p in parts:
        assert p.text.startswith("13:"), p.text[:20]
        assert p.anchor == {"time": p.text[:5]}, p.anchor


def test_meeting_transcript_falls_back_to_headings_when_there_are_no_speaker_turns():
    parts = split_body(MEETING_NOTES, "meeting_transcript", 900)
    assert len(parts) > 1, len(parts)
    assert all(p.anchor.get("section") for p in parts), [p.anchor for p in parts]


def test_structureless_wall_is_kept_whole_and_flagged():
    parts = split_body(WALL, "doc", 900)
    assert len(parts) == 1 and parts[0].oversized, parts[0].oversized


def test_trailing_blank_line_does_not_yield_an_empty_part():
    parts = split_body(TRAILING_BLANK, "doc", 900)
    assert len(parts) == 1 and parts[0].text == TRAILING_BLANK, [len(p.text) for p in parts]


def test_default_limit_is_the_shared_constant():
    assert UNIT_BODY_CHARS == 12_000, UNIT_BODY_CHARS
    parts = split_body("x" * 12_000, "doc")
    assert len(parts) == 1 and not parts[0].oversized, parts


def test_split_is_deterministic():
    assert split_body(ORDINANCE, "binary_doc", 900) == split_body(ORDINANCE, "binary_doc", 900)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all split checks passed")
