#!/usr/bin/env python3
"""Self-check for oversized code-package splitting.  Run: python3 test_packaging.py

pkg_root stops at the nearest PKG_MARKER. Python repos put __init__.py at every
level so packages come out small; pnpm puts one package.json per workspace
package, so an entire SPA collapses into a single unit that no article can
describe. split_oversized subdivides those by path level.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ingest import split_oversized  # noqa: E402


def pkg(paths):
    d = defaultdict(list)
    for p in paths:
        d["artifacts/frontend"].append(("sha" + p, p))
    return dict(d)


def converge(packages, limit):
    while True:
        split = split_oversized(packages, limit)
        if split.keys() == packages.keys():
            return split
        packages = split


def test_small_package_is_untouched():
    p = pkg(["artifacts/frontend/src/App.tsx", "artifacts/frontend/src/main.tsx"])
    assert converge(p, 120) == p


def test_oversized_package_splits_by_domain():
    paths = (
        [f"artifacts/frontend/shared/components/C{i}.tsx" for i in range(100)]
        + [f"artifacts/frontend/shared/pages/P{i}.tsx" for i in range(50)]
        + [f"artifacts/frontend/alpha-well/pages/P{i}.tsx" for i in range(50)]
    )
    out = converge(pkg(paths), 120)
    assert "artifacts/frontend/alpha-well" in out, out.keys()
    # shared was still 150 > 120, so it split one level further
    assert "artifacts/frontend/shared/components" in out, out.keys()
    assert "artifacts/frontend/shared/pages" in out, out.keys()
    assert all(len(m) <= 120 for m in out.values()), {k: len(v) for k, v in out.items()}


def test_single_deep_child_still_gets_a_more_specific_name():
    """150 files all under one subdir: no fan-out, but the package should be
    named for where the code actually is, not the workspace root."""
    paths = [f"artifacts/frontend/shared/components/C{i}.tsx" for i in range(150)]
    out = converge(pkg(paths), 120)
    assert list(out) == ["artifacts/frontend/shared/components"], out.keys()


def test_every_file_survives_the_split():
    paths = [f"artifacts/frontend/shared/components/C{i}.tsx" for i in range(200)] + [
        f"artifacts/frontend/alpha-well/pages/P{i}.tsx" for i in range(130)
    ]
    out = converge(pkg(paths), 120)
    assert sorted(r for mem in out.values() for _s, r in mem) == sorted(paths)


def test_files_directly_in_package_root_stay_there():
    paths = [f"artifacts/frontend/shared/C{i}.tsx" for i in range(130)] + [
        "artifacts/frontend/package.json", "artifacts/frontend/vite.config.ts",
    ]
    out = converge(pkg(paths), 120)
    assert ("sha" + "artifacts/frontend/package.json",
            "artifacts/frontend/package.json") in out["artifacts/frontend"]


def test_unsplittable_package_terminates():
    """All files at the same leaf: no deeper level exists. Must not loop forever."""
    paths = [f"artifacts/frontend/x/C{i}.tsx" for i in range(200)]
    out = converge(pkg(paths), 120)
    assert len(out) == 1 and len(next(iter(out.values()))) == 200


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all packaging checks passed")
