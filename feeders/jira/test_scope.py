"""A scope is only real if it reaches the query. These pin the JQL."""
import re

from feeders.jira import sync


def where(jql: str) -> str:
    return jql.replace(" ORDER BY updated ASC", "")


def test_no_scope_is_the_query_that_shipped_before():
    assert sync.scoped_jql(None) == "ORDER BY updated ASC"
    assert sync.scoped_jql({}) == "ORDER BY updated ASC"


def test_projects_become_an_in_clause():
    assert where(sync.scoped_jql({"items": ["DEL", "PLAT"]})) \
        == 'project in ("DEL", "PLAT")'


def test_every_filter_is_anded_and_the_ordering_stays_last():
    jql = sync.scoped_jql({"items": ["DEL"], "issue_types": ["Story", "Bug"],
                           "statuses": ["To Do"]})
    assert jql.endswith("ORDER BY updated ASC")
    assert jql.count("ORDER BY") == 1
    assert where(jql) == ('project in ("DEL") AND issuetype in ("Story", "Bug") '
                          'AND statusCategory in ("To Do")')


def test_a_name_with_a_space_or_quote_cannot_break_out_of_its_literal():
    jql = sync.scoped_jql({"items": ["DEL"], "issue_types": ['Sub-task', 'He said "no"']})
    assert 'issuetype in ("Sub-task", "He said \\"no\\"")' in jql


def test_the_day_window_applies_to_a_first_sync_only():
    # No watermark: the window is the only thing bounding a full sweep.
    assert "updated >= -90d" in sync.scoped_jql({"updated_within_days": 90})
    # With a watermark, a fixed -90d ANDed on top would stop anything older
    # that changed from ever being re-read.
    incremental = sync.scoped_jql({"updated_within_days": 90},
                                  modified_after="2026-09-01T00:00:00+00:00")
    assert "updated >= -90d" not in incremental
    assert re.search(r"updated >= -\d+m", incremental), incremental


def test_the_watermark_and_the_scope_are_anded_together():
    jql = sync.scoped_jql({"items": ["DEL"]}, modified_after="2026-09-01T00:00:00+00:00")
    assert " AND " in jql
    assert 'project in ("DEL")' in jql
    assert re.search(r"updated >= -\d+m", jql)
