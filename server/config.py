"""Env-driven config, loaded once from ai-brain/.env (real env vars win)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = ROOT / "var"  # includes durable source revisions/local profile plus rebuildable indexes


def _load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # Blank means "use the default". .env.example lists every setting
        # blank, and os.environ.get(k, default) returns "" for a key that is
        # present — which pointed clone/wiki/source dirs at the current directory.
        if v.strip():
            os.environ.setdefault(k.strip(), v.strip())


_load_env(ROOT / ".env")

AUTH_MODE = os.environ.get("AUTH_MODE", "dev")

# The Pipeline screen drives paid, hours-long runs and is not meant for everyday
# users. One env var, off unless explicitly set.
DEV_UI = os.environ.get("DEV_UI") == "1"
# One Web OAuth client covers both Google paths: sign-in verifies an ID token
# with the client id alone, while the Drive/Chat feeders run the server-side
# code exchange, which also needs the secret. Same client, same consent screen.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
ALLOWED_DOMAIN = os.environ.get("ALLOWED_DOMAIN", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
# Absorb uses a cheaper model than chat (bulk, one call per source-day). Default
# must be a model Groq currently serves — llama-3.3-70b-versatile was retired (404).
ABSORB_MODEL = os.environ.get("ABSORB_MODEL", "openai/gpt-oss-20b")

# Password gate for the live-browser iframe — it exposes the logged-in WhatsApp/
# LinkedIn session, so the panel asks for this before rendering. Value lives ONLY
# in .env (gitignored) — no default here, so nothing sensitive is committed; empty
# means unconfigured and view-auth then fails closed (nobody can reveal the iframe).
BROWSER_VIEW_PASSWORD = os.environ.get("BROWSER_VIEW_PASSWORD", "")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
WIKI_ROOTS = [Path(p.strip()).expanduser()
              for p in os.environ.get("WIKI_ROOTS", "").split(",") if p.strip()]
# Default matches the `db` service in docker-compose.yml, which publishes
# on 5434 to stay clear of a native Postgres on 5432. So `docker compose up -d
# db` then running the server needs no DATABASE_URL at all.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5434/ai_brain")
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-only-secret")
PORT = int(os.environ.get("PORT", "8300"))

# Written by the Google consent callback; its presence IS "Google is connected".
GOOGLE_TOKEN_FILE = Path(os.environ.get(
    "GOOGLE_TOKEN_FILE", ROOT / "secrets" / "google-oauth.json")).expanduser()

# Drive sources: empty means "files you own", which is the sane project-wide
# default — `corpora=user` on its own also returns everything ever shared with
# the account, which on a Workspace domain is unbounded. IDs narrow it, and each
# may be a folder (walked) or a single file.
GDRIVE_SOURCE_IDS = [f.strip() for f in
                     os.environ.get("GDRIVE_SOURCE_IDS", "").split(",") if f.strip()]
GDRIVE_EXCLUDE = [p.strip() for p in
                  os.environ.get("GDRIVE_EXCLUDE", "").split(",") if p.strip()]
# Which repo's working tree the feeder writes sources/ + raw/inbox/ into.
GDRIVE_TARGET_REPO = Path(os.environ.get("GDRIVE_TARGET_REPO", ROOT)).expanduser()

# Total fetch ATTEMPTS (successes and failures both count) allowed in one
# links-connector run, across the initial batch and its one recursion hop.
# A safety valve, not a quality filter — see feeders/links/sync.py.
LINKS_FETCH_CAP = int(os.environ.get("LINKS_FETCH_CAP", "5000"))

# GitHub repo connector. Clones are disposable and live under var/; wikis are
# the durable artifact and must be on a persistent volume in a deploy — losing
# them means re-buying every article.
REPO_CLONE_DIR = Path(os.environ.get("REPO_CLONE_DIR", VAR / "clones")).expanduser()
REPO_WIKI_DIR = Path(os.environ.get("REPO_WIKI_DIR", ROOT / "wikis")).expanduser()
# Private repos on a server: no working trees, no SSH agent, so a token is the
# only way in. Fine-grained PAT or GitHub App installation token, Contents:read.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# Git hosts a browser may ask the server to clone from. This is a trust
# boundary, not a convenience: an unlisted host is a request to open a
# connection from inside the network, so the list is closed and the default is
# the four public hosts. Add a self-hosted instance here deliberately.
REPO_ALLOWED_HOSTS = [h.strip().lower() for h in os.environ.get(
    "REPO_ALLOWED_HOSTS",
    "github.com,gitlab.com,bitbucket.org,codeberg.org").split(",") if h.strip()]

# Optional S3 backing for the wiki tree. Unset = no-op; the filesystem (and git)
# remain the store. S3_PREFIX is the environment separator — one bucket with
# dev/ and staging/ under it, so one policy covers both.
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", os.environ.get("ENV", "dev"))
S3_REGION = os.environ.get("AWS_REGION", "")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "")  # MinIO / non-AWS

# Feeder-fetched content (Drive docs, transcripts). Durable and NOT derivable:
# re-fetching gives what the document says today, not what was ingested, so a
# lost source silently invalidates every citation made against it.
SOURCES_DIR = Path(os.environ.get("SOURCES_DIR", ROOT / "sources")).expanduser()
REPO_MAX_TRACKED = int(os.environ.get("REPO_MAX_TRACKED", "20"))
# Working trees on this machine that may be cloned from. A path from a browser
# is arbitrary filesystem read, so it must be inside one of these. Defaults to
# the workspace folder that contains ai-brain.
REPO_LOCAL_ROOTS = [Path(p.strip()).expanduser().resolve()
                    for p in os.environ.get("REPO_LOCAL_ROOTS", str(ROOT.parent)).split(",")
                    if p.strip()]
REPO_MAX_SIZE_MB = int(os.environ.get("REPO_MAX_SIZE_MB", "2000"))
REPO_DISK_BUDGET_MB = int(os.environ.get("REPO_DISK_BUDGET_MB", "5000"))

# Binaries and scripts the pipeline shells out to. Overridable because the dev
# machine uses a uv tool and a venv while the image uses its own python.
# The interpreter running this server, so a source checkout's pipeline steps
# see its venv. The image sets PYTHON_BIN=python3 itself.
PYTHON_BIN = os.environ.get("PYTHON_BIN", sys.executable)
GRAPHIFY_BIN = os.environ.get("GRAPHIFY_BIN", "graphify")
PIPELINE_DIR = Path(os.environ.get("PIPELINE_DIR", ROOT / "pipeline")).expanduser()

# Browser connectors (WhatsApp/LinkedIn) drive a Steel Browser over CDP.
# BASE/WS are how this process reaches Steel; VIEWER_BASE is how the USER'S
# browser reaches it (loaded in an iframe), which differs inside compose.
STEEL_BASE_URL = os.environ.get("STEEL_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
STEEL_WS_URL = os.environ.get("STEEL_WS_URL", "ws://127.0.0.1:3000").rstrip("/")
STEEL_VIEWER_BASE_URL = os.environ.get(
    "STEEL_VIEWER_BASE_URL", "http://localhost:3000").rstrip("/")

# The browser fallback for links that plain HTTP cannot read. OFF by default:
# it needs a Steel instance running, and an install without one must behave
# exactly as it did before.
STEEL_ENABLED = os.environ.get("STEEL_ENABLED", "").lower() in ("1", "true", "yes")
# A page load is seconds where urllib is milliseconds. This ceiling is what
# stops one backlog from turning into an overnight job.
LINK_BROWSER_MAX = int(os.environ.get("LINK_BROWSER_MAX", "200"))
LINK_BROWSER_TIMEOUT = float(os.environ.get("LINK_BROWSER_TIMEOUT", "45"))

# Navigation-fallback agent only — extraction is deterministic CDP, never LLM.
BROWSER_LLM_MODEL = os.environ.get("BROWSER_LLM_MODEL", "openai/gpt-oss-120b")

WHATSAPP_GROUPS = [g.strip() for g in
                   os.environ.get("WHATSAPP_GROUPS", "").split(",") if g.strip()]
WHATSAPP_SINCE = os.environ.get("WHATSAPP_SINCE", "")
WHATSAPP_DATE_ORDER = os.environ.get("WHATSAPP_DATE_ORDER", "DMY")
LINKEDIN_THREADS = [t.strip() for t in
                    os.environ.get("LINKEDIN_THREADS", "").split(",") if t.strip()]
LINKEDIN_SINCE = os.environ.get("LINKEDIN_SINCE", "")

# >0 runs browser connectors on a timer; 0 keeps them manual-only.
CONNECTOR_SYNC_INTERVAL_MIN = int(os.environ.get("CONNECTOR_SYNC_INTERVAL_MIN", "0"))

# Durable per-platform browser state (LinkedIn cookies). Same home as the
# Google OAuth token; secrets/ is gitignored.
SECRETS_DIR = GOOGLE_TOKEN_FILE.parent
