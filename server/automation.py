"""Connection-owned schedules. The app worker executes them while Cairn is running."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import connect

DEFAULTS = dict(sync_enabled=False, schedule_minutes=60, cron_expression='',
                timezone='Asia/Kolkata', auto_absorb=False,
                # Syncing is free; absorbing is the only step that spends the
                # model. So when it runs, and how much it may take, are their
                # own answers rather than a flag on the sync schedule.
                absorb_trigger='manual', absorb_cron='',
                # 0 = no ceiling of its own: the whole queue, and the
                # pipeline's own token default. What ran before this existed.
                absorb_limit_units=0, absorb_max_tokens=0,
                absorb_guardrail_units=0)

# 'sync'     — drain the queue as soon as new content lands
# 'schedule' — sync often, absorb on absorb_cron
# 'manual'   — nothing is absorbed until asked
TRIGGERS = ('manual', 'sync', 'schedule')

# Ceilings on the ceilings. A budget is a guard against a surprise bill, so a
# typo in one must not be able to become the surprise.
LIMITS = dict(absorb_limit_units=(0, 100_000), absorb_max_tokens=(0, 2_000_000_000),
              absorb_guardrail_units=(0, 1_000_000))


def absorbs_on_sync(policy: dict) -> bool:
    """Whether a sync of this connection should absorb what it brings in.

    Reads the trigger, falling back to the older boolean. A policy dict can
    arrive from a row written before absorb_trigger existed, so deriving one
    from the other here keeps every caller from having to.
    """
    trigger = policy.get('absorb_trigger')
    if trigger in TRIGGERS:
        return trigger == 'sync'
    return bool(policy.get('auto_absorb'))


def _cron(expression, label: str) -> str:
    if not isinstance(expression, str):
        raise ValueError(f'{label} must be a string')
    expression = expression.strip()
    if expression:
        from croniter import croniter
        if len(expression.split()) != 5 or not croniter.is_valid(expression):
            raise ValueError(f'{label}: use a valid five-field cron expression, such as 0 9 * * *')
    return expression


def validate(payload: dict, current: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('policy must be an object')
    unknown = set(payload) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown policy fields: {', '.join(sorted(unknown))}")
    result = {**DEFAULTS, **{k: v for k, v in (current or {}).items() if k in DEFAULTS}, **payload}

    # The boolean and the trigger are one setting stored twice, so that older
    # callers keep working. Whichever the caller actually sent decides.
    if 'absorb_trigger' in payload:
        if result['absorb_trigger'] not in TRIGGERS:
            raise ValueError(f"absorb_trigger must be one of {', '.join(TRIGGERS)}")
        result['auto_absorb'] = result['absorb_trigger'] == 'sync'
    elif 'auto_absorb' in payload:
        result['absorb_trigger'] = 'sync' if payload['auto_absorb'] else 'manual'
    elif result['absorb_trigger'] not in TRIGGERS:
        raise ValueError(f"absorb_trigger must be one of {', '.join(TRIGGERS)}")

    for name, (low, high) in LIMITS.items():
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{name} must be between {low} and {high}')

    result['absorb_cron'] = _cron(result['absorb_cron'], 'absorb_cron')
    if result['absorb_trigger'] == 'schedule' and not result['absorb_cron']:
        raise ValueError('absorbing on a schedule needs absorb_cron')

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
    result['cron_expression'] = _cron(result['cron_expression'], 'cron_expression')
    return result


def _from_cron(expression: str, zone: str, now: datetime) -> datetime:
    from croniter import croniter
    local = now.astimezone(ZoneInfo(zone))
    return croniter(expression, local).get_next(datetime).astimezone(timezone.utc)


def next_time(policy: dict, now: datetime | None = None) -> datetime | None:
    if not policy['sync_enabled']:
        return None
    now = now or datetime.now(timezone.utc)
    if policy.get('cron_expression'):
        return _from_cron(policy['cron_expression'], policy['timezone'], now)
    return now + timedelta(minutes=policy['schedule_minutes'])


def next_absorb_time(policy: dict, now: datetime | None = None) -> datetime | None:
    """Absorbing on its own clock: sync hourly, write up the queue at 02:00.
    Only 'schedule' has a time of its own — 'sync' rides the sync it follows
    and 'manual' has none."""
    if policy.get('absorb_trigger') != 'schedule' or not policy.get('absorb_cron'):
        return None
    return _from_cron(policy['absorb_cron'], policy['timezone'], now or datetime.now(timezone.utc))


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
                   'next_run_at': None, 'last_run_at': None, 'absorb_next_run_at': None}


def save(connection_id: str, payload: dict) -> dict:
    current = get(connection_id)
    policy = validate(payload, current)
    with connect() as c:
        c.execute('INSERT INTO brain_connection_policies '
                  '(connection_id, project_id, sync_enabled, schedule_minutes, cron_expression, timezone, '
                  ' auto_absorb, next_run_at, absorb_trigger, absorb_cron, absorb_limit_units, '
                  ' absorb_max_tokens, absorb_guardrail_units, absorb_next_run_at) '
                  'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                  'ON CONFLICT (connection_id) DO UPDATE SET '
                  'sync_enabled=EXCLUDED.sync_enabled, schedule_minutes=EXCLUDED.schedule_minutes, '
                  'cron_expression=EXCLUDED.cron_expression, timezone=EXCLUDED.timezone, '
                  'auto_absorb=EXCLUDED.auto_absorb, next_run_at=EXCLUDED.next_run_at, '
                  'absorb_trigger=EXCLUDED.absorb_trigger, absorb_cron=EXCLUDED.absorb_cron, '
                  'absorb_limit_units=EXCLUDED.absorb_limit_units, '
                  'absorb_max_tokens=EXCLUDED.absorb_max_tokens, '
                  'absorb_guardrail_units=EXCLUDED.absorb_guardrail_units, '
                  'absorb_next_run_at=EXCLUDED.absorb_next_run_at',
                  (connection_id, current['project_id'], policy['sync_enabled'], policy['schedule_minutes'],
                   policy['cron_expression'], policy['timezone'], policy['auto_absorb'], next_time(policy),
                   policy['absorb_trigger'], policy['absorb_cron'], policy['absorb_limit_units'],
                   policy['absorb_max_tokens'], policy['absorb_guardrail_units'],
                   next_absorb_time(policy)))
    return get(connection_id)
