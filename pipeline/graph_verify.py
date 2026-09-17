#!/usr/bin/env python3
"""
graph_verify.py — graph-backed checks for the LLM wiki.

Loads graphify-out/graph.json and scans wiki/**/*.md claim tags:

  [verified: path@sha]  [code: path@sha]  [doc: path@sha]

For code-shaped paths still in the wiki corpus (not .wikiignore'd), reports
when no matching node exists in the graph → suggest demote to [gap] / stale.

Modes:
  (default)     claim path checks → wiki/_graph_verify.json + .md
  --gaps        ranked god-nodes with no wiki mention (for /wiki breakdown)

    python .cursor/skills/wiki/scripts/graph_verify.py
    python .cursor/skills/wiki/scripts/graph_verify.py --gaps --top 40
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

CLAIM_RE = re.compile(
    r"\[(verified|code|doc):\s*([^@\]]+?)@([a-fA-F0-9]+)\]"
)
# Ingest unit ids look like data-backend-new-... / pkg-foo — not filesystem paths.
UNIT_ID_RE = re.compile(r"^(data|pkg|backend|asset)-[a-z0-9]+(?:-[a-z0-9]+)+$")
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".sol", ".sql", ".daml", ".sh", ".ps1"}
# Paths we never expect as AST file nodes (ops/config/docs); skip quietly.
SKIP_EXT = {
    ".md", ".rst", ".txt", ".yml", ".yaml", ".toml", ".ini", ".conf",
    ".json", ".csv", ".tsv", ".parquet", ".xlsx", ".docx", ".pdf", ".pptx",
    ".excalidraw", ".mmd", ".html", ".css", ".svg", ".png", ".jpg",
}
SKIP_GAP_LABELS = {
    "testclient", "magicmock", "fixture", "pytest", "unittest", "mock",
}
# Feeder-sourced claims cite a URI, not a repo path: gchat://spaces/…,
# https://youtube.com/…, meet://…  A code graph never contains these, so without
# this guard every conversation- or meeting-sourced claim classifies as "missing"
# and gets demoted to [gap] on the first verify — silently undoing the feeders.
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)


def load_wikiignore(repo: Path) -> list[str]:
    path = repo / ".wikiignore"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.replace("\\", "/"))
    return out


def ignored(rel: str, patterns: list[str]) -> bool:
    p = rel.replace("\\", "/").lstrip("./")
    for pat in patterns:
        if pat.endswith("/"):
            root = pat.rstrip("/")
            if p == root or p.startswith(root + "/"):
                return True
        else:
            root = pat.rstrip("/")
            if p == root or p.startswith(root + "/"):
                return True
    return False


def norm_path(p: str) -> str:
    p = p.replace("\\", "/").strip().strip("`").strip('"').strip("'")
    # drop leading ./ and Absolute prefixes ending at repo-ish segments
    while p.startswith("./"):
        p = p[2:]
    return p


def load_graph(graph_path: Path) -> dict:
    if not graph_path.is_file():
        sys.exit(
            f"missing {graph_path}\n"
            "Run: graphify extract . --code-only --force   # or graphify update ."
        )
    return json.loads(graph_path.read_text(encoding="utf-8"))


def index_graph(graph: dict) -> tuple[set[str], set[str], dict[str, int], dict]:
    """Return (file_paths, labels_lower, degree_by_id, meta)."""
    files: set[str] = set()
    labels: set[str] = set()
    id_to_node: dict[str, dict] = {}
    for n in graph.get("nodes", []):
        nid = n.get("id") or ""
        id_to_node[nid] = n
        lab = (n.get("label") or "").strip()
        if lab:
            labels.add(lab.lower())
            labels.add(Path(lab).stem.lower())
        sf = norm_path(n.get("source_file") or "")
        if sf:
            files.add(sf)
            # also register parent packages as weak coverage for package cites
    degree: Counter[str] = Counter()
    for e in graph.get("links") or graph.get("edges") or []:
        s, t = e.get("source"), e.get("target")
        if s:
            degree[s] += 1
        if t:
            degree[t] += 1
    meta = {
        "built_at_commit": (graph.get("graph") or {}).get("built_at_commit")
        or graph.get("built_at_commit"),
        "node_count": len(graph.get("nodes", [])),
        "edge_count": len(graph.get("links") or graph.get("edges") or []),
    }
    return files, labels, dict(degree), {"id_to_node": id_to_node, **meta}


TRAVERSAL_RELATIONS = frozenset(
    {"calls", "imports", "imports_from", "uses", "references"}
)


def pkg_of(source_file: str, depth: int = 4) -> str:
    """Directory key a node belongs to, capped at `depth` segments.

    Mirrors the granularity ingest.py's split_oversized() produces, so the
    edges reported here line up with the entries that consume them.
    """
    parts = norm_path(source_file).split("/")
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1])


def package_edges(graph: dict, depth: int = 4) -> dict:
    """Cross-package call/import structure, keyed by package path.

    fact_sheet() records imports as `mod.split(".")[0]`, which collapses
    `from backend_new.src.application.arps.forecast import fit_hyperbolic`
    to `backend_new: 1` — losing the package, the symbol, and every call
    edge. It also has no inbound view, so a package cannot say who calls it.
    The writer therefore sees each package in isolation and falls back on
    whatever doc narrates it.

    Per package this returns:
      inbound      Counter{caller_pkg: n}   who depends on this
      outbound     Counter{callee_pkg: n}   what this depends on
      entry_points [(label, "file:loc", n_external, [top callers])]

    These are an itinerary, not article content: they tell the absorb step
    which files to open. Recomputable facts stay a graphify query (SKILL.md
    rule 11) — an article must not freeze "called by workflows" into prose.
    """
    nodes = {n.get("id"): n for n in graph.get("nodes", [])}
    out: dict[str, dict] = defaultdict(
        lambda: {"inbound": Counter(), "outbound": Counter(), "_sym": Counter(),
                 "_sym_callers": defaultdict(Counter)}
    )

    for e in graph.get("links") or graph.get("edges") or []:
        if e.get("relation") not in TRAVERSAL_RELATIONS:
            continue
        s, t = nodes.get(e.get("source")), nodes.get(e.get("target"))
        if not s or not t:
            continue
        sf, tf = s.get("source_file") or "", t.get("source_file") or ""
        if not sf or not tf:
            continue
        ps, pt = pkg_of(sf, depth), pkg_of(tf, depth)
        if not ps or not pt or ps == pt:
            continue  # intra-package edges say nothing about coupling
        out[pt]["inbound"][ps] += 1
        out[ps]["outbound"][pt] += 1
        # target is an entry point into its package: something outside calls it
        label = (t.get("label") or "").strip()
        if label:
            out[pt]["_sym"][label] += 1
            out[pt]["_sym_callers"][label][Path(norm_path(sf)).name] += 1

    result = {}
    for pkg, d in out.items():
        entries = []
        for label, n in d["_sym"].most_common(6):
            node = next(
                (x for x in graph.get("nodes", [])
                 if (x.get("label") or "").strip() == label
                 and pkg_of(x.get("source_file") or "", depth) == pkg),
                None,
            )
            loc = (f"{norm_path(node.get('source_file'))}:{node.get('source_location')}"
                   if node else "")
            entries.append((label, loc, n,
                            [c for c, _ in d["_sym_callers"][label].most_common(3)]))
        result[pkg] = {"inbound": d["inbound"], "outbound": d["outbound"],
                       "entry_points": entries}
    return result


def git_head(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        return out
    except Exception:
        return None


def path_in_graph(path: str, files: set[str]) -> bool:
    p = norm_path(path)
    if p in files:
        return True
    # package / directory cite: any file under it
    prefix = p.rstrip("/") + "/"
    return any(f.startswith(prefix) for f in files)


def classify_claim_path(path: str, ignore: list[str], files: set[str]) -> str:
    """Return: ignored | external | non_code | ok | missing."""
    p = norm_path(path)
    # checked before anything else: a URI has no repo path to be missing from
    if SCHEME_RE.match(p):
        return "external"
    if ignored(p, ignore):
        return "ignored"
    # Wiki ingest unit ids (not repo paths)
    if "/" not in p and UNIT_ID_RE.match(p.lower()):
        return "non_code"
    # Fixture / JSON data trees are not AST code nodes
    if "/data/" in f"/{p}/" or p.startswith("data/"):
        if Path(p).suffix.lower() not in CODE_EXT:
            return "non_code"
    ext = Path(p).suffix.lower()
    if ext in SKIP_EXT:
        return "non_code"
    if ext and ext not in CODE_EXT and not path_in_graph(p, files):
        return "non_code"
    if path_in_graph(p, files):
        return "ok"
    # bare directory / package cite
    if not ext:
        return "missing"
    if ext in CODE_EXT:
        return "missing"
    return "non_code"


def scan_claims(wiki_dir: Path, repo: Path) -> list[dict]:
    claims = []
    wiki_dir = wiki_dir.resolve()
    repo = repo.resolve()
    for md in sorted(wiki_dir.rglob("*.md")):
        if md.name.startswith("_"):
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        try:
            rel = str(md.resolve().relative_to(repo)).replace("\\", "/")
        except ValueError:
            rel = str(md).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), 1):
            for m in CLAIM_RE.finditer(line):
                claims.append({
                    "article": rel,
                    "line": i,
                    "grade": m.group(1),
                    "path": norm_path(m.group(2)),
                    "sha": m.group(3).lower(),
                    "raw": m.group(0),
                })
    return claims


def wiki_text_blob(wiki_dir: Path) -> str:
    parts = []
    for md in wiki_dir.rglob("*.md"):
        if md.name.startswith("_"):
            continue
        parts.append(md.read_text(encoding="utf-8", errors="replace").lower())
    return "\n".join(parts)


def run_verify(repo: Path, graph_path: Path, wiki_dir: Path, out_json: Path, out_md: Path) -> dict:
    ignore = load_wikiignore(repo)
    graph = load_graph(graph_path)
    files, _labels, _degree, meta = index_graph(graph)
    head = git_head(repo)
    built = meta.get("built_at_commit")
    freshness = {
        "built_at_commit": built,
        "head": head,
        "stale_hint": bool(built and head and not head.startswith(str(built)[:7])
                           and str(built)[:7] not in head and head[:7] not in str(built)),
    }

    claims = scan_claims(wiki_dir, repo)
    demote, skipped_ignored, skipped_non_code, skipped_external, ok = [], [], [], [], []
    by_article: dict[str, list] = defaultdict(list)

    for c in claims:
        status = classify_claim_path(c["path"], ignore, files)
        row = {**c, "status": status}
        if status == "missing":
            demote.append(row)
            by_article[c["article"]].append(row)
        elif status == "ignored":
            skipped_ignored.append(row)
        elif status == "non_code":
            skipped_non_code.append(row)
        elif status == "external":
            # chat / meeting / video citation: unverifiable against a code graph,
            # never demoted here. Its freshness is the feeder's sha, not the graph's.
            skipped_external.append(row)
        else:
            ok.append(row)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph": str(graph_path),
        "wiki": str(wiki_dir),
        "freshness": freshness,
        "graph_stats": {
            "nodes": meta["node_count"],
            "edges": meta["edge_count"],
            "indexed_files": len(files),
        },
        "summary": {
            "claims_scanned": len(claims),
            "ok": len(ok),
            "suggest_demote_gap": len(demote),
            "skipped_ignored_corpus": len(skipped_ignored),
            "skipped_non_code": len(skipped_non_code),
            "skipped_external_source": len(skipped_external),
        },
        "skipped_external_source": skipped_external,
        "suggest_demote_gap": demote,
        "skipped_ignored_corpus": skipped_ignored,
        "articles_affected": sorted(by_article.keys()),
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Graph verify report",
        "",
        f"Generated: {report['generated_at']}",
        f"Graph: `{graph_path}` ({meta['node_count']} nodes, {meta['edge_count']} edges)",
        f"Built at commit: `{built or 'unknown'}` · HEAD: `{head or 'unknown'}`",
        "",
        "## Summary",
        "",
        f"- Claims scanned: **{len(claims)}**",
        f"- OK (path in graph): **{len(ok)}**",
        f"- Suggest demote → `[gap]`: **{len(demote)}**",
        f"- Skipped (`.wikiignore` corpus): **{len(skipped_ignored)}**",
        f"- Skipped (non-code path): **{len(skipped_non_code)}**",
        "",
    ]
    if freshness.get("stale_hint"):
        lines += [
            "> Graph `built_at_commit` does not match HEAD — consider "
            "`graphify update .` before trusting demotions.",
            "",
        ]
    lines += ["## Suggest demote to `[gap]` (missing from graph)", ""]
    if not demote:
        lines += ["_none_", ""]
    else:
        for row in demote:
            lines.append(
                f"- `{row['path']}` — {row['grade']} @ `{row['article']}:{row['line']}`"
            )
        lines.append("")
    lines += [
        "## Next steps",
        "",
        "During `/wiki verify`, review demotions: if the symbol/path is truly gone, "
        "downgrade the claim to `[gap]` or mark the article `stale: true`. "
        "This script does not edit articles.",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return report


def run_gaps(
    repo: Path,
    graph_path: Path,
    wiki_dir: Path,
    out_json: Path,
    out_md: Path,
    top: int,
) -> dict:
    graph = load_graph(graph_path)
    files, labels, degree, meta = index_graph(graph)
    id_to_node = meta["id_to_node"]
    blob = wiki_text_blob(wiki_dir)

    # also harvest sources: frontmatter lists
    sources_blob = blob  # already lowercased full text

    ranked = sorted(degree.items(), key=lambda x: -x[1])
    gaps = []
    for nid, deg in ranked:
        if len(gaps) >= top:
            break
        n = id_to_node.get(nid) or {}
        lab = (n.get("label") or nid).strip()
        sf = norm_path(n.get("source_file") or "")
        if deg < 8:
            continue
        if not sf:
            continue
        if "/tests/" in f"/{sf}/" or sf.startswith("tests/"):
            continue
        stem = Path(lab).name.lower()
        if stem.startswith("test_") or stem.endswith("_test.py") or stem.endswith("_test.ts"):
            continue
        if stem in SKIP_GAP_LABELS or lab.lower() in SKIP_GAP_LABELS:
            continue
        needles = {lab.lower(), Path(lab).stem.lower()}
        if sf:
            needles.add(sf.lower())
            needles.add(Path(sf).stem.lower())
            # path segments often appear in wiki (e.g. future_wells)
            for part in Path(sf).parts:
                if len(part) >= 5 and part not in {"backend_new", "src", "application", "api"}:
                    needles.add(part.lower())
        covered = any(n and n in sources_blob for n in needles if len(n) >= 4)
        if covered:
            continue
        gaps.append({
            "id": nid,
            "label": lab,
            "source_file": sf,
            "degree": deg,
            "file_type": n.get("file_type"),
            "community_name": n.get("community_name"),
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "gaps",
        "graph": str(graph_path),
        "top": top,
        "graph_stats": {
            "nodes": meta["node_count"],
            "edges": meta["edge_count"],
        },
        "uncovered_hubs": gaps,
    }
    # merge into existing verify json if present
    prev = {}
    if out_json.is_file():
        try:
            prev = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev["gaps"] = report
    out_json.write_text(json.dumps(prev, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Graph coverage gaps (god-node style)",
        "",
        f"Generated: {report['generated_at']}",
        f"Hubs with degree ≥ 8 and no wiki mention (top {top} candidates scanned by degree).",
        "",
        "| Degree | Label | Source |",
        "|-------:|-------|--------|",
    ]
    for g in gaps:
        lines.append(
            f"| {g['degree']} | `{g['label']}` | `{g['source_file']}` |"
        )
    if not gaps:
        lines += ["", "_No uncovered hubs at this threshold._", ""]
    else:
        lines += [
            "",
            "Use during `/wiki breakdown` — write concept/workflow articles, "
            "not one page per file.",
            "",
        ]
    # append to md or write gaps section
    existing = out_md.read_text(encoding="utf-8") if out_md.is_file() else ""
    if "## Coverage gaps" in existing:
        head = existing.split("## Coverage gaps")[0].rstrip()
        out_md.write_text(head + "\n\n## Coverage gaps\n\n" + "\n".join(lines[4:]) + "\n", encoding="utf-8")
    else:
        block = "\n".join(lines)
        out_md.write_text(
            (existing.rstrip() + "\n\n" if existing else "") + block + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--graph", type=Path, default=None,
                    help="path to graph.json (default <repo>/graphify-out/graph.json)")
    ap.add_argument("--wiki", type=Path, default=None,
                    help="wiki dir (default <repo>/wiki)")
    ap.add_argument("--gaps", action="store_true",
                    help="list high-degree graph hubs not mentioned in wiki")
    ap.add_argument("--top", type=int, default=40,
                    help="max uncovered hubs to list with --gaps")
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--md-out", type=Path, default=None)
    a = ap.parse_args()

    repo = a.repo.resolve()
    graph_path = (a.graph or repo / "graphify-out" / "graph.json").resolve()
    wiki_dir = (a.wiki or repo / "wiki").resolve()
    out_json = (a.json_out or wiki_dir / "_graph_verify.json").resolve()
    out_md = (a.md_out or wiki_dir / "_graph_verify.md").resolve()

    if a.gaps:
        # ensure base report exists for merge friendliness
        if not out_json.is_file():
            run_verify(repo, graph_path, wiki_dir, out_json, out_md)
        report = run_gaps(repo, graph_path, wiki_dir, out_json, out_md, a.top)
        print(f"gaps: {len(report['uncovered_hubs'])} uncovered hubs → {out_md}")
        return

    report = run_verify(repo, graph_path, wiki_dir, out_json, out_md)
    s = report["summary"]
    print(
        f"graph_verify: {s['claims_scanned']} claims · "
        f"{s['ok']} ok · {s['suggest_demote_gap']} demote · "
        f"{s['skipped_ignored_corpus']} ignored · {s['skipped_non_code']} non-code"
    )
    print(f"  → {out_json}")
    print(f"  → {out_md}")
    if report["freshness"].get("stale_hint"):
        print("  warning: graph built_at_commit may not match HEAD")


if __name__ == "__main__":
    main()
