"""Offline self-checks for the links feeder. Run from ai-brain root:
.venv/bin/python -m feeders.links.check   (no network, no credentials)"""
from __future__ import annotations

import io
import tempfile
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from feeders.links import sync
from feeders.check_support import run_offline


def check_extract_urls_from_text() -> None:
    text = (
        "See https://www.eia.gov/todayinenergy/ for context.\n"
        "Also [the whitepaper](https://chestnutcarbon.com/paper.pdf) and "
        "a repeat: https://www.eia.gov/todayinenergy/.\n"
        "Trailing punctuation: https://example.com/page, then more text."
    )
    urls = sync.extract_urls_from_text(text)
    assert urls == [
        "https://www.eia.gov/todayinenergy/",
        "https://chestnutcarbon.com/paper.pdf",
        "https://example.com/page",
    ], urls


def check_extract_urls_from_text_balances_trailing_paren() -> None:
    """A real Wikipedia-style URL's own "(bar)" must survive extraction.
    URL_RE excludes ")" (so a link's surrounding sentence/markdown
    parenthesis isn't swallowed), which truncates .../wiki/Foo_(bar) right
    at the open paren. Confirms the balanced-trailing-paren fix pulls the
    matching close paren back into the URL, while a URL merely wrapped in
    outer sentence-parens (no paren inside the URL itself) still correctly
    excludes that outer close paren."""
    text = (
        "See https://en.wikipedia.org/wiki/Foo_(bar) for background, "
        "and (also see https://example.com/plain) for context."
    )
    urls = sync.extract_urls_from_text(text)
    assert urls == [
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "https://example.com/plain",
    ], urls


def check_discover_urls() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "sources/gdrive").mkdir(parents=True)
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gdrive/a.md").write_text(
            "Body citing [a report](https://sec.gov/filing.pdf).")
        (repo / "sources/gchat/b.md").write_text(
            "06:40 someone: https://sec.gov/filing.pdf\n"
            "06:43 someone: https://github.com/acme/widgets")
        urls = sync.discover_urls(repo)
        assert urls == [
            "https://sec.gov/filing.pdf",
            "https://github.com/acme/widgets",
        ], urls


def check_discover_urls_skips_links_connector_output() -> None:
    """Regression test: verify that discover_urls does NOT include URLs from
    sources/links/ (the links connector's own output). This ensures unbounded
    recursion growth doesn't happen — URLs found in previously-fetched content
    are reached through expand_one_hop, never by re-scanning the disk."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "sources/gdrive").mkdir(parents=True)
        (repo / "sources/links").mkdir(parents=True)

        # URL cited in a gdrive document — should be discovered
        (repo / "sources/gdrive/document.md").write_text(
            "See https://external-source.example/article for details.")

        # URL in sources/links/ from a previous fetch — must NOT be discovered
        # This simulates what happens after run 1: a fetched page's text
        # (containing URLs) is written to sources/links/, and on run 2 we must
        # NOT re-scan its content as top-level discoveries.
        (repo / "sources/links/fetched-page.md").write_text(
            "This page cites https://leaked-url.example/must-not-discover")

        urls = sync.discover_urls(repo)

        # Must include the gdrive-discovered URL
        assert "https://external-source.example/article" in urls, \
            f"gdrive URL should be discovered, got {urls}"

        # Must NOT include the leaked URL from sources/links/
        assert "https://leaked-url.example/must-not-discover" not in urls, \
            f"sources/links/ URL must not be discovered, got {urls}"


def check_classify_url() -> None:
    assert sync.classify_url("https://drive.google.com/file/d/abc123/view") == "drive"
    assert sync.classify_url("https://docs.google.com/document/d/xyz/edit") == "drive"
    assert sync.classify_url("https://eia.gov/data/production.csv") == "data"
    assert sync.classify_url("https://eia.gov/data/production.csv?raw=1") == "data"
    assert sync.classify_url("https://sec.gov/filing.pdf") == "pdf"
    assert sync.classify_url("https://semianalysis.com/some-article") == "html"


FAKE_PAGE = """<!doctype html>
<html><head><title>Ignored</title>
<style>.nav { color: red; }</style>
<script>track();</script>
</head>
<body>
<nav><a href="/a">Menu</a> <a href="/b">Item</a></nav>
<article>
<h1>Battery storage capacity averaged 70% growth</h1>
<p>Utility-scale battery storage capacity increased
significantly during the last three years.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>"""


def check_extract_html_text() -> None:
    text = sync.extract_html_text(FAKE_PAGE)
    assert "battery storage capacity increased" in text.lower(), text
    assert "track()" not in text, "script content must not leak into body"
    assert ".nav { color: red; }" not in text, "style content must not leak into body"
    assert "Ignored" not in text, "the <title> is metadata, not body text"


def check_extract_html_text_malformed() -> None:
    """Verify that unclosed skip tags cause extract_html_text to raise ValueError,
    rather than silently returning truncated text that looks successful."""
    malformed_html = """<!doctype html>
