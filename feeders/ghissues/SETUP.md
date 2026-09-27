# GitHub issues setup

This connector reads **issue and pull request threads** — the body and every comment.
It is not the *GitHub repos* connector, which clones code. The two are separate
cards and can use separate tokens.

Read-only throughout: the token Cairn receives cannot comment, close, merge or push.

**Time:** about 3 minutes.

---

## 1. Create a fine-grained token

Go to [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new).

| Field | What to choose |
|---|---|
| Token name | Anything — `cairn-issues` |
| Expiration | Your call; Cairn will report a 401 once it lapses |
| Resource owner | **You**, for your own repos — or the **organisation** that owns them |
| Repository access | *Only select repositories* → pick exactly the ones you want read |

Then under **Permissions → Repository permissions**, set these three to **Read-only**:

- **Issues**
- **Pull requests**
- **Contents**

Leave everything else at *No access*. Click **Generate token** and copy it — GitHub
shows it once.

> If you picked an organisation as the resource owner, the token sits in
> *Pending* until an owner approves it. It returns 404 on those repositories
> until they do.

## 2. Authorise SSO, if your organisation uses it

Organisations with SAML single sign-on require each token to be authorised for them
separately. Open [github.com/settings/tokens](https://github.com/settings/tokens),
find the token, click **Configure SSO**, and **Authorize** the organisation.

Skipping this is the most common failure here. Cairn detects it and says so instead
of showing a bare 403.

## 3. Connect

On the Connect screen pick **GitHub issues** and fill in:

| Field | Value |
|---|---|
| Personal access token | the `github_pat_…` string from step 1 |
| Repositories | `owner/name`, comma-separated — `acme/widgets, acme/gears` |

The repository name is the last two segments of its URL: `github.com/**acme/widgets**`.
Pasting the whole URL works too.

---

## What gets read

One source per issue or pull request: its title, state, labels, body and every
comment, in order. Bodies are already Markdown, so nothing is converted.

Later syncs ask GitHub only for threads changed since the last one. A new comment
counts as a change, so a long-quiet issue comes back the moment someone replies.

**Discussions are not included.** GitHub exposes them only through its GraphQL API,
which this connector does not speak yet.

## Limits

Authenticated requests are capped at 5,000 per hour. A sync costs roughly one
request per 100 threads plus one per thread that has comments. If the quota runs
out mid-run, Cairn stops, reports a partial sync, and picks up from the same point
next time rather than skipping what it did not reach.
