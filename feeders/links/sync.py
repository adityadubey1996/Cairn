"""Links feeder.

Discovers URLs already embedded in every other feeder's fetched content
(sources/*/*.md), fetches what they point to, and writes it into the same
sources/<connector>/ + raw/inbox/ contract Drive and Chat use — so a link
someone pasted into a meeting transcript becomes a normal, citable article
input, not a dead reference.

THE CONTRACT (matches ingest.py's load_inbox() exactly, same as gdrive/chat):
  raw/inbox/link-<id>.md, frontmatter this feeder owns:
    id: link-<sha1(url)[:12]>
    path: sources/links/<slug>.<ext>
    sha: <sha1 of the stored content, 8 chars>
    source_type: external_article | dataset | binary_doc | doc
    status: active
    date / time: capture time — most web pages expose no reliable authored
      date, unlike Drive's modifiedTime, so this is "when we fetched it",
      not "when the source was written". Said explicitly so nobody reads it
      as an authored date later.
    authors: [] unless the Drive-handoff path supplied real ones
    source_url: the original URL — new field, absent from gdrive/chat's
      contract since their content isn't fetched from an arbitrary address
    previous_sha / changed_at: present only when this write is a revision
      of a URL fetched with different content in an earlier run

WHY EVERY RUN RE-FETCHES EVERY DISCOVERED URL, NOT JUST NEW ONES: the
previous_sha/changed_at signal above only ever fires if a previously-seen
URL is fetched again and compared. Skipping anything already in raw/inbox/
would make that code permanently dead. This mirrors exactly how gdrive/chat
already work (list everything, let a sha compare decide what's unchanged) —
an accepted cost/time tradeoff, not an oversight (design doc §6).

WHY FOUR DISPATCH PATHS INSTEAD OF ONE "FETCH AND EXTRACT TEXT": a Drive
link fetched anonymously just hits a login wall (we already have an
authenticated path for exactly that — reuse it). A CSV dumped through an
HTML-article extractor produces garbage, and the ingest pipeline already
knows how to preview a data file (pipeline.ingest.summarize_dataset) — reuse
that instead of re-deciding what a good CSV summary looks like. A PDF has
the same "does it have a text layer" question the Drive feeder already
answers. Only a generic web page is genuinely new work here.
"""
from __future__ import annotations

import hashlib
import gzip
import io
import json
import ipaddress
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path

import feeders.gdrive.sync as _gdrive
from feeders.gdrive.sync import _pdf_to_text
from feeders.links import browser as _browser
from feeders.result import SyncResult, failure
from feeders import options
from pipeline.ingest import DATA_EXT, summarize_dataset
from pipeline.source_files import source_path as resolve_source_path
from server import config, sources as sources_index

SOURCES_SUBDIR = "links"


class _FetchResult(tuple):
    def __new__(cls, written, html_text, error=None):
        result = super().__new__(cls, (written, html_text))
        result.failure = error
        return result


class _BatchResult(tuple):
    def __new__(cls, written, html_texts, failures):
        result = super().__new__(cls, (written, html_texts))
        result.failures = failures
        return result

# Bare URLs and the URL half of markdown links both match this — a link
# inside [text](url) still matches starting at "https", the surrounding
# [](  ) is just characters this pattern doesn't need to know about.
URL_RE = re.compile(r"https?://[^\s\)\]\>\"]+")

_DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}

_DRIVE_ID_RE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")
_DRIVE_OPEN_ID_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})")
_DRIVE_API = _gdrive.API


