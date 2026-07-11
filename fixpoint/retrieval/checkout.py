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
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BARE_DIR = REPO_ROOT / "data" / "repos" / "bare"
TREE_DIR = REPO_ROOT / "data" / "repos" / "trees"


def _git(*args: str) -> None:
    # capture_output so a failure surfaces its stderr in the exception message
    # instead of interleaving with harness logs on the console.
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")


def bare_path(repo: str) -> Path:
    """Ensure a bare mirror of github.com/<repo> exists; return its path."""
    dest = BARE_DIR / f"{repo.replace('/', '__')}.git"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--bare", f"https://github.com/{repo}.git", str(dest))
    return dest


def tree_at(repo: str, commit: str) -> Path:
    """Plain source tree of repo@commit (no .git), cached under data/repos/trees."""
    dest = TREE_DIR / f"{repo.replace('/', '__')}__{commit[:12]}"
    if dest.exists():
        return dest
    bare = bare_path(repo)
    # Bare clones carry all branches/tags; base commits are ancestors of the
    # default branch so this fetch is only a fallback for exotic cases.
    probe = subprocess.run(["git", "--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"],
                           capture_output=True)
    if probe.returncode != 0:
        _git("--git-dir", str(bare), "fetch", "origin", commit)
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
    tmp.rename(dest)
    return dest
