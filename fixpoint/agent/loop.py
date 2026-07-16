"""The bounded test-driven replan loop — the second crux.

Composes two engines we validated independently:
  - the diff synthesizer (agent/edits.py, proven byte-identical to gold + git
    apply in verify_sanitizer.py), and
  - the reproducer sandbox (harness/sandbox.py, proven RED-on-base /
    GREEN-on-gold in verify_reproducer.py),
plus LLM calls for the reproducer and each patch attempt.

The loop is what makes this SEARCH, not retry: every attempt after the first is
conditioned on the concrete reproducer failure from the previous one, not
re-rolled blind. It is BOUNDED (max_attempts) because unbounded retry on a
messy signal burns money without converging.

Reward semantics (the honest part): the loop's "green" means the agent's OWN
reproducer passed and the nearby tests didn't regress. It is a PROXY for the
hidden graded tests, and it can be wrong in both directions. The gap between
loop-green and harness-RESOLVED is the number step 5 reports — this loop never
sees the graded tests, by construction.

Firewall: solve() takes only agent-visible inputs plus the opaque container
image string; it never receives the reference solution or the graded tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fixpoint.agent.llm import DEFAULT_MODEL
from fixpoint.agent.patcher import generate_patch
from fixpoint.agent.reproducer import generate_reproducer
from fixpoint.harness.sandbox import ReproResult, run_reproducer


@dataclass
class Step:
    """One event in the trajectory — the unit the run diary / UI will render."""

    kind: str          # reproducer | attempt | result
    detail: str
    green: bool | None = None


@dataclass
class SolveResult:
    diff: str                       # best patch produced ("" if none applied)
    green: bool                     # reproducer satisfied and (if run) no regression
    attempts: int
    cost_usd: float
    trajectory: list[Step] = field(default_factory=list)
    reproducer_valid: bool = False  # did the reproducer actually go red on base?


def _feedback(prior_diff: str, repro: ReproResult) -> str:
    """Turn a failed attempt into concrete guidance for the next one. The tail
    of the reproducer output is the signal; the diff reminds the model what it
    already tried so it doesn't repeat itself."""
    if not repro.applied:
        head = "Your patch did not apply to the repository (git apply failed)."
    elif repro.exit_code == -1:
        head = "Your patch applied but the reproducer could not be run."
    else:
        head = (f"Your patch applied, but the reproducer still fails "
                f"(exit {repro.exit_code}) — the bug is not fixed.")
    tail = "\n".join(repro.output.strip().splitlines()[-25:])
    return (f"{head}\n\nThe edits you tried:\n{prior_diff or '(no valid edits)'}\n\n"
            f"Reproducer output (last lines):\n{tail}")


def solve(problem_statement: str, files: dict[str, str], image: str, base_commit: str, *,
          max_attempts: int = 3, model: str = DEFAULT_MODEL) -> SolveResult:
    """Run the bounded loop for one instance and return the best patch found."""
    traj: list[Step] = []
    cost = 0.0

    # 1. Write a reproducer and confirm it actually captures the bug: it must be
    #    RED with no fix applied. A reproducer that's already green (or errors)
    #    is a broken compass — we note it and proceed with weak confidence
    #    rather than trusting a green that means nothing.
    script, rl = generate_reproducer(problem_statement, files, model=model)
    cost += rl.cost_usd
    base_check = run_reproducer(image, base_commit, patch="", script=script)
    reproducer_valid = base_check.applied and not base_check.green
    traj.append(Step("reproducer",
                     f"red-on-base={reproducer_valid} (exit {base_check.exit_code})",
                     green=not reproducer_valid))

    # 2. Bounded attempt loop. Attempt 1 is fresh; later attempts replan from
    #    the previous reproducer failure.
    best_diff = ""
    feedback: str | None = None
    green = False
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        patch = generate_patch(problem_statement, files, model=model, feedback=feedback)
        cost += patch.llm.cost_usd
        if not patch.diff:
            traj.append(Step("attempt", f"#{attempt}: no usable patch — {patch.error}", green=False))
            feedback = f"You produced no applicable edits: {patch.error}. Re-read the files and try again."
            continue
        best_diff = patch.diff  # keep the latest applicable diff as our best effort

        repro = run_reproducer(image, base_commit, patch=patch.diff, script=script)
        traj.append(Step("attempt",
                         f"#{attempt}: applied={repro.applied} reproducer_exit={repro.exit_code}",
                         green=repro.green))
        if repro.green:
            green = True
            break
        feedback = _feedback(patch.diff, repro)

    traj.append(Step("result", f"green={green} after {attempt} attempt(s)", green=green))
    return SolveResult(diff=best_diff, green=green, attempts=attempt, cost_usd=round(cost, 6),
                       trajectory=traj, reproducer_valid=reproducer_valid)
