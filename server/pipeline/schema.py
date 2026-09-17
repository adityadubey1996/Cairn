"""Phase A tables. Plain SQL, IF NOT EXISTS throughout, applied on every run.

Kept here rather than in server/models.py so in-flight edits to that file by
other work do not collide with this one. Moving these statements into
models.py later is a cut and paste.

The brain_projects and brain_sources statements mirror server/models.py so
this module can bootstrap an empty database on its own; IF NOT EXISTS makes
them no-ops when the app has already created the tables.
"""
from __future__ import annotations

import psycopg

DDL = """
CREATE TABLE IF NOT EXISTS brain_projects (
  id         text PRIMARY KEY,
  name       text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

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
  scraped_at    timestamptz NOT NULL DEFAULT now(),
  status        text NOT NULL DEFAULT 'ok',
  error         text,
  folder        text
);

ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS source_type text;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS occurred_at date;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS found_in text;
ALTER TABLE brain_sources ADD COLUMN IF NOT EXISTS publisher jsonb NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS brain_parts (
  id         text PRIMARY KEY,
  source_id  text NOT NULL REFERENCES brain_sources(id) ON DELETE CASCADE,
  project_id text NOT NULL,
  n          int  NOT NULL,
  total      int  NOT NULL,
  anchor     jsonb NOT NULL DEFAULT '{}',
  chars      int  NOT NULL,
  body       text NOT NULL,
  body_sha   text NOT NULL,
  oversized  boolean NOT NULL DEFAULT false,
  tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
);
CREATE INDEX IF NOT EXISTS brain_parts_tsv    ON brain_parts USING GIN (tsv);
CREATE INDEX IF NOT EXISTS brain_parts_source ON brain_parts (source_id, n);
CREATE INDEX IF NOT EXISTS brain_parts_project ON brain_parts (project_id);
"""


def ensure_schema(db: psycopg.Connection) -> None:
    db.execute(DDL)
    db.commit()