def extract_urls_from_text(text: str) -> list[str]:
    """Deduped, order-preserving. Shared by discover_urls (reads from disk)
    and the one-hop recursion step (already has fetched text in memory).

    URL_RE excludes ")" from URL characters (so a link's surrounding
    markdown/sentence parenthesis isn't swallowed into it), which truncates
    a real Wikipedia-style URL like .../wiki/Foo_(bar) right at the open
    paren. When a match carries one unmatched "(" and the very next
    character in the source text is ")", that paren belongs to the URL —
    pull it back in rather than leaving a guaranteed-404 truncated link."""
    seen: dict[str, None] = {}
    for m in URL_RE.finditer(text):
        url = m.group(0)
        if url.count("(") > url.count(")") and text[m.end():m.end() + 1] == ")":
            url += ")"
        url = url.rstrip(".,;:")
        seen.setdefault(url, None)
    return list(seen)


def discover_urls(target_repo: Path) -> list[str]:
    """Every URL embedded in every other feeder's fetched content. Scans
    sources/*/*.md by directory, not by naming gdrive/gchat specifically, so
    a future feeder's output is covered with no change here. Skips the links
    feeder's own output (sources/links/) to prevent unbounded recursion growth:
    URLs discovered in other feeders' content are reached through expand_one_hop,
    not by re-scanning disk."""
    seen: dict[str, None] = {}
    source_root = resolve_source_path(target_repo, "sources/.")
    for md in sorted(source_root.rglob("*.md")):
        if md.relative_to(source_root).parts[0] == SOURCES_SUBDIR:
            continue
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        for url in extract_urls_from_text(text):
            seen.setdefault(url, None)
    return list(seen)


def expand_one_hop(kept_html_texts: list[str], already: set[str]) -> list[str]:
    """URLs found inside pages fetched during the initial batch. The result
    is fed through the same dispatch+write pipeline exactly once — run()
    does not call this again on what it returns, which is what keeps
    recursion at one hop rather than unbounded."""
    found: dict[str, None] = {}
    for text in kept_html_texts:
        for url in extract_urls_from_text(text):
            if url not in already:
                found.setdefault(url, None)
    return list(found)


def classify_url(url: str) -> str:
    """Which fetch path this URL needs. Order matters: a Drive host is
    checked before extension, since a Drive share link's path never carries
    a real file extension to key off of."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if parsed.netloc.lower() in _DRIVE_HOSTS:
        return "drive"
    if any(path.endswith(ext) for ext in DATA_EXT):
        return "data"
    if path.endswith(".pdf"):
        return "pdf"
    return "html"


# ponytail: 25MB ceiling on a single fetched URL — generous for an
# article/PDF/dataset, adjustable if a legitimate larger file shows up.
MAX_FETCH_BYTES = 25 * 1024 * 1024

# Codes a real browser plausibly gets past: bot detection on user-agent or TLS
# fingerprint, and content negotiation. A 404 is not here — the page is gone,
# and retrying it in a browser only spends time.
_BROWSER_WORTHY = (403, 406, 503)

# Per host, not global. A backlog is usually a handful of hosts many times over,
# so one global delay is simultaneously too slow for the long tail and too fast
# for the hosts that matter. FETCH_WORKERS is 10, so this is the only thing
# standing between a popular host and ten concurrent requests from us — which is
# what produced 343 rate-limit failures in one afternoon.
LINK_HOST_INTERVAL = float(os.environ.get("LINK_HOST_INTERVAL", "1.5"))
_host_lock = threading.Lock()
_host_next_ok: dict[str, float] = {}


def _wait_turn(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower()
    while True:
        with _host_lock:
            now = time.monotonic()
            ready_at = _host_next_ok.get(host, 0.0)
            if now >= ready_at:
                _host_next_ok[host] = now + LINK_HOST_INTERVAL
                return
            wait = ready_at - now
        time.sleep(min(wait, 5.0))


def _back_off(url: str, seconds: float) -> None:
    """Hold a whole host back, not just this URL — a 429 is about the host."""
    host = urllib.parse.urlparse(url).netloc.lower()
    with _host_lock:
        _host_next_ok[host] = max(_host_next_ok.get(host, 0.0),
                                  time.monotonic() + seconds)


def _validate_public_address(url: str) -> None:
    """SSRF guard: everything fetched by this module is a URL discovered in
    content other people wrote (Drive docs, chat messages), not a trusted
    allowlist. Resolve the hostname and reject loopback/link-local/private
    targets (cloud metadata endpoints, localhost, RFC1918 ranges) before a
    connection is opened. Raises ValueError — same as every other rejected
    fetch in this module — so the existing retry/skip logic in _attempt
    handles it with no new error path."""
    host = urllib.parse.urlparse(url).hostname
    if not host:
        raise ValueError(f"no hostname in url: {url}")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ValueError(f"could not resolve host {host!r}: {e}") from e
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_loopback or addr.is_link_local or addr.is_private:
            raise ValueError(
                f"refusing to fetch {url}: resolves to non-public address {addr}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """A redirect target is a fresh URL the origin server chose — one that
    resolved safely on the first hop could still redirect somewhere
    internal, so the same SSRF check runs again before it's followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_address(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirectHandler)


