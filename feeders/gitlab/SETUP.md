# GitLab setup

Cairn reads **issues and merge requests** — their descriptions and the comments on
them. It does not clone your code, and it never writes, comments, closes or merges
anything. The token below cannot do any of that.

Works the same on gitlab.com and on a GitLab you host yourself.

**Time:** about two minutes.

---

## 1. Create a personal access token

1. Open [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens).
   On your own instance it is the same page under **your avatar → Preferences →
   Access tokens**.
2. **Token name:** anything — `cairn` is fine.
3. **Expiration date:** GitLab requires one. Pick a date you will remember; Cairn
   tells you when the token stops working, and you paste a new one.
4. **Select scopes:** tick **`read_api`** and nothing else.

   > `read_api` is read-only across the whole API. Do **not** tick `api` — that one
   > can write. `read_repository` is for cloning code and is not used here.
5. **Create personal access token**, then copy the `glpat-…` value. GitLab shows it
   once.

## 2. Connect it in Cairn

On the Connect screen, choose GitLab and fill in:

| Field | What to put |
|---|---|
| **GitLab host** | `gitlab.com`, or your own host — `gitlab.mycompany.com`. If GitLab is served under a path, include it: `gitlab.mycompany.com/gitlab`. |
| **Personal access token** | the `glpat-…` value from step 1. |
| **Projects** | leave empty to read every project you are a member of, or list paths: `group/subgroup/project, group/other`. |

Group paths nest as deeply as your groups do — copy the path straight out of the
project's URL, minus the `https://host/` in front.

## 3. Check it

```bash
python scripts/connector_check.py --connector gitlab --live --limit 2
```

It authenticates, lists two threads, and writes nothing. `account` in the output is
the username the token belongs to.

---

## If the host is refused

Cairn will not fetch from a host that is not reachable over `https://`, that has a
username or password baked into it, or that resolves to an address inside the
machine's own network (`127.0.0.1`, `10.x`, `192.168.x`, `169.254.169.254`). That
last rule is what stops a pasted host from turning Cairn into a way to read your
private network.

A GitLab that genuinely lives on your internal network is the one legitimate case.
The **operator of the Cairn install** — not whoever fills in the Connect form —
opts in by setting an environment variable before starting the server:

```bash
GITLAB_ALLOW_PRIVATE_HOST=1
```

It still requires `https://`.

## Common errors

| Message | Fix |
|---|---|
| *GitLab rejected the token…* | The token expired, was revoked, or lacks `read_api`. Create a new one. |
| *GitLab host must use https://* | Drop `http://`. GitLab.com and any sane instance serve HTTPS. |
| *remove the credentials embedded in the GitLab host* | Paste just the host. The token goes in the token field. |
| *…is not a GitLab project path like group/subgroup/project* | A project needs its full path, not just its name. |
| *refusing to reach … non-public address* | See *If the host is refused* above. |
