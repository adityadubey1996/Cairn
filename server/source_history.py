"""Immutable local source revisions for citations; S3 is optional backing."""
from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path

from . import config
from pipeline.source_files import source_path


def _destination(path: str) -> Path:
    source_path(config.ROOT, path)  # validate the logical path first
    key = hashlib.sha256(path.encode()).hexdigest()
    return config.VAR / 'source-revisions' / key


def _store(path: str, data: bytes) -> Path:
    """Publish an immutable snapshot of these bytes, including concurrent readers."""
    revision = hashlib.sha256(data).hexdigest()
    dest = _destination(path) / revision
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            f.write(data)
        try:
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
    return dest


def preserve(path: str) -> None:
    original = source_path(config.ROOT, path)
    if original.is_file():
        _store(path, original.read_bytes())


def _matches_data(data: bytes, version: str) -> bool:
    # SHA-1/MD5 support citations emitted before local SHA-256 revisions existed.
    return any(h(data).hexdigest().startswith(version) for h in
               (hashlib.sha256, hashlib.sha1, hashlib.md5))


def resolve(path: str, version: str = '') -> Path | None:
    if version and not re.fullmatch(r'[0-9a-f]{7,64}', version):
        raise ValueError('source revision must be 7-64 hex characters')
    current = source_path(config.ROOT, path)
    if current.is_file():
        if not version:
            return current
        data = current.read_bytes()
        if _matches_data(data, version):
            # A caller reads after resolve returns. Returning the live source
            # would let an upload replace its bytes under the old citation.
            return _store(path, data)
    if version:
        directory = _destination(path)
        if directory.is_dir():
            matches = [p for p in directory.iterdir() if p.is_file() and
                       re.fullmatch(r'[0-9a-f]{64}', p.name) and
                       _matches_data(p.read_bytes(), version)]
            if len(matches) == 1:
                return matches[0]
    return None