<html><head><title>Page</title></head>
<body>
<nav>
<article>Some content here</article>
</body></html>"""
    try:
        sync.extract_html_text(malformed_html)
        assert False, "Expected ValueError for unclosed <nav> tag"
    except ValueError as e:
        assert "unclosed tag" in str(e), f"Unexpected error message: {e}"


def check_build_dataset_body() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "sources/links").mkdir(parents=True)
        (repo / "sources/links/production.csv").write_text(
            "date,region,bcf_per_day\n2026-08-01,US,122.5\n2026-08-02,US,122.7\n")
        body = sync.build_dataset_body(repo, "sources/links/production.csv")
        assert "production.csv" in body, body
        assert "date,region,bcf_per_day" in body, body
        assert "2026-08-01,US,122.5" in body, body


def _pdf(text: str) -> bytes:
    w = PdfWriter()
    page = w.add_blank_page(width=200, height=200)
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


def check_fetch_pdf_text() -> None:
    assert sync.fetch_pdf_text(_pdf("Quarterly report")) == "Quarterly report"


def check_extract_drive_file_id() -> None:
    assert sync.extract_drive_file_id(
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQ/view?usp=sharing"
    ) == "1AbCdEfGhIjKlMnOpQ"
    assert sync.extract_drive_file_id(
        "https://docs.google.com/document/d/1XyZ_9-abc/edit#heading=h.1"
    ) == "1XyZ_9-abc"
    assert sync.extract_drive_file_id(
        "https://drive.google.com/open?id=1QwErTyUiOp"
    ) == "1QwErTyUiOp"
    assert sync.extract_drive_file_id("https://drive.google.com/drive/folders/1abc") is None


def check_fetch_via_drive() -> None:
    calls = []

    def fake_get_json(url):
        calls.append(url)
        return {"id": "f1", "name": "Pricing model",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-08-05T10:00:00.000Z",
                "owners": [{"displayName": "Teammate"}]}

    sync._gdrive._get_json = fake_get_json
    sync._gdrive.export_text = lambda f: "# Pricing model\n\nBody text."
    result = sync.fetch_via_drive("f1")
    assert result == ("# Pricing model\n\nBody text.", "doc", ["Teammate"]), result
    assert f"{sync._DRIVE_API}/files/f1" in calls[0], calls

    sync._gdrive._get_json = lambda url: {"id": "f2", "name": "budget.xlsx",
                                          "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                          "modifiedTime": "2026-08-05T10:00:00.000Z"}
    assert sync.fetch_via_drive("f2")[1] == "binary_doc", "Office documents are extractable"
    sync._gdrive._get_json = lambda url: {"id": "f2", "name": "recording.mp4",
                                          "mimeType": "video/mp4",
                                          "modifiedTime": "2026-08-05T10:00:00.000Z"}
    assert sync.fetch_via_drive("f2") is None, "unsupported mime type must not crash"

    # Test that export_text raising an exception returns None, not propagating
    sync._gdrive._get_json = lambda url: {"id": "f3", "name": "Broken doc",
                                          "mimeType": "application/vnd.google-apps.document",
                                          "modifiedTime": "2026-08-05T10:00:00.000Z",
                                          "owners": [{"displayName": "Author"}]}
    sync._gdrive.export_text = lambda f: (_ for _ in ()).throw(RuntimeError("Export failed"))
    assert sync.fetch_via_drive("f3") is None, "export_text raising must not crash"


def check_get_bytes_rejects_private_address() -> None:
    """SSRF guard: a URL that resolves to a loopback/link-local/private
    address must be rejected before any connection is attempted — not just
    eventually fail. No real DNS needed: these are IP literals."""
    def _must_not_connect(*args, **kwargs):
        raise AssertionError("connection must not be attempted for a rejected address")

    original_open = sync._opener.open
    sync._opener.open = _must_not_connect
    try:
        for url in ("http://127.0.0.1:9/secret",
                    "http://169.254.169.254/latest/meta-data/",
                    "http://10.0.0.5/internal"):
            try:
                sync._get_bytes(url)
                assert False, f"expected rejection for {url}"
            except ValueError:
                pass
    finally:
        sync._opener.open = original_open


def check_write_entry_new_then_unchanged_then_revised() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        url = "https://www.eia.gov/todayinenergy/"

        changed = sync.write_entry(repo, url, "First fetch of the article.",
                                   "external_article", "md")
        assert changed is True
        source = repo / "sources/links" / (sync.url_to_slug(url) + ".md")
        assert source.read_text() == "First fetch of the article."
        entry_path = repo / "raw/inbox" / f"link-{sync.link_id(url)}.md"
        entry = entry_path.read_text()
        assert "source_type: external_article" in entry
        assert f'source_url: "{url}"' in entry
        assert "previous_sha" not in entry, "first write is not a revision"
        assert "authors: []" in entry, "authors=None defaults to empty list"

        changed = sync.write_entry(repo, url, "First fetch of the article.",
                                   "external_article", "md")
        assert changed is False, "identical content must not rewrite"

        changed = sync.write_entry(repo, url, "Updated: production revised upward.",
                                   "external_article", "md")
        assert changed is True
        entry = entry_path.read_text()
        assert "previous_sha:" in entry and "changed_at:" in entry
        assert source.read_text() == "Updated: production revised upward."


def check_write_entry_self_heals_missing_inbox() -> None:
    """Regression test for the silent-permanent-data-loss bug: write_entry
    writes the source file, then the inbox entry. If a process dies (or any
    error hits) between those two writes, the source file is left on disk
    with the new content's sha already matching — reproduce that broken
    state directly and confirm a later call with identical content
    self-heals (writes the missing inbox entry) instead of silently
    skipping forever, and does not stamp a bogus previous_sha (this is the
    first real inbox write, not a revision)."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        url = "https://www.example.com/partial-write"
        body = "Content that was fetched but never made it to the inbox."

        sources_dir = repo / "sources/links"
        sources_dir.mkdir(parents=True)
        slug = sync.url_to_slug(url)
        (sources_dir / f"{slug}.md").write_text(body, encoding="utf-8")

        inbox_path = repo / "raw/inbox" / f"link-{sync.link_id(url)}.md"
        assert not inbox_path.is_file(), "test setup: inbox must not exist yet"

        changed = sync.write_entry(repo, url, body, "external_article", "md")
        assert changed is True, "must self-heal (write the inbox entry), not silently skip forever"
        assert inbox_path.is_file()
        entry = inbox_path.read_text()
        assert "previous_sha" not in entry, \
            "first real inbox write is not a revision, even though the source file pre-existed"


