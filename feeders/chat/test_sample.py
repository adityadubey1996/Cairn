"""Bounded Chat samples must preserve whole-day source identity and content."""
import urllib.parse
from unittest.mock import Mock

import pytest

from feeders.chat import sync


def message(day, hour, text):
    return {"name": f"messages/{text}", "createTime": f"2026-09-{day}T{hour}:00:00Z", "text": text}


def test_sample_reads_across_pages_until_whole_day_is_known(monkeypatch):
    pages = iter([
        {"messages": [message("20", "12", "new")], "nextPageToken": "page2"},
        {"messages": [message("20", "09", "old"), message("19", "08", "previous day")]},
    ])
    urls = []
    monkeypatch.setattr(sync, "_get_json", lambda url: (urls.append(url), next(pages))[1])
    messages, incomplete = sync.list_message_sample("spaces/space1", 1)
    assert [m["text"] for m in messages] == ["old", "new"] and incomplete
    assert urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)["orderBy"] == ["createTime DESC"]
    assert urllib.parse.parse_qs(urllib.parse.urlparse(urls[1]).query)["pageToken"] == ["page2"]


def test_oversized_day_fails_after_five_pages_instead_of_returning_partial_text(monkeypatch):
    fetch = Mock(return_value={"messages": [message("20", "12", "one day")], "nextPageToken": "more"})
    monkeypatch.setattr(sync, "_get_json", fetch)
    with pytest.raises(ValueError, match="five message pages"):
        sync.list_message_sample("spaces/space1", 1)
    assert fetch.call_count == 5


def test_run_sample_writes_complete_day_skips_attachments_and_other_spaces(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync.sources_index, "record", Mock())
    monkeypatch.setattr(sync.sources_index, "record_failure", Mock())
    monkeypatch.setattr(sync, "list_spaces", lambda: [{"name": "spaces/one"}, {"name": "spaces/two"}])
    old = message("20", "09", "earlier context")
    new = message("20", "12", "latest reply")
    new["attachment"] = [{"contentName": "notes.pdf"}]
    sample = Mock(return_value=([old, new], True))
    monkeypatch.setattr(sync, "list_message_sample", sample)
    download = Mock()
    monkeypatch.setattr(sync, "download_attachment", download)
    result = sync.run(project_id="project", max_items=1, created_after="2026-09-20T11:00:00Z")
    assert result == (1, 1) and not result.complete
    assert result.failures[0]["id"] == "gchat-backlog"
    sample.assert_called_once_with("spaces/one", 1)
    download.assert_not_called()
    text = next((tmp_path / "sources/gchat").glob("*.md")).read_text()
    assert "earlier context" in text and "latest reply" in text


def test_sample_completes_when_no_sources_are_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr(sync.config, "GDRIVE_TARGET_REPO", tmp_path)
    monkeypatch.setattr(sync.sources_index, "record", Mock())
    monkeypatch.setattr(sync, "list_spaces", lambda: [{"name": "spaces/one"}])
    monkeypatch.setattr(sync, "list_message_sample", lambda *_: ([message("20", "12", "only message")], False))
    result = sync.run(project_id="project", max_items=1)
    assert result == (1, 1) and result.complete


def test_invalid_cap_never_contacts_google(monkeypatch):
    remote = Mock()
    monkeypatch.setattr(sync, "list_spaces", remote)
    with pytest.raises(ValueError, match="max_items"):
        sync.run(project_id="project", max_items=-1)
    remote.assert_not_called()
