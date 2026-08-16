"""The run diary — the contract the UI renders.

A diary that is wrong or unparseable makes the whole UI wrong, and it is
written while a run is in flight, so partial files must stay readable.
"""

import json

import pytest

from fixpoint.diary import MAX_DETAIL_CHARS, Diary, read


def test_records_one_json_object_per_line(tmp_path):
    d = Diary("r1", "django__django-1", runs_dir=tmp_path)
    d.record("retrieval", "succeeded", files=["a.py"])
    d.record("developer", "failed", attempt=1, error="nope")
    lines = d.path.read_text().splitlines()
    assert len(lines) == 2
    assert all(json.loads(ln) for ln in lines)


def test_events_carry_stage_event_and_timestamp(tmp_path):
    d = Diary("r1", "i1", runs_dir=tmp_path)
    ev = d.record("tester", "succeeded", attempt=2, exit_code=0)
    assert ev.stage == "tester" and ev.event == "succeeded"
    assert ev.attempt == 2 and ev.ts > 0
    assert ev.detail["exit_code"] == 0


@pytest.mark.parametrize("stage,event", [("bogus", "started"), ("tester", "exploded")])
def test_unknown_vocabulary_fails_loudly(tmp_path, stage, event):
    """A typo that produces an unrenderable diary must crash, not pass."""
    d = Diary("r1", "i1", runs_dir=tmp_path)
    with pytest.raises(ValueError):
        d.record(stage, event)


def test_large_payloads_are_truncated(tmp_path):
    """Whole files would make the diary unshippable; tails are the point."""
    d = Diary("r1", "i1", runs_dir=tmp_path)
    ev = d.record("tester", "failed", output="x" * (MAX_DETAIL_CHARS * 3))
    assert len(ev.detail["output"]) < MAX_DETAIL_CHARS + 100
    assert "truncated" in ev.detail["output"]


def test_read_round_trips(tmp_path):
    d = Diary("r1", "i1", runs_dir=tmp_path)
    d.record("retrieval", "started")
    d.record("loop", "succeeded", green=True)
    events = read(d.path)
    assert [e.stage for e in events] == ["retrieval", "loop"]
    assert events[1].detail["green"] is True


def test_read_tolerates_a_partial_final_line(tmp_path):
    """A run still writing (or killed) must still be viewable."""
    d = Diary("r1", "i1", runs_dir=tmp_path)
    d.record("retrieval", "started")
    with d.path.open("a") as f:
        f.write('{"ts": 1.0, "run_id": "r1", "sta')  # truncated mid-write
    events = read(d.path)
    assert len(events) == 1 and events[0].stage == "retrieval"


def test_loop_emits_diary_events(tmp_path, monkeypatch):
    """End-to-end: a solve() run produces a renderable event stream."""
    from fixpoint.agent import loop as loop_mod
    from fixpoint.agent.llm import LLMResult
    from fixpoint.agent.patcher import PatchResult
    from fixpoint.harness.sandbox import ReproResult

    llm = LLMResult(text="x", input_tokens=1, output_tokens=1, cost_usd=0.0, model="fake")
    monkeypatch.setattr(loop_mod, "generate_reproducer", lambda *a, **k: ("s", llm))
    monkeypatch.setattr(loop_mod, "generate_patch",
                        lambda *a, **k: PatchResult(diff="d", edits=[], llm=llm, error=None))
    monkeypatch.setattr(loop_mod, "run_reproducer",
                        lambda image, base, patch="", script="", **k: ReproResult(
                            applied=True, exit_code=0 if patch else 1,
                            green=bool(patch), output="out"))

    d = Diary("r1", "i1", runs_dir=tmp_path)
    loop_mod.solve("issue", {"m.py": "a\n"}, "img", "abc", diary=d)
    stages = [e.stage for e in read(d.path)]
    assert "reproducer" in stages and "developer" in stages
    assert "tester" in stages and "loop" in stages
