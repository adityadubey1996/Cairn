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
curl -s localhost:8300/api/connectors/preflight | python3 -m json.tool
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
| **WhatsApp** | works | Steel browser + group titles |
| **LinkedIn** | works | Steel browser + thread URLs |
| Gmail | **not built** | — |
| Outlook · OneDrive · Teams | **not built** | — |
| Jira · Slack · Confluence | **not built** | — |

The unbuilt ones appear greyed out in the Connect screen. They are listed
because the UI catalogue is the product roadmap; there is no feeder behind them
in `REGISTRY`, so no amount of configuration will make them run. Adding one is
a `run()` in `feeders/<name>/sync.py` plus a single `REGISTRY` entry — see
[Adding a connector](#adding-a-connector).

---

## Google Drive and Google Chat

One OAuth client and one consent covers both. Scopes are read-only throughout:
the token cannot write, delete, send or post.

**1. Create a Google Cloud project** at
[console.cloud.google.com](https://console.cloud.google.com/projectcreate).

**2. Enable the APIs** you want — *APIs & Services → Library*:
- **Google Drive API** (for Drive)
- **Google Chat API** (for Chat)

**3. Configure the consent screen** — *APIs & Services → OAuth consent screen*.
Choose **External** unless you are on a Workspace domain and only you will use
it. Add yourself under **Test users**. It can stay in "Testing" forever for
personal use; you never need Google to verify the app.

**4. Create the client** — *Credentials → Create credentials → OAuth client ID*:
- Application type: **Web application**
- Authorised redirect URI, copied exactly:

```
http://localhost:8300/api/google/callback
```

That URI is an external contract registered byte for byte. If you run Cairn on
a different port, register that port instead and keep the path identical.

**5. Put the client in `.env`:**

```bash
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
```

**6. Restart the server, then press Connect** in the Connect screen. Google
asks for consent, the callback stores a refresh token at
`secrets/google-oauth.json` (gitignored, never logged, never returned through
the API), and the connector flips to configured.

### Choosing what Drive pulls

`GDRIVE_SOURCE_IDS` empty means *files you own*. That is deliberate: with no
IDs, "everything shared with me" is unbounded on a Workspace domain and the
first sync would never finish. To narrow it, paste folder or file IDs — the
string after `/folders/` or `/d/` in the URL:

```bash
GDRIVE_SOURCE_IDS=1a2B3cD4eF5gH6iJ,1zY9xW8vU7tS
GDRIVE_EXCLUDE=Archive/*,*/Personal/*
```

### If it stops working

A refresh token dies if you revoke access, change your password, or leave it
unused for six months (Testing-mode clients expire in 7 days). Cairn raises
`ReauthRequired` and surfaces it rather than syncing zero documents quietly — a
silent empty sync is indistinguishable from "no new files". Press Connect again.

---

## GitHub repos

Public repos need nothing. Add one from the **Repos** tab — it is not a
"connection", it has its own clone → graph → ingest → absorb lifecycle.

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

**4. Sign in once** — open the panel and scan the WhatsApp QR, or log into
LinkedIn. Cookies persist under `secrets/` so you do not repeat it every run.

---

## Links

No credentials. It follows URLs already present in content other connectors
brought in, and skips a `drive.google.com` link unless the Google connector is
connected. `LINKS_FETCH_CAP` bounds total fetch *attempts* per run — it is a
run-time valve, not a content filter.

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

---

## Adding a connector

The health screen, run history, status derivation and preflight are all
generic, so a new connector is two things:

1. `feeders/<name>/sync.py` exposing `run(project_id) -> (seen, written)`,
   writing into the target repo's `raw/inbox/`.
2. One `Connector(...)` entry in `REGISTRY` in
   [server/connectors.py](server/connectors.py) — including `requires=(...)`
   and `setup="..."`, which is what makes it appear in `/preflight` with
   honest instructions.

If it needs OAuth, give it an `OAuthSpec` instead of writing a router: the
generic consent routes at `/api/{provider}/authorize` and `/callback` serve
whatever declares one. A provider whose scopes a consent does not request must
not reuse that consent — that is how you end up claiming one connector is
connected while holding a different connector's token.
