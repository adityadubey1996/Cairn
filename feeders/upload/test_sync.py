#!/usr/bin/env python3
"""Self-check for the upload feeder's pure naming/classification logic.
Run: python3 feeders/upload/test_sync.py

No network, no Postgres: what's under test here is content-free — given a
relative path or an extension, what id/slug/classification does it get.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from feeders.upload import sync as upload  # noqa: E402


def test_upload_id_is_stable_for_the_same_path():
    assert upload.upload_id("proj-1", "reports/q3.pdf") == upload.upload_id("proj-1", "reports/q3.pdf")


def test_upload_id_differs_for_different_paths():
    assert upload.upload_id("proj-1", "reports/q3.pdf") != upload.upload_id("proj-1", "reports/q4.pdf")


def test_upload_id_differs_across_projects_for_the_same_path():
    assert upload.upload_id("proj-1", "report.pdf") != upload.upload_id("proj-2", "report.pdf")


def test_classify_text():
    assert upload.classify_upload(".md") == "text"
    assert upload.classify_upload(".TXT") == "text"


def test_classify_binary():
    assert upload.classify_upload(".docx") == "binary"
    assert upload.classify_upload(".pdf") == "binary"


def test_classify_data():
    assert upload.classify_upload(".csv") == "data"
    assert upload.classify_upload(".json") == "data"


def test_classify_unsupported():
    assert upload.classify_upload(".exe") == "unsupported"
    assert upload.classify_upload("") == "unsupported"


def test_dispatch_extract_reads_text_verbatim():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "notes.md"
        p.write_text("# Hello\n\nBody.", encoding="utf-8")
        source_type, payload = upload._dispatch_extract(p)
        assert source_type == "doc"
        assert payload == "# Hello\n\nBody."


def test_dispatch_extract_keeps_data_files_as_raw_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "table.csv"
        p.write_bytes(b"a,b\n1,2\n")
        source_type, payload = upload._dispatch_extract(p)
        assert source_type == "dataset"
        assert payload == b"a,b\n1,2\n"


def test_dispatch_extract_rejects_unsupported_extensions():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "app.exe"
        p.write_bytes(b"\x00\x01")
        try:
            upload._dispatch_extract(p)
        except ValueError:
            return
    raise AssertionError("an unsupported extension must raise, not silently pass through")


def test_dispatch_extract_rejects_a_binary_file_with_no_text_layer():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "empty.pdf"
        p.write_bytes(b"%PDF-1.4\n%%EOF")  # not a real PDF; extract_binary must find no text
        with patch.object(upload, "extract_binary", return_value=("", "NO_EXTRACTOR")):
            try:
                upload._dispatch_extract(p)
            except ValueError:
                return
    raise AssertionError("empty extracted text must raise, not write a blank article")


def test_write_entry_writes_source_and_calls_record():
    calls = []
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record",
                      side_effect=lambda **kw: calls.append(kw)):
        target_repo = Path(tmp)
        written = upload.write_entry(target_repo, "notes/hello.md", "doc",
                                     "# Hello", project_id="proj-1", connection_id="upload-x")
        assert written is True
        assert calls and calls[0]["id"] == upload.upload_id("proj-1", "notes/hello.md")
        assert calls[0]["folder"] == "notes"
        assert calls[0]["connection_id"] == "upload-x"
        assert (target_repo / "sources" / "upload" / f"{upload.upload_slug('proj-1', 'notes/hello.md')}.md").is_file()


def test_unchanged_upload_preserves_file_and_repairs_index():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record") as record:
        target_repo = Path(tmp)
        upload.write_entry(target_repo, "notes/hello.md", "doc", "# Hello",
                           project_id="proj-1", connection_id="upload-x")
        source = target_repo / "sources/upload" / f"{upload.upload_slug('proj-1', 'notes/hello.md')}.md"
        modified = source.stat().st_mtime_ns
        record.reset_mock()
        written_again = upload.write_entry(target_repo, "notes/hello.md", "doc", "# Hello",
                                           project_id="proj-1", connection_id="upload-x")
        assert source.stat().st_mtime_ns == modified
    assert written_again is False, "identical content must not rewrite"
    record.assert_called_once()
    assert record.call_args.kwargs["connection_id"] == "upload-x"
    assert record.call_args.kwargs["folder"] == "notes"


def test_write_entry_stores_data_files_as_raw_bytes_with_a_preview_inbox_body():
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record"):
        target_repo = Path(tmp)
        upload.write_entry(target_repo, "table.csv", "dataset", b"a,b\n1,2\n",
                           project_id="proj-1", connection_id="upload-x")
        stored = target_repo / "sources" / "upload" / f"{upload.upload_slug('proj-1', 'table.csv')}.csv"
        inbox = target_repo / "raw" / "inbox" / f"{upload.upload_id('proj-1', 'table.csv')}.md"
        assert stored.read_bytes() == b"a,b\n1,2\n"
        assert "a,b" in inbox.read_text()  # summarize_dataset's preview, not the raw bytes


def test_run_processes_every_staged_file_and_cleans_up():
    seen = []
    with tempfile.TemporaryDirectory() as tmp:
        target_repo = Path(tmp) / "repo"
        staged = Path(tmp) / "staged"
        (staged / "notes").mkdir(parents=True)
        (staged / "notes" / "a.md").write_text("A", encoding="utf-8")
        (staged / "b.exe").write_bytes(b"\x00")  # unsupported — must fail, not crash the run

        with patch.object(upload, "staging_dir", return_value=staged), \
             patch.object(upload, "config") as cfg, \
             patch.object(upload.sources_index, "record"), \
             patch.object(upload.sources_index, "record_failure",
                          side_effect=lambda **kw: seen.append(kw["id"])):
            cfg.GDRIVE_TARGET_REPO = target_repo
            result = upload.run(project_id="proj-1", connection_id="upload-x",
                                on_progress=lambda d, t, label: None)
            seen_count, written = result

        assert seen_count == 2, seen_count
        assert written == 1, written
        assert seen == [upload.upload_id("proj-1", "b.exe")]
        assert result.failed == 1 and not result.complete
        assert not (staged / "notes/a.md").exists(), "successful files leave staging"
        assert (staged / "b.exe").read_bytes() == b"\x00", "failed originals must remain available for retry"


def test_write_entry_keeps_the_original_beside_the_extracted_text():
    calls = []
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record",
                      side_effect=lambda **kw: calls.append(kw)):
        target_repo = Path(tmp)
        original = target_repo / "staged" / "q3.pdf"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"%PDF-1.7 not really a pdf")

        upload.write_entry(target_repo, "reports/q3.pdf", "binary_doc",
                           "Q3 revenue was flat.", project_id="proj-1",
                           connection_id="upload-x", original=original)

        slug = upload.upload_slug("proj-1", "reports/q3.pdf")
        kept = target_repo / "sources" / "upload" / "originals" / f"{slug}.pdf"
        assert kept.read_bytes() == b"%PDF-1.7 not really a pdf"
        assert calls[0]["original_path"] == f"sources/upload/originals/{slug}.pdf"
        # The extracted text is still the citable source file, unchanged.
        assert (target_repo / "sources" / "upload" / f"{slug}.md").is_file()


def test_write_entry_records_no_original_when_none_was_kept():
    calls = []
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record",
                      side_effect=lambda **kw: calls.append(kw)):
        upload.write_entry(Path(tmp), "notes/hello.md", "doc", "# Hello",
                           project_id="proj-1", connection_id="upload-x")
    assert calls[0]["original_path"] is None, "a .md is its own original"


def test_original_only_revision_updates_download_without_requeueing(tmp_path, monkeypatch):
    monkeypatch.setattr(upload.sources_index, "record", lambda **_: None)
    original = tmp_path / "report.pdf"
    original.write_bytes(b"original layout")
    upload.write_entry(tmp_path, "report.pdf", "binary_doc", "same text",
                       project_id="project", original=original)
    slug = upload.upload_slug("project", "report.pdf")
    source = tmp_path / "sources/upload" / f"{slug}.md"
    inbox = tmp_path / "raw/inbox" / f"{upload.upload_id('project', 'report.pdf')}.md"
    modified = (source.stat().st_mtime_ns, inbox.stat().st_mtime_ns)
    original.write_bytes(b"revised layout")

    changed = upload.write_entry(tmp_path, "report.pdf", "binary_doc", "same text",
                                 project_id="project", original=original)

    assert not changed
    assert (tmp_path / "sources/upload/originals" / f"{slug}.pdf").read_bytes() == b"revised layout"
    assert (source.stat().st_mtime_ns, inbox.stat().st_mtime_ns) == modified


def test_unchanged_content_still_self_heals_a_missing_original():
    """The sha-skip must not strand a row uploaded before originals/ existed."""
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(upload.sources_index, "record"), \
         patch.object(upload.sources_index, "attribute"), \
         patch.object(upload.sources_index, "set_folder"):
        target_repo = Path(tmp)
        original = target_repo / "staged" / "q3.pdf"
        original.parent.mkdir(parents=True)
        original.write_bytes(b"%PDF original")

        upload.write_entry(target_repo, "reports/q3.pdf", "binary_doc", "text",
                           project_id="proj-1", original=None)
        slug = upload.upload_slug("proj-1", "reports/q3.pdf")
        kept = target_repo / "sources" / "upload" / "originals" / f"{slug}.pdf"
        assert not kept.exists(), "nothing was kept the first time"

        rewrote = upload.write_entry(target_repo, "reports/q3.pdf", "binary_doc", "text",
                                     project_id="proj-1", original=original)
        assert rewrote is True, "a missing original must break the sha-skip"
        assert kept.read_bytes() == b"%PDF original"


def test_run_requires_a_connection_id():
    try:
        upload.run(project_id="proj-1", connection_id=None)
    except ValueError:
        return
    raise AssertionError("upload has no staging area without a connection_id")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("upload sync: all checks passed")
