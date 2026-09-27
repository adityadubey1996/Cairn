#!/usr/bin/env python3
"""Self-check for the repo connector's trust boundary.  Run: python3 -m server.test_repos

A browser-supplied URL ends up in a `git` argv. These pin the validation, the
sandbox, and the argument-injection defences — the parts where being wrong is a
vulnerability rather than a bug. No database and no network: everything here is
pure.

`repos._resolve` is replaced at import. It is the one seam that would otherwise
reach DNS, and a test that silently needs the internet is a test that passes on
a laptop and fails in CI for a reason nobody can reproduce.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import repos  # noqa: E402

PUBLIC_IP = "140.82.121.4"
PRIVATE_HOST = "git.internal.example"

FAKE_DNS = {
    "github.com": [PUBLIC_IP],
    "gitlab.com": [PUBLIC_IP],
    "bitbucket.org": [PUBLIC_IP],
    "codeberg.org": [PUBLIC_IP],
    "evil.com": [PUBLIC_IP],
    "github.com.evil.com": [PUBLIC_IP],
    PRIVATE_HOST: ["10.0.0.5"],
    "loopback.example": ["127.0.0.1"],
    "linklocal.example": ["169.254.169.254"],   # cloud metadata
    "ipv6private.example": ["fd00::1"],
    "split.example": [PUBLIC_IP, "192.168.1.10"],
}


def _fake_resolve(host: str) -> list[str]:
    try:
        return FAKE_DNS[host]
    except KeyError:
        raise OSError(f"name does not resolve: {host}")


repos._resolve = _fake_resolve


@contextlib.contextmanager
def allowing(*hosts: str):
    """Put a host on the allowlist for the duration of one test."""
    orig = repos.config.REPO_ALLOWED_HOSTS
    repos.config.REPO_ALLOWED_HOSTS = [*orig, *hosts]
    try:
        yield
    finally:
        repos.config.REPO_ALLOWED_HOSTS = orig


class _EmptyDB:
    """brain_settings with nothing in it, so token_for needs no Postgres."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


@contextlib.contextmanager
def no_db(github_token: str = ""):
    orig_connect, orig_token = repos.connect, repos.config.GITHUB_TOKEN
    repos.connect = lambda: _EmptyDB()
    repos.config.GITHUB_TOKEN = github_token
    try:
        yield
    finally:
        repos.connect, repos.config.GITHUB_TOKEN = orig_connect, orig_token


def rejects(fn, value, why=""):
    try:
        fn(value)
    except repos.Invalid:
        return
    raise AssertionError(f"accepted {value!r} {why}")


def test_accepts_the_canonical_shapes():
    for url, expect in (
        ("https://github.com/acme/widgets", ("github.com", "acme/widgets")),
        ("https://github.com/acme/widgets.git", ("github.com", "acme/widgets")),
        ("https://github.com/acme/widgets/", ("github.com", "acme/widgets")),
        ("  https://github.com/a/b  ", ("github.com", "a/b")),
        ("https://github.com/a/b.c_d-e", ("github.com", "a/b.c_d-e")),
        ("https://gitlab.com/a/b", ("gitlab.com", "a/b")),
        ("https://bitbucket.org/team/repo", ("bitbucket.org", "team/repo")),
        ("https://codeberg.org/user/thing", ("codeberg.org", "user/thing")),
        # GitLab is the whole reason `owner/name` had to go.
        ("https://gitlab.com/group/subgroup/project",
         ("gitlab.com", "group/subgroup/project")),
        ("https://gitlab.com/a/b/c/d/e.git", ("gitlab.com", "a/b/c/d/e")),
    ):
        assert repos.parse_url(url) == expect, url


def test_rejects_a_host_that_is_not_on_the_allowlist():
    """The allowlist is the boundary. An unlisted host is not a typo to be
    helpfully corrected — it is a request to open a connection somewhere."""
    for url in (
        "https://evil.com/a/b",
        "https://git.sr.ht/~user/repo",
        "https://evil.com/github.com/a/b",      # host confusion
        "https://github.com.evil.com/a/b",      # suffix confusion
        "https://GITHUB.COM.evil.com/a/b",
        "https://127.0.0.1/a/b",                # bare address, never listed
        "https://[::1]/a/b",
        "https://192.168.1.1/a/b",
    ):
        rejects(repos.parse_url, url)
    # …and it really is the list doing the work, not a hardcoded set of four.
    with allowing("git.example.com"):
        FAKE_DNS["git.example.com"] = [PUBLIC_IP]
        try:
            assert repos.parse_url("https://git.example.com/a/b") == \
                ("git.example.com", "a/b")
        finally:
            FAKE_DNS.pop("git.example.com")


