#!/usr/bin/env python3
"""Self-check for graph-derived package edges.  Run: python3 test_package_edges.py

fact_sheet() records imports as `mod.split(".")[0]`, so a package entry cannot
say which package a symbol came from and never says who calls in. The writer
sees each package in isolation, cannot trace a flow across boundaries, and
paraphrases whatever doc narrates it — module articles measured 2% code-grounded.

package_edges() recovers that from graphify's 18k edges. It is an itinerary for
the absorb step, not article content: recomputable facts stay a graphify query.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from graph_verify import package_edges, pkg_of  # noqa: E402


def node(nid, label, src, loc="L1"):
    return {"id": nid, "label": label, "source_file": src, "source_location": loc}


def link(s, t, relation="calls"):
    return {"source": s, "target": t, "relation": relation}


# arps is called by workflows and economics; it calls into domain/decline.
GRAPH = {
    "nodes": [
        node("a1", "run_arps()", "backend_new/src/application/arps/service.py", "L90"),
        node("a2", "ArpsRequest", "backend_new/src/application/arps/types.py", "L26"),
        node("a3", "_helper", "backend_new/src/application/arps/util.py", "L4"),
        node("w1", "run.py", "backend_new/src/application/workflows/run.py"),
        node("w2", "per_well_fit.py", "backend_new/src/application/workflows/per_well_fit.py"),
        node("e1", "dcf.py", "backend_new/src/application/economics/dcf.py"),
        node("d1", "fit_phase", "backend_new/src/domain/decline/multi_product.py"),
    ],
    "links": [
        link("w1", "a1"), link("w2", "a1"), link("e1", "a1"),   # 3 into run_arps
        link("w1", "a2"),                                        # 1 into ArpsRequest
        link("a1", "a3"),                                        # intra-package: ignored
        link("a1", "d1"),                                        # arps -> domain/decline
        link("w1", "a1", "imports"),                             # another relation counts
        link("w1", "a1", "contains"),                            # not a traversal relation
    ],
}
ARPS = "backend_new/src/application/arps"


def test_inbound_lists_calling_packages():
    e = package_edges(GRAPH)[ARPS]
    assert "backend_new/src/application/workflows" in e["inbound"], e["inbound"]
    assert "backend_new/src/application/economics" in e["inbound"], e["inbound"]


def test_outbound_lists_called_packages():
    e = package_edges(GRAPH)[ARPS]
    assert "backend_new/src/domain/decline" in e["outbound"], e["outbound"]


def test_intra_package_edges_are_excluded():
    """a1 -> a3 is inside arps; coupling to yourself says nothing."""
    e = package_edges(GRAPH)[ARPS]
    assert ARPS not in e["inbound"] and ARPS not in e["outbound"]


def test_non_traversal_relations_ignored():
    """`contains` is structural nesting, not a dependency."""
    only_contains = {"nodes": GRAPH["nodes"],
                     "links": [link("w1", "a1", "contains")]}
    assert package_edges(only_contains) == {}


def test_entry_points_rank_by_external_inbound():
    e = package_edges(GRAPH)[ARPS]
    labels = [ep[0] for ep in e["entry_points"]]
    assert labels[0] == "run_arps()", labels
    assert "_helper" not in labels, "intra-package symbol is not an entry point"


def test_entry_point_carries_file_and_location():
    e = package_edges(GRAPH)[ARPS]
    label, loc, n, callers = e["entry_points"][0]
    assert loc.endswith("arps/service.py:L90"), loc
    assert n == 4, n           # 3 calls + 1 imports
    assert "run.py" in callers, callers


def test_empty_graph_is_survivable():
    assert package_edges({}) == {}
    assert package_edges({"nodes": [], "links": []}) == {}


def test_nodes_without_source_file_do_not_crash():
    g = {"nodes": [{"id": "x", "label": "x"}, node("a1", "run_arps()", "a/b/c/d.py")],
         "links": [link("x", "a1")]}
    package_edges(g)  # must not raise


def test_pkg_of_caps_depth():
    assert pkg_of("a/b/c/d/e/f.py") == "a/b/c/d"
    assert pkg_of("a/b.py") == "a"
    assert pkg_of("top.py") == ""


def test_missing_graph_file_returns_empty_not_raises():
    """graphify-out/ is gitignored — absent on fresh clones and in CI.
    ingest.py must degrade to unenriched fact sheets, never fail."""
    sys.path.insert(0, str(Path(__file__).parent))
    from ingest import load_package_edges
    with tempfile.TemporaryDirectory() as d:
        assert load_package_edges(Path(d)) == {}
        gp = Path(d) / "graphify-out"; gp.mkdir()
        (gp / "graph.json").write_text("{not json")
        assert load_package_edges(Path(d)) == {}, "corrupt graph must not raise"
        (gp / "graph.json").write_text(json.dumps(GRAPH))
        assert ARPS in load_package_edges(Path(d))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("all package-edge checks passed")
