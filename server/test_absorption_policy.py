#!/usr/bin/env python3
"""The absorption plan: when absorbing runs, and how much it may take.

No database. validate() and the two next_*_time() helpers are the whole of the
decision — everything downstream just reads what they agreed.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import automation  # noqa: E402


def policy(**overrides):
    return automation.validate(overrides)


def test_a_new_connection_absorbs_nothing_until_asked():
    got = policy()
    assert got['absorb_trigger'] == 'manual'
    assert got['auto_absorb'] is False
    # No ceiling of its own: the whole queue, at the pipeline's token default.
    assert got['absorb_limit_units'] == 0
    assert got['absorb_max_tokens'] == 0


def test_the_trigger_and_the_old_boolean_stay_one_setting():
    assert policy(absorb_trigger='sync')['auto_absorb'] is True
    assert policy(absorb_trigger='manual')['auto_absorb'] is False
    assert policy(absorb_trigger='schedule', absorb_cron='0 2 * * *')['auto_absorb'] is False
    # An older caller that only knows the boolean still gets a coherent policy.
    assert policy(auto_absorb=True)['absorb_trigger'] == 'sync'
    assert policy(auto_absorb=False)['absorb_trigger'] == 'manual'


def test_absorbing_on_a_schedule_needs_a_schedule():
    with pytest.raises(ValueError, match='absorb_cron'):
        policy(absorb_trigger='schedule')
    assert policy(absorb_trigger='schedule', absorb_cron='0 2 * * *')['absorb_cron'] == '0 2 * * *'


def test_an_unreadable_absorb_cron_is_refused_by_name():
    with pytest.raises(ValueError, match='absorb_cron'):
        policy(absorb_trigger='schedule', absorb_cron='every night')
    # The sync schedule reports under its own name, so the form can say which.
    with pytest.raises(ValueError, match='cron_expression'):
        policy(cron_expression='0 25 * * *')


def test_an_unknown_trigger_is_refused():
    with pytest.raises(ValueError, match='absorb_trigger'):
        policy(absorb_trigger='whenever')


def test_budgets_are_whole_numbers_inside_a_sane_ceiling():
    assert policy(absorb_limit_units=40)['absorb_limit_units'] == 40
    assert policy(absorb_max_tokens=4_000_000)['absorb_max_tokens'] == 4_000_000
    for bad in (-1, 1.5, '40', True):
        with pytest.raises(ValueError, match='absorb_limit_units'):
            policy(absorb_limit_units=bad)
    with pytest.raises(ValueError, match='absorb_max_tokens'):
        policy(absorb_max_tokens=10 ** 12)


def test_an_unknown_field_is_still_refused():
    with pytest.raises(ValueError, match='unknown policy fields'):
        policy(absorb_everything=True)


def test_only_a_scheduled_absorb_has_a_time_of_its_own():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    assert automation.next_absorb_time(policy(absorb_trigger='manual'), now) is None
    # 'sync' rides the sync it follows, so it has no clock of its own.
    assert automation.next_absorb_time(policy(absorb_trigger='sync'), now) is None
    at = automation.next_absorb_time(
        policy(absorb_trigger='schedule', absorb_cron='0 2 * * *', timezone='UTC'), now)
    assert (at.hour, at.minute) == (2, 0)
    assert at > now


def test_the_absorb_clock_follows_the_policy_timezone_not_the_servers():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    common = dict(absorb_trigger='schedule', absorb_cron='0 2 * * *')
    in_utc = automation.next_absorb_time(policy(timezone='UTC', **common), now)
    in_kolkata = automation.next_absorb_time(policy(timezone='Asia/Kolkata', **common), now)
    assert in_utc != in_kolkata


def test_the_sync_schedule_is_untouched_by_any_of_this():
    got = policy(sync_enabled=True, cron_expression='0 9 * * 1-5', timezone='UTC')
    at = automation.next_time(got, datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert (at.hour, at.weekday()) == (9, 0)     # the following Monday
