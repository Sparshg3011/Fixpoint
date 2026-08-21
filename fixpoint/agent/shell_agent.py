"""The interactive bash agent: a model, a shell, and a step budget.

This is the architecture the current SWE-bench leaderboard runs on (the
mini-SWE-agent insight): stop photocopying files for the model and let it
work the repository like an engineer — grep for the code, read it, edit it,
RUN THE TESTS, iterate. Our measured caps disappear structurally:

  localization   the agent searches the whole tree itself; there is no top-5
                 lottery to lose (was a hard cap at ~70%).
  verification   it executes its own fix before submitting instead of
                 guessing (untested-guess losses were the largest bucket).
  patch format   the diff comes from `git diff` in the container; a malformed
                 patch is impossible, so the sanitizer isn't even involved.

Firewall unchanged and non-negotiable: the agent sees the issue text and a
sanitized, network-less container (interactive.py enforces both); it never
sees gold patches, graded tests, or hints.

Protocol, kept deliberately dumb (dumb protocols are what small open models
follow reliably): every reply must contain exactly one ```bash fence; each
command runs in a FRESH shell at /testbed (state does not persist — the
prompt says so, loudly); the agent submits by echoing a sentinel.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from fixpoint.agent.llm import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, chat
from fixpoint.diary import Diary
from fixpoint.harness.interactive import ShellSession

SUBMIT_SENTINEL = "FIXPOINT_SUBMIT"

SYSTEM = f"""You are an expert software engineer fixing a real GitHub issue in a
repository checked out at /testbed. You work in a bash shell.

RESPONSE FORMAT — every reply must contain exactly ONE bash code block:
```bash
<one or more shell commands>
```
The block is executed and you receive its output (exit code included). Do not
write anything after the code block.

RULES OF THE SHELL:
- Every block runs in a FRESH shell at /testbed. Nothing persists between
  blocks: no exports, no cd, no shell variables. Write self-contained blocks.
- The Python environment for the project is already active.
- There is NO network access.
- Long outputs are truncated; prefer targeted commands (grep -n, sed -n
  'X,Yp', python -m pytest path/to/test -x -q) over dumping whole files.

HOW TO WORK:
1. Understand the issue, then FIND the relevant code (grep/ls/sed).
2. Reproduce the problem if practical (run the snippet from the issue).
3. Edit the source with a small, root-cause fix. Reliable editing pattern:
   python - <<'EOF'
   import pathlib
   p = pathlib.Path('path/to/file.py')
   s = p.read_text()
   assert s.count('OLD') == 1
   p.write_text(s.replace('OLD', 'NEW'))
   EOF
4. Re-run your reproduction and any obviously related existing tests.
5. Keep scratch files in /tmp, NEVER in /testbed.

