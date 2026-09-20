#!/usr/bin/env python3
"""
validate_wiki.py — mechanical checks on wiki articles.

Write-time validation, not drift verification. This asks "is this article
honest about what it read?" once, when it is generated. graph_verify.py asks
"is it still true?" forever after. Same citation format, different question.

The point is to make a cheap model safe to generate with. A weaker writer gets
*form* wrong — invented wikilinks, rollups that do not match the tags, citations
to files that moved — and every one of those is catchable without an LLM. What
stays uncatchable is whether the cited file actually supports the sentence;
--anchor narrows that by requiring each verified/code claim to name a symbol
that appears in the file it cites.

    python3 validate_wiki.py                      # all articles
    python3 validate_wiki.py wiki/modules/x.md    # one
    python3 validate_wiki.py --anchor             # + symbol anchoring
    python3 validate_wiki.py --strict             # warnings fail too

Exit 0 = clean. Non-zero = a generator should retry or quarantine.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

try:
    from .source_files import content_version, source_path
except ImportError:
    from source_files import content_version, source_path

REQUIRED_KEYS = {"title", "type", "created", "last_updated", "stale", "grades",
                 "sources", "related"}

# SKILL.md length targets, by article type.
LENGTH = {"module": (40, 80), "package": (40, 80), "entity": (30, 60),
          "workflow": (60, 120), "contract": (30, 70), "decision": (40, 70),
          "tension": (40, 70), "service": (60, 100), "gap": (15, 30),
          "pattern": (30, 80), "history": (30, 80), "dataset": (30, 70)}
MIN_LINES = 15

GRADE = re.compile(r"\[(verified|code|doc|conflict|gap)(?::([^\]]*))?\]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A single space is allowed inside a path because ingest.py deliberately admits
# `*.excalidraw copy` files (classify() has a branch for them). Without it those
# citations parsed as "no path@sha" and quarantined every article about a
# duplicated diagram — 142 failures across a single repo's quarantine.
# The inserted word itself excludes "/" and ".": a bare extra word like "copy"
# has neither, but [conflict: a@sha vs b@sha] does — without this restriction
# the "vs" got pulled into the second citation's path ("vs sources/..."),
# which never exists, quarantining every article that used a conflict tag.
CITE = re.compile(r"([\w./\-]+(?: [\w\-]+)?\.\w+)@([0-9a-f]{7,40})")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
# Backtick spans first, identifiers within. Requiring a closing backtick right
# after the name matched nothing in `run_arps(ArpsRequest) -> ArpsResult`.
BACKTICK = re.compile(r"`([^`]+)`")
# Dots included so `Thing.run_thing` is one token that rsplit can reduce to
# `run_thing`; splitting first would leave `Thing` as a phantom orphan.
IDENT = re.compile(r"[A-Za-z_][\w.]{3,}")
# A bare filename in backticks — `service.py`, `docker-compose.yml`, `App.tsx`.
# It is a path, not a symbol, so nothing inside it should be demanded of the
# cited file.
FILENAME = re.compile(
    r"^[\w.-]+\.(py|ts|tsx|js|jsx|sol|sql|daml|md|rst|txt|json|ya?ml|toml|ini|conf|"
    r"excalidraw|mmd|csv|tsv|docx|pptx|pdf|xlsx|lock|sh|cfg)$", re.I)


class Report:
    def __init__(self, path):
        self.path, self.errors, self.warnings = path, [], []

    def error(self, check, detail):
        self.errors.append((check, detail))

    def warn(self, check, detail):
        self.warnings.append((check, detail))

    @property
    def ok(self):
        return not self.errors


_SHA_CACHE: dict[str, str | None] = {}


def git_blob_sha(repo: Path, path: str) -> str | None:
    """Blob sha of `path` at HEAD, or None if it is not tracked.

    On failure `git rev-parse` echoes the argument back on stdout, so returncode
    is the only reliable signal — trusting stdout reported "HEAD has HEAD:dia".
    Cached: the same file is cited many times across a corpus.
    """
    if path in _SHA_CACHE:
        return _SHA_CACHE[path]
    sha = None
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", f"HEAD:{path}"],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            sha = out.stdout.strip() or None
    except Exception:
        pass
    _SHA_CACHE[path] = sha
    return sha


_ETAG_CACHE: dict[str, str | None] = {}


def source_etag(path: str) -> str | None:
    """Current S3 etag for a sources/ path — sources are S3-only, never git."""
    if path in _ETAG_CACHE:
        return _ETAG_CACHE[path]
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server import storage
    try:
        _ETAG_CACHE[path] = storage.head_etag(path)
    except RuntimeError as e:
        raise SystemExit(f"cannot validate {path}: {e}")
    return _ETAG_CACHE[path]


def frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Empty frontmatter if absent."""
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---", 3)
    return (text[3:end], text[end + 4:]) if end != -1 else ("", text)


