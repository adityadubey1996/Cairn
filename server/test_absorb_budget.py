#!/usr/bin/env python3
"""The budget has to reach the process that spends it.

A ceiling stored in the database and never passed on argv is a number on a
screen, so these check the two hand-offs: policy -> job, and job -> argv.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import connections, jobs  # noqa: E402


def args_for(job: dict) -> list[str]:
    return jobs._arguments({'project_id': 'p1', 'connector': 'gdrive', **job}, 'r1')


def test_a_connection_with_no_ceiling_runs_exactly_as_before():
    assert connections._budget({'absorb_limit_units': 0, 'absorb_max_tokens': 0}) == {}
    argv = args_for({'absorb': True})
    assert '--absorb-limit' not in argv
    assert '--max-tokens' not in argv


def test_both_ceilings_reach_the_command_line():
    budget = connections._budget({'absorb_limit_units': 40, 'absorb_max_tokens': 4_000_000})
    assert budget == {'limit_units': 40, 'max_tokens': 4_000_000}
    argv = args_for({'absorb': True, 'budget': budget})
    assert argv[argv.index('--absorb-limit') + 1] == '40'
    assert argv[argv.index('--max-tokens') + 1] == '4000000'


def test_a_units_ceiling_alone_leaves_tokens_at_the_pipeline_default():
    argv = args_for({'absorb': True, 'budget': connections._budget(
        {'absorb_limit_units': 5, 'absorb_max_tokens': 0})})
    assert '--absorb-limit' in argv
    assert '--max-tokens' not in argv


def test_a_token_ceiling_is_what_bounds_one_enormous_document():
    # A unit limit cannot: a 900-page PDF is one unit and many model calls, so
    # "5 units" says nothing about what it will cost.
    argv = args_for({'absorb': True, 'budget': connections._budget(
        {'absorb_limit_units': 0, 'absorb_max_tokens': 250_000})})
    assert argv[argv.index('--max-tokens') + 1] == '250000'


def test_the_budget_is_absent_from_a_run_that_does_not_absorb():
    argv = args_for({'absorb': False})
    assert '--skip-absorb' in argv
    assert '--absorb-limit' not in argv
