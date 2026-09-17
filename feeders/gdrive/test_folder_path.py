"""Self-check for the folder walk. The cache is the seam: seeding it makes the
walk pure, so none of this touches Drive.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from feeders.gdrive import sync  # noqa: E402


def seed(tree):
    """tree: {folder_id: (name, parent_id)}. None models a folder Drive hides."""
    sync._FOLDER_CACHE.clear()
    sync._FOLDER_CACHE.update(tree)


def test_a_file_with_no_parents_has_no_folder():
    """The common case for this corpus: a transcript shared out of someone
    else's Drive, which the API returns with no parents at all."""
    seed({})
    assert sync._folder_path({"id": "f1"}) is None
    assert sync._folder_path({"id": "f1", "parents": []}) is None


def test_the_path_is_the_whole_chain_top_down():
    """Folder names repeat — this corpus has two called "findings" — so the
    immediate parent alone cannot tell them apart."""
    seed({"deep": ("findings", "mid"), "mid": ("research", "root"),
          "root": ("My Drive", None)})
    assert sync._folder_path({"parents": ["deep"]}) == "research/findings"


def test_my_drive_survives_alone_but_not_as_a_prefix():
    seed({"root": ("My Drive", None)})
    assert sync._folder_path({"parents": ["root"]}) == "My Drive"


def test_an_unreadable_parent_ends_the_walk_without_failing():
    """A 403 partway up is not an error: keep what resolved, drop the rest."""
    seed({"deep": ("findings", "hidden"), "hidden": None})
    assert sync._folder_path({"parents": ["deep"]}) == "findings"


def test_an_unreadable_immediate_parent_is_no_folder():
    seed({"hidden": None})
    assert sync._folder_path({"parents": ["hidden"]}) is None


def test_a_parent_cycle_terminates():
    """Drive should not produce one; a walk over data this code does not own
    must not hang if it ever does."""
    seed({"a": ("A", "b"), "b": ("B", "a")})
    assert sync._folder_path({"parents": ["a"]}) == "B/A"


def test_a_slash_inside_a_folder_name_does_not_become_a_level():
    """Every Google Meet folder is named "<title> - YYYY/MM/DD HH:MM TZ", so a
    raw join turned one folder into four nested ones."""
    seed({"meet-child": ("Connect with Satya - 2026/08/27 10:03 IST", "meet-root"),
          "meet-root": ("Google Meet", None)})
    got = sync._folder_path({"parents": ["meet-child"]})
    assert got.count("/") == 1, got
    assert got == "Google Meet/Connect with Satya - 2026\u221508\u221527 10:03 IST", got


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("gdrive folder path: all checks passed")
