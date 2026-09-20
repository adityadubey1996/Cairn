"""Repository citations use source provenance, not the wiki storage checkout."""
import json
from pathlib import Path

import pytest

from server import config, corpus, gitmeta, index, repos, sources
from server.application import synthesize
from server.application.framing import system_prompt

COMPILED = 'dae7ef63b4df6eded86637f251fc4e3a06c3b479'
NEW_HEAD = 'b' * 40
HOST_HEAD = '4f94f89459b7b45a7dd4973abed3bd978e012ddd'


@pytest.fixture
def generated_wiki(monkeypatch, tmp_path):
    host = tmp_path / 'host-app'
    (host / '.git').mkdir(parents=True)
    root = host / 'var/repo-wikis/psf/requests/wiki'
    root.mkdir(parents=True)
    row = {'name': 'requests', 'url': 'https://github.com/psf/requests',
           'wiki_root': str(root), 'head_sha': NEW_HEAD, 'pinned_sha': None,
           'clone_path': None}
    monkeypatch.setattr(repos, 'list_repos', lambda: [row])
    monkeypatch.setattr(gitmeta, 'github_slug_and_ref',
                        lambda *_: pytest.fail('generated wiki must not inspect its storage ancestor remote'))
    text = (f'---\ntitle: Requests\ntype: domain\nbuilt_from_commit: {COMPILED}\n'
            'sources: ["README.md@4e5c4455"]\nrelated: []\n---\n# Requests\n\n'
            'Requests supports Python 3.10+. [doc: README.md@4e5c4455]\n')
    article = root / 'domain/requests.md'
    article.parent.mkdir()
    article.write_text(text)
    return root, row, text


def test_generated_repo_citation_uses_article_commit_not_host_or_new_repo_head(generated_wiki):
    root, _, text = generated_wiki
    context = [{'root': root, 'rel': 'domain/requests.md', 'title': 'Requests', 'text': text}]
    cited = synthesize._citations('Python 3.10+ [README.md@4e5c4455]', context)
    assert cited == [{'path': 'README.md', 'sha': '4e5c4455', 'repo': 'requests',
                      'github': 'psf/requests', 'ref': COMPILED,
                      'href': f'https://github.com/psf/requests/blob/{COMPILED}/README.md'}]
    assert synthesize._trust(context)['heads'] == {'psf/requests': COMPILED[:8]}
    assert corpus._head(root) == NEW_HEAD


def test_local_source_wiki_does_not_inherit_host_repository_commit(monkeypatch, tmp_path):
    (tmp_path / '.git').mkdir()
    root = tmp_path / 'var/local/wiki'
    root.mkdir(parents=True)
    monkeypatch.setattr(repos, 'list_repos', lambda: [])
    monkeypatch.setattr(corpus.subprocess, 'run', lambda *_a, **_k: pytest.fail('must not discover ancestor Git'))
    meta = corpus.provenance(root, f'---\nbuilt_from_commit: {HOST_HEAD}\n---\n# Local fixture\n')
    assert meta == {'repo': 'local', 'head': 'local'}


def test_article_reader_uses_the_same_pinned_source_provenance(generated_wiki, monkeypatch):
    from server.routers import wiki
    root, _, _ = generated_wiki
    monkeypatch.setattr(corpus, 'roots', lambda *_: [root])
    monkeypatch.setattr(sources, 'get_by_paths', lambda *_: {})
    result = wiki.article('requests', 'domain/requests.md', 'personal', 'test@example.com')
    assert result['github'] == 'psf/requests' and result['ref'] == COMPILED
    assert result['head'] == COMPILED[:8]
    assert result['citations'][0]['url'] == f'https://github.com/psf/requests/blob/{COMPILED}/README.md'


def test_sync_repairs_index_provenance_even_when_article_fingerprint_is_unchanged(generated_wiki, monkeypatch, tmp_path):
    root, _, _ = generated_wiki
    var = tmp_path / 'runtime'
    var.mkdir()
    state = var / 'sync_state.json'
    state.write_text(json.dumps({str(root): {'fingerprint': corpus.fingerprint(root), 'head': HOST_HEAD}}))
    monkeypatch.setattr(config, 'VAR', var)
    monkeypatch.setattr(corpus, 'STATE', state)
    monkeypatch.setattr(corpus, 'roots', lambda: [root])
    index.db_path(root).touch()
    builds = []
    monkeypatch.setattr(index, 'build', lambda wiki, head: builds.append((wiki, head)) or 1)
    assert corpus.sync()[0]['status'] == 'reindexed'
    assert builds == [(root, NEW_HEAD)]
    assert json.loads(state.read_text())[str(root)]['head'] == NEW_HEAD


def test_context_headers_do_not_present_unroutable_article_urls(generated_wiki):
    root, _, text = generated_wiki
    prompt = system_prompt([{'root': root, 'rel': 'domain/requests.md', 'title': 'Requests', 'text': text}])
    assert 'requests/wiki/domain/requests.md' not in prompt
    assert 'plain titles' in prompt and 'README.md@4e5c4455' in prompt
