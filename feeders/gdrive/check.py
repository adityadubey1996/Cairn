"""Offline self-checks for the gdrive feeder. Run from ai-brain root:
.venv/bin/python -m feeders.gdrive.check   (no network, no credentials)"""
from __future__ import annotations

import io
import tempfile
import urllib.parse
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from feeders.gdrive import sync


def check_list_owned() -> None:
    """Default path: everything owned, paginated, PLUS transcript-shaped files
    shared by someone else — merged and de-duped by id."""
    owned_pages = [
        {"files": [
            {"id": "f1", "name": "Weekly sync — Transcript",
             "mimeType": "application/vnd.google-apps.document",
             "modifiedTime": "2026-08-05T10:00:00.000Z",
             "owners": [{"displayName": "Aditya"}]},
            {"id": "fx", "name": "budget.xlsx", "mimeType":
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
             "modifiedTime": "2026-08-06T12:00:00.000Z"}],  # not text — skipped
         "nextPageToken": "p2"},
        {"files": [
            {"id": "fp", "name": "Q3 board deck.pdf", "mimeType": "application/pdf",
             "modifiedTime": "2026-08-06T12:00:00.000Z"},  # shared drives omit owners
            {"id": "fs", "name": "Secret salaries.pdf", "mimeType": "application/pdf",
             "modifiedTime": "2026-08-06T12:00:00.000Z"}]},
    ]
    shared_pages = [
        {"files": [
            {"id": "f1", "name": "Weekly sync — Transcript",  # already owned — deduped
             "mimeType": "application/vnd.google-apps.document",
             "modifiedTime": "2026-08-05T10:00:00.000Z",
             "owners": [{"displayName": "Aditya"}]},
            {"id": "f9", "name": "Meeting started 2026/08/06 - Notes by Gemini",
             "mimeType": "application/vnd.google-apps.document",
             "modifiedTime": "2026-08-06T12:00:00.000Z",
             "owners": [{"displayName": "Teammate"}]}]},
    ]
    calls = owned_pages + shared_pages
    urls = []
    sync._get_json = lambda url: (urls.append(url), calls.pop(0))[1]
    sync.config.GDRIVE_SOURCE_IDS = []
    sync.config.GDRIVE_EXCLUDE = ["salaries"]
    files = sync.list_drive_files(modified_after="2026-08-01T00:00:00Z")
    assert [f["id"] for f in files] == ["f1", "fp", "f9"], files
    assert files[0]["authors"] == ["Aditya"] and files[1]["authors"] == []
    assert files[2]["authors"] == ["Teammate"]
    q0 = urllib.parse.unquote_plus(urls[0])
    assert "'me' in owners" in q0, q0
    assert "modifiedTime > '2026-08-01T00:00:00Z'" in q0, q0
    assert "pageToken=p2" in urls[1], urls
    q2 = urllib.parse.unquote_plus(urls[2])
    assert "'me' in owners" not in q2, q2
    assert "notes by gemini" in q2.lower(), q2


def check_list_source_ids() -> None:
    """A configured id may be a folder (walked, subfolders included) or a single
    file (taken directly)."""
    responses = {
        "files/FOLDER": {"id": "FOLDER", "name": "Meet Recordings",
                         "mimeType": sync._FOLDER_MIME,
                         "modifiedTime": "2026-08-01T00:00:00.000Z"},
        "files/ONEDOC": {"id": "ONEDOC", "name": "Pricing model",
                         "mimeType": "application/vnd.google-apps.document",
                         "modifiedTime": "2026-08-02T00:00:00.000Z",
                         "owners": [{"displayName": "Sam"}]},
    }
    listings = [
        {"files": [{"id": "sub", "name": "2026-08-05 standup",
                    "mimeType": sync._FOLDER_MIME,
                    "modifiedTime": "2026-08-05T10:00:00.000Z"}]},
        {"files": [{"id": "f9", "name": "standup.vtt", "mimeType": "text/vtt",
                    "modifiedTime": "2026-08-05T10:00:00.000Z"}]},
    ]
    urls = []

    def fake_get_json(url):
        urls.append(url)
        for key, meta in responses.items():
            if key in url:
                return meta
        return listings.pop(0)

    sync._get_json = fake_get_json
    sync.config.GDRIVE_SOURCE_IDS = ["FOLDER", "ONEDOC"]
    sync.config.GDRIVE_EXCLUDE = []
    files = sync.list_drive_files()
    assert [f["id"] for f in files] == ["f9", "ONEDOC"], files
    assert files[1]["authors"] == ["Sam"]
    assert "'sub' in parents" in urllib.parse.unquote_plus(urls[2]), urls


def check_inventory() -> None:
    sync.config.GDRIVE_SOURCE_IDS = []
    sync.config.GDRIVE_EXCLUDE = []
    sync._get_json = lambda url: {"files": [
        {"id": "a", "name": "Kickoff — Transcript",
         "mimeType": "application/vnd.google-apps.document",
         "modifiedTime": "2026-08-05T10:00:00.000Z",
         "owners": [{"displayName": "Aditya"}]},
        {"id": "b", "name": "deck.pdf", "mimeType": "application/pdf",
         "modifiedTime": "2026-08-05T10:00:00.000Z",
         "owners": [{"displayName": "Aditya"}]}]}
    inv = sync.inventory()
    assert inv["total"] == 2, inv
    assert inv["by_kind"] == {"meeting_transcript": 1, "binary_doc": 1}, inv
    assert inv["by_owner"] == {"Aditya": 2}, inv



VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Aditya: welcome everyone

2
00:00:03.000 --> 00:00:05.000
Aditya: welcome everyone