def test_rejects_credentials_embedded_in_the_url():
    """`user:pass@host` is a credential we would hand to whatever the host
    turns out to be, and it is also how the real host gets hidden: everything
    before the `@` is userinfo, so the authority is the part people skim past."""
    for url in (
        "https://user:pass@github.com/a/b",
        "https://user@github.com/a/b",
        "https://token@gitlab.com/a/b",
        "https://x-access-token:ghp_secret@github.com/a/b",
        "https://github.com@evil.com/a/b",      # the real host is evil.com
        "https://user:pass@evil.com/a/b",
        "https://:@github.com/a/b",
    ):
        rejects(repos.parse_url, url)


def test_rejects_a_host_that_resolves_to_a_private_address():
    """The allowlist says which names are permitted; this says the name must
    not be aimed inward. An operator who adds a self-hosted host still cannot
    be talked into cloning from 169.254.169.254."""
    for host in (PRIVATE_HOST, "loopback.example", "linklocal.example",
                 "ipv6private.example"):
        with allowing(host):
            rejects(repos.parse_url, f"https://{host}/a/b",
                    "resolves to a non-public address")
    # One public answer does not launder the private one: git picks whichever
    # address it likes, so any private result is disqualifying.
    with allowing("split.example"):
        rejects(repos.parse_url, "https://split.example/a/b",
                "one of its addresses is private")
    # A name that does not resolve at all fails closed rather than open.
    with allowing("nxdomain.example"):
        rejects(repos.parse_url, "https://nxdomain.example/a/b")


def test_rejects_every_other_scheme_and_shape():
    for url in (
        "http://github.com/a/b",              # plaintext
        "ssh://git@github.com/a/b",
        "git://github.com/a/b",
        "file:///etc/passwd",
        "https://github.com:22/a/b",          # port
        "https://github.com:443/a/b",         # even the default port
        "https://github.com/a/b?x=1",         # query
        "https://github.com/a/b#frag",
        "https://github.com/a",               # too shallow: not a repo
        "https://github.com//b",
        "https://github.com/a//b",            # empty interior segment
        "https://github.com/a/b/c",           # github is owner/name, not deeper
        "https://github.com/acme/widgets/tree/main",   # a page, not a repo
        "https://codeberg.org/a/b/c",
        "https://gitlab.com/" + "/".join("s" * 11),    # past the depth cap
        "https://github.com/a/b\\c",          # backslash
        "https://github.com/a b/c",           # whitespace inside
        "", "   ", "github.com/a/b", "a/b", "https://", "https://github.com",
    ):
        rejects(repos.parse_url, url)


def test_rejects_traversal_and_argument_injection_in_every_segment():
    """Not just the last one. A nested path multiplies the number of places a
    `..` or a leading `-` can hide, and each is a directory name we create and
    a string git is handed."""
    for url in (
        "https://github.com/../etc/passwd",
        "https://github.com/a/..",
        "https://github.com/a/.",
        "https://github.com/./b",
        "https://github.com/a/-rf",            # leading dash: reads as a flag
        "https://github.com/-a/b",
        "https://github.com/a/b%2F..%2Fc",
        "https://gitlab.com/group/../../etc/passwd",
        "https://gitlab.com/group/../project",
        "https://gitlab.com/group/-rf/project",     # middle segment
        "https://gitlab.com/group/./project",
        "https://gitlab.com/-group/sub/project",    # first segment
        "https://gitlab.com/group/sub/-project",    # last segment
        "https://gitlab.com/group/.hidden/project",
        "https://gitlab.com/group/sub/..",
        "https://gitlab.com/a/b/%2e%2e/c",
    ):
        rejects(repos.parse_url, url)
    for seg in ("..", ".", "-rf", "", "a/b", "a\\b", "a b", ".git", "a" * 101,
                "-", "a;rm -rf /", "a$(id)", "a\nb"):
        rejects(repos.check_segment, seg)


def test_a_nested_gitlab_path_round_trips():
    """The deliverable case: a group/subgroup/project URL has to survive all
    the way to a clone URL and a directory, without the `owner/name` pair ever
    being able to express it."""
    host, path = repos.parse_url("https://gitlab.com/group/subgroup/project")
    assert (host, path) == ("gitlab.com", "group/subgroup/project")

    key = repos.repo_key(host, path)
    assert key == "gitlab.com/group/subgroup/project", \
        "a non-github id carries its host, or it collides with github's a/b"
    owner, name = repos.owner_and_name(key)
    assert (owner, name) == ("gitlab.com/group/subgroup", "project")

    with no_db():
        assert repos.clone_url(host, path) == \
            "https://gitlab.com/group/subgroup/project.git"

    clone = repos.clone_dir(owner, name)
    wiki = repos.wiki_dir(owner, name)
    base = repos.config.REPO_CLONE_DIR.resolve()
    assert clone == base / "gitlab.com/group/subgroup/project", clone
    assert clone.is_relative_to(base)
    assert wiki == (repos.config.REPO_WIKI_DIR.resolve()
                    / "gitlab.com/group/subgroup/project" / "wiki"), wiki

    # Two projects with the same leaf under different subgroups must not share
    # a directory — the whole path is the identity, not the last segment.
    other = repos.owner_and_name(
        repos.repo_key(*repos.parse_url("https://gitlab.com/group/other/project")))
    assert repos.clone_dir(*other) != clone


