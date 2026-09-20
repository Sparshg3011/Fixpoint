"""Shallow mirrors: fetch the one commit a hosted run needs, not a decade of it.

Hermetic — "GitHub" here is a local repository that `checkout.origin_url` is
pointed at, so these run with no network. The properties under test are the
ones a small host depends on: the mirror stays tiny, a second commit of the
same repo still works, and the PR flow can still clone what it needs out of a
mirror that was never fully cloned.
"""

import subprocess

import pytest

from fixpoint.retrieval import checkout


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def origin(tmp_path, monkeypatch):
    """A three-commit repository standing in for github.com/acme/widget."""
    src = tmp_path / "origin"
    src.mkdir()
    git("init", "-q", "-b", "main", cwd=src)
    git("config", "user.email", "dev@example.com", cwd=src)
    git("config", "user.name", "Dev", cwd=src)
    # Fetching a bare SHA (rather than a branch) is what shallow mode does;
    # over file:// the server side must opt in, as GitHub's already does.
    git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=src)

    shas = []
    for n in range(3):
        (src / "app.py").write_text(f"VERSION = {n}\n")
        git("add", "-A", cwd=src)
        git("commit", "-qm", f"commit {n}", cwd=src)
        shas.append(git("rev-parse", "HEAD", cwd=src))

    monkeypatch.setattr(checkout, "origin_url", lambda repo: str(src))
    monkeypatch.setattr(checkout, "BARE_DIR", tmp_path / "bare")
    monkeypatch.setattr(checkout, "TREE_DIR", tmp_path / "trees")
    return shas


def commits_in(bare):
    return int(git("--git-dir", str(bare), "rev-list", "--count", "--all", cwd=bare.parent))


def test_shallow_mirror_fetches_one_commit_not_the_history(origin, monkeypatch):
    monkeypatch.setenv("FIXPOINT_SHALLOW_CLONES", "1")
    tree = checkout.tree_at("acme/widget", origin[1])

    assert (tree / "app.py").read_text() == "VERSION = 1\n"
    assert commits_in(checkout.bare_path("acme/widget")) == 1


def test_a_second_commit_of_the_same_repo_still_resolves(origin, monkeypatch):
    """The mirror is reused across runs, so the second visit must fetch into
    it rather than trip over the shallow boundary left by the first."""
    monkeypatch.setenv("FIXPOINT_SHALLOW_CLONES", "1")
    assert (checkout.tree_at("acme/widget", origin[0]) / "app.py").read_text() == "VERSION = 0\n"
    assert (checkout.tree_at("acme/widget", origin[2]) / "app.py").read_text() == "VERSION = 2\n"


def test_the_pr_flow_can_clone_a_commit_out_of_a_shallow_mirror(origin, tmp_path, monkeypatch):
    """What pr.open_pr does: clone the mirror, then check the base commit out.
    An unreferenced object would not survive the clone — hence the pin."""
    monkeypatch.setenv("FIXPOINT_SHALLOW_CLONES", "1")
    mirror = checkout.ensure_commit("acme/widget", origin[1])

    work = tmp_path / "work"
    git("clone", "--quiet", "--no-checkout", str(mirror), str(work), cwd=tmp_path)
    git("checkout", "-q", "-b", "fixpoint/base-x", origin[1], cwd=work)
    assert (work / "app.py").read_text() == "VERSION = 1\n"


def test_ensure_commit_rebuilds_a_mirror_the_cache_trimmer_evicted(origin, monkeypatch):
    """Hosted disks are swept and hosted disks are ephemeral: the PR click can
    arrive with nothing on disk at all."""
    monkeypatch.setenv("FIXPOINT_SHALLOW_CLONES", "1")
    checkout.tree_at("acme/widget", origin[1])
    import shutil

    shutil.rmtree(checkout.BARE_DIR)
    assert checkout._has_commit(checkout.ensure_commit("acme/widget", origin[1]), origin[1])


def test_default_mode_still_mirrors_the_whole_history(origin, monkeypatch):
    """Benchmark campaigns revisit dozens of commits per repo and must not
    start paying a fetch per instance. Unset = exactly the old behaviour."""
    monkeypatch.delenv("FIXPOINT_SHALLOW_CLONES", raising=False)
    checkout.tree_at("acme/widget", origin[1])

    bare = checkout.bare_path("acme/widget")
    assert commits_in(bare) == 3
    assert git("--git-dir", str(bare), "for-each-ref", "--format=%(refname)",
               "refs/heads/fixpoint-pin-*", cwd=bare.parent) == ""