WHEN THE FIX IS VERIFIED, submit by replying with exactly:
```bash
echo {SUBMIT_SENTINEL}
```
Submit only after your fix is in place — the final patch is the git diff of
/testbed at that moment. Do not modify tests. Do not reformat unrelated code."""

_BASH_FENCE_RE = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL)

# One nudge per malformed reply; two in a row ends the run (a model that
# cannot produce a fence twice will not recover by being asked a third time).
# The nudge must include the submit path: the most common fence-less reply is
# a model that considers itself DONE and writes a prose conclusion — it needs
# the exit door, not just a demand for more commands.
_NUDGE = ("Your reply had no ```bash block. Reply with exactly one ```bash "
          "fenced block containing the next command. If your fix is complete "
          f"and verified, submit it with:\n```bash\necho {SUBMIT_SENTINEL}\n```")


@dataclass
class ShellResult:
    diff: str
    submitted: bool           # the agent chose to stop (vs budget exhaustion)
    steps: int
    cost_usd: float
    wall_s: float
    error: str | None = None
    transcript: list[dict] = field(default_factory=list)  # saved for offline replay


def _extract_command(text: str) -> str | None:
    blocks = _BASH_FENCE_RE.findall(text)
    return blocks[0].strip() if blocks else None


def solve_in_shell(problem_statement: str, image: str, base_commit: str, *,
                   model: str = DEFAULT_MODEL, max_steps: int = 40,
                   command_timeout: int = 60, diary: Diary | None = None) -> ShellResult:
    """Run one instance interactively; return the git diff the agent left behind."""
    started = time.time()
    cost = 0.0
    messages: list[dict] = [{"role": "user", "content":
                             f"# GitHub issue\n\n{problem_statement.strip()}\n\n"
                             "Begin by locating the relevant code."}]
    if diary:
        diary.record("sandbox", "started", image=image, mode="interactive")

    try:
        with ShellSession(image, base_commit, timeout=command_timeout) as shell:
            if diary:
                diary.record("sandbox", "succeeded", sanitized=True, network="none")
            nudges = 0
            for step in range(1, max_steps + 1):
                reply = chat(SYSTEM, messages, model=model)
                cost += reply.cost_usd
                # An EMPTY reply is the endpoint failing, not the agent
                # deciding — measured: 19 of 20 "protocol drift" deaths in the
                # n=100 campaign were the congested free tier returning nothing
                # (or burning the whole budget on reasoning). Retry the turn;
                # double the budget when truncation was the stated reason.
                empty_retries = 0
                while not reply.text.strip() and empty_retries < 3:
                    empty_retries += 1
                    time.sleep(min(15 * empty_retries, 45))
                    reply = chat(SYSTEM, messages, model=model,
                                 max_tokens=(DEFAULT_MAX_TOKENS * 2
                                             if reply.finish_reason == "length" else None))
                    cost += reply.cost_usd
                messages.append({"role": "assistant", "content": reply.text})

                command = _extract_command(reply.text)
                if command is None:
                    nudges += 1
                    if nudges >= 2:
                        return ShellResult(diff=shell.diff(), submitted=False, steps=step,
                                           cost_usd=round(cost, 6),
                                           wall_s=round(time.time() - started, 1),
                                           error="model stopped emitting bash blocks",
                                           transcript=messages)
                    messages.append({"role": "user", "content": _NUDGE})
                    continue
                nudges = 0

                if SUBMIT_SENTINEL in command:
                    diff = shell.diff()
                    if diary:
                        diary.record("loop", "succeeded" if diff.strip() else "failed",
                                     green=bool(diff.strip()), steps=step, mode="interactive")
                    return ShellResult(diff=diff, submitted=True, steps=step,
                                       cost_usd=round(cost, 6),
                                       wall_s=round(time.time() - started, 1),
                                       transcript=messages)

                exit_code, output = shell.run(command)
                if diary:
                    stage = "developer" if step > 1 else "retrieval"
                    diary.record(stage, "progress", step=step, command=command[:2000],
                                 exit_code=exit_code, output=output[:2000])
                # Budget pressure lives in the observation, where the model
                # actually looks. Without it, a diligent model re-verifies
                # forever and burns the whole budget (measured on the first
                # smoke run: correct fix at step 11, still exploring at 25).
                observation = f"exit code: {exit_code} (step {step}/{max_steps})\n```\n{output}\n```"
                if step >= 4:
                    observation += (f"\nIf your fix is already made and verified, submit NOW "
                                    f"with:\n```bash\necho {SUBMIT_SENTINEL}\n```")
                messages.append({"role": "user", "content": observation})

            # Budget exhausted: whatever is edited so far is the best effort.
            diff = shell.diff()
            if diary:
                diary.record("loop", "failed", green=False, steps=max_steps,
                             mode="interactive", error="step budget exhausted")
            return ShellResult(diff=diff, submitted=False, steps=max_steps,
                               cost_usd=round(cost, 6),
                               wall_s=round(time.time() - started, 1),
                               error="step budget exhausted", transcript=messages)
    except Exception as e:
        if diary:
            diary.record("loop", "failed", green=False, error=str(e)[:500])
        return ShellResult(diff="", submitted=False, steps=0, cost_usd=round(cost, 6),
                           wall_s=round(time.time() - started, 1),
                           error=f"{type(e).__name__}: {e}", transcript=messages)
