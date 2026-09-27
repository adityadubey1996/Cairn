# Notion setup

Two things happen at Notion's end: you create an integration, and you share pages
with it. **Skipping the second one is the common failure** — the API then answers
every request with an empty list and no error at all, so it looks as though the
workspace is empty.

Cairn reads page titles and their block content, and nothing else. Read-only
throughout: step 1 turns the integration's write capabilities off, and an
integration that cannot write cannot be made to.

There is no redirect URL and no OAuth client here — an internal integration hands
you a secret directly, so nothing in this setup depends on the port Cairn runs on.

**Time:** about 5 minutes, plus a minute per section you share.

---

## Watch, or read

- [How to Create a Notion API Key in Under 2 Minutes! (2026)](https://www.youtube.com/watch?v=d4UeQVHB0vo) — two minutes, English

It covers step 1 on the current *notion.so/my-integrations* screens, through to
copying the secret. Two differences from what Cairn needs: turn **Update content**
and **Insert content** *off* rather than leaving the defaults, and then do step 3 —
sharing pages with the integration is the step that otherwise costs you an hour of
looking at an empty sync.

---

## 1. Create the integration

1. Go to <https://www.notion.so/my-integrations> and click **New integration**.
2. Name it something you will recognise later (for example `Cairn`) and pick the
   workspace you want read.
3. Under **Capabilities**, leave **Read content** on. Turn **Update content** and
   **Insert content** *off* — this connector never writes, and an integration
   that cannot write cannot be made to.
4. Leave **Read user information** on if you want edits attributed to people by
   name. Without it the sync still works; author lines show the raw Notion user
   id instead.
5. Copy the **Internal Integration Secret** (it starts with `ntn_`).

## 2. Paste the secret

On the Connect screen, add a Notion connection and paste the secret into
**Internal integration secret**. Nothing else is asked for.

## 3. Share the pages you want read

For each top-level page or database:

1. Open it in Notion.
2. **⋯** (top right) → **Connections** → **Connect to** → your integration.
3. Confirm.

Sharing a page shares everything nested underneath it, so one share at the top of
a section is usually enough. A **database has to be shared in its own right** —
sharing the page it is embedded in does not cover it.

Nothing is read until you do this. If you see "no pages are shared with this
integration" from the connection check, this step is what is missing.

## What gets read

One source file per page: the page title, and its block tree rendered as
Markdown (headings, lists, to-dos, quotes, callouts, toggles, code, tables,
and the captions or URLs of embedded files). Nested blocks are followed six
levels deep.

Files and images attached to a page are named and linked, but their bytes are
not downloaded.

Re-syncs are incremental: only pages edited since the last successful sync are
re-read, and a page whose rendering is byte-identical is not rewritten.

## Checking it works

```
.venv/bin/python scripts/connector_check.py --connector notion --live --limit 2
```

This authenticates, lists up to two pages, and writes nothing.
