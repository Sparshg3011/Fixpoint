"""Single-shot patcher: issue + candidate files -> validated unified diff.

Flow:
  1. Build a prompt from the AgentView (issue text) and the retrieved files.
  2. Ask the model for SEARCH/REPLACE edits (never a raw diff — see edits.py).
  3. Parse the edits and synthesize a git-applyable diff from the REAL files.

The model is told, explicitly and structurally, that it may edit only the files
we hand it and must reproduce SEARCH text verbatim. Step 4 wraps this in the
bounded replan loop; here it runs exactly once.
"""

from __future__ import annotations

from dataclasses import dataclass

from fixpoint.agent.edits import Edit, parse_edits, synthesize_diff
from fixpoint.agent.llm import DEFAULT_MODEL, LLMResult, call

SYSTEM = r"""You are an expert software engineer fixing a bug in a large codebase.
You are given a GitHub issue and the full text of the few files most likely to
contain the bug. Find the root cause and fix it with the smallest correct change.

Output ONLY edit blocks. Each block is exactly five parts, in order:
1. a line containing only the file path, written plainly (no XML tags, no
   angle brackets, no backticks, no quotes) — for example:
       django/contrib/auth/validators.py
2. a line containing only: <<<<<<< SEARCH
3. the exact original lines, copied verbatim from the file above
4. a line containing only: =======
5. the replacement lines, then a line containing only: >>>>>>> REPLACE

Concrete example of one complete block:

django/contrib/auth/validators.py
<<<<<<< SEARCH
    regex = r'^[\w.@+-]+$'
=======
    regex = r'^[\w.@+-]+\Z'
>>>>>>> REPLACE

Hard rules:
- Write the file path exactly as shown in the "## " heading above each file —
  the bare path and nothing else on that line.
- Copy the SEARCH text EXACTLY from the file — same indentation, same
  whitespace, same line breaks — so it can be located. Include enough lines to
  be unique (3-8).
- NEVER abbreviate. Do not write "..." or "# rest of function" or otherwise
  skip lines inside a SEARCH block: it is matched literally against the file,
  so an elided line means it will never be found. If a region is too long to
  copy, choose a shorter one.
- Use the markers exactly as shown: `<<<<<<< SEARCH`, `=======`, and
  `>>>>>>> REPLACE`, each alone on its own line.
- Only edit files that were shown to you. Never edit tests.
- Prefer the minimal change that fixes the root cause. Don't reformat
  unrelated code.
- Output nothing but the edit blocks: no prose, no explanation, no code fences."""


@dataclass(frozen=True)
class PatchResult:
    diff: str  # the synthesized unified diff ("" if the model proposed nothing usable)
    edits: list[Edit]
    llm: LLMResult
    error: str | None  # set if edits couldn't be located; drives the replan loop


def _stable_prompt(problem_statement: str, files: dict[str, str]) -> str:
    """The big, unchanging head of the user turn: the issue and the files.

    Identical on every attempt for a given instance, so it is exactly what the
    prompt cache should hold. Keep it byte-stable — any change anywhere in this
    text invalidates the cache for the whole prefix.
    """
    parts = [f"# GitHub issue\n\n{problem_statement.strip()}\n", "# Candidate files\n"]
    for path, content in files.items():
        # Fence each file with its path so the model can address edits by path.
        parts.append(f"\n## {path}\n```python\n{content}\n```\n")
    return "\n".join(parts)


def _volatile_prompt(feedback: str | None) -> str:
    """The short, per-attempt tail. Must come AFTER the cached prefix."""
    if feedback:
        # Replan turn: the previous attempt and why it failed. Conditioning the
        # next patch on the concrete failure is what makes this search, not
        # blind retry — the whole point of the loop.
        return (f"\n# Your previous attempt did not work\n{feedback}\n"
                "\nProduce a BETTER set of edit blocks that fixes the issue, "
                "taking the failure above into account.")
    return "\nProduce the edit blocks that fix the issue."


def generate_patch(problem_statement: str, files: dict[str, str], *,
                   model: str = DEFAULT_MODEL, feedback: str | None = None,
                   cache: bool = False) -> PatchResult:
    """One LLM round-trip; parse and synthesize. Never raises on a bad patch —
    it returns error text so the caller (the loop) can react.

    `feedback` (prior attempt + failure output) makes this a replan turn rather
    than a fresh attempt. `cache` puts the issue+files prefix in the prompt
    cache — set it when the same prefix will be sent again (the replan loop),
    not for a one-shot call, which would pay the 1.25x write for no read.
    """
    stable, volatile = _stable_prompt(problem_statement, files), _volatile_prompt(feedback)
    if cache:
        result = call(SYSTEM, volatile, model=model, cache_prefix=stable)
    else:
        result = call(SYSTEM, stable + volatile, model=model)
    edits = parse_edits(result.text)
    if not edits:
        return PatchResult(diff="", edits=[], llm=result, error="model produced no edit blocks")
    try:
        diff = synthesize_diff(files, edits)
    except Exception as e:  # EditApplyError and friends — a real, reportable failure
        return PatchResult(diff="", edits=edits, llm=result, error=str(e))
    if not diff:
        return PatchResult(diff="", edits=edits, llm=result, error="edits produced no net change")
    return PatchResult(diff=diff, edits=edits, llm=result, error=None)