def _decode_response(data: bytes, encoding: str) -> bytes:
    """Decode transport compression with the same cap as the wire response."""
    encodings = [value.strip().lower() for value in encoding.split(",") if value.strip()]
    # Some intermediary caches serve gzip bytes without Content-Encoding.
    if not encodings and data.startswith(b"\x1f\x8b"):
        encodings = ["gzip"]
    for coding in reversed(encodings):
        if coding == "identity":
            continue
        if coding in {"gzip", "x-gzip"}:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_FETCH_BYTES + 1)
        elif coding == "deflate":
            # HTTP deflate normally includes a zlib wrapper; a few servers
            # emit the raw format. Both paths remain bounded during expansion.
            for window in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                try:
                    decoder = zlib.decompressobj(window)
                    decoded = decoder.decompress(data, MAX_FETCH_BYTES + 1)
                    break
                except zlib.error:
                    if window < 0:
                        raise
            if len(decoded) <= MAX_FETCH_BYTES and not decoder.eof:
                raise ValueError("truncated deflate HTTP response")
            data = decoded
        else:
            raise ValueError(f"unsupported HTTP content encoding: {coding}")
        if len(data) > MAX_FETCH_BYTES:
            raise ValueError(f"decoded response exceeds {MAX_FETCH_BYTES}-byte fetch ceiling")
    return data


def _get_bytes(url: str, timeout: int = 30) -> bytes:
    """No Authorization header, deliberately — every other fetch in this
    module is anonymous. A 30s timeout, shorter than Drive's 60s: we don't
    control external servers and one slow site must not dominate run time
    (see the per-URL retry/backoff in run(), Task 10)."""
    _validate_public_address(url)
    _wait_turn(url)
    req = urllib.request.Request(url, headers={"User-Agent": "cairn-links/1",
                                               "Accept-Encoding": "gzip, deflate"})
    with _opener.open(req, timeout=timeout) as r:
        data = r.read(MAX_FETCH_BYTES + 1)
        encoding = r.headers.get("Content-Encoding", "")
    if len(data) > MAX_FETCH_BYTES:
        raise ValueError(f"response exceeds {MAX_FETCH_BYTES}-byte fetch ceiling: {url}")
    return _decode_response(data, encoding)


class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "head", "nav", "footer", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth:
            stripped = data.strip()
            if stripped:
                # Normalize internal whitespace: collapse newlines and multiple spaces
                normalized = " ".join(stripped.split())
                self.chunks.append(normalized)


def extract_html_text(html: str) -> str:
    """Stdlib-only extraction: strip script/style/head/nav/footer, keep
    everything else's text. Not readability-grade — a real article-
    extraction library is the documented upgrade path if this proves too
    noisy in practice (design doc §6), not built preemptively here.

    Raises ValueError if HTML is malformed (unclosed skip tag), to avoid
    silently returning truncated text that looks like a successful fetch."""
    extractor = _TextExtractor()
    extractor.feed(html)
    if extractor._skip_depth != 0:
        raise ValueError(
            f"malformed HTML: unclosed tag, skip_depth={extractor._skip_depth} at end of parse"
        )
    return "\n".join(extractor.chunks)


