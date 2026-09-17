#!/usr/bin/env python3
"""Tier-1 Search: Postgres full text over parts, free, no key needed.

Run: python3 -m server.pipeline.search "<query>" [--project default] [--limit 10]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.db import connect  # noqa: E402

SQL = """
SELECT p.id AS part_id, p.source_id, s.path, s.sha, s.source_type, p.n, p.total, p.anchor,
       ts_rank(p.tsv, websearch_to_tsquery('english', %(q)s)) AS score,
       ts_headline('english', p.body, websearch_to_tsquery('english', %(q)s),
                   'MaxWords=30, MinWords=15, MaxFragments=1') AS snippet
FROM brain_parts p
JOIN brain_sources s ON s.id = p.source_id
WHERE p.project_id = %(project_id)s
  AND p.tsv @@ websearch_to_tsquery('english', %(q)s)
ORDER BY score DESC, p.source_id, p.n
LIMIT %(limit)s
"""


def view_url(path: str, sha: str, anchor: dict) -> str:
    """The existing source view route mints a presigned URL per click; the
    fragment tells the browser which page to open. Browsers honour #page=N
    on PDFs natively, so no image pipeline is needed."""
    url = f"/api/sources/view?path={quote(path, safe='')}&etag={sha}"
    if anchor.get("pages"):
        url += f"#page={str(anchor['pages']).split('-')[0]}"
    return url


def search_parts(q: str, project_id: str, limit: int = 10) -> list[dict]:
    limit = max(1, min(limit, 500))
    with connect() as db:
        rows = db.execute(SQL, {"q": q, "project_id": project_id, "limit": limit}).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        r["score"] = float(r["score"])
        r["view_url"] = view_url(r["path"], r.pop("sha") or "", r["anchor"])
        out.append(r)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--project", default="default")
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()
    for h in search_parts(a.query, a.project, a.limit):
        print(f"{h['score']:.3f}  {h['path']}  part {h['n']}/{h['total']}  {h['anchor']}")
        print(f"       {h['snippet']}")
        print(f"       {h['view_url']}")
