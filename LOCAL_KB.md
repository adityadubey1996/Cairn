# Personal KB with local Ollama

Cairn can run entirely on your computer with Ollama. The Docker setup is the
shortest path; the source setup is useful while developing connectors or the UI.

## Docker setup

Install Docker and Ollama, then run:

```bash
git clone https://github.com/adityadubey1996/Cairn.git
cd Cairn
ollama pull llama3.1:8b
ollama pull nomic-embed-text
docker compose up -d --build
```

Open **http://localhost:8301**. Compose starts Cairn, Postgres, and local S3
emulation. Its named volumes preserve the database, source files, inbox,
generated wikis, repository clones, and indexes across container recreation.
OAuth tokens are stored in the gitignored local `secrets/` directory.

To use another installed Ollama model:

```bash
LLM_PROVIDER=ollama LLM_MODEL=qwen3:8b docker compose up -d --build
```

## Run from source

Install Python 3.11+, Node 22+, Docker, Ollama, and `uv`, then run:

```bash
git clone https://github.com/adityadubey1996/Cairn.git
cd Cairn
docker compose up -d db
uv venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt
cd web2 && npm ci && npm run build && cd ..
ollama pull llama3.1:8b
ollama pull nomic-embed-text
.venv/bin/python scripts/run_local.py
```

Open **http://localhost:8300**. This profile stores sources and generated
knowledge under `var/local/`, uses the Compose Postgres service on port 5434,
and does not require S3. Override the defaults with:

```bash
CAIRN_LOCAL_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5434/ai_brain
CAIRN_LOCAL_MODEL=llama3.1:8b
CAIRN_LOCAL_PORT=8300
CAIRN_GOOGLE_ACCOUNT=you@example.com
```

## Google connectors

Create a Google Web OAuth client and enable the Drive, Google Chat, and Gmail
APIs. Register the callback matching the way Cairn runs:

- Source: `http://localhost:8300/api/google/callback`
- Docker: `http://localhost:8301/api/google/callback`

Use the matching origin without the callback path. Put `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` in `.env`, restart Cairn, then connect from the UI. Set
`GOOGLE_ACCOUNT_EMAIL` or `CAIRN_GOOGLE_ACCOUNT` if Cairn should reject consent
from another account. Google access is read-only.

Drive, Chat, and Gmail currently share one signed-in Google identity. Gmail
imports complete message threads and attachment names; it does not download
attachment bytes. Chat covers named spaces rather than every direct message.
A connector with `max_items` set is a bounded sample and reports partial until
the remaining work is synced.

## Daily workflow

1. Add a connector from **Connections**.
2. Use **Files** to inspect regular sources. GitHub content appears in **Repos**.
3. Confirm extraction quality and choose an absorption policy.
4. Use **Pipeline** for schedules, manual runs, retries, and job history.
5. Absorb selected material into wiki articles.
6. Ask questions in **Chat** and open citations to inspect their source revision.

The scheduler runs inside Cairn while the app is running. Jobs are persisted in
Postgres and resume after restart. A stop request is handled at the next safe
point, so an active model request or provider fetch may finish first.

## Backups and limitations

Back up the Postgres database, `secrets/`, source revisions, inbox metadata,
source files, and generated wikis. Docker keeps these in named volumes except
for `secrets/`; the source profile keeps file data under `var/local/` and
`var/source-revisions/`.

Local model quality depends on the model and your documents. Validate dates,
numbers, unsupported questions, and citations before relying on an answer.
The validator checks article structure and source references; it cannot prove
that every generated claim is correct. Scanned documents and images require
OCR or vision support outside the basic text extraction path.

## Verification

```bash
DATABASE_URL='postgresql://invalid:invalid@127.0.0.1:1/none?connect_timeout=1' \
S3_BUCKET='' OLLAMA_BASE='' AWS_EC2_METADATA_DISABLED=true \
  .venv/bin/python -m pytest server pipeline feeders scripts -q
cd web2
node --test src/api/live.test.js src/screens/chat/answerLinks.test.js
npm run build
```

See [CONNECTORS.md](CONNECTORS.md) for provider-specific configuration and the
read-only connector diagnostic commands.
