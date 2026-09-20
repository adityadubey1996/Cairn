from pathlib import Path

from server.application import evidence


def test_hydration_recovers_a_detail_omitted_from_summary(monkeypatch, tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('Project Lantern selected Cedar. Review date: 15 October 2026.')
    seen = []
    def resolve(path, version):
        seen.append((path, version))
        return source
    monkeypatch.setattr(evidence.source_history, 'resolve', resolve)
    original = {'root': Path('/wiki'), 'rel': 'a.md', 'title': 'A',
                'text': 'Selected Cedar [doc: sources/upload/a.md@abcdef12].'}
    result = evidence.hydrate([original], 'What is the review date?')
    assert '15 October 2026' in result[0]['text']
    assert '15 October 2026' not in original['text']
    assert seen == [('sources/upload/a.md', 'abcdef12')]


def test_passage_search_can_recover_the_end_of_a_long_source():
    text = ('Unrelated operational notes.\n' * 4000) + '\nFinal review date: 15 October 2026.\n'
    chosen = evidence.passages(text, 'Final review date', 4800)
    assert '15 October 2026' in chosen
    assert len(chosen) < 5000


def test_missing_historical_revision_does_not_substitute_current_source(monkeypatch):
    monkeypatch.setattr(evidence.source_history, 'resolve', lambda *_: None)
    article = {'text': 'Old decision [doc: sources/upload/a.md@abcdef12]'}
    assert evidence.hydrate([article], 'Decision?') == [article]


def test_passage_separators_respect_the_evidence_budget():
    text = ('x' * evidence.PASSAGE_CHARS) * 4
    assert len(evidence.passages(text, 'x', 4800)) <= 4800
    assert evidence.passages(text, 'x', 0) == ''


def test_hydration_keeps_full_revision_and_reads_structured_text(tmp_path, monkeypatch):
    from server import config, source_history
    import hashlib
    monkeypatch.setattr(config, 'SOURCES_DIR', tmp_path / 'sources')
    monkeypatch.setattr(config, 'VAR', tmp_path / 'var')
    source = config.SOURCES_DIR / 'report.csv'
    source.parent.mkdir()
    source.write_text('project,review_date\nLantern,2026-10-15\n')
    revision = hashlib.sha256(source.read_bytes()).hexdigest()
    article = {'text': f'The project is Lantern [doc: sources/report.csv@{revision}]'}
    assert '2026-10-15' in evidence.hydrate([article], 'review date')[0]['text']
    source_history.preserve('sources/report.csv')
    source.write_text('project,review_date\nLantern,2027-01-01\n')
    hydrated = evidence.hydrate([article], 'review date')[0]['text']
    assert '2026-10-15' in hydrated and '2027-01-01' not in hydrated