def test_an_existing_github_repo_keeps_its_id_and_its_directory():
    """The migration test. Rows written before any of this existed hold
    id=`owner/name` and a clone under var/clones/<owner>/<name>; if the new
    shape moves either one, every tracked repo is orphaned."""
    host, path = repos.parse_url("https://github.com/Acme/Widgets")
    key = repos.repo_key(host, path)
    assert key == "Acme/Widgets", "github must stay unprefixed"
    assert key.lower() == "acme/widgets", "the id is unchanged"
    assert repos.owner_and_name(key) == ("Acme", "Widgets"), \
        "case is preserved, because the clone directory already uses it"
    assert repos.clone_dir("Acme", "Widgets") == \
        repos.config.REPO_CLONE_DIR.resolve() / "Acme" / "Widgets"

    # What step_clone rebuilds for a legacy row: the url column is the only
    # thing it trusts, and it must still produce the same remote.
    with no_db():
        legacy_url = "https://github.com/acme/widgets"
        assert repos.clone_url(*repos.parse_url(legacy_url)) == \
            "https://github.com/acme/widgets.git"


def test_clone_url_is_rebuilt_not_echoed():
    """The URL git receives is assembled from validated parts. Whatever the
    user typed is never passed through."""
    with no_db():
        assert repos.clone_url("github.com", "acme/widgets") == \
            "https://github.com/acme/widgets.git"
        assert repos.clone_url("gitlab.com", "a/b") == "https://gitlab.com/a/b.git"


def test_each_host_gets_the_token_username_it_expects():
    """A token is useless at the wrong username, and the resulting 401 is
    unreadable because _redact has already removed the interesting part."""
    orig = repos.token_for
    repos.token_for = lambda _id, _host="github.com": "s3cret"
    try:
        for host, path, expect in (
            ("github.com", "a/b",
             "https://x-access-token:s3cret@github.com/a/b.git"),
            ("gitlab.com", "group/sub/proj",
             "https://oauth2:s3cret@gitlab.com/group/sub/proj.git"),
            ("bitbucket.org", "team/repo",
             "https://x-token-auth:s3cret@bitbucket.org/team/repo.git"),
            ("codeberg.org", "u/r", "https://git:s3cret@codeberg.org/u/r.git"),
        ):
            assert repos.clone_url(host, path) == expect, host
    finally:
        repos.token_for = orig


def test_the_ambient_github_token_is_never_sent_to_another_host():
    """GITHUB_TOKEN is a fallback for github.com and nowhere else. Falling back
    to it for a gitlab.com clone would hand a GitHub credential to GitLab."""
    with no_db(github_token="ghp_ambient"):
        assert repos.token_for("a/b", "github.com") == "ghp_ambient"
        for host in ("gitlab.com", "bitbucket.org", "codeberg.org"):
            assert repos.token_for("x/y", host) == "", host
        assert "ghp_ambient" not in repos.clone_url("gitlab.com", "a/b")
        assert "ghp_ambient" in repos.clone_url("github.com", "a/b")


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


def test_paths_stay_inside_the_sandbox():
    base = repos.config.REPO_CLONE_DIR.resolve()
    assert repos.clone_dir("owner", "repo").is_relative_to(base)
    # parse_url already rejects these, so this is the second lock: even if a
    # traversal reached the path builder, it cannot escape. The nested cases
    # matter more now that `owner` legitimately contains slashes.
    for owner, name in (("..", "x"), ("a", ".."), ("../..", "x"),
                        ("a", "../../etc"), ("gitlab.com/../..", "x"),
                        ("gitlab.com/g/..", "../../etc"),
                        ("gitlab.com/g/s", "../../../../etc")):
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


def test_a_token_never_survives_into_an_error_message():
    with no_db(github_token="ghp_ambient"):
        msg = repos._redact(
            "fatal: could not read from "
            "https://x-access-token:ghp_ambient@github.com/a/b.git")
        assert "ghp_ambient" not in msg
        assert "***" in msg


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