def check_write_entry_raw_bytes_and_authors() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        url = "https://eia.gov/data/production.csv"
        raw = b"date,bcf\n2026-08-01,122.5\n"
        changed = sync.write_entry(repo, url, "_1 file._\n\nproduction.csv preview",
                                   "dataset", "csv", raw_bytes=raw,
                                   authors=["Teammate"])
        assert changed is True
        source = repo / "sources/links" / (sync.url_to_slug(url) + ".csv")
        assert source.read_bytes() == raw, "raw bytes stored verbatim, not the preview text"
        entry = (repo / "raw/inbox" / f"link-{sync.link_id(url)}.md").read_text()
        assert "production.csv preview" in entry, "inbox body is the preview, not the raw csv"
        assert 'authors: ["Teammate"]' in entry


def check_write_entry_authors_edge_cases() -> None:
    """Verify both authors=None (default) and authors=[] (explicit) render as empty list."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)

        # Test explicit authors=[] case
        url_empty = "https://example.com/no-authors"
        changed = sync.write_entry(repo, url_empty, "Body text",
                                   "external_article", "md", authors=[])
        assert changed is True
        entry = (repo / "raw/inbox" / f"link-{sync.link_id(url_empty)}.md").read_text()
        assert "authors: []" in entry, "explicit authors=[] must render as empty list"


def check_expand_one_hop() -> None:
    page_text = (
        "This report cites https://www.eia.gov/steo/ and "
        "https://sec.gov/filing.pdf, both cited again here."
    )
    already = {"https://sec.gov/filing.pdf"}  # already attempted earlier this run
    hop = sync.expand_one_hop([page_text], already)
    assert hop == ["https://www.eia.gov/steo/"], hop


def check_run_full_pipeline() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sync.config.GDRIVE_TARGET_REPO = repo
        sync.config.LINKS_FETCH_CAP = 5000
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gchat/day.md").write_text(
            "10:00 x: https://good.example/article\n"
            "10:01 x: https://data.example/table.csv\n"
            "10:02 x: https://dead.example/gone\n")

        third_hop_url = "https://third-hop.example/never-fetched"

        def fake_dispatch(url):
            if url == "https://good.example/article":
                return ("external_article", "Cites https://second-hop.example/more.", [])
            if url == "https://data.example/table.csv":
                return ("data-raw", b"a,b\n1,2\n", [])
            if url == "https://second-hop.example/more":
                # third_hop_url is technically discoverable from this
                # page's text, but expand_one_hop only ever runs once —
                # run() must never fetch it (recursion-boundary check below).
                return ("external_article", f"Also cites {third_hop_url}.", [])
            raise TimeoutError("simulated dead host")

        sync._dispatch_fetch = fake_dispatch

        seen, written = run_offline(sync)
        assert seen == 4, seen  # 3 initial + 1 one-hop; dead.example counted as an attempt
        assert written == 3, written  # good, data, second-hop written; dead wrote nothing
        assert not (repo / f"raw/inbox/link-{sync.link_id('https://dead.example/gone')}.md").is_file()

        # Recursion-depth boundary: a second-hop link is discovered (it's
        # right there in second-hop.example's text) but must not be
        # fetched — recursion stops at one hop, nothing scans the hop
        # batch's own fetched text for further URLs.
        third_hop_source = repo / "sources/links" / (sync.url_to_slug(third_hop_url) + ".md")
        assert not third_hop_source.is_file(), \
            "second-hop link must never be fetched — recursion stops at one hop"
        third_hop_id = sync.link_id(third_hop_url)
        assert not (repo / f"raw/inbox/link-{third_hop_id}.md").is_file()

        # Second run: everything gets re-attempted (not just new URLs) — this
        # is the behavior that makes previous_sha/changed_at possible at all.
        # seen2 is 4, matching run 1: discover_urls skips sources/links/ (the
        # links feeder's own output), so it only re-discovers the original 3
        # gchat URLs. The one-hop expansion still runs on top of those, finding
        # second-hop again from good.example's fetched text. Content is
        # unchanged, so nothing gets WRITTEN — the dedup is via sha compare, not
        # by avoiding re-fetch.
        seen2, written2 = run_offline(sync)
        assert seen2 == 4, seen2
        assert written2 == 0, written2


def check_fetch_cap_enforced() -> None:
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sync.config.GDRIVE_TARGET_REPO = repo
        sync.config.LINKS_FETCH_CAP = 2
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gchat/day.md").write_text(
            "https://a.example/1 https://a.example/2 https://a.example/3\n")
        sync._dispatch_fetch = lambda url: ("external_article", "text", [])
        seen, written = run_offline(sync)
        assert seen == 2, "must stop at the cap, not process all 3"


def check_revision_detected_on_recheck() -> None:
    """The whole reason every run re-attempts every URL: a page that
    genuinely changed must produce a previous_sha, not silence."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sync.config.GDRIVE_TARGET_REPO = repo
        sync.config.LINKS_FETCH_CAP = 5000
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gchat/day.md").write_text("https://changing.example/page\n")

        sync._dispatch_fetch = lambda url: ("external_article", "version one", [])
        run_offline(sync)
        sync._dispatch_fetch = lambda url: ("external_article", "version two, revised", [])
        run_offline(sync)

        entry = (repo / f"raw/inbox/link-{sync.link_id('https://changing.example/page')}.md").read_text()
        assert "previous_sha:" in entry, entry
        assert "version two, revised" in entry, entry


