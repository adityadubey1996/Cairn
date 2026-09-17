"""Self-check for the grouping filter. No DB: the aggregate itself is Postgres's
job, but which column a group narrows on — and whether it narrows at all — is
decided here, and getting it wrong filters the wrong column silently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.sources import GROUP_BY, _filters  # noqa: E402


def test_a_group_narrows_on_its_kinds_expression():
    where, args = _filters("p1", "", "gchat", None, None, "Dev-Group")
    assert f"{GROUP_BY['gchat']} = %s" in where, where
    assert args == ["p1", "gchat", "Dev-Group"], args


def test_a_group_without_its_kind_is_dropped_not_guessed():
    """There is no expression to filter on, and picking one would narrow the
    wrong column while the UI showed the group as applied."""
    where, args = _filters("p1", "", None, None, None, "Dev-Group")
    assert args == ["p1"], args
    assert not any("= %s" in w for w in where[1:]), where


def test_the_row_list_and_the_group_counts_share_one_where():
    """Both readers call _filters, so a filter added to one cannot go missing
    from the other — which is what made a chip read 146 next to a list of 12."""
    common = ("p1", "notes", "gdrive", "ok", "conn-1")
    counts_where, counts_args = _filters(*common)
    rows_where, rows_args = _filters(*common, None)
    assert counts_where == rows_where and counts_args == rows_args


def test_connectors_are_themselves_a_group_level():
    """The chip row derives connectors from this rather than from one page of
    rows, which only ever showed whichever connector synced last."""
    assert GROUP_BY[""] == "kind"


def test_the_chat_space_separator_still_matches_what_the_feeder_writes():
    """GROUP_BY splits gchat names on ' — '. The feeder builds them with the
    same string; if it ever changes, every space silently becomes one group."""
    assert " — " in GROUP_BY["gchat"]
    feeder = (Path(__file__).resolve().parent.parent
              / "feeders" / "chat" / "sync.py").read_text()
    assert 'name=f"{space.get(\'displayName\') or space_id} — {day}"' in feeder


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("source groups: all checks passed")
