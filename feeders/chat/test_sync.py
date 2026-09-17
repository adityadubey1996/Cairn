#!/usr/bin/env python3
"""Self-check for the Chat feeder's message-content rules.
Run: python3 feeders/chat/test_sync.py

No network and no Postgres: what is under test is the decision about what
counts as content, and how a message renders — the two places where 141
attachments and 74 whole messages per space were being dropped silently.
"""
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from feeders.chat import sync  # noqa: E402

SPACE = {"name": "spaces/X", "displayName": "Dev-Group"}


def _msg(**kw):
    return {"createTime": "2026-08-12T10:35:00Z",
            "sender": {"displayName": "Aditya"}, **kw}


def test_text_only_message_is_content():
    assert sync.carries_content(_msg(text="hello"))


def test_attachment_only_message_is_content():
    """The regression that lost 68 messages in one space: no text key at all."""
    m = _msg(attachment=[{"contentName": "q3.pdf", "contentType": "application/pdf"}])
    assert "text" not in m
    assert sync.carries_content(m), "a PDF with no caption is still content"


def test_rich_link_only_message_is_content():
    m = _msg(annotations=[{"richLinkMetadata": {"uri": "https://example.com/x"}}])
    assert sync.carries_content(m)


def test_genuinely_empty_message_is_not_content():
    assert not sync.carries_content(_msg(text="   "))
    assert not sync.carries_content(_msg())


def test_rich_links_reads_the_uri_out_of_annotations():
    m = _msg(annotations=[
        {"richLinkMetadata": {"uri": "https://drive.google.com/open?id=abc"}},
        {"userMention": {"type": "MENTION"}},           # not a link
    ])
    assert sync.rich_links(m) == ["https://drive.google.com/open?id=abc"]


def test_render_names_the_attachment():
    """Naming it is what lets a reader — and absorb — know a file was shared,
    whether or not its bytes could be fetched."""
    m = _msg(text="have a look",
             attachment=[{"contentName": "Addressing_Uncertainty.pdf",
                          "contentType": "application/pdf"}])
    out = sync._render_day(SPACE, "2026-08-12", [m])
    assert "have a look" in out
    assert "[attachment] Addressing_Uncertainty.pdf (application/pdf)" in out


def test_render_survives_a_message_with_no_text_key():
    """m['text'] used to be a bare index — a KeyError here killed the whole
    space-day, not just the message."""
    m = _msg(attachment=[{"contentName": "shot.png", "contentType": "image/png"}])
    out = sync._render_day(SPACE, "2026-08-12", [m])
    assert "[attachment] shot.png (image/png)" in out


def test_render_names_the_rich_link():
    m = _msg(annotations=[{"richLinkMetadata": {"uri": "https://example.com/paper"}}])
    out = sync._render_day(SPACE, "2026-08-12", [m])
    assert "[link] https://example.com/paper" in out


def test_attachment_id_separates_same_named_files():
    """"image.png" is posted dozens of times in a busy space and every one is
    a different file — the id must key on the ref, not the name."""
    a = sync.attachment_id("SPACE1", "image.png", "refA")
    b = sync.attachment_id("SPACE1", "image.png", "refB")
    assert a != b
    assert a == sync.attachment_id("SPACE1", "image.png", "refA")


def test_attachment_id_separates_spaces():
    assert (sync.attachment_id("SPACE1", "image.png", "ref")
            != sync.attachment_id("SPACE2", "image.png", "ref"))


def test_attachment_ref_prefers_media_then_drive():
    assert sync._attachment_ref({"attachmentDataRef": {"resourceName": "R"}}) == "R"
    assert sync._attachment_ref({"driveDataRef": {"driveFileId": "D"}}) == "D"
    assert sync._attachment_ref({"name": "spaces/x/.../a"}) == "spaces/x/.../a"


def test_extract_handles_html_pdf_image_and_unknown():
    """The four outcomes an attachment can have. The last one matters most:
    an unreadable type is KEPT, not failed — dropping it is how .zip and
    .html went missing."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)

        kind, body = sync._extract("report.html", b"<html><body><p>Hi there</p></body></html>", tmp)
        assert kind == "external_article" and "Hi there" in body

        kind, body = sync._extract("shot.png", b"\x89PNG\r\n", tmp)
        assert kind == "image" and "shot.png" in body

        kind, body = sync._extract("notes.txt", b"plain text", tmp)
        assert kind == "doc" and body == "plain text"

        kind, body = sync._extract("logos.zip", b"PK\x03\x04", tmp)
        assert kind == "attachment" and "logos.zip" in body, "unknown types are kept, not dropped"


def test_extract_keeps_a_file_with_no_extension():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        kind, body = sync._extract("Subpart-C", b"\x00", pathlib.Path(d))
    assert kind == "attachment"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("gchat sync: all checks passed")
