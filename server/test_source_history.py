from server import config, source_history
import hashlib
from concurrent.futures import ThreadPoolExecutor


def test_old_citation_still_reads_its_original_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SOURCES_DIR', tmp_path / 'sources')
    monkeypatch.setattr(config, 'VAR', tmp_path / 'var')
    path = config.SOURCES_DIR / 'upload' / 'manual.md'
    path.parent.mkdir(parents=True)
    path.write_text('The first version.')
    from pipeline.source_files import content_version
    revision = content_version(path)
    source_history.preserve('sources/upload/manual.md')
    path.write_text('The revised version.')
    source_history.preserve('sources/upload/manual.md')
    assert source_history.resolve('sources/upload/manual.md', revision).read_text() == 'The first version.'
    assert source_history.resolve('sources/upload/manual.md').read_text() == 'The revised version.'


def test_resolved_citation_is_immutable_during_a_concurrent_source_update(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SOURCES_DIR', tmp_path / 'sources')
    monkeypatch.setattr(config, 'VAR', tmp_path / 'var')
    source = config.SOURCES_DIR / 'document.md'
    source.parent.mkdir()
    source.write_text('Original cited evidence.')
    version = hashlib.sha256(source.read_bytes()).hexdigest()
    resolved = source_history.resolve('sources/document.md', version)
    source.write_text('A newer and different decision.')
    assert resolved != source
    assert resolved.read_text() == 'Original cited evidence.'
    assert source_history.resolve('sources/document.md', version).read_text() == 'Original cited evidence.'


def test_concurrent_snapshot_publication_never_loses_a_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'SOURCES_DIR', tmp_path / 'sources')
    monkeypatch.setattr(config, 'VAR', tmp_path / 'var')
    data = b'A shared immutable source revision.'
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: source_history._store('sources/doc.md', data), range(32)))
    assert len(set(paths)) == 1
    assert paths[0].read_bytes() == data
    assert list(paths[0].parent.iterdir()) == [paths[0]]
