"""The visibility firewall and the dataset fingerprint.

The firewall is the project's integrity claim: if the agent can see the
reference solution or the graded tests, every number is fiction. These tests
assert the boundary mechanically, so it can't erode by accident.
"""

import dataclasses

from fixpoint.bench import AgentView, Instance, agent_view, fingerprint

FORBIDDEN = {"gold_patch", "test_patch", "fail_to_pass", "pass_to_pass", "hints_text"}


def make(instance_id="a__a-1", base_commit="c0ffee"):
    return Instance(
        instance_id=instance_id, repo="a/a", base_commit=base_commit,
        environment_setup_commit="env1", version="1.0",
        problem_statement="the bug", hints_text="a leaked hint",
        created_at="2020-01-01T00:00:00Z",
        gold_patch="THE ANSWER", test_patch="THE GRADED TESTS",
        fail_to_pass=("t1",), pass_to_pass=("t2",),
    )


def test_agent_view_exposes_only_the_four_allowed_fields():
    fields = {f.name for f in dataclasses.fields(AgentView)}
    assert fields == {"instance_id", "repo", "base_commit", "problem_statement"}


def test_agent_view_carries_no_grading_data():
    av = agent_view(make())
    for value in dataclasses.asdict(av).values():
        assert "THE ANSWER" not in str(value)
        assert "THE GRADED TESTS" not in str(value)


def test_agent_view_has_no_forbidden_attributes():
    av = agent_view(make())
    for name in FORBIDDEN:
        assert not hasattr(av, name), f"AgentView leaks {name}"


def test_agent_view_excludes_hints_text():
    """hints_text is excluded by convention — using it inflates results versus
    every published baseline."""
    av = agent_view(make())
    assert "a leaked hint" not in str(dataclasses.asdict(av))


def test_instance_and_agent_view_are_frozen():
    """Grading inputs must be immutable so a view can't be mutated into a leak."""
    assert dataclasses.fields(Instance) and Instance.__dataclass_params__.frozen
    assert AgentView.__dataclass_params__.frozen


def test_fingerprint_is_order_independent():
    a, b = make("x-1", "aaa"), make("y-2", "bbb")
    assert fingerprint([a, b]) == fingerprint([b, a])


def test_fingerprint_changes_with_commit():
    assert fingerprint([make("x-1", "aaa")]) != fingerprint([make("x-1", "bbb")])


def test_fingerprint_changes_with_instance_set():
    assert fingerprint([make("x-1")]) != fingerprint([make("x-1"), make("y-2")])


def test_pinned_loader_refuses_a_drifted_split(monkeypatch):
    """Any dataset served through _load_pinned must match its fingerprint —
    an unpinned copy makes every published number unverifiable."""
    import pytest

    import fixpoint.bench.loader as loader

    fake_rows = [{
        "instance_id": "r__r-1", "repo": "r/r", "base_commit": "a" * 40,
        "environment_setup_commit": "b" * 40, "version": "1.0",
        "problem_statement": "x", "hints_text": "", "created_at": "",
        "patch": "p", "test_patch": "t", "FAIL_TO_PASS": "[]", "PASS_TO_PASS": "[]",
    }]
    monkeypatch.setattr(loader, "load_dataset", lambda *a, **kw: fake_rows)
    with pytest.raises(RuntimeError, match="pinned fingerprint"):
        loader._load_pinned("fake/DS", "0" * 64, "test", True)
    # and serves it fine when the caller opts out (dev-split semantics)
    assert loader._load_pinned("fake/DS", "0" * 64, "test", False)[0].instance_id == "r__r-1"
