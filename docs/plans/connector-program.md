# Connector program

How Cairn gets the rest of its connectors, and how each one is proven to work.

**The principle:** whatever you can open while logged in as yourself — personal or
work account — Cairn can read the same thing. Nothing is proxied through a service,
and every connector uses your own credentials.

---

## Status

| Connector | State |
|---|---|
| Upload, Links | works |
| GitHub repos | works (code and history; not issues or PRs) |
| Google Drive, Google Chat, Gmail | works (one OAuth client covers all three) |
| WhatsApp, LinkedIn | works, via a logged-in browser session |
| Jira, Confluence, Notion, Linear | batch 1 |
| GitLab, Bitbucket, Gitea | batch 2 |
| Slack | batch 3 |
| OneDrive, Outlook, SharePoint, Teams | batch 4 |
| Discord, Zoom, Fireflies, Dropbox, Box, local folder, website crawl | batch 5 |

**Already built, and inherited free by every new connector:** a durable Postgres job
queue with retries and cancellation (`server/jobs.py`), per-connection schedules —
interval or cron, with timezone and auto-absorb (`server/automation.py`), a
partial-failure signal so a half-failed run never advances its watermark
(`feeders/result.py`), per-connection scope validation (`feeders/options.py`), and a
bounded live diagnostic (`scripts/connector_check.py`).

---

## How a connector gets in

Each one takes the first of these that works.

| # | Route | Use when | Cost |
|---|---|---|---|
| 0 | **Vendor-hosted MCP server** — the vendor runs it, Cairn signs in and registers itself | The vendor hosts one, and batch 0b shows it can list everything with IDs, timestamps and authors | No app to register and no token to paste. The token only works through that MCP server |
| 1 | **Official API with your own login** — OAuth, device-code sign-in, or a token you create | The service lets users grant access themselves | Stable and fast, with fine-grained permissions |
| 2 | **Browser session** — you log in once in Steel; Cairn reuses that session | An admin blocks routes 0 and 1 | Breaks when the web app changes, and needs a fresh login when the session expires |
| 3 | **Export and upload** | always | Manual, one-off |

**A connector owns its own sign-in.** `server/signin.py` provides the browser half —
a loopback listener, over HTTPS when the provider demands it (Slack does; confirmed
that it accepts `https://localhost:3000/...`), with a self-signed certificate
generated per install. A connector adds `feeders/<id>/router.py` with its own
authorize and callback; the app mounts it and no shared file changes. What comes back
is stored through `server/credentials.py`, never in the connection's config row.

A pasted token stays the fallback, for providers we cannot register an app with and
for organisations that block third-party apps.

**Route 2 is opt-in per connection and labelled.** On a work account it reads the
same data you can already see, but it goes around an administrator's decision, so
it's the user's call, not a default. Discord gets no route 2: automating a user
account there is a bannable offence.

---

## The loop

1. **Build** — the connector's tests pass and `connector_check.py` works against
   recorded samples.
2. **Setup card** — what to create, where, and what a pass looks like.
3. **You verify on a real account:**
   ```bash
   .venv/bin/python scripts/connector_check.py --connector <id> --live --limit 2
   ```
   It signs in as you, lists a bounded sample, writes nothing, and prints no secret.
4. **PASS** ticks the row above. **FAIL** names the step — `config`, `login`,
   `permission`, `fetch` — and the fix.

Credentials never pass through the conversation. A successful probe proves access to
a bounded sample, never a complete sync.

---

## Batches

### Batch 0 — foundations (done)

The blocker for every paste-a-token connector: today all connections share one set of
credentials, so two Slack workspaces or two Jira sites cannot coexist.

- [x] `server/credentials.py` — per-connection secrets in `brain_settings` under
      `cred:<connection id>`, never in `brain_connector_connections.config`, which is
      the row every connection card is built from
- [x] `Field` on the registry (`name`, `label`, `secret`, `required`), plus `auth`
      per connector
- [x] `connections.create()` splits secret fields out, `remove()` drops them,
      `settings_for()` merges config and secrets for feeders
- [x] `NON_GITHUB_KINDS`, `AUTH_OF_KIND`, `pipeline_run.FEEDER` and `CONNECTORS` all
      derived from `REGISTRY`, so a connector is declared in exactly one place
- [x] Tests green with a database and without one (431)
- [x] Generic token form: `GET /api/connectors/catalogue` serves each connector's
      declared fields and `ConnectFlow.jsx` renders them, instead of hardcoding

**Done when:** adding a paste-a-token connector needs no UI edit and no
`connections.py` edit.

### Batch 0c — parallel-safe contract (done)

So several sessions can build connectors in one working tree without conflicting:

- [x] **Auto-discovery** — a folder becomes a connector by exporting `SPEC` from
      `feeders/<id>/connector.py`. `REGISTRY` is no longer a list anyone edits.
- [x] **Router auto-mount** — `feeders/<id>/router.py` exporting `router` is mounted
      by the app, so a connector adds endpoints without touching `app.py`.
- [x] **Server-driven gallery** — the Connect screen asks
      `GET /api/connectors/catalogue` what exists, so a new connector appears with no
      UI edit. A broken folder is logged and skipped, never fatal.
- [x] **`feeders/_template/`** — copy it; `README.md` there states the contract.

**What a session owns:** everything inside `feeders/<its-id>/`.
**What a session must not touch:** `server/`, `web2/`, `scripts/`, `CONNECTORS.md`,
`.env.example`, or another connector's folder. Per-connection settings belong in
`SPEC.fields` (stored in the database), never in `.env`.
**Provider docs** go in `feeders/<id>/SETUP.md`, not in `CONNECTORS.md`.

