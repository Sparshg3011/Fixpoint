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


# Prompt-cache multipliers (Anthropic pricing): a cache WRITE costs 1.25x the
# base input rate, a cache READ only 0.1x. So re-sending an identical prefix
# breaks even on the second call and is a large win by the third — exactly the
# shape of the replan loop, which re-sends the same files each attempt.
_CACHE_WRITE_MULT = 1.25
_CACHE_READ_MULT = 0.10


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    cache_write_tokens: int = 0  # prefix written to cache this call (1.25x)
    cache_read_tokens: int = 0   # prefix served from cache this call (0.1x)


def _cost(model: str, input_tokens: int, output_tokens: int,
          cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Dollar cost of one call, honoring the cache read/write multipliers."""
    in_price, out_price = _PRICING.get(model, (0.0, 0.0))
    return round(
        (input_tokens
         + cache_write_tokens * _CACHE_WRITE_MULT
         + cache_read_tokens * _CACHE_READ_MULT) / 1e6 * in_price
        + output_tokens / 1e6 * out_price,
        6,
    )


def call(system: str, user: str, *, model: str = DEFAULT_MODEL, max_tokens: int = 8000,
         cache_prefix: str | None = None) -> LLMResult:
    """One non-streaming message. Returns the concatenated text and its cost.

    max_tokens 8000 stays under the SDK's ~16k non-streaming timeout guard; a
    single-file patch never needs more. We omit the `thinking` parameter — the
    per-model default is fine for a first single-shot patcher, and we add effort
    only if the resolve rate says it pays.

    `cache_prefix` is the large, STABLE head of the user turn (issue + files).
    When given it becomes its own content block with cache_control, and `user`
    holds only the volatile tail (e.g. replan feedback). Caching is a prefix
    match, so the stable part must come first and stay byte-identical between
    calls or nothing is reused. Only worth passing when the same prefix will be
    sent more than once — a lone call pays the 1.25x write for no read.
    """
    import anthropic  # local import: no dependency at module import time

    if cache_prefix is None:
        content: object = user
    else:
        content = [
            {"type": "text", "text": cache_prefix, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": user},
        ]

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    # content is a list of blocks (text / thinking / ...). Concatenate the text
    # blocks; ignore any thinking blocks a model may emit.
    text = "".join(b.text for b in resp.content if b.type == "text")
    # getattr: these usage fields are absent on responses that never touched the
    # cache, and on older SDK versions.
    writes = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
    reads = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
    return LLMResult(
        text=text,
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cost_usd=_cost(model, resp.usage.input_tokens, resp.usage.output_tokens, writes, reads),
        model=model,
        cache_write_tokens=writes,
        cache_read_tokens=reads,
    )
