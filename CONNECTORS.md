# Connectors

A connector pulls content you already have access to — Drive documents, chat
history, a repo — into Cairn so it can be turned into cited wiki articles.

**The rule this page exists to explain:** Cairn has no accounts, no cloud, and
no credentials of its own. Every connector runs on your machine using *your*
own OAuth client or token. Nothing is proxied through a service, and nothing
here phones home. That is also why setup is not one click: for the Google
connectors you create the OAuth client that would normally belong to a vendor.

Ask the running server what is still missing rather than guessing:

```bash
# Docker default. Use 8300 when running from source.
curl -s localhost:8301/api/connectors/preflight | python3 -m json.tool
```

Every connector reports `ready`, the env vars still `missing`, and a one-line
`setup`. It is generated from the code that actually reads those variables
([server/connectors.py](server/connectors.py)), so it cannot drift from reality.

---

## What is actually built

| Connector | Status | Needs |
|---|---|---|
| **Upload** (files/folders) | works | nothing |
| **Links** | works | nothing |
| **GitHub repos** | works | nothing public · `GITHUB_TOKEN` for private |
| **Google Drive** | works | your own Google OAuth client |
| **Google Chat** | works | the same Google OAuth client |
| **Gmail** | implemented; complete email threads | Google OAuth + Gmail API + mail consent |
| **WhatsApp** | browser integration; requires live login | Steel browser + group titles |
| **LinkedIn** | browser integration; requires live login | Steel browser + thread URLs |
| Outlook · OneDrive · Teams | **not built** | — |
| Jira · Slack · Confluence | **not built** | — |

The unbuilt integrations have no feeder in `REGISTRY`. Their presence in a
catalogue is a roadmap entry, not a working connection.

