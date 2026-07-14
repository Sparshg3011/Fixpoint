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

SYSTEM = """You are an expert software engineer fixing a bug in a large codebase.
You are given a GitHub issue and the full text of the few files most likely to
contain the bug. Find the root cause and fix it with the smallest correct change.

Output ONLY edit blocks in exactly this format, one per change:

<path>
<<<<<<< SEARCH
<exact lines copied verbatim from the file, including indentation>
=======
<the replacement lines>
>>>>>>> REPLACE

Hard rules:
- The SEARCH text must be copied EXACTLY from the file shown — same
  indentation, same whitespace — so it can be found. Include enough lines to be
  unique (usually 3-8).
- Only edit files that were shown to you. Never edit tests.
- Prefer the minimal change that fixes the root cause. Do not reformat
  unrelated code.
- Output no prose, no explanation, no code fences around the blocks — only the
  edit blocks themselves."""


@dataclass(frozen=True)
class PatchResult:
    diff: str  # the synthesized unified diff ("" if the model proposed nothing usable)
    edits: list[Edit]
    llm: LLMResult
    error: str | None  # set if edits couldn't be located; drives the replan loop


def _build_user_prompt(problem_statement: str, files: dict[str, str]) -> str:
    parts = [f"# GitHub issue\n\n{problem_statement.strip()}\n", "# Candidate files\n"]
    for path, content in files.items():
        # Fence each file with its path so the model can address edits by path.
        parts.append(f"\n## {path}\n```python\n{content}\n```\n")
    parts.append("\nProduce the edit blocks that fix the issue.")
    return "\n".join(parts)


def generate_patch(problem_statement: str, files: dict[str, str], *, model: str = DEFAULT_MODEL) -> PatchResult:
    """One LLM round-trip; parse and synthesize. Never raises on a bad patch —
    it returns error text so the caller (the loop) can react."""
    result = call(SYSTEM, _build_user_prompt(problem_statement, files), model=model)
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
