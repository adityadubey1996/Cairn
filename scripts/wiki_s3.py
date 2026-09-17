#!/usr/bin/env python3
"""Move the wiki tree between local disk and S3.

The server pulls at boot and every 15 minutes, and pushes after each absorb, so
this is for the two things a running server cannot do: the first upload, and
looking at what is actually up there.

    python3 scripts/wiki_s3.py status          # what is local vs remote
    python3 scripts/wiki_s3.py push            # local -> s3   (the initial seed)
    python3 scripts/wiki_s3.py pull            # s3 -> local
    python3 scripts/wiki_s3.py status --env staging
    python3 scripts/wiki_s3.py versioning --enable   # keep every replaced copy

Environment is a key prefix, so the same bucket holds dev/ and staging/ and one
policy covers both. --env overrides S3_PREFIX for a single run, which is how you
promote: pull from dev, push to staging.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, storage  # noqa: E402


def versioning_state(s3) -> str:
    try:
        return s3.get_bucket_versioning(Bucket=config.S3_BUCKET).get(
            "Status", "Disabled")
    except Exception as e:
        return f"unknown ({str(e)[:60]})"


def versioning(enable: bool) -> int:
    """Report — or turn on — bucket versioning.

    storage.push() overwrites in place and never deletes, so without versioning
    the previous copy of an article is gone the moment it is re-absorbed. With
    it, every push keeps what it replaced and each stored version carries the
    commit it was built from in its metadata. This is the whole of the wiki's
    history mechanism; there is no snapshot prefix and nothing to prune by hand.

    Enabling is one-way in S3 (it can be suspended, never removed), so it is
    behind an explicit flag rather than done on first push.
    """
    s3 = storage._client()
    state = versioning_state(s3)
    if not enable:
        print(f"s3://{config.S3_BUCKET}  versioning: {state}")
        if state != "Enabled":
            print("  no history — a re-absorbed article overwrites its only copy.\n"
                  "  enable with: python3 scripts/wiki_s3.py versioning --enable")
        return 0
    if state == "Enabled":
        print(f"s3://{config.S3_BUCKET}  versioning: already Enabled")
        return 0
    s3.put_bucket_versioning(Bucket=config.S3_BUCKET,
                             VersioningConfiguration={"Status": "Enabled"})
    print(f"s3://{config.S3_BUCKET}  versioning: {versioning_state(s3)}")
    return 0


def status() -> int:
    local_dir = config.REPO_WIKI_DIR
    local = [p for p in local_dir.rglob("*.md")] if local_dir.is_dir() else []
    articles = [p for p in local if not p.name.startswith("_")]
    print(f"local   {local_dir}")
    print(f"        {len(articles)} articles, {len(local)} files")

    if not storage.enabled():
        print("\nS3_BUCKET is not set — the wiki lives on disk and in git only.")
        return 0

    prefix = f"{config.S3_PREFIX.strip('/')}/wikis/".lstrip("/")
    print(f"\nremote  s3://{config.S3_BUCKET}/{prefix}")
    try:
        s3 = storage._client()
        keys, total = 0, 0
        for page in s3.get_paginator("list_objects_v2").paginate(
                Bucket=config.S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys += 1
                total += obj["Size"]
        print(f"        {keys} objects, {total/1e6:.2f} MB")
        state = versioning_state(s3)
        print(f"        versioning: {state}"
              + ("" if state == "Enabled" else "  (no history — see `versioning --enable`)"))
    except Exception as e:
        print(f"        unreachable: {str(e)[:120]}", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("status", "push", "pull", "versioning"))
    ap.add_argument("--env", help="override S3_PREFIX for this run (dev, staging)")
    ap.add_argument("--enable", action="store_true",
                    help="versioning: turn it on (default is report only)")
    a = ap.parse_args()

    if a.env:
        config.S3_PREFIX = a.env

    if a.action != "status" and not storage.enabled():
        print("S3_BUCKET is not set — nothing to do.", file=sys.stderr)
        return 2

    if a.action == "status":
        return status()
    if a.action == "versioning":
        return versioning(a.enable)

    n = storage.push() if a.action == "push" else storage.pull()
    where = f"s3://{config.S3_BUCKET}/{config.S3_PREFIX.strip('/')}/wikis/"
    print(f"{a.action}: {n} files "
          f"{'->' if a.action == 'push' else '<-'} {where}")
    if n == 0:
        print("  (already in sync)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