The Gmail adapter follows the complete-thread boundary studied in
[Onyx's Gmail connector](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/gmail/connector.py),
implemented here against Google's REST API and Cairn's source/inbox format.
This is a selected adaptation: Onyx's entire connector factory, credential
system, permission sync and indexing runtime are not integrated. Adding another
provider still needs implementation and testing in this repository.

---

## Google Drive, Google Chat and Gmail

One OAuth client and one consent cover these Google integrations. Scopes are read-only throughout:
the token cannot write, delete, send or post.

**1. Create a Google Cloud project** at
[console.cloud.google.com](https://console.cloud.google.com/projectcreate).

**2. Enable the APIs** you want — *APIs & Services → Library*:
- **Google Drive API** (for Drive)
- **Google Chat API** (for Chat)
- **Gmail API** (for email)

**3. Configure the consent screen** — *APIs & Services → OAuth consent screen*.
Choose the audience appropriate to your Google account and organization. For
an External application in testing, add your account under **Test users**.
Testing grants can expire, so unattended operation may require renewing consent
or completing the Google publishing requirements for your application.

**4. Create the client** — *Credentials → Create credentials → OAuth client ID*:
- Application type: **Web application**
- Authorised redirect URI, copied exactly for the way Cairn runs:

```
Source: http://localhost:8300/api/google/callback
Docker: http://localhost:8301/api/google/callback
```

The selected URI is an external contract registered byte for byte. Add its
matching origin without the callback path. If you run Cairn on a different
port, register that port instead and keep the path identical.

**5. Put the client in `.env`:**

```bash
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
# Optional: reject a consent response from a different account.
GOOGLE_ACCOUNT_EMAIL=you@example.com
```

**6. Restart the server, then press Connect** in the Connect screen. Google
asks for consent, the callback stores a refresh token at
`secrets/google-oauth.json` (gitignored, never logged, never returned through
the API), and the connector flips to configured.

Existing Drive/Chat tokens do not automatically gain Gmail access. Connect again
and grant read-only Gmail access. The saved granted scopes determine whether the
Gmail connector is ready. All Google connections currently share one token;
independent work/personal Google accounts are not supported simultaneously.

### Choosing what Drive pulls

`GDRIVE_SOURCE_IDS` empty means supported text-like files you own plus visible
meeting-transcript-shaped documents shared by others. Generic shared files,
Sheets and Slides are not swept automatically. To narrow it, paste folder or file IDs — the
string after `/folders/` or `/d/` in the URL:

```bash
GDRIVE_SOURCE_IDS=1a2B3cD4eF5gH6iJ,1zY9xW8vU7tS
GDRIVE_EXCLUDE=Archive/*,*/Personal/*
```

Connection configuration can override `source_ids` and `max_items`. Renaming a
Drive file keeps its existing citation path. Incremental folder walks traverse
unchanged folders to find updated documents below them.

### Choosing what Gmail pulls

The default query is `newer_than:90d -in:spam -in:trash`; set `GMAIL_QUERY` or a
connection's `query` to choose labels, senders or another time window. A matching
thread is always fetched in full, preserving earlier messages when a reply
arrives. The source includes senders, recipients, dates, subject, readable MIME
text and attachment names. Attachment bytes are not downloaded or extracted.

`max_items` limits a run's thread count. A capped run reports partial and leaves
its watermark unchanged; narrow the query or raise/remove the cap to finish
that window. Source identifiers include the project and mailbox. The current
sync does not reconcile deleted messages or permission revocations.

### Google Chat coverage

Chat reads named `SPACE` conversations, their messages, rich links and supported
attachments. Direct messages and ordinary group chats are excluded. Full
space histories are reconciled each run so earlier messages and edits survive
daily-file regeneration; this can take time on large accounts. Images are kept
as originals with a descriptive entry, without image understanding.

For a bounded sample, a connection's `max_items` caps complete day entries and
attachments, and limits how many spaces are inspected. Each sampled space has
a five-page message budget; an oversized day fails safely instead of replacing
its transcript with partial content. Skipped work marks the run incomplete and
leaves its watermark unchanged. Set `max_items` to `0` for a full reconciliation.

### If it stops working

A refresh token dies if you revoke access, change your password, or leave it
unused for six months (Testing-mode clients expire in 7 days). Cairn raises
`ReauthRequired` and surfaces it rather than syncing zero documents quietly — a
silent empty sync is indistinguishable from "no new files". Press Connect again.

---

## GitHub repos

Public repos need no token. Add one from the **Repos** tab. Sync is queued as a
durable job through clone → graph → ingest, followed by absorption when enabled
for that repository. This connector reads repository files and history; it does
not import GitHub issues, pull-request discussions or account notifications.

A private repo needs a token, and a token is the *only* way in: git is scrubbed
of every ambient credential (`GIT_TERMINAL_PROMPT=0`, no system config, no
askpass) so a private repo fails fast as "not reachable" instead of quietly
succeeding through whatever login happens to be on the machine.

Create a **fine-grained PAT** with **Contents: read-only** at
[github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens),
then either paste it per repo in the UI, or set the fallback for all of them:

```bash
GITHUB_TOKEN=github_pat_...
```

It is embedded in the clone URL and nowhere else — never in the database, never
in a run row, never in an HTTP response, and redacted from every error surface.

A repo already checked out on your machine needs no token at all: point Cairn at
the path and git clones from the directory like any other remote, full history
intact. The path must sit inside `REPO_LOCAL_ROOTS`, because a path arriving
from a browser is otherwise arbitrary filesystem read.

---

## WhatsApp and LinkedIn

These drive a **real logged-in browser session** through
[Steel](https://github.com/steel-dev/steel-browser). There is no API involved:
extraction is deterministic CDP, and an LLM is used only as a navigation
fallback. Read the terms of the service you point it at — scraping your own
message history is not the same thing as being allowed to automate it.

**1. Start Steel:**

```bash
docker compose up -d steel-api
```

**2. Configure what to capture.** The titles must match WhatsApp Web's sidebar
*exactly* — copy them from the live sidebar, because a near-miss silently
captures nothing:

```bash
WHATSAPP_GROUPS=Team standup,Project Falcon
WHATSAPP_SINCE=2026-01-01
WHATSAPP_DATE_ORDER=DMY          # or MDY, matching your account's locale

LINKEDIN_THREADS=https://www.linkedin.com/messaging/thread/2-abc123/
```

**3. Set a password for the live-browser panel.** The panel shows a logged-in
session, so it stays hidden until this matches. Empty means nobody can reveal
it:

```bash
BROWSER_VIEW_PASSWORD=something-only-you-know
```

**4. Sign in** — open the panel and scan the WhatsApp QR, or log into LinkedIn.
LinkedIn cookies persist under `secrets/` and can expire. WhatsApp relies on the
live browser's IndexedDB; restarting Steel requires scanning the QR again.
Selectors and scrolling limits can affect coverage when these websites change.

---

## Links

Public website URLs need no account. A connection can supply `urls`; those
pages are fetched directly without crawling their child links. Without an
explicit URL list, the feeder discovers URLs in other sources and follows one
additional hop. Drive URLs use the connected Google credential.
`LINKS_FETCH_CAP` and an optional connection `max_items` bound attempts per run.
Login walls, access restrictions and unreachable pages can still fail extraction.

Pages that plain HTTP cannot read (JS-rendered) can fall back to Steel:

```bash
STEEL_ENABLED=1
LINK_BROWSER_MAX=200             # a page load is seconds where urllib is ms
```

Off by default so an install without Steel behaves exactly as before.

---

## Upload

No credentials and no account. Drag files or a folder into the Connect screen.
This is the fastest way to see the whole pipeline work before wiring any OAuth.
Failed extractions remain in staging for retry; the only uploaded copy is not
discarded on failure.

DOCX, XLSX and PPTX use installed Python readers, with pandoc preferred for
DOCX when available. XLSX extraction includes every worksheet and labels formula
expressions without recalculating them. PPTX includes slide text, tables and
speaker notes. Text PDFs use pdftotext or pypdf. These readers do not interpret
images, charts or drawings; scanned uploads without a text layer report an
extraction failure. Drive PDF fetching additionally supports Tesseract OCR when
the system tools are installed. Uploaded Office files in Drive use the same
readers as local uploads.

## Verify configuration and access

The diagnostic command reads configuration without contacting providers:

```bash
.venv/bin/python scripts/connector_check.py
```

Use `--live` for an explicitly selected provider and a bounded read-only sample:

```bash
.venv/bin/python scripts/connector_check.py --connector gdrive --live --limit 1 --extract
.venv/bin/python scripts/connector_check.py --connector links --live --url https://www.python.org/about/
.venv/bin/python scripts/connector_check.py --connector github --live --url https://github.com/octocat/Hello-World
```

The output reports configuration, access, extraction sizes and partial/error
state without printing tokens or source bodies. It does not save sources,
write the database or generate wiki articles. Browser checks inspect existing
login sessions and do not create or navigate one. A successful probe validates
that sample; it is not evidence of a complete account synchronization.

---

## Adding a connector

The health screen and preflight share a registry. A complete integration needs:

1. `feeders/<name>/sync.py` exposing `run(project_id, connection_id, on_progress)`
   and returning `feeders.result.SyncResult`. It remains compatible with
   `(seen, written)` unpacking and also carries `failed`, `failures` and `complete`.
   Write canonical `sources/...` references and `raw/inbox/` entries; use
   `pipeline.source_files.source_path()` for the configured physical source volume.
2. One `Connector(...)` entry in `REGISTRY` in
   [server/connectors.py](server/connectors.py) — including `requires=(...)`
   and `setup="..."`, which is what makes it appear in `/preflight` with
   honest instructions.
3. Connection-kind, pipeline-runner and UI wiring, with a configuration
   allowlist, appropriate source scope, offline tests and a live access probe.
   Preserve source identity, failures and retryability; incomplete scrapes must
   not advance their watermark.

If it needs OAuth, give it an `OAuthSpec` instead of writing a router: the
generic consent routes at `/api/{provider}/authorize` and `/callback` serve
whatever declares one. A provider whose scopes a consent does not request must
not reuse that consent — that is how you end up claiming one connector is
connected while holding a different connector's token.
