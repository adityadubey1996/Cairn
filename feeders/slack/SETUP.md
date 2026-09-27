# Slack setup

You create a Slack app once, in your own workspace. Slack has no dynamic client
registration, so there is no way around that first step — but you paste its
**client ID and secret**, never a token. The token comes from clicking Sign in.

Read-only throughout: every scope below is a `:history` or `:read` scope. Nothing
Cairn is granted can post, edit, delete or join anything.

An app you build for your own workspace needs **no Slack Marketplace review**.
Your workspace may still require an admin to approve the install; if so, Slack
shows a "request approval" screen instead of the Allow button at step 3, and an
admin has to clear it before you can finish.

**Time:** about 10 minutes, once.

---

## Watch, or read

- [How to Find Slack Client ID & Client Secret | Slack App Setup Guide 2025](https://www.youtube.com/watch?v=LCkmofX9gck) — one minute, English

It walks the current `api.slack.com/apps` console from **Create New App** to the
**Client ID** and **Client Secret** on *Basic Information*. It stops there: it
does not touch Redirect URLs or User Token Scopes, and those two are the steps
Cairn specifically needs. Do step 1 below in full.

---

## 1. Create the app

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
2. Name it (for example `Cairn`) and pick your workspace.
3. **OAuth & Permissions** → **Redirect URLs** → **Add New Redirect URL**:

   ```
   https://localhost:3000/slack/callback
   ```

   Save it. See the next section for why this one is `https`, and why — unlike
   every other connector here — it does not change with how you run Cairn.

4. Same page, **Scopes** → **User Token Scopes** (*not* Bot Token Scopes). Add:

   ```
   channels:history  channels:read
   groups:history    groups:read
   im:history        mpim:history
   users:read
   ```

   A *user* token reads what you can already read, so the app never has to be
   added to a channel and never appears as a member.

5. **Basic Information** → **App Credentials**: copy the **Client ID** and the
   **Client Secret**.

### The redirect URL — one URL, and it never changes

Every other connector's redirect URL carries the port Cairn is served on, which
is why they break when you move between running from source and running under
Docker. Slack is the exception. The Slack sign-in does not use Cairn's own web
port at all: it starts a throwaway listener of its own, always on port 3000, and
that is the only URL Slack ever redirects to:

```
https://localhost:3000/slack/callback
```

Register that one string. It is the same whether Cairn is on 8300, 8301 or a
`PORT` you picked yourself, so there is nothing to add later. What *does* move
with the port is the address you open to **start** the sign-in — see step 3.

**Why `https`, and why the certificate warning.** Slack's documentation is
unambiguous: "A Redirect URL must also use HTTPS." There is no localhost
exception, no development exception, and no exception for an internal app that
never goes near the Marketplace. Since no public authority will certify
`localhost`, Cairn generates a self-signed certificate on your machine
(`secrets/loopback-tls/`, once per install) and your browser warns about it once.
That warning is the cost of Slack's rule, not a sign anything is wrong.

**How Slack matches it.** The `redirect_uri` must equal a registered Redirect
URL, or be a subdirectory of one. Scheme, host, port and path all count.

**When it does not match**, the browser lands on a Slack error page reading:

```
OAuth Error: redirect_uri did not match any configured URIs.
```

If the mismatch survives to the token exchange instead, Slack's API answers
`bad_redirect_uri` — "Value passed for `redirect_uri` did not match the
`redirect_uri` in the original request."

**On a non-default port.** Nothing to do. Changing `PORT` does not change this
URL. If port 3000 is already taken on your machine — Steel's default host port is
3001, so that one does not collide — the sign-in fails to bind, and the port is
fixed in `feeders/slack/router.py` rather than configurable.

## 2. Create the connection

On the Connect screen, add a Slack connection and fill in:

- **Slack app client ID**
- **Slack app client secret**
- **Channels to read** — optional. Comma-separated names (`general, engineering`).
  Leave it empty to read every channel you are in.

## 3. Sign in

Open this in the browser **on the machine running Cairn**, with your connection's
id in the path. This one *is* on Cairn's own port, so use the line matching how
you started it:

```
from source (default PORT):   http://localhost:8300/api/slack/signin/<connection id>
custom PORT=9000:             http://localhost:9000/api/slack/signin/<connection id>
```

> **Run from source for this step.** Under `docker compose` the sign-in cannot
> complete: the listener binds port 3000 *inside* the container, which the compose
> file does not publish, and the endpoint opens a browser on the machine it runs
> on — there is none in the container. Sign in once from source; the token is
> stored in Postgres rather than in the process, so the Docker stack picks it up
> as long as both are pointed at the same database, which they are by default.

What happens:

1. A Slack consent page opens in a new tab. Approve it.
2. Slack redirects to `https://localhost:3000/slack/callback`, which Cairn is
   listening on.
3. **Your browser will warn about the certificate once** — "Your connection is
   not private", or similar. Click through it (*Advanced* → *Proceed*). The
   certificate is self-signed and generated on your own machine; see *The redirect
   URL* above for why Slack leaves no alternative. Nothing leaves the machine.
4. The tab you opened in step 3 answers `Signed in to <workspace>`.

The token is stored as a credential, separate from the connection's settings.
Sign in again the same way if you ever revoke it.

## What gets read

One source file per channel per day — the same shape Google Chat and WhatsApp
write, so the wiki treats all three alike:

```
# #engineering — 2026-09-20
09:14 dana: shipping the migration today
  ↳ 09:20 sam: after the backup finishes?
```

Thread replies are included (Slack leaves them out of channel history, so
without this most of a busy channel would be missing). Mentions, channel
references and links are converted from Slack's mrkdwn to readable text:
`<@U123>` becomes `@dana`, `<#C123|general>` becomes `#general`, and
`<https://x|label>` becomes a Markdown link. Joins, leaves and topic changes are
skipped. Shared files are named in the transcript; their bytes are not
downloaded.

Re-syncs start at the beginning of the day the last sync reached, so the day
file it rewrites is always a whole day. A reply added to a channel-day older
than that is not picked up until a full sync.

## Rate limits

`conversations.history` and `conversations.replies` were clamped to one request
a minute and 15 messages a request for non-Marketplace apps in 2025 — but that
clamp does not apply to an app a workspace built for itself, which keeps the
original tier. This connector paces itself to that tier and honours
`Retry-After`. If your first sync of a large workspace crawls, check that the
app is installed as an internal app in its own workspace rather than a
distributed one.

## Checking it works

```
.venv/bin/python scripts/connector_check.py --connector slack --live --limit 2
```

This authenticates, lists up to two channels, and writes nothing.
