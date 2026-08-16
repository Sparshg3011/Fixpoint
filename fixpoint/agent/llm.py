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

# Default model: GLM-5.2, free via NVIDIA NIM. Chosen on measured evidence, not
# vibes — of the models tested it matched the frontier model's conditional
# apply rate (94%) and led on raw apply rate, at zero cost. Override with
# FIXPOINT_MODEL.
DEFAULT_MODEL = os.environ.get("FIXPOINT_MODEL", "z-ai/glm-5.2")

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


def _call_openai_compatible(system: str, user: str, *, model: str, max_tokens: int,
                            base_url: str, max_retries: int = 9) -> LLMResult:
    """One /chat/completions call against any OpenAI-compatible endpoint.

    Uses httpx directly (already present via the anthropic SDK) rather than
    adding the openai package — the request is a small JSON body and doing it
    by hand keeps the dependency list honest.

    Free tiers rate-limit aggressively and by current traffic, so 429s and 5xx
    are retried with exponential backoff rather than failing an entire run.
    """
    import random
    import time

    import httpx

    # Any of these keys is accepted so a user can name it after their provider.
    key = next((os.environ[k] for k in
                ("FIXPOINT_API_KEY", "NVIDIA_API_KEY", "ZAI_API_KEY", "OPENAI_API_KEY")
                if os.environ.get(k)), "")
    if not base_url:
        raise RuntimeError("FIXPOINT_BACKEND=openai needs FIXPOINT_BASE_URL set")

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    headers = {"Content-Type": "application/json"}
    if key:  # a local Ollama server needs no key
        headers["Authorization"] = f"Bearer {key}"

    last = ""
    for attempt in range(max_retries):
        resp = httpx.post(f"{base_url.rstrip('/')}/chat/completions",
                          json=payload, headers=headers, timeout=600.0)
        if resp.status_code == 200:
            data = resp.json()
            msg = data["choices"][0]["message"]
            # Reasoning models may put the answer in content and their chain of
            # thought elsewhere; we only ever want content.
            text = msg.get("content") or ""
            usage = data.get("usage") or {}
            in_tok = usage.get("prompt_tokens", 0)
            out_tok = usage.get("completion_tokens", 0)
            return LLMResult(text=text, input_tokens=in_tok, output_tokens=out_tok,
                             cost_usd=_cost(model, in_tok, out_tok), model=model)
        last = f"{resp.status_code}: {resp.text[:200]}"
        if resp.status_code == 429 or resp.status_code >= 500:
            # Free tiers throttle on TOKENS, not just requests, and our prompts
            # are large — so a 429 can persist for a while. Honor Retry-After
            # when the server sends it; otherwise back off exponentially with a
            # 90s ceiling plus jitter so parallel workers don't retry in lockstep.
            hinted = resp.headers.get("retry-after")
            delay = float(hinted) if (hinted or "").replace(".", "", 1).isdigit() \
                else min(2 ** attempt, 90)
            time.sleep(delay + random.uniform(0, 2))
            continue
        break  # 4xx other than 429 will not fix itself
    raise RuntimeError(f"{base_url} call failed after {max_retries} attempts — {last}")


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
