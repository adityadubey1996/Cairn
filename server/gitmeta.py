"""Resolve local wiki roots to public GitHub blob URLs.

Local clone folders are not always the GitHub repo name — e.g. a folder named
`payments-service/` might clone `acme-corp/payments`. Citation links must use
the remote slug, not the folder name.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

# git@host:owner/repo.git  OR  https://host/owner/repo(.git)
_REMOTE_SLUG = re.compile(
    r"(?:git@[^:]+:|https?://[^/]+/)(?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            text=True, stderr=subprocess.DEVNULL,
        ).strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


@lru_cache(maxsize=32)
def github_slug(repo_dir: Path) -> str | None:
    """Return owner/repo from origin, or None if unresolvable."""
    url = _git(Path(repo_dir).resolve(), "remote", "get-url", "origin")
    if not url:
        return None
    m = _REMOTE_SLUG.search(url)
    return m.group("slug") if m else None


@lru_cache(maxsize=32)
def github_fallback_ref(repo_dir: Path) -> str:
    """Branch to use when no indexed commit is available: the remote's real
    default branch first, then the common main/master names, then whatever
    is checked out locally."""
    repo = Path(repo_dir).resolve()
    candidates: list[str] = []
    head = _git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        candidates.append(head.removeprefix("origin/"))
    for candidate in ("main", "master"):
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if _git(repo, "rev-parse", "--verify", f"refs/remotes/origin/{candidate}"):
            return candidate
    ref = _git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "main"
    return "main" if ref == "HEAD" else ref


def github_slug_and_ref(repo_dir: Path, preferred_ref: str | None = None
                        ) -> tuple[str, str] | None:
    """Return (owner/repo, ref). preferred_ref wins when provided (e.g. index head)."""
    slug = github_slug(repo_dir)
    if not slug:
        return None
    ref = preferred_ref if preferred_ref and preferred_ref not in ("?", "HEAD") \
        else github_fallback_ref(repo_dir)
    return slug, ref


def github_blob_url(repo_dir: Path, path: str,
                    preferred_ref: str | None = None) -> str | None:
    """https://github.com/OWNER/REPO/blob/REF/path — None if unresolvable."""
    resolved = github_slug_and_ref(repo_dir, preferred_ref)
    if not resolved:
        return None
    slug, ref = resolved
    return f"https://github.com/{slug}/blob/{ref}/{path.lstrip('/')}"
