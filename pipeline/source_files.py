"""Resolve and fingerprint the source bytes used by the writer and reader.

Connector paths are logical ``sources/...`` names. Their physical directory
can be a mounted volume outside the checkout; no object store is required.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def source_path(repo: Path, path: str) -> Path:
    repo = Path(repo).resolve()
    rel = Path(path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("source path must stay inside its source root")
    if not path.startswith("sources/"):
        candidate = (repo / rel).resolve()
        if not candidate.is_relative_to(repo):
            raise ValueError("source symlink leaves its source root")
        return candidate
    # A standalone repository can carry its own source tree. The application's
    # configured volume owns paths only when operating on the application root.
    from server import config
    root = (config.SOURCES_DIR if repo == config.ROOT.resolve()
            else repo / "sources")
    root = Path(root).resolve()
    candidate = (root / Path(*rel.parts[1:])).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("source symlink leaves its source root")
    return candidate


def content_version(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()
