"""The replan loop's correctness properties, with the LLM and Docker faked out.

The property that matters most: the loop must NEVER report green off a
reproducer that didn't first go red on the unpatched base. A reproducer that
passes on base cannot tell a fix from nothing, so its "green" is not evidence —
trusting it would manufacture a fake resolve.
"""

import pytest

from fixpoint.agent import loop as loop_mod
from fixpoint.agent.llm import LLMResult
from fixpoint.agent.patcher import PatchResult
from fixpoint.harness.sandbox import ReproResult

DIFF = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a\n+b\n"


def _llm(cost=0.001):
    return LLMResult(text="x", input_tokens=10, output_tokens=5, cost_usd=cost, model="fake")


@pytest.fixture
def fakes(monkeypatch):
    """Wire fake reproducer/patcher/sandbox into the loop and record calls."""
    calls = {"patch_feedback": [], "repro_patches": [], "patch_cache": []}

    def fake_reproducer(problem_statement, files, *, model=None):
        return "print('repro')\n", _llm()

    def fake_patch(problem_statement, files, *, model=None, feedback=None, cache=False,
                   corpus=None):
        calls["patch_feedback"].append(feedback)
        calls["patch_cache"].append(cache)
        return PatchResult(diff=DIFF, edits=[], llm=_llm(), error=None)

    monkeypatch.setattr(loop_mod, "generate_reproducer", fake_reproducer)
    monkeypatch.setattr(loop_mod, "generate_patch", fake_patch)
    return calls


def wire_sandbox(monkeypatch, base_exit: int, patched_exit: int, calls):
    """base_exit: reproducer exit with no patch. patched_exit: with a patch."""
    def fake_run(image, base_commit, patch="", script="", **kw):
        calls["repro_patches"].append(patch)
        code = base_exit if not patch else patched_exit
        applied = code != -2  # -2 is our stand-in for "patch failed to apply"
        return ReproResult(applied=applied, exit_code=code, green=(applied and code == 0),
                           output=f"exit {code}")
    monkeypatch.setattr(loop_mod, "run_reproducer", fake_run)


def solve():
    return loop_mod.solve("issue text", {"m.py": "a\n"}, "img", "abc123", max_attempts=3)


# --- the guard --------------------------------------------------------------

def test_broken_reproducer_never_reports_green(monkeypatch, fakes):
    """Reproducer passes on base (exit 0) => it detects nothing. Even though the
    patched run would also 'pass', the loop must NOT claim green."""
    wire_sandbox(monkeypatch, base_exit=0, patched_exit=0, calls=fakes)
    r = solve()
    assert r.reproducer_valid is False
    assert r.green is False, "false green: trusted a reproducer that never went red"
    assert r.attempts == 1, "should not burn replan attempts without a signal"
    assert r.diff == DIFF, "should still return a best-effort patch"


def test_reproducer_infra_failure_is_not_trusted(monkeypatch, fakes):
    """exit -1 means the run itself failed, not that the bug was detected."""
    wire_sandbox(monkeypatch, base_exit=-1, patched_exit=0, calls=fakes)
    r = solve()
    assert r.reproducer_valid is False
    assert r.green is False


# --- the happy path and the search behaviour --------------------------------

def test_valid_reproducer_green_on_first_attempt(monkeypatch, fakes):
    wire_sandbox(monkeypatch, base_exit=1, patched_exit=0, calls=fakes)
    r = solve()
    assert r.reproducer_valid is True
    assert r.green is True
    assert r.attempts == 1
    assert fakes["patch_feedback"] == [None], "first attempt must be a fresh (unconditioned) patch"


def test_failed_attempts_replan_with_feedback_then_stop_at_budget(monkeypatch, fakes):
    """Never green => burn exactly max_attempts, and every attempt after the
    first must be conditioned on the previous failure (search, not retry)."""
    wire_sandbox(monkeypatch, base_exit=1, patched_exit=1, calls=fakes)
    r = solve()
    assert r.green is False
    assert r.attempts == 3, "must respect the attempt budget"
    assert fakes["patch_feedback"][0] is None
    assert all(f is not None for f in fakes["patch_feedback"][1:]), "replans must carry feedback"
    assert "exit 1" in fakes["patch_feedback"][1], "feedback must include reproducer output"


def test_cost_accumulates_across_reproducer_and_attempts(monkeypatch, fakes):
    wire_sandbox(monkeypatch, base_exit=1, patched_exit=1, calls=fakes)
    r = solve()
    # 1 reproducer call + 3 patch attempts, each faked at $0.001.
    assert r.cost_usd == pytest.approx(0.004)


def test_base_check_runs_with_no_patch(monkeypatch, fakes):
    """The red-on-base check must genuinely test the UNPATCHED repo."""
    wire_sandbox(monkeypatch, base_exit=1, patched_exit=0, calls=fakes)
    solve()
    assert fakes["repro_patches"][0] == "", "first sandbox run must apply no patch"


def test_loop_uses_prompt_cache_on_every_attempt(monkeypatch, fakes):
    """The loop re-sends an identical issue+files prefix each attempt; caching
    it turns attempts 2+ into 0.1x reads. Without this the loop costs ~2x."""
    wire_sandbox(monkeypatch, base_exit=1, patched_exit=1, calls=fakes)
    solve()
    assert fakes["patch_cache"] == [True, True, True]