def build_dataset_body(target_repo: Path, rel_path: str) -> str:
    """A fetched CSV/JSON is stored in full at sources/links/ (the citable
    artifact — see write_entry's raw_bytes handling in Task 8), but the
    raw/inbox body an absorb pass actually reads is this preview: schema and
    a few sample rows, not the whole file dumped as prose. Reuses
    ingest.py's existing multi-file summarizer unmodified with a one-file
    list — no new summarization logic to get subtly wrong."""
    return summarize_dataset(target_repo, [rel_path])


def fetch_pdf_text(raw: bytes) -> str:
    return _pdf_to_text(raw)


def extract_drive_file_id(url: str) -> str | None:
    m = _DRIVE_ID_RE.search(url) or _DRIVE_OPEN_ID_RE.search(url)
    return m.group(1) if m else None


# A link is a deliberate reference, so it earns a wider net than the Drive
# sweep's _wanted(): Sheets and Slides are worth reading when someone shared
# one, even though sweeping every spreadsheet in a Drive would not be.
_LINK_DRIVE_MIMES = (_gdrive._EXPORTABLE_MIME, _gdrive._PDF_MIME,
                     _gdrive._SHEET_MIME, _gdrive._SLIDES_MIME)


def _wanted_via_link(meta: dict) -> bool:
    return (meta.get("mimeType") in _LINK_DRIVE_MIMES
            or meta.get("mimeType") in _gdrive._OFFICE_MIMES
            or meta.get("name", "").lower().endswith(_gdrive._TEXT_EXTS))


def fetch_via_drive(file_id: str) -> tuple[str, str, list[str]] | None:
    """Routes a Drive/Docs link through the already-authenticated Drive
    feeder instead of an anonymous fetch, which would just hit a login
    wall. Returns None (never raises) for anything the mimeType check
    doesn't recognize, or that the API call fails on — a file we can't
    reach this way is simply skipped, exactly like any other fetch that
    yields nothing."""
    try:
        meta = _gdrive._get_json(
            f"{_DRIVE_API}/files/{file_id}?fields={_gdrive._FIELDS}&supportsAllDrives=true")
        if not _wanted_via_link(meta):
            # Videos are the single biggest category here and no amount of auth
            # makes one into text; naming the type beats "unsupported".
            return None
        norm = _gdrive._norm(meta)
        text = _gdrive.export_text(norm)
    except Exception:
        return None
    if not text.strip():
        return None
    return text, _gdrive._source_type(norm), norm["authors"]


def link_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def url_to_slug(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}".strip("/")
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:50]
    return f"{slug}-{link_id(url)[:6]}" if slug else link_id(url)


