#!/usr/bin/env python3
"""Self-check for the repo connector's trust boundary.  Run: python3 -m server.test_repos

A browser-supplied URL ends up in a `git` argv. These pin the validation, the
sandbox, and the argument-injection defences — the parts where being wrong is a
vulnerability rather than a bug. No database and no network: everything here is
pure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import repos  # noqa: E402


def rejects(fn, value, why=""):
    try:
        fn(value)
    except repos.Invalid:
        return
    raise AssertionError(f"accepted {value!r} {why}")


def test_accepts_the_canonical_shapes():
    for url, expect in (
        ("https://github.com/acme/widgets", ("acme", "widgets")),
        ("https://github.com/acme/widgets.git", ("acme", "widgets")),
        ("https://github.com/acme/widgets/", ("acme", "widgets")),
        ("  https://github.com/a/b  ", ("a", "b")),
        ("https://github.com/a/b.c_d-e", ("a", "b.c_d-e")),
    ):
        assert repos.parse_url(url) == expect, url


def test_rejects_every_other_scheme_and_host():
    for url in (
        "http://github.com/a/b",              # plaintext
        "ssh://git@github.com/a/b",
        "git://github.com/a/b",
        "file:///etc/passwd",
        "https://gitlab.com/a/b",
        "https://evil.com/github.com/a/b",    # host confusion
        "https://github.com.evil.com/a/b",    # suffix confusion
        "https://user:pass@github.com/a/b",   # credentials in the URL
        "https://github.com:22/a/b",          # port
        "https://github.com/a/b?x=1",         # query
        "https://github.com/a/b#frag",
        "https://github.com/a",               # too shallow
        "https://github.com/a/b/c",           # too deep
        "https://github.com//b",
        "", "   ", "github.com/a/b", "a/b",
    ):
        rejects(repos.parse_url, url)


def test_rejects_traversal_and_argument_injection_in_the_path():
    for url in (
        "https://github.com/../etc/passwd",
        "https://github.com/a/..",
        "https://github.com/a/.",
        "https://github.com/a/-rf",            # leading dash: reads as a flag
        "https://github.com/-a/b",
        "https://github.com/a/b%2F..%2Fc",
    ):
        rejects(repos.parse_url, url)


def test_branch_rules():
    for good in ("dev", "main", "feature/kb-redesign", "release/1.2.x", "a_b.c-d"):
        assert repos.check_branch(good) == good
    for bad in ("--upload-pack=/bin/sh",   # argument injection
                "-x", "a..b", "a//b", "a.lock", "feat/", "", "  ",
                "a b", "a;rm -rf /", "a$(id)", "a\nb", "a" * 256):
        rejects(repos.check_branch, bad)


def test_sha_rules():
    assert repos.check_sha("A1B2C3D") == "a1b2c3d"
    assert repos.check_sha("f29c9e9df7f685228082fff81c4a2fa0cc601e00").startswith("f29c")
    for bad in ("", "abc", "z" * 40, "a" * 41, "--all", "HEAD", "a1b2c3d;ls"):
        rejects(repos.check_sha, bad)


def test_clone_url_is_rebuilt_not_echoed():
    """The URL git receives is assembled from validated parts. Whatever the user
    typed is never passed through.

    token_for() is stubbed because it reads brain_settings: without this the one
    test in this file would need Postgres, contradicting the no-database promise
    above. The no-token branch is the one under test anyway."""
    orig = repos.token_for
    repos.token_for = lambda _repo_id: ""
    try:
        assert repos.clone_url("acme", "widgets") == \
            "https://github.com/acme/widgets.git"
    finally:
        repos.token_for = orig


def test_paths_stay_inside_the_sandbox():
    base = repos.config.REPO_CLONE_DIR.resolve()
    assert repos.clone_dir("owner", "repo").is_relative_to(base)
    # parse_url already rejects these, so this is the second lock: even if a
    # traversal reached the path builder, it cannot escape.
    for owner, name in (("..", "x"), ("a", ".."), ("../..", "x"), ("a", "../../etc")):
        try:
            p = repos._sandboxed(repos.config.REPO_CLONE_DIR, owner, name)
        except repos.Invalid:
            continue
        assert p.is_relative_to(base), f"escaped: {owner}/{name} -> {p}"


def test_env_is_scrubbed_of_credentials_and_prompts():
    import os
    os.environ["GITHUB_TOKEN"] = "should-not-survive"
    try:
        e = repos._env()
        assert "GITHUB_TOKEN" not in e, "an ambient token would leak into git"
        assert e["GIT_TERMINAL_PROMPT"] == "0", "git could block on a prompt"
        assert e["GIT_CONFIG_NOSYSTEM"] == "1"
    finally:
        os.environ.pop("GITHUB_TOKEN", None)


def test_no_shell_true_anywhere_in_the_feature():
    """Lint rule as a test: a shell string is how a validated URL becomes an
    injection anyway."""
    for name in ("repos.py", "routers/repos.py"):
        src = (Path(__file__).parent / name).read_text()
        assert "shell=True" not in src, f"{name} uses shell=True"
        assert "os.system" not in src, f"{name} uses os.system"


def test_step_names_are_closed():
    assert set(repos.STEPS) == {"clone", "graph", "ingest", "absorb"}
    for bad in ("rm", "clone;rm", "", "CLONE"):
        try:
            repos.run_step("x/y", bad)
        except repos.Invalid:
            continue
        except Exception:
            raise AssertionError(f"unknown step {bad!r} got past the guard")
        raise AssertionError(f"unknown step {bad!r} accepted")


def test_job_registry_rejects_a_second_job():
    repos._claim("a/b", "clone")
    try:
        repos._claim("a/b", "absorb")
        raise AssertionError("two jobs claimed the same repo")
    except repos.Busy:
        pass
    finally:
        repos._release("a/b")
    repos._claim("a/b", "absorb")   # released, so this must now succeed
    repos._release("a/b")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"  ok  {name}")
    print("repos: all checks passed")
