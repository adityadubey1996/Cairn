#!/usr/bin/env python3
"""
absorb_runner.py — generate wiki articles from queued entries, without an agent.

Absorb has always been a human-or-LLM reading loop. It can be a single
completion because the fact sheet already answers the question an agent loop
exists to answer: *what should I read next?* graphify's cross-package edges name
the entry points, so this script gathers those files deterministically and makes
one call. No tools, no agent, no Claude Code — it runs anywhere Python and an
API key do.

    GROQ_API_KEY=... python3 absorb_runner.py --limit 3
    python3 absorb_runner.py --dry-run          # show prompts, spend nothing
    python3 absorb_runner.py --only pkg-backend-new-src-application-arps

Every generated article is gated by validate_wiki.py before it lands. On failure
the errors are fed back and the model retries; after --retries attempts the
article is quarantined rather than published, because a confidently wrong graded
claim is worse than a missing article.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import signal
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_index  # noqa: E402
import vocab  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from validate_wiki import GRADE, article_titles, fix_rollup, validate  # noqa: E402

# ABSORB_BASE/ABSORB_API_KEY take priority so absorb can run against a
# different OpenAI-compatible provider (e.g. OpenRouter) without touching
# GROQ_API_KEY, which server/wikilib.py also uses for live chat against the
# real api.groq.com — overwriting it there would break chat.
GROQ_URL = (os.environ.get("ABSORB_BASE")
           or os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")) + "/chat/completions"
MODEL = os.environ.get("ABSORB_MODEL", "llama-3.3-70b-versatile")

# Budgets. A 200k-token prompt costs real money and buys nothing — the itinerary
# already narrowed what matters.
MAX_FILE_CHARS = 14_000
MAX_TOTAL_CHARS = 60_000

# Expanding a dependency directory: keep this aligned with ingest.CODE_EXT.
DEP_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".sol", ".sql", ".daml"}
# Re-export barrels carry no logic — reading one wastes a dependency slot.
BARREL = {"__init__", "index"}

# The trailing ` ←` is load-bearing. ingest.py renders "Declared symbols" as
# `- `name` — `path`` too, so without it this also matched that block: 30 hits
# where 3 were intended, alphabetical, and graphify's priority ordering was lost
# before it reached the model. gather() truncates at MAX_TOTAL_CHARS, so on a
# large package the ordering decides which files survive the cut.
OPEN_NEXT = re.compile(r"^- `[^`]+` — `([^`]+?)(?::L\d+)?` ←", re.M)
CALLS_INTO = re.compile(r"^\*\*Calls into:\*\* (.+)$", re.M)
FILES_LIST = re.compile(r"^- `([^`]+)`$", re.M)
TARGET = re.compile(r"<!--\s*target:\s*(wiki/[\w./-]+\.md)\s*-->")
TYPE_DIR = vocab.TYPE_DIR

SYSTEM = """You are a writer compiling a wiki about a software system, following \
the project's SKILL.md rules. You are not a documentation generator.

NON-NEGOTIABLE RULES:
1. Every claim carries a grade tag with a real citation:
   [verified: path@sha]  asserted in a doc AND confirmed in code you were shown
   [code: path@sha]      read from the code you were shown; nobody wrote it down
   [doc: path@sha]       asserted in a PROSE doc (.md/.rst), unchecked against code.
                         Never use [doc] for something you read in a source file —
                         that is [code].
   [conflict: pathA@sha vs pathB@sha]   doc and code disagree — record both
   [gap: what is unknown and what would need reading]
2. Cite ONLY paths and shas given to you below. Never invent either.
3. If a claim names a symbol, cite the file that actually contains that symbol.
4. Do NOT assert what a call graph can recompute ("X is called by Y"). That is
   queryable and goes stale silently. Write judgment the graph cannot derive:
   what it means, what breaks, what contradicts what, what you do not know.
