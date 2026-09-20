"""Offline regressions for complete, restartable personal-source compilation."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from pipeline import absorb_runner as writer
from pipeline import ingest
from pipeline.completion import read_completed, write_completed
from pipeline.source_files import source_path


def test_changed_revision_remains_pending_across_discovery_runs():
    unit = {"id": "report", "sha": "v2", "path": "sources/report.md",
            "status": "active", "first": "2026-09-20"}
    for discovered in ("v1", "v2"):
        pending = ingest.build_pending([unit], {"report": (discovered, "active")},
                                       {"report": unit["path"]}, {"report": "v1"})
        assert pending["changed"] == ["report"]
    done = ingest.build_pending([unit], {"report": ("v2", "active")}, {}, {"report": "v2"})
    assert done["changed"] == done["new"] == []
    # Disposable discovery state can be rebuilt without buying completed work.
    rebuilt = ingest.build_pending([unit], {}, {}, {"report": "v2"})
    assert rebuilt["changed"] == rebuilt["new"] == []
    migrated = ingest.build_pending([unit], {}, {}, {"report": None})
    assert migrated["new"] == [] and migrated["changed"] == ["report"]


def test_legacy_ledger_requires_a_revision_refresh(tmp_path):
    ledger = tmp_path / "_absorb_log.json"
    ledger.write_text('["report"]')
    assert read_completed(ledger) == {"report": None}
    write_completed(ledger, {"report": "v2"})
    assert read_completed(ledger) == {"report": "v2"}


def test_local_publication_records_revision_even_when_backup_fails(tmp_path):
    ledger = tmp_path / "_absorb_log.json"
    assert writer.record_absorbed(ledger, {"report": "v2"}, push_succeeded=False)
    assert read_completed(ledger) == {"report": "v2"}


def test_ingest_connector_only_directory_without_git(monkeypatch, tmp_path):
    inbox = tmp_path / "raw" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "upload-one.md").write_text(
        '---\nid: upload-one\npath: sources/upload/one.md\nsha: v1\n'
        'source_type: doc\ndate: 2026-09-20\n---\nEvidence\n')
    wiki = tmp_path / "durable-wiki"
    monkeypatch.setattr(sys, "argv", ["ingest", "--repo", str(tmp_path), "--wiki", str(wiki)])
    ingest.main()
    assert (tmp_path / "raw" / "entries" / "2026-09-20_upload-one.md").is_file()
    assert json.loads((tmp_path / "raw" / "_pending.json").read_text())["new"] == ["upload-one"]
    assert (wiki / "_manifest.json").is_file()


def test_external_source_volume_and_local_citation(monkeypatch, tmp_path):
    from server import config
    repo, mounted = tmp_path / "app", tmp_path / "mounted"
    repo.mkdir()
    (mounted / "upload").mkdir(parents=True)
    source = mounted / "upload" / "one.md"
    source.write_text("Complete local evidence")
    monkeypatch.setattr(config, "ROOT", repo)
    monkeypatch.setattr(config, "SOURCES_DIR", mounted)
    assert source_path(repo, "sources/upload/one.md") == source
    assert writer.version_of(repo, "sources/upload/one.md") == hashlib.sha256(source.read_bytes()).hexdigest()[:8]
    body, citations = writer.gather(repo, ["sources/upload/one.md"])
    assert "Complete local evidence" in body
    assert len(citations) == 1
    with pytest.raises(ValueError):
        source_path(repo, "sources/../../secret")
    outside = tmp_path / "outside.md"
    outside.write_text("private")
    (mounted / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError):
        source_path(repo, "sources/escape.md")


def test_all_source_fragments_are_read_and_cached(monkeypatch, tmp_path):
    source = "sources/upload/book.md@abcdef01"
    raw = "\n\n".join(f"SECTION_{i}: " + "detail " * 2200 for i in range(6))
    raw += "\nTAIL_DECISION: approved on September 20."
    text = f"===== FILE {source} =====\n{raw}"
    inputs = []

    def extract(messages):
        body = messages[-1]["content"]
        inputs.append(body)
        ids = __import__('re').findall(r"(?m)^(S\d+):", body)
        return json.dumps({"selected": [ids[-1] if "TAIL_DECISION" in body else ids[0]]})

    evidence, parts = writer.prepare_evidence(text, [source], tmp_path, extract)
    assert parts > 1
    assert "TAIL_DECISION" in evidence
    assert any("TAIL_DECISION" in chunk for chunk in inputs)
    assert "".join(writer.evidence_chunks(raw)) == raw
    # Restart reuses successful extraction; compiling still reads every cached part.
    count = len(inputs)
    again, again_parts = writer.prepare_evidence(text, [source], tmp_path, extract)
    assert again == evidence and again_parts == parts
    assert len(inputs) == count


def test_gather_never_discards_tail_or_later_files(monkeypatch, tmp_path):
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text("x" * 26000 + f"TAIL_{name}")
    monkeypatch.setattr(writer, "version_of", lambda *_: "abcdef01")
    text, citations = writer.gather(tmp_path, ["a.md", "b.md", "c.md"])
    assert len(citations) == 3
    assert all(f"TAIL_{name}" in text for name in ("a.md", "b.md", "c.md"))


def test_selected_ollama_wins_over_unrelated_cloud_key(monkeypatch):
    from server import llm
    monkeypatch.setattr(llm, "_env", lambda name: "unused-cloud-key" if name == "GROQ_API_KEY" else "")
    monkeypatch.setattr(llm, "resolve", lambda: {
        "preset": "ollama", "base": "http://localhost:11434/v1",
        "model": "llama3.1:8b", "kind": "openai", "key": ""})
    env = llm.absorb_env()
    assert env["ABSORB_PROTOCOL"] == "ollama"
    assert env["ABSORB_API_KEY"] == "ollama-local"
    assert env["ABSORB_MODEL"] == "llama3.1:8b"


def test_writer_gate_rejects_ungraded_claims_and_unseen_sources(tmp_path, monkeypatch):
    from pipeline import validate_wiki
    source = tmp_path / "real.md"
    source.write_text("Evidence")
    monkeypatch.setattr(validate_wiki, "git_blob_sha", lambda *_: "abcdef01")
    article = tmp_path / "article.md"
    article.write_text(
        '---\ntitle: Report\ntype: domain\ncreated: 2026-09-20\n'
        'last_updated: 2026-09-20\nstale: false\ngrades: {doc: 1, gap: 1}\n'
        'sources: []\nrelated: []\n---\n# Report\n\n'
        + '\n\n'.join("An unsupported assertion." for _ in range(8))
        + '\n\nA sourced assertion. [doc: real.md@abcdef01]\n\n[gap: unknown]\n')
    report = validate_wiki.validate(article, tmp_path, set(), None, anchor=False,
                                    allowed_citations=set(), require_grades=True)
    assert any(code == "ungraded claim" for code, _ in report.errors)
    assert any(code == "unseen source" for code, _ in report.errors)


def test_recovery_of_completed_revision_emits_done_without_model(monkeypatch, tmp_path, capsys):
    from server import storage
    entries = tmp_path / "raw" / "entries"
    entries.mkdir(parents=True)
    source = tmp_path / "sources" / "upload" / "one.md"
    source.parent.mkdir(parents=True)
    source.write_text("The release date is September 20.")
    version = hashlib.sha256(source.read_bytes()).hexdigest()[:8]
    (entries / "2026-09-20_upload-one.md").write_text(
        '---\nid: upload-one\nsha: revision-1\nsource_type: doc\n'
        'path: sources/upload/one.md\n---\n')
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "release.md").write_text(
        '---\ntitle: Release\ntype: domain\nunit: upload-one\n'
        'source_revision: revision-1\nsources: []\nrelated: []\n'
        'grades: {doc: 1}\n---\n# Release\n'
        f'The release date is September 20. [doc: sources/upload/one.md@{version}]\n')
    write_completed(wiki / "_absorb_log.json", {"upload-one": "revision-1"})
    monkeypatch.setenv("ABSORB_API_KEY", "offline-test")
    monkeypatch.setattr(storage, "push", lambda: 0)
    monkeypatch.setattr(writer, "groq", lambda *_a, **_kw: pytest.fail("completed source must not call model"))
    monkeypatch.setattr(sys, "argv", ["absorb", "--repo", str(tmp_path), "--wiki", str(wiki),
                                     "--kind", "", "--only", "upload-one"])
    assert writer.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"event"')]
    unit = next(event for event in events if event["event"] == "unit_result")
    assert unit["status"] == "done" and unit["tokens_in"] == unit["tokens_out"] == 0
    assert unit['source_revision'] == 'revision-1'


def test_requested_missing_entry_reports_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ABSORB_API_KEY", "offline-test")
    monkeypatch.setattr(sys, "argv", ["absorb", "--repo", str(tmp_path), "--kind", "", "--only", "missing"])
    assert writer.main() == 1
    event = json.loads(capsys.readouterr().out.splitlines()[0])
    assert event["unit_id"] == "missing" and event["status"] == "failed"


@pytest.mark.parametrize("prefix", ["Here is the revised article:\n\n", "```markdown\n", ""])
def test_local_model_preambles_do_not_break_metadata(prefix):
    output = (prefix + '<!-- target: wiki/domain/review.md -->\n---\ntitle: Review\n'
              'type: domain\n---\n# Review\n\nThe code is LANTERN-742. '
              '[doc: sources/review.md@abcdef01]\n')
    if prefix.startswith("```"):
        output += '```'
    normalized = writer.normalize_article_output(output, "review", "head", ["sources/review.md@abcdef01"], "")
    assert normalized.startswith('---\ntitle: Review\n')
    assert normalized.count('\n---\n') == 1
    assert "Here is" not in normalized and "```" not in normalized
    assert "The code is LANTERN-742. [doc: sources/review.md@abcdef01]" in normalized
    assert "grades: {verified: 0, code: 0, doc: 1, conflict: 0, gap: 0}" in normalized


def test_metadata_envelope_does_not_invent_citations_or_grades():
    normalized = writer.normalize_article_output(
        "# Review\n\nAn unsupported assertion.", "review", "head", ["sources/review.md@abcdef01"], "")
    assert "An unsupported assertion." in normalized
    assert "[doc:" not in normalized


def test_evidence_rejects_invented_counts_and_accepts_verbatim_facts():
    source = "Checklist note 110. Mira approved LANTERN-742."
    with pytest.raises(ValueError, match="not present"):
        writer.verified_quotes('{"quotes":["There are 110 checklist notes."]}', source)
    assert writer.verified_quotes('Here is the JSON: {"quotes":["Mira approved LANTERN-742."]}', source) == ["Mira approved LANTERN-742."]


def test_writer_rejects_binary_garbage_before_a_model_request(monkeypatch, tmp_path):
    (tmp_path / "garbage.md").write_text("\ufffd\x00\x08" * 100)
    monkeypatch.setattr(writer, "version_of", lambda *_: "abcdef01")
    with pytest.raises(ValueError, match="not readable extracted text"):
        writer.gather(tmp_path, ["garbage.md"])


def test_writer_rechecks_policy_and_records_published_revision_not_rewritten_entry(monkeypatch, tmp_path, capsys):
    from server import sources, storage
    entries = tmp_path / 'raw' / 'entries'
    entries.mkdir(parents=True)
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    for uid in ('first', 'deferred'):
        (entries / f'2026-09-20_{uid}.md').write_text(f'---\nid: {uid}\nsha: revision-1\nsource_type: doc\n---\n')
    checks = []

    def eligible(project, ids, automatic=False):
        checks.append((project, ids, automatic))
        return ids if ids == ['first'] else []

    compiled = []
    def compile(_repo, _wiki, uid, entry_path, *_args, **_kwargs):
        compiled.append(uid)
        (wiki / 'first.md').write_text('---\ntitle: First\ntype: domain\nunit: first\n'
                                      'source_revision: revision-1\n---\n# First\n')
        # A later upload/discovery run must not change the published revision.
        entry_path.write_text(entry_path.read_text().replace('revision-1', 'revision-2'))
        return 'ok  -> wiki/first.md', {'in': 20, 'out': 10}

    monkeypatch.setattr(sources, 'eligible', eligible, raising=False)
    monkeypatch.setattr(writer, 'run_one', compile)
    monkeypatch.setattr(storage, 'push', lambda: 0)
    monkeypatch.setenv('ABSORB_API_KEY', 'offline-test')
    monkeypatch.setattr(sys, 'argv', ['absorb', '--repo', str(tmp_path), '--kind', '',
                                     '--project-id', 'personal', '--auto-only',
                                     '--only', 'first', '--only', 'deferred'])
    assert writer.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{"event"')]
    units = {e['unit_id']: e for e in events if e['event'] == 'unit_result'}
    assert compiled == ['first']
    assert checks == [('personal', ['first'], True), ('personal', ['deferred'], True)]
    assert units['first']['source_revision'] == 'revision-1'
    assert units['deferred']['status'] == 'deferred' and units['deferred']['tokens_in'] == 0
    assert read_completed(wiki / '_absorb_log.json') == {'first': 'revision-1'}


def test_local_json_envelope_preserves_model_citations_without_edit_commentary():
    claim = 'The review date is 15 October 2026. [doc: sources/review.md@abcdef01]'
    rendered = writer.render_article_json(json.dumps({
        'title': 'Review', 'type': 'domain',
        'sections': [{'heading': 'Decision', 'paragraphs': [claim]}],
        'notes': 'I fixed the validation failure.'}))
    assert claim in rendered
    assert 'I fixed' not in rendered
    # An uncited paragraph is never mechanically given a citation.
    uncited = writer.render_article_json(json.dumps({
        'title': 'Review', 'type': 'domain',
        'sections': [{'heading': '', 'paragraphs': ['An unsupported claim.']}]}))
    assert '[doc:' not in uncited
    with pytest.raises(ValueError, match='paragraphs'):
        writer.render_article_json('{"title":"Review","type":"domain","sections":[{"heading":"Decision"}]}')


def test_local_update_uses_current_evidence_and_retries_false_code_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(writer, 'PROTOCOL', 'ollama')
    source = tmp_path / 'sources/review.md'
    source.parent.mkdir()
    source.write_text('Synthetic fixture. Revision REVISION-TWO was approved by Mira on 15 October 2026.')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()[:8]
    entry = tmp_path / 'entry.md'
    entry.write_text('---\nid: review\nsource_type: doc\npath: sources/review.md\nsha: revision-two\n---\n')
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    article = wiki / 'review.md'
    article.write_text('---\ntitle: Review\ntype: domain\nunit: review\ncreated: 2026-09-01\n---\n'
                       '# Review\nOld detail [doc: sources/review.md@11111111]\n')
    requests = []
    def local_model(messages, *_args, **_kwargs):
        requests.append([dict(m) for m in messages])
        grade = 'verified' if len(requests) == 1 else 'doc'
        output = {'title': 'Review', 'type': 'domain', 'sections': [{'heading': 'Revision',
                  'paragraphs': [f'This synthetic fixture records REVISION-TWO, approved by Mira '
                                 f'on 15 October 2026. [{grade}: sources/review.md@{sha}]']}]}
        return json.dumps(output), {'prompt_tokens': 10, 'completion_tokens': 20}
    monkeypatch.setattr(writer, 'groq', local_model)
    result, usage = writer.run_one(tmp_path, wiki, 'review', entry, 'offline', 'test', 2, False)
    assert result.startswith('ok') and 'attempt 2' in result
    assert usage == {'in': 20, 'out': 40}
    assert requests[0][0]['content'] == writer.LOCAL_WRITER_SYSTEM
    assert '@11111111' not in requests[0][1]['content']
    assert 'unsupported grade' in requests[1][-1]['content']
    saved = article.read_text()
    assert 'source_revision: revision-two' in saved and 'created: 2026-09-01' in saved
    assert 'REVISION-TWO' in saved and f'[doc: sources/review.md@{sha}]' in saved
    assert '[verified:' not in saved
    assert writer.allowed_source_grades(['README.md@abcdef01']) == {'doc', 'gap', 'conflict'}
    assert writer.allowed_source_grades(['src/main.py@abcdef01']) is None
