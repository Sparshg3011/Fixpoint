"""Ask the model to write a reproducer script from the issue text.

The reproducer is the inner loop's reward signal (see harness/sandbox.py). It
must be a standalone script that exercises the buggy behavior and exits 0 only
when the bug is fixed. The graded tests are never shown to it — the whole point
is that the agent manufactures its own signal from the issue.

Failure mode this invites (the one to watch): reproducer overfitting — a script
that passes for the wrong reason, so the loop declares green while the real
tests still fail. We measure that gap (inner-green vs harness-RESOLVED) in
step 5; here we just try to write a faithful reproducer.
"""

from __future__ import annotations

import re

from fixpoint.agent.llm import DEFAULT_MODEL, LLMResult, call

SYSTEM = r"""You write a single standalone Python reproducer script for a bug
described in a GitHub issue, to be run from inside the project's repo checkout.

Contract for the script:
- It must reproduce the specific misbehavior the issue describes.
- It must exit with code 0 if and only if the behavior is CORRECT (bug fixed),
  and exit non-zero if the bug is present. Use sys.exit(0) / sys.exit(1) or let
  an assertion fail.
- It must run with no arguments: `python reproducer.py`.
- Import only the standard library and the project under test. If the project
  needs configuration to import (e.g. Django settings), configure the minimum
  inline (settings.configure(...)); do not rely on a project on disk.
- Print a short line stating what it observed, so a failure is legible.

Output ONLY the Python script, no prose and no code fences."""


def _extract_script(text: str) -> str:
    """Pull the script out, tolerating a ```python fence if the model adds one."""
    fence = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return (fence.group(1) if fence else text).strip() + "\n"


def generate_reproducer(problem_statement: str, files: dict[str, str], *,
                        model: str = DEFAULT_MODEL) -> tuple[str, LLMResult]:
    """Return (script_text, llm_result)."""
    user = [f"# GitHub issue\n\n{problem_statement.strip()}\n", "# Relevant files (for reference)\n"]
    for path, content in files.items():
        # Truncate each file — the reproducer needs the API shape, not every line.
        snippet = content if len(content) < 6000 else content[:6000] + "\n# ...(truncated)\n"
        user.append(f"\n## {path}\n```python\n{snippet}\n```\n")
    user.append("\nWrite the reproducer script.")
    result = call(SYSTEM, "\n".join(user), model=model)
    return _extract_script(result.text), result
