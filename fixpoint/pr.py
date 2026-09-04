"""Open a pull request carrying an agent-generated patch.

The demo's last mile: a patch that resolved becomes a real, reviewable PR.

SAFETY — this is the part that matters. These patches target real open-source
projects (django, sympy, ...). Opening a PR against those upstreams would spam
maintainers with machine-generated code. So:

  * the target repo MUST be owned by the authenticated GitHub user (a fork);
    a target owned by anyone else is refused outright, and
  * the PR is opened fork-internal: we push a branch at base_commit and another
    with the patch applied, then open fix -> base. The diff shown is exactly the
    agent's patch, one commit, nothing else.

Two identities can publish. With a GitHub App configured (github_app.py) the
PR comes from `<slug>[bot]` and the safety rule becomes "the App is installed
on the target" — an owner has to grant that deliberately. Otherwise the `gh`
login publishes into its own fork. Dry runs take no outward action in either.

Nothing here calls a model — it consumes patches already produced and graded.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fixpoint.retrieval.checkout import bare_path


class PRSafetyError(Exception):
    """Refused: the target would publish outside the user's own fork."""


@dataclass(frozen=True)
class PRResult:
    url: str
    branch: str
    base_branch: str
    dry_run: bool
    actor: str = ""  # who the PR is (or would be) authored as


def _gh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{proc.stderr.strip()}")
    return proc.stdout.strip()


def current_user() -> str:
    return _gh("api", "user", "--jq", ".login")


def fork_name(upstream: str) -> str:
    """What the authenticated user's fork of `upstream` is (or would be) called."""
    return f"{current_user()}/{upstream.split('/')[1]}"


def ensure_fork(upstream: str) -> str:
    """Return the user's fork of `upstream`, CREATING it if absent.

    Creating a fork makes a repository under the user's account — an outward
    action — so this is only ever called on the --execute path, never during a
    dry run. `--default-branch-only` keeps the fork small; we push the branches
    we actually need from the local mirror anyway.
    """
    fork = fork_name(upstream)
    exists = subprocess.run(["gh", "repo", "view", fork], capture_output=True).returncode == 0
    if not exists:
        _gh("repo", "fork", upstream, "--clone=false", "--default-branch-only")
    return fork


def assert_safe_target(target: str) -> None:
    """Refuse any target the authenticated user does not own."""
    owner = target.split("/")[0]
    user = current_user()
    if owner.lower() != user.lower():
        raise PRSafetyError(
            f"refusing to open a PR against {target!r}: owned by {owner!r}, not you ({user!r}).\n"
            "Fixpoint only opens PRs on your own fork — never on an upstream project."
        )


def open_pr(*, upstream: str, base_commit: str, patch: str, instance_id: str,
            problem_statement: str = "", resolved: bool = False,
            dry_run: bool = True) -> PRResult:
    """Push base + fix branches and open a PR between them.

    A dry run does everything locally — clone, branch, apply, diff — and takes
    no outward action at all: no fork is created, nothing is pushed, no PR is
    opened. That is what makes it safe to run freely.
    """
    from fixpoint import github_app  # lazy: only the PR flow pays for the import

    app = github_app.client_if_configured()
    if app is not None:
        # App mode: publish into the repo itself, which the App must be
        # installed on. Not installed == not permitted, same as not owned.
        try:
            app.assert_installed(upstream)
        except github_app.AppNotInstalledError as e:
            raise PRSafetyError(str(e)) from e
        target, actor = upstream, app.bot_login()
        author = app.commit_identity()
    else:
        # Personal mode: resolve the fork name in both modes so the safety
        # check runs either way, but only CREATE the fork when publishing.
        target = ensure_fork(upstream) if not dry_run else fork_name(upstream)
        assert_safe_target(target)
        actor = current_user()
        author = ("fixpoint-agent", "fixpoint@localhost")

    short = base_commit[:8]
    base_branch = f"fixpoint/base-{instance_id}"
    fix_branch = f"fixpoint/fix-{instance_id}"

    # Work in a throwaway clone of the local bare mirror so we never disturb the
    # cached mirrors the retrieval layer depends on.
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / "repo"
        _git("clone", "--quiet", "--no-checkout", str(bare_path(upstream)), str(work))
        _git("checkout", "-q", "-b", base_branch, base_commit, cwd=work)

        patch_file = work.parent / "patch.diff"
        patch_file.write_text(patch)
        _git("checkout", "-q", "-b", fix_branch, cwd=work)
        # Same apply chain the grading harness uses, so a patch that graded
        # RESOLVED cannot fail to apply here for a different reason.
        for cmd in (["apply", "-v", str(patch_file)],
                    ["apply", "-v", "--reject", str(patch_file)],
                    ["apply", "--3way", str(patch_file)]):
            if subprocess.run(["git", *cmd], cwd=work, capture_output=True).returncode == 0:
                break
        else:
            raise RuntimeError(f"patch for {instance_id} did not apply to {short}")

        _git("-c", f"user.name={author[0]}", "-c", f"user.email={author[1]}",
             "commit", "-aqm", f"fix: {instance_id}", cwd=work)

        if dry_run:
            stat = _git("diff", "--stat", base_branch, fix_branch, cwd=work)
            return PRResult(url=f"(dry run) would open PR on {target} as {actor}\n{stat}",
                            branch=fix_branch, base_branch=base_branch, dry_run=True,
                            actor=actor)

        remote = f"https://github.com/{target}.git"
        # App mode carries its token in an HTTP header (push_config), never in
        # the remote URL — so no git error message or log can ever quote it.
        auth = app.push_config() if app is not None else []
        _git(*auth, "push", "-q", "--force", remote, f"{base_branch}:{base_branch}", cwd=work)
        _git(*auth, "push", "-q", "--force", remote, f"{fix_branch}:{fix_branch}", cwd=work)

    body = _pr_body(instance_id, upstream, base_commit, problem_statement, resolved)
    title = f"fix: {instance_id}"
    if app is not None:
        url = app.create_pr(target, base=base_branch, head=fix_branch, title=title, body=body)
    else:
        url = _gh("pr", "create", "--repo", target, "--base", base_branch, "--head", fix_branch,
                  "--title", title, "--body", body)
    return PRResult(url=url, branch=fix_branch, base_branch=base_branch, dry_run=False,
                    actor=actor)


def _pr_body(instance_id: str, upstream: str, base_commit: str,
             problem_statement: str, resolved: bool) -> str:
    verdict = ("**Graded RESOLVED** by the official SWE-bench harness — the hidden "
               "FAIL_TO_PASS tests pass and no PASS_TO_PASS test regressed."
               if resolved else
               "Not graded as resolved; opened for inspection.")
    issue = (problem_statement.strip()[:1200] + "…") if len(problem_statement) > 1200 else problem_statement.strip()
    return (
        f"Generated by [Fixpoint](https://github.com/Sparshg3011/Fixpoint), an autonomous "
        f"SWE-bench agent.\n\n"
        f"- **Instance**: `{instance_id}` (upstream `{upstream}`)\n"
        f"- **Base commit**: `{base_commit[:12]}`\n"
        f"- **Verdict**: {verdict}\n\n"
        f"The agent saw only the issue text below and the repository at the base commit — "
        f"never the reference patch or the graded tests.\n\n"
        f"<details><summary>Issue</summary>\n\n{issue}\n\n</details>\n"
    )
