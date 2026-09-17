"""Optional S3 backing for the wiki tree, separated by environment.

The pipeline is filesystem-native — absorb writes files, build_index walks the
tree, corpus fingerprints mtimes — so S3 is a *backing store*, not a mount.
Mounting a bucket would put POSIX rename and fsync semantics on something that
has neither, and absorb_runner writes through a tempfile-then-move.

    boot        pull()   s3://bucket/<env>/wikis/  ->  /data/wikis
    after absorb push()   /data/wikis  ->  s3://bucket/<env>/wikis/

Unconfigured (no S3_BUCKET) both calls are no-ops, so nothing changes for a
local run or a git-backed deploy.

Env separation is a key prefix, not a bucket: one bucket, `dev/` and `staging/`
under it, so a policy or lifecycle rule applies to both without duplication.

push() overwrites in place, so history is the bucket's job, not this module's:
turn on versioning (`scripts/wiki_s3.py versioning --enable`) and every push
keeps the copy it replaced. Each uploaded article carries its `built_from_commit`
as object metadata, which is what makes a stored version attributable to a commit.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from . import config

log = logging.getLogger("cairn.storage")

# Every article carries the commit its claims were written against. Copying it
# into object metadata is what makes a bucket version answerable — "which commit
# produced this article?" from a HEAD request, without downloading and parsing
# the file. Frontmatter only: a sha further down the body is a citation.
COMMIT_RE = re.compile(rb"^built_from_commit:\s*([0-9a-f]{7,40})", re.M)

# ponytail: fixed pool size — a cold-volume pull is dominated by S3 request
# latency per object, not bandwidth. Bump if a bucket much bigger than this
# corpus ever makes even 20-wide downloads the bottleneck.
PULL_WORKERS = 20


def enabled() -> bool:
    return bool(config.S3_BUCKET)


def trees() -> list[tuple[str, Path]]:
    """(s3 sub-path, local dir) for everything durable.

    Three things here, all things the system cannot rebuild:

      wiki     ai-brain's own company-knowledge articles, absorbed from
               sources/ into <ROOT>/wiki — same "cost an LLM call" reasoning
               as wikis/ below, just the repo's own wiki instead of the
               generated per-repo ones. Absent until absorb_runner.py ran
               successfully against ai-brain itself for the first time.
      wikis    every OTHER repo's generated wiki article — also cost an LLM call
      sources  content a feeder fetched — a Drive doc, a transcript. Re-fetching
               gives what the document says TODAY, not what was ingested, so a
               lost source silently invalidates every citation against it.

      raw/inbox  the metadata md a feeder wrote from a scrape — one entry per
               source-day, frontmatter carrying id/sha/source_type/date/authors.
               NOT derived (a feeder produced it), so it is backed up too.

    Only raw/entries, _pending.json and the manifest stay out of S3 — ingest
    regenerates those from raw/inbox + the clone in seconds, so syncing them
    would triple the traffic to protect nothing.
    """
    out = [("wiki", config.ROOT / "wiki"),
           ("wikis", config.REPO_WIKI_DIR), ("sources", config.SOURCES_DIR),
           ("raw/inbox", config.GDRIVE_TARGET_REPO / "raw" / "inbox")]
    # v2-wiki  the V2 wiki root — the directory the V2 UI actually serves, and
    #          until now the one place absorb could write that nothing pushed,
    #          pulled or backed up. Registering it is what lets it live under
    #          var/ despite that directory being documented as disposable: the
    #          bucket holds the durable copy and pull() restores it at boot, so
    #          the local tree is a cache like every other tree here.
    #
    # Keyed off WIKI_ROOTS[0] rather than a path constant so the directory
    # absorb writes, the directory push/pull cover and the directory the UI
    # reads cannot drift apart — they are one setting.
    if config.WIKI_ROOTS:
        out.append(("v2-wiki", config.WIKI_ROOTS[0]))
    return out


@lru_cache(maxsize=1)
def _client():
    import boto3  # imported lazily: an unconfigured deploy needs no boto3
    from botocore.config import Config
    return boto3.client("s3", region_name=config.S3_REGION or None,
                        endpoint_url=config.S3_ENDPOINT or None,
                        config=Config(connect_timeout=10, read_timeout=30,
                                     retries={"max_attempts": 3}))


def _prefix(tree: str) -> str:
    return f"{config.S3_PREFIX.strip('/')}/{tree}/".lstrip("/")


def _md5(p: Path) -> str:
    h = hashlib.md5()
    h.update(p.read_bytes())
    return h.hexdigest()


def commit_of(data: bytes) -> str:
    """The commit an article was built from, or "" for anything else.

    Sources, manifests and JSON twins have no frontmatter and get no metadata —
    absence is correct there, not a failure to detect. The leading `---` is the
    test: without it, a prose line reading `built_from_commit: ...` anywhere in a
    plain document would be stamped on the object as if it were the build commit.
    """
    if not data.startswith(b"---\n"):
        return ""
    m = COMMIT_RE.search(data[:4000].split(b"\n---", 1)[0])
    return m.group(1).decode() if m else ""


def _key(rel_path: str) -> str:
    return f"{config.S3_PREFIX.strip('/')}/{rel_path}".lstrip("/")


def _require_s3() -> None:
    if not enabled():
        raise RuntimeError(
            "S3_BUCKET unset — sources/ are S3-only; there is no git fallback")


def head_etag(rel_path: str) -> str | None:
    """Current ETag of the object backing `rel_path`, or None if absent.

    The etag is opaque — multipart uploads make it a non-MD5 value, so it is
    only ever compared to another etag from S3, never to a locally computed
    hash. Citations store its first 8 chars and prefix-match, exactly like
    truncated git blob shas.
    """
    _require_s3()
    s3 = _client()
    try:
        return s3.head_object(Bucket=config.S3_BUCKET,
                              Key=_key(rel_path))["ETag"].strip('"')
    except s3.exceptions.ClientError:
        return None


# Enough for any markdown a feeder writes; the biggest source in this corpus
# is ~1 MB of meeting transcript.
MAX_INLINE_BYTES = 512 * 1024


def read_text(rel_path: str, limit: int = MAX_INLINE_BYTES) -> tuple[str, bool]:
    """Fetch a stored file as text, truncated at `limit`. Returns (text, cut).

    # ponytail: read through the server rather than letting the page fetch the
    # presigned URL itself, which would need CORS configured on the bucket.
    # Fine for markdown; revisit if something large ever needs streaming.
    """
    _require_s3()
    body = _client().get_object(Bucket=config.S3_BUCKET, Key=_key(rel_path))["Body"]
    data = body.read(limit + 1)
    return data[:limit].decode("utf-8", "replace"), len(data) > limit


def read_full(rel_path: str) -> str:
    """The whole object as text. Ingest needs every byte; read_text() truncates.

    Raises FileNotFoundError when the key is absent, so a caller can record a
    pointer whose source copy is missing instead of crashing the run.
    """
    _require_s3()
    s3 = _client()
    try:
        body = s3.get_object(Bucket=config.S3_BUCKET, Key=_key(rel_path))["Body"]
    except s3.exceptions.NoSuchKey:
        raise FileNotFoundError(rel_path) from None
    return body.read().decode("utf-8", "replace")


def list_rel_paths(prefix: str) -> list[str]:
    """Every key under S3_PREFIX/<prefix>, returned relative to S3_PREFIX, sorted."""
    _require_s3()
    full = _key(prefix)
    base = f"{config.S3_PREFIX.strip('/')}/".lstrip("/")
    out: list[str] = []
    for page in _client().get_paginator("list_objects_v2").paginate(
            Bucket=config.S3_BUCKET, Prefix=full):
        for obj in page.get("Contents", []):
            out.append(obj["Key"][len(base):] if base and obj["Key"].startswith(base) else obj["Key"])
    return sorted(out)


def presigned_url(rel_path: str, expires_seconds: int = 900) -> str:
    """Short-lived GET URL for a source object. Minted per click, never stored.

    The type is overridden on the REQUEST rather than fixed on the object:
    push() has always uploaded without a ContentType, so every object already
    in the bucket is stored as binary/octet-stream, and a browser given that
    downloads a PDF instead of rendering it. Overriding per request fixes the
    whole existing corpus at once, where re-uploading would fix only what is
    pushed from here on.
    """
    _require_s3()
    guessed, _ = mimetypes.guess_type(rel_path)
    params = {"Bucket": config.S3_BUCKET, "Key": _key(rel_path)}
    if guessed:
        params["ResponseContentType"] = guessed
        # Without this the browser still treats an octet-stream-ish response as
        # an attachment in some paths; `inline` is what makes the iframe render.
        params["ResponseContentDisposition"] = "inline"
    return _client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expires_seconds)


def pull() -> int:
    """Download every durable tree. Returns files written.

    Only writes a file whose content differs, so a restart with a warm volume
    costs a listing and nothing else. A cold volume (fresh deploy) instead
    needs every object fetched, and boto3 clients are thread-safe for exactly
    this — sequential downloads made a ~3,300-object first boot take 20+
    minutes; that shrunk to under two.
    """
    if not enabled():
        return 0
    s3, n = _client(), 0
    for tree, dest in trees():
        dest.mkdir(parents=True, exist_ok=True)
        prefix = _prefix(tree)
        to_fetch: list[tuple[str, Path]] = []
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=config.S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                rel = obj["Key"][len(prefix):]
                if not rel or rel.endswith("/"):
                    continue
                local = dest / rel
                if local.is_file():
                    # ETag is the MD5 for single-part uploads, which every
                    # article is (they are kilobytes). Quoted in the response.
                    if _md5(local) == obj["ETag"].strip('"'):
                        continue
                    # Content differs — but never let an older remote copy
                    # clobber a newer local one. Two instances absorbing at once
                    # would otherwise silently undo each other's paid work.
                    if local.stat().st_mtime > obj["LastModified"].timestamp():
                        log.warning("s3 pull: keeping newer local %s/%s", tree, rel)
                        continue
                to_fetch.append((obj["Key"], local))

        def _download(item: tuple[str, Path]) -> None:
            key, local = item
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(config.S3_BUCKET, key, str(local))

        if to_fetch:
            with ThreadPoolExecutor(max_workers=PULL_WORKERS) as pool:
                list(pool.map(_download, to_fetch))
        if to_fetch:
            log.info("s3 pull: %d files from s3://%s/%s",
                     len(to_fetch), config.S3_BUCKET, prefix)
        n += len(to_fetch)
    return n


def push() -> int:
    """Upload every changed file in every durable tree. Returns files written.

    Deliberately does NOT delete remote objects that vanished locally. A wiki is
    the expensive artifact; a bug that empties the local tree must not propagate
    that to the durable copy. Prune by hand, or with a lifecycle rule.
    """
    if not enabled():
        return 0
    s3, n = _client(), 0
    for tree, src in trees():
        if not src.is_dir():
            continue
        prefix, wrote = _prefix(tree), 0

        remote: dict[str, str] = {}
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=config.S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                remote[obj["Key"][len(prefix):]] = obj["ETag"].strip('"')

        for f in src.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(src)).replace("\\", "/")
            # Read once: the same bytes answer "has it changed" and "what commit
            # was it built from". _md5 would re-read the file for the second.
            data = f.read_bytes()
            if remote.get(rel) == hashlib.md5(data).hexdigest():
                continue
            extra = {"Metadata": {"commit": c}} if (c := commit_of(data)) else {}
            s3.upload_file(str(f), config.S3_BUCKET, prefix + rel, ExtraArgs=extra)
            wrote += 1
        if wrote:
            log.info("s3 push: %d files to s3://%s/%s",
                     wrote, config.S3_BUCKET, prefix)
        n += wrote
    return n
