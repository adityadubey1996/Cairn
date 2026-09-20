"""Regression checks for preserving source history and reporting partial syncs.

All provider and database operations are mocked; writes stay in tmp_path.
"""
from unittest.mock import Mock

from feeders.chat import sync as chat
from feeders.gdrive import sync as drive
from feeders.result import SyncResult


def _sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(drive.config, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(drive.config, "GDRIVE_TARGET_REPO", tmp_path)
    rows = {}
    monkeypatch.setattr(drive.sources_index, "get", lambda uid: rows.get(uid))
    monkeypatch.setattr(drive.sources_index, "record", lambda **row: rows.update({row["id"]: row}))
    monkeypatch.setattr(drive.sources_index, "record_failure", Mock())
    return rows


def _file(**values):
    return {"id": "fileABCDEF", "name": "Original name", "mime_type": "text/plain",
            "modified_time": "2026-09-20T09:00:00Z", "folder": None,
            "authors": [], **values}


def test_partial_result_keeps_the_original_unpacking_contract():
    result = SyncResult(3, 2, [{"id": "failed", "error": "timeout"}])
    assert tuple(result) == (3, 2)
    assert result == (3, 2)
    assert result.failed == 1 and not result.complete
    assert SyncResult(0, 0).complete


def test_chat_incremental_preserves_the_complete_day_and_old_edits(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    space = {"name": "spaces/ABCDEF", "displayName": "Test Space"}
    first = {"name": "messages/one", "createTime": "2026-09-20T09:00:00Z",
             "sender": {"displayName": "A"}, "text": "old context"}
    second = {"name": "messages/two", "createTime": "2026-09-20T11:00:00Z",
              "sender": {"displayName": "A"}, "text": "new context"}
    monkeypatch.setattr(chat, "list_spaces", lambda: [space])
    remote = [first]
    calls = []

    def messages(name, created_after=""):
        calls.append(created_after)
        return [m for m in remote if not created_after or m["createTime"] > created_after]

    monkeypatch.setattr(chat, "list_messages", messages)
    assert chat.run(project_id="p") == (1, 1)
    first["text"] = "edited old context"
    remote.append(second)
    assert chat.run(created_after="2026-09-20T10:00:00Z", project_id="p") == (1, 1)
    text = next((tmp_path / "sources/gchat").glob("*.md")).read_text()
    assert "edited old context" in text and "new context" in text
    assert calls == ["", ""]


def test_drive_rename_keeps_cited_path_and_updates_title(monkeypatch, tmp_path):
    rows = _sandbox(monkeypatch, tmp_path)
    document = _file()
    monkeypatch.setattr(drive, "list_drive_files", lambda *_: [document])
    monkeypatch.setattr(drive, "export_text", lambda _: "same content")
    assert drive.run(project_id="p") == (1, 1)
    original_path = rows["gdrive-fileABCDEF"]["path"]
    document["name"] = "Renamed file"
    assert drive.run(project_id="p") == (1, 0)
    assert (tmp_path / original_path).read_text() == "same content"
    assert rows["gdrive-fileABCDEF"]["path"] == original_path
    assert rows["gdrive-fileABCDEF"]["name"] == "Renamed file"
    assert original_path in (tmp_path / "raw/inbox/gdrive-fileABCDEF.md").read_text()
    assert len(list((tmp_path / "sources/gdrive").glob("*.md"))) == 1


def test_drive_recreates_lost_inbox_and_index_without_changing_source(monkeypatch, tmp_path):
    rows = _sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(drive, "list_drive_files", lambda *_: [_file()])
    monkeypatch.setattr(drive, "export_text", lambda _: "same content")
    drive.run(project_id="p")
    inbox = tmp_path / "raw/inbox/gdrive-fileABCDEF.md"
    inbox.unlink()
    rows.clear()
    assert drive.run(project_id="p") == (1, 1)
    assert inbox.is_file() and "gdrive-fileABCDEF" in rows


def test_drive_failure_remains_visible_to_watermark_owner(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(drive, "list_drive_files", lambda *_: [_file()])
    monkeypatch.setattr(drive, "export_text", Mock(side_effect=RuntimeError("provider timeout")))
    result = drive.run(project_id="p", connection_id="connection")
    assert result == (1, 0) and not result.complete
    assert result.failures[0]["id"] == "gdrive-fileABCDEF"
    assert result.failures[0]["error"] == "provider timeout"


def test_chat_space_failure_remains_visible_to_watermark_owner(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    monkeypatch.setattr(chat, "list_spaces", lambda: [{"name": "spaces/ABCDEF"}])
    monkeypatch.setattr(chat, "list_messages", Mock(side_effect=RuntimeError("not available")))
    result = chat.run(project_id="p")
    assert result.failed == 1 and not result.complete


def test_folder_watermark_does_not_hide_changed_descendants(monkeypatch):
    calls = []

    def pages(query, modified_after=""):
        calls.append(modified_after)
        if "'root'" in query:
            return [{"id": "old-folder", "mimeType": drive._FOLDER_MIME,
                     "modifiedTime": "2025-01-01T00:00:00Z"}] if not modified_after else []
        return [{"id": "updated-doc", "mimeType": "text/plain",
                 "modifiedTime": "2026-09-20T09:00:00Z"}]

    monkeypatch.setattr(drive, "_pages", pages)
    assert [f["id"] for f in drive._walk_folder("root", "2026-09-19T00:00:00Z")] == ["updated-doc"]
    assert calls == ["", ""]


def test_drive_external_source_volume_keeps_logical_paths(monkeypatch, tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    rows = _sandbox(monkeypatch, repo)
    monkeypatch.setattr(drive.config, "ROOT", repo)
    monkeypatch.setattr(drive.config, "SOURCES_DIR", tmp_path / "external-volume")
    monkeypatch.setattr(drive, "list_drive_files", lambda *_: [_file()])
    monkeypatch.setattr(drive, "export_text", lambda _: "source on another volume")
    assert drive.run(project_id="p") == (1, 1)
    logical = rows["gdrive-fileABCDEF"]["path"]
    assert logical.startswith("sources/gdrive/")
    assert not (repo / logical).exists()
    assert (tmp_path / "external-volume/gdrive" / logical.rsplit("/", 1)[-1]).is_file()


def test_chat_attachment_hashes_extracted_citation_bytes(monkeypatch, tmp_path):
    import hashlib

    rows = _sandbox(monkeypatch, tmp_path)
    att = {"contentName": "note.txt", "attachmentDataRef": {"resourceName": "media/ref"}}
    raw = b"text with invalid utf8 \xff"
    assert chat.write_attachment(tmp_path, att, raw, space_id="space", space_label="Space",
                                 day="2026-09-20", project_id="p")
    row = next(iter(rows.values()))
    assert row["sha"] == hashlib.sha1((tmp_path / row["path"]).read_bytes()).hexdigest()[:8]
    assert (tmp_path / row["original_path"]).read_bytes() == raw


def test_link_discovery_reads_configured_volume_and_nested_gmail(monkeypatch, tmp_path):
    from feeders.links import sync as links

    repo, volume = tmp_path / "app", tmp_path / "volume"
    repo.mkdir()
    monkeypatch.setattr(links.config, "ROOT", repo)
    monkeypatch.setattr(links.config, "SOURCES_DIR", volume)
    mail = volume / "gmail/mailbox/thread.md"
    mail.parent.mkdir(parents=True)
    mail.write_text("https://example.com/selected")
    previous = volume / "links/fetched.md"
    previous.parent.mkdir()
    previous.write_text("https://example.com/must-not-crawl")
    assert links.discover_urls(repo) == ["https://example.com/selected"]


def test_explicit_website_connection_does_not_crawl_other_sources(monkeypatch, tmp_path):
    from feeders.links import sync as links

    monkeypatch.setattr(links.config, "GDRIVE_TARGET_REPO", tmp_path)
    discover = Mock(side_effect=AssertionError("must not discover account content"))
    monkeypatch.setattr(links, "discover_urls", discover)
    monkeypatch.setattr(links, "_fetch_batch", lambda *args: (1, ["https://example.com/child"]))
    monkeypatch.setattr(links, "expand_one_hop", Mock(side_effect=AssertionError("must not crawl children")))
    result = links.run(project_id="p", urls=["https://example.com/selected"])
    assert result.seen == 1
    discover.assert_not_called()
