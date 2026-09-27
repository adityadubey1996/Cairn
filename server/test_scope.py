#!/usr/bin/env python3
"""What a connection is allowed to read. No database, no network.

Validation is the safety boundary here: a scope that loses a filter, or keeps
a name a connector never offered, syncs more than the user agreed to.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import connectors, scope  # noqa: E402


def test_only_connectors_that_declare_a_scope_can_be_scoped():
    with pytest.raises(scope.Invalid):
        scope.validate("links", {"items": ["anything"]})
    assert scope.validate("jira", {"items": ["DEL"]}) == {"items": ["DEL"]}


def test_no_scope_stays_no_scope():
    # The default for a connection an OAuth callback just created. It must mean
    # "everything reachable", never "nothing".
    assert scope.validate("jira", None) == {}


def test_an_unknown_field_is_refused_rather_than_dropped():
    with pytest.raises(scope.Invalid) as e:
        scope.validate("jira", {"items": ["DEL"], "sprint": ["current"]})
    assert "sprint" in str(e.value)


def test_a_value_the_connector_never_offered_is_refused():
    with pytest.raises(scope.Invalid) as e:
        scope.validate("jira", {"items": [], "issue_types": ["Story", "Incident"]})
    assert "Incident" in str(e.value)


def test_choices_are_checked_against_the_connector_that_declares_them():
    # Drive's kinds come from _wanted() in its feeder; Jira's do not exist there.
    assert scope.validate("gdrive", {"items": [], "file_kinds": ["pdf"]})["file_kinds"] == ["pdf"]
    with pytest.raises(scope.Invalid):
        scope.validate("gdrive", {"items": [], "file_kinds": ["Story"]})


def test_items_are_deduplicated_but_keep_the_order_they_were_ticked():
    got = scope.validate("jira", {"items": ["PLAT", "DEL", "PLAT", " DEL "]})
    assert got["items"] == ["PLAT", "DEL"]


def test_a_day_window_is_a_whole_number_of_days():
    assert scope.validate("jira", {"items": [], "updated_within_days": 0}) \
        == {"items": [], "updated_within_days": 0}
    for bad in (-1, 1.5, "90", True, 10_000):
        with pytest.raises(scope.Invalid):
            scope.validate("jira", {"items": [], "updated_within_days": bad})


def test_a_scope_cannot_name_an_unbounded_number_of_things():
    with pytest.raises(scope.Invalid):
        scope.validate("jira", {"items": [f"P{n}" for n in range(scope.MAX_ITEMS + 1)]})


def test_items_must_be_ids():
    for bad in ("DEL", [1], [""], [None]):
        with pytest.raises(scope.Invalid):
            scope.validate("jira", {"items": bad})


def test_every_scopeable_connector_declares_choices_its_feeder_can_apply():
    # A Choice with fixed values that the feeder never reads is a filter the
    # form offers and the sync ignores — the exact failure this guards.
    for connector in connectors.REGISTRY:
        if not connector.scope:
            continue
        assert connector.scope.kind, connector.id
        assert callable(connector.scope.options), connector.id
        for field in connector.scope.fields:
            assert field.name, connector.id
            assert set(field.default) <= set(field.values), (connector.id, field.name)


def test_defaults_come_from_the_connector_not_from_here():
    assert scope.defaults("jira")["issue_types"] == ["Story", "Bug", "Task", "Epic"]
    assert scope.defaults("gdrive")["file_kinds"] == ["document", "pdf", "office", "text"]
    assert scope.defaults("links") == {}


def test_a_connection_id_with_no_row_behind_it_syncs_unscoped():
    """Scope only narrows. A connector that could not read its scope must sync
    everything, never fail and never sync nothing.

    connection_id is also passed for attribution alone — a hand run, a test —
    so looking it up has to be allowed to come back empty.
    """
    from feeders.chat import sync as chat
    from feeders.gdrive import sync as drive

    everything = [{"name": "spaces/A"}, {"name": "spaces/B"}]
    original = chat.list_spaces
    chat.list_spaces = lambda: list(everything)
    try:
        assert chat.scoped_spaces(None) == everything
        assert chat.scoped_spaces({}) == everything
        assert chat.scoped_spaces({"items": []}) == everything
        assert chat.scoped_spaces({"items": ["spaces/B"]}) == [{"name": "spaces/B"}]
    finally:
        chat.list_spaces = original

    readable = {"mimeType": "application/pdf", "name": "report.pdf"}
    assert drive.wanted_by_scope(readable, None) is True
    assert drive.wanted_by_scope(readable, {"file_kinds": ["pdf"]}) is True
    assert drive.wanted_by_scope(readable, {"file_kinds": ["document"]}) is False
    # A kind Cairn cannot read stays out however the scope is written.
    assert drive.wanted_by_scope({"mimeType": "image/png", "name": "a.png"},
                                 {"file_kinds": ["pdf", "document"]}) is False


def test_a_tracked_repo_answers_not_scopeable_rather_than_404():
    """A repo is a connection on the Connect screen but a brain_repos row
    underneath. Every card asks what it reads, so the answer for one has to be
    'nothing to choose', not an error the screen quietly swallows."""
    assert scope._spec("github") is None
    with pytest.raises(scope.Invalid):
        scope.validate("github", {"items": ["anything"]})
