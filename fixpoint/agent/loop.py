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
from fixpoint.diary import Diary
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
          max_attempts: int = 3, model: str = DEFAULT_MODEL,
          diary: Diary | None = None, corpus: dict[str, str] | None = None) -> SolveResult:
    """Run the bounded loop for one instance and return the best patch found.

    `corpus` (whole-checkout path -> content) is passed to the sanitizer so an
    edit targeting a real-but-unshown file can still apply — see synthesize_diff.
    """
    traj: list[Step] = []
    cost = 0.0

    # 1. Write a reproducer and confirm it actually captures the bug: with no
    #    fix applied it must exit non-zero for a real, captured reason. exit 0
    #    means it doesn't detect the bug; exit -1 means the run itself failed.
    #    Either way its "green" would be meaningless, so we don't trust it.
    script, rl = generate_reproducer(problem_statement, files, model=model)
    cost += rl.cost_usd
    base_check = run_reproducer(image, base_commit, patch="", script=script)
    reproducer_valid = base_check.applied and base_check.exit_code > 0
    traj.append(Step("reproducer",
                     f"red-on-base={reproducer_valid} (exit {base_check.exit_code})",
                     green=not reproducer_valid))
    if diary:
        diary.record("reproducer", "succeeded" if reproducer_valid else "failed",
                     red_on_base=reproducer_valid, exit_code=base_check.exit_code,
                     script=script)

    # 2a. Broken compass: the reproducer can't tell a fix from nothing, so its
    #     green is not evidence. Produce one best-effort patch and report it
    #     UNVERIFIED — never claim green off a reproducer that didn't go red.
    if not reproducer_valid:
        patch = generate_patch(problem_statement, files, model=model, cache=True, corpus=corpus)
        cost += patch.llm.cost_usd
        traj.append(Step("attempt", "#1: reproducer not trustworthy — best-effort patch, unverified",
                         green=None))
        traj.append(Step("result", "green=False (no trustworthy reproducer signal)", green=False))
        return SolveResult(diff=patch.diff, green=False, attempts=1, cost_usd=round(cost, 6),
                           trajectory=traj, reproducer_valid=False)

    # 2b. Bounded attempt loop (reproducer is trustworthy here). Attempt 1 is
    #     fresh; later attempts replan from the previous reproducer failure.
    #
    #     Best-diff policy, measured not guessed: a green diff wins outright;
    #     otherwise the FIRST diff that applied in the sandbox is kept, never
    #     the latest. The loop-100 campaign scored 41/100 keeping the latest
    #     attempt while same-scaffold single-shot scored 44/100 on the same
    #     instances — when the reproducer rejects a patch it cannot actually
    #     judge, a feedback retry is a blind reroll, and blind rerolls lose
    #     to first answers.
    best_diff = ""
    feedback: str | None = None
    green = False
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        # cache=True: attempts 2+ re-read the identical issue+files prefix at
        # 0.1x instead of full price — the loop's main cost lever.
        patch = generate_patch(problem_statement, files, model=model, feedback=feedback,
                               cache=True, corpus=corpus)
        cost += patch.llm.cost_usd
        if not patch.diff:
            traj.append(Step("attempt", f"#{attempt}: no usable patch — {patch.error}", green=False))
            if diary:
                diary.record("developer", "failed", attempt=attempt, error=patch.error)
            feedback = f"You produced no applicable edits: {patch.error}. Re-read the files and try again."
            continue

        if diary:
            diary.record("developer", "succeeded", attempt=attempt, diff=patch.diff)
        repro = run_reproducer(image, base_commit, patch=patch.diff, script=script)
        if diary:
            diary.record("tester", "succeeded" if repro.green else "failed", attempt=attempt,
                         applied=repro.applied, exit_code=repro.exit_code,
                         output=repro.output[-2000:])
        traj.append(Step("attempt",
                         f"#{attempt}: applied={repro.applied} reproducer_exit={repro.exit_code}",
                         green=repro.green))
        if repro.green:  # trustworthy: the reproducer went red on base
            best_diff = patch.diff
            green = True
            break
        if not best_diff and (repro.applied or not patch.error):
            best_diff = patch.diff  # first applying diff stands unless green beats it
        feedback = _feedback(patch.diff, repro)

    traj.append(Step("result", f"green={green} after {attempt} attempt(s)", green=green))
    if diary:
        diary.record("loop", "succeeded" if green else "failed", attempt=attempt,
                     green=green, attempts=attempt, cost_usd=round(cost, 6))
    return SolveResult(diff=best_diff, green=green, attempts=attempt, cost_usd=round(cost, 6),
                       trajectory=traj, reproducer_valid=reproducer_valid)
