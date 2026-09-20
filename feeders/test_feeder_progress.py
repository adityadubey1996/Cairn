#!/usr/bin/env python3
"""Self-check that both feeders accept a progress callback.
Run: python3 feeders/test_feeder_progress.py

No network: the Drive and Chat list calls are monkeypatched. What is under test
is the callback contract the pipeline runner depends on, not the fetching.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feeders.gdrive.sync as gdrive  # noqa: E402
import feeders.chat.sync as chat  # noqa: E402
import feeders.links.sync as links  # noqa: E402


@contextmanager
def isolated_run():
    """The advertised offline checks must not load the configured database."""
    with TemporaryDirectory() as directory, \
         patch("server.projects.ensure_default", return_value="test-project"), \
         patch.object(gdrive.config, "SOURCES_DIR", Path(directory) / "sources"), \
         patch.object(gdrive.config, "GDRIVE_TARGET_REPO", Path(directory)), \
         patch.object(gdrive.sources_index, "record_failure"):
        yield


def test_gdrive_run_accepts_on_progress_and_reports_every_file():
    seen = []
    files = [{"id": "a", "name": "One", "modified_time": "2026-01-01T00:00:00"},
             {"id": "b", "name": "Two", "modified_time": "2026-01-02T00:00:00"}]
    orig_list, orig_export = gdrive.list_drive_files, gdrive.export_text
    gdrive.list_drive_files = lambda *_a, **_k: files
    gdrive.export_text = lambda _f: ""          # empty text short-circuits the write
    try:
        with isolated_run():
            gdrive.run(on_progress=lambda done, total, label: seen.append((done, total, label)))
    finally:
        gdrive.list_drive_files, gdrive.export_text = orig_list, orig_export
    assert seen == [(1, 2, "One"), (2, 2, "Two")], seen


def test_gdrive_run_still_works_with_no_callback():
    orig_list, orig_export = gdrive.list_drive_files, gdrive.export_text
    gdrive.list_drive_files = lambda *_a, **_k: []
    gdrive.export_text = lambda _f: ""
    try:
        with isolated_run():
            assert gdrive.run() == (0, 0)
    finally:
        gdrive.list_drive_files, gdrive.export_text = orig_list, orig_export


def test_chat_run_reports_one_tick_per_space():
    # Chat cannot know its day count before walking every space, so the honest
    # denominator is spaces, not days. Days land in the log instead.
    seen = []
    spaces = [{"name": "spaces/AAA", "displayName": "Alpha"},
              {"name": "spaces/BBB", "displayName": "Beta"}]
    orig_spaces, orig_msgs = chat.list_spaces, chat.list_messages
    chat.list_spaces = lambda *_a, **_k: spaces
    chat.list_messages = lambda *_a, **_k: []
    try:
        with isolated_run():
            chat.run(on_progress=lambda done, total, label: seen.append((done, total, label)))
    finally:
        chat.list_spaces, chat.list_messages = orig_spaces, orig_msgs
    assert seen == [(1, 2, "Alpha"), (2, 2, "Beta")], seen


def test_links_run_reports_one_tick_per_attempted_url():
    """The denominator is the fetch CAP, not a URL count.

    The one-hop pass discovers its own batch from the first pass's HTML, so no
    true total exists until the run is over; the cap is the ceiling both passes
    share. Worth a check because _fetch_batch zips the url list against
    pool.map's results to know WHICH url each result belongs to.
    """
    seen = []
    orig = (links.discover_urls, links._attempt, links.expand_one_hop)
    links.discover_urls = lambda _repo: ["http://a.test/1", "http://a.test/2"]
    got_cid = []
    links._attempt = lambda _repo, _url, _pid=None, _cid=None: (got_cid.append(_cid), (0, ""))[1]
    links.expand_one_hop = lambda *_a, **_k: []
    try:
        links.run(project_id="p1", connection_id="links-abc123",
                  on_progress=lambda done, total, label: seen.append((done, total, label)))
    finally:
        links.discover_urls, links._attempt, links.expand_one_hop = orig
    assert [d for d, _t, _l in seen] == [1, 2], seen
    assert [l for _d, _t, l in seen] == ["http://a.test/1", "http://a.test/2"], seen
    assert {t for _d, t, _l in seen} == {links.config.LINKS_FETCH_CAP}, seen
    # Phase 3: every fetch is attributed to the connection that asked for it.
    assert got_cid == ["links-abc123", "links-abc123"], got_cid


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("feeder progress: all checks passed")