def write_entry(target_repo: Path, url: str, body: str, source_type: str, ext: str,
                *, raw_bytes: bytes | None = None,
                authors: list[str] | None = None, project_id: str | None = None,
                connection_id: str | None = None) -> bool:
    """The one write path every dispatch branch (html/data/pdf/drive) calls.
    sources/links/<slug>.<ext> holds raw_bytes verbatim when given (data
    files: the full fetched CSV/JSON, a real citable artifact), otherwise
    body as text (html/pdf/drive: extracted text IS the artifact, same
    convention gdrive already uses for a PDF's text layer). raw/inbox's
    body is always the preview/extracted text, never the raw bytes — that's
    what an absorb pass reads.

    sha compares whichever of (raw_bytes, body) was actually stored, so an
    unchanged fetch never rewrites — this, not any cross-run "have we seen
    this URL" check, is the entire dedup mechanism, since run() (Task 10)
    calls this for every discovered URL on every run. On a genuine change,
    the previous hash and a timestamp are recorded rather than silently
    discarded — the versioning gap this pipeline closes relative to
    Drive/Chat."""
    stored = raw_bytes if raw_bytes is not None else body.encode()
    sha = hashlib.sha1(stored).hexdigest()[:8]
    slug = url_to_slug(url)
    sources_dir = resolve_source_path(target_repo, f"sources/{SOURCES_SUBDIR}")
    inbox_dir = target_repo / "raw" / "inbox"
    sources_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)

    source_path = sources_dir / f"{slug}.{ext}"
    inbox_path = inbox_dir / f"link-{link_id(url)}.md"
    existing = None
    if source_path.is_file():
        existing = hashlib.sha1(source_path.read_bytes()).hexdigest()[:8]
    # existing == sha alone is not enough: if an earlier run wrote the
    # source file but never got to the inbox entry (crash, or any failure
    # between the two writes), the sha would already match forever and the
    # inbox entry would never be created — a silent, permanent skip.
    # Requiring the inbox entry to exist too makes that state self-healing.
    if existing == sha and inbox_path.is_file():
        if project_id:
            sources_index.record(
                id=f"link-{link_id(url)}", project_id=project_id, kind="links",
                type="link", name=url, path=f"sources/{SOURCES_SUBDIR}/{slug}.{ext}",
                url=url, size=len(stored), sha=sha, authors=authors or [],
                connection_id=connection_id)
        return False

    if raw_bytes is not None:
        source_path.write_bytes(raw_bytes)
    else:
        source_path.write_text(body, encoding="utf-8")

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    date, tm = now.split("T", 1)
    fm = [
        "---",
        f"id: link-{link_id(url)}",
        f"path: sources/links/{slug}.{ext}",
        f"sha: {sha}",
        f"source_type: {source_type}",
        "status: active",
        f"date: {date}",
        f'time: "{tm.rstrip("Z")}"',
        f"authors: [{', '.join(json.dumps(a) for a in (authors or []))}]",
        f'source_url: "{url}"',
    ]
    # existing != sha (not just "existing is not None") — otherwise the
    # self-heal case above (source file existed, inbox didn't, same
    # content) would stamp a bogus previous_sha equal to the current sha,
    # claiming a revision that never happened.
    if existing is not None and existing != sha:
        fm += [f"previous_sha: {existing}", f'changed_at: "{now}"']
    fm.append("---")
    inbox_path.write_text(
        "\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")
    if project_id:
        sources_index.record(
            id=f"link-{link_id(url)}", project_id=project_id, kind="links",
            type="link", name=url, path=f"sources/{SOURCES_SUBDIR}/{slug}.{ext}",
            url=url, size=len(stored), sha=sha, authors=authors or [],
            connection_id=connection_id)
    return True


def _dispatch_fetch(url: str) -> tuple[str, str, list[str]]:
    """Routes to the right fetcher by URL shape and returns
    (kind, payload, authors) for the caller to store — kind is "external_article" (text
    body, source_type external_article), "data-raw" (raw csv/json bytes,
    handled specially by _attempt), "binary_doc" (PDF text), or whatever
    source_type Drive itself assigns for a "drive"-shaped URL. Raises on any
    failure — run() decides retry/skip, not this function."""
    shape = classify_url(url)
    if shape == "drive":
        file_id = extract_drive_file_id(url)
        result = fetch_via_drive(file_id) if file_id else None
        if result is None:
            raise ValueError("drive file not accessible or unsupported type")
        text, source_type, authors = result
        return source_type, text, authors
    if shape == "pdf":
        text = fetch_pdf_text(_get_bytes(url))
        if not text.strip():
            raise ValueError("pdf has no extractable text layer")
        return "binary_doc", text, []
    if shape == "data":
        return "data-raw", _get_bytes(url), []
    # Plain fetch first, always: urllib is milliseconds where a browser page
    # load is seconds, so browser-first would turn a few thousand URLs into an
    # overnight job. The browser is only tried for the two shapes it actually
    # fixes — a body that arrived empty because the content is rendered by
    # JavaScript, and an outright refusal of a non-browser client.
    try:
        html = _get_bytes(url).decode("utf-8", errors="replace")
        text = extract_html_text(html)
        if text.strip():
            return "external_article", text, []
        reason = "no extractable text"
    except urllib.error.HTTPError as e:
        if e.code not in _BROWSER_WORTHY:
            raise
        reason = f"HTTP Error {e.code}: {e.reason}"

    try:
        text = extract_html_text(_browser.browser_fetch(url))
    except _browser.BrowserUnavailable:
        # No browser route available — the original reason is the true one.
        raise ValueError(reason) from None
    if not text.strip():
        raise ValueError(reason)
    return "external_article", text, []