def check_data_raw_produces_inbox_entry() -> None:
    """Regression test: data-raw URLs must produce both sources file AND inbox entry.
    Verifies that the temporary-directory approach for building previews doesn't
    interfere with write_entry's sha-compare logic, and that file extension is
    correctly derived from URL, not hardcoded to 'csv'."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sync.config.GDRIVE_TARGET_REPO = repo
        sync.config.LINKS_FETCH_CAP = 5000
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gchat/day.md").write_text(
            "https://data.example/table.csv https://data.example/data.json\n")

        sync._dispatch_fetch = lambda url: (
            ("data-raw", b"a,b\n1,2\n", []) if url == "https://data.example/table.csv"
            else ("data-raw", b'{"name":"test","value":42}', [])
        )
        seen, written = run_offline(sync)

        # Both data URLs should be written
        assert written == 2, f"expected 2 written (csv + json), got {written}"

        # Check CSV: both sources file and inbox entry must exist
        csv_id = sync.link_id("https://data.example/table.csv")
        csv_inbox = repo / f"raw/inbox/link-{csv_id}.md"
        assert csv_inbox.is_file(), f"CSV inbox entry missing: {csv_inbox}"
        csv_inbox_text = csv_inbox.read_text()
        assert "a,b" in csv_inbox_text, "CSV preview must be in inbox entry body"
        assert "1,2" in csv_inbox_text, "CSV data must be in inbox entry body"

        csv_sources = sorted((repo / "sources/links").glob("*-*.csv"))
        assert len(csv_sources) >= 1, "CSV source file missing"
        assert csv_sources[0].read_bytes() == b"a,b\n1,2\n", "CSV source must have raw bytes"

        # Check JSON: extension must be .json, not hardcoded .csv
        json_id = sync.link_id("https://data.example/data.json")
        json_inbox = repo / f"raw/inbox/link-{json_id}.md"
        assert json_inbox.is_file(), f"JSON inbox entry missing: {json_inbox}"
        json_inbox_text = json_inbox.read_text()
        assert "name" in json_inbox_text, "JSON key 'name' must be in inbox entry body"
        assert "value" in json_inbox_text, "JSON key 'value' must be in inbox entry body"

        json_sources = sorted((repo / "sources/links").glob("*-*.json"))
        assert len(json_sources) >= 1, "JSON source file with correct .json extension missing"
        assert json_sources[0].read_bytes() == b'{"name":"test","value":42}', "JSON source must have raw bytes"


def check_write_phase_error_doesnt_crash_run() -> None:
    """Regression test: write-phase errors (disk full, permission denied, etc.) must NOT
    crash the entire run. _attempt() must catch write errors and return (False, None),
    allowing other URLs to be processed normally. Without this guard, one write failure
    would propagate uncaught out of run() and skip all remaining URLs."""
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        sync.config.GDRIVE_TARGET_REPO = repo
        sync.config.LINKS_FETCH_CAP = 5000
        (repo / "sources/gchat").mkdir(parents=True)
        (repo / "sources/gchat/day.md").write_text(
            "https://good.example/1 https://bad.example/2 https://good.example/3\n")

        original_write_entry = sync.write_entry

        def fake_write_entry(*args, **kwargs):
            # Fail for this specific URL, not "the Nth call" — run() now
            # fetches concurrently, so call order isn't deterministic.
            if args[1] == "https://bad.example/2":
                raise OSError("simulated disk full")
            return original_write_entry(*args, **kwargs)

        sync._dispatch_fetch = lambda url: ("external_article", f"Content for {url}", [])
        sync.write_entry = fake_write_entry

        # This should NOT raise; the run should complete with partial results
        seen, written = run_offline(sync)

        # All 3 URLs should be attempted (seen=3)
        assert seen == 3, f"expected 3 seen (all URLs attempted), got {seen}"

        # Only 2 should be written (1st and 3rd succeeded, 2nd failed)
        assert written == 2, f"expected 2 written (1st and 3rd), got {written}"

        # Verify that entries 1 and 3 were created
        id1 = sync.link_id("https://good.example/1")
        id3 = sync.link_id("https://good.example/3")
        entry1 = repo / f"raw/inbox/link-{id1}.md"
        entry3 = repo / f"raw/inbox/link-{id3}.md"
        assert entry1.is_file(), "Entry 1 should exist"
        assert entry3.is_file(), "Entry 3 should exist"

        # Verify that entry 2 was NOT created (write failed)
        id2 = sync.link_id("https://bad.example/2")
        entry2 = repo / f"raw/inbox/link-{id2}.md"
        assert not entry2.is_file(), "Entry 2 should NOT exist (write failed)"


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
    check_extract_urls_from_text()
    check_extract_urls_from_text_balances_trailing_paren()
    check_discover_urls()
    check_discover_urls_skips_links_connector_output()
    check_classify_url()
    check_extract_html_text()
    check_extract_html_text_malformed()
    check_build_dataset_body()
    check_fetch_pdf_text()
    check_extract_drive_file_id()
    check_fetch_via_drive()
    check_get_bytes_rejects_private_address()
    check_write_entry_new_then_unchanged_then_revised()
    check_write_entry_self_heals_missing_inbox()
    check_write_entry_raw_bytes_and_authors()
    check_write_entry_authors_edge_cases()
    check_expand_one_hop()
    check_run_full_pipeline()
    check_fetch_cap_enforced()
    check_revision_detected_on_recheck()
    check_data_raw_produces_inbox_entry()
    check_write_phase_error_doesnt_crash_run()
    check_authors_survive_the_frontmatter_quote_strip()
    print("links checks OK")
