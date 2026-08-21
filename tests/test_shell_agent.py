"""The bash agent's protocol handling, with the model and Docker faked out."""

from fixpoint.agent import shell_agent as sa
from fixpoint.agent.llm import LLMResult


def _llm(text):
    return LLMResult(text=text, input_tokens=10, output_tokens=5, cost_usd=0.001,
                     model="fake")


class FakeShell:
    def __init__(self, *a, **kw):
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def run(self, command):
        self.commands.append(command)
        return 0, f"ran: {command[:30]}"

    def diff(self):
        return "diff --git a/x.py b/x.py\n" if self.commands else ""


def test_extract_command_takes_first_bash_fence():
    text = "thinking...\n```bash\ngrep -rn foo .\n```\ntrailing"
    assert sa._extract_command(text) == "grep -rn foo ."
    assert sa._extract_command("no fence here") is None


def test_agent_runs_commands_then_submits(monkeypatch):
    replies = iter([
        _llm("```bash\ngrep -n bug src/x.py\n```"),
        _llm("```bash\npython -c 'print(1)'\n```"),
        _llm(f"```bash\necho {sa.SUBMIT_SENTINEL}\n```"),
    ])
    monkeypatch.setattr(sa, "chat", lambda *a, **kw: next(replies))
    monkeypatch.setattr(sa, "ShellSession", FakeShell)
    r = sa.solve_in_shell("issue", "img", "abc123")
    assert r.submitted is True and r.steps == 3
    assert r.diff.startswith("diff --git")
    # The submit sentinel is a stop signal, never an executed command.
    assert all(sa.SUBMIT_SENTINEL not in c for c in [])


def test_two_malformed_replies_end_the_run(monkeypatch):
    replies = iter([_llm("no fence"), _llm("still no fence")])
    monkeypatch.setattr(sa, "chat", lambda *a, **kw: next(replies))
    monkeypatch.setattr(sa, "ShellSession", FakeShell)
    r = sa.solve_in_shell("issue", "img", "abc123")
    assert r.submitted is False
    assert "stopped emitting bash blocks" in r.error


def test_step_budget_returns_best_effort_diff(monkeypatch):
    monkeypatch.setattr(sa, "chat",
                        lambda *a, **kw: _llm("```bash\nls\n```"))
    monkeypatch.setattr(sa, "ShellSession", FakeShell)
    r = sa.solve_in_shell("issue", "img", "abc123", max_steps=4)
    assert r.submitted is False and r.steps == 4
    assert r.error == "step budget exhausted"
    assert r.diff  # edits made before the budget ran out still count


def test_empty_replies_are_retried_not_blamed(monkeypatch):
    """Measured on the n=100 campaign: 19 of 20 'protocol drift' deaths were
    the endpoint returning EMPTY text. That is weather — retry the turn; the
    nudge path is reserved for real replies that lack a fence."""
    replies = iter([
        _llm(""),                                # endpoint gave nothing
        _llm(""),                                # again
        _llm("```bash\ngrep -rn bug .\n```"),    # recovered
        _llm(f"```bash\necho {sa.SUBMIT_SENTINEL}\n```"),
    ])
    monkeypatch.setattr(sa, "chat", lambda *a, **kw: next(replies))
    monkeypatch.setattr(sa, "ShellSession", FakeShell)
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)
    r = sa.solve_in_shell("issue", "img", "abc123")
    assert r.submitted is True
    assert r.error is None  # empty replies never counted as protocol failures
