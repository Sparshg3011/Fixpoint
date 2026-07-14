"""Thin Anthropic client wrapper: one model call, with cost accounting.

Kept deliberately small. The agent needs exactly one thing from the LLM — send
a system + user prompt, get text back, know what it cost — so that's all this
exposes. The client is constructed lazily inside call() so importing this
module (and running the offline diff tests) needs no API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Default model. Sonnet 5 is the iteration workhorse: near-Opus on coding at
# ~60% the price, which matters when we sweep 25-300 instances repeatedly.
# Override per run with FIXPOINT_MODEL (e.g. claude-opus-4-8 for a headline run).
DEFAULT_MODEL = os.environ.get("FIXPOINT_MODEL", "claude-sonnet-5")

# USD per 1M tokens (input, output), from the pinned model catalog. Used only
# to attach a dollar figure to each call — the harness bills nothing.
_PRICING = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str


def _cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = _PRICING.get(model, (0.0, 0.0))
    return round(input_tokens / 1e6 * in_price + output_tokens / 1e6 * out_price, 6)


def call(system: str, user: str, *, model: str = DEFAULT_MODEL, max_tokens: int = 8000) -> LLMResult:
    """One non-streaming message. Returns the concatenated text and its cost.

    max_tokens 8000 stays under the SDK's ~16k non-streaming timeout guard; a
    single-file patch never needs more. We omit the `thinking` parameter — the
    per-model default is fine for a first single-shot patcher, and we add effort
    only if the resolve rate says it pays.
    """
    import anthropic  # local import: no dependency at module import time

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    # content is a list of blocks (text / thinking / ...). Concatenate the text
    # blocks; ignore any thinking blocks a model may emit.
    text = "".join(b.text for b in resp.content if b.type == "text")
    return LLMResult(
        text=text,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cost_usd=_cost(model, resp.usage.input_tokens, resp.usage.output_tokens),
        model=model,
    )
