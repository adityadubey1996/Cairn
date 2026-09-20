"""Connection cards must not borrow another account's run or source count."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from server import connections


class ListingDB:
    def __init__(self, accounts, runs, counts, repos=()):
        self.accounts, self.runs, self.counts, self.repos = accounts, runs, counts, repos
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, sql, params):
        # Every participating table must be scoped, including the latest run.
        assert params == ('personal',)
        assert 'project_id = %s' in sql
        if 'FROM brain_connector_connections' in sql:
            self.result = self.accounts
        elif 'FROM brain_connector_runs' in sql:
            assert 'DISTINCT ON (connection_id)' in sql
            assert 'connection_id IS NOT NULL' in sql
            self.result = self.runs
        elif 'FROM brain_sources' in sql:
            assert 'GROUP BY connection_id' in sql
            self.result = self.counts
        elif 'FROM brain_repos' in sql:
            self.result = self.repos
        else:
            raise AssertionError(sql)
        return self

    def fetchall(self):
        return self.result


def account(id_):
    return dict(id=id_, project_id='personal', kind='links', name=id_, config={},
                created_at=None, synced_at=None)


def run(connection_id, status, *, stale=False):
    now = datetime.now(timezone.utc)
    beat = now - timedelta(hours=1) if stale else now
    return dict(id=f'run-{connection_id}', connection_id=connection_id,
                connector_id='links', status=status, items_written=1,
                started_at=beat, heartbeat_at=beat,
                finished_at=None if status in ('queued', 'running', 'cancelling') else now,
                error='Some pages failed' if status == 'partial' else None)


def test_same_provider_accounts_keep_independent_counts_and_status():
    db = ListingDB([account('links-a'), account('links-b'), account('links-new')],
                   [run('links-a', 'queued'), run('links-b', 'partial')],
                   [{'connection_id': 'links-a', 'n': 17}, {'connection_id': 'links-b', 'n': 4}])
    with patch.object(connections, 'connect', return_value=db):
        a, b, new = connections.list_connections('personal')
    assert (a['status'], a['itemCount'], a['runId']) == ('queued', 17, 'run-links-a')
    assert (b['status'], b['itemCount'], b['error']) == ('partial', 4, 'Some pages failed')
    assert (new['status'], new['itemCount'], new['runId']) == ('never_run', 0, None)


@pytest.mark.parametrize('status', ['queued', 'running', 'cancelling', 'partial', 'stopped', 'error', 'ok'])
def test_job_state_is_not_collapsed_to_success(status):
    db = ListingDB([account('links-a')], [run('links-a', status)], [])
    with patch.object(connections, 'connect', return_value=db):
        row = connections.list_connections('personal')[0]
    assert row['status'] == status
    assert bool(row['runId']) == (status in ('queued', 'running', 'cancelling'))


def test_stale_running_job_is_interrupted_not_live():
    db = ListingDB([account('links-a')], [run('links-a', 'running', stale=True)], [])
    with patch.object(connections, 'connect', return_value=db):
        row = connections.list_connections('personal')[0]
    assert row['status'] == 'interrupted'
    assert row['runId'] is None


def test_repository_card_uses_its_job_and_source_count():
    repo = dict(id='owner/repo', owner='owner', name='repo', branch='main', state='ready',
                last_error=None, articles=12, project_id='personal', updated_at=None)
    db = ListingDB([], [run('owner/repo', 'cancelling')],
                   [{'connection_id': 'owner/repo', 'n': 8}], [repo])
    with patch.object(connections, 'connect', return_value=db):
        row = connections.list_connections('personal')[0]
    assert (row['status'], row['itemCount']) == ('cancelling', 8)
    assert row['runId'] == 'run-owner/repo'
