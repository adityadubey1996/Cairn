from pathlib import Path

from server.application.framing import MAX_CITATION_REMINDERS, system_prompt
from server.wikilib import CITE


def article(text):
    return {'root': Path('/kb/requests/wiki'), 'rel': 'domain/requests.md',
            'title': 'Requests', 'text': text}


def reminder(prompt):
    return prompt.split('===== END OF EVIDENCE — ANSWER REQUIREMENTS =====', 1)[1]


def test_tail_reminder_uses_only_actual_citation_tokens():
    context = [article('Python 3.10+ [doc: README.md@4e5c4455].\n'
                       'Install with pip [doc: README.md@4e5c4455].\n'
                       'See packaging [code: pyproject.toml@abcdef01].')]
    tail = reminder(system_prompt(context))
    assert set(CITE.findall(tail)) == {('README.md', '4e5c4455'), ('pyproject.toml', 'abcdef01')}
    assert '[supported statement] [README.md@4e5c4455]' in tail
    assert 'only when its evidence supports that claim' in tail
    assert 'built_from_commit' in tail


def test_citation_reminder_is_bounded_and_preserves_exact_tokens():
    source = '\n'.join(f'[doc: file-{n}.md@{n:064x}]' for n in range(40))
    tail = reminder(system_prompt([article(source)]))
    expected = {(f'file-{n}.md', f'{n:064x}') for n in range(MAX_CITATION_REMINDERS)}
    assert set(CITE.findall(tail)) == expected
    assert len(CITE.findall(tail)) == MAX_CITATION_REMINDERS + 1  # first-token syntax example


def test_empty_context_has_no_invented_citation_example():
    tail = reminder(system_prompt([]))
    assert not CITE.findall(tail)
    assert 'No source citation tokens are available' in tail
    assert 'sufficient cited evidence' in tail
    assert 'syntax example' not in tail