def article_titles(wiki: Path) -> set[str]:
    out = set()
    for p in wiki.rglob("*.md"):
        if p.name.startswith("_"):
            continue
        m = re.search(r"^title:\s*(.+)$", p.read_text(errors="replace"), re.M)
        if m:
            out.add(m.group(1).strip().strip('"'))
    return out


def validate(path: Path, repo: Path, titles: set[str], graph_files: set[str] | None,
             anchor: bool, allowed_citations: set[str] | None = None,
             require_grades: bool = False, minimum_lines: int = MIN_LINES,
             allowed_grades: set[str] | None = None) -> Report:
    r = Report(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = frontmatter(text)

    if not fm:
        r.error("frontmatter", "missing or unterminated")
        return r

    keys = set(re.findall(r"^(\w+):", fm, re.M))
    if missing := REQUIRED_KEYS - keys:
        r.error("frontmatter", f"missing keys: {', '.join(sorted(missing))}")

    for key in ("created", "last_updated"):
        m = re.search(rf"^{key}:\s*(\S+)", fm, re.M)
        if m and not DATE_RE.match(m.group(1)):
            r.error("frontmatter", f"{key} is not a real YYYY-MM-DD date: {m.group(1)}")

    atype = (re.search(r"^type:\s*(\w+)", fm, re.M) or [None, ""])[1]

    tags = GRADE.findall(text)
    counts = Counter(t for t, _ in tags)
    total = sum(counts.values())
    if total == 0:
        r.error("grades", "no graded claims — ungraded prose about code is a liability")

    # A verified/code/doc claim without a path@sha cannot go stale, so it cannot
    # be trusted. gap and conflict are exempt: gap has no source by definition,
    # conflict carries two and is checked through CITE below.
    for kind, payload in tags:
        if allowed_grades is not None and kind not in allowed_grades:
            r.error('unsupported grade', f'[{kind}] is not supported by the supplied source types')
        if kind in ("verified", "code", "doc") and not CITE.search(payload or ""):
            r.error("citation", f"[{kind}] without path@sha: {(payload or '')[:60]}")
    if require_grades:
        if not any(counts[kind] for kind in ("verified", "code", "doc", "conflict")):
            r.error("grades", "no source-supported claims to publish")
        prose = re.sub(r"```.*?```", "", body, flags=re.S)
        prose = re.sub(r"<!--.*?-->", "", prose, flags=re.S)
        for paragraph in re.split(r"\n\s*\n", prose):
            lines = [line for line in paragraph.splitlines()
                     if line.strip() and not re.match(r"^\s*(#{1,6}\s|[-*_]{3,}\s*$)", line)]
            # List rows are separate claims even when Markdown puts them in one
            # paragraph. A citation on the last item cannot cover preceding ones.
            claims = lines if lines and all(re.match(r"^\s*(?:[-*+] |\d+[.)] )", s) for s in lines) else [" ".join(lines)]
            for claim in claims:
                if re.search(r"[A-Za-z]", claim) and not GRADE.search(claim):
                    r.error("ungraded claim", claim.strip()[:100])

    # dedupe: one file cited eight times is one problem, not eight
    for cited_path, sha in sorted(set(CITE.findall(text))):
        if allowed_citations is not None and f"{cited_path}@{sha}" not in allowed_citations:
            r.error("unseen source", f"{cited_path}@{sha} was not supplied to the writer")
        try:
            local = source_path(repo, cited_path)
        except ValueError:
            r.error("cited path", f"{cited_path} is outside the source root")
            continue
        if not local.exists():
            r.error("cited path", f"{cited_path} does not exist")
            continue
        real = (content_version(local) if cited_path.startswith("sources/")
                else git_blob_sha(repo, cited_path))
        if real is None and not cited_path.startswith("sources/"):
            r.error("stale sha", f"{cited_path}@{sha} — git cannot resolve this "
                                 f"path at HEAD (check for a case mismatch between "
                                 f"the citation and the tracked path)")
        elif real and not real.startswith(sha):
            r.error("stale sha", f"{cited_path}@{sha} — HEAD has {real[:8]}")
        if graph_files is not None and cited_path.endswith((".py", ".ts", ".tsx", ".js", ".sol")):
            if cited_path not in graph_files:
                r.warn("not in graph", f"{cited_path} — code path absent from graphify")

    # The frontmatter rollup is what /wiki status and the grade audit read. If it
    # drifts from the inline tags every downstream number is wrong.
    if m := re.search(r"grades:\s*\{([^}]*)\}", fm):
        declared = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", m.group(1))}
        for kind in set(declared) | set(counts):
            if declared.get(kind, 0) != counts.get(kind, 0):
                r.error("grades rollup",
                        f"{kind}: frontmatter says {declared.get(kind,0)}, "
                        f"found {counts.get(kind,0)}")

    # SKILL.md: "If doc exceeds half, you are transcribing documentation."
    if total and counts["doc"] * 2 > total:
        r.warn("doc ratio", f"{counts['doc']}/{total} claims are [doc] — go open code")

    if dangling := WIKILINK.findall(text):
        for link in set(dangling):
            if link.strip() not in titles:
                r.error("wikilink", f"[[{link}]] resolves to no article")

    lines = len(body.strip().splitlines())
    lo, hi = LENGTH.get(atype, (MIN_LINES, 150))
    if lines < minimum_lines:
        r.error("length", f"{lines} lines — below the {minimum_lines}-line minimum")
    elif not (lo <= lines <= hi):
        r.warn("length", f"{lines} lines, target {lo}-{hi} for type '{atype}'")

    if anchor:
        # Narrows the one thing mechanical checks cannot see: whether the cited
        # file supports the claim. A claim naming `run_arps` should cite a file
        # containing that string. Catches invented symbols, not wrong reasoning.
        # Fenced blocks are evidence, not claims — and ```python made "python"
        # look like an unresolved symbol in every article carrying a snippet.
        unfenced = re.sub(r"```.*?```", "", text, flags=re.S)
        for para in re.split(r"\n\s*\n", unfenced):
            m = GRADE.search(para)
            if not m or m.group(1) not in ("verified", "code"):
                continue
            # `ArpsResult.all_unfit` never appears literally — the definition is
            # `def all_unfit`. Match the final component, or every dotted
            # reference is a false positive and the real ones get buried.
            syms = {m.strip(".").rsplit(".", 1)[-1]
                    for span in BACKTICK.findall(para)
                    # `a/b/c.py` in backticks is a path, not a symbol; mining
                    # "backend_new" out of it quarantined a correct article.
                    # A BARE filename has no slash and slipped through, so
                    # `service.py` yielded the symbol "py" and `docker-compose.yml`
                    # yielded "yml" — 21 of 32 recent quarantines were this.
                    if "/" not in span and not FILENAME.match(span)
                    for m in IDENT.findall(span)}
            paths = [p for p, _ in CITE.findall(para)]
            if not syms or not paths:
                continue
            blobs = "\n".join(source_path(repo, p).read_text(errors="replace")
                              for p in paths if ".." not in Path(p).parts
                              and not Path(p).is_absolute() and source_path(repo, p).is_file())
            if orphan := {s for s in syms if s not in blobs}:
                r.warn("anchor", f"symbols not found in cited files: "
                                 f"{', '.join(sorted(orphan))[:80]}")
    return r


def fix_rollup(path: Path) -> bool:
    """Rewrite the frontmatter grades rollup from the inline tags. True if the
    file changed. The rollup is derived data — when it disagrees with the tags,
    the tags are the truth. Articles whose rollup already matches are left
    byte-identical, so a corpus-wide fix only diffs the actual mismatches."""
    text = path.read_text(encoding="utf-8", errors="replace")
    counts = Counter(t for t, _ in GRADE.findall(text))
    m = re.search(r"grades:\s*\{([^}]*)\}", text)
    if m:
        declared = {k: int(v) for k, v in re.findall(r"(\w+):\s*(\d+)", m.group(1))}
        if all(declared.get(k, 0) == counts.get(k, 0)
               for k in set(declared) | set(counts)):
            return False
    rollup = "{" + ", ".join(f"{k}: {counts.get(k, 0)}" for k in
                             ("verified", "code", "doc", "conflict", "gap")) + "}"
    new = re.sub(r"^grades:.*$", f"grades: {rollup}", text, count=1, flags=re.M)
    if new == text:
        return False
    path.write_text(new, encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--wiki", type=Path, default=None)
    ap.add_argument("--anchor", action="store_true", help="check symbols appear in cited files")
    ap.add_argument("--strict", action="store_true", help="warnings count as failures")
    ap.add_argument("--quiet", action="store_true", help="only report problems")
    ap.add_argument("--fix-rollups", action="store_true",
                    help="rewrite grades rollups from the inline tags, then validate")
    a = ap.parse_args()

    repo = a.repo.resolve()
    wiki = (a.wiki or repo / "wiki").resolve()
    paths = a.paths or sorted(p for p in wiki.rglob("*.md")
                              if not p.name.startswith("_")
                              and p.parent.name != "topics")  # hubs: generated, not gated
    if not paths:
        print(f"no articles under {wiki}")
        return 0

    if a.fix_rollups:
        fixed = [p for p in paths if fix_rollup(p)]
        print(f"rewrote {len(fixed)} grades rollup(s)")

    graph_files = None
    gp = repo / "graphify-out" / "graph.json"
    if gp.is_file():
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from graph_verify import index_graph, load_graph
            graph_files, *_ = index_graph(load_graph(gp))
        except Exception:
            graph_files = None

    titles = article_titles(wiki)
    reports = [validate(p, repo, titles, graph_files, a.anchor) for p in paths]

    failed = warned = 0
    for r in reports:
        rel = r.path.relative_to(repo) if r.path.is_relative_to(repo) else r.path
        if r.errors:
            failed += 1
            print(f"\nFAIL  {rel}")
            for c, d in r.errors:
                print(f"        {c:14} {d}")
            for c, d in r.warnings:
                print(f"   warn {c:14} {d}")
        elif r.warnings:
            warned += 1
            if not a.quiet:
                print(f"\nWARN  {rel}")
            for c, d in r.warnings:
                print(f"        {c:14} {d}")
        elif not a.quiet:
            print(f"ok    {rel}")

    print(f"\n{len(reports)} articles — {len(reports)-failed-warned} clean, "
          f"{warned} with warnings, {failed} failed")
    return 1 if failed or (a.strict and warned) else 0


if __name__ == "__main__":
    sys.exit(main())
