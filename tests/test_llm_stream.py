"""The streaming client's pure parts, plus the truncation diagnosis.

_drain_sse is deliberately a pure function over lines so the SSE contract is
testable without a socket: content deltas accumulate, reasoning deltas are
ignored, usage rides the final chunk, torn frames don't poison the stream.
"""

import json

from fixpoint.agent.llm import LLMResult, _drain_sse
from fixpoint.agent.patcher import generate_patch


def _chunk(**kw) -> str:
    return "data: " + json.dumps(kw)


def test_drain_sse_accumulates_content_and_usage():
    lines = [
        _chunk(choices=[{"delta": {"role": "assistant"}}]),
        _chunk(choices=[{"delta": {"reasoning_content": "thinking..."}}]),
        _chunk(choices=[{"delta": {"content": "Hello "}}]),
        ": keep-alive comment",
        "",
        _chunk(choices=[{"delta": {"content": "world"}, "finish_reason": "stop"}]),
        _chunk(choices=[], usage={"prompt_tokens": 7, "completion_tokens": 3}),
        "data: [DONE]",
    ]
    text, usage, finish = _drain_sse(lines)
    assert text == "Hello world"          # reasoning deltas never leak into text
    assert usage == {"prompt_tokens": 7, "completion_tokens": 3}
    assert finish == "stop"


def test_drain_sse_survives_torn_frames():
    lines = [_chunk(choices=[{"delta": {"content": "ok"}}]), 'data: {"torn', "data: [DONE]"]
    text, usage, finish = _drain_sse(lines)
    assert text == "ok" and usage == {} and finish == ""


def test_truncated_response_is_diagnosed_not_blamed(monkeypatch):
    """A reasoning model that burns max_tokens thinking produces no blocks;
    the error must say TRUNCATED, not 'model produced no edit blocks'."""
    import fixpoint.agent.patcher as patcher_mod

    def fake_call(system, user, **kw):
        return LLMResult(text="", input_tokens=10, output_tokens=8000,
                         cost_usd=0.0, model="m", finish_reason="length")

    monkeypatch.setattr(patcher_mod, "call", fake_call)
    result = generate_patch("issue", {"f.py": "x = 1\n"})
    assert result.diff == ""
    assert "truncated at max_tokens" in result.error


def test_truncation_triggers_one_retry_at_double_budget(monkeypatch):
    """Truncated-with-no-edits retries once at 2x max_tokens; the merged
    LLMResult must account for BOTH calls so cost totals stay truthful."""
    import fixpoint.agent.patcher as patcher_mod

    calls = []

    def fake_call(system, user, **kw):
        calls.append(kw.get("max_tokens"))
        if len(calls) == 1:
            return LLMResult(text="", input_tokens=100, output_tokens=8000,
                             cost_usd=0.0, model="m", finish_reason="length")
        return LLMResult(
            text="f.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n",
            input_tokens=100, output_tokens=50, cost_usd=0.0, model="m",
            finish_reason="stop")

    monkeypatch.setattr(patcher_mod, "call", fake_call)
    result = generate_patch("issue", {"f.py": "x = 1\n"})
    assert "+x = 2" in result.diff
    assert calls == [None, patcher_mod.DEFAULT_MAX_TOKENS * 2]
    assert result.llm.output_tokens == 8050  # both calls accounted
