#!/usr/bin/env python3
"""Self-check for reading_list().  Run: python3 test_reading_list.py

ingest.py renders both blocks below as `- `name` — `path``, so the OPEN_NEXT
regex used to match the alphabetical "Declared symbols" sample as well as the
graph-derived "Open next" block: 30 hits where 3 were intended, with graphify's
priority ordering lost before it reached the model. gather() truncates at 60k
chars, so on a large package the ordering decides which files survive.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from absorb_runner import reading_list  # noqa: E402

PKG = "backend_new/src/application/project_analysis_context"

ENTRY = f"""---
id: pkg-project-analysis-context
path: "{PKG}"
---
**Declared symbols (sample):**
- `parse_results_json` — `{PKG}/mappers.py`
- `_anchor_item_dict` — `{PKG}/repository.py`
- `EconomicsSnapshot` — `{PKG}/types.py`
- `test_pdp_mapper` — `{PKG}/test_mappers.py`

**Called by:** `backend_new/src/api/routers`(5)
**Calls into:** `backend_new/src/application/well_chat`(2), `backend_new/src/infrastructure/ipfs`(1)

**Open next** — what the rest of the system actually calls:
- `build_project_analysis_context()` — `{PKG}/service.py:L25` ← 3 external via projects.py
- `load_analysis_run_row_for_anchor()` — `{PKG}/repository.py:L136` ← 1 external via projects.py

**Files:**
- `{PKG}/__init__.py`
- `{PKG}/mappers.py`
"""


def test_graph_entry_points_come_first():
    got = reading_list(ENTRY)
    assert got[0] == f"{PKG}/service.py", got[:3]
    assert got[1] == f"{PKG}/repository.py", got[:3]


def test_declared_symbols_block_is_not_a_reading_list():
    got = reading_list(ENTRY)
    # types.py and test_mappers.py appear ONLY in the symbol sample. If they are
    # here, the regex matched that block and the ordering is alphabetical noise.
    assert f"{PKG}/types.py" not in got, got
    assert f"{PKG}/test_mappers.py" not in got, got


def test_calls_into_dependencies_are_appended():
    got = reading_list(ENTRY)
    assert "backend_new/src/application/well_chat" in got, got
    assert "backend_new/src/infrastructure/ipfs" in got, got
    # ...and after the entry points, never before them.
    assert got.index("backend_new/src/application/well_chat") > 1, got


def test_falls_back_to_files_when_no_graph():
    """graphify-out/ is gitignored, so a fresh clone or CI has no graph and the
    three graph lines are simply absent. The reading list must still work."""
    nograph = ENTRY.split("**Called by:**")[0] + "**Files:**\n- `%s/mappers.py`\n" % PKG
    got = reading_list(nograph)
    assert got, "empty reading list with no graph"
    assert f"{PKG}/mappers.py" in got, got


def test_init_files_are_skipped():
    assert f"{PKG}/__init__.py" not in reading_list(ENTRY)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("reading_list: all checks passed")
