"""Single-shot pipeline: one instance -> a candidate patch, end to end.

Grading-side module (it may read gold files to annotate results), but the
patching path it drives is strictly agent-side: the patcher sees only the
AgentView problem statement and the retrieved file contents. This is where
retrieval, patching, and sanitizing meet for the first time.

    retrieve top-K files (BM25)  ->  patcher (LLM, SEARCH/REPLACE)
      ->  synthesize unified diff  ->  verify it applies with real git apply
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from fixpoint.agent.patcher import generate_patch
from fixpoint.bench import Instance, agent_view
from fixpoint.eval.recall import first_hit_rank, gold_files
from fixpoint.retrieval import load_corpus, tree_at
from fixpoint.retrieval.bm25 import BM25Searcher
from fixpoint.retrieval.guided import resolve_requested_paths

# The `diff --git a/<path> b/<path>` header line names each file the diff edits.
_DIFF_GIT_RE = re.compile(r"^diff --git a/(\S+) b/\S+$", re.MULTILINE)


def _touched_paths(diff: str) -> list[str]:
    return _DIFF_GIT_RE.findall(diff)


def git_apply_check(tree: Path, diff: str) -> bool:
    """Real `git apply --check` — the harness's own first apply command — run in
    a throwaway repo seeded with just the files the diff touches at their real
    paths. Returns True iff git would apply it cleanly.
    """
    paths = _touched_paths(diff)
    if not paths:
        return False
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for rel in paths:
            src = tree / rel
            if not src.exists():
                return False
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(errors="replace"))
        (root / "p.diff").write_text(diff)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        return subprocess.run(["git", "apply", "--check", "p.diff"],
                              cwd=root, capture_output=True).returncode == 0


@dataclass(frozen=True)
class SingleShotResult:
    instance_id: str
    applied: bool          # git apply --check passed
    diff: str
    error: str | None      # patcher/sanitizer error, if any
    raw_response: str      # the model's raw output, kept for offline debugging
    cost_usd: float
    top_files: list[str]   # files actually shown to the model (incl. guided adds)
    gold_files: list[str]  # grading-side annotation (not shown to the agent)
    gold_retrieved_rank: int | None  # where the gold file landed in BM25's ranking
    input_tokens: int
    output_tokens: int
    # Defaulted fields must come last (dataclass ordering).
    guided_retrieval: bool = False  # did a model-requested file rescue this?


def run_single(inst: Instance, k: int = 5, model: str | None = None) -> SingleShotResult:
    """Retrieve, patch, synthesize, verify — for one instance."""
    av = agent_view(inst)  # firewall: the patcher never sees more than this
    tree = tree_at(inst.repo, inst.base_commit)
    docs = load_corpus(tree)
    by_path = {d.path: d.text for d in docs}

    ranked = [p for p, _ in BM25Searcher(docs).search(av.problem_statement, k=k)]
    files = {p: by_path[p] for p in ranked}

    kwargs = {"model": model} if model else {}
    patch = generate_patch(av.problem_statement, files, **kwargs)

    # Model-guided second round: if the model asked to edit a file BM25 never
    # surfaced, that request IS a localization hypothesis — resolve it against
    # the real corpus and re-ask once with the file included. Measured on this
    # subset, 2 of 5 such requests named the exact gold file.
    guided_used = False
    if not patch.diff and patch.missing_paths:
        extra = resolve_requested_paths(patch.missing_paths, by_path, already_given=files)
        if extra:
            guided_used = True
            files = {**files, **extra}
            patch = generate_patch(av.problem_statement, files, **kwargs)

    applied = bool(patch.diff) and git_apply_check(tree, patch.diff)

    gold = list(gold_files(inst))
    return SingleShotResult(
        instance_id=inst.instance_id,
        applied=applied,
        diff=patch.diff,
        error=patch.error,
        raw_response=patch.llm.text,
        cost_usd=patch.llm.cost_usd,
        top_files=list(files),
        guided_retrieval=guided_used,
        gold_files=gold,
        gold_retrieved_rank=first_hit_rank(ranked, gold),
        input_tokens=patch.llm.input_tokens,
        output_tokens=patch.llm.output_tokens,
    )
