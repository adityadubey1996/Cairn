#!/usr/bin/env python3
"""Bounded connector diagnostics, without source/DB writes or absorption.

Configuration only (no network):
    python scripts/connector_check.py
Read-only provider probes (explicit network opt-in):
    python scripts/connector_check.py --connector gdrive --live --limit 2 --extract
    python scripts/connector_check.py --connector gmail --live --limit 2
    python scripts/connector_check.py --connector links --live --url https://example.com
    python scripts/connector_check.py --connector github --live --url https://github.com/onyx-dot-app/onyx

A successful probe proves access to a bounded sample, never a complete sync.
Token values and source bodies are never emitted. Google may refresh an access
token in memory. Browser probes inspect existing session state and do not open
a login session, click, or send messages.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import config, connectors


def _drive(limit: int, extract: bool) -> dict:
    from feeders.gdrive import sync

    fields = "nextPageToken,files(id,name,mimeType,modifiedTime,owners(displayName))"
    params = {"pageSize": limit, "fields": fields,
              "q": "'me' in owners and trashed = false and mimeType != 'application/vnd.google-apps.folder'"}
    response = sync._get_json(f"{sync.API}/files?{urllib.parse.urlencode(params)}")
    items = []
    for document in response.get("files", []):
        item = {"id": document["id"], "name": document["name"], "mime_type": document["mimeType"]}
        if extract and sync._wanted(document):
            normalized = {"id": document["id"], "name": document["name"],
                          "mime_type": document["mimeType"], "modified_time": document["modifiedTime"],
                          "authors": [owner["displayName"] for owner in document.get("owners", [])]}
            item["extracted_characters"] = len(sync.export_text(normalized))
        items.append(item)
    return {"items": items, "more_available": bool(response.get("nextPageToken"))}


def _chat(limit: int, extract: bool) -> dict:
    from feeders.chat import sync

    response = sync._get_json(f"{sync.API}/spaces?pageSize={limit}")
    items = []
    for space in response.get("spaces", []):
        item = {"id": space["name"], "name": space.get("displayName", space["name"]),
                "space_type": space.get("spaceType", "")}
        if extract and space.get("spaceType") == "SPACE":
            params = urllib.parse.urlencode({"pageSize": limit, "orderBy": "createTime desc"})
            messages = sync._get_json(f"{sync.API}/{space['name']}/messages?{params}").get("messages", [])
            item["sample_messages"] = len(messages)
            item["extracted_characters"] = sum(len(m.get("text", "")) for m in messages)
        items.append(item)
    return {"items": items, "more_available": bool(response.get("nextPageToken"))}


def _gmail(limit: int, extract: bool, query: str | None) -> dict:
    from feeders.gmail import sync

    selected_query = query if query is not None else getattr(config, "GMAIL_QUERY", sync.DEFAULT_QUERY)
    threads, more = sync.list_threads(selected_query, max_items=limit)
    items = []
    for thread in threads:
        item = {"id": thread["id"]}
        if extract:
            full = sync._get_json(f"threads/{urllib.parse.quote(thread['id'], safe='')}", {"format": "full"})
            title, body, authors, modified = sync.render_thread(full)
            item.update(name=title, extracted_characters=len(body), messages=len(full.get("messages", [])))
        items.append(item)
    return {"items": items, "more_available": more, "query": selected_query}


def _links(urls: list[str], limit: int) -> dict:
    from feeders.links import sync

    if not urls:
        raise ValueError("A links probe needs at least one explicit --url")
    items = []
    for url in urls[:limit]:
        kind, payload, authors = sync._dispatch_fetch(url)
        items.append({"url": url, "kind": kind, "extracted_size": len(payload)})
    return {"items": items, "more_available": len(urls) > limit}


def _github(urls: list[str], limit: int) -> dict:
    if not urls:
        raise ValueError("A GitHub probe needs an explicit public repository --url")
    items = []
    for url in urls[:limit]:
        parsed = urllib.parse.urlsplit(url)
        parts = parsed.path.strip("/").removesuffix(".git").split("/")
        if (parsed.scheme != "https" or parsed.netloc != "github.com"
                or len(parts) != 2 or not all(parts) or parsed.query or parsed.fragment):
            raise ValueError("Use a public https://github.com/owner/repo URL")
        result = subprocess.run(["git", "ls-remote", "--exit-code", url, "HEAD"],
                                capture_output=True, text=True, timeout=30,
                                env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"})
        if result.returncode:
            raise RuntimeError(f"Cannot read public repository {url}; git exited {result.returncode}")
        items.append({"url": url, "head": result.stdout.split()[0]})
    return {"items": items, "more_available": len(urls) > limit}


def check(kind: str, *, live: bool, limit: int = 3, extract: bool = False,
          urls: list[str] | None = None, query: str | None = None) -> dict:
    row = next(item for item in connectors.preflight() if item["id"] == kind)
    output = {"connector": kind, "configured": row["configured"], "ready": row["ready"],
              "missing": row["missing"], "status": "configured" if row["ready"] else "needs_setup",
              "sample_only": True, "sync_complete": False}
    if not live:
        return output
    if not row["ready"] and kind not in ("whatsapp", "linkedin"):
        output["setup"] = row["setup"]
        return output
    try:
        if kind in ("gdrive", "gchat", "gmail"):
            from feeders.google import auth
            output["account"] = auth.account()
        if kind == "gdrive":
            result = _drive(limit, extract)
        elif kind == "gchat":
            result = _chat(limit, extract)
        elif kind == "gmail":
            result = _gmail(limit, extract, query)
        elif kind == "links":
            result = _links(urls or [], limit)
        elif kind == "github":
            result = _github(urls or [], limit)
        elif kind in ("whatsapp", "linkedin"):
            from feeders.browser import sessions
            state = asyncio.run(sessions.status(kind))
            result = {"session_status": state["status"]}
            if state["status"] != "logged_in":
                output["status"] = state["status"]
        else:
            result = {"note": "Uploads need no provider account; staged extraction is tested during sync."}
        output.update(result)
        if output["status"] == "configured":
            output["status"] = "ok"
    except Exception as error:
        output.update(status="error", error=str(error)[:500])
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--connector", choices=["all"] + [c.id for c in connectors.REGISTRY], default="all")
    parser.add_argument("--live", action="store_true", help="Allow bounded read-only provider requests")
    parser.add_argument("--extract", action="store_true", help="Extract the sampled Google documents in memory")
    parser.add_argument("--limit", type=int, default=3, help="Sample size (1–10)")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--query", help="Gmail search query override")
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")
    if args.live and args.connector == "all":
        parser.error("Choose one --connector for live requests")
    kinds = [c.id for c in connectors.REGISTRY] if args.connector == "all" else [args.connector]
    results = [check(kind, live=args.live, limit=args.limit, extract=args.extract,
                     urls=args.url, query=args.query) for kind in kinds]
    print(json.dumps(results, indent=2))
    return 1 if any(row["status"] == "error" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
