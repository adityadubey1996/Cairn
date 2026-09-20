"""Offline self-checks for the chat feeder. Run from ai-brain root:
.venv/bin/python -m feeders.chat.check   (no network, no credentials)"""
from __future__ import annotations

import tempfile
import urllib.parse
from pathlib import Path

from feeders.chat import sync
from feeders.check_support import run_offline


def check_list_spaces() -> None:
    pages = [
        {"spaces": [
            {"name": "spaces/AAA111", "displayName": "Eng standup",
             "spaceType": "SPACE", "accessSettings": {"accessState": "PRIVATE"}},
            {"name": "spaces/BBB222", "spaceType": "GROUP_CHAT"},
            {"name": "spaces/DDD444", "spaceType": "DIRECT_MESSAGE"}],
         "nextPageToken": "p2"},
        {"spaces": [
            {"name": "spaces/CCC333", "displayName": "Carbon team",
             "spaceType": "SPACE", "accessSettings": {"accessState": "DISCOVERABLE"}}]},
    ]
    urls = []
    sync._get_json = lambda url: (urls.append(url), pages.pop(0))[1]
    spaces = sync.list_spaces()
    # no server-side filter param — spaceType = "SPACE" was verified to
    # silently drop PRIVATE (meeting-auto-created) spaces; AAA111 here is
    # PRIVATE and must still come through via the client-side type check,
    # which is also what drops the GROUP_CHAT and DIRECT_MESSAGE entries
    assert [s["name"] for s in spaces] == ["spaces/AAA111", "spaces/CCC333"], spaces
    q = urllib.parse.unquote_plus(urls[0])
    assert "filter=" not in q, q
    assert "pageToken=p2" in urls[1], urls


def check_list_messages() -> None:
    urls = []
    sync._get_json = lambda url: (urls.append(url), {"messages": [
        {"name": "m2", "createTime": "2026-08-12T10:00:00.000000Z", "text": "later"},
        {"name": "m1", "createTime": "2026-08-11T09:00:00.000000Z", "text": "earlier"}]})[1]
    msgs = sync.list_messages("spaces/AAA111", created_after="2026-08-01T00:00:00Z")
    assert [m["name"] for m in msgs] == ["m1", "m2"], "must sort by createTime"
    assert urls[0].startswith(sync.API + "/spaces/AAA111/messages?"), urls
    q = urllib.parse.unquote_plus(urls[0])
    assert 'createTime > "2026-08-01T00:00:00Z"' in q, q


MSGS = [
    {"createTime": "2026-08-12T09:15:00.000000Z", "text": "morning all",
     "sender": {"name": "users/111", "displayName": "Aditya Dubey", "type": "HUMAN"}},
    {"createTime": "2026-08-12T09:16:30.000000Z", "text": "shipping the fix",
     "sender": {"name": "users/222", "type": "HUMAN"}},  # no displayName
]


def check_render_day() -> None:
    space = {"name": "spaces/AAA111", "displayName": "Eng standup"}
    body = sync._render_day(space, "2026-08-12", MSGS)
    assert body == ("# Eng standup — 2026-08-12\n\n"
                    "09:15 Aditya Dubey: morning all\n"
                    "09:16 222: shipping the fix"), body


def check_run() -> None:
    """The grouping, empty-day skip, frontmatter, and sha dedup all live in
    run(), so run() gets the offline check — against a temp tree."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sync.config.SOURCES_DIR = root / "sources"
        sync.config.GDRIVE_TARGET_REPO = root
        sync.list_spaces = lambda: [
            {"name": "spaces/AAA111", "displayName": "Eng standup",
             "spaceType": "SPACE"}]
        sync.list_messages = lambda name, created_after="": MSGS + [
            {"createTime": "2026-08-13T08:00:00.000000Z", "text": "   ",
             "sender": {"name": "users/111", "displayName": "Aditya Dubey"}}]

        seen, written = run_offline(sync)
        # the whitespace-only 08-13 message must not become an entry
        assert (seen, written) == (1, 1), (seen, written)
        entry = (root / "raw/inbox/gchat-AAA111-2026-08-12.md").read_text()
        assert "id: gchat-AAA111-2026-08-12" in entry
        assert "path: sources/gchat/eng-standup-2026-08-12-aaa111.md" in entry
        assert "source_type: chat_thread" in entry
        assert "date: 2026-08-12" in entry
        assert 'time: "09:16:30"' in entry
        assert 'authors: ["Aditya Dubey", "222"]' in entry
        assert (root / "sources/gchat/eng-standup-2026-08-12-aaa111.md").is_file()

        seen, written = run_offline(sync)
        assert (seen, written) == (1, 0), "unchanged day must not rewrite"


def check_one_bad_space_does_not_abort_the_batch() -> None:
    with tempfile.TemporaryDirectory() as d:
        sync.config.SOURCES_DIR = Path(d) / "sources"
        sync.config.GDRIVE_TARGET_REPO = Path(d)
        sync.list_spaces = lambda: [
            {"name": "spaces/bad"}, {"name": "spaces/good", "displayName": "Good"}]
        def fake_list_messages(space_name, created_after=""):
            if "bad" in space_name:
                raise RuntimeError("boom")
            return [{"createTime": "2026-01-01T10:00:00Z", "text": "hi",
                     "sender": {"displayName": "A"}}]
        sync.list_messages = fake_list_messages
        seen, written = run_offline(sync)
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
    check_list_spaces()
    check_list_messages()
    check_render_day()
    check_run()
    check_one_bad_space_does_not_abort_the_batch()
    check_authors_survive_the_frontmatter_quote_strip()
    print("chat checks OK")
