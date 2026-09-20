"""Host-side repo checkouts for indexing.

One bare mirror per repo (cloned once, ~100-300MB), then one plain tree per
(repo, commit) extracted with `git archive`. Two properties we rely on:

  no .git in trees   `git archive` emits only the tree at that commit, so an
                     index built from it physically cannot see the future —
                     the fix commit for every Lite instance exists in these
                     repos' histories, and history is a leak channel.
  idempotent + cheap trees are cached by (repo, first 12 of commit); repeat
                     calls return instantly, so the eval harness can call this
                     per instance without thinking about it.

`FIXPOINT_SHALLOW_CLONES=1` switches the mirror from "clone the whole history"
to "fetch the one commit we were asked about". A benchmark campaign wants the
full mirror — it revisits dozens of commits per repo and pays for the history
once. A hosted deployment is the opposite case: one commit, once, on a sliver
of a CPU, where cloning django's full history cost eleven minutes before the
agent could read a single file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from fixpoint.paths import REPOS_DIR  # FIXPOINT_STATE_DIR-aware

BARE_DIR = REPOS_DIR / "bare"
TREE_DIR = REPOS_DIR / "trees"

# One lock per cache key. Benchmark runners chunk instances by repo, so two
# workers hitting the SAME repo's first checkout simultaneously is the common
# case, not the exotic one — without this, both would clone/extract into the
# same destination and one of them corrupts the other.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _finalize(tmp: Path, dest: Path) -> None:
    """Atomically promote tmp -> dest, tolerating a concurrent winner.

    rename over an existing directory fails on POSIX; if dest appeared while
    we were building tmp (another process finished first), their copy is as
    good as ours — discard tmp and use theirs."""
    try:
        tmp.rename(dest)
    except OSError:
        if dest.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            raise


def _git(*args: str) -> None:
    # capture_output so a failure surfaces its stderr in the exception message
    # instead of interleaving with harness logs on the console.
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")


def shallow_mode() -> bool:
    """Read at call time, never cached: tests and the server toggle it."""
    return bool(os.environ.get("FIXPOINT_SHALLOW_CLONES"))


def origin_url(repo: str) -> str:
    """Where a mirror is fetched from. A seam: tests point it at a local repo."""
    return f"https://github.com/{repo}.git"


def _pin_ref(commit: str) -> str:
    """A shallow mirror is built by `git init` and so has no refs at all, and
    `git clone` transfers only what some ref makes reachable. Without this pin
    the PR flow would clone an empty repository."""
    return f"refs/heads/fixpoint-pin-{commit[:12]}"


def _has_commit(bare: Path, commit: str) -> bool:
    return subprocess.run(
        ["git", "--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True).returncode == 0


def bare_path(repo: str) -> Path:
    """Ensure a bare mirror of github.com/<repo> exists; return its path.

    Clone lands in a temp dir and is renamed into place — `git clone` straight
    into the destination would let a killed clone masquerade as a complete
    mirror forever (dest.exists() is the only completeness check we have).

    In shallow mode the mirror starts EMPTY: an initialised bare repo with an
    origin, holding no objects until someone asks for a commit."""
    dest = BARE_DIR / f"{repo.replace('/', '__')}.git"
    if dest.exists():
        return dest
    with _lock_for(f"bare:{repo}"):
        if dest.exists():  # another thread won while we waited
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        if shallow_mode():
            _git("init", "--bare", "--quiet", str(tmp))
            _git("--git-dir", str(tmp), "remote", "add", "origin", origin_url(repo))
        else:
            _git("clone", "--bare", origin_url(repo), str(tmp))
        _finalize(tmp, dest)
    return dest


def ensure_commit(repo: str, commit: str) -> Path:
    """The repo's mirror, guaranteed to contain `commit` — fetching it if the
    mirror is shallow, new, or was evicted by the cache trimmer.

    Returned mirrors are safe to `git clone` locally: in shallow mode the
    commit is pinned under a ref first, because an unreferenced object does
    not survive a clone.
    """
    bare = bare_path(repo)
    if _has_commit(bare, commit):
        return bare
    with _lock_for(f"fetch:{repo}:{commit[:12]}"):
        if not _has_commit(bare, commit):
            # Full mirrors carry every branch and tag, so a base commit is
            # normally already here and this fetch is an exotic-case fallback.
            depth = ["--depth", "1"] if shallow_mode() else []
            _git("--git-dir", str(bare), "fetch", "--quiet", *depth, "origin", commit)
        if shallow_mode():
            _git("--git-dir", str(bare), "update-ref", _pin_ref(commit), commit)
    return bare


def tree_at(repo: str, commit: str) -> Path:
    """Plain source tree of repo@commit (no .git), cached under data/repos/trees."""
    dest = TREE_DIR / f"{repo.replace('/', '__')}__{commit[:12]}"
    if dest.exists():
        return dest
    with _lock_for(f"tree:{dest.name}"):
        if dest.exists():
            return dest
        bare = ensure_commit(repo, commit)
        # Extract into a temp dir and rename, so an interrupted extraction can
        # never masquerade as a complete cached tree.
        tmp = dest.with_suffix(".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        archive = subprocess.Popen(["git", "--git-dir", str(bare), "archive", commit],
                                   stdout=subprocess.PIPE)
        subprocess.run(["tar", "-x", "-C", str(tmp)], stdin=archive.stdout, check=True)
        if archive.wait() != 0:
            shutil.rmtree(tmp)
            raise RuntimeError(f"git archive failed for {repo}@{commit[:12]}")
        _finalize(tmp, dest)
    return dest