_EXT_FOR_KIND = {"external_article": "md", "binary_doc": "md"}


def _attempt(target_repo: Path, url: str, project_id: str | None = None,
             connection_id: str | None = None) -> tuple[bool, str | None]:
    """One URL, up to 3 tries with a short backoff — a slow or unreachable
    external site must not abort the whole run. Returns
    (written, html_text_if_kept) — html_text feeds expand_one_hop; other
    kinds contribute nothing to recursion.
    # ponytail: a URL that fails every run is retried every run — no
    # cross-run failure counter. Cheap at this scale (3 tries with backoff,
    # bounded by the per-run fetch cap either way); add persistent
    # backoff-tracking if a set of permanently-dead links measurably slows
    # real runs down."""
    kind = payload = None
    authors: list[str] = []
    for attempt in (1, 2, 3):
        try:
            kind, payload, authors = _dispatch_fetch(url)
            break
        except Exception as e:
            # A 429 is a "later", not a "no". Honouring Retry-After and holding
            # the whole host back keeps the URL in this run instead of turning a
            # temporary limit into a permanent failed row.
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                try:
                    wait = float(e.headers.get("Retry-After") or 0)
                except (TypeError, ValueError):
                    wait = 0.0
                wait = min(wait or 30.0, 120.0)
                _back_off(url, wait)
                if attempt < 3:
                    time.sleep(min(wait, 30.0))
                    continue
            if attempt == 3:
                # Three tries gone. Recorded rather than silently dropped, so
                # the listing can show which URLs a run could not reach.
                if project_id:
                    sources_index.record_failure(
                        id=f"link-{link_id(url)}", project_id=project_id,
                        kind="links", name=url, url=url, reason=e,
                        connection_id=connection_id)
                return _FetchResult(False, None, failure(f"link-{link_id(url)}", url, e))
            time.sleep(2 * attempt)

    try:
        if kind == "data-raw":
            ext = (Path(urllib.parse.urlparse(url).path).suffix.lstrip(".") or "csv").lower()
            with tempfile.TemporaryDirectory() as scratch:
                scratch_file = Path(scratch) / f"data.{ext}"
                scratch_file.write_bytes(payload)
                preview = build_dataset_body(Path(scratch), scratch_file.name)
            written = write_entry(target_repo, url, preview, "dataset", ext,
                                  raw_bytes=payload, authors=authors, project_id=project_id,
                                  connection_id=connection_id)
            return written, None

        ext = _EXT_FOR_KIND.get(kind, "md")
        written = write_entry(target_repo, url, payload, kind, ext, authors=authors,
                              project_id=project_id, connection_id=connection_id)
        return written, (payload if kind == "external_article" else None)
    except Exception as e:
        if project_id:
            sources_index.record_failure(id=f"link-{link_id(url)}", project_id=project_id,
                                         kind="links", name=url, url=url, reason=e,
                                         connection_id=connection_id)
        return _FetchResult(False, None, failure(f"link-{link_id(url)}", url, e))


# ponytail: fixed pool size. Each _attempt() is dominated by one URL's own
# network latency — worse on a dead link, up to 3 tries x 30s timeout — and
# thousands of discovered URLs fetched one at a time made a single run take
# over two hours. Bump this if a run is still slow with mostly-live links.
FETCH_WORKERS = 10


