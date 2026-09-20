from datetime import datetime, timezone

import pytest

from server.automation import validate, next_time


def test_disabled_schedule_has_no_next_run():
    assert next_time(validate({})) is None


def test_interval_and_timezone_cron_have_distinct_next_occurrences():
    at = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
    interval = validate({'sync_enabled': True, 'schedule_minutes': 15})
    assert next_time(interval, at).isoformat() == '2026-09-20T03:15:00+00:00'
    daily = validate({'sync_enabled': True, 'cron_expression': '0 9 * * *', 'timezone': 'Asia/Kolkata'})
    assert next_time(daily, at).isoformat() == '2026-09-20T03:30:00+00:00'


@pytest.mark.parametrize('payload', [
    {'schedule_minutes': 0}, {'schedule_minutes': True}, {'sync_enabled': 'false'},
    {'auto_absorb': 'yes'}, {'cron_expression': '* * * * * *'},
    {'cron_expression': 'bad'}, {'timezone': 'Mars/Olympus'}, {'unexpected': 3},
])
def test_invalid_policy_cannot_be_saved(payload):
    with pytest.raises(ValueError):
        validate(payload)


def test_patching_absorption_preserves_schedule():
    before = validate({'sync_enabled': True, 'cron_expression': '0 9 * * 1-5'})
    after = validate({'auto_absorb': True}, before)
    assert after['sync_enabled'] and after['auto_absorb']
    assert after['cron_expression'] == before['cron_expression']
