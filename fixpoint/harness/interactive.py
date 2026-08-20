"""A live shell inside an instance container — the bash agent's hands.

One ShellSession per instance: the official SWE-bench image is started
detached, the agent's commands are exec'd into it one at a time, and the
final patch is whatever `git diff` says changed under /testbed. The diff
COMES FROM GIT — malformed-patch failures are structurally impossible, which
retires the entire sanitizer layer for this mode.

Two firewall properties are enforced at session start, not trusted:

  no network   `--network none`. The fix for every benchmark instance exists
               on public GitHub; a shell with internet access could simply go
               read it. With no network there is nothing to fetch.
  no future    The image's /testbed checkout carries git metadata. Anything
               that could name commits beyond base (remotes, reflogs, remote
               refs, packed remote entries) is scrubbed before the agent's
               first command, and `git log` visibility is asserted to end at
               the base commit — fail closed if it doesn't.

Commands run statelessly (fresh bash each time, cwd /testbed, conda testbed
env active) with a hard timeout enforced INSIDE the container via `timeout`,
so a hung test run cannot wedge the session.
"""

from __future__ import annotations

import subprocess
import uuid

# Output returned to the model per command. Long pytest logs bury the signal
# and burn context; head+tail keeps both the command echo and the verdict.
MAX_OUTPUT_CHARS = 8_000

# The conda activation mirrors sandbox.py: every instance image has a
# `testbed` env; a handful name it differently, so fall back to the first
# env that exists.
_ACTIVATE = ("source /opt/miniconda3/bin/activate testbed 2>/dev/null "
             "|| conda activate testbed 2>/dev/null || true; "
             # No core dumps: a segfaulting reproduction otherwise leaves a
             # binary `core` file in /testbed that pollutes the final diff.
             "ulimit -c 0 2>/dev/null")


class ShellSessionError(Exception):
    """The container could not be started, sanitized, or verified."""


def _run(args: list[str], timeout: int = 120,
         stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          input=stdin)


class ShellSession:
    """Lifecycle: ShellSession(image, base_commit) -> run()* -> diff() -> close().

    Use as a context manager so a crashed agent never leaks a container.
    """

    def __init__(self, image: str, base_commit: str, *, timeout: int = 60):
        self.image = image
        self.base_commit = base_commit
        self.timeout = timeout
        self.name = f"fixpoint-shell-{uuid.uuid4().hex[:12]}"
        started = _run(["docker", "run", "-d", "--rm", "--network", "none",
                        "--platform", "linux/amd64", "--name", self.name,
                        image, "sleep", "infinity"])
        if started.returncode != 0:
            raise ShellSessionError(f"container start failed: {started.stderr.strip()[:300]}")
        try:
            self._sanitize()
        except Exception:
            self.close()
            raise

    def _sanitize(self) -> None:
        """Remove every path from /testbed's git metadata to post-base commits,
        then PROVE the scrub worked. The clean checkout becomes the diff base."""
        scrub = (
            "cd /testbed && "
            "git remote | xargs -r -n1 git remote remove; "
            "rm -rf .git/refs/remotes .git/logs; "
            "git reflog expire --expire=now --all 2>/dev/null; "
            "git tag | xargs -r git tag -d >/dev/null; "
            "git gc --prune=now --quiet 2>/dev/null; "
            "git checkout -q -- . 2>/dev/null; true"
        )
        r = self._exec(scrub, timeout=180)
        if r.returncode != 0:
            raise ShellSessionError(f"git scrub failed: {r.stdout[-300:]}")
        # Fail-closed assertion, calibrated to how the images are actually
        # built: SWE-bench layers ONE environment-setup commit on top of the
        # base commit (the harness grades against exactly this state, so it is
        # our diff baseline too). Legitimate therefore means: base is an
        # ancestor of HEAD, HEAD is within a couple of commits of base, and —
        # the leak channel — NOTHING in the repo is reachable outside HEAD's
        # history. The repo's real future (the fix commit) could only appear
        # as such an extra ref after the remote/tag/reflog scrub above.
        check = self._exec(
            "cd /testbed"
            f" && git merge-base --is-ancestor {self.base_commit} HEAD"
            " && echo ANCESTOR"
            f" && git rev-list --count {self.base_commit}..HEAD"
            " && git rev-list --all --not HEAD --count", timeout=60)
        lines = [ln.strip() for ln in check.stdout.strip().splitlines()]
        ok = (len(lines) >= 3 and lines[-3] == "ANCESTOR"
              and lines[-2].isdigit() and int(lines[-2]) <= 3
              and lines[-1] == "0")
        if not ok:
            raise ShellSessionError(
                f"future-leak check failed for {self.image}: {check.stdout[-300:]!r}")

    def _exec(self, script: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        t = timeout or self.timeout
        # The agent's script arrives on stdin — no shell-quoting layer to get
        # wrong, no injection surface through nested quotes. `timeout` inside
        # the container kills the process tree even when the outer subprocess
        # timeout would only abandon the docker-exec client.
        return _run(["docker", "exec", "-i", self.name, "bash", "-lc",
                     f"{_ACTIVATE}; cd /testbed; timeout {t} bash -s"],
                    timeout=t + 30, stdin=script)

    def run(self, command: str) -> tuple[int, str]:
        """One agent command -> (exit_code, capped combined output)."""
        try:
            r = self._exec(command)
        except subprocess.TimeoutExpired:
            return 124, f"(command killed after {self.timeout}s)"
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
        if len(out) > MAX_OUTPUT_CHARS:
            half = MAX_OUTPUT_CHARS // 2
            out = out[:half] + f"\n... (output truncated, {len(out)} chars total) ...\n" + out[-half:]
        return r.returncode, out

    def diff(self) -> str:
        """The agent's patch: every change to tracked files plus new files it
        added under /testbed (intent-to-add makes them diffable).

        Binary files are excluded by construction: a text diff of a binary
        shows no content, so the harness's `git apply` would reject the whole
        patch over a crash dump or stray artifact the agent never meant to
        ship. numstat marks binaries with "-" columns; everything else keeps
        its hunks."""
        r = self._exec("git add -N . >/dev/null 2>&1; git diff --numstat", timeout=60)
        if r.returncode != 0:
            return ""
        text_paths = [parts[2] for ln in r.stdout.splitlines()
                      if len(parts := ln.split("\t")) == 3 and parts[0] != "-"]
        if not text_paths:
            return ""
        import shlex
        paths = " ".join(shlex.quote(p) for p in text_paths)
        d = self._exec(f"git diff -- {paths}", timeout=60)
        return d.stdout if d.returncode == 0 else ""

    def close(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def __enter__(self) -> ShellSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
