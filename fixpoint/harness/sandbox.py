"""Run the agent's OWN reproducer against a candidate patch, in the sandbox.

This is the execution half of the replan loop (step 4). It is NOT grading: the
graded FAIL_TO_PASS tests are never touched here. The agent writes a reproducer
script from the issue text; we apply the agent's patch inside the instance's
prebuilt container and run that script. Its exit code is the inner-loop reward:

    exit 0  -> reproducer is satisfied (the bug behavior is gone) = GREEN
    exit !0 -> the bug still reproduces, OR the script errored          = RED

Firewall: this takes only agent-visible inputs (image, base_commit, patch,
script). It cannot see gold_patch or the graded tests, so the loop that calls
it cannot cheat.

Container facts, verified by probing the django/11099 image:
  - repo lives at /testbed; deps are installed in the `testbed` conda env;
  - base_commit is reachable, so `git reset --hard <base>` gives a known state;
  - patches/scripts are passed base64-encoded via env vars to dodge every
    shell-quoting hazard (diffs are full of $, backticks, quotes).
"""

from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass

# Sentinels we grep out of the container's combined output. Distinctive so they
# can't collide with real test output.
_APPLY_FAILED = "__FIXPOINT_APPLY_FAILED__"
_EXIT_MARKER = "__FIXPOINT_EXIT__="

# The harness's own patch-apply fallback chain (run_evaluation.py:65-67), so our
# reproducer applies patches exactly the way grading will.
_RUN_SCRIPT = r"""
set +e
cd /testbed
git reset --hard {base_commit} -q
git clean -fdq
printf '%s' "$FIXPOINT_PATCH_B64" | base64 -d > /tmp/fixpoint_patch.diff
if [ -s /tmp/fixpoint_patch.diff ]; then
  ( git apply -v /tmp/fixpoint_patch.diff \
    || git apply -v --reject /tmp/fixpoint_patch.diff \
    || patch --batch --fuzz=5 -p1 -i /tmp/fixpoint_patch.diff ) > /tmp/fixpoint_apply.log 2>&1
  if [ $? -ne 0 ]; then echo "{apply_failed}"; cat /tmp/fixpoint_apply.log; exit 97; fi
fi
printf '%s' "$FIXPOINT_SCRIPT_B64" | base64 -d > /tmp/fixpoint_reproducer.py
source /opt/miniconda3/bin/activate
# swebench standardizes on the `testbed` env; fall back to the first non-base
# env so a repo that names it differently doesn't silently run base python
# (which would import-error and be misread as "the bug is present").
conda activate {env_name} 2>/dev/null || conda activate "$(conda env list | awk 'NR>2 && $1!="base" && $1!="" {{print $1; exit}}')"
timeout {timeout} python /tmp/fixpoint_reproducer.py
echo "{exit_marker}$?"
"""


@dataclass(frozen=True)
class ReproResult:
    applied: bool     # did the candidate patch apply?
    exit_code: int    # reproducer script exit code (-1 if it never ran)
    green: bool       # applied AND exit_code == 0
    output: str       # combined stdout/stderr, for feeding back into the loop


def run_reproducer(
    image: str,
    base_commit: str,
    patch: str,
    script: str,
    *,
    env_name: str = "testbed",
    timeout: int = 120,
    container_timeout: int = 300,
) -> ReproResult:
    """Apply `patch` in a fresh container of `image`, run `script`, report."""
    cmd = _RUN_SCRIPT.format(
        base_commit=base_commit, env_name=env_name, timeout=timeout,
        apply_failed=_APPLY_FAILED, exit_marker=_EXIT_MARKER,
    )
    proc = subprocess.run(
        ["docker", "run", "--rm", "--platform", "linux/amd64",
         "-e", "FIXPOINT_PATCH_B64", "-e", "FIXPOINT_SCRIPT_B64",
         image, "bash", "-lc", cmd],
        # Inherit the host env so the `docker` binary is found on PATH; only the
        # two FIXPOINT_* vars are forwarded INTO the container (via -e above), so
        # no host secret leaks past the docker CLI.
        env={
            **os.environ,
            "FIXPOINT_PATCH_B64": base64.b64encode(patch.encode()).decode(),
            "FIXPOINT_SCRIPT_B64": base64.b64encode(script.encode()).decode(),
        },
        capture_output=True, text=True, timeout=container_timeout,
    )
    output = proc.stdout + proc.stderr

    if _APPLY_FAILED in output:
        return ReproResult(applied=False, exit_code=-1, green=False, output=output)

    exit_code = -1
    for line in output.splitlines():
        if line.startswith(_EXIT_MARKER):
            try:
                exit_code = int(line[len(_EXIT_MARKER):].strip())
            except ValueError:
                exit_code = -1
    return ReproResult(applied=True, exit_code=exit_code, green=(exit_code == 0), output=output)
