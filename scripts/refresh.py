#!/usr/bin/env python3
"""Pull every tracked repo and rebuild its knowledge graph and entries.

The repo list comes from the same table the Repos screen reads, so whatever you
added in the UI is what this refreshes. No server needed — it talks to Postgres
directly, which is what makes it safe to cron.

    python3 scripts/refresh.py                 # free: pull, graph, ingest
    python3 scripts/refresh.py --absorb 5      # ...then buy up to 5 articles each
    python3 scripts/refresh.py --repo your-org/your-repo
    python3 scripts/refresh.py --dry-run

Absorb is off by default and always capped. It is the only step that spends
money, so a timer must never trigger it implicitly — pass --absorb to opt in.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, connectors, repos  # noqa: E402

FREE = ("clone", "graph", "ingest")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", action="append",
                    help="only this repo id (repeatable); default is all tracked")
    ap.add_argument("--absorb", type=int, metavar="N",
                    help="after ingest, absorb up to N units per repo (COSTS MONEY)")
    ap.add_argument("--kind", default="code_package",
                    help="unit kind to absorb (default code_package)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    connectors.ensure_rows()
    rows = repos.list_repos()
    if a.repo:
        want = {r.lower() for r in a.repo}
        rows = [r for r in rows if r["id"] in want]
        missing = want - {r["id"] for r in rows}
        for m in sorted(missing):
            print(f"  ! not tracked: {m}", file=sys.stderr)
    if not rows:
        print("nothing tracked — add a repo first")
        return 0

    if a.absorb and not config.GROQ_API_KEY:
        print("GROQ_API_KEY is not set — cannot absorb", file=sys.stderr)
        return 2

    failures = 0
    for r in rows:
        rid = r["id"]
        if r["pinned_sha"]:
            # Parked on a commit-filtered preview. Refreshing would drag it back
            # to HEAD and quietly discard what the user was looking at.
            print(f"{rid}: pinned at {r['pinned_sha'][:8]}, skipping")
            continue
        if a.dry_run:
            print(f"{rid}: would run {', '.join(FREE)}"
                  + (f", absorb {a.absorb} × {a.kind}" if a.absorb else ""))
            continue

        print(f"{rid}:")
        try:
            before = r["head_sha"]
            repos.run_step(rid, "clone")
            after = (repos.get(rid) or {}).get("head_sha")
            if after == before and r["state"] in ("ingested", "ready"):
                print(f"  up to date at {str(after)[:8]}")
                continue
            for step in ("graph", "ingest"):
                repos.run_step(rid, step)
            row = repos.get(rid) or {}
            print(f"  {str(after)[:8]}  queue: {row.get('queue_new')} new, "
                  f"{row.get('queue_changed')} changed")

            if a.absorb:
                out = repos.run_step(rid, "absorb", limit=a.absorb, kind=a.kind)
                print(f"  absorbed → {out.get('articles')} articles total")
        except repos.Busy as e:
            print(f"  busy: {e}")
        except Exception as e:
            # One bad repo must not stop the sweep; run_step already recorded
            # the error on the row and on its run.
            failures += 1
            print(f"  FAILED: {str(e)[:200]}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
