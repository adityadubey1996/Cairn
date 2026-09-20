"""Completed source revisions, independent of how often discovery runs."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def read_completed(path: Path) -> dict[str, str | None]:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if isinstance(value, list):
        # Old ledgers did not record revisions. A one-time refresh is necessary
        # to establish what was actually compiled, rather than guess from ingest.
        return {str(uid): None for uid in value}
    if isinstance(value, dict):
        return {str(uid): sha if isinstance(sha, str) else None
                for uid, sha in value.items()}
    return {}


def write_completed(path: Path, revisions: dict[str, str]) -> None:
    completed = read_completed(path)
    completed.update(revisions)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                                     prefix=".absorb-log-", delete=False) as f:
        json.dump(completed, f, sort_keys=True, indent=1)
        f.write("\n")
        name = f.name
    os.replace(name, path)
