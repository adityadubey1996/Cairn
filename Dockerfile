# Two stages: node builds the SPA, python runs everything. The image needs git
# and graphify because the pipeline shells out to both — this is not a plain
# web app, it clones repositories and runs an AST extractor over them.

FROM node:22-alpine AS web2
WORKDIR /web2
COPY web2/package.json web2/package-lock.json* ./
RUN npm ci || npm install
COPY web2/ ./
RUN npx vite build


FROM python:3.11-slim
WORKDIR /app

# git: the connector clones with it, and ingest reads first-seen dates from the
#      log, so a full history is required (never --depth).
# ca-certificates: https clones.
# pandoc / poppler-utils: optional binary-doc extraction. ingest degrades
#      gracefully without them, but a .docx then absorbs as EXTRACTION FAILED.
# tesseract-ocr: scanned/photographed PDFs have no text layer for pdftotext or
#      pypdf to read — feeders/gdrive/sync.py's _pdf_to_text() falls back to
#      OCR only when both of those come back empty.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates pandoc poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# graphifyy is the PyPI name (two y's). Pinned: SKILL.md once documented a
# command that did not exist in the installed version, and graph.json's shape is
# parsed directly by graph_verify.py — an unattended upgrade can break both.
RUN pip install --no-cache-dir -r requirements.txt "graphifyy==0.8.49"

COPY server/ ./server/
COPY pipeline/ ./pipeline/
COPY feeders/ ./feeders/
COPY scripts/ ./scripts/
COPY --from=web2 /web2/dist ./web2/dist

# The articles, committed to git. Git is the storage of knowledge; the volume is
# the live working copy the server writes to. The entrypoint seeds one from the
# other on a fresh deploy.
COPY wikis/ ./wikis/
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Clones and wikis are volumes (see compose). Creating them here means the
# container still starts if a volume is missing — it just has nothing tracked.
RUN mkdir -p /data/clones /data/wikis /data/sources /app/var

ENV PYTHONUNBUFFERED=1 \
    PYTHON_BIN=python3 \
    GRAPHIFY_BIN=graphify \
    PIPELINE_DIR=/app/pipeline \
    REPO_CLONE_DIR=/data/clones \
    REPO_WIKI_DIR=/data/wikis \
    SOURCES_DIR=/data/sources

EXPOSE 8300
ENTRYPOINT ["docker-entrypoint.sh"]
# One worker on purpose: the job registry that stops two absorbs racing on the
# same repo is an in-process dict. Multi-worker needs that lock in Postgres
# first — see the ponytail note in server/repos.py.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "1"]
