"""Connection-owned schedules. The app worker executes them while Cairn is running."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import connect

DEFAULTS = dict(sync_enabled=False, schedule_minutes=60, cron_expression='',
                timezone='Asia/Kolkata', auto_absorb=False)


def validate(payload: dict, current: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('policy must be an object')
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown policy fields: {', '.join(sorted(unknown))}")
    result = {**DEFAULTS, **{k: v for k, v in (current or {}).items() if k in DEFAULTS}, **payload}
    for flag in ('sync_enabled', 'auto_absorb'):
        if not isinstance(result[flag], bool):
            raise ValueError(f'{flag} must be true or false')
    minutes = result['schedule_minutes']
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 43200:
        raise ValueError('schedule_minutes must be between 1 and 43200')
    try:
        ZoneInfo(result['timezone'])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('timezone must be a valid IANA time zone') from None
    expression = result['cron_expression']
    if not isinstance(expression, str):
        raise ValueError('cron_expression must be a string')
    expression = expression.strip()
    if expression:
        from croniter import croniter
        if len(expression.split()) != 5 or not croniter.is_valid(expression):
            raise ValueError('use a valid five-field cron expression, such as 0 9 * * *')
    result['cron_expression'] = expression
    return result


def next_time(policy: dict, now: datetime | None = None) -> datetime | None:
    if not policy['sync_enabled']:
        return None
    now = now or datetime.now(timezone.utc)
    if policy.get('cron_expression'):
        from croniter import croniter
        local = now.astimezone(ZoneInfo(policy['timezone']))
        return croniter(policy['cron_expression'], local).get_next(datetime).astimezone(timezone.utc)
    return now + timedelta(minutes=policy['schedule_minutes'])


def connection_project(connection_id: str) -> str:
    with connect() as c:
        table = 'brain_repos' if '/' in connection_id else 'brain_connector_connections'
        row = c.execute(f'SELECT project_id FROM {table} WHERE id = %s', (connection_id,)).fetchone()
    if not row:
        raise KeyError('connection not found')
    return row['project_id']


def get(connection_id: str) -> dict:
    project_id = connection_project(connection_id)
    with connect() as c:
        row = c.execute('SELECT * FROM brain_connection_policies WHERE connection_id = %s',
                        (connection_id,)).fetchone()
    return row or {**DEFAULTS, 'connection_id': connection_id, 'project_id': project_id,
                   'next_run_at': None, 'last_run_at': None}


def save(connection_id: str, payload: dict) -> dict:
    current = get(connection_id)
    policy = validate(payload, current)
    with connect() as c:
        c.execute('INSERT INTO brain_connection_policies '
                  '(connection_id, project_id, sync_enabled, schedule_minutes, cron_expression, timezone, auto_absorb, next_run_at) '
                  'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (connection_id) DO UPDATE SET '
                  'sync_enabled=EXCLUDED.sync_enabled, schedule_minutes=EXCLUDED.schedule_minutes, '
                  'cron_expression=EXCLUDED.cron_expression, timezone=EXCLUDED.timezone, '
                  'auto_absorb=EXCLUDED.auto_absorb, next_run_at=EXCLUDED.next_run_at',
                  (connection_id, current['project_id'], policy['sync_enabled'], policy['schedule_minutes'],
                   policy['cron_expression'], policy['timezone'], policy['auto_absorb'], next_time(policy)))
    return get(connection_id)
