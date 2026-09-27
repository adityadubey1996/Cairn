# Google setup — Drive, Gmail and Chat

One OAuth client covers all three. Read-only throughout: the token Cairn receives
cannot write, send, delete or post.

> **You do not need a service account.** Those only see files explicitly shared with
> them, and many organisations block creating their keys outright. Cairn signs in as
> *you*, so it sees exactly what you see. If a guide tells you to download a service
> account JSON key, you are following the wrong one.

**Time:** about 10 minutes, once.

---

## Watch, or read

Either video covers creating the client; the steps below are what Cairn specifically
needs afterwards.

- [How to Create Google OAuth Client ID and Client Secret](https://www.youtube.com/watch?v=1TvrgUdlzAc) — English
- [Tuto Google OAuth : Créer un Client ID (Drive, Gmail, API)](https://www.youtube.com/watch?v=SF7B3mJ3678) — French

---

## 1. Create a project

[console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate).
Any name. A personal Google account is fine.

## 2. Enable the APIs you want

*APIs & Services → Library*, then enable one per connector you plan to use:

| Connector | API to enable |
|---|---|
| Google Drive | Google Drive API |
| Gmail | Gmail API |
| Google Chat | Google Chat API — **work accounts only**, see the note at the end |

## 3. Configure the consent screen

*APIs & Services → OAuth consent screen*. Choose the audience that matches your
account. For a personal account that means **External**, and you must add your own
address under **Test users** or Google will refuse the sign-in.

While the app stays in *Testing*, Google expires the sign-in every 7 days, so you
will press Connect again about weekly. That is Google's rule for unverified apps, not
a Cairn limitation.

## 4. Create the client

*Credentials → Create credentials → OAuth client ID*

- Application type: **Web application**

Google shows you a **Client ID** and a **Client secret**. Keep the tab open.

### The redirect URL — register all of these now

Cairn builds the redirect from the address in your browser's bar, so the port
changes with how you started it. Register **every** URL you might use, today,
under **Authorised redirect URIs**. Google allows many on one client, adding them
costs nothing, and the alternative is a setup that works until the first time you
switch between source and Docker:

```
http://localhost:8300/api/google/callback
http://localhost:8301/api/google/callback
```

The first is running from source on the default `PORT`. The second is
`docker compose up`, which publishes the container's 8300 on host 8301.

**How Google matches it.** Byte for byte, for the `Web application` client type —
which is the type step 4 tells you to create. Google's own wording is that "the
`http` or `https` scheme, case, and trailing slash (`/`) must all match". So:

- `127.0.0.1` is **not** `localhost`. Both are legal to register — Google exempts
  loopback addresses from its no-raw-IP rule — but they are different strings, and
  registering one does not cover the other. Open Cairn at `http://localhost:8300`.
- A trailing slash breaks it. `…/callback/` is not `…/callback`.
- `http` is correct here. Google requires HTTPS for redirect URIs and exempts
  localhost from that rule.

> The `Desktop app` client type behaves differently: it accepts any loopback port
> at request time, with nothing registered in advance, which is the RFC 8252
> pattern. Cairn does not use it — it needs a client secret to keep refresh tokens
> across restarts, so **Web application** is the type to create, and its
> redirect URIs are the exact strings above.

**When it does not match**, Google refuses before showing the consent screen and
the error is `redirect_uri_mismatch`. The message names the URI Cairn sent; add
that exact string to the client and try again.

**On a non-default port.** If you set `PORT=9000` in `.env`, or publish Docker
elsewhere with `V2_PORT`, add `http://localhost:9000/api/google/callback` to the
same client. Only the port ever changes — `/api/google/callback` is fixed, for
Drive, Gmail and Chat alike, because one Google consent covers all three.

## 5. Paste them into `.env`

In the repository root, in the file called `.env` (create it with
`cp .env.example .env` if it is not there yet):

```bash
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
```

Nothing else is needed. The file is gitignored, so these never reach git.

## 6. Restart, then press Connect

Restart Cairn so it reads the new values, open the Connect screen, and press Connect
on Google Drive. Google asks for consent, and because the app is unverified it warns
you first — click **Advanced → Go to (unsafe)**, which is you trusting your own app.

The refresh token lands in `secrets/google-oauth.json`: gitignored, never logged, and
never returned through the API.

## Checking it worked

```bash
.venv/bin/python scripts/connector_check.py --connector gdrive --live --limit 2
```

It signs in as you, lists two files and writes nothing.

---

## Notes that save time later

- **Gmail needs its own consent.** An existing Drive sign-in does not grant mail
  access. Enable the Gmail API, then press Connect again and approve the mail scope.
- **Google Chat is work accounts only.** Its API requires a Google Workspace Business
  or Enterprise account and a Chat app configured in the same Cloud project. There is
  no path for a personal `@gmail.com` account.
- **If sync stops with a re-authentication error**, the refresh token died: revoked
  access, a password change, six months unused, or the 7-day Testing expiry. Press
  Connect again.