def _fetch_batch(target_repo: Path, urls: list[str],
                 project_id: str | None = None,
                 on_item=None,
                 connection_id: str | None = None) -> tuple[int, list[str]]:
    """_attempt() over `urls` concurrently. Safe to parallelize as-is: each
    call only touches its own URL's slug-derived file, and the SSRF-checked
    opener has no shared mutable state across requests.

    on_item(url) fires as each result lands. pool.map yields in submission
    order, so the count it drives is monotonic even though the fetches are not.
    """
    written = 0
    kept_html: list[str] = []
    failures = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for url, result in zip(
                urls, pool.map(lambda u: _attempt(target_repo, u, project_id, connection_id), urls)):
            ok, html_text = result
            written += ok
            if getattr(result, "failure", None):
                failures.append(result.failure)
            if html_text:
                kept_html.append(html_text)
            if on_item:
                on_item(url)
    return _BatchResult(written, kept_html, failures)


def run(project_id: str | None = None, on_progress=None,
        connection_id: str | None = None, urls: list[str] | None = None,
        max_items: int = 0) -> SyncResult:
    """Returns (items_seen, items_written). items_seen counts every fetch
    ATTEMPT, successes and failures alike, since the cap bounds run time and
    risk regardless of outcome. Every discovered URL is attempted on every
    run — not gated on "have we seen this before" — because write_entry's
    sha compare is what makes an unchanged fetch a no-op write, and that
    compare is the only way a real content change is ever noticed. Called
    by connectors.run_now().

    on_progress(done, total, label) fires once per attempted URL. `total` is the
    fetch cap, not a URL count: the one-hop pass discovers its own batch from the
    first pass's HTML, so no true denominator exists until the run is over — and
    the cap is the ceiling both passes share.
    """
    max_items = options.max_items(max_items)
    urls = options.urls(urls)
    if project_id is None:
        from server import projects
        project_id = projects.ensure_default()
    # The browser ceiling is per pass, not per process: a long-running server
    # would otherwise spend it once and never use the fallback again.
    _browser.reset_budget()
    target_repo = config.GDRIVE_TARGET_REPO
    cap = min(config.LINKS_FETCH_CAP, max_items) if max_items else config.LINKS_FETCH_CAP

    # Explicit website connections only follow the URLs the user selected.
    # A links connection with no URL list retains discovery from other sources.
    explicit_urls = urls is not None
    urls = list(dict.fromkeys(urls)) if explicit_urls else discover_urls(target_repo)
    batch = urls[:cap]
    cap_skipped = len(urls) - len(batch)

    done = 0

    def tick(url: str) -> None:
        nonlocal done
        done += 1
        if on_progress:
            on_progress(done, cap, url)

    initial = _fetch_batch(target_repo, batch, project_id, tick, connection_id)
    written, kept_html = initial
    failures = list(getattr(initial, "failures", []))
    attempted = set(batch)
    seen = len(batch)

    hop_urls = [] if explicit_urls else expand_one_hop(kept_html, attempted)
    hop_batch = hop_urls[:max(0, cap - seen)]
    cap_skipped += len(hop_urls) - len(hop_batch)
    # second-hop pages are not scanned further — their kept_html is unused.
    second = _fetch_batch(target_repo, hop_batch, project_id, tick, connection_id)
    hop_written, _ = second
    failures.extend(getattr(second, "failures", []))
    written += hop_written
    seen += len(hop_batch)

    # Design requirement: never a silent truncation — say so, and say how
    # many discovered/hop URLs the cap left unattempted this run.
    if cap_skipped:
        print(f"links: fetch cap {cap} reached; {cap_skipped} URLs unprocessed this run")
        failures.append(failure("links-backlog", "Unprocessed links",
                                f"Fetch cap reached; {cap_skipped} URLs remain"))

    return SyncResult(seen, written, failures)
