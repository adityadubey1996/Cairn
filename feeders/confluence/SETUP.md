# Confluence setup

Cairn signs in to Confluence with **Atlassian OAuth 2.0 (3LO)** — you press Connect,
Atlassian asks for consent, and the browser comes back signed in. There is no token to
copy around. Read-only throughout: the scopes granted cannot create, edit, move or
delete anything.

You register the app once, on localhost. **Time:** about 5 minutes.

---

## 1. Create the OAuth app

[developer.atlassian.com/console/myapps](https://developer.atlassian.com/console/myapps)
→ **Create** → **OAuth 2.0 integration**. Name it anything; `Cairn` is fine.

## 2. Add the Confluence permissions

*Permissions* → **Confluence API** → *Add* → *Configure*. Under **Granular scopes**
add exactly these:

| Scope | Why |
|---|---|
| `read:page:confluence` | the pages themselves |
| `read:space:confluence` | resolving the space keys you type into space ids |
| `read:user:confluence` | editor display names — without it authors show as account ids |

Then *User identity API* → *Add*, and add `read:me`.

`offline_access` is requested automatically and does not appear in this list. It is
what makes the sign-in survive longer than an hour, so nothing to do here.

## 3. Set the callback URL — you only get one, so pin the port

*Authorization* → **OAuth 2.0 (3LO)** → *Configure*, then fill in **Callback URL**.

Cairn builds the redirect from the address in your browser's bar, so the port
changes with how you started it:

```
from source (default PORT):   http://localhost:8300/api/confluence/callback
under Docker:                 http://localhost:8301/api/confluence/callback
custom PORT=9000:             http://localhost:9000/api/confluence/callback
```

A 3LO app takes a **single** callback URL — you cannot register all three the way
you can on a Google client, and Atlassian's answer to wanting more has been to
create a second app. Paste the one line that matches how you will actually run
Cairn, then keep that run mode fixed: set `PORT` in `.env` and leave it alone.
Moving between source and Docker later means editing this field in the console.

> Since early 2026 the console has been reported to accept more than one URL in
> that field, with no announcement and no documentation change. If yours does, add
> all three. Treat it as a bonus — the documented behaviour is still one URL.

**How Atlassian matches it.** Byte for byte: scheme, host, port and path.

- **`localhost`, never `127.0.0.1`.** The two spellings are different strings, so
  registering one does not cover the other. Open Cairn at `http://localhost:8300`,
  not `http://127.0.0.1:8300`.
- **`http` is correct for localhost.** Atlassian accepts it there and only there.
- **No trailing slash.** `…/callback/` is not `…/callback`.

**When it does not match**, Atlassian refuses before the consent screen and drops
you on `id.atlassian.com/error` with `error=unauthorized_client` and:

```
redirect_uri is not registered for client: http://localhost:8301/api/confluence/callback
```

The URI in that message is the one Cairn sent. If it is not character-identical to
the Callback URL on the app, that is the whole bug — usually the port.

**On a non-default port.** Change the callback URL in the console to the new port.
The path after the host never changes: it is always `/api/confluence/callback`.

## 4. Put the client credentials in `.env`

The *Settings* page of your app shows a **Client ID** and a **Secret**. In the
repository root, in `.env`:

```bash
CONFLUENCE_CLIENT_ID=...
CONFLUENCE_CLIENT_SECRET=...
```

> These two are **not** yet in `.env.example` — add them by hand. The Connect screen
> names them as missing until they are set.

Restart Cairn so it reads them.

## 5. Press Connect

Connect screen → **Confluence** → **Connect**. Atlassian asks which site to grant and
shows the permissions; approve, and the browser returns to Cairn with the connection
made. The refresh token lands in `secrets/confluence-oauth.json`: gitignored, mode
600, never logged and never returned through the API.

## Checking it worked

```bash
.venv/bin/python scripts/connector_check.py --connector confluence --live --limit 2
```

It names the signed-in account and site, lists two pages, and writes nothing.

---

## Notes that save time later

- **Server / Data Center is not supported.** This connector speaks the Confluence
  Cloud v2 API through `api.atlassian.com`. A self-hosted `confluence.yourcompany.com`
  has neither.
- **One site per install.** If your account can reach several Atlassian sites, the
  consent screen asks you to pick one, and the first one granting Confluence access is
  the one used. To switch sites, press Connect again and grant a different one.
- **Your admin may have to approve the app** before the consent screen offers a
  company site. Personal sites need no approval.
- **`invalid_scope` on the consent screen** means step 2 is incomplete — the app is
  asking for a scope you have not added to it.
- **Adding a scope later needs a new consent.** Press Connect again; the old grant
  does not widen by itself.
- **Which spaces get read.** Everything the signed-in account can see. To narrow it,
  set `spaces` on the connection's config row to comma-separated space keys
  (`ENG, DESIGN`) — the sign-in button has nowhere to ask for them.
- **The first sync is the slow one**, and today so is every sync after it: the
  pipeline does not yet hand this connector its watermark, so each run re-lists every
  page. Nothing is rewritten — unchanged pages are skipped on a content hash — but the
  listing itself costs time on a large site.