Two sessions in one tree therefore never edit the same file. When staging, stage your
own folder (`git add feeders/<id>`) rather than everything, so you don't pick up
another session's half-finished work.

### Batch 0b — MCP spike (you run it)

`scripts/mcp_probe.py` signs into a vendor-hosted MCP server with your own account,
lists its tools, pages through one, and reports whether the output can feed citations.

Route 0 is adopted for a vendor only if all four hold:

| # | Criterion | Pass |
|---|---|---|
| 1 | Login | A self-registered localhost client signs in with your own account, no admin step |
| 2 | Completeness | A tool filters by "updated since" and pages to the end; the total matches the vendor's own UI for the same filter |
| 3 | Fidelity | Every item keeps an ID, a last-updated timestamp and an author, and bodies are not truncated |
| 4 | Throughput | Five pages with no rate-limit error |

Tested against Atlassian (`https://mcp.atlassian.com/v2/mcp`) and Notion. **You
need:** an Atlassian Cloud site and a Notion workspace.

**Why before batch 1:** if MCP passes, Jira, Confluence, Notion and Linear become
thin adapters and batch 1 roughly halves.

### Batch 1 — Jira, Confluence, Notion, Linear

Paste-a-token, so no admin approval. One Atlassian API token covers Jira and
Confluence. Notion needs pages shared with the integration, which fails silently, so
the check reports "0 pages shared" explicitly.

### Batch 2 — code hosts and issue trackers

Two separate pieces, and only one of them is parallel-safe:

- **GitHub issues, PRs** and **GitLab issues, MRs** are ordinary connector folders.
  Note that today's GitHub connector reads code and history only — issues and pull
  requests are a different connector.
- **Cloning from GitLab, Bitbucket or Gitea is not.** It needs `server/repos.py`,
  where `URL_RE` accepts `github.com` alone and the whole path assumes `owner/name`,
  which cannot express GitLab's nested groups. That file is the trust boundary for a
  browser-supplied host, so one session owns it, running alone.

### Batch 3 — Slack

Route 1 is your own Slack app's user token, installed to your own workspace. Route 2
reuses the browser session, needing no app and no admin, and is worth building if the
2025 rate-limit change makes route 1 too slow to be useful. Both write the same
day-per-channel files Google Chat and WhatsApp already produce.

### Batch 4 — Microsoft

One device-code sign-in module, then OneDrive, SharePoint, Outlook and Teams. Reading
Teams channels needs admin approval; personal Microsoft accounts have no Teams access
at all. **Decision needed before this batch:** one shipped Cairn app that everyone
signs into, or every user registers their own.

### Batch 5 — long tail

Discord (a server admin must add the bot), Zoom and Fireflies transcripts, Dropbox and
Box, a watched local folder, and a website crawler. Build only the ones actually used.

### Batch 6 — showcase

- **In-app gallery** — every connector with live status, schedule, last sync and item
  counts, plus a per-connector setup card and schedule editing. The Connect screen
  already covers part of this.
- **Demo mode** — seed data so a fresh clone shows a populated wiki and working search
  without connecting any account.

---

## Rules that apply to every batch

- **Your own identity, never an admin's.** Onyx, the closest reference implementation,
  uses app-only sign-in for Teams and SharePoint, a bot token for Slack and a service
  account for Gmail. Its fetching and parsing are worth porting; its auth is not.
- **Read-only everywhere.** No connector writes, sends, posts or deletes.
- **Secrets** never enter `brain_connector_connections.config`, never appear in an API
  response, and are replaced with `***` in errors.
- **Porting code:** Onyx (MIT outside `ee/`) and SurfSense (Apache-2.0 outside
  `app/proprietary/`) only, with attribution at the top of the file. Never from an
  `ee/` or `proprietary/` directory, and never from an AGPL project such as slackdump
  — reference only.
- **Scope before the first sync.** A connection says which channels, spaces or
  projects to read, or a large account syncs unbounded. This is the lesson
  `GDRIVE_SOURCE_IDS` already encodes.
- **Back off on 429** using `Retry-After`, in every feeder.
- **No new dependency** for what a few lines of `urllib`/`httpx` can do. MCP work uses
  the `mcp` package browser-use already pins (1.26.0); installing 2.x breaks the
  WhatsApp and LinkedIn connectors.

**Test command, green both ways:**

```bash
.venv/bin/python -m pytest server pipeline feeders scripts -q
DATABASE_URL=postgresql://postgres:postgres@localhost:59999/none \
  .venv/bin/python -m pytest server pipeline feeders scripts -q
```

---

## Known limits worth stating

- **Google Chat is work-accounts-only.** The Chat API needs a Workspace Business or
  Enterprise account and a Chat app configured in the Cloud project. No login approach
  changes that.
- **Google Drive and Gmail use restricted scopes.** Until Google verifies an app, it
  shows an unverified-app warning and is capped at 100 users; in Testing mode the
  sign-in also expires every 7 days. A shipped "Desktop app" client would remove the
  per-user Cloud Console setup and work on any port, at the cost of that verification.
- **Teams channel messages require admin approval**, always.
- **An MCP token only works with its MCP server**, so a vendor's MCP sign-in cannot be
  reused against that vendor's normal API.

## Adding a connector

One `run()` in `feeders/<name>/sync.py` plus one `REGISTRY` entry in
`server/connectors.py`. Health, run history, scheduling, the job queue and preflight
are all generic. See [CONNECTORS.md](../../CONNECTORS.md#adding-a-connector).
