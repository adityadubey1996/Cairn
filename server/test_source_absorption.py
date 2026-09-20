"""Exercise absorption state against an isolated SQL store; no live Postgres."""
import re
import sqlite3

import pytest

from server import config, sources


class Database:
    """Translate only psycopg placeholders/ANY; execute the actual state SQL."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.create_function('now', 0, lambda: '2026-09-20T12:00:00Z')
        fields = [name.strip() for name in sources.FIELDS.split(',')]
        columns = [name + (' TEXT PRIMARY KEY' if name == 'id' else
                          " TEXT DEFAULT 'inherit'" if name == 'absorption_policy' else ' TEXT')
                   for name in fields]
        self.db.execute(f"CREATE TABLE brain_sources ({','.join(columns)})")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=()):
        args, values = iter(params), []
        def substitute(match):
            value = next(args)
            if match.group().startswith('='):
                values.extend(value)
                return ' IN (' + ','.join('?' for _ in value) + ')'
            values.append(value)
            return '?'
        sql = re.sub(r'=\s*ANY\(%s\)|%s', substitute, sql)
        return self.db.execute(sql, values)


@pytest.fixture
def database(monkeypatch, tmp_path):
    database = Database()
    monkeypatch.setattr(sources, 'connect', lambda: database)
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'SOURCES_DIR', tmp_path / 'sources')
    monkeypatch.setattr(config, 'VAR', tmp_path / 'var')
    return database


def record(revision='first'):
    sources.record(id='upload-one', project_id='personal', kind='upload',
                   name='one.md', path='sources/upload/one.md', sha=revision)


def test_later_source_revision_stays_queued_when_old_compilation_finishes(database):
    record('first')
    sources.set_queued('personal', ['upload-one'], True)
    record('second')
    assert not sources.absorbed('personal', 'upload-one', 'first')
    row = sources.get('upload-one')
    assert row['absorbed_sha'] is None and row['wiki_queued_at'] is not None
    assert sources.eligible('personal', ['upload-one']) == ['upload-one']
    assert sources.absorbed('personal', 'upload-one', 'second')
    row = sources.get('upload-one')
    assert row['absorbed_sha'] == 'second' and row['wiki_queued_at'] is None


def test_unchanged_upload_reindex_preserves_completed_revision_and_manual_policy(database):
    record('same')
    sources.set_policy('personal', ['upload-one'], 'manual')
    sources.set_queued('personal', ['upload-one'], True)
    assert sources.absorbed('personal', 'upload-one', 'same')
    record('same')
    row = sources.get('upload-one')
    assert row['absorbed_sha'] == 'same' and row['absorbed_at']
    assert row['absorption_policy'] == 'manual' and row['wiki_queued_at'] is None
    assert sources.auto_candidates('personal', '', 'upload') == []


def test_policy_and_unqueue_changes_are_respected_before_the_next_unit(database):
    record()
    sources.set_queued('personal', ['upload-one'], True)
    assert sources.eligible('personal', ['upload-one'], automatic=True) == ['upload-one']
    sources.set_policy('personal', ['upload-one'], 'manual')
    assert sources.eligible('personal', ['upload-one'], automatic=True) == []
    assert sources.eligible('personal', ['upload-one']) == ['upload-one']
    sources.set_policy('personal', ['upload-one'], 'exclude')
    assert sources.eligible('personal', ['upload-one']) == []
    assert sources.set_queued('personal', ['upload-one'], True) == 0
    sources.set_policy('personal', ['upload-one'], 'inherit')
    assert sources.eligible('personal', ['upload-one'], automatic=True) == []
    sources.set_queued('personal', ['upload-one'], True)
    sources.set_queued('personal', ['upload-one'], False)
    assert sources.eligible('personal', ['upload-one']) == []


def test_another_project_cannot_mark_a_source_revision_complete(database):
    record()
    sources.set_queued('personal', ['upload-one'], True)
    assert not sources.absorbed('other-project', 'upload-one', 'first')
    assert sources.eligible('other-project', ['upload-one']) == []
    assert sources.get('upload-one')['wiki_queued_at'] is not None
