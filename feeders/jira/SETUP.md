# Jira setup

You register one app, once, for this whole install. After that every Jira
connection is a click on **Connect** and an Atlassian sign-in page — nobody ever
pastes a token.

Read-only throughout: the scopes requested are `read:jira-work` and
`read:jira-user`. No approval here can be turned into a change in Jira.

**Time:** about five minutes, once ever.

---

## Why there is a registration step at all

Atlassian's API authorization server has no `registration_endpoint` — a client
cannot create itself, the way it can against some providers. So *somebody* has to
register one. The point is that it is **you, once, for the install** — not each
user, and not each connection.

It needs no client secret. Atlassian advertises
`token_endpoint_auth_methods_supported: ["none"]` with PKCE `S256`, so Cairn is a
public client: the client id travels in the open and PKCE is what stops a stolen
authorization code being redeemed by anyone else.

---

## 1. Register the app

[developer.atlassian.com](https://developer.atlassian.com) → your profile →
**Developer console** → **Create** → **OAuth 2.0 integration**. Name it `Cairn`.

**Permissions** → add the **Jira API** → **Add scopes**, and tick exactly:

- `read:jira-work`
- `read:jira-user`

Add nothing else. A write scope you grant here is a write scope that exists
forever, and this connector never issues one.

**Authorization** → **Add** on OAuth 2.0 (3LO) → **Callback URL**.

**Settings** → copy the **Client ID**. Ignore the secret; nothing here uses it.

### The redirect URL — you only get one, so pin the port

Cairn builds the redirect from the address in your browser's bar, so the port
changes with how you started it:

```
from source (default PORT):   http://localhost:8300/api/jira/callback
under Docker:                 http://localhost:8301/api/jira/callback
custom PORT=9000:             http://localhost:9000/api/jira/callback
```

Every other connector here tells you to register all of them at once. **You
cannot do that on Atlassian.** A 3LO app takes a single callback URL, and
Atlassian's answer to wanting more has been to create a second app. So pick the
one line above that matches how you will actually run Cairn, paste it, and then
keep that run mode fixed — set `PORT` in `.env` and leave it alone. Switching
between source and Docker later means editing the callback URL in the console,
or registering a second app with its own client id.

> Atlassian's developer console has, since early 2026, been reported to accept
> more than one URL in that field, without an announcement or a documentation
> change. If yours does, add all three and you are done. Treat it as a bonus, not
> something to plan around — the documented behaviour is still one URL.

**How Atlassian matches it.** Byte for byte: scheme, host, port and path, with
the spellings `localhost` and `127.0.0.1` counting as different strings. `http`
is accepted on localhost and only there.

**When it does not match**, Atlassian refuses before the consent screen and drops
you on `id.atlassian.com/error` with `error=unauthorized_client` and:

```
redirect_uri is not registered for client: http://localhost:8301/api/jira/callback
```

The URI in that message is the one Cairn sent. If it is not character-identical
to the Callback URL on the app, that is the whole bug — usually the port.

**On a non-default port.** Change the callback URL in the console to the new
port. The path after the host never changes: it is always `/api/jira/callback`,
derived from the connector's provider name, not from anything you configure.

## 2. Tell Cairn the client id

```bash
ATLASSIAN_CLIENT_ID=<the client id from step 1>
```

in `.env`, then restart the server.

Until it is set, the Jira card reads *not configured* and names
`ATLASSIAN_CLIENT_ID` as what is missing.

> This variable is **not** in `.env.example` — add the line by hand.

## 3. Connect

Connect screen → **Add connector** → **Jira** → **Continue to Jira**. Approve on
Atlassian's page. You land back on Cairn with the connection created and its
first sync starting.

Set **Maximum items per sync** to 20 for the first run, so you can look at what
extraction produced before committing to a full sweep. A capped run reports
itself as partial and picks the rest up next time.

---

## What gets read

Every issue your account can see, one source per issue: key, summary,
description and comments. After the first sync only issues updated since the
last one are re-read.

The sign-in belongs to the install, not to a connection — like the Google one
beside it. If your account can reach several Atlassian sites, the first one
granted is used; a connection can name a different one by setting `site` in its
config, and naming a site the sign-in does **not** cover is an error rather than
a silent fallback to the wrong project.

## Renewal

Atlassian access tokens last an hour and the refresh token rotates on every use.
Cairn stores the replacement each time, so nothing expires under you as long as
it syncs at least every 90 days. The tokens live in `secrets/atlassian-oauth.json`,
mode `600`, and never enter the database or a connection's config row.

---

## Fallback: an API token, no registration

For a single-person install that would rather register nothing, the connector
still accepts a personal API token. It is **not offered on the Connect form** —
a token box beside a sign-in button is how this screen became unusable — so it is
set through the API:

1. Create a token at
   [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens).
   Choose the plain **API token**, not "API token with scopes".
2. Create the connection with `site`, `email` and `token` in its config:

```bash
curl -s -X POST http://localhost:8300/api/connections \
  -H 'Content-Type: application/json' \
  -d '{"projectId":"<project id>","kind":"jira","name":"Jira",
       "config":{"site":"yourteam.atlassian.net","email":"you@yourteam.com","token":"<token>"}}'
```

A connection carrying a `token` uses it and ignores the sign-in entirely. The
same token also reaches Confluence, but this connector never calls it.

## Checking it

```bash
.venv/bin/python scripts/connector_check.py --connector jira --live --limit 2
```

Writes nothing, anywhere. Each sampled issue reports `comments_inline: true` or
`false` — if `false`, this site does not return comments with the search, and
reading them costs one extra request per issue.

## When it stops working

| What you see | What it is |
|---|---|
| `ATLASSIAN_CLIENT_ID` in *missing* | Step 2 was skipped, or the server was not restarted after it. |
| `Sign in to Atlassian again…` | The approval was revoked, or the refresh token went unused too long. Press Connect again. |
| `The Atlassian sign-in does not cover <site>` | A connection names a site the approval did not include. The message lists the ones it did. |
| `Atlassian refused the sign-in (400)` | Usually the callback URL on the app does not match the port Cairn is served on. Atlassian's own page says `redirect_uri is not registered for client: …` and names the URI it received. |
| `Jira rejected the credentials` | Only on the API-token fallback: the token expired or the email is not its owner. |
| An issue is missing | Your account cannot see it. Cairn reads exactly what you read. |
