"""Thin Anthropic client wrapper: one model call, with cost accounting.

Kept deliberately small. The agent needs exactly one thing from the LLM — send
a system + user prompt, get text back, know what it cost — so that's all this
exposes. The client is constructed lazily inside call() so importing this
module (and running the offline diff tests) needs no API key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Which API to talk to. Default is "openai" — meaning any OpenAI-compatible
# endpoint — because Fixpoint runs on FREE open-weight models: NVIDIA NIM
# serves GLM, Nemotron, DeepSeek and others at no cost, and the measured
# results show an open model reaching most of a frontier model's resolve rate
# on this scaffold. "anthropic" remains available for a paid comparison run.
BACKEND = os.environ.get("FIXPOINT_BACKEND", "openai")

# Base URL for the openai backend, e.g.
#   NVIDIA NIM  https://integrate.api.nvidia.com/v1   (free)
#   Z.ai (GLM)  https://api.z.ai/api/paas/v4
#   Ollama      http://localhost:11434/v1             (local, free)
BASE_URL = os.environ.get("FIXPOINT_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Default model, free via NVIDIA NIM. GLM-5.2 held this slot on measured
# evidence until NIM retired it (410 Gone, EOL 2026-08-21) — free-tier models
# are mortal, which is why every run records its exact model id. Kimi K3
# succeeds it: its predecessor K2.5 out-scores the GLM family on the current
# SWE-bench Verified board. Override with FIXPOINT_MODEL.
DEFAULT_MODEL = os.environ.get("FIXPOINT_MODEL", "moonshotai/kimi-k3")

# USD per 1M tokens (input, output). Used only to attach a dollar figure to each
# call — the harness bills nothing. Unknown models price at 0.0, which is the
# correct answer for a free endpoint (NVIDIA NIM) or a local server.
_PRICING = {
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Z.ai published rates. NVIDIA NIM's free tier is genuinely $0, so its
    # models are deliberately absent and price at zero.
    "glm-4.6": (0.60, 2.20),
    "glm-4.5-air": (0.20, 1.10),
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
    # Why the model stopped: "stop" is a complete answer; "length" means the
    # output was TRUNCATED at max_tokens — for a reasoning model that can mean
    # the whole budget went to chain-of-thought and the content is empty, which
    # otherwise looks identical to "the model produced no edit blocks".
    finish_reason: str = ""


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


# Ceiling on any single retry sleep. A free tier once hinted Retry-After in the
# THOUSANDS of seconds; honoring it verbatim parked every worker for hours and
# looked exactly like a hung run (10h wall, 80s CPU). We retry sooner and let
# the server 429 us again if it must — bounded impatience beats unbounded sleep.
_MAX_BACKOFF_S = 90.0

# Total wall-clock budget for ONE call including all retries and queue time.
# Without this, 9 retries x (read timeout + backoff) lets a single instance
# consume over an hour; with it, the worst case is bounded and visible.
_CALL_BUDGET_S = float(os.environ.get("FIXPOINT_CALL_BUDGET_S", "2400"))


def _drain_sse(lines) -> tuple[str, dict, str]:
    """Fold an OpenAI-style SSE stream into (text, usage, finish_reason).

    Pure function over an iterable of lines so it is testable without a socket.
    Reasoning models interleave `reasoning_content` deltas with `content`
    deltas; only content is the answer, so only content is kept.
    """
    import json as _json

    parts: list[str] = []
    usage: dict = {}
    finish = ""
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue  # keep-alive comments and blank separators
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            chunk = _json.loads(data)
        except ValueError:
            continue  # torn frame from a flaky proxy — nothing to salvage
        if chunk.get("usage"):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                parts.append(delta["content"])
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
    return "".join(parts), usage, finish


def chat(system: str, messages: list[dict], *, model: str = DEFAULT_MODEL,
         max_tokens: int | None = None) -> LLMResult:
    """One turn of a multi-turn conversation — the bash agent's primitive.

    `messages` is the running transcript ([{role, content}, ...]); the caller
    owns it and appends both sides after each turn. Rides the same streaming/
    retry/budget machinery as call(). OpenAI-compatible backends only: the
    interactive agent exists to drive free open-weight endpoints, and single
    calls remain available for everything else.
    """
    if BACKEND != "openai":
        raise RuntimeError("chat() requires FIXPOINT_BACKEND=openai")
    return _call_openai_compatible(
        system, messages, model=model,
        max_tokens=max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS,
        base_url=BASE_URL)


def _call_openai_compatible(system: str, user: str | list[dict], *, model: str,
                            max_tokens: int, base_url: str,
                            max_retries: int = 9) -> LLMResult:
    """One /chat/completions call against any OpenAI-compatible endpoint.

    Uses httpx directly (already present via the anthropic SDK) rather than
    adding the openai package — the request is a small JSON body and doing it
    by hand keeps the dependency list honest.

    STREAMING by design, not for UX: a 550B model generating thousands of
    tokens can legitimately take longer than any sane whole-body read timeout,
    and the free tier adds queue time on top. With streaming, the read timeout
    applies BETWEEN chunks — a slow-but-alive generation never trips it, while
    a genuinely dead connection still fails fast and gets retried.

    Three failure classes are retried with capped backoff: 429s (free tiers
    throttle on tokens, not requests), 5xx, and transport errors (timeouts,
    resets — the previous version let these propagate and crash the instance).
    Everything is bounded by a per-call wall-clock budget so no single instance
    can silently absorb hours.
    """
    import json as _json
    import random
    import time

    import httpx

    # Any of these keys is accepted so a user can name it after their provider.
    key = next((os.environ[k] for k in
                ("FIXPOINT_API_KEY", "NVIDIA_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY")
                if os.environ.get(k)), "")
    if not base_url:
        raise RuntimeError("FIXPOINT_BACKEND=openai needs FIXPOINT_BASE_URL set")

    # `user` is either a single user turn (str) or a whole running transcript
    # (list of {role, content}) from chat().
    turns = user if isinstance(user, list) else [{"role": "user", "content": user}]
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": True,
        # Ask for token counts on the final chunk; servers that predate this
        # option 400 on it and we fall back to non-streaming below.
        "stream_options": {"include_usage": True},
        "messages": [{"role": "system", "content": system}, *turns],
    }
    headers = {"Content-Type": "application/json"}
    if key:  # a local Ollama server needs no key
        headers["Authorization"] = f"Bearer {key}"

    url = f"{base_url.rstrip('/')}/chat/completions"
    # connect/write fail fast; read is per-chunk while streaming, sized for the
    # free tier's queue-before-first-token delay rather than a whole generation.
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0)
    deadline = time.monotonic() + _CALL_BUDGET_S

    last = ""
    for attempt in range(max_retries):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last = f"{last} [call budget {_CALL_BUDGET_S:.0f}s exhausted]".strip()
            break
        try:
            with httpx.stream("POST", url, json=payload, headers=headers,
                              timeout=timeout) as resp:
                if resp.status_code == 200:
                    if payload.get("stream"):
                        text, usage, finish = _drain_sse(resp.iter_lines())
                    else:  # non-streaming fallback path (see 400 handling)
                        data = _json.loads(resp.read())
                        msg = data["choices"][0]["message"]
                        text = msg.get("content") or ""
                        usage = data.get("usage") or {}
                        finish = data["choices"][0].get("finish_reason") or ""
                    in_tok = usage.get("prompt_tokens", 0)
                    out_tok = usage.get("completion_tokens", 0)
                    return LLMResult(text=text, input_tokens=in_tok, output_tokens=out_tok,
                                     cost_usd=_cost(model, in_tok, out_tok), model=model,
                                     finish_reason=finish)
                body = resp.read().decode("utf-8", "replace")[:300]
                last = f"{resp.status_code}: {body}"
                if resp.status_code == 400 and payload.get("stream") and "stream" in body:
                    # The server rejected streaming or stream_options — drop
                    # them and retry the same request the old-fashioned way.
                    payload.pop("stream", None)
                    payload.pop("stream_options", None)
                    continue
                # Retryable: 429 (throttling), 5xx (server), and 404 — which on
                # a free NIM tier is usually "the model is being cycled out of
                # capacity right now", not "this model does not exist". A truly
                # wrong model id costs the bounded retry ladder once per run,
                # a price worth paying to survive the transient kind (measured:
                # 8 consecutive instances lost to one 404 window mid-campaign).
                if not (resp.status_code in (404, 429) or resp.status_code >= 500):
                    break  # other 4xx will not fix itself
                hinted = resp.headers.get("retry-after")
                try:
                    delay = min(float(hinted), _MAX_BACKOFF_S)
                except (TypeError, ValueError):
                    delay = min(2.0 ** attempt, _MAX_BACKOFF_S)
        except httpx.HTTPError as e:
            # Read timeout, reset, DNS blip — transient by nature. Same capped
            # backoff as a 5xx; crashing the whole instance on one flaky socket
            # (the old behavior) threw away a finished retrieval for nothing.
            last = f"transport {type(e).__name__}: {e}"
            delay = min(2.0 ** attempt, _MAX_BACKOFF_S)
        # Jitter so parallel workers don't retry in lockstep; never sleep past
        # the budget deadline.
        time.sleep(max(0.0, min(delay + random.uniform(0, 2), deadline - time.monotonic())))
    raise RuntimeError(f"{url} call failed after {max_retries} attempts — {last}")


# Base output budget per call. 8000 was chosen for the SDK's non-streaming
# guard and "a single-file patch never needs more" — which held until reasoning
# models: measured on the Nemotron-Ultra Lite-300 run, 64/300 responses burned
# the whole budget on chain-of-thought and truncated before any edit block.
# The patcher now retries once at double this on truncation (see patcher.py).
DEFAULT_MAX_TOKENS = int(os.environ.get("FIXPOINT_MAX_TOKENS", "8000"))


def call(system: str, user: str, *, model: str = DEFAULT_MODEL,
         max_tokens: int = DEFAULT_MAX_TOKENS,
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
    # Non-Anthropic endpoints have no prompt-cache concept, so the stable
    # prefix is simply prepended — same prompt, just billed at full rate.
    if BACKEND == "openai":
        return _call_openai_compatible(
            system, (cache_prefix + user) if cache_prefix else user,
            model=model, max_tokens=max_tokens, base_url=BASE_URL)

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
