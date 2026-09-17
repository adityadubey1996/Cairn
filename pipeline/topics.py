#!/usr/bin/env python3
"""
topics.py — discover, materialize and maintain subject-matter topics.

A topic answers "what is this about" (Carbon AI, Well Economics); an article
type answers "what question does this article answer". Types are frozen in
vocab.py; topics are an overlay the system may create and destroy freely.
Design: docs/superpowers/specs/2026-08-18-rl-topic-discovery-design.md.

    python3 pipeline/topics.py --propose            # LLM proposes; threshold gates
    python3 pipeline/topics.py --score              # reward from brain_messages
    python3 pipeline/topics.py --auto               # score, then propose if pressure
    python3 pipeline/topics.py --status             # print the state, spend nothing

State: wiki/_topics.json (source of truth). Hubs: wiki/topics/<slug>.md — a
regenerated VIEW of the state; hand edits are overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vocab  # noqa: E402
from build_index import read_article  # noqa: E402

# ---- TUNABLES (spec §11) ----------------------------------------------------
MIN_MEMBERS = 5             # materialization floor
COHERENCE_MARGIN = 0.05     # over corpus-baseline cosine
MIN_CONFIDENCE = 0.5        # assignment kept
MAX_TOPICS_PER_ARTICLE = 2  # membership cap
SOFT_MAX_ACTIVE = 12        # proposer told to prefer assignment past this
SCORE_WINDOW = 200          # trailing reward window, in messages
DECAY_CYCLES = 3            # unused cycles before decay
CYCLE_MIN_MESSAGES = 20     # messages for a --score run to count as a cycle
PRESSURE_UNASSIGNED = 15    # auto-propose trigger
PRESSURE_FALLBACK = 5       # auto-propose trigger (packages/ landings)
MERGE_JACCARD = 0.5         # overlap triggering a merge suggestion
# Unlike absorb_runner's per-unit prompts, this one scales with total corpus
# size (every article gets one line), so it has no natural bound without this
# cap. Same ceiling absorb_runner.py uses, for the same reason: a 200k-token
# prompt costs real money and the model can't act on all of it anyway.
MAX_PROMPT_CHARS = 60_000
# -----------------------------------------------------------------------------

MODEL = os.environ.get("TOPICS_MODEL",
                       os.environ.get("ABSORB_MODEL", "llama-3.3-70b-versatile"))
# Same ABSORB_BASE/ABSORB_API_KEY override as absorb_runner.py — keeps this off
# GROQ_API_KEY, which server/wikilib.py uses for live chat against the real
# api.groq.com.
GROQ_URL = (os.environ.get("ABSORB_BASE")
           or os.environ.get("GROQ_BASE", "https://api.groq.com/openai/v1")) + "/chat/completions"

STATE_DEFAULT = {
    "topics": {},
    "unassigned": [],
    "pressure": {"packages_fallback": 0, "domain_overflow": 0, "since": ""},
    "last_scored_at": None,
}


def today() -> str:
    return date.today().isoformat()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_state(wiki: Path) -> dict:
    p = wiki / "_topics.json"
    if not p.is_file():
        return json.loads(json.dumps(STATE_DEFAULT))
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(wiki: Path, state: dict) -> None:
    # Atomic like server/index.py: never let a reader see half a file.
    fd, tmp = tempfile.mkstemp(dir=wiki, suffix=".topics.tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(state, indent=1) + "\n")
    os.replace(tmp, wiki / "_topics.json")


def catalog(wiki: Path) -> list[dict]:
    """One row per real article. Hubs are never members of anything."""
    out = []
    for p in wiki.rglob("*.md"):
        if p.name.startswith("_") or p.parent.name == "topics":
            continue
        a = read_article(p)
        if a:
            out.append({"rel": str(p.relative_to(wiki)).replace("\\", "/"),
                        "title": a["title"], "type": a["type"],
                        "lede": a["summary"]})
    out.sort(key=lambda c: c["rel"])
    return out


def index_db_for(wiki: Path) -> Path:
    # Same slug rule as server/index.py db_path() — kept as a 2-line copy so
    # this script runs standalone against sibling wikis, like absorb_runner.
    slug = re.sub(r"[^a-z0-9]+", "-", str(wiki).lower()).strip("-")
    return Path(__file__).resolve().parent.parent / "var" / f"index-{slug[-80:]}.sqlite3"


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def read_vectors(index_db: Path, rels: list[str]) -> dict[str, tuple[float, ...]]:
    if not index_db.is_file():
        return {}
    db = sqlite3.connect(index_db)
    marks = ",".join("?" * len(rels))
    rows = db.execute(f"SELECT path, vec FROM embeddings WHERE path IN ({marks})",
                      rels).fetchall()
    db.close()
    return {rel: struct.unpack(f"{len(blob) // 4}f", blob) for rel, blob in rows}


def corpus_baseline(index_db: Path, pairs: int = 200, seed: int = 0) -> float:
    """Mean cosine of random article pairs — what 'unrelated' looks like here."""
    import random
    if not index_db.is_file():
        return 0.0
    db = sqlite3.connect(index_db)
    rows = db.execute("SELECT vec FROM embeddings").fetchall()
    db.close()
    vecs = [struct.unpack(f"{len(b) // 4}f", b) for (b,) in rows]
    if len(vecs) < 2:
        return 0.0
    rng = random.Random(seed)  # seeded: same corpus -> same baseline
    sims = []
    for _ in range(pairs):
        a, b = rng.sample(vecs, 2)
        sims.append(_cosine(a, b))
    return sum(sims) / len(sims)


def coherence_check(member_rels: list[str], index_db: Path,
                    baseline: float) -> tuple[bool, str]:
    vecs = list(read_vectors(index_db, list(member_rels)).values())
    if len(vecs) < 3:
        # index build skips articles whose embedding call failed; a topic must
        # not be blocked forever because Ollama was down during one build.
        return True, f"coherence: skipped (<3 vectors, have {len(vecs)})"
    sims = [_cosine(vecs[i], vecs[j])
            for i in range(len(vecs)) for j in range(i + 1, len(vecs))]
    mean = sum(sims) / len(sims)
    ok = mean >= baseline + COHERENCE_MARGIN
    return ok, f"coherence: mean {mean:.3f} vs baseline {baseline:.3f}+{COHERENCE_MARGIN}"


def llm(messages: list[dict], key: str, temperature: float = 0.2) -> str:
    body = json.dumps({"model": MODEL, "messages": messages,
                       "temperature": temperature}).encode()
    req = urllib.request.Request(
        GROQ_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 # Cloudflare rejects default urllib UA — same fix as absorb_runner.
                 "User-Agent": "topics-runner/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except Exception as e:  # one failed round must not kill an --auto run
        raise RuntimeError(f"groq call failed: {e}") from e


def merge_candidates(state: dict) -> list[tuple[str, str]]:
    actives = {s: set(t["members"]) for s, t in state["topics"].items()
               if t["status"] == "active" and not t["pinned"]}
    out = []
    slugs = sorted(actives)
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            union = actives[a] | actives[b]
            if union and len(actives[a] & actives[b]) / len(union) >= MERGE_JACCARD:
                out.append((a, b))
    return out


PROPOSAL_SCHEMA = """Reply with JSON only, exactly this shape:
{"new_topics": [{"name": "Title Case", "description": "Two sentences.",
                 "members": {"<rel path>": 0.0-1.0 confidence}}],
 "assignments": {"<existing slug>": {"<rel path>": confidence}},
 "merges": [{"a": "<slug>", "b": "<slug>"}]}"""


def _fit_to_budget(section: list[str], budget: int, noun: str) -> tuple[list[str], int]:
    """Keep as many lines of `section` as fit in `budget` chars, appending an
    '... N more <noun> omitted' line when any are dropped. Returns the kept
    lines and the total chars they (including any omitted-count line) cost —
    the caller uses that to charge a second droppable section against what's
    left. Never returns more than `budget` chars' worth of lines: if even the
    omitted-count line itself doesn't fit, it is left out rather than pushed
    over the ceiling.
    """
    budget = max(budget, 0)
    kept, used = [], 0
    for line in section:
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    dropped = len(section) - len(kept)
    if dropped:
        omitted_line = f"  ... and {dropped} more {noun} omitted to fit the prompt budget"
        # The omitted line itself takes space; if it doesn't fit, drop one more
        # kept line and recompute (dropped's digit count can grow by one here,
        # which is why this is a loop, not a single check).
        while kept and used + len(omitted_line) + 1 > budget:
            used -= len(kept.pop()) + 1
            dropped += 1
            omitted_line = f"  ... and {dropped} more {noun} omitted to fit the prompt budget"
        if used + len(omitted_line) + 1 <= budget:
            kept.append(omitted_line)
            used += len(omitted_line) + 1
    return kept, used


def build_proposal_prompt(cat: list[dict], state: dict) -> str:
    actives = {s: t for s, t in state["topics"].items()
               if t["status"] in ("active", "candidate")}
    vetoed = [t["name"] for t in state["topics"].values() if t["status"] == "vetoed"]
    n_active = sum(1 for t in actives.values() if t["status"] == "active")
    lines = [
        "You group wiki articles into SUBJECT-MATTER topics (what they are about,",
        "e.g. 'Carbon AI', 'Well Economics') — never by article type.",
        "Rules:",
        f"- an article belongs to at most {MAX_TOPICS_PER_ARTICLE} topics",
        "- only propose a topic when several articles genuinely share a subject",
        f"- NEVER propose these vetoed names: {', '.join(vetoed) or '(none)'}",
        "- never propose a name that matches an existing article's title",
    ]
    if n_active >= SOFT_MAX_ACTIVE:
        lines.append(f"- {n_active} topics already exist — prefer assignments "
                     "over new topics")
    lines += ["", "EXISTING TOPICS (slug: name — members):"]
    for s, t in sorted(actives.items()):
        lines.append(f"  {s}: {t['name']} [{t['status']}] — {len(t['members'])} members")
    mc = merge_candidates(state)
    if mc:
        lines += ["", "MERGE CANDIDATES — confirm in merges[] or leave out to reject:"]
        lines += [f"  {a} + {b}" for a, b in mc]

    # Both the unassigned list and the article catalog are droppable — on a
    # fresh corpus with no topics yet, recompute_unassigned() makes
    # `unassigned` equal to the ENTIRE catalog, so the "fixed" portion below
    # must never embed either list uncounted; only the truly fixed headers do.
    unassigned_header = ["", "UNASSIGNED ARTICLES (organize these first):"]
    article_header = ["", "ALL ARTICLES (rel | type | title | first line):"]
    unassigned_lines = [f"  {r}" for r in state["unassigned"]]
    article_lines = [f"  {c['rel']} | {c['type']} | {c['title']} | {c['lede']}"
                     for c in cat]

    fixed = "\n".join(lines + unassigned_header + article_header + ["", PROPOSAL_SCHEMA])
    budget = MAX_PROMPT_CHARS - len(fixed)

    # Unassigned gets first call on the remaining budget: the section right
    # above literally tells the model to organize these first, and a
    # truncated catalog is still useful context on its own, while a
    # truncated unassigned list is the one thing the model is asked to act
    # on directly.
    unassigned_kept, unassigned_used = _fit_to_budget(unassigned_lines, budget, "unassigned articles")
    article_kept, _ = _fit_to_budget(article_lines, budget - unassigned_used, "articles")

    return "\n".join(lines + unassigned_header + unassigned_kept
                     + article_header + article_kept + ["", PROPOSAL_SCHEMA])


def parse_proposal(text: str) -> dict | None:
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        raw = json.loads(body)
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    out = {"new_topics": raw.get("new_topics", []),
           "assignments": raw.get("assignments", {}),
           "merges": raw.get("merges", [])}
    if not (isinstance(out["new_topics"], list) and isinstance(out["assignments"], dict)
            and isinstance(out["merges"], list)):
        return None
    for t in out["new_topics"]:
        if not (isinstance(t, dict) and t.get("name")
                and isinstance(t.get("members"), dict)):
            return None
    return out


def _note(t: dict, day: str, event: str, **extra) -> None:
    t["history"].append({"date": day, "event": event, **extra})


def _memberships(state: dict, rel: str) -> list[tuple[str, float]]:
    return [(s, t["members"][rel]) for s, t in state["topics"].items()
            if t["status"] in ("active", "candidate") and rel in t["members"]]


def _admit_members(state: dict, slug: str, offered: dict, known: set) -> dict:
    """Confidence floor, catalog check, and the per-article cap."""
    kept = {}
    for rel, conf in offered.items():
        if rel not in known or not isinstance(conf, (int, float)) or conf < MIN_CONFIDENCE:
            continue
        holders = [h for h in _memberships(state, rel) if h[0] != slug]
        if len(holders) >= MAX_TOPICS_PER_ARTICLE:
            # existing memberships win ties: displace only a strictly weaker one
            weakest, w_conf = min(holders, key=lambda h: h[1])
            if conf <= w_conf:
                continue
            del state["topics"][weakest]["members"][rel]
        kept[rel] = float(conf)
    return kept


def _try_materialize(state: dict, slug: str, coh, day: str, log: list[str]) -> None:
    t = state["topics"][slug]
    if t["status"] != "candidate" or len(t["members"]) < MIN_MEMBERS:
        return
    ok, note = coh(sorted(t["members"]))
    if ok:
        t["status"] = "active"
        _note(t, day, "materialized", members=len(t["members"]))
        log.append(f"materialized {slug} ({len(t['members'])} members; {note})")
    else:
        log.append(f"held {slug} as candidate ({note})")


def _collides_with_article_title(name: str, cat: list[dict]) -> bool:
    needle = name.strip().casefold()
    return any(c["title"].strip().casefold() == needle for c in cat)


def apply_proposal(state: dict, proposal: dict, cat: list[dict],
                   coh, day: str) -> list[str]:
    known = {c["rel"] for c in cat}
    log: list[str] = []

    for nt in proposal["new_topics"]:
        slug = slugify(nt["name"])
        if not slug:
            continue
        if _collides_with_article_title(nt["name"], cat):
            log.append(f"skipped {slug}: name collides with an existing article title")
            continue
        existing = state["topics"].get(slug)
        if existing and existing["status"] == "vetoed":
            log.append(f"skipped vetoed {slug}")
            continue
        if existing is None:
            state["topics"][slug] = {
                "name": nt["name"].strip(), "description": (nt.get("description") or "").strip(),
                "status": "candidate", "pinned": False, "members": {},
                "score": {"window_uses": 0, "cycles_unused": 0, "last_used": ""},
                "created": day, "history": []}
            _note(state["topics"][slug], day, "proposed")
            log.append(f"proposed {slug}")
        t = state["topics"][slug]
        t["members"].update(_admit_members(state, slug, nt["members"], known))
        if t["status"] == "decayed" and t["members"]:
            t["status"] = "candidate"          # revival is cheap by design
            _note(t, day, "revived")
        _try_materialize(state, slug, coh, day, log)

    for slug, offered in proposal["assignments"].items():
        if not isinstance(offered, dict):
            log.append(f"skipped malformed assignment for {slug}")
            continue
        t = state["topics"].get(slug)
        if not t or t["status"] not in ("active", "candidate"):
            continue
        added = _admit_members(state, slug, offered, known)
        if added:
            t["members"].update(added)
            _note(t, day, "assigned", added=len(added))
            log.append(f"assigned {len(added)} article(s) to {slug}")
        _try_materialize(state, slug, coh, day, log)

    for m in proposal["merges"]:
        if not isinstance(m, dict):
            log.append("skipped malformed merge entry")
            continue
        if m.get("a") == m.get("b"):
            log.append(f"skipped self-merge {m.get('a')}")
            continue
        a, b = state["topics"].get(m.get("a")), state["topics"].get(m.get("b"))
        if not a or not b or b["pinned"] or a["status"] != "active" or b["status"] != "active":
            continue
        for rel, conf in list(b["members"].items()):
            del b["members"][rel]
            a["members"].update(_admit_members(state, m["a"], {rel: conf}, known))
        b["status"] = "merged"
        _note(b, day, "merged_into", target=m["a"])
        _note(a, day, "absorbed", source=m["b"])
        log.append(f"merged {m['b']} into {m['a']}")

    recompute_unassigned(state, cat)
    return log


def recompute_unassigned(state: dict, cat: list[dict]) -> None:
    assigned = {rel for t in state["topics"].values()
                if t["status"] in ("active", "candidate") for rel in t["members"]}
    state["unassigned"] = sorted({c["rel"] for c in cat} - assigned)


HUB_FOOTER = "<!-- generated by pipeline/topics.py — hand edits are overwritten -->"


def render_hub(t: dict, cat: list[dict], day: str) -> str:
    by_rel = {c["rel"]: c for c in cat}
    heading = dict(vocab.TYPE_ORDER)
    sections: dict[str, list[str]] = {}
    for rel in sorted(t["members"]):
        c = by_rel.get(rel)
        if not c:
            continue                       # dangling member: dropped at render
        key = c["type"] if c["type"] in heading else "Other"
        sections.setdefault(key, []).append(f"[[{c['title']}]]")
    ordered = [k for k, _ in vocab.TYPE_ORDER if k in sections]
    if "Other" in sections:
        ordered.append("Other")
    lines = ["---", f"title: {t['name']}", "hub: true",
             f"created: {t['created']}", f"last_updated: {day}", "---", "",
             f"# {t['name']}", "", t["description"], ""]
    for key in ordered:
        lines += [f"## {heading.get(key, key)}", "",
                  " · ".join(sections[key]), ""]
    lines.append(HUB_FOOTER)
    return "\n".join(lines) + "\n"


def _sans_updated(text: str) -> str:
    return re.sub(r"^last_updated:.*$", "", text, count=1, flags=re.M)


def write_hubs(wiki: Path, state: dict, cat: list[dict], day: str) -> dict:
    hubs_dir = wiki / "topics"
    hubs_dir.mkdir(exist_ok=True)
    written, removed = [], []
    active = {s: t for s, t in state["topics"].items() if t["status"] == "active"}
    for slug, t in sorted(active.items()):
        target = hubs_dir / f"{slug}.md"
        new = render_hub(t, cat, day)
        if target.is_file():
            old = target.read_text(encoding="utf-8", errors="replace")
            # keep created: stable across reruns — the state's created wins only
            # for brand-new hubs; an existing file already carries the truth
            if _sans_updated(old) == _sans_updated(new):
                continue
        target.write_text(new, encoding="utf-8")
        written.append(f"topics/{slug}.md")
    for p in sorted(hubs_dir.glob("*.md")):
        if p.stem not in active:
            p.unlink()
            removed.append(f"topics/{p.name}")
    return {"written": written, "removed": removed}


def _credit(t: dict, slug: str, meta: dict, member_texts: dict) -> int:
    members = set(t["members"])
    proc = meta.get("process") or {}
    seeds = {h.get("path") for h in proc.get("hits", [])}
    ctx = {a.get("path") for a in proc.get("articles", [])}
    cited = set()
    for c in meta.get("citations", []):
        needle = f"{c.get('path')}@{c.get('sha')}"
        cited |= {rel for rel in members if needle in member_texts.get(rel, "")}
    used = (seeds | ctx | cited) & members
    if f"topics/{slug}.md" in seeds:
        credit = 1
    elif len(used) >= 2 and (seeds & members):
        # external-demand rule: without a similarity-seeded member, members in
        # context are just this hub's own expansion echoing back — no reward.
        credit = 1
    else:
        return 0
    return credit * 2 if cited else credit


def score_messages(state: dict, messages: list[dict],
                   member_texts: dict[str, str], day: str) -> list[str]:
    log: list[str] = []
    window = messages[-SCORE_WINDOW:]
    new = [m for m in messages if m["created_at"] > (state["last_scored_at"] or "")]
    is_cycle = len(new) >= CYCLE_MIN_MESSAGES
    for slug, t in state["topics"].items():
        if t["status"] != "active":
            continue
        uses = sum(_credit(t, slug, m.get("meta") or {}, member_texts) for m in window)
        t["score"]["window_uses"] = uses
        if uses:
            t["score"]["last_used"] = day
        if is_cycle:
            t["score"]["cycles_unused"] = 0 if uses else t["score"]["cycles_unused"] + 1
            if (t["score"]["cycles_unused"] >= DECAY_CYCLES and not t["pinned"]):
                t["status"] = "decayed"
                _note(t, day, "decayed", cycles_unused=t["score"]["cycles_unused"])
                log.append(f"decayed {slug} (unused {t['score']['cycles_unused']} cycles)")
        log.append(f"{slug}: {uses} use(s) in window")
    if messages:
        candidates = [m["created_at"] for m in messages]
        if state["last_scored_at"]:
            candidates.append(state["last_scored_at"])
        state["last_scored_at"] = max(candidates)
    log.append(f"cycle counted: {is_cycle} ({len(new)} new messages)")
    return log


def mine_pressure(wiki: Path, state: dict, day: str) -> None:
    fallback_total = len(list((wiki / "packages").glob("*.md"))) if (wiki / "packages").is_dir() else 0
    domain_n = len(list((wiki / "domain").glob("*.md"))) if (wiki / "domain").is_dir() else 0
    prev_total = state["pressure"].get("packages_fallback_total", 0)
    state["pressure"] = {"packages_fallback": max(0, fallback_total - prev_total),
                         "packages_fallback_total": fallback_total,
                         "domain_overflow": max(0, domain_n - 20),  # taxonomy cap: 10-20
                         "since": day}


def pressure_says_propose(state: dict) -> bool:
    return (len(state["unassigned"]) >= PRESSURE_UNASSIGNED
            or state["pressure"]["packages_fallback"] >= PRESSURE_FALLBACK)


def member_texts_for(wiki: Path, state: dict) -> dict[str, str]:
    # ponytail: full read per --score run — ~200 small files, milliseconds,
    # same call the assemble._scan path already makes per chat request.
    out = {}
    for t in state["topics"].values():
        if t["status"] != "active":
            continue
        for rel in t["members"]:
            p = wiki / rel
            if rel not in out and p.is_file():
                out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def fetch_messages(limit: int = SCORE_WINDOW) -> list[dict]:
    """Last `limit` assistant rows, oldest first. Lazy imports: pipeline scripts
    must run against sibling wikis with no server/ or psycopg present."""
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    try:
        from server import config as sconfig
        dsn = sconfig.DATABASE_URL
    except Exception:
        dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("no DATABASE_URL — cannot score without brain_messages")
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT id, citations, created_at FROM brain_messages WHERE role = 'assistant' "
            "ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
    out = []
    for r in reversed(rows):
        meta = r["citations"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        out.append({"id": str(r["id"]), "created_at": r["created_at"].isoformat(),
                    "meta": meta or {}})
    return out


def cmd_propose(wiki: Path, dry: bool) -> int:
    state = load_state(wiki)
    cat = catalog(wiki)
    recompute_unassigned(state, cat)
    prompt = build_proposal_prompt(cat, state)
    if dry:
        print(prompt)
        print(f"\n--- dry-run: {len(cat)} articles, {len(prompt):,} prompt chars, no spend")
        return 0
    key = os.environ.get("ABSORB_API_KEY") or os.environ.get("GROQ_API_KEY")
    if not key:
        return print("set ABSORB_API_KEY or GROQ_API_KEY (or use --dry-run)") or 2
    msgs = [{"role": "user", "content": prompt}]
    proposal = None
    for attempt in (1, 2):
        out = llm(msgs, key)
        proposal = parse_proposal(out)
        if proposal:
            break
        msgs += [{"role": "assistant", "content": out},
                 {"role": "user", "content": "That was not valid JSON of the "
                  "required shape. Re-emit ONLY the JSON."}]
    if not proposal:
        return print("proposer returned unparseable output twice; nothing changed") or 1
    db = index_db_for(wiki)
    base = corpus_baseline(db)
    coh = lambda rels: coherence_check(rels, db, base)
    for line in apply_proposal(state, proposal, cat, coh, today()):
        print(f"  {line}")
    r = write_hubs(wiki, state, cat, today())
    save_state(wiki, state)
    print(f"hubs: {len(r['written'])} written, {len(r['removed'])} removed; "
          f"{len(state['unassigned'])} unassigned")
    return 0


def cmd_score(wiki: Path) -> int:
    state = load_state(wiki)
    try:
        messages = fetch_messages()
    except Exception as e:
        return print(f"score failed: {e}") or 1
    texts = member_texts_for(wiki, state)
    for line in score_messages(state, messages, texts, today()):
        print(f"  {line}")
    write_hubs(wiki, state, catalog(wiki), today())   # drops decayed hubs
    mine_pressure(wiki, state, today())
    save_state(wiki, state)
    return 0


def cmd_status(wiki: Path) -> int:
    state = load_state(wiki)
    recompute_unassigned(state, catalog(wiki))
    for slug, t in sorted(state["topics"].items()):
        pin = " pinned" if t["pinned"] else ""
        print(f"{t['status']:<9} {slug:<32} {len(t['members']):>3} members  "
              f"{t['score']['window_uses']:>3} uses{pin}")
    print(f"{len(state['unassigned'])} unassigned; pressure {state['pressure']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", type=Path,
                    default=Path(__file__).resolve().parent.parent / "wiki")
    ap.add_argument("--propose", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="score, then propose only under pressure")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    wiki = a.wiki.resolve()
    if a.status:
        return cmd_status(wiki)
    if a.score or a.auto:
        rc = cmd_score(wiki)
        if rc:
            return rc
        if not (a.auto or a.propose):
            return rc
    if a.auto:
        state = load_state(wiki)
        recompute_unassigned(state, catalog(wiki))
        if not pressure_says_propose(state):
            print("no pressure — skipping propose")
            return 0
    if a.propose or a.auto:
        return cmd_propose(wiki, a.dry_run)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
