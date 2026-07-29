"""The PR flow must never publish outside the user's own fork.

These patches target real OSS projects; opening a PR upstream would spam
maintainers with machine-generated code. The guard is asserted here so it
cannot be weakened by accident.
"""

import pytest

from fixpoint import pr as pr_mod


@pytest.fixture
def as_user(monkeypatch):
    monkeypatch.setattr(pr_mod, "current_user", lambda: "Sparshg3011")


@pytest.mark.parametrize("target", ["django/django", "sympy/sympy", "someoneelse/django"])
def test_refuses_targets_the_user_does_not_own(as_user, target):
    with pytest.raises(pr_mod.PRSafetyError, match="refusing"):
        pr_mod.assert_safe_target(target)


def test_allows_the_users_own_fork(as_user):
    pr_mod.assert_safe_target("Sparshg3011/django")  # must not raise


def test_owner_check_is_case_insensitive(as_user):
    pr_mod.assert_safe_target("sparshg3011/django")  # must not raise


def test_pr_body_states_the_firewall_and_verdict():
    body = pr_mod._pr_body("django__django-10914", "django/django", "abc123def456",
                           "The bug is X", resolved=True)
    assert "RESOLVED" in body
    assert "never the reference patch or the graded tests" in body
    assert "abc123def456"[:12] in body


def test_dry_run_takes_no_outward_action(monkeypatch, tmp_path):
    """A dry run must not create a fork, push, or open a PR."""
    calls = []
    monkeypatch.setattr(pr_mod, "current_user", lambda: "Sparshg3011")
    monkeypatch.setattr(pr_mod, "ensure_fork", lambda u: calls.append(("fork", u)) or "x/y")
    monkeypatch.setattr(pr_mod, "_gh", lambda *a, **k: calls.append(("gh", a)) or "")

    def fake_git(*args, cwd=None):
        if args[0] == "push":
            calls.append(("push", args))
        return ""
    monkeypatch.setattr(pr_mod, "_git", fake_git)
    monkeypatch.setattr(pr_mod, "bare_path", lambda repo: tmp_path)
    monkeypatch.setattr(pr_mod.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})())

    res = pr_mod.open_pr(upstream="django/django", base_commit="abc123", patch="diff",
                         instance_id="django__django-1", dry_run=True)
    assert res.dry_run is True
    assert not any(c[0] in {"fork", "push"} for c in calls), f"dry run acted outward: {calls}"
    assert not any(c[0] == "gh" and c[1] and c[1][0] == "pr" for c in calls)