3
00:00:05.500 --> 00:00:09.000
Sam: thanks, let's start
"""


def check_captions_to_prose() -> None:
    assert sync._captions_to_prose(VTT) == \
        "Aditya: welcome everyone\nSam: thanks, let's start"
    assert sync._captions_to_prose("") == ""


def _pdf(text: str | None) -> bytes:
    """One-page PDF. text=None gives a page with no text layer — what a
    scanned/photographed PDF looks like to an extractor."""
    w = PdfWriter()
    page = w.add_blank_page(width=200, height=200)
    if text is not None:
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode())
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Contents")] = w._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def check_pdf_to_text() -> None:
    assert sync._pdf_to_text(_pdf("Quarterly report")) == "Quarterly report"
    assert sync._pdf_to_text(_pdf(None)) == ""  # run() skips these


def check_source_type() -> None:
    def st(name, mime="application/octet-stream"):
        return sync._source_type({"name": name, "mime_type": mime})

    # shares ingest.py's kind vocabulary — see _source_type's docstring
    assert st("Q3 board deck.pdf", "application/pdf") == "binary_doc"
    assert st("Weekly sync — Transcript",
              "application/vnd.google-apps.document") == "meeting_transcript"
    assert st("standup.vtt", "text/vtt") == "meeting_transcript"
    assert st("Pricing model", "application/vnd.google-apps.document") == "doc"
    assert st("notes.txt", "text/plain") == "doc"
    # the real Drive naming that the "transcript"-only match used to miss
    assert st("Meeting started 2026/06/29 20:25 IST - Notes by Gemini",
              "application/vnd.google-apps.document") == "meeting_transcript"


def check_source_slug() -> None:
    same_name = {"name": "UI_SCREENS_SPEC.html.pdf", "id": "1AbCdEfGhIjKlMnOpQ"}
    twin = {"name": "UI_SCREENS_SPEC.html.pdf", "id": "1ZzYyXxWwVvUuTtSsR"}
    assert sync._source_slug(same_name) != sync._source_slug(twin), "paths collide"
    assert sync._source_slug(same_name).startswith("ui-screens-spec-html-pdf-")
    assert sync._source_slug(same_name) == sync._source_slug(dict(same_name)), \
        "must be stable across runs"
    long_name = {"name": "x" * 200, "id": "1AbCdEfGhIjKlMnOpQ"}
    assert len(sync._source_slug(long_name)) <= 67, sync._source_slug(long_name)


def check_title() -> None:
    doc = {"name": "compass_artifact_wf-982542dd-3db6-43d7.md"}
    assert sync._title(doc, "# Carbon Registry Landscape\n\nbody") == \
        "Carbon Registry Landscape"
    # no H1 to borrow — fall back to the name, minus the extension
    assert sync._title(doc, "plain body") == "compass_artifact_wf-982542dd-3db6-43d7"
    assert sync._title({"name": "Strategic Context"}, "body") == "Strategic Context"


def check_export_text() -> None:
    calls = []

    def fake_get_bytes(url):
        calls.append(url)
        if "/f3?" in url:
            return _pdf("Quarterly report")
        return (b"WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nAditya: hi\n"
                if "alt=media" in url else b"# Doc body\n")

    sync._get_bytes = fake_get_bytes
    doc = sync.export_text({"id": "f1", "name": "Weekly sync — Transcript",
                            "mime_type": "application/vnd.google-apps.document"})
    assert doc == "# Doc body\n", doc
    assert "/files/f1/export?mimeType=text%2Fmarkdown" in calls[0], calls
    vtt = sync.export_text({"id": "f2", "name": "standup.vtt",
                            "mime_type": "text/vtt"})
    assert vtt == "Aditya: hi", vtt
    assert "/files/f2?alt=media" in calls[1], calls
    pdf = sync.export_text({"id": "f3", "name": "Q3 board deck.pdf",
                            "mime_type": "application/pdf"})
    assert pdf == "Quarterly report", pdf
    assert "/files/f3?alt=media" in calls[2], calls


def check_one_bad_file_does_not_abort_the_batch() -> None:
    calls = []
    def fake_export_text(f):
        if f["id"] == "bad":
            raise RuntimeError("boom")
        calls.append(f["id"])
        return "# Doc\nreal content"
    with tempfile.TemporaryDirectory() as d:
        sync.config.SOURCES_DIR = Path(d) / "sources"
        sync.config.GDRIVE_TARGET_REPO = Path(d)
        sync.list_drive_files = lambda modified_after: [
            {"id": "bad", "name": "bad.txt", "mime_type": "text/plain",
             "modified_time": "2026-01-01T00:00:00Z", "authors": []},
            {"id": "good", "name": "good.txt", "mime_type": "text/plain",
             "modified_time": "2026-01-01T00:00:00Z", "authors": []},
        ]
        sync.export_text = fake_export_text
        seen, written = sync.run()
    assert "good" in calls, calls
    assert written == 1, written


def check_authors_survive_the_frontmatter_quote_strip() -> None:
    """Authors with apostrophes must survive the frontmatter parser's quote stripping."""
    import json as _json
    authors = ["O'Brien", "Jane Doe"]
    line = f"authors: [{', '.join(_json.dumps(a) for a in authors)}]"
    # Mirror ingest.py's own parser exactly.
    parsed = [x.strip().strip('"') for x in
             line.removeprefix("authors: ").strip("[]").split(", ")]
    assert parsed == ["O'Brien", "Jane Doe"], parsed


if __name__ == "__main__":
    check_list_owned()
    check_list_source_ids()
    check_inventory()
    check_captions_to_prose()
    check_pdf_to_text()
    check_source_type()
    check_source_slug()
    check_title()
    check_export_text()
    check_one_bad_file_does_not_abort_the_batch()
    check_authors_survive_the_frontmatter_quote_strip()
    print("gdrive checks OK")
