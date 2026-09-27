"""Schema, applied idempotently at startup. Alembic when this grows."""
from .db import connect

DDL = """
CREATE TABLE IF NOT EXISTS brain_conversations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_email text NOT NULL,
  title      text NOT NULL DEFAULT 'New conversation',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS brain_messages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES brain_conversations(id) ON DELETE CASCADE,
  role            text NOT NULL,
  content         text NOT NULL,
  citations       jsonb NOT NULL DEFAULT '[]',
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS brain_messages_conv
  ON brain_messages (conversation_id, created_at);

-- Feeders (Drive, Chat, ...) write raw/inbox/ entries in some repo's working
-- tree; run history lives here rather than in var/ because it is operational
-- record, not a disposable index — it must survive an index rebuild.
CREATE TABLE IF NOT EXISTS brain_connectors (
  id         text PRIMARY KEY,
  name       text NOT NULL,
  kind       text NOT NULL,
  config     jsonb NOT NULL DEFAULT '{}',
  enabled    boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS brain_connector_runs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id  text NOT NULL REFERENCES brain_connectors(id) ON DELETE CASCADE,
  status        text NOT NULL,
  items_seen    int NOT NULL DEFAULT 0,
  items_written int NOT NULL DEFAULT 0,
  error         text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS brain_connector_runs_conn
  ON brain_connector_runs (connector_id, started_at DESC);

-- A wiki write-up is a run like any other — same phases, same log, same
-- "one at a time" guard — but it belongs to no feeder, so it needs a
-- brain_connectors row of its own to satisfy the FK above. Deliberately not a
-- REGISTRY entry: connectors.health() iterates REGISTRY, and this must not
-- appear there as something to configure or sync.
INSERT INTO brain_connectors (id, name, kind) VALUES ('wiki', 'Wiki write-up', 'wiki')
  ON CONFLICT (id) DO NOTHING;

-- Tracked GitHub repos. The clone under var/ is disposable and evictable; the
-- wiki it generates is not, so wiki_root points outside the clone and survives
-- eviction. head_sha is what the articles describe; pinned_sha is set only
-- while a commit-filtered preview is parked, and clearing it returns to HEAD.
CREATE TABLE IF NOT EXISTS brain_repos (
  id            text PRIMARY KEY,
  owner         text NOT NULL,
  name          text NOT NULL,
  url           text NOT NULL,
  branch        text NOT NULL,
  state         text NOT NULL DEFAULT 'added',
  last_error    text,
  clone_path    text,
  wiki_root     text,
  head_sha      text,
  pinned_sha    text,
  articles      int NOT NULL DEFAULT 0,
  queue_new     int NOT NULL DEFAULT 0,
  queue_changed int NOT NULL DEFAULT 0,
  clone_bytes   bigint NOT NULL DEFAULT 0,
  last_used_at  timestamptz NOT NULL DEFAULT now(),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS repo_id text
  REFERENCES brain_repos(id) ON DELETE CASCADE;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS step text;
CREATE INDEX IF NOT EXISTS brain_connector_runs_repo
  ON brain_connector_runs (repo_id, started_at DESC);

-- V2: hard isolation. Every other table below is scoped to one of these —
-- there is no "all projects" read anywhere in the API.
CREATE TABLE IF NOT EXISTS brain_projects (
  id         text PRIMARY KEY,
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE brain_conversations ADD COLUMN IF NOT EXISTS project_id text
  REFERENCES brain_projects(id) ON DELETE CASCADE;
ALTER TABLE brain_repos ADD COLUMN IF NOT EXISTS project_id text
  REFERENCES brain_projects(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS brain_repos_project ON brain_repos (project_id);

-- One row per actual connection (two GitHub repos are two brain_repos rows,
-- already 1:1 with a connection; this table covers every other kind, where
-- today's config is one env-driven singleton per kind). `config` holds
-- connection-specific overrides — e.g. which WhatsApp groups this one tracks.
CREATE TABLE IF NOT EXISTS brain_connector_connections (
  id         text PRIMARY KEY,
  project_id text NOT NULL REFERENCES brain_projects(id) ON DELETE CASCADE,
  kind       text NOT NULL,
  name       text NOT NULL,
  config     jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS brain_connector_connections_project
  ON brain_connector_connections (project_id);

-- One row per fed file (not code: github's tracked files are browsed via the
-- wiki, never listed here). Feeders insert a row the moment they write the
-- file under sources/ — see feeders/*/sync.py. `id` reuses the same id the
-- feeder already mints for its raw/inbox/ unit, so the two stay one lookup.
CREATE TABLE IF NOT EXISTS brain_sources (
  id            text PRIMARY KEY,
  project_id    text NOT NULL REFERENCES brain_projects(id) ON DELETE CASCADE,
  connection_id text REFERENCES brain_connector_connections(id) ON DELETE SET NULL,
  kind          text NOT NULL,
  type          text NOT NULL DEFAULT 'file',
  name          text NOT NULL,
  path          text NOT NULL,
  url           text,
  detail        text,
  bytes         bigint,
  sha           text,
  authors       jsonb NOT NULL DEFAULT '[]',
  scraped_at    timestamptz NOT NULL DEFAULT now()
);
-- A row is normally a file that WAS fetched. `status='failed'` records an item
-- the connector saw and could not bring in: the feeders knew the id at the
-- moment they logged and skipped it, and threw it away, so nothing could show
-- what a sync missed. Failed rows carry no bytes/sha and their path may not
-- exist, so every read except the Sources listing filters them out.
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ok';
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS error text;
-- Where the item sits in its connector's own hierarchy: a Drive folder path.
-- NULL is the common answer, not a gap — most Drive files here are transcripts
-- shared out of a teammate's Drive, whose parent this account cannot read.
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS folder text;
-- How far a connection has been scraped. NULL means "never", which is what
-- makes the first sync a full one with no special case. Advanced only by a
-- scrape phase that finished clean: moving it past a failure would skip the
-- window that failed, permanently.
ALTER TABLE brain_connector_connections
  ADD COLUMN IF NOT EXISTS synced_at timestamptz;

-- One row per unit of ingestion work — the thing a wiki write-up consumes.
-- This is the answer to "which file is done, which is running, which never
-- started", previously split between brain_sources.wiki_queued_at and a
-- _absorb_log.json file on disk, with nothing at all for "running".
-- unit_id is NOT a foreign key: it is usually a brain_sources id, but a repo
-- file's unit has no source row, and an FK would reject those outright.
CREATE TABLE IF NOT EXISTS brain_ingest_units (
  unit_id    text NOT NULL,
  project_id text NOT NULL REFERENCES brain_projects(id) ON DELETE CASCADE,
  state      text NOT NULL DEFAULT 'pending',
  attempts   int  NOT NULL DEFAULT 0,
  error      text,
  article    text,
  run_id     uuid,
  started_at timestamptz,
  ended_at   timestamptz,
  PRIMARY KEY (unit_id, project_id)
);
CREATE INDEX IF NOT EXISTS brain_ingest_units_state
  ON brain_ingest_units (project_id, state);
-- The original bytes, kept beside the extracted markdown only where the two
-- are different files. An uploaded PDF has no external URL to fall back on, so
-- feeders/upload/sync.py stores it under sources/upload/originals/; a .txt or
-- .csv needs no copy because the stored source file IS the original.
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS original_path text;
-- Marked for a wiki write-up, not yet written up. NULL means not queued.
-- There is deliberately no matching "already in the wiki" column: that stays
-- answered by articles_citing(), which reads the citations articles actually
-- carry, and a second copy of that truth could only drift from it.
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS wiki_queued_at timestamptz;
-- Pipeline Phase A columns (also in server/pipeline/schema.py). Kept here so
-- app startup applies them without a separate migrate step.
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS occurred_at date;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS found_in text;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS publisher jsonb NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS brain_sources_project
  ON brain_sources (project_id, scraped_at DESC);
CREATE INDEX IF NOT EXISTS brain_sources_path ON brain_sources (path);

-- BYOK: the answer/absorb provider and the embeddings provider are two
-- independent settings, each one row here (id = 'provider' | 'embeddings').
-- Single-user tool, so this is global, not per-project.
CREATE TABLE IF NOT EXISTS brain_settings (
  id         text PRIMARY KEY,
  value      jsonb NOT NULL DEFAULT '{}',
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- The pipeline runner is a detached process, so Postgres is the only channel
-- between it and the UI. phase/phases/pid/heartbeat_at are what it writes;
-- items_seen and items_written stay as they are and now carry the CURRENT
-- phase's progress, which is what connectors.health() already reads.
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS phase text;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS phases jsonb NOT NULL DEFAULT '{}';
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS pid int;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz;

CREATE TABLE IF NOT EXISTS brain_run_log (
  run_id uuid NOT NULL REFERENCES brain_connector_runs(id) ON DELETE CASCADE,
  seq    int  NOT NULL,
  at     timestamptz NOT NULL DEFAULT now(),
  line   text NOT NULL,
  PRIMARY KEY (run_id, seq)
);

-- Persistent connection schedules and work survive closing the browser/server.
CREATE TABLE IF NOT EXISTS brain_connection_policies (
  connection_id text PRIMARY KEY,
  project_id text NOT NULL REFERENCES brain_projects(id) ON DELETE CASCADE,
  sync_enabled boolean NOT NULL DEFAULT false,
  schedule_minutes integer NOT NULL DEFAULT 60,
  cron_expression text NOT NULL DEFAULT '',
  timezone text NOT NULL DEFAULT 'Asia/Kolkata',
  auto_absorb boolean NOT NULL DEFAULT false,
  next_run_at timestamptz,
  last_run_at timestamptz
);
-- The absorption plan. Syncing is free; absorbing is the only step that spends
-- the model, so when it runs and how much it may take are separate answers from
-- the sync schedule above. `auto_absorb` is kept in step with absorb_trigger so
-- that anything still reading the old boolean keeps working.
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_trigger text NOT NULL DEFAULT 'manual';
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_cron text NOT NULL DEFAULT '';
-- 0 means "no ceiling of its own": units fall back to the whole queue and
-- tokens to the pipeline's own default, which is what ran before this existed.
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_limit_units integer NOT NULL DEFAULT 0;
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_max_tokens bigint NOT NULL DEFAULT 0;
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_guardrail_units integer NOT NULL DEFAULT 0;
ALTER TABLE brain_connection_policies ADD COLUMN IF NOT EXISTS
  absorb_next_run_at timestamptz;

ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS project_id text;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS connection_id text;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS job jsonb;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE brain_connector_runs ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS absorption_policy text NOT NULL DEFAULT 'inherit';
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS absorbed_sha text;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS absorbed_at timestamptz;
CREATE INDEX IF NOT EXISTS brain_runs_jobs ON brain_connector_runs(status, started_at) WHERE job IS NOT NULL;
CREATE TABLE IF NOT EXISTS brain_oauth_states (
  state_hash text PRIMARY KEY,
  provider text NOT NULL,
  payload jsonb NOT NULL,
  expires_at timestamptz NOT NULL
);
"""


def ensure_schema() -> None:
    with connect() as c:
        c.execute(DDL)
