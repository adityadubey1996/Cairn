"""A durable, serialized local pipeline queue backed by Postgres.

Only opaque work descriptions are persisted, never provider credentials. A
runner heartbeats independently. A stale abandoned job is retried at most twice.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

from . import config
from .db import connect

log = logging.getLogger(__name__)
_children: dict[str, subprocess.Popen] = {}
_last_tick: datetime | None = None


def submit(connector: str, project_id: str, *, connection_id: str = '',
           absorb: bool = False, full: bool = False, ids: list[str] | None = None,
           repo_step: str = '', options: dict | None = None,
           budget: dict | None = None) -> dict:
    job = dict(connector=connector, project_id=project_id, connection_id=connection_id,
               absorb=absorb, full=full, ids=ids or [])
    if budget:
        job['budget'] = budget
    if repo_step:
        job.update(repo_step=repo_step, options=options or {})
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(81427001)')
        existing = c.execute(
            "SELECT id,status FROM brain_connector_runs WHERE status IN ('queued','running','cancelling') "
            'AND project_id = %s AND connector_id = %s AND COALESCE(connection_id,\'\') = %s '
            'AND job = %s::jsonb ORDER BY started_at LIMIT 1',
            (project_id, connector, connection_id, json.dumps(job))).fetchone()
        if existing:
            return {'run_id': str(existing['id']), 'status': existing['status'], 'existing': True}
        row = c.execute(
            "INSERT INTO brain_connector_runs (connector_id,project_id,connection_id,status,job,heartbeat_at) "
            "VALUES (%s,%s,%s,'queued',%s,now()) RETURNING id",
            (connector, project_id, connection_id or None, json.dumps(job))).fetchone()
        if connector == 'github':
            c.execute('UPDATE brain_connector_runs SET repo_id=%s,step=%s WHERE id=%s',
                      (connection_id, repo_step or 'sync', row['id']))
    return {'run_id': str(row['id']), 'status': 'queued', 'existing': False}


def request_cancel(run_id: str) -> dict:
    with connect() as c:
        row = c.execute('SELECT status FROM brain_connector_runs WHERE id=%s FOR UPDATE', (run_id,)).fetchone()
        if not row:
            raise KeyError('run not found')
        if row['status'] not in ('queued', 'running', 'cancelling'):
            raise ValueError(f"run is {row['status']}")
        status = 'stopped' if row['status'] == 'queued' else 'cancelling'
        c.execute('UPDATE brain_connector_runs SET cancel_requested=true, status=%s, '
                  "finished_at=CASE WHEN %s='stopped' THEN now() ELSE finished_at END WHERE id=%s",
                  (status, status, run_id))
    return {'stopping': status == 'cancelling', 'status': status}


def retry(run_id: str) -> dict:
    with connect() as c:
        row = c.execute('SELECT job,status FROM brain_connector_runs WHERE id=%s', (run_id,)).fetchone()
    if not row or not row['job']:
        raise KeyError('this run has no saved work description')
    if row['status'] in ('queued', 'running', 'cancelling'):
        raise ValueError('run is already active')
    job = row['job']
    extra = {'repo_step': job['repo_step'], 'options': job.get('options', {})} if job.get('repo_step') else {}
    return submit(job['connector'], job['project_id'], connection_id=job.get('connection_id', ''),
                  absorb=job.get('absorb', False), full=job.get('full', False), ids=job.get('ids'), **extra)


def _schedule() -> None:
    from . import automation, connections
    with connect() as c:
        rows = c.execute('SELECT * FROM brain_connection_policies WHERE sync_enabled '
                         'AND next_run_at <= now() ORDER BY next_run_at').fetchall()
    for row in rows:
        try:
            connections.sync(row['connection_id'])
            with connect() as c:
                c.execute('UPDATE brain_connection_policies SET last_run_at=now(),next_run_at=%s '
                          'WHERE connection_id=%s', (automation.next_time(row), row['connection_id']))
        except KeyError:
            with connect() as c:
                c.execute('DELETE FROM brain_connection_policies WHERE connection_id=%s', (row['connection_id'],))
        except Exception:
            log.exception('scheduled sync failed for %s', row['connection_id'])
    _schedule_absorb()


def _schedule_absorb() -> None:
    """Absorbing on its own clock: sync through the day, write up the queue at
    02:00. Separate from the sync loop above because the two answer different
    questions — one is free, the other is the only step that spends the model.

    Not gated on sync_enabled: a connection synced by hand still deserves to
    have its queue drained on a schedule.
    """
    from . import automation, connections
    with connect() as c:
        rows = c.execute("SELECT * FROM brain_connection_policies "
                         "WHERE absorb_trigger = 'schedule' AND absorb_next_run_at <= now() "
                         'ORDER BY absorb_next_run_at').fetchall()
    for row in rows:
        try:
            connections.absorb(row['connection_id'])
        except Exception:
            log.exception('scheduled absorb failed for %s', row['connection_id'])
        finally:
            # Advanced whatever happened: a failure that left the time in the
            # past would re-fire every tick.
            try:
                with connect() as c:
                    c.execute('UPDATE brain_connection_policies SET absorb_next_run_at=%s '
                              'WHERE connection_id=%s',
                              (automation.next_absorb_time(row), row['connection_id']))
            except Exception:
                log.exception('could not advance absorb schedule for %s', row['connection_id'])


def tick() -> None:
    global _last_tick
    _last_tick = datetime.now(timezone.utc)
    for rid, p in list(_children.items()):
        if p.poll() is not None:
            _children.pop(rid)
    _schedule()
    with connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(81427002)')
        c.execute("UPDATE brain_connector_runs SET status=CASE WHEN cancel_requested THEN 'stopped' "
                  "WHEN attempts < 3 THEN 'queued' ELSE 'error' END, pid=NULL, "
                  "error='Worker stopped responding; unfinished work will be retried', heartbeat_at=now() "
                  "WHERE job IS NOT NULL AND status IN ('running','cancelling') "
                  "AND heartbeat_at < now() - interval '3 minutes'")
        # A single writer protects filesystem manifests and wiki metadata.
        if c.execute("SELECT 1 FROM brain_connector_runs WHERE job IS NOT NULL "
                     "AND status IN ('running','cancelling') LIMIT 1").fetchone():
            return
        row = c.execute("SELECT id,job FROM brain_connector_runs WHERE status='queued' AND job IS NOT NULL "
                        'ORDER BY started_at FOR UPDATE SKIP LOCKED LIMIT 1').fetchone()
        if not row:
            return
        c.execute("UPDATE brain_connector_runs SET status='running',attempts=attempts+1, "
                  'heartbeat_at=now(),finished_at=NULL,error=NULL WHERE id=%s', (row['id'],))
    job, rid = row['job'], str(row['id'])
    try:
        args = _arguments(job, rid)
        env = dict(os.environ)
        if job.get('absorb') or job['connector'] == 'wiki':
            from .llm import absorb_env
            env.update(absorb_env())
        config.VAR.mkdir(parents=True, exist_ok=True)
        logfile = config.VAR / 'worker.log'
        if logfile.exists() and logfile.stat().st_size > 5_000_000:
            logfile.replace(config.VAR / 'worker.previous.log')
        with logfile.open('a') as output:
            p = subprocess.Popen(args, cwd=config.ROOT, env=env, start_new_session=True,
                                 stdout=output, stderr=subprocess.STDOUT)
        _children[rid] = p
        with connect() as c:
            c.execute('UPDATE brain_connector_runs SET pid=%s WHERE id=%s', (p.pid, rid))
    except Exception as e:
        from .pipeline_runs import finish
        finish(rid, status='error', error=str(e)[:2000])


def _arguments(job: dict, rid: str) -> list[str]:
    args = [sys.executable, str(config.ROOT/'scripts/pipeline_run.py'), '--run-id', rid,
            '--project-id', job['project_id']]
    if job['connector'] == 'wiki':
        args.append('--absorb-only')
        for uid in job.get('ids', []):
            args += ['--only-source', uid]
    else:
        args += ['--connector', job['connector'], '--skip-links']
        if job.get('connection_id'):
            args += ['--connection-id', job['connection_id']]
            from .connections import watermark
            if '/' not in job['connection_id'] and not job.get('full'):
                since = watermark(job['connection_id'])
                if since:
                    args += ['--since', since]
        if not job.get('absorb'):
            args.append('--skip-absorb')
        if job.get('full'):
            args.append('--full')
    # The plan's ceilings, handed to the process that actually spends them. A
    # budget kept only in the database would be a number on a screen.
    budget = job.get('budget') or {}
    if budget.get('max_tokens'):
        args += ['--max-tokens', str(budget['max_tokens'])]
    if budget.get('limit_units'):
        args += ['--absorb-limit', str(budget['limit_units'])]
    return args


def worker_running() -> bool:
    return bool(_last_tick and (datetime.now(timezone.utc)-_last_tick).total_seconds() < 30)