5. [gap] and [conflict] are findings, not failures. An article with no gaps is
   probably lying about how much it knows.
6. Wikilinks [[Like This]] may ONLY use titles from the index given below.
7. Never link an article to itself in `related`.
8. Tone: Wikipedia-flat. One claim per sentence. No adjectives, no "would go on
   to", never the aspirational present ("is designed to").
9. Maximum 2 code snippets, 10 lines each. Snippets are evidence, not content.

OUTPUT FORMAT — emit exactly this and nothing else:
<!-- target: wiki/<dir>/<slug>.md -->
---
title: <Title Case>
type: system|domain|flow|boundary|runtime|decision|conflict|unknown|idea|outcome
created: <YYYY-MM-DD>
last_updated: <YYYY-MM-DD>
stale: false
built_from_commit: <the commit given below>
grades: {verified: N, code: N, doc: N, conflict: N, gap: N}
sources: ["path@sha", ...]
related: ["[[Existing Title]]", ...]
---

# Title

<the article>

The grades map MUST equal the number of each tag you actually wrote. Count them.
"""


def add_usage(total: dict, usage: dict) -> dict:
    """Fold one call's usage into a running {"in": n, "out": n} total.

    Accumulates rather than replaces: a unit that takes three attempts spent
    three calls' worth of tokens, and a ceiling that only counted the last one
    would let a retry-heavy batch run far past its budget.

    A missing or null usage block reads as zero — some OpenAI-compatible
    providers omit it entirely, and a KeyError there would kill a batch
    mid-flight over bookkeeping.
    """
    return {"in": total.get("in", 0) + (usage.get("prompt_tokens") or 0),
            "out": total.get("out", 0) + (usage.get("completion_tokens") or 0)}


# Set by the handler, read by the loop between units. A flag rather than an
# exception on purpose: scripts/pipeline_run.py stops a run with SIGTERM, and
# raising out of the middle of a unit would skip the post-loop index rebuild
# and S3 push that make already-published articles durable.
_sigterm = {"seen": False}


def _note_sigterm(_signum, _frame) -> None:
    _sigterm["seen"] = True
    print("\n    SIGTERM — finishing the current unit, then stopping")


def stop_reason(sigterm: bool, total: dict, budget: int) -> str | None:
    """Why this batch must stop before the next unit, or None to continue.

    SIGTERM is checked first: an operator pressing Stop should not be told the
    run ended for budget reasons.

    Input and output count against one number. They are billed differently but
    the ceiling exists to bound total spend, and splitting it into two budgets
    would just be two numbers to get wrong.

    budget <= 0 means no ceiling — hand-run batches and the connector path pass
    none, and those must not stop immediately.
    """
    if sigterm:
        return "SIGTERM received — stopping after the current unit"
    if budget > 0 and total.get("in", 0) + total.get("out", 0) >= budget:
        return (f"token ceiling reached: {total.get('in', 0) + total.get('out', 0):,} "
                f"of {budget:,}")
    return None


def groq(messages: list[dict], key: str, temperature: float = 0.2) -> tuple[str, dict]:
    """(content, usage). The usage block is returned rather than dropped
    because it is the only honest measure of what a batch cost — everything
    downstream (the token ceiling, the per-unit rate the UI estimates from)
    is derived from it."""
    body = json.dumps({"model": MODEL, "messages": messages,
                       "temperature": temperature}).encode()
    req = urllib.request.Request(
        GROQ_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 # Cloudflare in front of api.groq.com rejects the default
                 # Python-urllib agent with 403 "error code: 1010". Identical
                 # request from curl succeeds; only the UA differs.
                 "User-Agent": "absorb-runner/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = json.loads(r.read())
            return (payload["choices"][0]["message"]["content"],
                    payload.get("usage") or {})
    except urllib.error.HTTPError as e:
        raise SystemExit(f"groq {e.code}: {e.read().decode('utf-8','replace')[:300]}")


def blob_sha(repo: Path, path: str) -> str | None:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", f"HEAD:{path}"],
                         capture_output=True, text=True)
    return out.stdout.strip()[:8] if out.returncode == 0 else None


def version_of(repo: Path, path: str) -> str | None:
    """8-char version tag for a citation: S3 etag prefix for sources/,
    git blob sha for everything else.

    sources/ are S3-only — no git fallback exists on purpose. An absent
    object returns None (the unit is skipped as "nothing citable", same as
    an untracked file); an unconfigured S3 is a hard stop, not a skip.
    """
    if not path.startswith("sources/"):
        return blob_sha(repo, path)
    # ai-brain's own root, NOT `repo` — absorb also runs against sibling
    # repos that have no server/ package.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server import storage
    try:
        etag = storage.head_etag(path)
    except RuntimeError as e:
        raise SystemExit(f"cannot cite {path}: {e}")
    return etag[:8] if etag else None


def reading_list(entry: str) -> list[str]:
    """Files to open, in priority order.

    'Open next' comes from graphify's cross-package edges — the symbols the rest
    of the system actually calls. That ordering is the whole point: it is why one
    completion suffices where an agent loop would otherwise explore.
    """
    picks = OPEN_NEXT.findall(entry)
    if not picks:  # non-code entries, or a graph-less ingest
        picks = FILES_LIST.findall(entry)
    if not picks:
        # single-file units (docs, contracts): the unit's own path IS the
        # reading list. inbox:// pseudo-paths have no file to open.
        m = re.search(r'^path:\s*"?([^"\n]+)"?', entry, re.M)
        if m and "://" not in m.group(1):
            picks = [m.group(1)]
    # Outbound dependencies matter as much as entry points: the strongest
    # finding in the hand-absorbed ARPS article (two of three registered decline
    # strategies raise NotImplementedError) lived in constants/registry.py,
    # reachable only through "Calls into". Reading entry points alone missed it.
    for line in CALLS_INTO.findall(entry)[:1]:
        picks += re.findall(r"`([^`]+)`\(\d+\)", line)[:3]
    seen, out = set(), []
    for p in picks:
        p = p.strip()
        if p and p not in seen and not p.endswith("/__init__.py"):
            seen.add(p)
            out.append(p)
    return out


def gather(repo: Path, paths: list[str]) -> tuple[str, list[str]]:
    """Read the files, with their real shas. Deterministic and free."""
    # Text of the package's own files, used to rank dependency dirs: alphabetical
    # expansion of constants/ picked basins.py and columns.py while the finding
    # that mattered lived in registry.py — the one module the package imports.
    own_text = " ".join((repo / p).read_text(errors="replace")
                        for p in paths if (repo / p).is_file())
    expanded = []
    for p in paths:
        f = repo / p
        if f.is_dir():
            # Every language ingest indexes, not just Python. Globbing "*.py"
            # meant graphify's cross-package dependencies contributed nothing at
            # all on a TypeScript repo: reading_list named the directories,
            # gather found no candidates in them, and the article ended up
            # citing only the package's own files.
            cands = [c for c in sorted(f.iterdir())
                     if c.is_file() and c.suffix in DEP_EXT
                     and c.stem not in BARREL]
            cands.sort(key=lambda c: c.stem not in own_text)  # imported first
            expanded += [str(c.relative_to(repo)) for c in cands[:2]]
        else:
            expanded.append(p)
    chunks, cited, total = [], [], 0
    for p in expanded:
        f = repo / p
        if not f.is_file():
            continue
        sha = version_of(repo, p)
        if not sha:
            continue
        body = f.read_text(errors="replace")[:MAX_FILE_CHARS]
        if total + len(body) > MAX_TOTAL_CHARS:
            break
        total += len(body)
        cited.append(f"{p}@{sha}")
        chunks.append(f"===== FILE {p}@{sha} =====\n{body}")
    return "\n\n".join(chunks), cited


def force_real_dates(body: str, existing_path: Path | None, today: str) -> str:
    """Stamp created/last_updated with real values. The model has no clock
    and no reason to know today's date, so its guess is never trusted.
    `created` is preserved from the existing article across a re-absorb;
    `last_updated` always becomes `today`."""
    created = today
    if existing_path is not None and existing_path.is_file():
        kept = re.search(r"^created:\s*(\S+)",
                         existing_path.read_text(errors="replace"), re.M)
        if kept:
            created = kept.group(1)
    body = re.sub(r"^created:.*$", f"created: {created}", body, count=1, flags=re.M)
    body = re.sub(r"^last_updated:.*$", f"last_updated: {today}", body, count=1, flags=re.M)
    return body


def build_prompt(entry_text, files_text, cited, index, existing, head, titles):
    parts = [
        f"COMMIT: {head}",
        "",
        "CITABLE SOURCES — use these exact path@sha strings, no others:",
        *(f"  {c}" for c in cited),
        "",
        "EXISTING ARTICLE TITLES you may link to with [[...]]:",
        "  " + ", ".join(sorted(titles)) if titles else "  (none yet)",
        "",
        "===== ENTRY (fact sheet; its 'Called by'/'Open next' lines are an",
        "===== itinerary — do not restate them as claims) =====",
        entry_text[:12_000],
        "",
        "===== SOURCE FILES =====",
        files_text,
    ]
    if existing:
        parts += ["", "===== EXISTING ARTICLE — integrate into this, do not append",
                  "===== or restate. Keep what is still true; correct what is not.",
                  existing[:10_000]]
    if index:
        parts += ["", "===== WIKI INDEX =====", index[:4_000]]
    return "\n".join(parts)


def find_existing_article(wiki: Path, unit_id: str) -> tuple[str, Path | None]:
    """The article already absorbed from this exact unit, if any.

    Matches a mechanically-written `unit: <id>` frontmatter line, never a
    citation-text search — matching on "does this article cite a file inside
    the package" caught dependency citations too (package A citing a file
    from package B as supporting evidence), and silently overwrote A with B's
    content when B was later absorbed.
    """
    needle = re.compile(rf"^unit:\s*{re.escape(unit_id)}\s*$", re.M)
    for p in sorted(wiki.rglob("*.md")):
        if p.name.startswith("_"):
            continue
        body = p.read_text(errors="replace")
        if needle.search(body):
            return body, p
    return "", None


def run_one(repo, wiki, unit_id, entry_path, key, head, retries, dry) -> tuple[str, dict]:
    """(result line, usage spent on this unit).

    The result line carries `tok=in/out  Ns` because scripts/pipeline_run.py
    parses it back out of the log — that is how a run row learns what it
    cost, and how the UI's per-unit estimate gets a measured rate.
    """
    t0 = time.monotonic()
    used: dict = {}

    def done(line: str) -> tuple[str, dict]:
        return (f"{line}  tok={used.get('in', 0)}/{used.get('out', 0)}  "
                f"{time.monotonic() - t0:.1f}s", used)

    entry = entry_path.read_text(errors="replace")
    paths = reading_list(entry)
    files_text, cited = gather(repo, paths)
    if not cited:
        # A doc entry embeds its own document text, whose backticked list
        # lines can mis-parse as a phantom reading list. The unit's own path
        # is always a valid last resort.
        m = re.search(r'^path:\s*"?([^"\n]+)"?', entry, re.M)
        if m and "://" not in m.group(1):
            files_text, cited = gather(repo, [m.group(1)])
    if not cited:
        return done("skip: nothing citable to read")

    index_p = wiki / "_index.md"
    index = index_p.read_text(errors="replace") if index_p.is_file() else ""
    titles = article_titles(wiki)

    # If an article already covers this unit, integrate rather than duplicate.
    existing, existing_path = find_existing_article(wiki, unit_id)

    user = build_prompt(entry, files_text, cited, index, existing, head, titles)
    if dry:
        print(f"--- {unit_id}: {len(paths)} files, {len(cited)} citable, "
              f"{len(user):,} prompt chars")
        return done("dry-run")

    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    for attempt in range(1, retries + 1):
        out, usage = groq(msgs, key)
        used = add_usage(used, usage)
        m = TARGET.search(out)
        # Strip ANY leading comment, matched or not — a malformed target line
        # ("wiki/services/backend_new-arps", no .md) otherwise stays in the body
        # and breaks frontmatter detection.
        body = re.sub(r"^\s*<!--.*?-->\s*", "", out, count=1, flags=re.S).strip()
        # models sometimes wrap the whole thing in a markdown fence
        body = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", body).strip()
        # The rollup is derived from the tags, so derive it. Asking a model to
        # count its own output is a guaranteed retry loop for no benefit.
        # The SAME regex validate_wiki checks with. They used to differ — this
        # one matched `[code:` without requiring the closing bracket, so a single
        # malformed tag made the rollup exactly one too high and the article was
        # quarantined for "frontmatter says 93, found 92". Two counters that can
        # disagree is a bug generator; there is now one.
        counts = Counter(t for t, _ in GRADE.findall(body))
        rollup = "{" + ", ".join(f"{k}: {counts.get(k, 0)}" for k in
                                 ("verified", "code", "doc", "conflict", "gap")) + "}"
        body = re.sub(r"^grades:.*$", f"grades: {rollup}", body, count=1, flags=re.M)

        # Mechanical, never left to the model: this is what find_existing_article()
        # matches on for the NEXT absorb of this same unit.
        body = re.sub(r"\n---\n", f"\nunit: {unit_id}\n---\n", body, count=1)

        body = force_real_dates(body, existing_path,
                                datetime.now(timezone.utc).date().isoformat())

        # The title IS the wikilink key, so re-absorbing a unit must not rename
        # its article: "Alpha Well Analysis" came back as "Alpha Well Analysis
        # Whatif" on a second pass and every inbound [[link]] dangled, which
        # quarantined four otherwise-valid articles. When an article already
        # exists for this unit, its title wins over whatever the model chose.
        if existing_path:
            kept = re.search(r"^title:\s*(.+)$", existing_path.read_text(errors="replace"), re.M)
            if kept:
                body = re.sub(r"^title:\s*.+$", f"title: {kept.group(1).strip()}",
                              body, count=1, flags=re.M)

        atype = (re.search(r"^type:\s*(\w+)", body, re.M) or [None, ""])[1]
        slug = re.sub(r"[^a-z0-9]+", "-",
                      (re.search(r"^title:\s*(.+)$", body, re.M) or [None, unit_id])[1].lower()).strip("-")
        target = Path(m.group(1)) if m else None
        # Models invent target dirs (wiki/backend_new/src/..., wiki/filter/).
        # The corpus convention is wiki/<type-dir>/<slug>.md — anything else
        # falls back to the article's own type.
        if (target is None or len(target.parts) != 3
                or target.parts[1] not in TYPE_DIR.values()):
            target = Path("wiki") / TYPE_DIR.get(atype, "packages") / f"{slug}.md"
        # The model emits "wiki/<type-dir>/<slug>.md". Drop its leading "wiki"
        # and resolve under the real wiki dir, which --wiki may put outside the
        # clone entirely.
        target = wiki / Path(*target.parts[1:])
        if existing_path:      # never fork an article that already exists
            target = existing_path
        elif target.exists():
            # Two different units whose titles happen to slug to the same
            # filename must never silently overwrite each other. existing_path
            # is None here, so find_existing_article() did NOT recognize the
            # file at `target` as belonging to unit_id — if it already belongs
            # to some OTHER unit, disambiguate this write's filename instead
            # of clobbering it.
            prior_unit = (re.search(r"^unit:\s*(\S+)",
                                    target.read_text(errors="replace"), re.M)
                         or [None, ""])[1]
            if prior_unit and prior_unit != unit_id:
                suffix = re.sub(r"[^a-z0-9]+", "-", unit_id.lower()).strip("-")
                target = target.with_name(f"{target.stem}-{suffix}{target.suffix}")

        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.md"
            probe.write_text(body)
            r = validate(probe, repo, titles, None, anchor=True)
        problems = r.errors + [w for w in r.warnings if w[0] == "anchor"]
        if not problems:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + "\n")
            # The wiki may sit outside the clone, so anchor the label on it.
            return done(f"ok  -> wiki/{target.relative_to(wiki)} (attempt {attempt})")

        if attempt == retries:
            q = repo / "raw" / "quarantine"
            q.mkdir(parents=True, exist_ok=True)
            (q / f"{unit_id}.md").write_text(body + "\n")
            return done("QUARANTINED after %d attempts: %s" %
                        (retries, "; ".join(f"{c}: {d}" for c, d in problems[:3])))

        msgs += [{"role": "assistant", "content": out},
                 {"role": "user", "content":
                  "Validation failed. Fix exactly these and re-emit the whole "
                  "article:\n" + "\n".join(f"- {c}: {d}" for c, d in problems)}]
    return done("unreachable")


def rescue_quarantine(repo: Path, wiki: Path) -> list[str]:
    """Re-validate quarantined articles once the batch is complete.

    Most quarantines are forward references: article A links [[B]], B is written
    ten minutes later in the same batch, and A was judged against a wiki that
    did not contain B yet. Half of one repo's quarantine validated clean
    the moment the run finished.

    This re-checks against the finished wiki and publishes whatever now passes.
    Free — no model call. The rule is unchanged, only the moment it is applied.
    """
    q = repo / "raw" / "quarantine"
    if not q.is_dir():
        return []
    titles = article_titles(wiki)
    rescued = []
    for f in sorted(q.glob("*.md")):
        # The rollup is derived data. A file quarantined for "says 93, found 92"
        # is otherwise fine, so recompute it rather than discard paid work.
        fix_rollup(f)
        body = f.read_text(errors="replace")
        r = validate(f, repo, titles, None, anchor=True)
        if r.errors or [w for w in r.warnings if w[0] == "anchor"]:
            continue
        atype = (re.search(r"^type:\s*(\w+)", body, re.M) or [None, ""])[1]
        title = (re.search(r"^title:\s*(.+)$", body, re.M) or [None, f.stem])[1]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        target = wiki / TYPE_DIR.get(atype, "packages") / f"{slug}.md"
        if target.exists():
            continue          # a real article already owns this slug
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body if body.endswith("\n") else body + "\n")
        f.unlink()
        rescued.append(str(target.relative_to(wiki)))
    return rescued


def queued(pending: Path) -> list[str]:
    if not pending.is_file():
        return []
    txt = pending.read_text()
    # only the new/changed buckets; removed units have no entry to read
    keep, bucket = [], None
    for line in txt.splitlines():
        if line.startswith("## "):
            bucket = line
        elif line.startswith("- `") and bucket and ("new" in bucket or "changed" in bucket):
            keep.append(line.split("`")[1])
    return keep


def record_absorbed(log_p: Path, published: list[str], push_succeeded: bool) -> bool:
    """Only record units as absorbed once their articles are durably stored.
    A failed push must leave them queued — ingest re-adds anything absent
    from this log, however many times it runs — so a paid-for article that
    never reached S3 isn't silently lost if the process dies before the next
    successful push."""
    if not push_succeeded:
        return False
    try:
        logged = set(json.loads(log_p.read_text())) if log_p.is_file() else set()
    except Exception:
        logged = set()
    log_p.write_text(json.dumps(sorted(logged | set(published)), indent=1) + "\n")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--wiki", type=Path, default=None,
                    help="where articles land (default <repo>/wiki)")
    ap.add_argument("--limit", type=int, default=0, help="0 = whole queue")
    ap.add_argument("--only", action="append", help="absorb just these unit ids")
    ap.add_argument("--kind", default="code_package", help="filter entries by source_type")
    ap.add_argument("--since", help="only entries dated on/after this (YYYY-MM-DD)")
    ap.add_argument("--until", help="only entries dated on/before this (YYYY-MM-DD)")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=0,
                    help="stop once input+output tokens reach this; 0 = no ceiling")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    # Registered before any work: pipeline_run.py stops a run by SIGTERM, and
    # the default disposition would kill this process outright, losing the
    # index rebuild and S3 push for units already paid for.
    signal.signal(signal.SIGTERM, _note_sigterm)

    repo = a.repo.resolve()
    # --wiki lets the caller put articles outside the clone. ai-brain does, so a
    # repo that commits its own wiki/ cannot have it clobbered, and `git reset
    # --hard` cannot destroy generated work.
    wiki = a.wiki.resolve() if a.wiki else repo / "wiki"
    entries = repo / "raw" / "entries"
    key = os.environ.get("ABSORB_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key and not a.dry_run:
        return print("set ABSORB_API_KEY or GROQ_API_KEY (or use --dry-run)") or 2

    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    ids = a.only or queued(repo / "raw" / "_pending.md")
    by_id = {}
    for f in entries.glob("*.md"):
        # ingest.py names every entry `<YYYY-MM-DD>_<id>.md` — reading the date
        # off the filename is free; no need to also regex it out of the body.
        date = f.name[:10]
        if a.since and date < a.since:
            continue
        if a.until and date > a.until:
            continue
        head_txt = f.read_text(errors="replace")[:400]
        uid = (re.search(r"^id:\s*(\S+)", head_txt, re.M) or [None, ""])[1]
        kind = (re.search(r"^source_type:\s*(\S+)", head_txt, re.M) or [None, ""])[1]
        if uid and (not a.kind or kind == a.kind):
            by_id[uid] = f

    todo = [i for i in ids if i in by_id]
    if a.limit:
        todo = todo[:a.limit]
    if not todo:
        return print(f"nothing to absorb (queue={len(ids)}, kind={a.kind})") or 0

    scope = a.kind
    if a.since or a.until:
        scope += f", {a.since or '...'}–{a.until or '...'}"
    print(f"absorbing {len(todo)} of {len(ids)} queued ({scope}) with {MODEL}\n")
    published, failed = [], []
    stopped: str | None = None
    spent: dict = {}
    for i, uid in enumerate(todo, 1):
        # Checked BEFORE the unit, never mid-unit: a half-written article is
        # worse than one more unit's spend, and run_one already writes through
        # a tempfile so a unit is all-or-nothing.
        if (why := stop_reason(_sigterm["seen"], spent, a.max_tokens)):
            stopped = why
            print(f"    stopped: {stopped}")
            break
        print(f"[{i}/{len(todo)}] {uid}")
        # run_one's own retry loop covers bad model OUTPUT (validation
        # failures); it does not cover the network call itself. A raw
        # connection reset mid-batch used to be an uncaught exception that
        # killed every remaining unit — one bad unit must not cost the rest
        # of a 228-unit run, same principle as the links feeder's per-URL
        # isolation.
        try:
            result, used = run_one(repo, wiki, uid, by_id[uid], key, head,
                                   a.retries, a.dry_run)
            spent = add_usage(spent, {"prompt_tokens": used.get("in", 0),
                                      "completion_tokens": used.get("out", 0)})
        except SystemExit as e:
            # groq() raises this for any rejected call — most commonly an
            # exhausted API budget. Units published earlier in this SAME
            # batch are already written to wiki/ but not yet logged/indexed/
            # pushed (that happens once, below, after the loop) — breaking
            # instead of letting this propagate is what makes that bookkeeping
            # still happen instead of silently losing it to an unhandled exit.
            stopped = str(e)
            print(f"    stopped: {stopped}")
            break
        except Exception as e:
            result = f"error: {type(e).__name__}: {e}"
            failed.append(uid)
        print("   ", result)
        if result.startswith("ok"):
            published.append(uid)
            # A unit quarantined on an earlier run and retried successfully
            # left its stale draft in raw/quarantine/ forever — nothing here
            # cleaned it up, so the directory kept reporting units as still
            # failing after they'd actually succeeded. raw/ is disposable
            # scratch regardless; this just keeps it honest in the meantime.
            stale = repo / "raw" / "quarantine" / f"{uid}.md"
            if stale.is_file():
                stale.unlink()
    if failed:
        print(f"\n{len(failed)} unit(s) failed outright (not quarantined — never "
              f"got a validatable draft): {', '.join(failed[:10])}"
              + (f" (+{len(failed) - 10} more)" if len(failed) > 10 else ""))
        print("re-run with the same --kind to retry them; nothing was skipped "
              "permanently.")
    if published:
        # Mechanical, not a remembered step. absorb feeds _index.md back into the
        # next prompt and wikilinks may only use titles from it, so a batch that
        # ends without rebuilding leaves the following batch blind to everything
        # it just wrote — which is how one topic became four MCP Server articles.
        # Before the index: a rescued article must be in it.
        rescued = rescue_quarantine(repo, wiki)
        if rescued:
            print(f"\nrescued {len(rescued)} from quarantine "
                  f"(forward references, now resolvable):")
            for t in rescued[:10]:
                print(f"    {t}")

        r = build_index.rebuild(wiki)
        print(f"\nindex rebuilt: {r['articles']} articles")
        for norm, variants in r["duplicate_titles"].items():
            print(f"  DUPLICATE  {sorted(set(variants))}", file=sys.stderr)
        if r["dangling_links"]:
            print(f"  dangling wikilinks: {r['dangling_links'][:8]}", file=sys.stderr)

        # New articles cost an LLM call each — back them up the moment they
        # exist, same as connectors.py does for feeder sources. server/ is
        # ai-brain-specific and this script also runs standalone against
        # sibling repos without it, so the import is lazy and best-effort:
        # missing module, no S3_BUCKET, or a real S3 error must never undo
        # articles already written to disk.
        pushed_ok = True
        try:
            # ai-brain's own root, NOT `repo` — absorb also runs against
            # sibling repos that have no server/ package (same reasoning as
            # version_of()'s sys.path.insert above).
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from server import storage
            n = storage.push()
            if n:
                print(f"\ns3 push: {n} files")
        except Exception as e:
            pushed_ok = False
            print(f"\ns3 push failed: {e} (these units stay queued for the next run)",
                  file=sys.stderr)

        # Mark published units absorbed, or the next ingest re-queues them and a
        # CI loop re-buys the same articles on every push. Quarantined units stay
        # out: still queued is exactly what a failed gate should be. Gated on the
        # push above: a unit whose article never reached S3 must stay queued too,
        # or a paid-for article can be lost for good if the process dies before
        # the next successful push.
        log_p = wiki / "_absorb_log.json"
        record_absorbed(log_p, published, pushed_ok)
    if stopped:
        # Everything published above this point is logged, indexed and pushed
        # already — re-running (same or a different ABSORB_BASE/ABSORB_API_KEY/
        # ABSORB_MODEL in .env, e.g. to switch provider) picks up exactly where
        # this stopped; nothing here needs to be redone.
        print(f"\nstopped after {len(published)}/{len(todo)} units: {stopped}\n"
              "already-published work above is saved — re-run once the key "
              "works again (same provider, or point ABSORB_BASE/ABSORB_API_KEY/"
              "ABSORB_MODEL in .env at a different one) to continue.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
